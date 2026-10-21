"""MCP access to the same validated, durable workspace execution service."""
from __future__ import annotations

import base64
import re
from collections import OrderedDict
from typing import Any
from urllib.parse import quote

from fastmcp import Context, FastMCP
from fastmcp.apps.config import AppConfig
from fastmcp.tools import ToolResult as McpResult
from mcp.types import AudioContent, ImageContent, ResourceLink, TextContent

from app.agent.events import Event, ToolResult
from app.agent.execution import SessionExecutionReceipt, restore_execution_state
from app.agent.tools.base import ToolContext
from app.storage.db import new_id
from app.ui.mcp_apps import VIEWER_URI, project_surface, viewer_html


def _runtime():
    from app.runtime import get_runtime
    return get_runtime()


def _valid_identity(cid: str) -> bool:
    return isinstance(cid, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", cid) is not None


def _protocol_result(result: ToolResult, ctx: ToolContext | None = None) -> McpResult:
    text = result.output or ("(no output)" if result.ok else "")
    if result.error:
        text = f"{text}\n{result.error}".strip()
    content: list = [TextContent(type="text", text=text)]
    for artifact in result.artifacts:
        if ctx is None or not artifact.path:
            continue
        mime = artifact.mime or "application/octet-stream"
        path = ctx.workspace.resolve(artifact.path)
        if path.is_file() and path.stat().st_size <= 8 * 1024 * 1024:
            if mime in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
                content.append(ImageContent(type="image", mimeType=mime,
                                            data=base64.b64encode(path.read_bytes()).decode("ascii")))
                continue
            if mime.startswith("audio/"):
                content.append(AudioContent(type="audio", mimeType=mime,
                                            data=base64.b64encode(path.read_bytes()).decode("ascii")))
                continue
        content.append(ResourceLink(type="resource_link", name=artifact.name, mimeType=mime,
            uri=f"matopt://{ctx.conversation_id}/{quote(artifact.path, safe='/')}",
            description="Workspace artifact; use the returned conversation_id and path with workspace tools."))
    return McpResult(content=content, structured_content=result.to_dict(), is_error=not result.ok)


class McpSandbox:
    """Application-owned workspace observations, independent of HTTP sessions."""
    def __init__(self, runtime: Any = None):
        self._runtime = runtime
        self._states: OrderedDict[str, dict] = OrderedDict()

    @property
    def runtime(self):
        return self._runtime if self._runtime is not None else _runtime()

    def open_workspace(self, conversation_id: str = "") -> dict:
        rt = self.runtime
        if conversation_id:
            if not _valid_identity(conversation_id):
                raise ValueError("Invalid conversation_id")
            if rt.db.get_conversation(conversation_id) is None:
                rt.db.create_conversation(title="MCP sandbox", conversation_id=conversation_id)
            cid = conversation_id
        else:
            cid = rt.db.create_conversation(title="MCP sandbox")["id"]
        return {"conversation_id": cid, "files": rt.workspace(cid).list_files(),
                "instructions": "Pass this conversation_id on every workspace tool call."}

    def open_ui_app(self, conversation_id: str, surface_id: str) -> McpResult:
        """Project one saved surface for an MCP Apps host. Never runs handlers."""
        rt = self.runtime
        if not _valid_identity(conversation_id) or rt.db.get_conversation(conversation_id) is None:
            return _protocol_result(ToolResult(ok=False, error="Unknown conversation_id.", meta={"code": "MCP_WORKSPACE_UNKNOWN"}))
        surface = None
        if isinstance(surface_id, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", surface_id):
            surface = rt.harness.ui.store.get(conversation_id, surface_id)
        if surface is None:
            return _protocol_result(ToolResult(ok=False, error="Unknown surface_id.", meta={"code": "UI_SURFACE_UNKNOWN"}))
        if not rt.harness.ui.enabled(surface["kind"]):
            return _protocol_result(ToolResult(ok=False, error="This interactive UI kind is disabled.", output=surface["fallback"],
                                               meta={"code": "UI_DISABLED"}))
        text, summary, meta = project_surface(surface)
        return McpResult(content=[TextContent(type="text", text=text)], structured_content=summary, meta=meta)

    async def execute(self, name: str, conversation_id: str, arguments: dict, mcp_context: Context | None = None) -> McpResult:
        rt = self.runtime
        if not _valid_identity(conversation_id) or rt.db.get_conversation(conversation_id) is None:
            return _protocol_result(ToolResult(ok=False, error="Unknown conversation_id. Call open_workspace first.",
                                               meta={"code": "MCP_WORKSPACE_UNKNOWN"}))
        tool = rt.tools.get(name)
        if tool is None:
            return _protocol_result(ToolResult(ok=False, error=f"Tool {name!r} is unavailable."))
        # The native lease also guards MCP requests. Independent HTTP requests
        # never depend on a ContextVar to choose their workspace.
        try:
            cancel = rt.harness.claim_external_run(conversation_id)
        except Exception as exc:
            from app.ui.store import UIConflict
            if not isinstance(exc, UIConflict):
                raise
            return _protocol_result(ToolResult(ok=False, error=str(exc), meta={"code": "WORKSPACE_BUSY"}))
        try:
            session = await rt.harness._open_session(conversation_id)
            state = self._states.setdefault(conversation_id, {})
            self._states.move_to_end(conversation_id)
            while len(self._states) > 32:
                self._states.popitem(last=False)

            async def emit(event: Event) -> None:
                if mcp_context is None:
                    return
                if event.type == "tool_activity_progress":
                    measured = event.data.get("progress", {})
                    await mcp_context.report_progress(measured.get("completed", 0), measured.get("total"))
                elif event.type == "tool_running":
                    await mcp_context.info(f"Running {name}")
                elif event.type == "tool_progress":
                    # This is explicit MCP tool output. Ordinary chat SSE uses
                    # the separate public projection, never this raw channel.
                    await mcp_context.info(str(event.data.get("delta", "")))

            ctx = ToolContext(conversation_id, rt.workspace(conversation_id), emit, cancel,
                              call_id=new_id("mcp_"), state=state, kernels=rt.kernels,
                              settings=rt.executions.settings)
            restore_execution_state(ctx, session.events)
            result = await rt.executions.dispatch(tool, ctx, arguments,
                                                  receipt=SessionExecutionReceipt(session))
            return _protocol_result(result, ctx)
        finally:
            rt.harness.release_external_run(conversation_id, cancel)


def build_mcp(runtime: Any = None):
    """Expose native tools, with explicit workspace identity on every call."""
    sandbox = McpSandbox(runtime)
    mcp = FastMCP("matopt-sandbox", instructions=(
        "MatOpt persistent Python kernel and workspace. Call open_workspace once, then pass its "
        "conversation_id to EVERY tool call; HTTP sessions never imply a selected workspace. "
        "Read an existing file before editing or overwriting it. Paths are workspace-relative. "
        "An interrupted mutation requires inspecting its outcome before retrying."))
    ro = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
    rw = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
    system = {**rw, "openWorldHint": True}
    # MCP Apps views may call any tool whose visibility includes "app" (the
    # default). Mini-app HTML is model-authored, so no tool is app-callable.
    model_only = AppConfig(visibility=["model"])

    @mcp.tool(annotations=rw, app=model_only)
    async def open_workspace(conversation_id: str = "") -> dict:
        """Create or select a workspace; retain its conversation_id for every subsequent call."""
        return sandbox.open_workspace(conversation_id)

    @mcp.tool(annotations=system, app=model_only)
    async def python(conversation_id: str, code: str, ctx: Context, timeout: int = 120, reset: bool = False) -> McpResult:
        """Execute Python in this workspace's persistent kernel. Active work returns WORKSPACE_BUSY."""
        return await sandbox.execute("python", conversation_id, {"code": code, "timeout": timeout, "reset": reset}, ctx)

    @mcp.tool(annotations=system, app=model_only)
    async def bash(conversation_id: str, command: str, ctx: Context, timeout: int = 120) -> McpResult:
        """Run a bounded shell command in the explicit workspace."""
        return await sandbox.execute("bash", conversation_id, {"command": command, "timeout": timeout}, ctx)

    @mcp.tool(annotations=ro, app=model_only)
    async def read_file(conversation_id: str, path: str, ctx: Context, start_line: int = 1, max_lines: int = 400) -> McpResult:
        """Read text and preserve its version receipt for edits, including across HTTP requests."""
        return await sandbox.execute("read_file", conversation_id,
                                     {"path": path, "start_line": start_line, "max_lines": max_lines}, ctx)

    @mcp.tool(annotations=rw, app=model_only)
    async def write_file(conversation_id: str, path: str, content: str, ctx: Context) -> McpResult:
        """Create text, or overwrite an existing file only after reading its current version."""
        return await sandbox.execute("write_file", conversation_id, {"path": path, "content": content}, ctx)

    @mcp.tool(annotations=rw, app=model_only)
    async def edit_file(conversation_id: str, path: str, old_string: str, new_string: str, ctx: Context, replace_all: bool = False) -> McpResult:
        """Replace exact text after a fresh read. Refuse stale or missing file observations."""
        return await sandbox.execute("edit_file", conversation_id,
                                     {"path": path, "old_string": old_string, "new_string": new_string, "replace_all": replace_all}, ctx)

    @mcp.tool(annotations=ro, app=model_only)
    async def list_files(conversation_id: str, ctx: Context, pattern: str = "**/*", path: str = "") -> McpResult:
        """List files in the explicit workspace."""
        return await sandbox.execute("list_files", conversation_id, {"pattern": pattern, "path": path}, ctx)

    @mcp.tool(annotations=ro, app=model_only)
    async def grep(conversation_id: str, pattern: str, ctx: Context, path: str = "", glob: str = "", case_sensitive: bool = True) -> McpResult:
        """Search text files in the explicit workspace."""
        args = {"pattern": pattern, "case_sensitive": case_sensitive}
        if path:
            args["path"] = path
        if glob:
            args["glob"] = glob
        return await sandbox.execute("grep", conversation_id, args, ctx)

    @mcp.tool(annotations=rw, app=model_only)
    async def parse_document(conversation_id: str, path: str, ctx: Context) -> McpResult:
        """OCR or convert a PDF/image, returning structured results and generated artifacts."""
        return await sandbox.execute("parse_document", conversation_id, {"path": path}, ctx)

    @mcp.tool(annotations=ro, app=model_only)
    async def check_equivalence(conversation_id: str, expression_a: str, expression_b: str, ctx: Context) -> McpResult:
        """Check whether two mathematical expressions are equivalent."""
        return await sandbox.execute("check_equivalence", conversation_id,
                                     {"expression_a": expression_a, "expression_b": expression_b}, ctx)

    @mcp.resource(VIEWER_URI, name="MatOpt mini-app viewer",
                  description="MCP Apps view for a saved MatOpt mini-app; data arrives in the open_ui_app result.")
    def mini_app_viewer() -> str:
        return viewer_html()

    @mcp.tool(annotations=ro, app=AppConfig(resource_uri=VIEWER_URI, visibility=["model"]))
    async def open_ui_app(conversation_id: str, surface_id: str) -> McpResult:
        """Show a saved interactive mini-app; text clients receive its readable fallback."""
        return sandbox.open_ui_app(conversation_id, surface_id)

    return mcp


_mcp = None


def get_mcp():
    global _mcp
    if _mcp is None:
        _mcp = build_mcp()
    return _mcp


def build_mcp_asgi():
    """Starlette app mounted at /mcp; workspace identity is request-explicit."""
    mcp = get_mcp()
    try:
        return mcp.http_app(stateless_http=True, path="/")
    except TypeError:
        return mcp.http_app(path="/")
