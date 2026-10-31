"""One execution boundary for native calls and the MCP sandbox adapter.

The caller owns scheduling and its session lifecycle. This service owns the
validated execution fact, policy, cancellation and deadline. Receipt hooks let
both transports checkpoint in the existing SessionLog; no second receipt store
or provider transcript is invented for external MCP calls.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import math
import time
from typing import Any, Awaitable, Callable, Protocol

from jsonschema import Draft202012Validator, ValidationError, SchemaError

from app.agent.budget import BudgetExhausted
from app.agent.events import ToolResult, ev
from app.agent.exec_output import redact_secrets
from app.agent.monitor import inspect_tool_call
from app.agent.recovery_guard import RecoveryGuard
from app.agent.session import SessionLog
from app.agent.session.derive import fold_surface
from app.agent.session.events import snapshot_json
from app.agent.tools.base import Tool, ToolContext
from app.agent.tools.read_state import STATE_KEY, clear_read_state, restore_read_state

logger = logging.getLogger(__name__)


class ExecutionReceipt(Protocol):
    async def started(self, tool: Tool, ctx: ToolContext, arguments: dict) -> None: ...
    async def finished(self, tool: Tool, ctx: ToolContext, arguments: dict, result: ToolResult) -> None: ...


def _failure(code: str, text: str, **meta: Any) -> ToolResult:
    return ToolResult(ok=False, error=text, meta={"code": code, **meta})


def _budget_failure(error: BudgetExhausted) -> ToolResult:
    return _failure(error.code, str(error), budget_dimension=error.dimension,
                    budget_limit=error.limit, budget_used=error.used)


def _interrupted(tool: Tool, ctx: ToolContext, reason: str) -> ToolResult:
    meta: dict[str, Any] = {"cancelled": reason == "cancelled", "execution_id": ctx.call_id}
    if not tool.read_only and tool.side_effect_scope != "none":
        meta["recovery_code"] = "TOOL_OUTCOME_UNKNOWN"
        meta["read_state_invalidated"] = list(ctx.state.get(STATE_KEY, {}))
        clear_read_state(ctx.state)
    return _failure("TOOL_TIMEOUT" if reason == "timeout" else "TOOL_CANCELLED",
                    f"Tool {reason}; its execution was interrupted."
                    + (" Inspect the affected state before retrying this operation."
                       if "recovery_code" in meta else ""), **meta)


class ToolExecutionService:
    def __init__(self, settings: Any, *, cleanup_timeout: float = 3.0, hooks: Any = None):
        self.settings = settings
        # PreToolUse / PostToolUse command hooks (app/agent/hooks.py), if any.
        self.hooks = hooks
        self.cleanup_timeout = max(0.01, cleanup_timeout)
        # Cancellation-resistant work is owned until it actually settles. A
        # timed-out coroutine may not be labelled finished while it still runs.
        self._unsettled: dict[asyncio.Task, str] = {}

    def timeout_for(self, tool: Tool, arguments: dict) -> float:
        declared = (tool.timeout_ms / 1000) if tool.timeout_ms else None
        default = getattr(tool, "default_timeout", None) or declared or getattr(self.settings, "tool_timeout", 120)
        maximum = getattr(self.settings, "tool_max_timeout", 900)
        # Declared fixed bounds (e.g. document OCR) are meaningful even when
        # larger than the configurable command timeout ceiling.
        if "timeout" not in tool.parameters.get("properties", {}):
            return max(.01, float(declared or default))
        requested = arguments.get("timeout", default)
        if type(requested) not in (int, float) or not math.isfinite(requested) or requested <= 0:
            requested = default
        return max(.01, min(float(requested), float(maximum)))

    async def dispatch(self, tool: Tool, ctx: ToolContext, arguments: dict, *, normalized: bool = False,
                       before_execute: Callable[[Tool, ToolContext, dict], Awaitable[None]] | None = None,
                       receipt: ExecutionReceipt | None = None, admission_id: str | None = None,
                       admission_check: Callable[[Tool, ToolContext, dict], ToolResult | None] | None = None) -> ToolResult:
        started_at = time.monotonic()
        try:
            if not isinstance(arguments, dict):
                raise ValueError("Arguments must be a JSON object")
            args = arguments if normalized else tool.resolve_input(arguments)
            if not isinstance(args, dict):
                raise ValueError("resolve_input must return a JSON object")
            snapshot_json(args)
            # Existing native schemas intentionally allow additional keys.
            # Respect that contract instead of silently tightening every tool.
            Draft202012Validator(tool.parameters).validate(args)
        except Exception as exc:
            detail = exc.message if isinstance(exc, (ValidationError, SchemaError)) else str(exc)
            return _failure("TOOL_ARGUMENTS_INVALID", f"Invalid arguments for {tool.name}: {detail}")

        hooks = self.hooks
        hook_meta: dict[str, Any] = {}
        if hooks is not None and hooks.has("PreToolUse"):
            pre = await hooks.run("PreToolUse", {"conversation_id": ctx.conversation_id, "tool_name": tool.name,
                                                 "tool_input": args, "cwd": str(ctx.workspace.root)},
                                  cwd=ctx.workspace.root, target=tool.name)
            if pre.blocked:
                return _failure("TOOL_HOOK_BLOCKED", f"Tool call blocked by PreToolUse hook: {pre.reason}")
            if pre.updated_input is not None:
                # A rewrite is held to the same contract as the model's input,
                # and recorded so the executed arguments stay inspectable.
                try:
                    snapshot_json(pre.updated_input)
                    Draft202012Validator(tool.parameters).validate(pre.updated_input)
                except Exception as exc:
                    detail = exc.message if isinstance(exc, (ValidationError, SchemaError)) else str(exc)
                    return _failure("TOOL_ARGUMENTS_INVALID", f"PreToolUse hook produced invalid arguments: {detail}")
                args = pre.updated_input
                hook_meta["hook_updated_input"] = args

        if tool.name in (ctx.state.get("mode_excluded") or ()):
            return _failure("TOOL_NOT_IN_MODE", f"`{tool.name}` is not available in Mat (chat and explain) mode, "
                            "which does not change files or run commands."
                            + (" Call switch_to_opt first if the request needs it." if ctx.state.get("mode_can_switch")
                               else " Suggest that the user switch to Opt."))
        approvals = ctx.state.get("approvals")
        if approvals is not None and not ctx.cancel.is_set():
            from app.agent.approvals import approval_trigger, decide
            needed = approval_trigger(tool, args)
            if needed is not None:
                waited = time.monotonic()
                outcome = await decide(approvals, ctx, tool, args, *needed)
                if outcome.by == "user":
                    # The critical path counts this as waiting on the user, not tool time.
                    hook_meta["approval_wait_ms"] = int((time.monotonic() - waited) * 1000)
                if outcome.decision != "allow":
                    why = outcome.reason or "The action was not approved."
                    if outcome.by == "guardian":
                        return _failure("TOOL_GUARDIAN_DENIED", why, approval=outcome.decision, approved_by="guardian")
                    return _failure("TOOL_APPROVAL_DENIED", f"Not approved ({outcome.by}): {why} Choose another "
                                    "approach or ask the user.", approval=outcome.decision, approved_by=outcome.by)
                ctx.state.setdefault("approved_calls", set()).add(ctx.call_id)
                hook_meta["approved_by"] = outcome.by

        if any(cid == ctx.conversation_id for cid in self._unsettled.values()):
            return _failure("TOOL_EXECUTION_UNSETTLED", "An interrupted tool is still stopping in this workspace.")
        if ctx.cancel.is_set():
            return _failure("TOOL_NOT_STARTED", "Skipped: the run was stopped.", cancelled=True)
        if any(not ctx.state.get(flag) for flag in tool.requires_flags):
            return _failure("TOOL_DISABLED", f"Tool '{tool.name}' is disabled for this turn.", capability_disabled=True)
        guard = ctx.state.setdefault("recovery_guard", RecoveryGuard())
        if guard is not None and (blocked := guard.check(tool, args)) is not None:
            return blocked
        if getattr(self.settings, "align_monitor", True):
            verdict = inspect_tool_call(tool.name, args)
            if verdict.level != "ok":
                await ctx.emit(ev("monitor", level=verdict.level, summary=verdict.summary, tool=tool.name))
            if verdict.level == "block":
                return _failure("TOOL_POLICY_BLOCKED", verdict.summary, monitor="block")

        budget = ctx.state.get("run_budget")
        local_cancel = asyncio.Event()
        call_ctx = dataclasses.replace(ctx, cancel=local_cancel)
        body: asyncio.Task | None = None
        stop_waiter: asyncio.Task | None = None
        result: ToolResult | None = None
        outer_cancelled = False
        try:
            # Optional native controller policy follows the stronger recovery
            # and capability gates, but cannot spend an execution allowance.
            if admission_check is not None:
                result = admission_check(tool, ctx, args)
            if result is None and budget is not None:
                admission = budget.admit_tool(admission_id if admission_id is not None else ctx.call_id,
                                              source=f"tool:{tool.name}")
                if not admission.is_new:
                    result = _failure("TOOL_ALREADY_ADMITTED", "This tool call was already admitted; it was not executed again.",
                                      dispatched=False, admission_id=admission.id)
            if result is None and ctx.cancel.is_set():
                result = _failure("TOOL_NOT_STARTED", "Skipped: the run was stopped.", cancelled=True)
            if result is None:
                if before_execute is not None:
                    await before_execute(tool, ctx, args)
                if receipt is not None:
                    await receipt.started(tool, ctx, args)
                timeout = self.timeout_for(tool, args)
                if budget is not None:
                    timeout = budget.remaining_timeout(timeout)
                # Receipt and preflight hooks may await. A stop during those
                # hooks must not start a handler afterward.
                if ctx.cancel.is_set():
                    result = _failure("TOOL_NOT_STARTED", "Skipped: the run was stopped.", cancelled=True)
            if result is None:
                await ctx.emit(ev("tool_running", id=ctx.call_id, name=tool.name))
                body = asyncio.create_task(tool(call_ctx, args), name=f"tool:{ctx.call_id}:{tool.name}")
                stop_waiter = asyncio.create_task(ctx.cancel.wait())
                done, _ = await asyncio.wait({body, stop_waiter}, timeout=timeout,
                                             return_when=asyncio.FIRST_COMPLETED)
                if body in done:
                    result = body.result()
                else:
                    reason = "cancelled" if stop_waiter in done else "timeout"
                    expired: BudgetExhausted | None = None
                    if reason == "timeout" and budget is not None:
                        try:
                            budget.remaining_timeout()
                        except BudgetExhausted as exc:
                            expired = exc
                    known = await self._stop_body(body, call_ctx)
                    result = known if known is not None and known.ok else _interrupted(tool, ctx, reason)
                    if known is not None and not known.ok:
                        result.output, result.artifacts = known.output, known.artifacts
                        result.meta = {**known.meta, **result.meta}
                    if expired is not None:
                        control = _budget_failure(expired)
                        control.output, control.artifacts = result.output, result.artifacts
                        control.meta = {**result.meta, **control.meta}
                        if known is not None and known.ok:
                            control.meta["execution_completed"] = True
                        result = control
        except BudgetExhausted as exc:
            result = _budget_failure(exc)
            result.meta["dispatched"] = body is not None
        except asyncio.CancelledError:
            outer_cancelled = True
            known = await self._stop_body(body, call_ctx) if body is not None else None
            result = known if known is not None and known.ok else _interrupted(tool, ctx, "cancelled")
        except Exception as exc:  # tool/body transport failure after the checkpoint
            logger.warning("Execution boundary failed for %s: %s", tool.name, type(exc).__name__)
            result = _failure("TOOL_EXECUTION_ERROR", f"{type(exc).__name__}: {exc}")
            if body is not None and not tool.read_only and tool.side_effect_scope != "none":
                result.meta.update(recovery_code="TOOL_OUTCOME_UNKNOWN", execution_id=ctx.call_id)
                clear_read_state(ctx.state)
        finally:
            if stop_waiter is not None:
                stop_waiter.cancel()
                await asyncio.gather(stop_waiter, return_exceptions=True)
            if result is not None and body is not None and not outer_cancelled and hooks is not None \
                    and hooks.has("PostToolUse"):
                try:
                    post = await hooks.run("PostToolUse", {
                        "conversation_id": ctx.conversation_id, "tool_name": tool.name, "tool_input": args,
                        "tool_response": {"ok": result.ok, "output": result.output, "error": result.error},
                        "cwd": str(ctx.workspace.root)}, cwd=ctx.workspace.root, target=tool.name)
                except asyncio.CancelledError:
                    outer_cancelled = True
                    post = None
                if post is not None and (post.blocked or post.additional_context):
                    feedback = post.reason if post.blocked else post.additional_context
                    result.output = f"{result.output}\n\n[PostToolUse hook] {feedback}".strip()
                    result.meta["hook_feedback"] = feedback
            if result is not None:
                result.meta.update(hook_meta)
                if getattr(self.settings, "redact_tool_output", True):
                    result.output = redact_secrets(result.output)
                    if result.error:
                        result.error = redact_secrets(result.error)
                result.meta.setdefault("duration_ms", int((time.monotonic() - started_at) * 1000))
                if result.meta.get("recovery_code") == "TOOL_OUTCOME_UNKNOWN":
                    result.meta.setdefault("execution_id", ctx.call_id)
                    invalidated = set(result.meta.get("read_state_invalidated", []))
                    invalidated.update(ctx.state.get(STATE_KEY, {}))
                    result.meta["read_state_invalidated"] = sorted(invalidated)
                    clear_read_state(ctx.state)
                if guard is not None:
                    guard.observe(tool, args, result)
                if receipt is not None:
                    # SessionLog.flush is cancellation-safe; finish the receipt
                    # before propagating a client disconnect or process stop.
                    finish = asyncio.create_task(receipt.finished(tool, ctx, args, result))
                    while not finish.done():
                        try:
                            await asyncio.shield(finish)
                        except asyncio.CancelledError:
                            outer_cancelled = True
                    finish.result()
        if outer_cancelled:
            raise asyncio.CancelledError
        assert result is not None
        return result

    async def _stop_body(self, task: asyncio.Task, ctx: ToolContext) -> ToolResult | None:
        ctx.cancel.set()
        done, _ = await asyncio.wait({task}, timeout=self.cleanup_timeout)
        if task not in done:
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=self.cleanup_timeout)
        if task not in done:
            self._unsettled[task] = ctx.conversation_id
            task.add_done_callback(self._settled)
            return None
        try:
            return task.result()
        except (asyncio.CancelledError, Exception):
            return None

    def _settled(self, task: asyncio.Task) -> None:
        self._unsettled.pop(task, None)
        if not task.cancelled():
            task.exception()  # retrieve failures even after the original waiter left

    async def shutdown(self) -> None:
        tasks = list(self._unsettled)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=self.cleanup_timeout)


class SessionExecutionReceipt:
    """MCP-only execution facts in the same private append-only session log.

These are not fabricated assistant messages and cannot enter a provider's
conversation surface. Native execution keeps its existing tool/call/result
events; both fact formats feed the same recovery and file observation guard.
"""
    def __init__(self, session: SessionLog, *, origin: str = "mcp"):
        self.session = session
        self.origin = origin

    async def started(self, tool: Tool, ctx: ToolContext, arguments: dict) -> None:
        self.session.append("execution/call", {"callId": ctx.call_id, "name": tool.name,
            "resolved_name": tool.name, "normalized_arguments": arguments, "origin": self.origin,
            "read_only": tool.read_only, "side_effect_scope": tool.side_effect_scope}, ignorable=True)
        await self.session.flush()

    async def finished(self, tool: Tool, ctx: ToolContext, arguments: dict, result: ToolResult) -> None:
        self.session.append("execution/result", {"callId": ctx.call_id, "name": tool.name,
            "origin": self.origin, **result.to_dict()}, ignorable=True)
        await self.session.flush()


def execution_recovery_events(events: list[dict]) -> list[dict]:
    """Translate receipts for the existing guard, marking abandoned starts unknown.

Only call this with no concurrent owner executing that session (the native or
MCP conversation lease). An open external call is then an interrupted checkpoint.
"""
    pending: dict[str, dict] = {}
    for event in events:
        data = event.get("data", {})
        if event.get("type") == "execution/call":
            pending[data.get("callId")] = data
        elif event.get("type") == "execution/result":
            pending.pop(data.get("callId"), None)
    facts: list[dict] = []
    for event in events:
        kind, data = event.get("type"), event.get("data", {})
        if kind == "execution/call":
            cid = data.get("callId")
            facts.append({**event, "type": "tool/call", "data": {**data, "turn": "mcp", "step": 0}})
            if cid in pending:
                # MCP owns the conversation lease for an entire invocation.
                # A later event therefore comes after this abandoned call.
                # Insert the inferred interruption here, never after later
                # inspection receipts which may resolve the uncertainty.
                facts.append({"type": "tool/result", "data": {"turn": "mcp", "step": 0, "callId": cid,
                    "name": data.get("name"), "ok": False,
                    "meta": {"recovery_code": "TOOL_OUTCOME_UNKNOWN"}}})
        elif kind == "execution/result":
            facts.append({**event, "type": "tool/result", "data": {**data, "turn": "mcp", "step": 0}})
        else:
            facts.append(event)
    return facts


def restore_execution_state(ctx: ToolContext, events: list[dict], user_text: str = "") -> None:
    """Restore native surface observations plus external receipts in event order."""
    surface = set(fold_surface(events))
    completed = {event["data"].get("callId") for event in events if event["type"] == "execution/result"}
    results = []
    for event in events:
        data = event["data"]
        if (event["type"] == "execution/call" and data.get("callId") not in completed
                and not data.get("read_only") and data.get("side_effect_scope") != "none"):
            # Unknown work can invalidate earlier observations; a later fresh
            # read still establishes a usable receipt after that interruption.
            results.clear()
        elif event["type"] == "execution/result" or (event["seq"] in surface and event["type"] == "tool/result"):
            results.append(data)
    restore_read_state(ctx.state, ctx.workspace, results)
    facts = execution_recovery_events(events)
    ctx.state["recovery_guard"] = RecoveryGuard.from_events(facts, user_text)
