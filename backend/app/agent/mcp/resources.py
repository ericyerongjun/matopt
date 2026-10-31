"""Read-only native tools over MCP *resources* (list / read).

MCP servers can share context besides tools: files, schemas, documents,
addressed by URI. Codex exposes them through two built-in function tools,
``list_mcp_resources`` and ``read_mcp_resource``
(``core/src/tools/handlers/mcp_resource_spec.rs`` for the schemas,
``core/src/tools/handlers/mcp_resource.rs`` for the behaviour,
``codex-mcp/src/resource_client.rs`` for the per-server calls). This module
ports that pair onto :class:`app.agent.mcp.client.McpHub`:

* listing without ``server`` returns the first page of every server that
  advertises the ``resources`` capability; a ``cursor`` is only meaningful
  for one server, so it requires ``server`` (Codex rejects the same mix);
* reading returns the resource contents through the same conversion as MCP
  tool results (``mcp/results.py``), so text is bounded by the output budget
  and binary blobs become workspace artifacts instead of prompt text;
* both tools are advertised only while at least one connected server
  supports resources (``is_provider_visible``), so a deployment without such
  servers sees no new tools.

Reads are side-effect free by protocol, so the tools are ``read_only`` with
scope ``none`` and may run concurrently. Error text names the exception type,
plus the server's own JSON-RPC message when it sent one; transport errors are
never echoed because they can contain configured URLs or headers.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.agent.events import ToolResult
from app.agent.mcp.results import convert_result, field, json_value
from app.agent.tools.base import Tool, ToolContext, output_bound_bytes, truncate_text

MAX_SERVER_ERROR_CHARS = 200
RESOURCE_KEYS = ("uri", "name", "title", "description", "mimeType", "size")


def _error_text(exc: BaseException) -> str:
    """Exception type, plus a JSON-RPC error message the server itself sent.

    The MCP SDK raises ``MCPError`` (``McpError`` in older releases) carrying
    the server's ``ErrorData``; only that message is echoed. Any other
    exception is a local transport failure whose text may hold configuration.
    """
    from mcp.types import ErrorData
    error = getattr(exc, "error", None)
    if type(exc).__name__ in ("MCPError", "McpError") and isinstance(error, ErrorData) and error.message:
        return f"{type(exc).__name__}: {error.message[:MAX_SERVER_ERROR_CHARS]}"
    return type(exc).__name__


def _resource_row(server: str, resource: Any) -> dict:
    value = json_value(resource)
    row: dict[str, Any] = {"server": server}
    if isinstance(value, dict):
        row.update({key: value[key] for key in RESOURCE_KEYS if value.get(key) is not None})
    return row


class _ResourceTool(Tool):
    read_only = True
    concurrent_safe = True
    side_effect_scope = "none"
    risk_level = "low"
    ui_kind = "generic"

    def __init__(self, hub: Any, max_output_chars: int | None = None):
        self.hub = hub
        self.max_output_chars = int(max_output_chars or getattr(hub, "max_output_chars", 12_000))
        self.max_output_bytes = output_bound_bytes(self.max_output_chars)

    def is_provider_visible(self, ctx: ToolContext) -> bool:
        return super().is_provider_visible(ctx) and bool(self.hub.supports_resources())

    def _resource_client(self, server: str) -> tuple[Any | None, ToolResult | None]:
        client = self.hub.client(server)
        if client is None:
            known = ", ".join(self.hub.resource_servers()) or "none"
            return None, ToolResult(ok=False, error=f"Unknown MCP server '{server}'. Servers with resources: {known}.",
                                    meta={"code": "MCP_UNKNOWN_SERVER"})
        if server not in self.hub.resource_servers():
            return None, ToolResult(ok=False, error=f"MCP server '{server}' does not provide resources.",
                                    meta={"code": "MCP_RESOURCES_UNSUPPORTED"})
        return client, None


class ListMcpResourcesTool(_ResourceTool):
    name = "list_mcp_resources"
    description = ("Lists resources provided by MCP servers. Resources allow servers to share data that provides "
                   "context, such as files, database schemas, or application-specific information. Prefer resources "
                   "over web search when possible.")
    parameters = {
        "type": "object",
        "properties": {
            "server": {"type": "string",
                       "description": "MCP server name. Omit to list resources from every configured server."},
            "cursor": {"type": "string",
                       "description": "Opaque cursor from a previous list_mcp_resources call; omit for the first page."},
        },
        "additionalProperties": False,
    }

    async def _page(self, client: Any, cursor: str | None) -> tuple[list, str | None]:
        if hasattr(client, "list_resources_mcp"):
            result = await client.list_resources_mcp(cursor=cursor)
            return list(field(result, "resources", default=[]) or []), field(result, "next_cursor", "nextCursor")
        if cursor is not None:
            raise ValueError("This MCP client cannot page resources")
        return list(await client.list_resources() or []), None

    async def run(self, ctx: ToolContext, server: str | None = None, cursor: str | None = None) -> ToolResult:
        server = (server or "").strip() or None
        cursor = (cursor or "").strip() or None
        if server is None and cursor is not None:
            return ToolResult(ok=False, error="cursor can only be used when a server is specified",
                              meta={"code": "TOOL_ARGUMENTS_INVALID"})
        targets = [server] if server is not None else self.hub.resource_servers()
        payload: dict[str, Any] = {"resources": []}
        errors: list[dict] = []
        for name in targets:
            client, failure = self._resource_client(name)
            if failure is not None:
                return failure
            try:
                resources, next_cursor = await self._page(client, cursor)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                if server is not None:
                    return ToolResult(ok=False, error=f"Listing MCP resources from '{name}' failed: {_error_text(exc)}",
                                      meta={"code": "MCP_RESOURCE_ERROR"})
                errors.append({"server": name, "error": _error_text(exc)})
                continue
            payload["resources"].extend(_resource_row(name, resource) for resource in resources)
            if server is not None and next_cursor:
                payload["nextCursor"] = str(next_cursor)
        if errors:
            payload["errors"] = errors
        text = json.dumps(payload, ensure_ascii=False)
        return convert_result({"content": [{"type": "text", "text": text}]}, ctx, self.max_output_chars)


class ReadMcpResourceTool(_ResourceTool):
    name = "read_mcp_resource"
    description = "Read a specific resource from an MCP server given the server name and resource URI."
    parameters = {
        "type": "object",
        "properties": {
            "server": {"type": "string",
                       "description": "MCP server name exactly as configured. Must match the 'server' field "
                                      "returned by list_mcp_resources."},
            "uri": {"type": "string",
                    "description": "Resource URI to read. Must be one of the URIs returned by list_mcp_resources."},
        },
        "required": ["server", "uri"],
        "additionalProperties": False,
    }

    async def run(self, ctx: ToolContext, server: str, uri: str) -> ToolResult:
        server, uri = (server or "").strip(), (uri or "").strip()
        if not server or not uri:
            return ToolResult(ok=False, error="server and uri are required", meta={"code": "TOOL_ARGUMENTS_INVALID"})
        client, failure = self._resource_client(server)
        if failure is not None:
            return failure
        try:
            contents = await client.read_resource(uri)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=f"Reading MCP resource from '{server}' failed: {_error_text(exc)}",
                              meta={"code": "MCP_RESOURCE_ERROR"})
        if not isinstance(contents, (list, tuple)):
            contents = field(contents, "contents", default=[]) or []
        blocks = [{"type": "resource", "resource": json_value(item)} for item in contents]
        try:
            result = convert_result({"content": blocks}, ctx, self.max_output_chars)
        except (ValueError, TypeError) as exc:
            return ToolResult(ok=False, error=f"MCP resource could not be represented: {type(exc).__name__}",
                              meta={"code": "MCP_RESULT_INVALID"})
        result.meta.update(mcp_server=server, uri=uri)
        if not blocks:
            result.output = truncate_text("(resource is empty)", self.max_output_chars)
        return result


def resource_tools(hub: Any) -> list[Tool]:
    """Both resource tools for ``hub``; they hide themselves without resource servers."""
    return [ListMcpResourcesTool(hub), ReadMcpResourceTool(hub)]
