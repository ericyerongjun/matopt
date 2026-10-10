"""Shared tool/OCR budget boundaries with deterministic gates and fake providers."""
import asyncio
import dataclasses
import os
import threading
from types import SimpleNamespace

import pymupdf
import pytest

from app.agent.budget import BudgetExhausted, RunBudget
from app.agent.events import ToolResult
from app.agent.execution import SessionExecutionReceipt, ToolExecutionService
from app.agent.session import SessionLog
from app.agent.tools.base import Tool, ToolContext
from app.agent.tools.document_tool import ParseDocumentTool
from app.agent.tools.read_state import STATE_KEY
from app.agent.workspace import Workspace
from app.config import settings
from app.llm.base import CompletionResult


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def context(tmp_path):
    events = []

    async def emit(event):
        events.append(event)

    return ToolContext("budget", Workspace("budget", tmp_path), emit, asyncio.Event(),
                       call_id="call", settings=settings), events


def service(timeout=30):
    return ToolExecutionService(SimpleNamespace(align_monitor=True, tool_timeout=timeout, tool_max_timeout=timeout),
                                cleanup_timeout=.01)


class CounterTool(Tool):
    name = "count"
    read_only = True
    parameters = {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}

    def __init__(self):
        self.calls = 0

    async def run(self, ctx, value):
        self.calls += 1
        return ToolResult(ok=True, output=str(value))


@pytest.mark.asyncio
async def test_exhausted_admission_is_durable_without_running_body_or_preflight(context):
    ctx, events = context
    budget = ctx.state["run_budget"] = RunBudget(tool_start_limit=0)
    log, tool, preflights = SessionLog(), CounterTool(), []

    async def preflight(*_):
        preflights.append(True)

    result = await service().dispatch(tool, ctx, {"value": 1}, before_execute=preflight,
                                      receipt=SessionExecutionReceipt(log))
    assert result.meta["code"] == "RUN_BUDGET_EXHAUSTED"
    assert (result.meta["budget_dimension"], result.meta["budget_limit"], result.meta["budget_used"]) == ("tool_starts", 0, 0)
    assert result.meta["dispatched"] is False
    assert not tool.calls and not events and not preflights
    assert budget.snapshot()["tool_starts"] == 0
    assert [event["type"] for event in log.events] == ["execution/result"]
    assert log.events[-1]["data"]["meta"]["code"] == "RUN_BUDGET_EXHAUSTED"


@pytest.mark.asyncio
async def test_validation_capability_and_policy_rejections_do_not_consume_admission(context):
    from app.agent.tools.shell_tool import ShellTool

    ctx, events = context
    budget = ctx.state["run_budget"] = RunBudget(tool_start_limit=1)
    tool, execute = CounterTool(), service()
    assert (await execute.dispatch(tool, ctx, {"value": "bad"})).meta["code"] == "TOOL_ARGUMENTS_INVALID"
    tool.requires_flags = ("enabled",)
    assert (await execute.dispatch(tool, ctx, {"value": 1})).meta["code"] == "TOOL_DISABLED"
    assert (await execute.dispatch(ShellTool(), ctx, {"command": "shutdown now"})).meta["code"] == "TOOL_POLICY_BLOCKED"
    assert budget.snapshot()["tool_starts"] == 0
    assert not any(event.type == "tool_running" for event in events)
    ctx.state["enabled"] = True
    assert (await execute.dispatch(tool, ctx, {"value": 1})).ok


@pytest.mark.asyncio
async def test_shared_parallel_one_slot_and_explicit_ids_for_reused_provider_call(context):
    ctx, events = context
    budget = ctx.state["run_budget"] = RunBudget(tool_start_limit=1)
    tool, execute = CounterTool(), service()
    results = await asyncio.gather(*(execute.dispatch(tool, dataclasses.replace(ctx, call_id=f"call-{index}"),
                               {"value": index}, admission_id=f"native:1:1:{index}") for index in range(12)))
    assert sum(result.ok for result in results) == 1
    assert sum(result.meta.get("code") == "RUN_BUDGET_EXHAUSTED" for result in results) == 11
    assert tool.calls == budget.snapshot()["tool_starts"] == 1
    assert sum(event.type == "tool_running" for event in events) == 1
    # Provider call IDs need not be unique between steps. The execution key is.
    ctx.state["run_budget"] = RunBudget(tool_start_limit=2)
    assert (await execute.dispatch(tool, ctx, {"value": 2}, admission_id="native:1:2:call")).ok
    duplicate = await execute.dispatch(tool, ctx, {"value": 2}, admission_id="native:1:2:call")
    assert duplicate.meta["code"] == "TOOL_ALREADY_ADMITTED" and duplicate.meta["dispatched"] is False
    assert (await execute.dispatch(tool, ctx, {"value": 3}, admission_id="native:1:3:call")).ok
    assert tool.calls == 3


class CooperativeMutation(Tool):
    name = "mutation"
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}}

    def __init__(self):
        self.entered = asyncio.Event()
        self.exited = asyncio.Event()

    async def run(self, ctx, path):
        self.entered.set()
        try:
            await ctx.cancel.wait()
            return ToolResult(ok=False, output="Observed partial work", error="Stopped")
        finally:
            self.exited.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("global_deadline", [True, False])
async def test_deadline_clipping_stops_body_and_preserves_unknown_mutation_receipt(context, monkeypatch, global_deadline):
    ctx, _ = context
    clock = Clock()
    budget = ctx.state["run_budget"] = RunBudget(timeout_seconds=2 if global_deadline else 20, clock=clock)
    ctx.state[STATE_KEY] = {"invoice.txt": object()}
    tool, log, execute = CooperativeMutation(), SessionLog(), service(timeout=30 if global_deadline else 1)
    actual_wait = asyncio.wait
    observed = []

    async def gated_wait(tasks, *, timeout=None, return_when=asyncio.ALL_COMPLETED):
        if not observed and any(task.get_name().startswith("tool:") for task in tasks):
            observed.append(timeout)
            await tool.entered.wait()
            clock.now += 2 if global_deadline else 1
            return set(), set(tasks)
        return await actual_wait(tasks, timeout=timeout, return_when=return_when)

    monkeypatch.setattr(asyncio, "wait", gated_wait)
    result = await execute.dispatch(tool, ctx, {"path": "invoice.txt"}, receipt=SessionExecutionReceipt(log))
    assert observed == [2 if global_deadline else 1]
    assert tool.exited.is_set() and not execute._unsettled
    assert result.output == "Observed partial work"
    assert result.meta["code"] == ("RUN_BUDGET_EXHAUSTED" if global_deadline else "TOOL_TIMEOUT")
    if global_deadline:
        assert result.meta["budget_dimension"] == "deadline"
    assert result.meta["recovery_code"] == "TOOL_OUTCOME_UNKNOWN"
    assert result.meta["read_state_invalidated"] == ["invoice.txt"]
    assert not ctx.state.get(STATE_KEY)
    assert log.events[-1]["data"]["meta"] == result.meta
    assert budget.snapshot()["tool_starts"] == 1
    retry = await execute.dispatch(tool, ctx, {"path": "invoice.txt"}, admission_id="retry")
    assert retry.meta["recovery_code"] == "RECOVERY_CHECK_REQUIRED"


@pytest.mark.asyncio
async def test_tool_control_error_retains_nullable_observed_spend_metadata(context):
    ctx, _ = context

    class SpendGuardTool(CounterTool):
        async def run(self, ctx, value):
            raise BudgetExhausted("observed_spend", limit=None, used=None, detail="Observed spend limit reached.")

    log = SessionLog()
    result = await service().dispatch(SpendGuardTool(), ctx, {"value": 1}, receipt=SessionExecutionReceipt(log))
    assert result.meta["code"] == "RUN_BUDGET_EXHAUSTED"
    assert result.meta["budget_dimension"] == "observed_spend"
    assert result.meta["budget_limit"] is None and result.meta["budget_used"] is None
    assert result.meta["dispatched"] is True
    assert log.events[-1]["data"]["meta"]["code"] == "RUN_BUDGET_EXHAUSTED"


def make_pdf(ctx, pages=2):
    with pymupdf.open() as document:
        for number in range(pages):
            page = document.new_page()
            page.insert_text((40, 40), f"Native fallback canary page {number + 1}")
        return ctx.workspace.save_upload("budget.pdf", document.tobytes())


class Vision:
    def __init__(self):
        self.calls = 0

    async def complete(self, messages, options):
        self.calls += 1
        return CompletionResult("Visible source text", {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10}, "stop")


def install_ocr_recorder(ctx, budget):
    recorded = []

    async def recorder(event):
        if event["status"] == "started":
            admission = budget.admit_model(event["task_id"], source="ocr", provider=event["provider"], model=event["model"])
            assert admission.is_new
        else:
            budget.settle_model(event["task_id"], usage=event.get("usage"), outcome=event["status"])
        recorded.append(event)

    ctx.state["document_record_call"] = recorder
    return recorded


@pytest.mark.asyncio
async def test_ocr_model_allowance_stops_before_an_extra_page_without_fallback_or_double_charge(context):
    ctx, _ = context
    budget = ctx.state["run_budget"] = RunBudget(model_attempt_limit=1, tool_start_limit=1)
    provider = ctx.state["document_provider"] = Vision()
    records = install_ocr_recorder(ctx, budget)
    src = make_pdf(ctx)
    result = await service().dispatch(ParseDocumentTool(), ctx, {"path": ctx.workspace.relpath(src)})
    assert result.meta["code"] == "RUN_BUDGET_EXHAUSTED" and result.meta["budget_dimension"] == "model_attempts"
    assert provider.calls == 1
    assert [record["status"] for record in records] == ["started", "completed"]
    snapshot = budget.snapshot()
    assert snapshot["model_attempts"] == snapshot["tool_starts"] == 1
    assert snapshot["usage"]["exact"]["total_tokens"] == 10
    assert not list((ctx.workspace.root / "parsed").glob("*.md"))
    assert "Native fallback canary" not in result.output


@pytest.mark.asyncio
async def test_ocr_global_deadline_cancels_provider_and_propagates_control_without_native_fallback(context, monkeypatch):
    ctx, _ = context
    clock = Clock()
    budget = ctx.state["run_budget"] = RunBudget(timeout_seconds=4, clock=clock)
    entered, exited = asyncio.Event(), asyncio.Event()

    class PausedVision(Vision):
        async def complete(self, messages, options):
            self.calls += 1
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                exited.set()

    provider = ctx.state["document_provider"] = PausedVision()
    records = install_ocr_recorder(ctx, budget)
    src = make_pdf(ctx)
    actual_wait, observed = asyncio.wait, []

    async def gated_wait(tasks, *, timeout=None, return_when=asyncio.ALL_COMPLETED):
        if any("PausedVision.complete" in getattr(task.get_coro(), "__qualname__", "") for task in tasks):
            observed.append(timeout)
            await entered.wait()
            clock.now += 4
            return set(), set(tasks)
        return await actual_wait(tasks, timeout=timeout, return_when=return_when)

    monkeypatch.setattr(asyncio, "wait", gated_wait)
    with pytest.raises(BudgetExhausted) as error:
        await ParseDocumentTool()(ctx, {"path": ctx.workspace.relpath(src)})
    assert error.value.dimension == "deadline" and observed == [4]
    assert provider.calls == 1 and exited.is_set()
    assert [record["status"] for record in records] == ["started", "aborted"]
    assert budget.snapshot()["usage"]["unknown_settled_attempts"] == 1
    assert not list((ctx.workspace.root / "parsed").glob("*.md"))


@pytest.mark.asyncio
async def test_expired_document_budget_never_starts_planning_or_fallback(context, monkeypatch):
    from app.agent.tools import document_tool

    ctx, _ = context
    clock = Clock()
    ctx.state["run_budget"] = RunBudget(timeout_seconds=1, clock=clock)
    clock.now += 1
    calls = []
    monkeypatch.setattr(document_tool, "plan_document", lambda *_: calls.append("plan"))
    src = make_pdf(ctx, pages=1)
    with pytest.raises(BudgetExhausted):
        await ParseDocumentTool()(ctx, {"path": ctx.workspace.relpath(src), "backend": "fast"})
    assert not calls


@pytest.mark.asyncio
async def test_stopped_fast_extractor_keeps_workspace_owned_until_thread_exits(context, monkeypatch):
    ctx, _ = context
    entered, release, wrote = threading.Event(), threading.Event(), threading.Event()
    marker = ctx.workspace.root / "parsed" / "still-owned.txt"

    def extract(*_):
        entered.set()
        assert release.wait(5), "test must release its extractor thread"
        marker.write_text("Extraction can finish after Stop, but not after ownership is released.")
        wrote.set()
        return "# Extracted text"

    monkeypatch.setattr(ParseDocumentTool, "_run_fast", staticmethod(extract))
    execute = service()
    src = make_pdf(ctx, pages=1)
    task = asyncio.create_task(execute.dispatch(ParseDocumentTool(), ctx,
        {"path": ctx.workspace.relpath(src), "backend": "fast"}))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        ctx.cancel.set()
        result = await asyncio.wait_for(task, 2)
        assert result.meta["code"] == "TOOL_CANCELLED"
        assert result.meta["recovery_code"] == "TOOL_OUTCOME_UNKNOWN"
        assert execute._unsettled and not wrote.is_set()
        # A new request must not reuse the workspace while the stopped writer
        # is alive, even if another cancellation reaches its cleanup task.
        for owner in execute._unsettled:
            owner.cancel()
        next_ctx = dataclasses.replace(ctx, cancel=asyncio.Event(), call_id="next")
        blocked = await execute.dispatch(CounterTool(), next_ctx, {"value": 1})
        assert blocked.meta["code"] == "TOOL_EXECUTION_UNSETTLED"
        owners = list(execute._unsettled)
        release.set()
        await asyncio.wait_for(asyncio.gather(*owners, return_exceptions=True), 2)
        await asyncio.sleep(0)
        assert wrote.is_set() and not execute._unsettled
        assert (await execute.dispatch(CounterTool(), next_ctx, {"value": 1})).ok
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await execute.shutdown()


@pytest.mark.asyncio
async def test_mineru_deadline_reaps_started_process_group(context, monkeypatch, tmp_path):
    ctx, _ = context
    clock = Clock()
    ctx.state["run_budget"] = RunBudget(timeout_seconds=1, clock=clock)
    executable = tmp_path / "mineru-test"
    executable.write_text("#!/bin/sh\ni=0\nwhile [ $i -lt 20 ]; do echo $$; i=$((i+1)); done\nexec sleep 30\n")
    executable.chmod(0o700)
    child_started, pids = asyncio.Event(), []

    async def emit(event):
        if event.type == "tool_progress" and event.data["delta"].strip().isdigit():
            pids.append(int(event.data["delta"].strip()))
            child_started.set()

    ctx.emit = emit
    actual_wait = asyncio.wait

    async def gated_wait(tasks, *, timeout=None, return_when=asyncio.ALL_COMPLETED):
        if any("_run_mineru.<locals>.collect" in getattr(task.get_coro(), "__qualname__", "") for task in tasks):
            assert timeout == 1
            await asyncio.wait_for(child_started.wait(), 3)
            clock.now += 1
            return set(), set(tasks)
        return await actual_wait(tasks, timeout=timeout, return_when=return_when)

    monkeypatch.setattr(asyncio, "wait", gated_wait)
    with pytest.raises(BudgetExhausted):
        await ParseDocumentTool()._run_mineru(str(executable), make_pdf(ctx, 1), tmp_path / "out", None, "en", ctx)
    assert len(pids) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(pids[0], 0)
