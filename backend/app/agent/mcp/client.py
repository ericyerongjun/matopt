"""Wrap remote MCP tools as MatOpt ``Tool`` instances and track server status.

The hub keeps one FastMCP client per configured server (a broken server is
isolated from healthy ones) plus a per-server status row, following Codex's
``codex-mcp`` connection manager: each server is ``connected``, ``failed`` or
``disabled``, with a tool count and whether it advertises the MCP
``resources`` capability (used by ``app/agent/mcp/resources.py``). Status
error text is the exception *type* only: transport errors can embed
configured URLs, headers or tokens, which must never reach the settings API.
"""

from __future__ import annotations

import asyncio
import hashlib
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

# Per-server keys MatOpt reads itself (Codex's ``McpServerConfig``,
# ``config/src/mcp_types.rs``). They are stripped before the entry reaches
# FastMCP, whose transport config does not know them.
SERVER_POLICY_KEYS = ("enabled", "startup_timeout_sec", "tool_timeout_sec", "enabled_tools", "disabled_tools")
DEFAULT_STARTUP_TIMEOUT = 30.0
# Chat Completions caps function names at 64 characters.
MAX_TOOL_NAME = 64


def namespaced_name(server: str, remote: str) -> str:
    """Codex's collision-proof ``mcp__<server>__<tool>`` name, within 64 chars.

    Used when the historical name (bare, or ``<server>_<tool>``) would collide
    with a native tool or another server's tool. Over-long names keep a sha1
    suffix so two long names cannot truncate to the same string.
    """
    clean = re.sub(r"[^A-Za-z0-9_-]", "_", f"mcp__{server}__{remote}")
    if len(clean) <= MAX_TOOL_NAME:
        return clean
    digest = hashlib.sha1(clean.encode()).hexdigest()[:8]
    return f"{clean[:MAX_TOOL_NAME - 9]}_{digest}"


def _server_policy(configuration: Any) -> tuple[dict, dict]:
    """Split one ``mcpServers`` entry into (transport config, MatOpt policy)."""
    if not isinstance(configuration, dict):
        return configuration, {}
    transport = {k: v for k, v in configuration.items() if k not in SERVER_POLICY_KEYS}
    policy = {k: configuration[k] for k in SERVER_POLICY_KEYS if k in configuration}
    return transport, policy


def _positive(value: Any, default: float | None) -> float | None:
    if type(value) in (int, float) and math.isfinite(value) and value > 0:
        return float(value)
    return default


def _tool_allowed(remote: str, policy: dict) -> bool:
    enabled, disabled = policy.get("enabled_tools"), policy.get("disabled_tools")
    if isinstance(enabled, list) and remote not in enabled:
        return False
    return not (isinstance(disabled, list) and remote in disabled)


def _advertises_resources(client: Any) -> bool:
    """Whether the initialized server declared the ``resources`` capability.

    FastMCP exposes the negotiated ``ServerCapabilities`` after ``__aenter__``.
    A client without that information (or a server that omitted the
    capability) is treated as not supporting resources.
    """
    try:
        capabilities = getattr(client, "server_capabilities", None)
    except Exception:  # noqa: BLE001 - property may raise before initialization
        return False
    return field(capabilities, "resources") is not None if capabilities is not None else False


class McpProxyTool(Tool):
    """A tool that lives on an external MCP server."""

    mcp_server: str = ""

    def __init__(self, client: Any, server: str, spec: Any, max_output_chars: int = 12_000,
                 tool_timeout: float | None = None):
        self._client = client
        self.tool_timeout = tool_timeout
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
        if self.tool_timeout is not None:
            timeout = self.tool_timeout
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
        # server name -> connected client, and server name -> status row.
        self._by_server: dict[str, Any] = {}
        self._status: dict[str, dict[str, Any]] = {}

    def tools(self) -> list[Tool]:
        return list(self._tools)

    def client(self, server: str) -> Any | None:
        """The connected client for ``server``, or ``None``."""
        return self._by_server.get(server)

    def servers(self) -> list[str]:
        """Connected server names, in configuration order."""
        return list(self._by_server)

    def resource_servers(self) -> list[str]:
        """Connected servers that advertise the MCP ``resources`` capability."""
        return [name for name in self._by_server if self._status.get(name, {}).get("resources")]

    def supports_resources(self) -> bool:
        return bool(self.resource_servers())

    def status(self) -> list[dict[str, Any]]:
        """Per-server rows for the settings API: no URLs, headers or secrets."""
        return [dict(row) for row in self._status.values()]

    def _set_status(self, server: str, state: str, *, tools: int = 0, error: str | None = None,
                    resources: bool = False) -> None:
        row: dict[str, Any] = {"name": server, "state": state, "tools": tools, "resources": resources}
        if error:
            row["error"] = error
        self._status[server] = row

    async def start(self, reserved: Optional[set[str]] = None, extra_servers: Optional[dict] = None) -> None:
        """Connect every enabled server and wrap its tools.

        ``extra_servers`` are enabled plugins' servers (already namespaced
        ``<plugin>__<server>``); a name in ``mcp.json`` wins over a plugin's.

        ``reserved`` holds the names already taken (the native tools). A remote
        tool never replaces one of them: on any collision it is advertised
        under its namespaced ``mcp__<server>__<tool>`` name instead.
        """
        reserved = set(reserved or ())
        try:
            cfg = load_mcp_config(self.config_path)
        except (ValueError, OSError):
            logger.warning("Invalid MCP configuration; extra servers are disabled")
            return
        servers = {**(extra_servers or {}), **(cfg.get("mcpServers") or {})}
        if not servers:
            return
        try:
            from fastmcp import Client
        except ImportError:
            logger.warning("fastmcp is not installed; extra MCP servers are disabled")
            for server in servers:
                self._set_status(server, "failed", error="The MCP client library is not installed")
            return
        if self._clients:
            return
        for server, configuration in servers.items():
            client = None
            transport, policy = _server_policy(configuration)
            if policy.get("enabled") is False:
                self._set_status(server, "disabled")
                continue
            startup_timeout = _positive(policy.get("startup_timeout_sec"), DEFAULT_STARTUP_TIMEOUT)
            tool_timeout = _positive(policy.get("tool_timeout_sec"), None)
            try:
                # Independent connections isolate a broken server. A
                # single-server FastMCP connection uses unprefixed remote names.
                # A server that hangs at startup must not hold up the backend.
                client = Client({"mcpServers": {server: transport}})
                await asyncio.wait_for(client.__aenter__(), timeout=startup_timeout)
                listed = await asyncio.wait_for(client.list_tools(), timeout=startup_timeout)
            except Exception as exc:  # noqa: BLE001
                # Transport errors may embed configured URLs or headers.
                logger.warning("Failed to connect/list MCP server %s (%s)", server, type(exc).__name__)
                reason = "startup timed out" if isinstance(exc, asyncio.TimeoutError) else type(exc).__name__
                self._set_status(server, "failed", error=f"Connection failed: {reason}")
                if client is not None:
                    try:
                        await client.__aexit__(None, None, None)
                    except Exception:
                        pass
                continue
            self._clients.append(client)
            self._by_server[server] = client
            before = len(self._tools)
            for spec in listed:
                try:
                    tool = McpProxyTool(client, server, spec, self.max_output_chars, tool_timeout)
                    if not _tool_allowed(tool.remote_name, policy):
                        continue
                    # Retain historical advertised names while keeping exact
                    # server identity; underscores need no reverse inference.
                    tool.name = f"{server}_{tool.remote_name}" if len(servers) > 1 else tool.remote_name
                    taken = reserved | {existing.name for existing in self._tools}
                    if tool.name in taken:
                        tool.name = namespaced_name(server, tool.remote_name)
                    if tool.name in taken:
                        raise ValueError("Duplicate MCP tool name")
                    self._tools.append(tool)
                except Exception:  # noqa: BLE001
                    logger.warning("Ignoring malformed tool metadata from MCP server %s", server)
            self._set_status(server, "connected", tools=len(self._tools) - before,
                             resources=_advertises_resources(client))
        logger.info("MCP hub: %d extra tool(s) from %s", len(self._tools), self.config_path)

    async def stop(self) -> None:
        clients, self._clients = self._clients, []
        self._tools.clear()
        self._by_server.clear()
        self._status.clear()
        for client in reversed(clients):
            try:
                await client.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                logger.debug("MCP hub shutdown failed", exc_info=True)
