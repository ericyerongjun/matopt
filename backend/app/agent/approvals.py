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
``prompt``, a tool that declares itself destructive, a write-capable tool on an
external MCP server, and any code execution while no sandbox is active do.

Command rules also apply to commands a ``python`` cell or a nested ``bash -c``
spells out. That reads literal text only: a command assembled at run time is
out of its view, and the sandbox remains the boundary.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import os
import re
import shlex
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from app.agent.events import ev
from app.agent.exec_rules import _RANK, _SHELLS, Evaluation, ExecPolicy, split_segments

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


EXEC_TOOLS = frozenset({"python", "bash", "lean_check", "data_agent"})
_MAX_NESTING = 3
_SHELL_ESCAPE = re.compile(r"^\s*(?:[\w.]+\s*=\s*)?!(?!=)(.+)$|^\s*%(?:system|sx)\s+(.+)$", re.MULTILINE)
_SHELL_CELL = re.compile(r"\s*%%(?:bash|sh|script\s+\S*sh)\b[^\n]*\n")
_COMMAND_CALLS = frozenset({
    "subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output", "subprocess.Popen",
    "subprocess.getoutput", "subprocess.getstatusoutput", "os.system", "os.popen",
    "asyncio.create_subprocess_shell",
})
_VARARG_CALLS = frozenset({"asyncio.create_subprocess_exec"})
_ARGV_SECOND = frozenset({"os.execv", "os.execvp"})


def _literal(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else "x" for part in node.values)
    return None


def _command_text(node: ast.AST) -> Optional[str]:
    if isinstance(node, (ast.List, ast.Tuple)):
        return " ".join(shlex.quote(_literal(item) or "x") for item in node.elts)
    return _literal(node)


def _python_commands(code: str) -> list[str]:
    """Shell commands a cell spells out in IPython syntax or a subprocess call."""
    cell = _SHELL_CELL.match(code)
    if cell:
        return [code[cell.end():]]
    commands = [first or second for first, second in _SHELL_ESCAPE.findall(code)]
    source = _SHELL_ESCAPE.sub(lambda m: re.match(r"\s*", m.group(0)).group(0) + "pass", code)
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return commands
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update({a.asname: a.name for a in node.names if a.asname})
        elif isinstance(node, ast.ImportFrom) and node.module:
            aliases.update({a.asname or a.name: f"{node.module}.{a.name}" for a in node.names})

    def dotted(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        return f"{dotted(node.value)}.{node.attr}" if isinstance(node, ast.Attribute) else ""

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = dotted(node.func)
        if name in _VARARG_CALLS:
            commands.append(" ".join(shlex.quote(_literal(a) or "x") for a in node.args))
        elif name in _COMMAND_CALLS or (name in _ARGV_SECOND and len(node.args) > 1):
            text = _command_text(node.args[1 if name in _ARGV_SECOND else 0])
            if text:
                commands.append(text)
    return commands


def _nested_commands(command: str) -> list[str]:
    """Commands carried inside ``bash -c``, ``eval`` and ``python -c`` arguments."""
    nested: list[str] = []
    for argv in split_segments(command) or []:
        name = os.path.basename(argv[0])
        if name == "eval":
            nested.append(" ".join(argv[1:]))
            continue
        is_python = name.startswith("python")
        if name not in _SHELLS and not is_python:
            continue
        for flag, value in zip(argv[1:], argv[2:]):
            if flag == "-c" or (not is_python and re.fullmatch(r"-[a-zA-Z]*c", flag)):
                nested.extend(_python_commands(value) if is_python else [value])
                break
    return nested


def _worst_verdict(policy: ExecPolicy, commands: list[str], depth: int = 0) -> Optional[Evaluation]:
    worst: Optional[Evaluation] = None
    for command in commands:
        verdicts = [policy.evaluate(command)]
        if depth < _MAX_NESTING and (nested := _worst_verdict(policy, _nested_commands(command), depth + 1)):
            verdicts.append(nested)
        for verdict in verdicts:
            if verdict.decision != "allow" and (worst is None or _RANK[verdict.decision] > _RANK[worst.decision]):
                worst = verdict
    return worst


def _commands_of(tool: Any, args: dict) -> list[str]:
    if tool.name == "bash":
        return [str(args.get("command") or "")]
    if tool.name == "bash_poll":
        return [str(args["input"])] if args.get("input") else []
    if tool.name == "python":
        return _python_commands(str(args.get("code") or ""))
    return []


def _exec_rule_trigger(tool: Any, args: dict) -> Optional[tuple[str, str]]:
    commands = _commands_of(tool, args)
    if not commands:
        return None
    from app.config import settings
    policy = getattr(tool, "exec_policy", None) or ExecPolicy.from_settings(settings)
    verdict = _worst_verdict(policy, commands)
    if verdict is None:
        return None
    why = verdict.rule.justification if verdict.rule and verdict.rule.justification else "this command needs approval"
    if verdict.decision == "prompt":
        return "exec_rule_prompt", why
    if tool.name == "bash" and policy.evaluate(commands[0]).decision == "forbidden":
        return None  # ShellTool refuses it itself, with the command in the message.
    return "exec_rule_forbidden", why


def _sandbox_contains_children() -> bool:
    from app.agent.sandbox import select_backend
    from app.config import settings
    mode = settings.sandbox_mode
    return mode == "external" or (mode in ("workspace-write", "read-only")
                                  and select_backend(settings.sandbox_backend) != "none")


def _executes_code(tool: Any, args: dict) -> bool:
    return tool.name in EXEC_TOOLS or (tool.name == "bash_poll" and bool(args.get("input")))


def approval_trigger(tool: Any, args: dict) -> Optional[tuple[str, str]]:
    """(trigger, reason) when this call needs an approval, else None."""
    rule = _exec_rule_trigger(tool, args)
    if rule is not None:
        return rule
    if _executes_code(tool, args) and not _sandbox_contains_children():
        return "unsandboxed", "No sandbox is active, so this code runs with the backend's full access."
    if getattr(tool, "destructive", False):
        return "destructive", "This tool declares itself destructive."
    if getattr(tool, "mcp_server", "") and not getattr(tool, "read_only", False):
        return "external", f"This acts on the external server '{tool.mcp_server}'."
    return None


def summarize_call(tool: Any, args: dict) -> str:
    if tool.name == "bash":
        return str(args.get("command") or "")[:1000]
    if tool.name == "python":
        return str(args.get("code") or "")[:1000]
    parts = [f"{k}={str(v)[:120]}" for k, v in args.items()]
    return (tool.name + "(" + ", ".join(parts) + ")")[:1000]


async def decide(approvals: ApprovalContext, ctx: Any, tool: Any, args: dict, trigger: str,
                 reason: str) -> ApprovalOutcome:
    if trigger == "exec_rule_forbidden":
        return ApprovalOutcome("deny", "policy", reason)
    if approvals.reviewer == "deny":
        return ApprovalOutcome("deny", "policy", f"{reason} Nobody can approve it on this connection.")
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
