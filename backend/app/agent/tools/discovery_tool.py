"""Progressive tool metadata search and explicit, per-run schema activation."""

from __future__ import annotations

import json
from typing import Any

from app.agent.events import ToolResult
from app.agent.tools.base import Tool, ToolContext, ToolRegistry
from app.agent.tools.router import ToolRouter


class SearchToolsTool(Tool):
    name = "search_tools"
    description = (
        "Find available tools by capability, service or exact ID. Returns ranked metadata, not execution results. "
        "Use this when the available tools do not cover the next step, or your task changes. "
        "Then call load_tool_schemas with selected exact names to learn their parameters before invoking them."
    )
    parameters = {"type": "object", "properties": {
        "query": {"type": "string", "description": "Needed capability and service in plain language (maximum 2000 characters)."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
    }, "required": ["query"], "additionalProperties": False}
    read_only = True
    concurrent_safe = True
    side_effect_scope = "none"

    def __init__(self, registry: ToolRegistry, router: ToolRouter | None = None):
        self.registry = registry
        self.router = router or ToolRouter()

    async def run(self, ctx: ToolContext, query: str, limit: int = 8, **_: Any) -> ToolResult:
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            return ToolResult(ok=False, error="query must be nonempty text of at most 2000 characters.")
        if type(limit) is not int or not 1 <= limit <= 20:
            return ToolResult(ok=False, error="limit must be an integer from 1 to 20.")
        hits = self.router.search(self.registry, query, limit=limit, ctx=ctx)
        output = {"tools": [hit.as_dict() for hit in hits],
                  "next_step": "Call load_tool_schemas with exact names before invocation." if hits
                  else "No relevant tools found. Refine the capability or service name; nothing was activated."}
        return ToolResult(ok=True, output=json.dumps(output, ensure_ascii=False),
                          meta={"names": [hit.name for hit in hits]})


class LoadToolSchemasTool(Tool):
    name = "load_tool_schemas"
    description = (
        "Load parameter schemas for exact registered tool names returned by search_tools. "
        "These tools become available on the next model step for the rest of the run. "
        "Loading does not execute a tool, enable disabled capabilities, or authorize a side effect."
    )
    parameters = {"type": "object", "properties": {
        "names": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20,
                  "description": "Exact registered IDs; existing explicit native aliases are supported."},
    }, "required": ["names"], "additionalProperties": False}
    # Mutates this run's available capabilities, so concurrent scheduling must
    # not treat activation as a pure read even though no workspace file changes.
    read_only = False
    concurrent_safe = False
    side_effect_scope = "none"

    def __init__(self, registry: ToolRegistry, router: ToolRouter | None = None):
        self.registry = registry
        self.router = router or ToolRouter()

    async def run(self, ctx: ToolContext, names: list[str], **_: Any) -> ToolResult:
        if not isinstance(names, list) or not 1 <= len(names) <= 20 or any(not isinstance(name, str) or not name for name in names):
            return ToolResult(ok=False, error="names must contain 1 to 20 exact registered tool IDs.")
        try:
            schemas = self.router.load_schemas(self.registry, names, ctx=ctx)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        canonical = [schema["function"]["name"] for schema in schemas]
        loaded = list(ctx.state.get("loaded_tools") or [])
        for name in canonical:
            if name not in loaded:
                loaded.append(name)
        ctx.state["loaded_tools"] = loaded
        return ToolResult(ok=True, output=json.dumps({"schemas": schemas, "loaded_names": canonical}, ensure_ascii=False),
                          meta={"names": canonical, "refresh_tools": True})
