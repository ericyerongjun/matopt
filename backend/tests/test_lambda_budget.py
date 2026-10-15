"""Delegated admission, honest usage, and cancellation without provider traffic."""
from __future__ import annotations

import asyncio
import json
import os
import shlex
from contextlib import aclosing
from types import SimpleNamespace

import pytest

from app.agent.budget import BudgetExhausted, RunBudget
from app.agent.lambda_bridge import LambdaExecContext, bind_lambda_exec_context, run_python_on_kernel
from app.llm.catalog import MODEL_ID
from lambda_app.services.agent_service import AgentService
from lambda_app.services.llm_service import LLMService


class Stream:
    def __init__(self, chunks=(), *, gate=None):
        self.chunks = list(chunks)
        self.gate = gate
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.chunks:
            return self.chunks.pop(0)
        if self.gate is not None:
            self.entered.set()
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        raise StopAsyncIteration

    async def close(self):
        self.closed = True


def chunk(content="", usage=None):
    return SimpleNamespace(usage=usage, choices=[] if usage is not None else [SimpleNamespace(
        finish_reason=None, delta=SimpleNamespace(content=content, tool_calls=None),
    )])


def tool_chunk(call_id="child", name="test", arguments="{}"):
    value = chunk()
    value.choices[0].delta.tool_calls = [SimpleNamespace(
        index=0, id=call_id, function=SimpleNamespace(name=name, arguments=arguments),
    )]
    return value


def finish_chunk(reason):
    value = chunk()
    value.choices[0].finish_reason = reason
    return value


def service(stream=None, create=None):
    calls = []

    async def request(**kwargs):
        calls.append(kwargs)
        return await create() if create is not None else stream

    svc = LLMService.__new__(LLMService)
    svc.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=request)))
    svc.default_model = MODEL_ID
    return svc, calls


def context(tmp_path, budget=None, **kwargs):
    return LambdaExecContext("parent", tmp_path, run_budget=budget, cancel=asyncio.Event(), **kwargs)


async def collect(source):
    async with aclosing(source):
        return [item async for item in source]


@pytest.mark.asyncio
@pytest.mark.parametrize("usage", [
    {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
    SimpleNamespace(prompt_tokens=10, completion_tokens=3, total_tokens=13),
])
async def test_stream_admits_once_and_records_genuine_usage(tmp_path, usage):
    budget = RunBudget(model_attempt_limit=1)
    recorded = []

    async def recorder(data):
        recorded.append(data)

    stream = Stream([chunk("Useful result."), chunk(usage=usage)])
    svc, calls = service(stream)
    with bind_lambda_exec_context(context(tmp_path, budget, record_model_call=recorder)):
        events = await collect(svc.stream_chat([], MODEL_ID))
    assert len(calls) == 1 and events[-1]["type"] == "done" and stream.closed
    assert [event["status"] for event in recorded] == ["started", "completed"]
    snapshot = budget.snapshot()
    assert snapshot["model_attempts"] == 1 and snapshot["usage"]["reported_attempts"] == 1
    assert snapshot["usage"]["exact"] == {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}
    assert recorded[0]["task_id"] == recorded[1]["task_id"]
    assert recorded[1]["usage"] == snapshot["usage"]["exact"]


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [None, "{}", '{"code":'])
@pytest.mark.parametrize("usage", [None, {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}])
async def test_truncated_stream_keeps_text_and_late_usage_without_done_or_tools(tmp_path, arguments, usage):
    budget = RunBudget(model_attempt_limit=1)
    recorded = []

    async def recorder(data):
        recorded.append(data)

    stream = Stream([
        chunk("Partial measured result"),
        *([tool_chunk(arguments=arguments)] if arguments is not None else []),
        finish_chunk("length"),
        *([chunk(usage=usage)] if usage is not None else []),
    ])
    svc, calls = service(stream)
    with bind_lambda_exec_context(context(tmp_path, budget, record_model_call=recorder)):
        events = await collect(svc.stream_chat([]))
    assert len(calls) == 1 and stream.closed
    assert [event["type"] for event in events] == ["delta", "error"]
    assert events[0]["content"] == "Partial measured result"
    assert events[-1]["incomplete"] is True and events[-1]["finish_reason"] == "length"
    assert [event["status"] for event in recorded] == ["started", "error"]
    snapshot = budget.snapshot()
    assert snapshot["usage"]["unsettled_attempts"] == 0
    assert snapshot["usage"]["exact"] == usage
    if usage is None:
        assert events[-1]["usage"] is None
        assert snapshot["usage"]["unknown_settled_attempts"] == 1
    else:
        assert events[-1]["usage"]["total_tokens"] == usage["total_tokens"]
    assert recorded[-1]["usage"] == usage


@pytest.mark.asyncio
@pytest.mark.parametrize("usage", [None, {"completion_tokens": 3}, {"prompt_tokens": 10},
                                       {"prompt_tokens": 10, "completion_tokens": 3, "estimated": True}])
async def test_missing_partial_or_estimated_receipt_stays_unknown(tmp_path, usage):
    budget = RunBudget(model_attempt_limit=1)
    svc, _ = service(Stream([chunk("Output"), *([chunk(usage=usage)] if usage is not None else [])]))
    with bind_lambda_exec_context(context(tmp_path, budget)):
        await collect(svc.stream_chat([]))
    snapshot = budget.snapshot()
    assert snapshot["usage"]["exact"] is None
    assert snapshot["usage"]["unknown_settled_attempts"] == 1
    assert next(iter(snapshot["model_receipts"].values()))["outcome"] == "completed"


@pytest.mark.asyncio
async def test_child_admission_preserves_parent_final_slot(tmp_path):
    budget = RunBudget(model_attempt_limit=2, reserve_final_slot=True)
    budget.admit_model("parent-work", source="native")
    svc, calls = service(Stream())
    with bind_lambda_exec_context(context(tmp_path, budget)):
        with pytest.raises(BudgetExhausted) as error:
            await collect(svc.stream_chat([]))
    assert error.value.dimension == "model_attempts" and calls == []
    assert budget.admit_model("parent-final", source="native", final=True).is_new


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["request", "stream"])
async def test_cancel_gated_provider_reaps_and_settles_attempt(tmp_path, where):
    entered, stopped = asyncio.Event(), asyncio.Event()
    stream = Stream(gate=asyncio.Event())

    async def create():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    svc, calls = service(stream, create=create if where == "request" else None)
    budget = RunBudget(model_attempt_limit=2)
    bridge = context(tmp_path, budget)
    with bind_lambda_exec_context(bridge):
        task = asyncio.create_task(collect(svc.stream_chat([])))
        await asyncio.wait_for((entered if where == "request" else stream.entered).wait(), 1)
        bridge.cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
    assert len(calls) == 1
    assert stopped.is_set() if where == "request" else stream.cancelled.is_set() and stream.closed
    snapshot = budget.snapshot()
    assert snapshot["usage"]["unsettled_attempts"] == 0
    assert next(iter(snapshot["model_receipts"].values()))["outcome"] == "aborted"


@pytest.mark.asyncio
async def test_deadline_during_provider_is_control_not_error_chunk(tmp_path):
    budget = RunBudget(model_attempt_limit=1, timeout_seconds=0.03)
    stream = Stream(gate=asyncio.Event())
    svc, calls = service(stream)
    with bind_lambda_exec_context(context(tmp_path, budget)):
        with pytest.raises(BudgetExhausted) as error:
            await collect(svc.stream_chat([]))
    assert error.value.dimension == "deadline" and len(calls) == 1
    assert stream.closed and stream.cancelled.is_set()
    assert next(iter(budget.snapshot()["model_receipts"].values()))["outcome"] == "aborted"


@pytest.mark.asyncio
async def test_control_boundary_waits_before_child_admission(tmp_path):
    entered, left = asyncio.Event(), asyncio.Event()

    async def boundary():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            left.set()

    budget = RunBudget(model_attempt_limit=1)
    bridge = context(tmp_path, budget, control_boundary=boundary)
    svc, calls = service(Stream())
    with bind_lambda_exec_context(bridge):
        task = asyncio.create_task(collect(svc.stream_chat([])))
        await asyncio.wait_for(entered.wait(), 1)
        assert budget.snapshot()["model_attempts"] == 0 and not calls
        bridge.cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert left.is_set() and budget.snapshot()["model_attempts"] == 0


@pytest.mark.asyncio
async def test_nonstream_request_uses_same_accounting(tmp_path):
    async def create():
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Done"))],
                               usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6})

    svc, calls = service(create=create)
    budget = RunBudget(model_attempt_limit=1)
    with bind_lambda_exec_context(context(tmp_path, budget)):
        assert await svc.chat([]) == "Done"
    assert len(calls) == 1 and budget.snapshot()["usage"]["exact"]["total_tokens"] == 6


@pytest.mark.asyncio
async def test_tools_count_actual_starts_once_and_reject_exhaustion(tmp_path):
    starts = []

    async def handler(args):
        starts.append(args)
        return {"success": True, "output": "done"}

    svc = AgentService()
    svc.tools = {"test": handler}
    budget = RunBudget(tool_start_limit=1)
    with bind_lambda_exec_context(context(tmp_path, budget)):
        assert not (await svc.execute_tool("missing", {}))["success"]
        assert (await svc.execute_tool("test", {}, call_id="child-1"))["success"]
        assert not (await svc.execute_tool("test", {}, call_id="child-1"))["success"]
        with pytest.raises(BudgetExhausted) as error:
            await svc.execute_tool("test", {}, call_id="child-2")
    assert error.value.dimension == "tool_starts" and len(starts) == 1
    assert budget.snapshot()["tool_starts"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "deadline"])
async def test_child_tool_gated_body_is_reaped(tmp_path, stop):
    entered, left = asyncio.Event(), asyncio.Event()

    async def handler(args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            left.set()

    svc = AgentService()
    svc.tools = {"test": handler}
    budget = RunBudget(tool_start_limit=1, timeout_seconds=0.03 if stop == "deadline" else None)
    bridge = context(tmp_path, budget)
    with bind_lambda_exec_context(bridge):
        task = asyncio.create_task(svc.execute_tool("test", {}))
        await asyncio.wait_for(entered.wait(), 1)
        if stop == "cancel":
            bridge.cancel.set()
        with pytest.raises(asyncio.CancelledError if stop == "cancel" else BudgetExhausted):
            await asyncio.wait_for(task, 1)
    assert left.is_set() and budget.snapshot()["tool_starts"] == 1


@pytest.mark.asyncio
async def test_repeated_parent_cancellation_cannot_abort_child_cleanup(tmp_path):
    entered, cleaning, release, reaped = (asyncio.Event() for _ in range(4))

    async def handler(args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            reaped.set()

    svc = AgentService()
    svc.tools = {"test": handler}
    bridge = context(tmp_path, RunBudget(tool_start_limit=1))
    with bind_lambda_exec_context(bridge):
        task = asyncio.create_task(svc.execute_tool("test", {}))
        await asyncio.wait_for(entered.wait(), 1)
        bridge.cancel.set()
        await asyncio.wait_for(cleaning.wait(), 1)
        task.cancel()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not reaped.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
    assert reaped.is_set()


@pytest.mark.asyncio
async def test_kernel_receives_shared_cancel_and_clipped_deadline(tmp_path):
    entered, release, reaped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    captured = {}

    class Kernel:
        async def execute(self, code, *, timeout, cancel):
            captured.update(timeout=timeout, cancel=cancel, code=code)
            entered.set()
            try:
                await release.wait()
                return SimpleNamespace(stdout="", results=[], ok=False, status="aborted", error_name="Aborted",
                                       error_value="stopped", artifacts=[], stderr="", duration_ms=1)
            finally:
                reaped.set()

        async def interrupt(self):
            release.set()

    budget = RunBudget(timeout_seconds=30)
    bridge = context(tmp_path, budget, kernel=Kernel())
    with bind_lambda_exec_context(bridge):
        task = asyncio.create_task(run_python_on_kernel("print(1)", timeout=120))
        await asyncio.wait_for(entered.wait(), 1)
        bridge.cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
    assert 0 < captured["timeout"] <= 30 and captured["cancel"] is bridge.cancel
    assert reaped.is_set()


@pytest.mark.asyncio
async def test_keepalive_forwards_budget_control_and_closes_source():
    from lambda_app.api.v1.chat import stream_with_keepalive

    closed = asyncio.Event()

    async def source():
        try:
            raise BudgetExhausted("model_attempts", limit=1, used=1)
            yield  # make an async generator
        finally:
            closed.set()

    with pytest.raises(BudgetExhausted):
        await collect(stream_with_keepalive(source(), interval=0.01))
    assert closed.is_set()


@pytest.fixture
def lambda_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from lambda_app.core.database import Base
    from lambda_app.models.models import User

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        user = User(email="delegation@lambda.local", hashed_password="unused")
        db.add(user)
        db.commit()
        yield db, user
    engine.dispose()


@pytest.mark.asyncio
async def test_chat_applies_shared_academic_guidance_to_data_agent(tmp_path, monkeypatch, lambda_db):
    from app.academic_guidance import ACADEMIC_GUIDANCE
    from lambda_app.api.v1 import chat
    from lambda_app.models.schemas import ChatRequest

    requests = []

    async def stream(messages, model):
        requests.append(messages)
        yield {"type": "done", "content": "A finite second moment is required.", "usage": None}

    svc = AgentService()
    monkeypatch.setattr(svc, "is_session_sync_current", lambda *args: True)
    monkeypatch.setattr(svc, "get_cached_synced_files", lambda *args: [])
    monkeypatch.setattr(svc, "get_available_files_prompt", lambda *args: "")
    monkeypatch.setattr(chat, "agent_service", svc)
    monkeypatch.setattr(chat.llm_service, "stream_chat", stream)
    db, user = lambda_db
    with bind_lambda_exec_context(context(tmp_path, RunBudget())):
        await collect(chat.stream_chat_response(ChatRequest(message="State the assumptions for variance."), user, db))
    assert len(requests) == 1
    system = requests[0][0]
    assert system["role"] == "system" and system["content"].count(ACADEMIC_GUIDANCE) == 1
    assert r"\mathbb{E}" in system["content"]
    assert "You are LAMBDA" in system["content"]
    assert "session_id:" in system["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["task_cancel", "parent_cancel", "budget"])
async def test_chat_reaps_shielded_inner_tool_on_control(tmp_path, monkeypatch, lambda_db, stop):
    from lambda_app.api.v1 import chat
    from lambda_app.models.schemas import ChatRequest

    entered, reaped = asyncio.Event(), asyncio.Event()

    async def handler(args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            reaped.set()

    async def stream(*args):
        yield {"type": "tool_call", "tool_call": {"id": "child", "type": "function",
               "function": {"name": "test", "arguments": "{}"}}}
        yield {"type": "done", "content": "", "usage": None}

    svc = AgentService()
    svc.tools = {"test": handler}
    monkeypatch.setattr(svc, "is_session_sync_current", lambda *args: True)
    monkeypatch.setattr(svc, "get_cached_synced_files", lambda *args: [])
    monkeypatch.setattr(svc, "get_available_files_prompt", lambda *args: "")
    monkeypatch.setattr(chat, "agent_service", svc)
    monkeypatch.setattr(chat.llm_service, "stream_chat", stream)
    budget = RunBudget(tool_start_limit=0 if stop == "budget" else 1)
    bridge = context(tmp_path, budget)
    db, user = lambda_db
    with bind_lambda_exec_context(bridge):
        task = asyncio.create_task(collect(chat.stream_chat_response(ChatRequest(message="Inspect"), user, db)))
        if stop == "budget":
            with pytest.raises(BudgetExhausted):
                await asyncio.wait_for(task, 1)
            assert not entered.is_set()
        else:
            await asyncio.wait_for(entered.wait(), 1)
            if stop == "parent_cancel":
                bridge.cancel.set()
            else:
                task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            assert reaped.is_set()


@pytest.mark.asyncio
async def test_standalone_provider_and_tools_have_no_parent_dependency():
    svc, calls = service(Stream([chunk("Standalone answer")]))
    with bind_lambda_exec_context(None):
        events = await collect(svc.stream_chat([]))
        agent = AgentService()

        async def handler(args):
            return {"success": True, "output": args["value"]}

        agent.tools = {"test": handler}
        result = await agent.execute_tool("test", {"value": "unchanged"})
    assert len(calls) == 1 and events[-1]["content"] == "Standalone answer"
    assert result == {"success": True, "output": "unchanged"}


@pytest.mark.asyncio
async def test_delegated_shell_cancellation_reaps_native_process(tmp_path):
    from app.agent.tools.base import ToolContext
    from app.agent.workspace import Workspace

    async def emit(event):
        pass

    workspace = Workspace("shell", tmp_path / "workspace")
    parent = ToolContext("shell", workspace, emit, asyncio.Event())
    budget = RunBudget(tool_start_limit=1)
    bridge = LambdaExecContext("shell", workspace.root, run_budget=budget,
                               cancel=parent.cancel, tool_context=parent)
    pid_file = workspace.root / "child.pid"
    svc = AgentService()
    with bind_lambda_exec_context(bridge):
        task = asyncio.create_task(svc.execute_tool("execute_shell", {
            "command": f"echo $$ > {shlex.quote(str(pid_file))}; sleep 30", "session_id": "shell",
        }))
        try:
            async with asyncio.timeout(3):
                while not pid_file.exists():
                    await asyncio.sleep(0.01)
            pid = int(pid_file.read_text().strip())
            parent.cancel.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert budget.snapshot()["tool_starts"] == 1


@pytest.mark.asyncio
async def test_parent_wrapper_passes_same_controls_and_closes_idle_stream(tmp_path, monkeypatch, lambda_db):
    from app.agent.lambda_bridge import get_lambda_exec_context
    from app.agent.tools.base import ToolContext
    from app.agent.tools.lambda_tool import LambdaDataAgentTool
    from app.agent.workspace import Workspace
    from lambda_app.api import deps
    from lambda_app.api.v1 import chat
    from lambda_app.core import database

    db, user = lambda_db
    entered, closed = asyncio.Event(), asyncio.Event()
    captured = []

    async def emit(event):
        pass

    async def boundary():
        pass

    async def recorder(data):
        pass

    async def stream(*args):
        captured.append(get_lambda_exec_context())
        entered.set()
        try:
            await asyncio.Event().wait()
            yield "unused"
        finally:
            closed.set()

    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(deps, "get_current_user", lambda db: user)
    monkeypatch.setattr(chat, "stream_chat_response", stream)
    budget = RunBudget()
    parent = ToolContext("wrapper", Workspace("wrapper", tmp_path / "workspace"), emit, asyncio.Event(),
                         state={"run_budget": budget, "control_boundary": boundary, "lambda_record_call": recorder})
    task = asyncio.create_task(LambdaDataAgentTool().run(parent, message="Inspect"))
    await asyncio.wait_for(entered.wait(), 1)
    child = captured[0]
    assert child.run_budget is budget and child.cancel is parent.cancel and child.tool_context is parent
    assert child.control_boundary is boundary and child.record_model_call is recorder
    parent.cancel.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert closed.is_set() and get_lambda_exec_context() is None


@pytest.mark.asyncio
async def test_truncation_reaches_parent_as_incomplete_with_prior_work_and_usage(tmp_path, monkeypatch, lambda_db):
    from app.agent.tools.base import ToolContext
    from app.agent.tools.lambda_tool import LambdaDataAgentTool
    from app.agent.workspace import Workspace
    from lambda_app.api import deps
    from lambda_app.api.v1 import chat
    from lambda_app.core import database
    from lambda_app.models.models import Message, UsageRecord

    db, user = lambda_db
    workspace = Workspace("truncated", tmp_path / "workspace")
    starts, recorded = [], []
    source_streams = [
        Stream([tool_chunk(), finish_chunk("tool_calls"),
                chunk(usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6})]),
        # The second response proposes another valid tool call, but it was cut
        # short. Neither that call nor a successful handoff may be published.
        Stream([chunk("The measured total is 42; further analysis"), tool_chunk("unfinished"),
                finish_chunk("length"),
                chunk(usage={"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13})]),
    ]
    pending = iter(source_streams)

    async def create():
        return next(pending)

    async def handler(args):
        starts.append(args)
        (workspace.root / "measured.txt").write_text("Verified measurement: 42\n")
        return {"success": True, "output": "Verified measurement: 42"}

    async def emit(event):
        pass

    async def recorder(data):
        recorded.append(data)

    svc, calls = service(create=create)
    agent = AgentService()
    agent.tools = {"test": handler}
    monkeypatch.setattr(agent, "is_session_sync_current", lambda *args: True)
    monkeypatch.setattr(agent, "get_cached_synced_files", lambda *args: [])
    monkeypatch.setattr(agent, "get_available_files_prompt", lambda *args: "")
    monkeypatch.setattr(chat, "agent_service", agent)
    monkeypatch.setattr(chat, "llm_service", svc)
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(deps, "get_current_user", lambda db: user)
    monkeypatch.setattr(chat.settings, "LOCAL_WORKSPACE_ROOT", str(tmp_path / "lambda"))
    budget = RunBudget(model_attempt_limit=2, tool_start_limit=1)
    parent = ToolContext("truncated", workspace, emit, asyncio.Event(),
                         state={"run_budget": budget, "lambda_record_call": recorder})
    result = await LambdaDataAgentTool().run(parent, message="Measure the total and explain it")

    assert len(calls) == 2 and len(starts) == 1
    assert all(stream.closed for stream in source_streams)
    assert result.ok is False and "incomplete" in result.error
    assert result.meta["incomplete"] is True and result.meta["finish_reason"] == "length"
    assert "The measured total is 42; further analysis" in result.output
    assert "Verified measurement: 42" in result.output
    assert any(artifact.path == "measured.txt" for artifact in result.artifacts)
    snapshot = budget.snapshot()
    assert snapshot["model_attempts"] == 2 and snapshot["tool_starts"] == 1
    assert snapshot["usage"]["exact"] == {"prompt_tokens": 14, "completion_tokens": 5, "total_tokens": 19}
    assert snapshot["usage"]["unsettled_attempts"] == 0
    assert [event["status"] for event in recorded] == ["started", "completed", "started", "error"]
    saved = db.query(Message).filter_by(role="assistant").order_by(Message.id.desc()).first()
    assert "incomplete" in saved.content and "Verified measurement: 42" in saved.content
    assert len(json.loads(saved.tool_calls)) == 1
    assert sum(row.total_tokens for row in db.query(UsageRecord).all()) == 19


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [True, False])
async def test_artifact_discovery_and_explicit_markers_follow_bound_workspace_visibility(tmp_path, monkeypatch, bound):
    from app.agent.workspace import Workspace, is_internal_output
    from lambda_app.services import sandbox_file_manager

    workspace = Workspace("visibility", tmp_path)
    internal = [".ipython/history.sqlite", ".matplotlib/fontlist.json", "nested/.cache/state.json",
                "__pycache__/helper.pyc", ".hidden.txt", "analysis.ipynb"]
    visible = ["plots/chart.png", "tables/result.csv"]
    for relative in internal + visible:
        path = workspace.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("saved result")

    registered = []

    async def upload(session, path, filename, *args, **kwargs):
        registered.append(path)
        return "/api/result/" + filename

    manager = SimpleNamespace(upload_generated_file=upload, get_all_synced_files=lambda session: {})
    svc = AgentService()
    svc.file_manager = manager
    monkeypatch.setattr(sandbox_file_manager, "get_sandbox_file_manager", lambda: manager)
    monkeypatch.setattr(svc, "_local_workspace", lambda session: workspace.root)
    bridge = context(workspace.root) if bound else None
    with bind_lambda_exec_context(bridge):
        before = await svc._snapshot_workspace_files("visibility")
        discovered = {str(__import__("pathlib").Path(path).relative_to(workspace.root)) for path in before}
        expected = {workspace.relpath(path) for path in workspace.iter_files()
                    if not is_internal_output(workspace.relpath(path))} if bound else set(internal + visible)
        assert discovered == expected
        (workspace.root / ".ipython/new-history.sqlite").write_text("internal change")
        (workspace.root / "report.md").write_text("Useful report")
        marker_paths = [str(workspace.root / relative) for relative in internal + [visible[0]]]
        result = {"stdout": "[IMAGE_SAVED] " + marker_paths[0] + "\n[CAPTURED_IMAGES] " + json.dumps(marker_paths)}
        await svc._scan_and_upload_files("visibility", 1, object(), result, before_files=before)
        if bound:
            assert set(registered) == {str(workspace.root / visible[0]), str(workspace.root / "report.md")}
            assert {item["filename"] for item in result["generated_files"]} == {"chart.png", "report.md"}
            assert not svc.is_visible_generated_file("/tmp/workspace/.matplotlib/fontlist.json")
        else:
            # Legacy standalone discovery/registration is intentionally unchanged.
            assert str(workspace.root / ".ipython/history.sqlite") in registered
            assert str(workspace.root / ".ipython/new-history.sqlite") in registered
        assert svc.is_visible_generated_file("report.md")
