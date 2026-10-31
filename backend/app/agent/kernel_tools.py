"""Code mode: Python cells call MatOpt tools through an injected ``tools`` object.

Codex's code mode (``codex-rs/core/src/tools/code_mode/``, protocol in
``codex-rs/code-mode-protocol``) runs model-written JavaScript in a runtime
whose host functions submit *nested* tool calls back to the session; every
nested call goes through the normal tool router, so hooks, approval policy and
output truncation apply exactly as for a top-level call
(``code_mode/mod.rs`` ``submit_nested_tool``). MatOpt already has a code
runtime, the conversation's Jupyter kernel, so the port adapts the host side:

- **Transport.** A per-kernel Unix socket at ``<kernel ipc dir>/tools.sock``
  (the directory is ``mkdtemp``'d, so 0700; the socket is chmod 0600). Not the
  Jupyter comm channel: comm messages are handled on the shell channel, which
  is busy *running the cell* that is waiting for the answer, so a blocking
  ``tools.x()`` inside a cell would deadlock. A random 32-byte token reaches
  the kernel only through its environment (``MATOPT_TOOLS_SOCK`` /
  ``MATOPT_TOOLS_TOKEN``), which the startup code pops immediately so shell
  commands launched from the kernel do not inherit it.
- **Wire format.** One request per connection: a 4-byte big-endian length
  and a UTF-8 JSON object ``{"token", "op": "call"|"list"|"describe",
  "name", "args"}``; the reply is framed the same way, ``{"ok", "output",
  "error", "code"}``. Codex's ``CodeModeNestedToolCall`` carries the same
  three facts (tool name, JSON input, originating cell).
- **Dispatch gate.** Requests are served only while a python cell executes:
  ``KernelSession.execute`` activates a :class:`KernelToolBinding` (the
  python call's ``ToolContext``, its call id, the cell deadline and the
  harness's dispatcher) for exactly the life of the cell, mirroring Codex's
  ``CodeModeDispatchBroker`` (``delegate.rs``: ``mark_cell_ready_for_dispatch``
  opens the gate, ``close_cell`` closes it and cancels in-flight work). A
  request outside a cell is refused.
- **Bounds.** One nested call at a time per cell, at most
  ``code_mode_max_calls_per_cell`` per cell, each bounded by the *remaining*
  cell deadline (the tool's own timeout is applied inside the dispatcher), and
  cancelled when the cell ends, is interrupted, or the run is stopped.
- **Allow-list.** Only read-only / compute tools are callable from a cell
  (:data:`KERNEL_CALLABLE_TOOLS`, plus MCP proxy tools that declare
  ``readOnlyHint``). Exec tools (``python`` itself, ``bash``), sub-agents,
  plan/progress/result and UI tools never are: a cell must not escape the
  approval and scheduling the top-level loop applies to them. Codex reaches
  the same end by exposing only the tools the code-mode tool definitions list
  (``tool_definitions.rs``).

The dispatcher itself is the harness's: ``ctx.state["tool_dispatch"]`` is an
async callable ``(name, args, parent_call_id, *, call_id) -> ToolResult`` that
resolves the tool and runs it through ``ToolExecutionService.dispatch`` (the
single execution boundary: hooks, monitor, policy, run budget, redaction).
Without it code mode is unavailable and every ``tools.*`` call raises a clear
``ToolError`` in the cell.
"""

from __future__ import annotations

import asyncio
import hmac
import inspect
import json
import logging
import os
import secrets
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from app.agent.events import ToolResult

logger = logging.getLogger(__name__)

SOCKET_NAME = "tools.sock"
ENV_SOCK = "MATOPT_TOOLS_SOCK"
ENV_TOKEN = "MATOPT_TOOLS_TOKEN"
MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_REPLY_BYTES = 16 * 1024 * 1024
_HEADER = struct.Struct(">I")

# Read-only or pure-compute tools a cell may call. Everything else is denied,
# including tools added later, until someone decides they belong here.
KERNEL_CALLABLE_TOOLS = frozenset({
    "web_search", "web_fetch", "wolfram_alpha",
    "read_file", "list_files", "find_files", "grep",
    "check_equivalence", "lean_check",
    "search_skills",
    "list_mcp_resources", "read_mcp_resource",
})
# Never callable, whatever their metadata says (defence in depth against a
# future tool that reuses one of these names with read_only=True).
NEVER_KERNEL_CALLABLE = frozenset({
    "python", "bash", "bash_poll", "data_agent",
    "update_plan", "report_progress", "publish_result",
    "render_ui", "create_ui_app", "register_ui_action",
})
_NEVER_PREFIXES = ("spawn_", "agent", "wait_agent", "send_message", "close_agent", "interrupt_agent")

DispatchFn = Callable[..., Awaitable[ToolResult]]


def kernel_callable(tool: Any) -> bool:
    """Whether ``tool`` (a ``Tool`` or a bare name) may be called from a cell."""
    name = tool if isinstance(tool, str) else getattr(tool, "name", "")
    if not isinstance(name, str) or not name:
        return False
    if name in NEVER_KERNEL_CALLABLE or name.startswith(_NEVER_PREFIXES):
        return False
    if name in KERNEL_CALLABLE_TOOLS:
        return True
    if isinstance(tool, str):
        return False
    # An MCP proxy tool: its only safety signal is the server's readOnlyHint.
    return bool(getattr(tool, "mcp_server", "")) and getattr(tool, "read_only", False) is True


def _reply(ok: bool, output: Any = "", error: Optional[str] = None, code: Optional[str] = None) -> dict:
    return {"ok": ok, "output": output, "error": error, "code": code}


def _refuse(code: str, error: str) -> dict:
    return _reply(False, "", error, code)


@dataclass
class KernelToolBinding:
    """The python call a cell's nested tool calls belong to.

    Built by ``PythonTool`` and activated by ``KernelSession.execute`` for the
    life of one cell (Codex: the per-cell dispatch gate in ``delegate.rs``).
    """

    ctx: Any                              # ToolContext of the python call
    call_id: str
    dispatch: Optional[DispatchFn] = None
    registry: Any = None                  # ToolRegistry-like: .get(name), iterable
    max_calls: int = 50
    deadline: float = field(default_factory=lambda: time.monotonic() + 120.0)
    calls: int = 0
    closed: bool = False
    _inflight: Optional[asyncio.Task] = None

    @property
    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def close(self) -> None:
        """Close the gate and cancel the in-flight nested call, if any."""
        self.closed = True
        task = self._inflight
        if task is not None and not task.done():
            task.cancel()

    # ── catalog ─────────────────────────────────────────────────────────

    def _lookup(self, name: str) -> Any:
        if self.registry is None:
            return None
        getter = getattr(self.registry, "get", None)
        return getter(name) if callable(getter) else None

    def _visible(self, tool: Any) -> bool:
        check = getattr(tool, "is_provider_visible", None)
        try:
            return bool(check(self.ctx)) if callable(check) else True
        except Exception:  # noqa: BLE001
            return False

    def catalog(self) -> list[dict]:
        if self.registry is None:
            return [{"name": n, "description": ""} for n in sorted(KERNEL_CALLABLE_TOOLS)]
        out = []
        for tool in self.registry:
            if kernel_callable(tool) and self._visible(tool):
                out.append({"name": tool.name, "description": (tool.description or "").strip()})
        return sorted(out, key=lambda t: t["name"])

    def describe(self, name: str) -> dict:
        if not isinstance(name, str) or not kernel_callable(self._lookup(name) or name):
            return _refuse("TOOL_NOT_KERNEL_CALLABLE", f"Tool {name!r} cannot be called from Python.")
        tool = self._lookup(name)
        if tool is None:
            if self.registry is not None:
                return _refuse("TOOL_UNKNOWN", f"Unknown tool {name!r}.")
            return _reply(True, {"name": name, "description": "", "parameters": {}})
        return _reply(True, {"name": tool.name, "description": tool.description or "",
                             "parameters": tool.parameters})

    # ── nested call ─────────────────────────────────────────────────────

    async def call(self, name: Any, args: Any) -> dict:
        if self.closed:
            return _refuse("CODE_MODE_CELL_CLOSED", "The cell that owned this tool call has finished.")
        if self.dispatch is None:
            return _refuse("CODE_MODE_UNAVAILABLE",
                           "Code mode is unavailable in this run: no tool dispatcher is attached.")
        if not isinstance(name, str) or not name:
            return _refuse("TOOL_ARGUMENTS_INVALID", "Tool name must be a non-empty string.")
        if not isinstance(args, dict):
            return _refuse("TOOL_ARGUMENTS_INVALID", "Tool arguments must be keyword arguments (a JSON object).")
        if self._inflight is not None:
            return _refuse("CODE_MODE_BUSY", "Only one tool call may run at a time from a cell.")
        tool = self._lookup(name)
        if self.registry is not None and tool is None:
            return _refuse("TOOL_UNKNOWN", f"Unknown tool {name!r}. Use tools.list() to see what is callable.")
        if not kernel_callable(tool if tool is not None else name):
            return _refuse("TOOL_NOT_KERNEL_CALLABLE",
                           f"Tool {name!r} cannot be called from Python; call it directly instead.")
        if self.calls >= self.max_calls:
            return _refuse("CODE_MODE_CALL_LIMIT",
                           f"This cell reached the limit of {self.max_calls} tool calls.")
        remaining = self.remaining
        if remaining <= 0.05:
            return _refuse("TOOL_TIMEOUT", "The cell's time limit leaves no time for another tool call.")
        self.calls += 1
        nested_id = f"kernel:{self.call_id}:{self.calls}"
        task = asyncio.create_task(self._invoke(name, args, nested_id), name=f"tool:{nested_id}")
        self._inflight = task
        stop = asyncio.create_task(self.ctx.cancel.wait()) if getattr(self.ctx, "cancel", None) else None
        try:
            done, _ = await asyncio.wait({task, *([stop] if stop else [])}, timeout=remaining,
                                         return_when=asyncio.FIRST_COMPLETED)
            if task not in done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                if stop is not None and stop in done:
                    return _refuse("TOOL_CANCELLED", "The run was stopped during the tool call.")
                return _refuse("TOOL_TIMEOUT", "The tool call ran past the cell's time limit and was cancelled.")
            if task.cancelled():
                return _refuse("TOOL_CANCELLED", "The tool call was cancelled because the cell ended.")
            exc = task.exception()
            if exc is not None:
                logger.warning("Nested tool %s failed: %s", name, type(exc).__name__)
                return _refuse("TOOL_EXECUTION_ERROR", f"{type(exc).__name__}: {exc}")
            result = task.result()
        finally:
            if stop is not None:
                stop.cancel()
            self._inflight = None
        if not isinstance(result, ToolResult):
            return _refuse("TOOL_EXECUTION_ERROR", "The dispatcher returned no tool result.")
        meta = result.meta if isinstance(result.meta, dict) else {}
        if result.ok:
            return _reply(True, result.output or "")
        return _reply(False, result.output or "", result.error or "Tool call failed.", meta.get("code"))

    async def _invoke(self, name: str, args: dict, nested_id: str) -> ToolResult:
        dispatch = self.dispatch
        assert dispatch is not None
        if _accepts_call_id(dispatch):
            return await dispatch(name, args, self.call_id, call_id=nested_id)
        return await dispatch(name, args, self.call_id)


def _accepts_call_id(fn: Callable) -> bool:
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "call_id" or p.kind is p.VAR_KEYWORD for p in params)


class KernelToolServer:
    """The host end of one kernel's tools socket."""

    def __init__(self, socket_path: str):
        self.socket_path = socket_path
        self.token = secrets.token_hex(32)
        self._server: Optional[asyncio.AbstractServer] = None
        self._binding: Optional[KernelToolBinding] = None

    async def start(self) -> None:
        self._server = await asyncio.start_unix_server(self._handle, path=self.socket_path)
        os.chmod(self.socket_path, 0o600)

    def child_env(self) -> dict[str, str]:
        return {ENV_SOCK: self.socket_path, ENV_TOKEN: self.token}

    def activate(self, binding: KernelToolBinding) -> None:
        if self._binding is not None:
            self._binding.close()
        self._binding = binding

    def deactivate(self, binding: Optional[KernelToolBinding] = None) -> None:
        current = self._binding
        if binding is not None:
            binding.close()
        if current is not None and (binding is None or current is binding):
            current.close()
            self._binding = None

    async def close(self) -> None:
        self.deactivate()
        server, self._server = self._server, None
        if server is not None:
            server.close()
            try:
                await asyncio.wait_for(server.wait_closed(), timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                (size,) = _HEADER.unpack(await asyncio.wait_for(reader.readexactly(_HEADER.size), 10))
                if size > MAX_REQUEST_BYTES:
                    reply = _refuse("CODE_MODE_REQUEST_TOO_LARGE", "Tool request is too large.")
                else:
                    raw = await asyncio.wait_for(reader.readexactly(size), 30)
                    reply = await self._serve(json.loads(raw.decode("utf-8")))
            except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError):
                return
            except (ValueError, UnicodeDecodeError):
                reply = _refuse("CODE_MODE_BAD_REQUEST", "Malformed tool request.")
            data = json.dumps(reply, default=str).encode("utf-8")
            if len(data) > MAX_REPLY_BYTES:
                data = json.dumps(_refuse("CODE_MODE_REPLY_TOO_LARGE", "Tool output is too large.")).encode()
            writer.write(_HEADER.pack(len(data)) + data)
            await writer.drain()
        except Exception:  # noqa: BLE001 — the cell side reports a dropped connection
            logger.debug("kernel tool request failed", exc_info=True)
        finally:
            writer.close()

    async def _serve(self, req: Any) -> dict:
        if not isinstance(req, dict):
            return _refuse("CODE_MODE_BAD_REQUEST", "Malformed tool request.")
        token = req.get("token")
        if not isinstance(token, str) or not hmac.compare_digest(token.encode(), self.token.encode()):
            return _refuse("CODE_MODE_UNAUTHORIZED", "Invalid code-mode token.")
        binding = self._binding
        if binding is None or binding.closed:
            return _refuse("CODE_MODE_NO_CELL", "tools.* can only be used while a python cell is running.")
        op = req.get("op")
        if op == "call":
            return await binding.call(req.get("name"), req.get("args", {}))
        if binding.dispatch is None:
            return _refuse("CODE_MODE_UNAVAILABLE",
                           "Code mode is unavailable in this run: no tool dispatcher is attached.")
        if op == "list":
            return _reply(True, binding.catalog())
        if op == "describe":
            return binding.describe(req.get("name"))
        return _refuse("CODE_MODE_BAD_REQUEST", f"Unknown operation {op!r}.")


# Runs in the kernel after STARTUP_CODE when code mode is on. Kept free of
# str.format braces conflicts by not being formatted at all.
KERNEL_TOOLS_CODE = r'''
def _matopt_install_tools():
    import json, os, socket, struct
    path = os.environ.pop("MATOPT_TOOLS_SOCK", None)
    token = os.environ.pop("MATOPT_TOOLS_TOKEN", None)

    class ToolError(RuntimeError):
        """A MatOpt tool called from Python failed (``.code`` names why)."""
        def __init__(self, message, code=None, tool=None, output=""):
            super().__init__(message)
            self.code, self.tool, self.output = code, tool, output

    def _default(obj):
        for attr in ("tolist", "item"):
            if hasattr(obj, attr):
                return getattr(obj, attr)()
        if hasattr(obj, "__fspath__"):
            return os.fspath(obj)
        raise TypeError(f"{type(obj).__name__} is not JSON serializable")

    def _recv(sock, n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ToolError("The MatOpt tool server closed the connection.", "CODE_MODE_DISCONNECTED")
            buf += chunk
        return buf

    def _request(payload, tool=None):
        if not path or not token:
            raise ToolError("Code mode is not available in this kernel.", "CODE_MODE_UNAVAILABLE", tool)
        data = json.dumps(dict(payload, token=token), default=_default).encode("utf-8")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(path)
            sock.sendall(struct.pack(">I", len(data)) + data)
            (size,) = struct.unpack(">I", _recv(sock, 4))
            reply = json.loads(_recv(sock, size).decode("utf-8"))
        except OSError as exc:
            raise ToolError(f"Cannot reach the MatOpt tool server: {exc}", "CODE_MODE_DISCONNECTED", tool) from None
        finally:
            sock.close()
        if not reply.get("ok"):
            raise ToolError(reply.get("error") or "Tool call failed.", reply.get("code"), tool,
                            reply.get("output") or "")
        return reply.get("output")

    class MatOptTools:
        """Call MatOpt tools from Python: ``tools.read_file(path="a.csv")``.

        ``tools.list()`` lists callable tools, ``tools.describe(name)`` shows a
        tool's parameters. Calls return the tool's text output and raise
        ``ToolError`` on failure.
        """
        __slots__ = ()

        def __getattr__(self, name):
            if name.startswith("_"):
                raise AttributeError(name)
            def call(*args, **kwargs):
                if args:
                    raise TypeError(f"tools.{name}() takes keyword arguments only")
                return _request({"op": "call", "name": name, "args": kwargs}, name)
            call.__name__ = call.__qualname__ = name
            return call

        def call(self, name, **kwargs):
            return _request({"op": "call", "name": name, "args": kwargs}, name)

        def list(self):
            return _request({"op": "list"})

        def describe(self, name):
            return _request({"op": "describe", "name": name}, name)

        def __dir__(self):
            try:
                return [t["name"] for t in self.list()] + ["call", "describe", "list"]
            except Exception:
                return ["call", "describe", "list"]

        def __repr__(self):
            return "<MatOpt tools: tools.list() shows what is callable>"

    return MatOptTools(), ToolError

tools, ToolError = _matopt_install_tools()
del _matopt_install_tools
'''
