"""Approvals for risky tool calls, ported from Codex's approval flow.

Codex decides per call whether to run, ask, or refuse
(``core/src/tools/orchestrator.rs``, ``ExecApprovalRequirement``), and the ask
goes either to the person or to an automatic reviewer, the guardian
(``core/src/guardian/``). MatOpt keeps that split behind one setting,
``approvals_reviewer``:

``off``       the historical behaviour. Nothing asks; command rules that say
              ``prompt`` refuse.
``user``      the run emits ``approval_request`` and waits for
              ``POST /api/runs/{run}/approvals/{id}`` (or the timeout, which
              denies).
``guardian``  a reviewer model decides (``app/agent/guardian.py``), with a
              circuit breaker that stops the turn after repeated denials.

What needs an approval (``approval_trigger``) follows Codex's on-request
policy with a sandbox: ordinary ``python`` and ``bash`` work does not ask —
the sandbox and the command rules bound it — but a command rule that says
``prompt``, a tool that declares itself destructive, and a write-capable tool
on an external MCP server do.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from app.agent.events import ev

logger = logging.getLogger(__name__)


@dataclass
class ApprovalOutcome:
    decision: str            # allow | deny | timeout
    by: str                  # user | guardian | policy
    reason: str = ""


@dataclass
class _Pending:
    conversation_id: str
    future: asyncio.Future


class ApprovalBroker:
    """Pending user approvals, resolved through the REST endpoint."""

    def __init__(self) -> None:
        self._pending: dict[str, _Pending] = {}

    def pending_for(self, conversation_id: str) -> list[str]:
        return [aid for aid, p in self._pending.items() if p.conversation_id == conversation_id]

    def resolve(self, conversation_id: str, approval_id: str, decision: str) -> bool:
        pending = self._pending.get(approval_id)
        if pending is None or pending.conversation_id != conversation_id or decision not in ("allow", "deny"):
            return False
        if not pending.future.done():
            pending.future.set_result(decision)
        return True

    async def ask(self, emit: Callable, conversation_id: str, cancel: asyncio.Event, *, tool: str, title: str,
                  summary: str, reason: str, risk: str, timeout: float) -> ApprovalOutcome:
        approval_id = "appr_" + uuid.uuid4().hex[:16]
        future = asyncio.get_running_loop().create_future()
        self._pending[approval_id] = _Pending(conversation_id, future)
        await emit(ev("approval_request", id=approval_id, tool=tool, title=title, summary=summary,
                      reason=reason, risk=risk))
        stopper = asyncio.ensure_future(cancel.wait())
        try:
            done, _ = await asyncio.wait({future, stopper}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            decision = future.result() if future in done else "timeout"
        finally:
            stopper.cancel()
            self._pending.pop(approval_id, None)
        await emit(ev("approval_resolved", id=approval_id, decision=decision, by="user"))
        return ApprovalOutcome(decision, "user", "The user declined this action." if decision == "deny"
                               else "No answer arrived in time." if decision == "timeout" else "")


@dataclass
class ApprovalContext:
    """Per-run approval wiring the harness places in ``ctx.state["approvals"]``."""

    reviewer: str
    broker: ApprovalBroker
    emit: Callable
    timeout: float = 600.0
    # async (request: dict) -> GuardianVerdict | None, bound to the run's provider.
    guardian: Optional[Callable[[dict], Awaitable[Any]]] = None
    breaker: Any = None
    fail_closed: bool = True
    denials: list[str] = field(default_factory=list)


def approval_trigger(tool: Any, args: dict) -> Optional[tuple[str, str]]:
    """(trigger, reason) when this call needs an approval, else None."""
    if tool.name == "bash" and hasattr(tool, "exec_policy"):
        verdict = tool.exec_policy.evaluate(str(args.get("command") or ""))
        if verdict.decision == "prompt":
            why = verdict.rule.justification if verdict.rule else "this command needs approval"
            return "exec_rule_prompt", why
    if getattr(tool, "destructive", False):
        return "destructive", "This tool declares itself destructive."
    if getattr(tool, "mcp_server", "") and not getattr(tool, "read_only", False):
        return "external", f"This acts on the external server '{tool.mcp_server}'."
    return None


def summarize_call(tool: Any, args: dict) -> str:
    if tool.name == "bash":
        return str(args.get("command") or "")[:1000]
    parts = [f"{k}={str(v)[:120]}" for k, v in args.items()]
    return (tool.name + "(" + ", ".join(parts) + ")")[:1000]


async def decide(approvals: ApprovalContext, ctx: Any, tool: Any, args: dict, trigger: str,
                 reason: str) -> ApprovalOutcome:
    summary = summarize_call(tool, args)
    risk = getattr(tool, "risk_level", "medium")
    if approvals.reviewer == "user":
        return await approvals.broker.ask(approvals.emit, ctx.conversation_id, ctx.cancel, tool=tool.name,
                                          title=f"Allow {tool.name}?", summary=summary, reason=reason, risk=risk,
                                          timeout=approvals.timeout)
    if approvals.reviewer == "guardian" and approvals.guardian is not None:
        request = {"tool": tool.name, "arguments": args, "risk_level": risk,
                   "side_effect_scope": getattr(tool, "side_effect_scope", "system"), "trigger": trigger,
                   "summary": summary, "call_id": ctx.call_id}
        verdict = None
        try:
            verdict = await approvals.guardian(request)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("Guardian review failed", exc_info=True)
        if verdict is None:
            # Fail closed by default (Codex); otherwise fall back to the
            # reviewer-off behaviour, where only prompt rules refuse.
            outcome = "deny" if approvals.fail_closed or trigger == "exec_rule_prompt" else "allow"
            await approvals.emit(ev("guardian_review", call_id=ctx.call_id, tool=tool.name, outcome="error",
                                    risk_level=risk, user_authorization="unknown",
                                    rationale="The reviewer was unavailable."))
            return ApprovalOutcome(outcome, "guardian", "The reviewer was unavailable.")
        await approvals.emit(ev("guardian_review", call_id=ctx.call_id, tool=tool.name, outcome=verdict.outcome,
                                risk_level=verdict.risk_level, user_authorization=verdict.user_authorization,
                                rationale=verdict.rationale))
        from app.agent.guardian import CIRCUIT_BREAKER_MESSAGE, render_rejection
        if approvals.breaker is not None and approvals.breaker.record_verdict(verdict):
            # Repeated denials stop the turn (Codex's circuit breaker).
            ctx.cancel.set()
            return ApprovalOutcome("deny", "guardian", CIRCUIT_BREAKER_MESSAGE)
        return ApprovalOutcome(verdict.outcome, "guardian",
                               "" if verdict.allowed else render_rejection(verdict))
    # Reviewer off: keep the old behaviour — prompt rules refuse, the rest run.
    if trigger == "exec_rule_prompt":
        return ApprovalOutcome("deny", "policy", reason)
    return ApprovalOutcome("allow", "policy")
