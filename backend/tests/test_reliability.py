"""Regression tests for agent-core failures found in review."""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import queue
import shutil
import threading
import time
from types import SimpleNamespace

import pytest

from app.agent import kernel as kernel_module, notebook, otel
from app.agent.events import ToolResult, ev
from app.agent.execution import ToolExecutionService
from app.agent.harness import AgentHarness, RunRequest
from app.agent.kernel import ExecResult, KernelPool, KernelSession
from app.agent.runs import RunManager
from app.agent.session import SessionLog, derive_request
from app.agent.session.validate import IncrementalValidator, validate_events
from app.agent.subagents import subagent_tools
from app.agent.tools.base import Tool, ToolContext, ToolRegistry
from app.agent.tools.python_tool import PythonTool
from app.agent.workspace import Workspace
from app.config import settings
from app.llm.base import StreamDelta
from app.llm.retry import IncompleteStream, classify, failure_category
from app.log_context import bind_log_context, install_log_context, log_context_suffix
from app.storage.db import Database
from tests.fake_provider import FakeProvider, FakeRegistry


# ── session log ─────────────────────────────────────────────────────────

def started_log() -> SessionLog:
    log = SessionLog()
    log.set_system({"role": "system", "content": "s"})
    log.append("turn/start", {"turn": 1, "message_id": "m1"})
    log.append("step/start", {"turn": 1, "step": 1})
    return log


def test_append_does_not_rewalk_the_log():
    log = started_log()
    for index in range(50):
        log.append("llm/side-task", {"task": "x", "task_id": str(index)})
    calls = []
    import app.agent.session.derive as derive
    real = derive.validate_envelope
    derive.validate_envelope = lambda event, index: (calls.append(index), real(event, index))[1]
    try:
        log.append("llm/side-task", {"task": "x", "task_id": "last"})
    finally:
        derive.validate_envelope = real
    assert calls == [log.last_seq]


def test_rejected_batch_leaves_the_validator_unchanged():
    log = started_log()
    before = log.events
    good = {"seq": len(before), "time": 1.0, "type": "step/end", "data": {"turn": 1, "step": 1}}
    bad = {"seq": len(before) + 1, "time": 1.0, "type": "step/end", "data": {"turn": 1, "step": 1}}
    with pytest.raises(ValueError):
        log.append_batch([good, bad])
    assert log.events == before
    log.append_batch([good])
    assert log.events[-1]["type"] == "step/end"


def test_incremental_validator_matches_full_validation():
    log = started_log()
    log.sync_history([{"role": "system", "content": "s"}, {"role": "user", "content": "a"}, {"role": "user", "content": "b"}])
    log.sync_history([{"role": "system", "content": "s"}, {"role": "user", "content": "c"}])
    events = log.events
    validate_events(events)
    validator = IncrementalValidator()
    validator.extend(events)
    assert validator.count == len(events)
    assert log.events == events


def test_views_agree_with_the_copying_accessors():
    log = started_log()
    log.append("request/header", {"turn": 1, "step": 1, "attempt": 1, "reason": "dispatch",
                                  "header": {"config": {"provider": "p"}}})
    assert log.events_view == log.events
    assert log.last_seq == log.events[-1]["seq"]
    assert log.next_turn == 2
    assert log.current_request() == derive_request(log.events)


def test_flush_does_not_reread_stored_rows(tmp_path):
    db = Database(tmp_path / "s.sqlite")
    try:
        cid = db.create_conversation()["id"]
        log = started_log()
        db.append_session_events(cid, log.events, prevalidated=True)
        statements = []
        db._conn.set_trace_callback(statements.append)
        db.append_session_events(cid, [{"seq": log.last_seq + 1, "time": 1.0, "type": "llm/side-task", "data": {}}],
                                 prevalidated=True)
        db._conn.set_trace_callback(None)
        assert not any(s.startswith("SELECT * FROM session_events") for s in statements)
    finally:
        db.close()


def test_prevalidated_flush_still_refuses_a_gap(tmp_path):
    db = Database(tmp_path / "s.sqlite")
    try:
        cid = db.create_conversation()["id"]
        with pytest.raises(ValueError, match="contiguous"):
            db.append_session_events(cid, [{"seq": 5, "time": 1.0, "type": "llm/side-task", "data": {}}], prevalidated=True)
        assert db.list_session_events(cid) == []
    finally:
        db.close()


# ── kernel lifecycle ────────────────────────────────────────────────────

class FakeClient:
    """Serves queued iopub messages; a cell stays silent until `idle` is queued."""

    def __init__(self):
        self.iopub: asyncio.Queue = asyncio.Queue()
        self.closed = False
        self.executed = []

    def execute(self, code, **_):
        self.executed.append(code)
        return f"cell{len(self.executed)}"

    async def get_iopub_msg(self, timeout=None):
        if self.closed:
            raise AssertionError("channel closed")
        try:
            return await asyncio.wait_for(self.iopub.get(), .02)
        except asyncio.TimeoutError:
            raise queue.Empty from None

    async def get_shell_msg(self, timeout=None):
        return {}

    def stop_channels(self):
        self.closed = True

    def idle(self, msg_id):
        self.iopub.put_nowait({"parent_header": {"msg_id": msg_id}, "header": {"msg_type": "status"},
                               "content": {"execution_state": "idle"}})


class FakeManager:
    def __init__(self):
        self.alive = True
        self.interrupts = 0
        self.shutdowns = 0

    async def is_alive(self):
        return self.alive

    async def interrupt_kernel(self):
        self.interrupts += 1

    async def shutdown_kernel(self, now=False):
        self.shutdowns += 1


@pytest.fixture
def ws(tmp_path):
    return Workspace("c", tmp_path)


@pytest.fixture
def session(ws, monkeypatch):
    item = KernelSession("c", ws)
    client, manager = FakeClient(), FakeManager()
    item._kc, item._km, item.started = client, manager, True
    restarts = []

    async def fresh_start():
        restarts.append(time.monotonic())
        item._kc, item._km, item.started = FakeClient(), FakeManager(), True

    monkeypatch.setattr(item, "_start", fresh_start)
    monkeypatch.setattr(kernel_module, "ABANDONED_CELL_GRACE_SECONDS", .05)
    monkeypatch.setattr("app.agent.notebook.append_analysis_cell", lambda *a: None)
    item.restarts = restarts
    return item


async def wait_until(predicate, seconds=2.0):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "condition never became true"
        await asyncio.sleep(.005)


@pytest.mark.asyncio
async def test_hung_cell_restarts_the_kernel_after_its_tool_is_cancelled(session):
    cell = asyncio.create_task(session.execute("while True: pass", timeout=60))
    await wait_until(lambda: session._kc.executed)
    cell.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cell
    await wait_until(lambda: session.restarts)
    assert session.started and not session._kc.executed


@pytest.mark.asyncio
async def test_the_next_cell_is_told_its_variables_are_gone(session):
    cell = asyncio.create_task(session.execute("hang", timeout=60))
    await wait_until(lambda: session._kc.executed)
    cell.cancel()
    await asyncio.gather(cell, return_exceptions=True)
    await wait_until(lambda: session.restarts)

    next_cell = asyncio.create_task(session.execute("print(1)", timeout=5))
    await wait_until(lambda: session._kc.executed)
    session._kc.idle("cell1")
    result = await next_cell
    assert "variables are gone" in result.stderr


@pytest.mark.asyncio
async def test_abandoned_cell_that_finishes_keeps_the_kernel(session, monkeypatch):
    monkeypatch.setattr(kernel_module, "ABANDONED_CELL_GRACE_SECONDS", 1.0)
    client = session._kc
    cell = asyncio.create_task(session.execute("slow", timeout=60))
    await wait_until(lambda: client.executed)
    cell.cancel()
    await asyncio.gather(cell, return_exceptions=True)
    client.idle("cell1")
    await asyncio.sleep(.05)
    await session._settle_task
    assert session.restarts == [] and session._kc is client


@pytest.mark.asyncio
async def test_real_tool_boundary_recovers_a_cell_that_ignores_sigint(session, ws):
    """The whole path: dispatch timeout, the 3s-style cleanup, then the restart."""
    service = ToolExecutionService(settings.model_copy(update={"tool_max_timeout": 900}), cleanup_timeout=.02)
    tool = PythonTool(default_timeout=1, max_timeout=1)  # dispatch and the cell share this limit
    pool = SimpleNamespace(get=lambda cid: session, peek=lambda cid: session)
    ctx = ToolContext(conversation_id="c", workspace=ws, emit=_no_emit, cancel=asyncio.Event(), state={}, kernels=pool,
                      settings=settings, call_id="call")
    result = await service.dispatch(tool, ctx, {"code": "hang"})
    assert not result.ok
    await wait_until(lambda: session.restarts)


async def _no_emit(event):
    return None


@pytest.mark.asyncio
async def test_cancelled_startup_does_not_orphan_the_kernel(ws, monkeypatch):
    import jupyter_client

    created = []

    class Client:
        stopped = False

        def start_channels(self):
            pass

        def stop_channels(self):
            Client.stopped = True

        async def wait_for_ready(self, timeout=None):
            await asyncio.sleep(60)

    class Manager(FakeManager):
        has_kernel = True

        def __init__(self, **_):
            super().__init__()
            created.append(self)

        async def start_kernel(self, **_):
            pass

        def client(self):
            return Client()

    monkeypatch.setattr(jupyter_client, "AsyncKernelManager", Manager)
    item = KernelSession("c", ws)
    starting = asyncio.create_task(item.start())
    await wait_until(lambda: created)
    await asyncio.sleep(.01)
    starting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await starting
    assert created[0].shutdowns == 1 and Client.stopped and item._ipc_dir is None


@pytest.mark.asyncio
async def test_replaced_kernel_ends_the_running_cell_without_blocking_the_loop(session):
    ticks = []

    async def ticker():
        while True:
            ticks.append(1)
            await asyncio.sleep(.001)

    background = asyncio.create_task(ticker())
    old = session._kc
    cell = asyncio.create_task(session.execute("x", timeout=60))
    await wait_until(lambda: old.executed)
    old.closed = True  # the failure mode: every read now raises without suspending
    await asyncio.sleep(.03)
    before = len(ticks)
    await asyncio.sleep(.03)
    assert len(ticks) > before, "the event loop was starved"
    await session.restart()
    result = await asyncio.wait_for(cell, 2)
    background.cancel()
    assert result.status == "restarted" and len(session.restarts) == 1


@pytest.mark.asyncio
async def test_dead_kernel_is_reported_promptly(session):
    cell = asyncio.create_task(session.execute("oom", timeout=600))
    await wait_until(lambda: session._kc.executed)
    session._km.alive = False
    result = await asyncio.wait_for(cell, 2)
    assert result.status == "died" and result.error_name == "KernelDied" and not session.started


@pytest.mark.asyncio
async def test_failing_tool_server_close_does_not_skip_kernel_shutdown(session):
    class Broken:
        async def close(self):
            raise OSError("socket gone")

    manager = session._km
    session._tool_server = Broken()
    await session.shutdown()
    assert manager.shutdowns == 1 and session._km is None


@pytest.mark.asyncio
async def test_reaper_survives_a_failing_shutdown(ws):
    pool = KernelPool(lambda cid: ws, idle_seconds=0)
    pool.reap_interval = .005
    bad = KernelSession("bad", ws)
    bad.started, bad.last_used = True, 0

    async def explode():
        raise RuntimeError("cannot stop")

    bad.shutdown = explode
    pool._sessions["bad"] = bad
    pool.start_reaper()
    await asyncio.sleep(.03)
    status = pool.reaper_status()
    assert status["running"] and status["last_tick_age_seconds"] is not None
    pool._reaper.cancel()


# ── model retries and failure causes ────────────────────────────────────

class Noop(Tool):
    name = "noop"
    description = "Do nothing."
    parameters = {"type": "object", "properties": {}}

    async def run(self, ctx, **_):
        return ToolResult(ok=True, output="done")


def make_harness(tmp_path, provider, **updates):
    db = Database(tmp_path / "h.sqlite")
    config = settings.model_copy(update={"data_dir": tmp_path, "skill_auto_activate": False, "data_agent_enabled": False,
                                         "verify_rounds": 0, "memories_enabled": False, "llm_retry_base_delay": 0.0,
                                         "subagents_enabled": False, **updates})
    # With no tools at all the harness answers in one direct pass; a tool gives the normal worker-then-final flow.
    harness = AgentHarness(db, SimpleNamespace(peek=lambda cid: None), config, FakeRegistry(provider), ToolRegistry([Noop()]))
    return harness, db, db.create_conversation()["id"]


async def run_turn(harness, cid, text="Prove the bound."):
    return [event async for event in harness.run(RunRequest(cid, text))]


def assistant_content(db, cid):
    return next(m["content"] for m in reversed(db.list_messages(cid)) if m["role"] == "assistant")


class Flaky(FakeProvider):
    """Each script entry is a list of deltas, then an exception to raise or None."""

    def __init__(self, attempts):
        super().__init__([])
        self.attempts = list(attempts)

    async def stream(self, messages, tools, options):
        self.calls.append(list(messages))
        deltas, failure = self.attempts.pop(0)
        for delta in deltas:
            yield delta
        if failure is not None:
            raise failure


FINISHED = ([StreamDelta(kind="text", text="All good."), StreamDelta(kind="finish", finish_reason="stop")], None)


@pytest.mark.asyncio
async def test_mid_stream_drop_retries_while_output_is_private(tmp_path):
    provider = Flaky([([StreamDelta(kind="reasoning", text="thinking")], ConnectionError("connection reset")),
                      FINISHED, FINISHED])
    harness, db, cid = make_harness(tmp_path, provider)
    try:
        await run_turn(harness, cid)
        content = assistant_content(db, cid)
        assert content["status"] == "complete" and content["final_text"] == "All good."
        assert len(provider.calls) == 3  # dropped worker attempt, its retry, the final render
    finally:
        await harness.shutdown()
        db.close()


@pytest.mark.asyncio
async def test_no_retry_once_final_text_is_public(tmp_path):
    provider = Flaky([FINISHED, ([StreamDelta(kind="text", text="Half an ans")], ConnectionError("connection reset")), FINISHED])
    harness, db, cid = make_harness(tmp_path, provider)
    try:
        await run_turn(harness, cid)
        content = assistant_content(db, cid)
        assert len(provider.calls) == 2 and content["status"] == "error"
        assert content["final_text"] == "Half an ans"
    finally:
        await harness.shutdown()
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    ([StreamDelta(kind="text", text="cut")], None),  # no finish reason at all
    ([StreamDelta(kind="finish", finish_reason="insufficient_system_resource")], None),
])
async def test_truncated_or_overloaded_streams_are_retried(tmp_path, bad):
    provider = Flaky([bad, FINISHED, FINISHED])
    harness, db, cid = make_harness(tmp_path, provider)
    try:
        await run_turn(harness, cid)
        assert assistant_content(db, cid)["status"] == "complete" and len(provider.calls) == 3
    finally:
        await harness.shutdown()
        db.close()


def test_retry_classification_of_new_failures():
    assert classify(IncompleteStream("cut")).retryable
    assert classify(RuntimeError("Provider ended the stream with insufficient_system_resource")).retryable
    assert not classify(RuntimeError("Provider ended the stream with error")).retryable


class Status(Exception):
    def __init__(self, status, text="sk-secret-value"):
        super().__init__(text)
        self.status_code = status


@pytest.mark.parametrize(("error", "category"), [
    (Status(401), "auth"), (Status(402), "quota"), (Status(429), "rate_limit"), (Status(503), "provider"),
    (Status(400, "maximum context length"), "context"), (ConnectionError("connection reset"), "network"),
    (ValueError("nope"), "unknown"),
])
def test_failure_categories(error, category):
    assert failure_category(error) == category


@pytest.mark.asyncio
async def test_failure_cause_reaches_the_saved_message_and_the_terminal_event(tmp_path):
    provider = Flaky([([], Status(402))])
    harness, db, cid = make_harness(tmp_path, provider)
    manager = RunManager(db, harness)
    try:
        run = await manager.start(RunRequest(cid, "Prove the bound."))
        frames = [event.to_dict() async for event in manager.subscribe(run["id"]) if event is not None]
        terminal = frames[-1]
        assert terminal["type"] == "error" and "out of credit" in terminal["message"]
        assert "sk-secret" not in json.dumps(frames)
        assert "out of credit" in assistant_content(db, cid)["notice"]
    finally:
        await manager.shutdown()
        await harness.shutdown()
        db.close()


# ── run manager ─────────────────────────────────────────────────────────

class ScriptedHarness:
    def __init__(self, script):
        self.script, self.active = script, set()

    def is_running(self, cid):
        return False

    def stop(self, cid):
        return False

    async def prepare_session(self, cid):
        if cid == "bad":
            raise ValueError("corrupt session")

    async def persist_recovered_run(self, cid, mid, content):
        pass

    async def run(self, req, *, on_claim=None):
        for item in self.script:
            if isinstance(item, (int, float)):
                await asyncio.sleep(item)
            else:
                yield item
        yield ev("done", message_id="m")


@pytest.fixture
def db(tmp_path):
    item = Database(tmp_path / "r.sqlite")
    yield item
    item.close()


async def frames(manager, run_id):
    return [event.to_dict() async for event in manager.subscribe(run_id) if event is not None]


@pytest.mark.asyncio
async def test_text_deltas_are_coalesced_in_order(db):
    script = [ev("final_start", message_id="m", mode="streaming"),
              *[ev("text_delta", delta=f"{i} ") for i in range(200)],
              ev("usage", prompt_tokens=1), ev("text_delta", delta="tail")]
    manager = RunManager(db, ScriptedHarness(script))
    run = await manager.start(RunRequest(None, "q"))
    await manager.wait(run["id"])
    rows = await frames(manager, run["id"])
    deltas = [row for row in rows if row["type"] == "text_delta"]
    assert len(deltas) <= 3
    assert "".join(row["delta"] for row in deltas) == "".join(f"{i} " for i in range(200)) + "tail"
    kinds = [row["type"] for row in rows]
    assert kinds.index("usage") > kinds.index("text_delta") and kinds.index("usage") < len(kinds) - 1
    await manager.shutdown()


@pytest.mark.asyncio
async def test_buffered_text_is_published_during_a_quiet_stream(db):
    script = [ev("final_start", message_id="m", mode="streaming"), ev("text_delta", delta="visible"), 5.0]
    manager = RunManager(db, ScriptedHarness(script), delta_flush_seconds=.01)
    run = await manager.start(RunRequest(None, "q"))
    await wait_until(lambda: any(r["type"] == "text_delta" for r in db.list_run_events(run["id"])))
    await manager.shutdown()


@pytest.mark.asyncio
async def test_one_unrecoverable_run_does_not_block_startup(db):
    for cid, run_id in (("bad", "r_bad"), ("good", "r_good")):
        db.create_conversation(conversation_id=cid)
        db.create_run(run_id, cid, "hash")
    await RunManager(db, ScriptedHarness([])).recover()
    assert {db.get_run(r)["status"] for r in ("r_bad", "r_good")} == {"interrupted"}
    assert db.list_run_events("r_bad")[-1]["type"] == "error"


def test_sqlite_uses_normal_sync(db):
    assert db._conn.execute("PRAGMA synchronous").fetchone()[0] == 1


def test_retention_prunes_only_old_finished_journals(db):
    for cid, run_id, status in (("c1", "old", "completed"), ("c2", "live", "running")):
        db.create_conversation(conversation_id=cid)
        db.create_run(run_id, cid, "h")
        db.append_run_event(run_id, "work", {}, status=status)
    assert db.prune_run_events(time.time() - 3600) == 0
    assert db.prune_run_events(time.time() + 3600, batch_size=1) == 1
    assert db.list_run_events("old") == [] and db.list_run_events("live") != []
    assert db.get_run("old")["status"] == "completed"


@pytest.mark.asyncio
async def test_quiet_run_is_reported_once_and_not_stopped(db, monkeypatch, caplog):
    reported = []
    monkeypatch.setattr(otel, "export_stalled_run", lambda *a: reported.append(a))
    manager = RunManager(db, ScriptedHarness([60.0]))
    run = await manager.start(RunRequest(None, "q"))
    await wait_until(lambda: db.get_run(run["id"])["status"] == "running")
    with caplog.at_level(logging.WARNING, logger="app.agent.runs"):
        assert manager.check_stalled(0.0) == 1
        assert manager.check_stalled(0.0) == 1
    assert len(reported) == 1 and manager.active_count == 1
    assert manager.check_stalled(3600.0) == 0
    await manager.shutdown()


def test_stalled_span_is_emitted():
    spans = []

    class Span:
        def end(self):
            spans.append("ended")

    tracer = SimpleNamespace(start_span=lambda name, attributes: spans.append((name, attributes)) or Span())
    assert otel.export_stalled_run("c", "r", 12.0, tracer=tracer)
    assert spans[0][0] == "matopt.run.stalled" and spans[0][1]["matopt.run_id"] == "r" and spans[1] == "ended"


# ── sub-agents ──────────────────────────────────────────────────────────

class ParentThenChild(FakeProvider):
    def __init__(self):
        super().__init__([])
        self.parent_steps = 0

    async def stream(self, messages, tools, options):
        system = messages[0].get("content") or ""
        if "You are Ada" in system:
            self.script = [{"text": "child result"}]
        else:
            self.parent_steps += 1
            self.script = [{"tool": "spawn_agent", "args": {"task_name": "one", "message": "Go.", "role": "default"}}] \
                if self.parent_steps == 1 else [{"text": "parent done"}]
        async for delta in super().stream(messages, tools, options):
            yield delta


def subagent_rig(tmp_path, **updates):
    db = Database(tmp_path / "a.sqlite")
    config = settings.model_copy(update={"data_dir": tmp_path, "skill_auto_activate": False, "data_agent_enabled": False,
                                         "subagents_enabled": True, "verify_rounds": 0, "memories_enabled": False, **updates})
    provider = ParentThenChild()
    harness = AgentHarness(db, SimpleNamespace(peek=lambda cid: None), config, FakeRegistry(provider),
                           ToolRegistry([*subagent_tools()]))
    return harness, db, db.create_conversation()["id"]


@pytest.mark.asyncio
async def test_child_spend_is_booked_to_the_parent_log(tmp_path):
    from app.agent.session.telemetry import project_usage

    harness, db, cid = subagent_rig(tmp_path)
    try:
        await run_turn(harness, cid, "Delegate this.")
        await harness.wait_side_tasks()
        events = db.list_session_events(cid)
        booked = [e for e in events if e["type"] == "llm/side-task" and e["data"].get("task") == "subagent"]
        assert len(booked) == 1
        assert booked[0]["data"]["status"] == "completed" and booked[0]["data"]["usage"]["total_tokens"] == 15
        coverage = project_usage(events)["coverage"]
        assert coverage["attempts"] >= 4 and coverage["reported_attempts"] >= 4  # spawn, child, answer, final render
    finally:
        await harness.shutdown()
        db.close()


@pytest.mark.asyncio
async def test_cost_cap_applies_to_children(tmp_path):
    harness, db, cid = subagent_rig(tmp_path, max_turn_cost=0.0)
    try:
        # With a cap and no prices the parent is refused before any dispatch,
        # and so is any child consulting the same ledger.
        log = await harness._open_session(cid)
        log.append("turn/start", {"turn": 1, "message_id": "m"})
        from app.agent.harness import BudgetExceeded, TurnState
        from app.llm.base import ChatOptions
        state = TurnState()
        state.is_subagent, state.spend_log, state.turn = True, log, 1
        with pytest.raises(BudgetExceeded):
            await harness._stream_llm(ParentThenChild(), ChatOptions(model="m"), [{"role": "system", "content": ""}],
                                      None, _no_emit, state, asyncio.Event())
    finally:
        await harness.shutdown()
        db.close()


class ChildUsesTool(FakeProvider):
    """The parent delegates once; its child calls `child_tool`, then reports."""

    def __init__(self):
        super().__init__([])
        self.parent_steps = 0

    async def stream(self, messages, tools, options):
        if "You are Ada" in (messages[0].get("content") or ""):
            done = any(m.get("role") == "tool" for m in messages)
            self.script = [{"text": "child result"}] if done else [{"tool": "child_tool", "args": {"value": "x"}}]
        else:
            self.parent_steps += 1
            self.script = [{"tool": "spawn_agent", "args": {"task_name": "one", "message": "Go.", "role": "default"}}] \
                if self.parent_steps == 1 else [{"text": "parent done"}]
        async for delta in super().stream(messages, tools, options):
            yield delta


class ChildTool(Tool):
    name = "child_tool"
    description = "Echo a value."
    parameters = {"type": "object", "properties": {"value": {"type": "string"}}}

    async def run(self, ctx, value="", **_):
        return ToolResult(ok=True, output=f"echo {value}")


@pytest.mark.asyncio
async def test_child_tool_calls_survive_in_the_parent_log_without_touching_usage(tmp_path):
    from app.agent.session.telemetry import _group_turns, project_usage

    harness, db, cid = subagent_rig(tmp_path)
    harness.providers = FakeRegistry(ChildUsesTool())
    harness.tools.register(ChildTool())
    try:
        await run_turn(harness, cid, "Delegate this.")
        await harness.wait_side_tasks()
        events = db.list_session_events(cid)
        validate_events(events)
        call = next(e for e in events if e["type"] == "subagent/tool-call")
        result = next(e for e in events if e["type"] == "subagent/tool-result")
        assert call["data"]["normalized_arguments"] == {"value": "x"} and call["data"]["callId"] == result["data"]["callId"]
        assert result["data"]["ok"] is True and result["data"]["output"] == "echo x"
        assert call["ignorable"] is True and call["seq"] < result["seq"]
        assert call["data"]["agent"].startswith("agent_") and "turn" not in call["data"]
        assert not any(e["type"].startswith("subagent/") for rows in _group_turns(events).values() for e in rows)
        assert project_usage(events)["coverage"]["attempts"] >= 4
    finally:
        await harness.shutdown()
        db.close()


@pytest.mark.asyncio
async def test_child_tool_record_cannot_fail_the_child(tmp_path):
    from app.agent.harness import TurnState, _book_child_tool

    log = started_log()
    state = TurnState()
    state.spend_log, state.turn, state.step = log, 1, 1
    before = log.last_seq
    await _book_child_tool(state, "subagent/tool-result", {"meta": {"bad": object()}})
    assert log.last_seq == before


# ── provider catalog ────────────────────────────────────────────────────

def test_dispatch_is_decided_by_the_catalog(monkeypatch):
    from dataclasses import replace

    from app.llm import catalog
    from app.llm.base import ChatOptions as Options
    from app.llm.openai_compat import OpenAICompatProvider
    from app.llm.registry import ProviderRegistry

    base = catalog.PROVIDERS["deepseek"]
    model = replace(base.default_model, id="other-model", max_output=100)
    other = replace(base, id="other", env_key="OTHER_API_KEY", default_base_url="https://other.invalid/v1", models=(model,))
    with pytest.raises(ValueError):
        OpenAICompatProvider(other, "k", None)
    monkeypatch.setitem(catalog.PROVIDERS, "other", other)
    monkeypatch.setattr("app.llm.registry.settings", SimpleNamespace(other_api_key="k", llm_timeout=5.0))
    registry = ProviderRegistry()
    assert registry.api_key("other") == "k" and registry.base_url("other") == "https://other.invalid/v1"
    provider = registry.get("other")
    assert provider.id == "other" and str(provider.client.base_url).startswith("https://other.invalid")
    assert provider._request_params(Options(model="other-model", max_tokens=100))[0]["max_tokens"] == 100
    with pytest.raises(ValueError):
        provider._request_params(Options(model="other-model", max_tokens=101))
    with pytest.raises(ValueError):
        provider._request_params(Options(model="deepseek-flash"))


def test_context_limits_come_from_the_model_spec(tmp_path):
    from app.llm.catalog import default_model_spec

    harness, db, _ = make_harness(tmp_path, FakeProvider([]))
    try:
        reserve = max(harness.settings.context_reserve_tokens, harness.settings.max_output_tokens or 0)
        assert harness._context_limits(200_000)[1] == 200_000 - reserve
        assert harness._context_limits()[1] == default_model_spec().context_window - reserve
    finally:
        db.close()


def test_last_event_is_a_private_copy():
    log = started_log()
    event = log.last_event
    assert event == log.events[-1]
    event["data"]["turn"] = 99
    assert log.events[-1]["data"]["turn"] == 1
    assert SessionLog().last_event is None


# ── notebook ────────────────────────────────────────────────────────────

def cell_result(stdout="out"):
    return ExecResult(stdout=stdout)


def test_failed_write_keeps_the_previous_notebook(ws, monkeypatch):
    import nbformat

    notebook.append_analysis_cell(ws, "a = 1", cell_result())
    path = ws.root / notebook.NOTEBOOK_NAME
    original = path.read_bytes()

    def torn(nb, out):
        out.write("{ torn")
        raise OSError("disk full")

    monkeypatch.setattr(nbformat, "write", torn)
    with pytest.raises(OSError):
        notebook.append_analysis_cell(ws, "b = 2", cell_result())
    assert path.read_bytes() == original
    assert not list(ws.root.glob(".*.tmp"))


def test_unreadable_notebook_is_kept_not_discarded(ws):
    path = ws.root / notebook.NOTEBOOK_NAME
    path.write_text("{ not json")
    notebook.append_analysis_cell(ws, "a = 1", cell_result())
    kept = list(ws.root.glob(f".{notebook.NOTEBOOK_NAME}.corrupt-*"))
    assert len(kept) == 1 and kept[0].read_text() == "{ not json"
    assert "".join(json.loads(path.read_text())["cells"][0]["source"]) == "a = 1"
    assert not any(notebook.NOTEBOOK_NAME in rel and "corrupt" in rel for rel in ws.snapshot())


# ── blocking work stays off the loop ────────────────────────────────────

@pytest.mark.asyncio
async def test_python_tool_scans_the_workspace_off_the_loop(session, ws, monkeypatch):
    threads = []
    real = Workspace.snapshot

    def spy(self):
        threads.append(threading.current_thread())
        return real(self)

    monkeypatch.setattr(Workspace, "snapshot", spy)
    pool = SimpleNamespace(get=lambda cid: session)
    ctx = ToolContext(conversation_id="c", workspace=ws, emit=_no_emit, cancel=asyncio.Event(), state={}, kernels=pool,
                      settings=settings, call_id="call")

    async def finish():
        await wait_until(lambda: session._kc.executed)
        session._kc.idle("cell1")

    finisher = asyncio.create_task(finish())
    await PythonTool().run(ctx, "x = 1")
    await finisher
    assert len(threads) == 2 and threading.main_thread() not in threads


@pytest.mark.asyncio
async def test_notebook_write_happens_off_the_loop(session, monkeypatch):
    threads = []
    monkeypatch.setattr("app.agent.notebook.append_analysis_cell", lambda *a: threads.append(threading.current_thread()))
    cell = asyncio.create_task(session.execute("x", timeout=5))
    await wait_until(lambda: session._kc.executed)
    session._kc.idle("cell1")
    await cell
    assert threads and threading.main_thread() not in threads


def test_upload_copy_and_registration_are_separable(tmp_path):
    from app.agent.tools.data_agent_tool import _copy_workspace_uploads

    uploads, dest = tmp_path / "uploads", tmp_path / "dest"
    uploads.mkdir()
    (uploads / "a.csv").write_text("1")
    (uploads / "sub").mkdir()
    assert _copy_workspace_uploads(uploads, dest) == [(uploads / "a.csv", dest / "matopt_a.csv")]
    assert (dest / "matopt_a.csv").read_text() == "1"
    shutil.rmtree(uploads)
    assert _copy_workspace_uploads(uploads, dest) == []


# ── approvals and kernel isolation ──────────────────────────────────────

class RecordingPython(Tool):
    name = "python"
    description = "Run code."
    parameters = {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}
    ran: list = []

    async def run(self, ctx, code="", **_):
        RecordingPython.ran.append(code)
        return ToolResult(ok=True, output="ran")


@pytest.mark.asyncio
async def test_exec_rules_refuse_with_the_reviewer_off(tmp_path):
    RecordingPython.ran = []
    provider = FakeProvider([{"tool": "python", "args": {"code": 'import os\nos.system("sudo rm x")'}},
                             {"text": "Refused."}, {"text": "Refused."}])
    harness, db, cid = make_harness(tmp_path, provider, approvals_reviewer="off")
    harness.tools.register(RecordingPython())
    try:
        await run_turn(harness, cid, "Clean up.")
        call = next(p for p in assistant_content(db, cid)["parts"] if p["type"] == "tool_call")
        assert RecordingPython.ran == [] and call["result"]["ok"] is False
        assert call["result"]["meta"]["code"] == "TOOL_APPROVAL_DENIED"
    finally:
        await harness.shutdown()
        db.close()


@pytest.mark.asyncio
async def test_ordinary_code_still_runs_with_the_reviewer_off(tmp_path):
    RecordingPython.ran = []
    provider = FakeProvider([{"tool": "python", "args": {"code": "1 + 1"}}, {"text": "Two."}, {"text": "Two."}])
    harness, db, cid = make_harness(tmp_path, provider, approvals_reviewer="off")
    harness.tools.register(RecordingPython())
    try:
        await run_turn(harness, cid, "Add.")
        assert RecordingPython.ran == ["1 + 1"]
    finally:
        await harness.shutdown()
        db.close()


async def captured_ipc_dir(ws, monkeypatch, temp_dir):
    import jupyter_client
    import app.agent.sandbox as sandbox

    class Stop(Exception):
        pass

    class Manager:
        def __init__(self, **_):
            pass

        async def start_kernel(self, **kwargs):
            raise Stop(kwargs["env"]["IPYTHONDIR"])

    monkeypatch.setattr(jupyter_client, "AsyncKernelManager", Manager)
    monkeypatch.setattr(sandbox, "build_policy", lambda *a, **k: sandbox.SandboxPolicy(mode="off", temp_dir=temp_dir))
    with pytest.raises(Stop) as stopped:
        await KernelSession("c", ws).start()
    return str(stopped.value)


@pytest.mark.asyncio
async def test_kernel_sockets_live_in_the_conversation_temp_dir(ws, tmp_path, monkeypatch):
    own = tmp_path / "conversation-temp"
    own.mkdir()
    assert (await captured_ipc_dir(ws, monkeypatch, str(own))).startswith(str(own) + "/k")


@pytest.mark.asyncio
async def test_kernel_sockets_stay_in_the_temp_root_without_a_sandbox(ws, monkeypatch):
    assert "/mo-k" in await captured_ipc_dir(ws, monkeypatch, None)


# ── observability ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_log_lines_carry_ids_into_child_tasks(caplog):
    install_log_context()
    logger = logging.getLogger("tests.reliability")

    async def child():
        logger.warning("100%% of %s", "cells")

    async def run():
        bind_log_context(conversation_id="c_1", run_id="r_1")
        await asyncio.create_task(child())

    with caplog.at_level(logging.WARNING, logger="tests.reliability"):
        await asyncio.create_task(run())
    assert caplog.messages[-1] == "100% of cells [conversation=c_1 run=r_1]"
    assert caplog.records[-1].run_id == "r_1"
    assert log_context_suffix() == ""


@pytest.mark.asyncio
async def test_health_reports_background_liveness():
    from app.runtime import Runtime

    runtime = Runtime.__new__(Runtime)
    runtime._lag_samples = collections.deque([.002, .25], maxlen=120)
    runtime._started, runtime._stalled_runs = True, 1
    runtime.runs = SimpleNamespace(active_count=2)
    runtime.kernels = SimpleNamespace(reaper_status=lambda: {"running": False, "last_tick_age_seconds": 61.0})
    health = runtime.health()
    assert health["status"] == "degraded" and health["kernel_reaper"]["running"] is False
    assert health["event_loop_lag_ms"] == {"last": 250.0, "max_last_minute": 250.0}
    assert health["runs"] == {"active": 2, "stalled": 1}


def test_a_stopped_runtime_is_not_served_again():
    """A TestClient lifespan closes the database; the next caller used to inherit it."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.runtime import get_runtime

    with TestClient(app):
        stopped = get_runtime()
    current = get_runtime()
    assert current is not stopped
    assert current.db.get_conversation("missing") is None


@pytest.mark.asyncio
async def test_maintenance_refreshes_stalled_count_and_prunes():
    from app.runtime import Runtime

    runtime = Runtime.__new__(Runtime)
    pruned = []
    runtime.db = SimpleNamespace(prune_run_events=lambda cutoff: pruned.append(cutoff) or 3)
    runtime.runs = SimpleNamespace(check_stalled=lambda seconds: 4)
    await runtime.maintain(True)
    await runtime.maintain(False)
    assert runtime._stalled_runs == 4 and len(pruned) == 1
