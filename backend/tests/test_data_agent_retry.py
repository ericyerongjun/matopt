"""The data agent retries transient model failures and reports outputs it could not save."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.agent.budget import BudgetExhausted, RunBudget
from app.agent.data_agent_bridge import DataAgentExecContext, bind_data_agent_exec_context
from app.config import settings as app_settings
from app.llm.catalog import MODEL_ID
from data_agent_app.services import agent_service as agent_service_module
from data_agent_app.services import sandbox_file_manager
from data_agent_app.services.agent_service import AgentService
from data_agent_app.services.llm_service import LLMService


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    monkeypatch.setattr(app_settings, "llm_max_retries", 2)
    monkeypatch.setattr(app_settings, "llm_retry_base_delay", 0.001)


def status_error(status):
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    response = httpx.Response(status, request=request, headers={})
    kind = openai.RateLimitError if status == 429 else openai.InternalServerError if status >= 500 else openai.BadRequestError
    return kind("provider failure", response=response, body=None)


def piece(content=None, reasoning=None, finish=None):
    delta = SimpleNamespace(content=content, tool_calls=None, reasoning_content=reasoning)
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(finish_reason=finish, delta=delta)])


class Stream:
    def __init__(self, items, then=None):
        self.items, self.then = iter(items), then

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.items)
        except StopIteration:
            if self.then is not None:
                raise self.then from None
            raise StopAsyncIteration from None

    async def close(self):
        pass


def service(*outcomes):
    """Each ``create`` call consumes one outcome: an exception to raise or a value to return."""
    queue, calls = list(outcomes), []

    async def create(**kwargs):
        calls.append(kwargs)
        outcome = queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    svc = LLMService.__new__(LLMService)
    svc.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    svc.default_model = MODEL_ID
    return svc, calls


async def events(svc):
    return [item async for item in svc.stream_chat([{"role": "user", "content": "hi"}])]


FINE = Stream([piece(content="answer", finish="stop")])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [status_error(429), status_error(503), openai.APIConnectionError(
    request=httpx.Request("POST", "https://api.deepseek.com"))])
async def test_transient_failure_before_output_is_retried(failure):
    svc, calls = service(failure, Stream([piece(content="answer", finish="stop")]))
    items = await events(svc)
    assert len(calls) == 2 and [i["type"] for i in items] == ["delta", "done"] and items[-1]["content"] == "answer"


@pytest.mark.asyncio
async def test_permanent_failure_is_not_retried():
    svc, calls = service(status_error(400))
    items = await events(svc)
    assert len(calls) == 1 and items[-1]["type"] == "error"


@pytest.mark.asyncio
async def test_retries_stop_at_the_configured_limit():
    svc, calls = service(status_error(503), status_error(503), status_error(503), FINE)
    items = await events(svc)
    assert len(calls) == 3 and items[-1]["type"] == "error"


@pytest.mark.asyncio
async def test_failure_after_text_is_not_retried_so_nothing_repeats():
    svc, calls = service(Stream([piece(content="partial")], then=status_error(503)), FINE)
    items = await events(svc)
    assert len(calls) == 1 and [i["type"] for i in items] == ["delta", "error"]


@pytest.mark.asyncio
async def test_failure_during_private_reasoning_is_retried():
    svc, calls = service(Stream([piece(reasoning="thinking")], then=status_error(503)),
                         Stream([piece(content="answer", finish="stop")]))
    items = await events(svc)
    assert len(calls) == 2 and [i["type"] for i in items] == ["reasoning", "delta", "done"]


@pytest.mark.asyncio
async def test_provider_resource_shortage_is_retried():
    svc, calls = service(Stream([piece(finish="insufficient_system_resource")]),
                         Stream([piece(content="answer", finish="stop")]))
    items = await events(svc)
    assert len(calls) == 2 and items[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_nonstreaming_chat_retries():
    answer = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Done"))], usage=None)
    svc, calls = service(status_error(503), answer)
    assert await svc.chat([]) == "Done" and len(calls) == 2


def bound(tmp_path, budget):
    return DataAgentExecContext(conversation_id="c", workspace_root=tmp_path, run_budget=budget)


@pytest.mark.asyncio
async def test_each_retry_is_a_budgeted_model_attempt(tmp_path):
    budget = RunBudget(model_attempt_limit=2)
    svc, calls = service(status_error(503), Stream([piece(content="answer", finish="stop")]))
    with bind_data_agent_exec_context(bound(tmp_path, budget)):
        items = await events(svc)
    assert items[-1]["type"] == "done" and len(calls) == 2 and budget.snapshot()["model_attempts"] == 2


@pytest.mark.asyncio
async def test_retry_cannot_exceed_the_shared_budget(tmp_path):
    budget = RunBudget(model_attempt_limit=1)
    svc, calls = service(status_error(503), FINE)
    with bind_data_agent_exec_context(bound(tmp_path, budget)):
        with pytest.raises(BudgetExhausted):
            await events(svc)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_stopping_a_run_interrupts_the_backoff(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "llm_retry_base_delay", 30.0)
    cancel = asyncio.Event()
    svc, _ = service(status_error(503), FINE)
    context = DataAgentExecContext(conversation_id="c", workspace_root=tmp_path, cancel=cancel)

    async def run():
        with bind_data_agent_exec_context(context):
            await events(svc)

    task = asyncio.create_task(run())
    await asyncio.sleep(0.05)
    cancel.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)


# ── outputs that could not be saved ─────────────────────────────────────

def agent(monkeypatch, tmp_path, upload):
    svc = AgentService.__new__(AgentService)
    svc.current_session_id = "1"
    svc.session_files = {}
    svc.file_manager = SimpleNamespace(upload_generated_file=upload)
    output = tmp_path / "chart.png"
    output.write_bytes(b"png")

    async def snapshot(_session):
        return {str(output): {"size": 3, "mtime_ns": 1}}

    async def no_wait(_delay):
        pass

    svc._snapshot_workspace_files = snapshot
    monkeypatch.setattr(agent_service_module.asyncio, "sleep", no_wait)
    monkeypatch.setattr(sandbox_file_manager, "get_sandbox_file_manager",
                        lambda: SimpleNamespace(get_all_synced_files=lambda _session: {}))
    return svc, output


async def scan(svc, result):
    await svc._scan_and_upload_files("1", 1, object(), result, before_files={})
    return result


@pytest.mark.asyncio
async def test_unsaved_output_is_a_failure_the_model_can_read(monkeypatch, tmp_path):
    async def upload(*_args, **_kwargs):
        return None

    svc, _ = agent(monkeypatch, tmp_path, upload)
    result = await scan(svc, {"success": True, "stdout": "made a chart", "error": None})
    assert result["success"] is False and result["artifact_errors"] == ["chart.png"]
    assert "made a chart" in result["stdout"] and "chart.png" in result["stdout"] and "chart.png" in result["error"]
    assert "generated_files" not in result


@pytest.mark.asyncio
async def test_upload_exceptions_are_retried_then_reported(monkeypatch, tmp_path):
    attempts = []

    async def upload(*_args, **_kwargs):
        attempts.append(1)
        raise OSError("disk full")

    svc, _ = agent(monkeypatch, tmp_path, upload)
    result = await scan(svc, {"success": True, "stdout": ""})
    assert len(attempts) == 3 and result["success"] is False and result["artifact_errors"] == ["chart.png"]


@pytest.mark.asyncio
async def test_a_crashed_scan_is_a_failure_too(monkeypatch, tmp_path):
    async def upload(*_args, **_kwargs):
        return "/url"

    svc, _ = agent(monkeypatch, tmp_path, upload)

    async def broken(_session):
        raise RuntimeError("scan broke")

    svc._snapshot_workspace_files = broken
    result = await scan(svc, {"success": True, "stdout": "ok", "error": None})
    assert result["success"] is False and "RuntimeError" in result["artifact_errors"][0]


@pytest.mark.asyncio
async def test_saved_output_stays_a_success_and_keeps_an_earlier_error(monkeypatch, tmp_path):
    async def upload(*_args, **_kwargs):
        return "/url/chart.png"

    svc, output = agent(monkeypatch, tmp_path, upload)
    result = await scan(svc, {"success": True, "stdout": "ok", "error": None})
    assert result["success"] is True and "artifact_errors" not in result
    assert result["generated_files"][0]["sandbox_path"] == str(Path(output))

    async def fails(*_args, **_kwargs):
        return None

    svc.file_manager.upload_generated_file = fails
    failed = await scan(svc, {"success": False, "stdout": "", "error": "NameError: x"})
    assert failed["error"] == "NameError: x" and failed["artifact_errors"] == ["chart.png"]
