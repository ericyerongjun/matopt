"""
Tool abstraction.

A tool exposes a JSON-schema description to the LLM and an async ``run``
method that receives a :class:`ToolContext` plus the parsed arguments and
returns a :class:`ToolResult`.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from app.agent.events import Event, ToolResult
from app.agent.workspace import Workspace

logger = logging.getLogger(__name__)

EmitFn = Callable[[Event], Awaitable[None]]


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


class Tool(ABC):
    name: str = ""
    description: str = ""
    parameters: dict = {"type": "object", "properties": {}}
    read_only: bool = False      # read-only tools may run concurrently
    background: bool = False     # may start without blocking sibling tools
    ui_kind: str = "generic"     # hint for the frontend renderer

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
        except asyncio.CancelledError:
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
