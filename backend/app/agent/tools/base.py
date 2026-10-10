"""
Tool abstraction.

A tool exposes a JSON-schema description to the LLM and an async ``run``
method that receives a :class:`ToolContext` plus the parsed arguments and
returns a :class:`ToolResult`.

Every tool also declares a *safety and capability surface* (plan §3.8a, ported
from ZCode's ``ToolMetadata``, ``core/src/tool/types.ts:68``). The harness reads
it today and the tool scheduler and approval broker read it in later phases, so
each value must describe what the tool *actually* does:

``read_only``            nothing outside the conversation changes.
``destructive``          intrinsically destructive *regardless of arguments*.
                         ZCode reserves this for operations like deleting a
                         saved task: a shell or exec tool is not destructive by
                         class — its arguments are, which is the monitor's and
                         the approval policy's job.
``concurrent_safe``      may run in parallel with other calls (phase 7b).
``side_effect_scope``    where effects land: ``none`` (nothing durable),
                         ``workspace`` (this conversation's files), ``system``
                         (anything the process can reach).
``risk_level``           low/medium/high, for surfacing and future policy.
``needs_approval``       the call should be confirmed before it runs. Nothing
                         reads this yet — it is a declaration for the approval
                         broker, and the current loop never prompts.
``provider_visible``     may be advertised to the model at all.
``stop_turn_on_success`` a successful result concludes the turn; the loop reads
                         this declaration instead of pattern-matching a name.
``requires_flags``       per-turn runtime switches that must be on before the
                         tool is advertised and callable (Web Search for the web
                         tools). Enabling a tool by name turns them on, so the
                         harness never needs to know which tool needs what.
``timeout_ms``           the bound the tool *enforces itself*, if any; ``None``
                         means it enforces none (a scheduler may add one).
``max_output_bytes``     the output ceiling the tool *enforces itself*, if any.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Optional

from app.agent.events import Event, ToolResult
from app.agent.budget import BudgetExhausted
from app.agent.workspace import Workspace

logger = logging.getLogger(__name__)

EmitFn = Callable[[Event], Awaitable[None]]

SideEffectScope = Literal["none", "workspace", "system"]
RiskLevel = Literal["low", "medium", "high"]


def output_bound_bytes(chars: int) -> int:
    """Byte ceiling of a character-budgeted truncation.

    ``truncate_text`` bounds a tool's output by *characters*; this converts the
    same budget to the bytes ``Tool.max_output_bytes`` declares. Four bytes is
    the longest UTF-8 encoding of one character, so the result is a true upper
    bound (conservative for the ASCII-heavy text these tools mostly produce).
    """
    return 4 * int(chars)


@dataclass
class ToolContext:
    conversation_id: str
    workspace: Workspace
    emit: EmitFn                         # push a progress event to the client
    cancel: asyncio.Event                # set when the user hits stop
    call_id: str = ""
    state: dict = field(default_factory=dict)   # mutable per-conversation state (plan, ...)
    kernels: Any = None                  # KernelPool (set by the harness)
    settings: Any = None

    async def progress(self, delta: str) -> None:
        if delta:
            await self.emit(Event("tool_progress", {"id": self.call_id, "delta": delta}))

    async def activity_progress(self, completed: int, total: int | None = None, *, unit: str = "pages") -> None:
        """Measured public progress. Raw stdout uses the separate private channel."""
        await self.emit(Event("tool_activity_progress", {"id": self.call_id, "progress": {
            "completed": completed, "unit": unit, **({"total": total} if total is not None else {})}}))


class Tool(ABC):
    name: str = ""
    description: str = ""
    parameters: dict = {"type": "object", "properties": {}}
    read_only: bool = False      # read-only tools may run concurrently
    background: bool = False     # may start without blocking sibling tools
    ui_kind: str = "generic"     # hint for the frontend renderer

    # ── safety / capability surface (plan §3.8a) ────────────────────────
    destructive: bool = False
    concurrent_safe: bool = False
    side_effect_scope: SideEffectScope = "workspace"
    risk_level: RiskLevel = "low"
    needs_approval: bool = False
    provider_visible: bool = True
    stop_turn_on_success: bool = False
    requires_flags: tuple[str, ...] = ()
    timeout_ms: Optional[int] = None
    max_output_bytes: Optional[int] = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Where a tool does not declare a scope, follow `read_only`: a read-only
        # tool touches nothing outside the conversation, anything else is
        # assumed to write its workspace. A tool that writes further (the exec
        # tools) declares `system` explicitly.
        if "side_effect_scope" not in cls.__dict__:
            cls.side_effect_scope = "none" if getattr(cls, "read_only", False) else "workspace"

    def is_provider_visible(self, ctx: "ToolContext") -> bool:
        """Whether this tool may be advertised to the model *this turn*.

        The static half is ``provider_visible``; ``requires_flags`` is the
        per-turn half (a web tool needs Web Search switched on). The harness
        asks this one question instead of name-matching tools in
        ``current_schemas`` — see plan §3.8a.
        """
        return self.provider_visible and all(bool(ctx.state.get(f)) for f in self.requires_flags)

    def resolve_input(self, arguments: dict) -> dict:
        """Normalize the model's arguments into the execution fact (plan §3.8c).

        The harness calls this **exactly once** per call, after JSON parsing and
        before anything else reads the arguments; the returned dict is what the
        monitor, the ``tool_call_ready`` event, the persisted ``tool_call`` part
        and the handler all see. Normalizing here rather than in ``run`` is the
        anti-TOCTOU property ZCode buys with ``resolveInput``
        (``core/src/tool/types.ts:313-326``): there is no "approve A, run B",
        and the logged arguments are the executed ones.

        Default: the parsed arguments, unchanged (and the same object, so the
        common path cannot drift). Raising here is a business failure — the call
        is recorded as ``{"_raw": ...}`` and the tool never runs.
        """
        return arguments

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    @abstractmethod
    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult: ...

    async def __call__(self, ctx: ToolContext, arguments: dict) -> ToolResult:
        try:
            sig = inspect.signature(self.run)
            accepted = {k: v for k, v in arguments.items() if k in sig.parameters}
            if "kwargs" in sig.parameters:
                accepted = dict(arguments)
            missing = [
                p.name for p in sig.parameters.values()
                if p.default is inspect.Parameter.empty and p.name not in ("ctx", "kwargs")
                and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
                and p.name not in accepted
            ]
            if missing:
                return ToolResult(ok=False, output="", error=f"Missing required argument(s): {', '.join(missing)}")
            return await self.run(ctx, **accepted)
        except (asyncio.CancelledError, BudgetExhausted):
            raise
        except Exception as exc:  # noqa: BLE001 — tools must never crash the harness
            logger.exception("Tool %s crashed", self.name)
            return ToolResult(ok=False, output="", error=f"{type(exc).__name__}: {exc}")


class ToolRegistry:
    def __init__(self, tools: Optional[list[Tool]] = None):
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.register(t)

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("Tool must have a name")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    def __iter__(self):
        return iter(self._tools.values())

    def __len__(self):
        return len(self._tools)


def truncate_text(text: str, limit: int, label: str = "output") -> str:
    """Keep the head and tail of a long string so the model sees both ends."""
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    omitted = len(text) - head - tail
    return f"{text[:head]}\n\n... [{omitted} characters of {label} truncated] ...\n\n{text[-tail:]}"
