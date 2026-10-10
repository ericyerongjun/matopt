import asyncio
import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

import app.agent.harness as harness_module
import app.api.conversations as conversations_api
import app.api.chat as chat_api
from app.agent.events import ToolResult
from app.agent.events import Event
from app.agent.harness import AgentHarness, RunRequest, _trim_live_history
from app.agent.session import derive_messages, derive_request
from app.agent.session.stream import expand_stream
from app.agent.session.telemetry import derive_turn_usage
from app.agent.tools.base import Tool, ToolRegistry
from app.agent.workspace import Workspace
from app.config import settings
from app.llm.base import CompletionResult, StreamDelta
from app.main import app
from app.storage.db import Database
from tests.fake_provider import FakeProvider, FakeRegistry


class EchoTool(Tool):
    name = "echo"
    read_only = True
    concurrent_safe = True
    description = "Return a value for a deterministic test."

    def __init__(self, db):
        self.db = db
        self.harness = None
        self.steer = None

    async def run(self, ctx, **kwargs):
        rows = self.db.list_session_events(ctx.conversation_id)
        assert rows[-1]["type"] == "tool/call"
        assert rows[-1]["data"]["callId"] == ctx.call_id
        if self.steer:
            assert self.harness.enqueue_steer(ctx.conversation_id, self.steer)
        return ToolResult(ok=True, output="echo result")


class RecordingProvider(FakeProvider):
    def __init__(self, script, db):
        super().__init__(script)
        self.db = db
        self.requests = []

    async def stream(self, messages, tools, options):
        cid = self.db.list_conversations()[0]["id"]
        rows = self.db.list_session_events(cid)
        assert rows[-1]["type"] == "request/context"
        header = next(e for e in reversed(rows) if e["type"] == "request/header")
        request = derive_request(rows[:header["seq"] + 1])
        assert request["messages"] == messages
        assert request["tools"] == tools
        self.requests.append((header["seq"], request))
        async for delta in super().stream(messages, tools, options):
            yield delta

    async def complete(self, messages, options):
        return CompletionResult("Fake title", {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3})


def make_harness(db, provider, tmp_path, *, logging=True, tools=None, **overrides):
    test_settings = settings.model_copy(update={"data_dir": tmp_path, "session_log_enabled": logging,
                                               "skill_auto_activate": False, **overrides})
    return AgentHarness(db, SimpleNamespace(), test_settings, FakeRegistry(provider), ToolRegistry(tools or []))


async def collect(harness, *, wait_side_tasks=True, **kwargs):
    events = [event.to_dict() async for event in harness.run(RunRequest(**kwargs), internal_events=True)]
    if wait_side_tasks:
        await harness.wait_side_tasks()
    return events


@pytest.mark.asyncio
async def test_exact_provider_requests_raw_calls_and_checkpoint_before_tool_body(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    raw = '{ "value" : "a" , "nested" : { "n" : 2 } }'
    provider = RecordingProvider([{"tool": "echo", "raw_args": raw, "text": "checking"}, {"text": "final"}, {"text": "final"}], db)
    harness = make_harness(db, provider, tmp_path, tools=[EchoTool(db)])
    events = await collect(harness, conversation_id=None, text="test exact requests")
    cid = events[0]["conversation_id"]
    rows = db.list_session_events(cid)
    assert [e["seq"] for e in rows] == list(range(len(rows)))
    calls = [e for e in rows if e["type"] == "tool/call"]
    assert calls[0]["data"]["arguments"] == raw
    assert provider.calls[1][-2]["tool_calls"][0]["function"]["arguments"] == raw
    for header_seq, request in provider.requests:
        assert derive_request(rows[:header_seq + 1]) == request
    # Restoring the normal context after the isolated final writer records
    # legacy copies; only actual provider attempts have new timing samples.
    assistant = [e for e in rows if e["type"] == "assistant/message" and not e["data"].get("legacy")]
    assert all(e["data"]["duration_ms"] >= 0 for e in assistant)
    assert all(e["data"]["ttft_ms"] >= 0 for e in assistant)
    assert all(expand_stream(e["data"]["stream"])[-1]["chunk"]["type"] == "finish" for e in assistant)
    assert derive_turn_usage([e for e in rows if e["data"].get("turn") == 1])["total_tokens"] == 45
    assert events[-2]["type"] == "message_end" and events[-1]["type"] == "done"
    assert all("seq" not in e for e in events if e["type"] in {"text_delta", "reasoning_delta", "usage"})
    for event in events:
        if event["type"] == "tool_result":
            assert rows[event["seq"]]["type"] == "tool/result"
    assert rows[events[-2]["seq"]]["type"] == "turn/end"
    db.close()


@pytest.mark.asyncio
async def test_steer_is_logged_between_tool_result_and_next_assistant_and_survives_reload(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    provider = RecordingProvider([{"tool": "echo", "text": "first action"}, {"text": "corrected answer"},
                                  {"text": "corrected answer"}, {"text": "next turn"}, {"text": "next turn"}], db)
    tool = EchoTool(db)
    tool.steer = "use a different method"
    harness = make_harness(db, provider, tmp_path, tools=[tool])
    tool.harness = harness
    events = await collect(harness, conversation_id=None, text="work")
    cid = events[0]["conversation_id"]
    rows = db.list_session_events(cid)
    steer = next(e for e in rows if e["type"] == "user/message" and e["data"].get("source") == "steer")
    tool_result = next(e for e in rows if e["type"] == "tool/result")
    final = next(e for e in rows if e["type"] == "assistant/message" and e["data"].get("text") == "corrected answer")
    assert tool_result["seq"] < steer["seq"] < final["seq"]
    # New harness uses only durable history, not its previous in-memory lists.
    resumed = make_harness(db, provider, tmp_path, tools=[EchoTool(db)])
    await collect(resumed, conversation_id=cid, text="continue")
    messages = provider.calls[-2]  # Work request precedes the tool-free final writer.
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "tool", "user", "assistant", "user"]
    assert messages[-3]["content"] == "[Steer] use a different method"
    assert messages[-2]["content"] == "corrected answer"
    assert messages[-1]["content"] == "continue"
    db.close()


@pytest.mark.asyncio
async def test_cumulative_samples_retry_attempts_and_side_task_are_not_double_counted(tmp_path, monkeypatch):
    class RetryProvider(RecordingProvider):
        def __init__(self, db):
            super().__init__([], db)
            self.attempt = 0

        async def stream(self, messages, tools, options):
            self.attempt += 1
            yield StreamDelta(kind="usage", usage={"prompt_tokens": 3, "completion_tokens": 0, "total_tokens": 3})
            if self.attempt == 1:
                raise RuntimeError("connection failed")
            yield StreamDelta(kind="text", text="done")
            yield StreamDelta(kind="usage", usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5})
            yield StreamDelta(kind="finish", finish_reason="stop")

    async def no_delay(seconds):
        return None

    monkeypatch.setattr(harness_module.asyncio, "sleep", no_delay)
    db = Database(tmp_path / "test.sqlite")
    provider = RetryProvider(db)
    harness = make_harness(db, provider, tmp_path)
    events = await collect(harness, conversation_id=None, text="retry")
    cid = events[0]["conversation_id"]
    rows = db.list_session_events(cid)
    attempts = [e for e in rows if e["type"] == "assistant/attempt"]
    assert len(attempts) == 1 and attempts[0]["data"]["attempt"] == 1
    assert any(e["type"] == "llm/retry" for e in rows)
    assert any(e["type"] == "llm/retry-started" for e in rows)
    assert derive_turn_usage([e for e in rows if e["data"].get("turn") == 1])["total_tokens"] == 8
    assistant = db.list_messages(cid)[1]["content"]
    assert assistant["usage"]["total_tokens"] == 8
    assert assistant["stats"]["llm_calls"] == 2
    side_tasks = [e for e in rows if e["type"] == "llm/side-task"]
    assert [e["data"]["status"] for e in side_tasks] == ["started", "completed"]
    assert side_tasks[0]["data"]["task_id"] == side_tasks[1]["data"]["task_id"]
    db.close()


@pytest.mark.asyncio
async def test_setup_failure_is_durable_and_closes_the_ui_message(tmp_path, monkeypatch):
    db = Database(tmp_path / "test.sqlite")
    provider = FakeProvider([])
    harness = make_harness(db, provider, tmp_path)

    def failing_prompt(*args, **kwargs):
        raise ValueError("prompt setup failed")

    monkeypatch.setattr(harness, "_system_prompt", failing_prompt)
    events = await collect(harness, conversation_id=None, text="work")
    cid = events[0]["conversation_id"]
    assert any(e["type"] == "error" and "prompt setup failed" in e["message"] for e in events)
    assert events[-1]["type"] == "message_end" and events[-1]["status"] == "error"
    rows = db.list_session_events(cid)
    assert rows[-1]["type"] == "turn/end" and rows[-1]["data"]["reason"] == {"kind": "error"}
    assert [m["role"] for m in db.list_messages(cid)] == ["user", "assistant"]
    assert provider.calls == []
    db.close()


@pytest.mark.asyncio
async def test_runtime_rollback_flag_retains_legacy_stream_and_messages(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    provider = FakeProvider([{"text": "done"}])
    harness = make_harness(db, provider, tmp_path, logging=False)
    events = await collect(harness, conversation_id=None, text="work")
    cid = events[0]["conversation_id"]
    assert db.list_session_events(cid) == []
    assert [m["role"] for m in db.list_messages(cid)] == ["user", "assistant"]
    assert events[-1]["type"] == "done"
    db.close()


@pytest.mark.asyncio
async def test_disconnect_preserves_delivered_partial_text_and_closes_the_attempt(tmp_path):
    class HangingProvider(FakeProvider):
        async def stream(self, messages, tools, options):
            yield StreamDelta(kind="text", text="delivered partial")
            await asyncio.Event().wait()

    db = Database(tmp_path / "test.sqlite")
    harness = make_harness(db, HangingProvider([]), tmp_path)
    iterator = harness.run(RunRequest(conversation_id=None, text="work"), internal_events=True)
    first = await anext(iterator)
    cid = first.data["conversation_id"]
    assert (await anext(iterator)).type == "message_start"
    assert (await anext(iterator)).type == "final_start"
    assert (await anext(iterator)).type == "text_delta"
    await iterator.aclose()
    rows = db.list_session_events(cid)
    assistant = next(e for e in rows if e["type"] == "assistant/message")
    assert assistant["data"]["interrupted"] is True
    assert assistant["data"]["message"]["content"] == "delivered partial"
    assert rows[-1]["type"] == "turn/end" and rows[-1]["data"]["reason"] == {"kind": "aborted"}
    assert db.list_messages(cid)[-1]["content"]["parts"][0]["text"] == "delivered partial"
    assert not harness.is_running(cid)
    db.close()


@pytest.mark.asyncio
async def test_idle_session_cache_is_bounded_and_active_logs_cannot_be_evicted(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    harness = make_harness(db, FakeProvider([]), tmp_path)
    pinned = db.create_conversation()["id"]
    await harness.prepare_session(pinned)
    harness._active[pinned] = asyncio.Event()
    ids = []
    for _ in range(12):
        cid = db.create_conversation()["id"]
        ids.append(cid)
        await harness.prepare_session(cid)
    assert len(harness._session_logs) == 8
    assert pinned in harness._session_logs and ids[0] not in harness._session_logs
    # Concurrent auxiliary calls share one append sequence and one checkpoint.
    await asyncio.gather(*(harness.record_side_task(ids[-1], {"task": "test", "task_id": f"task-{i}",
                            "status": "completed"}) for i in range(10)))
    rows = db.list_session_events(ids[-1])
    assert [e["seq"] for e in rows] == list(range(10))
    assert harness._session_users == {}
    harness._active.clear()
    db.close()


@pytest.mark.asyncio
async def test_blocked_background_title_allows_next_turn_and_later_records_terminal_usage(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()

    class BlockedTitleProvider(RecordingProvider):
        async def complete(self, messages, options):
            entered.set()
            await release.wait()
            return CompletionResult("New title", {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3})

    db = Database(tmp_path / "test.sqlite")
    provider = BlockedTitleProvider([{"text": "first answer"}, {"text": "second answer"}], db)
    harness = make_harness(db, provider, tmp_path)
    first = await collect(harness, conversation_id=None, text="first", wait_side_tasks=False)
    cid = first[0]["conversation_id"]
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert not harness.is_running(cid)
    second = await asyncio.wait_for(collect(harness, conversation_id=cid, text="second", wait_side_tasks=False), timeout=2)
    assert second[-1]["type"] == "done"
    assert not any(e["type"] == "error" for e in second)
    rows = db.list_session_events(cid)
    title_started = next(e for e in rows if e["type"] == "llm/side-task")
    assert title_started["data"]["status"] == "started"
    release.set()
    await harness.wait_side_tasks()
    terminal = db.list_session_events(cid)[-1]
    assert terminal["type"] == "llm/side-task" and terminal["data"]["status"] == "completed"
    assert terminal["data"]["task_id"] == title_started["data"]["task_id"]
    assert terminal["data"]["usage"]["total_tokens"] == 3
    assert harness._session_users == {} and harness._side_tasks == set()
    assert db.get_conversation(cid)["title"] == "New title"
    db.close()


@pytest.mark.asyncio
async def test_shutdown_reaps_a_title_cancelled_before_coroutine_entry_and_releases_lease(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    provider = FakeProvider([])
    harness = make_harness(db, provider, tmp_path)
    cid = db.create_conversation()["id"]
    from app.llm.base import ChatOptions
    harness._start_title(cid, provider, ChatOptions(model="fake"), "title", None)
    await harness.shutdown()
    assert harness._session_users == {} and harness._side_tasks == set()
    db.close()


@pytest.mark.asyncio
async def test_stalled_consumer_backpressure_is_bounded_and_full_queue_cleanup_cannot_deadlock(tmp_path):
    class FloodProvider(FakeProvider):
        produced = 0

        async def stream(self, messages, tools, options):
            for _ in range(10000):
                self.produced += 1
                yield StreamDelta(kind="text", text="x")

    db = Database(tmp_path / "test.sqlite")
    provider = FloodProvider([])
    harness = make_harness(db, provider, tmp_path)
    iterator = harness.run(RunRequest(conversation_id=None, text="work"), internal_events=True)
    first = await anext(iterator)
    cid = first.data["conversation_id"]
    await asyncio.sleep(0.05)
    assert 0 < provider.produced <= 257
    await asyncio.wait_for(iterator.aclose(), timeout=2)
    assert not harness.is_running(cid)
    assert db.list_session_events(cid)[-1]["data"]["reason"] == {"kind": "aborted"}
    assert db.list_messages(cid)[-1]["content"]["status"] == "stopped"
    db.close()


@pytest.mark.asyncio
async def test_chat_disconnect_preserves_silent_run_until_explicit_stop(tmp_path, monkeypatch):
    from app.agent.runs import RunManager
    entered = asyncio.Event()

    class SilentProvider(FakeProvider):
        async def stream(self, messages, tools, options):
            entered.set()
            await asyncio.Event().wait()
            yield StreamDelta(kind="text", text="never reached")

    class DisconnectRequest:
        disconnected = False

        async def is_disconnected(self):
            return self.disconnected

    db = Database(tmp_path / "test.sqlite")
    provider = SilentProvider([])
    harness = make_harness(db, provider, tmp_path)
    manager = RunManager(db, harness, stop_grace_seconds=.02)
    monkeypatch.setattr(chat_api, "get_runtime", lambda: SimpleNamespace(db=db, harness=harness, runs=manager, providers=FakeRegistry(provider)))
    request = DisconnectRequest()
    response = await chat_api.chat_stream(chat_api.ChatStreamRequest(message="work"), request)
    body = response.body_iterator
    first = await anext(body)
    assert first.startswith("id: ") and "data: " in first
    async def drain():
        return [chunk async for chunk in body]
    pending = asyncio.create_task(drain())
    await asyncio.wait_for(entered.wait(), timeout=1)
    request.disconnected = True
    await asyncio.wait_for(pending, timeout=2)
    cid = db.list_conversations()[0]["id"]
    assert harness.is_running(cid)
    run = manager.latest(cid)
    assert run["status"] == "running"
    stopped, run_id = await manager.stop(cid)
    assert stopped and run_id == run["id"]
    await asyncio.wait_for(manager.wait(run_id), timeout=2)
    assert not harness.is_running(cid)
    assert db.list_session_events(cid)[-1]["data"]["reason"] == {"kind": "aborted"}
    assert manager.get(run_id)["status"] == "stopped"
    await manager.shutdown()
    db.close()


def test_sse_ids_are_explicit_durable_cursors_outside_event_data():
    checkpoint = Event("message_end", {"status": "complete"}, seq=7)
    assert "seq" not in checkpoint.data
    assert checkpoint.to_sse().startswith('id: 7\ndata: {"type": "message_end",')
    assert checkpoint.to_dict()["seq"] == 7
    assert Event("text_delta", {"delta": "x"}).to_sse().startswith("data: ")


@pytest.mark.asyncio
async def test_verifier_and_restored_solver_requests_reconstruct_exactly(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    provider = RecordingProvider([{"text": "candidate"}, {"text": "<verdict>incorrect</verdict><report>fix</report>"},
                                  {"text": "revised"}, {"text": "<verdict>correct</verdict>"},
                                  {"text": "revised"}, {"text": "continue answer"}], db)
    harness = make_harness(db, provider, tmp_path)
    events = await collect(harness, conversation_id=None, text="work", verify=True)
    cid = events[0]["conversation_id"]
    await collect(make_harness(db, provider, tmp_path), conversation_id=cid, text="continue")
    rows = db.list_session_events(cid)
    for header_seq, request in provider.requests:
        assert derive_request(rows[:header_seq + 1]) == request
    messages = provider.calls[-1]
    assert any(m.get("content") == "candidate" for m in messages)
    assert any(m.get("content") == "revised" for m in messages)
    assert not any("<verdict>correct</verdict>" in str(m.get("content")) for m in messages)
    db.close()


@pytest.mark.asyncio
async def test_switching_provider_reasoning_envelopes_preserves_original_raw_history(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    provider = RecordingProvider([{"tool": "echo", "reasoning": "original reasoning"}, {"text": "first"}, {"text": "first"},
                                  {"text": "second"}, {"text": "second"}, {"text": "third"}, {"text": "third"}], db)
    harness = make_harness(db, provider, tmp_path, tools=[EchoTool(db)])
    first = await collect(harness, conversation_id=None, text="work")
    cid = first[0]["conversation_id"]
    provider.replay_reasoning = False
    await collect(harness, conversation_id=cid, text="switch to a different adapter")
    prior_call = next(m for m in provider.calls[-2] if m.get("tool_calls"))
    assert "reasoning_content" not in prior_call
    provider.replay_reasoning = True
    await collect(harness, conversation_id=cid, text="switch back")
    prior_call = next(m for m in provider.calls[-2] if m.get("tool_calls"))
    assert prior_call["reasoning_content"] == "original reasoning"
    rows = db.list_session_events(cid)
    for seq, request in provider.requests:
        assert derive_request(rows[:seq + 1]) == request
    db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("logging", [True, False])
async def test_all_prior_thoughts_replay_with_flash_tool_requests(tmp_path, logging):
    db = Database(tmp_path / "thoughts.sqlite")
    provider = RecordingProvider([
        {"text": "same answer", "reasoning": "first turn thinking"},
        {"text": "same answer", "reasoning": "second turn thinking"},
        {"tool": "echo", "reasoning": "tool planning"},
        {"text": "final", "reasoning": "final thinking"},
    ], db) if logging else FakeProvider([
        {"text": "same answer", "reasoning": "first turn thinking"},
        {"text": "same answer", "reasoning": "second turn thinking"},
        {"tool": "echo", "reasoning": "tool planning"},
        {"text": "final", "reasoning": "final thinking"},
    ])
    # EchoTool asserts durable checkpoints; use a simple pure tool in legacy mode.
    class PureEcho(Tool):
        name = "echo"
        description = "Return a deterministic result."

        async def run(self, ctx, **kwargs):
            return ToolResult(ok=True, output="echo result")

    harness = make_harness(db, provider, tmp_path, logging=logging, tools=[PureEcho()], recent_turns_full_replay=1)
    # This case exercises exact reasoning-envelope replay in strict wording
    # mode; the default final-writer context has its own streaming tests.
    first = await collect(harness, conversation_id=None, text="first", strict_final=True)
    cid = first[0]["conversation_id"]
    await collect(harness, conversation_id=cid, text="second", strict_final=True)
    await collect(harness, conversation_id=cid, text="use the tool", strict_final=True)
    history = provider.calls[-1]
    assistants = [message for message in history if message["role"] == "assistant"]
    assert [message["reasoning_content"] for message in assistants] == [
        "first turn thinking", "second turn thinking", "tool planning",
    ]
    assert history[-2]["tool_calls"][0]["id"] == history[-1]["tool_call_id"]
    assert history[-1]["role"] == "tool"
    if logging:
        rows = db.list_session_events(cid)
        for seq, request in provider.requests:
            assert derive_request(rows[:seq + 1]) == request
    db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_reason", ["aborted", "insufficient_system_resource"])
async def test_provider_terminal_failure_cannot_complete_turn(tmp_path, finish_reason):
    class FailedProvider(FakeProvider):
        async def stream(self, messages, tools, options):
            yield StreamDelta(kind="text", text="partial")
            yield StreamDelta(kind="usage", usage={"total_tokens": 7})
            yield StreamDelta(kind="finish", finish_reason=finish_reason)

    db = Database(tmp_path / "failure.sqlite")
    harness = make_harness(db, FailedProvider([]), tmp_path)
    events = await collect(harness, conversation_id=None, text="work")
    cid = events[0]["conversation_id"]
    rows = db.list_session_events(cid)
    assert not any(event["type"] == "done" for event in events)
    assert rows[-1]["data"]["reason"]["kind"] == "error"
    attempt = next(event for event in rows if event["type"] == "assistant/attempt")
    assert attempt["data"]["usage"]["total_tokens"] == 7
    assert finish_reason in attempt["data"]["error"]["message"]
    db.close()


@pytest.mark.asyncio
async def test_old_saved_route_starts_new_flash_turn_with_historical_rows_intact(tmp_path, monkeypatch):
    from app.agent.session import SessionLog
    from app.llm.catalog import MODEL_ID
    from app.llm.registry import ProviderRegistry

    db = Database(tmp_path / "saved-route.sqlite")
    old = db.create_conversation(provider="qwen", model="qwen3.8-max", reasoning="medium")
    log = await SessionLog.open(db, old["id"])
    log.set_system({"role": "system", "content": "historical system"})
    log.append("turn/start", {"turn": 1})
    log.append("step/start", {"turn": 1, "step": 1})
    log.append("request/header", {"turn": 1, "step": 1, "provider": "qwen",
                                  "header": {"config": {"model": "qwen3.8-max", "reasoning": "medium"}}})
    log.append("assistant/message", {"turn": 1, "step": 1, "stream": [],
                                      "message": {"role": "assistant", "content": "historical answer"},
                                      "reasoning_content": "historical thinking"}, surface_op="append")
    log.append("step/end", {"turn": 1, "step": 1, "reason": {"kind": "completed"}})
    log.append("turn/end", {"turn": 1, "reason": {"kind": "completed"}})
    await log.flush()
    original_events = db.list_session_events(old["id"])
    provider = RecordingProvider([{"text": "new Flash answer"}], db)
    monkeypatch.setattr(settings, "deepseek_api_key", "deepseek-test")
    registry = ProviderRegistry()
    registry._instances["deepseek"] = provider
    harness = make_harness(db, provider, tmp_path)
    harness.providers = registry
    events = await collect(harness, conversation_id=old["id"], text="continue", provider_id=old["provider"],
                           model_id=old["model"], reasoning=old["reasoning"])
    assert events[-1]["type"] == "done"
    current = db.get_conversation(old["id"])
    assert (current["provider"], current["model"], current["reasoning"]) == ("deepseek", MODEL_ID, "high")
    rows = db.list_session_events(old["id"])
    assert rows[:len(original_events)] == original_events
    request = next(event["data"]["header"]["config"] for event in reversed(rows) if event["type"] == "request/header")
    assert request["model"] == MODEL_ID
    assert request["reasoning"] == "high"
    assert (old["provider"], old["model"]) == ("qwen", "qwen3.8-max")
    prior_assistant = next(message for message in provider.calls[0] if message["role"] == "assistant")
    assert prior_assistant["reasoning_content"] == "historical thinking"
    db.close()


@pytest.mark.asyncio
async def test_paged_events_and_historical_request_http_routes(tmp_path, monkeypatch):
    from app.agent.runs import RunManager
    db = Database(tmp_path / "test.sqlite")
    provider = RecordingProvider([{"text": "done"}], db)
    harness = make_harness(db, provider, tmp_path)
    events = await collect(harness, conversation_id=None, text="work")
    cid = events[0]["conversation_id"]
    harness.settings.diagnostics_enabled = True
    rt = SimpleNamespace(db=db, harness=harness, runs=RunManager(db, harness))
    monkeypatch.setattr(conversations_api, "get_runtime", lambda: rt)
    seen = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        cursor = -1
        while True:
            response = await client.get(f"/api/conversations/{cid}/events", params={"from_seq": cursor, "limit": 3})
            assert response.status_code == 200
            page = response.json()
            assert len(page["events"]) <= 3
            assert all(e["seq"] > cursor for e in page["events"])
            assert all(1_000_000_000 < e["time"] < 10_000_000_000 for e in page["events"])
            seen.extend(page["events"])
            cursor = page["next_seq"]
            if not page["has_more"]:
                break
        assert seen == db.list_session_events(cid)
        empty = (await client.get(f"/api/conversations/{cid}/events", params={"from_seq": cursor})).json()
        assert empty == {"events": [], "next_seq": cursor, "has_more": False}
        header_seq = provider.requests[0][0]
        response = await client.get(f"/api/conversations/{cid}/requests/{header_seq}")
        assert response.json() == provider.requests[0][1]
        assert (await client.get(f"/api/conversations/{cid}/requests/0")).status_code == 404
        assert (await client.get(f"/api/conversations/{cid}/trajectory")).status_code == 200
        assert (await client.get(f"/api/conversations/{cid}/usage")).status_code == 200
        assert (await client.get(f"/api/conversations/{cid}/stats")).status_code == 200
        assert (await client.get(f"/api/conversations/{cid}/context")).status_code == 200
        assert (await client.get("/api/conversations/missing/events")).status_code == 404
        assert (await client.get(f"/api/conversations/{cid}/events?limit=0")).status_code == 422
        assert (await client.get(f"/api/conversations/{cid}/events?from_seq=-2")).status_code == 422
    db.close()


@pytest.mark.asyncio
async def test_chat_reload_and_export_repair_crashed_ui_before_returning_messages(tmp_path, monkeypatch):
    from app.agent.runs import RunManager
    from tests.test_session_log import crashed_prefix

    db = Database(tmp_path / "test.sqlite")
    cid = db.create_conversation()["id"]
    db.append_session_events(cid, crashed_prefix().events)
    harness = make_harness(db, FakeProvider([]), tmp_path)
    rt = SimpleNamespace(db=db, harness=harness, runs=RunManager(db, harness), workspace=lambda cid: Workspace(cid, tmp_path / "workspaces"))
    monkeypatch.setattr(conversations_api, "get_runtime", lambda: rt)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/conversations/{cid}")
        assert response.status_code == 200
        messages = response.json()["messages"]
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[-1]["content"]["status"] == "stopped"
        assert messages[-1]["content"]["recovered"] is True
        assert db.list_session_events(cid)[-1]["data"]["reason"] == {"kind": "interrupted"}
        count = len(db.list_session_events(cid))
        exported = await client.get(f"/api/conversations/{cid}/export?format=markdown")
        assert exported.status_code == 200 and "doing work" not in exported.text
        assert "doing work" in json.dumps(db.list_session_events(cid))
        assert len(db.list_session_events(cid)) == count
        assert (await client.get("/api/conversations/missing")).status_code == 404
        assert (await client.get("/api/conversations/missing/export")).status_code == 404
    db.close()


def test_live_budget_never_drops_the_latest_user_or_leaves_dangling_tool_results():
    history = [{"role": "system", "content": "system"}, {"role": "user", "content": "old" * 200},
               {"role": "assistant", "content": "old answer"}, {"role": "user", "content": "new"},
               {"role": "assistant", "content": "using a tool"}, {"role": "tool", "content": "result"}]
    assert _trim_live_history(history, 50)
    assert [m["content"] for m in history] == ["system", "new", "using a tool", "result"]
    single_turn = [{"role": "system", "content": "system"}, {"role": "user", "content": "large" * 100},
                   {"role": "assistant", "content": "tools"}, *[{"role": "tool", "content": "result"}] * 3]
    assert not _trim_live_history(single_turn, 20)
    assert single_turn[1]["content"].startswith("large")
