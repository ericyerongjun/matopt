"""``load_skill`` / ``search_skills`` — progressive disclosure for the skill space."""

from __future__ import annotations

from typing import Any, Optional

from app.agent.events import ToolResult
from app.agent.skills.loader import SkillRegistry
from app.agent.skills.materialize import materialize_skill
from app.agent.skills.router import SkillRouter
from app.agent.tools.base import Tool, ToolContext


class SearchSkillsTool(Tool):
    name = "search_skills"
    read_only = True
    ui_kind = "file"
    description = (
        "Search the skill catalog by query. Returns ranked skill names, scores, and descriptions. "
        "Call this when the task might match a specialised playbook that is not already ACTIVE, "
        "then `load_skill` on the best match before doing the work."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What you need a skill for, in plain language."},
            "limit": {"type": "integer", "description": "Maximum hits (default 8, max 20)."},
        },
        "required": ["query"],
    }

    def __init__(self, registry: SkillRegistry, router: Optional[SkillRouter] = None):
        self.registry = registry
        self.router = router or SkillRouter(registry)

    async def run(self, ctx: ToolContext, query: str, limit: int = 8, **_: Any) -> ToolResult:
        hits = self.router.search(query, limit=max(1, min(int(limit or 8), 20)))
        if not hits:
            return ToolResult(ok=True, output="No skills are registered.")
        lines = [f"{r.score:.2f}  {r.skill.name}  ({r.skill.source})  {r.skill.description.replace(chr(10), ' ')}" for r in hits]
        return ToolResult(ok=True, output="\n".join(lines), meta={"names": [r.skill.name for r in hits]})


class LoadSkillTool(Tool):
    name = "load_skill"
    ui_kind = "file"
    description = (
        "Activate a skill by name: inject its SKILL.md instructions and copy its files into "
        "`skills/<name>/` in the workspace so scripts can be run with python/bash. "
        "Always load a relevant skill before specialised document, MCP, or authoring work."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill name, e.g. pdf, xlsx, sympy-compute."},
        },
        "required": ["name"],
    }

    def __init__(self, registry: SkillRegistry, router: Optional[SkillRouter] = None):
        self.registry = registry
        self.router = router or SkillRouter(registry)

    async def run(self, ctx: ToolContext, name: str, **_: Any) -> ToolResult:
        key = (name or "").strip()
        skill = self.registry.get(key)
        if skill is None:
            hits = self.router.search(key, limit=5)
            suggest = ", ".join(r.skill.name for r in hits) or ", ".join(self.registry.names()[:8])
            return ToolResult(ok=False, output="", error=f"Unknown skill {key!r}. Closest: {suggest}")
        dest = materialize_skill(skill, ctx.workspace)
        loaded = list(ctx.state.setdefault("skills", []))
        if skill.name not in loaded:
            loaded.append(skill.name)
            ctx.state["skills"] = loaded
        files = "\n".join(f"- skills/{skill.name}/{f}" for f in skill.files[:40]) or "(no bundled files)"
        body = skill.body
        if len(body) > 14_000:
            body = body[:12_000] + "\n\n… [truncated; read skills/" + skill.name + "/SKILL.md for the rest]"
        text = (
            f"Loaded skill `{skill.name}` ({skill.source}). Files copied to `skills/{skill.name}/`.\n\n"
            f"Bundled files:\n{files}\n\n"
            f"# {skill.name}\n\n{body}"
        )
        return ToolResult(
            ok=True,
            output=text,
            artifacts=[ctx.workspace.artifact_for(dest / "SKILL.md")],
            meta={"name": skill.name, "path": f"skills/{skill.name}", "files": skill.files},
        )
