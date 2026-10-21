"""Internal, capability-negotiated rendering and computation tools."""
from __future__ import annotations

import json
from typing import Any

from app.agent.events import ToolResult, ev
from app.agent.tools.base import Tool
from app.ui.contracts import UINode

_NODE_SCHEMA = UINode.model_json_schema()


def _committed_result(surface: dict) -> ToolResult:
    """Acknowledge the complete render, not merely its text fallback."""
    pending = list(surface["nodes"])
    node_count = 0
    node_types = set()
    while pending:
        node = pending.pop()
        node_count += 1
        node_types.add(node["type"])
        pending.extend(node.get("children") or [])
    receipt = {
        "ok": True, "status": "committed", "surface_id": surface["id"], "revision": surface["revision"],
        "kind": surface["kind"], "node_count": node_count, "node_types": sorted(node_types),
        "result": surface["fallback"], "state": surface["state"],
        "message": "The complete UI definition was validated, saved, and emitted to the client. Continue the answer; no repair is needed.",
    }
    return ToolResult(ok=True, output=json.dumps(receipt, ensure_ascii=False),
                      meta={"ui_surface_id": surface["id"], "ui_revision": surface["revision"]})


class _UITool(Tool):
    requires_flags = ("ui_enabled",)
    concurrent_safe = False
    side_effect_scope = "workspace"
    ui_kind = "ui"

    @staticmethod
    def repair_key(ctx, surface_id):
        sid = surface_id if isinstance(surface_id, str) else "invalid-surface"
        service = ctx.state.get("ui_service")
        prior = service.store.get(ctx.conversation_id, sid) if service else None
        revision = (prior["revision"] + 1) if prior else 1
        return sid, revision, f"{sid}:{revision}"

    async def failure(self, ctx, surface_id, fallback, exc):
        sid, revision, key = self.repair_key(ctx, surface_id)
        failures = ctx.state.setdefault("ui_validation_failures", {})
        count = failures.get(key, 0) + 1
        failures[key] = count
        error = str(exc)[:2000]
        fallback = fallback[:20000] if isinstance(fallback, str) and fallback.strip() else "The interactive view could not be created. A text explanation is still available."
        if ctx.state.get("ui_client_version") == 1:
            await ctx.emit(ev("ui_error", surface_id=sid, revision=revision, error=error, fallback=fallback))
        instruction = "Repair this surface once, retaining its id." if count < 2 else "UI repair limit reached for this surface. Give its fallback as plain text; do not render this surface again this turn."
        return ToolResult(ok=False, error=error + " " + instruction, output=fallback,
                          meta={"ui_repair_managed": True, "ui_surface_id": sid, "ui_revision": revision})

    def available(self, ctx, flag, surface_id=None):
        if ctx.state.get("ui_client_version") != 1 or not ctx.state.get(flag) or not ctx.state.get("ui_service"):
            raise ValueError("Interactive UI was not negotiated for this client")
        if surface_id is not None:
            _, _, key = self.repair_key(ctx, surface_id)
            if ctx.state.get("ui_validation_failures", {}).get(key, 0) >= 2:
                raise ValueError("UI repair limit reached for this surface; answer in plain text")
        return ctx.state["ui_service"]


class RenderUITool(_UITool):
    name = "render_ui"
    requires_flags = ("ui_native",)
    description = (
        "Render a native interactive output only when interaction or structured visual comparison helps. "
        "Use stable id and node ids, title, an informative text fallback, nodes, initial state, and registered action_ids. "
        "Nodes: markdown {text}, equation {latex}, comparison {items:[{label,value,detail?}]}, table {columns,rows}, plot {data,layout}, "
        "mermaid {code}, tabs {label} with children whose props.label is the tab caption; disclosure/stack/grid with children; controls slider/number/select/toggle "
        "{stateKey,label,value,min?,max?,step?,options?}; button {actionId,label}. "
        "A prop value {$state:'key'} binds declared local state; filters/selections cost no model call. "
        "Register server computations before rendering their buttons. Put id/title/fallback before nodes to allow safe streaming previews. "
        "A committed receipt confirms the complete view was accepted; it summarizes the result without echoing all nodes."
    )
    parameters = {
        "type": "object", "$defs": _NODE_SCHEMA.get("$defs", {}),
        "properties": {
            "id": {"type": "string"}, "title": {"type": "string"}, "fallback": {"type": "string"},
            "nodes": {"type": "array", "items": {"$ref": "#/$defs/UINode"}},
            "state": {"type": "object"}, "action_ids": {"type": "array", "items": {"type": "string"}},
        }, "required": ["id", "title", "fallback", "nodes"],
    }

    async def run(self, ctx, id: str, title: str, fallback: str, nodes: list, state: dict | None = None, action_ids: list[str] | None = None, **_: Any):
        try:
            service = self.available(ctx, "ui_native", id)
            actions = []
            for aid in action_ids or []:
                saved = service.store.action(ctx.conversation_id, id, aid)
                if saved is None:
                    raise ValueError(f"Register action {aid} first")
                actions.append(saved[0])
            surface = service.commit(ctx, {"id": id, "kind": "native", "title": title, "fallback": fallback, "nodes": nodes, "state": state or {}, "actions": actions})
            await ctx.emit(ev("ui_commit", surface=surface))
            return _committed_result(surface)
        except (ValueError, TypeError) as exc:
            return await self.failure(ctx, id, fallback, exc)


class CreateUIAppTool(_UITool):
    name = "create_ui_app"
    requires_flags = ("ui_mini_apps",)
    description = (
        "Create a self-contained interactive HTML/CSS/JS mini-app when native nodes cannot express the required simulation. "
        "It runs only after complete validation in an opaque-origin sandbox with no network, cookies, storage, or API access. "
        "Inline all assets; no CDN scripts, external links, downloads, forms or server calls. "
        "Use window.matopt.getState(), window.matopt.onStateChange(callback), window.matopt.setState(partial) for declared local state; "
        "window.matopt.getTheme(), onThemeChange(callback), and resize() are also available; theme tokens are --matopt-bg, --matopt-text, --matopt-muted, --matopt-border, and --matopt-control CSS custom properties. Use a blob Web Worker for expensive local computation, falling back to the main thread on the worker's error event (external MCP Apps hosts may block blob: workers). "
        "Declare server action_ids for host-owned computation buttons outside the frame; never execute server code from the frame. "
        "Always provide a meaningful fallback, units, assumptions, and initial state. "
        "A committed receipt confirms the complete app was accepted; it summarizes the result without echoing the HTML."
    )
    parameters = {"type": "object", "properties": {
        "id": {"type": "string"}, "title": {"type": "string"}, "fallback": {"type": "string"},
        "html": {"type": "string"}, "state": {"type": "object"},
        "action_ids": {"type": "array", "items": {"type": "string"}},
    }, "required": ["id", "title", "fallback", "html"]}

    async def run(self, ctx, id: str, title: str, fallback: str, html: str, state: dict | None = None, action_ids: list[str] | None = None, **_: Any):
        try:
            service = self.available(ctx, "ui_mini_apps", id)
            actions = []
            for aid in action_ids or []:
                saved = service.store.action(ctx.conversation_id, id, aid)
                if saved is None:
                    raise ValueError(f"Register action {aid} first")
                actions.append(saved[0])
            surface = service.commit(ctx, {"id": id, "kind": "mini_app", "title": title, "fallback": fallback, "app": {"html": html}, "state": state or {}, "actions": actions})
            await ctx.emit(ev("ui_commit", surface=surface))
            return _committed_result(surface)
        except (ValueError, TypeError) as exc:
            return await self.failure(ctx, id, fallback, exc)


class RegisterUIActionTool(_UITool):
    name = "register_ui_action"
    description = (
        "Register a server-owned Python computation for an interactive surface. Code must define a self-contained "
        "compute(values) function returning a JSON object containing nodes, state, fallback and/or title. "
        "Import dependencies inside this definition/module and reload declared input_files; do not depend on previous kernel variables. "
        "Inputs must match a self-contained JSON Schema of type object. Return finite JSON values. "
        "The browser sends action_id and values only; code runs in the existing conversation kernel with timeout, cancellation "
        "and a reproducible seed. Register before render_ui/create_ui_app and use the same surface_id."
    )
    parameters = {"type": "object", "properties": {
        "surface_id": {"type": "string"}, "action_id": {"type": "string"}, "label": {"type": "string"},
        "description": {"type": "string"}, "input_schema": {"type": "object"}, "code": {"type": "string"},
        "timeout": {"type": "integer", "minimum": 1, "maximum": 900},
        "input_files": {"type": "array", "items": {"type": "string"}}, "seed": {"type": "integer", "minimum": 0, "maximum": 4294967295},
    }, "required": ["surface_id", "action_id", "label", "input_schema", "code"]}

    async def run(self, ctx, surface_id: str, action_id: str, label: str, input_schema: dict, code: str,
                  description: str | None = None, timeout: int = 60, input_files: list | None = None, seed: int = 0, **_: Any):
        service = self.available(ctx, "ui_enabled")
        if type(seed) is not int or not 0 <= seed <= 4294967295:
            return ToolResult(ok=False, error="Seed must be an integer between 0 and 4294967295")
        manifest = service.register(ctx, surface_id, action_id, label, input_schema, code, description, timeout, input_files, seed)
        return ToolResult(ok=True, output=json.dumps(manifest, ensure_ascii=False), meta={"ui_surface_id": surface_id, "ui_action_id": action_id, "handler_version": manifest["handler_version"]})
