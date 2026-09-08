"""
MatOpt sandbox as a modern MCP server.

The old RestrictedPython eval loop is gone; the conversation kernel + workspace
are the sandbox. This module exposes that environment over MCP (Streamable HTTP
on the FastAPI app, or stdio via ``python -m app.agent.mcp``) so other MCP
clients can run the same tools the harness uses.
"""

from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from typing import Any, Optional

from app.agent.events import Event
from app.agent.tools.base import ToolContext
from app.config import settings

logger = logging.getLogger(__name__)

_conversation_id: ContextVar[Optional[str]] = ContextVar("matopt_mcp_cid", default=None)


def _noop_emit(_event: Event) -> Any:
    async def _inner() -> None:
        return None

    return _inner()


async def _emit_drop(_event: Event) -> None:
    return None


def _tool_context(conversation_id: str) -> ToolContext:
    from app.runtime import get_runtime

    rt = get_runtime()
    return ToolContext(
        conversation_id=conversation_id,
        workspace=rt.workspace(conversation_id),
        emit=_emit_drop,
        cancel=asyncio.Event(),
        kernels=rt.kernels,
        settings=settings,
        state={},
    )


async def _ensure_conversation(conversation_id: Optional[str] = None) -> str:
    from app.runtime import get_runtime

    rt = get_runtime()
    cid = conversation_id or _conversation_id.get()
    if cid:
        if rt.db.get_conversation(cid) is None:
            rt.db.create_conversation(title="MCP sandbox", conversation_id=cid)
        _conversation_id.set(cid)
        return cid
    conv = rt.db.create_conversation(title="MCP sandbox")
    _conversation_id.set(conv["id"])
    return conv["id"]


def _text_result(res: Any) -> str:
    if res.ok:
        return res.output or "(no output)"
    err = res.error or "error"
    return f"{res.output}\n[error] {err}".strip()


def build_mcp():
    """Build the FastMCP server wrapping native sandbox tools."""
    from fastmcp import FastMCP

    from app.agent.tools.document_tool import ParseDocumentTool
    from app.agent.tools.file_tools import EditFileTool, GrepTool, ListFilesTool, ReadFileTool, WriteFileTool
    from app.agent.tools.math_tools import CheckEquivalenceTool
    from app.agent.tools.python_tool import PythonTool
    from app.agent.tools.shell_tool import ShellTool

    mcp = FastMCP(
        "matopt-sandbox",
        instructions=(
            "MatOpt conversation sandbox: a persistent Jupyter kernel and a private workspace. "
            "Call open_workspace first (optionally with a conversation id). Paths are workspace-relative. "
            "Prefer python for computation; bash for pip and file conversion."
        ),
    )
    python_tool = PythonTool(settings.tool_timeout, settings.tool_max_timeout, settings.tool_max_output_chars)
    shell_tool = ShellTool(settings.tool_timeout, settings.tool_max_timeout, settings.tool_max_output_chars)
    read_tool, write_tool, edit_tool = ReadFileTool(), WriteFileTool(), EditFileTool()
    list_tool, grep_tool = ListFilesTool(), GrepTool()
    parse_tool, eq_tool = ParseDocumentTool(), CheckEquivalenceTool()

    @mcp.tool
    async def open_workspace(conversation_id: str = "") -> str:
        """Select or create the sandbox conversation. Call this before other tools."""
        cid = await _ensure_conversation(conversation_id or None)
        ctx = _tool_context(cid)
        listing = ctx.workspace.describe_for_prompt()
        return f"Workspace ready for conversation {cid}.\n{listing}"

    @mcp.tool
    async def python(code: str, timeout: int = 120, reset: bool = False) -> str:
        """Execute Python in the persistent Jupyter kernel bound to this workspace."""
        cid = await _ensure_conversation()
        ctx = _tool_context(cid)
        res = await python_tool(ctx, {"code": code, "timeout": timeout, "reset": reset})
        return _text_result(res)

    @mcp.tool
    async def bash(command: str, timeout: int = 120) -> str:
        """Run a shell command in the workspace."""
        cid = await _ensure_conversation()
        ctx = _tool_context(cid)
        res = await shell_tool(ctx, {"command": command, "timeout": timeout})
        return _text_result(res)

    @mcp.tool
    async def read_file(path: str, start_line: int = 1, max_lines: int = 400) -> str:
        """Read a workspace text file."""
        cid = await _ensure_conversation()
        ctx = _tool_context(cid)
        res = await read_tool(ctx, {"path": path, "start_line": start_line, "max_lines": max_lines})
        return _text_result(res)

    @mcp.tool
    async def write_file(path: str, content: str) -> str:
        """Write a workspace text file."""
        cid = await _ensure_conversation()
        ctx = _tool_context(cid)
        res = await write_tool(ctx, {"path": path, "content": content})
        return _text_result(res)

    @mcp.tool
    async def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        """Replace text in a workspace file."""
        cid = await _ensure_conversation()
        ctx = _tool_context(cid)
        res = await edit_tool(ctx, {"path": path, "old_string": old_string, "new_string": new_string, "replace_all": replace_all})
        return _text_result(res)

    @mcp.tool
    async def list_files(pattern: str = "**/*", path: str = "") -> str:
        """List workspace files."""
        cid = await _ensure_conversation()
        ctx = _tool_context(cid)
        args = {"pattern": pattern}
        if path:
            args["path"] = path
        res = await list_tool(ctx, args)
        return _text_result(res)

    @mcp.tool
    async def grep(pattern: str, path: str = "", glob: str = "", case_sensitive: bool = True) -> str:
        """Search workspace files with a regular expression."""
        cid = await _ensure_conversation()
        ctx = _tool_context(cid)
        args: dict[str, Any] = {"pattern": pattern, "case_sensitive": case_sensitive}
        if path:
            args["path"] = path
        if glob:
            args["glob"] = glob
        res = await grep_tool(ctx, args)
        return _text_result(res)

    @mcp.tool
    async def parse_document(path: str) -> str:
        """OCR / convert a PDF or image in the workspace to Markdown."""
        cid = await _ensure_conversation()
        ctx = _tool_context(cid)
        res = await parse_tool(ctx, {"path": path})
        return _text_result(res)

    @mcp.tool
    async def check_equivalence(expression_a: str, expression_b: str) -> str:
        """Decide whether two mathematical expressions are equivalent."""
        cid = await _ensure_conversation()
        ctx = _tool_context(cid)
        res = await eq_tool(ctx, {"expression_a": expression_a, "expression_b": expression_b})
        return _text_result(res)

    return mcp


_mcp = None


def get_mcp():
    global _mcp
    if _mcp is None:
        _mcp = build_mcp()
    return _mcp


def build_mcp_asgi():
    """Starlette app for mounting at /mcp (Streamable HTTP, stateless)."""
    mcp = get_mcp()
    kwargs = {"path": "/"}
    try:
        return mcp.http_app(stateless_http=True, **kwargs)
    except TypeError:
        return mcp.http_app(**kwargs)
