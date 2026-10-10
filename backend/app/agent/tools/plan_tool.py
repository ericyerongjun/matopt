"""``update_plan`` tool: a lightweight todo list shown as a checklist in the UI."""

from __future__ import annotations

from typing import Any

from app.agent.events import Event, ToolResult
from app.agent.tools.base import Tool, ToolContext

STATUSES = ("pending", "in_progress", "completed")


class UpdatePlanTool(Tool):
    name = "update_plan"
    # Mutates only this turn's agent state (`ctx.state["plan"]`) plus the `plan`
    # event that renders it — nothing on disk and nothing on the host, hence
    # scope "none" even though it is not read-only: the plan is shared mutable
    # state, so two concurrent calls would interleave last-write-wins.
    read_only = False
    destructive = False
    concurrent_safe = False
    side_effect_scope = "none"
    risk_level = "low"
    needs_approval = False
    ui_kind = "plan"
    description = (
        "Create or update the step-by-step plan for a multi-step task (5+ meaningful steps). "
        "This plan is visible to the user: use brief task milestones, never private reasoning or agent messages. "
        "Send the full list each time; mark steps in_progress/completed as you go. Skip it for simple questions."
    )
    parameters = {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "status": {"type": "string", "enum": list(STATUSES)},
                    },
                    "required": ["content", "status"],
                },
            }
        },
        "required": ["steps"],
    }

    async def run(self, ctx: ToolContext, steps: list | None = None, **_: Any) -> ToolResult:
        normalized = []
        for item in steps or []:
            if isinstance(item, str):
                content, status = item.strip(), "pending"
            elif isinstance(item, dict):
                content = str(item.get("content") or item.get("task") or "").strip()
                status = str(item.get("status") or "pending")
            else:
                continue
            if not content:
                continue
            if status not in STATUSES:
                status = "pending"
            normalized.append({"content": content, "status": status})
        ctx.state["plan"] = normalized
        await ctx.emit(Event("plan", {"steps": normalized}))
        if not normalized:
            return ToolResult(ok=True, output="Plan cleared.", meta={"steps": []})
        marks = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
        text = "\n".join(f"{marks[s['status']]} {i}. {s['content']}" for i, s in enumerate(normalized, 1))
        return ToolResult(ok=True, output=text, meta={"steps": normalized})
