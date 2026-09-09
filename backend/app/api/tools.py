"""GET /api/tools — slash-menu catalog of callable tools."""

from __future__ import annotations

from fastapi import APIRouter

from app.runtime import get_runtime

router = APIRouter(prefix="/api/tools", tags=["tools"])

# Agent-internal: users invoke playbooks with /skill-name instead.
HIDDEN = {"load_skill", "search_skills"}


@router.get("")
async def list_tools():
    rt = get_runtime()
    tools = []
    for t in rt.tools:
        if t.name in HIDDEN:
            continue
        tools.append({
            "name": t.name,
            "description": (t.description or "").replace("\n", " ").strip(),
            "kind": getattr(t, "ui_kind", "generic"),
            "source": getattr(t, "mcp_server", None) or "matopt",
        })
    return {"tools": tools}
