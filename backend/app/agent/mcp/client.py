"""Wrap remote MCP tools as MatOpt ``Tool`` instances."""

from __future__ import annotations

import asyncio
import logging
import math
import re
from typing import Any, Optional

from app.agent.events import ToolResult
from app.agent.mcp.config import load_mcp_config
from app.agent.mcp.results import convert_result, field, json_value
from app.agent.tools.base import Tool, ToolContext, output_bound_bytes
from app.agent.events import ev
from jsonschema import Draft202012Validator

logger = logging.getLogger(__name__)


class McpProxyTool(Tool):
    """A tool that lives on an external MCP server."""

    mcp_server: str = ""

    def __init__(self, client: Any, server: str, spec: Any, max_output_chars: int = 12_000):
        self._client = client
        self.mcp_server = server
        remote = field(spec, "name")
        if not isinstance(remote, str) or re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", remote) is None:
            raise ValueError("Invalid MCP tool name")
        self.remote_name = remote
        self.name = f"{server}__{remote}" if server else remote
        self.description = field(spec, "description") or f"MCP tool {remote} from {server}."
        if not isinstance(self.description, str):
            raise ValueError("MCP description must be text")
        schema = field(spec, "inputSchema", "input_schema", default={"type": "object", "properties": {}})
        self.parameters = json_value(schema)
        if not isinstance(self.parameters, dict):
            raise ValueError("MCP inputSchema must be an object")
        Draft202012Validator.check_schema(self.parameters)
        annotations = field(spec, "annotations", default={})
        self.read_only = field(annotations, "readOnlyHint", "read_only_hint", default=False) is True
        # A remote server's tool is opaque: `readOnlyHint` is the only hint we
        # get, so derive the safety surface from it here (the class-level
        # default cannot see the instance flag). Anything that is not declared
        # read-only is treated as writing *outside* the conversation workspace
        # — the server does whatever it does, on its own machine.
        self.destructive = field(annotations, "destructiveHint", "destructive_hint", default=False) is True and not self.read_only
        self.concurrent_safe = self.read_only
        self.side_effect_scope = "none" if self.read_only else "system"
        self.risk_level = "low" if self.read_only else "medium"
        self.needs_approval = not self.read_only
        self.max_output_bytes = output_bound_bytes(max_output_chars)
        self.ui_kind = "generic"
        self.max_output_chars = max_output_chars

    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        async def progress(completed: float, total: float | None = None, message: str | None = None) -> None:
            # Server prose can contain tool data. Only measured counts enter
            # the public activity channel; private text stays private.
            if type(completed) in (int, float) and math.isfinite(completed) and completed >= 0:
                measured = {"completed": completed, "unit": "steps"}
                if type(total) in (int, float) and math.isfinite(total) and total >= completed:
                    measured["total"] = total
                await ctx.emit(ev("tool_activity_progress", id=ctx.call_id, progress=measured))
            if message:
                await ctx.progress(str(message))

        timeout = getattr(ctx.settings, "tool_timeout", 120) if ctx.settings is not None else 120
        try:
            result = await self._client.call_tool(self.remote_name, kwargs or {}, timeout=timeout,
                                                   progress_handler=progress, raise_on_error=False)
        except asyncio.CancelledError:
            raise  # shared dispatcher owns cancellation and its durable outcome
        except Exception as exc:  # noqa: BLE001
            meta = {"code": "MCP_TRANSPORT_ERROR", "execution_id": ctx.call_id}
            if not self.read_only:
                meta["recovery_code"] = "TOOL_OUTCOME_UNKNOWN"
            return ToolResult(ok=False, error=f"MCP {self.name} transport failed: {type(exc).__name__}", meta=meta)
        try:
            return convert_result(result, ctx, self.max_output_chars)
        except (ValueError, TypeError) as exc:
            return ToolResult(ok=False, error=f"MCP result could not be represented: {type(exc).__name__}",
                              meta={"code": "MCP_RESULT_INVALID"})


class McpHub:
    """Long-lived FastMCP client over the configured extra servers."""

    def __init__(self, config_path: Optional[Any] = None, max_output_chars: int = 12_000):
        self.config_path = config_path
        self.max_output_chars = max_output_chars
        self._clients: list[Any] = []
        self._tools: list[McpProxyTool] = []

    def tools(self) -> list[Tool]:
        return list(self._tools)

    async def start(self) -> None:
        try:
            cfg = load_mcp_config(self.config_path)
        except (ValueError, OSError):
            logger.warning("Invalid MCP configuration; extra servers are disabled")
            return
        servers = cfg.get("mcpServers") or {}
        if not servers:
            return
        try:
            from fastmcp import Client
        except ImportError:
            logger.warning("fastmcp is not installed; extra MCP servers are disabled")
            return
        if self._clients:
            return
        for server, configuration in servers.items():
            client = None
            try:
                # Independent connections isolate a broken server. A
                # single-server FastMCP connection uses unprefixed remote names.
                client = Client({"mcpServers": {server: configuration}})
                await client.__aenter__()
                listed = await client.list_tools()
            except Exception as exc:  # noqa: BLE001
                # Transport errors may embed configured URLs or headers.
                logger.warning("Failed to connect/list MCP server %s (%s)", server, type(exc).__name__)
                if client is not None:
                    try:
                        await client.__aexit__(None, None, None)
                    except Exception:
                        pass
                continue
            self._clients.append(client)
            for spec in listed:
                try:
                    tool = McpProxyTool(client, server, spec, self.max_output_chars)
                    # Retain historical advertised names while keeping exact
                    # server identity; underscores need no reverse inference.
                    tool.name = f"{server}_{tool.remote_name}" if len(servers) > 1 else tool.remote_name
                    if any(existing.name == tool.name for existing in self._tools):
                        raise ValueError("Duplicate MCP tool name")
                    self._tools.append(tool)
                except Exception:  # noqa: BLE001
                    logger.warning("Ignoring malformed tool metadata from MCP server %s", server)
        logger.info("MCP hub: %d extra tool(s) from %s", len(self._tools), self.config_path)

    async def stop(self) -> None:
        clients, self._clients = self._clients, []
        self._tools.clear()
        for client in reversed(clients):
            try:
                await client.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                logger.debug("MCP hub shutdown failed", exc_info=True)
