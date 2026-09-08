"""Wrap remote MCP tools as MatOpt ``Tool`` instances."""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.agent.events import ToolResult
from app.agent.mcp.config import load_mcp_config
from app.agent.tools.base import Tool, ToolContext, truncate_text

logger = logging.getLogger(__name__)


def _result_text(result: Any) -> str:
    data = getattr(result, "data", None)
    if data is not None and not isinstance(data, (bytes, bytearray)):
        return str(data)
    content = getattr(result, "content", None) or []
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
            continue
        if isinstance(block, dict) and block.get("text"):
            parts.append(str(block["text"]))
    return "\n".join(parts).strip() or "(no output)"


class McpProxyTool(Tool):
    """A tool that lives on an external MCP server."""

    mcp_server: str = ""

    def __init__(self, client: Any, server: str, spec: Any, max_output_chars: int = 12_000):
        self._client = client
        self.mcp_server = server
        remote = getattr(spec, "name", None) or spec.get("name")
        self.remote_name = remote
        self.name = f"{server}__{remote}" if server else remote
        self.description = (getattr(spec, "description", None) or "") or f"MCP tool {remote} from {server}."
        schema = getattr(spec, "inputSchema", None) or getattr(spec, "input_schema", None) or {"type": "object", "properties": {}}
        if hasattr(schema, "model_dump"):
            schema = schema.model_dump()
        self.parameters = schema if isinstance(schema, dict) else {"type": "object", "properties": {}}
        self.read_only = bool(getattr(spec, "readOnlyHint", False) or (getattr(spec, "annotations", None) or {}).get("readOnlyHint"))
        self.ui_kind = "generic"
        self.max_output_chars = max_output_chars

    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        try:
            result = await self._client.call_tool(self.remote_name, kwargs or {})
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, output="", error=f"MCP {self.name} failed: {exc}")
        is_error = bool(getattr(result, "is_error", False) or getattr(result, "isError", False))
        text = truncate_text(_result_text(result), self.max_output_chars)
        return ToolResult(ok=not is_error, output=text, error=("MCP tool reported an error" if is_error else None))


class McpHub:
    """Long-lived FastMCP client over the configured extra servers."""

    def __init__(self, config_path: Optional[Any] = None, max_output_chars: int = 12_000):
        self.config_path = config_path
        self.max_output_chars = max_output_chars
        self._client = None
        self._tools: list[McpProxyTool] = []

    def tools(self) -> list[Tool]:
        return list(self._tools)

    async def start(self) -> None:
        cfg = load_mcp_config(self.config_path)
        servers = cfg.get("mcpServers") or {}
        if not servers:
            return
        try:
            from fastmcp import Client
        except ImportError:
            logger.warning("fastmcp is not installed; extra MCP servers are disabled")
            return
        client = Client(cfg)
        try:
            await client.__aenter__()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to start extra MCP servers from %s", self.config_path)
            return
        self._client = client
        try:
            listed = await client.list_tools()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to list extra MCP tools")
            listed = []
        for spec in listed:
            name = getattr(spec, "name", "") or ""
            server = ""
            remote = name
            if "_" in name:
                # FastMCP prefixes multi-server tools as server_tool
                server, remote = name.split("_", 1)
            tool = McpProxyTool(client, server or "mcp", spec, self.max_output_chars)
            tool.remote_name = name  # call the prefixed name the client expects
            tool.name = name if name else tool.name
            self._tools.append(tool)
        logger.info("MCP hub: %d extra tool(s) from %s", len(self._tools), self.config_path)

    async def stop(self) -> None:
        client = self._client
        self._client = None
        self._tools.clear()
        if client is None:
            return
        try:
            await client.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            logger.debug("MCP hub shutdown failed", exc_info=True)
