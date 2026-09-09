"""
Tool-call router: aliases, fuzzy names, and MCP-schema filtering.

Native MatOpt tools are a small set and stay advertised. Extra MCP tools can
flood the context window, so they are ranked against the user query. Unknown
tool names from the model are resolved to the closest registered tool.
"""

from __future__ import annotations

import difflib
import re
from typing import Optional

from app.agent.skills.router import RoutePlan, tokenize
from app.agent.tools.base import Tool, ToolRegistry

ALIASES = {
    "read": "read_file",
    "write": "write_file",
    "edit": "edit_file",
    "ls": "list_files",
    "list": "list_files",
    "shell": "bash",
    "sh": "bash",
    "terminal": "bash",
    "execute": "python",
    "run": "python",
    "code": "python",
    "repl": "python",
    "search": "grep",
    "find": "grep",
    "plan": "update_plan",
    "todo": "update_plan",
    "skill": "load_skill",
    "skills": "search_skills",
    "wolfram": "wolfram_alpha",
    "equivalent": "check_equivalence",
    "ocr": "parse_document",
    "parse": "parse_document",
    "lambda": "lambda_data_agent",
    "data_agent": "lambda_data_agent",
    "eda": "lambda_data_agent",
    "web": "web_search",
    "websearch": "web_search",
    "search_web": "web_search",
    "fetch": "web_fetch",
    "browse": "web_fetch",
}

CORE = (
    "python",
    "bash",
    "read_file",
    "write_file",
    "edit_file",
    "list_files",
    "grep",
    "load_skill",
    "search_skills",
    "update_plan",
    "parse_document",
    "check_equivalence",
    "lambda_data_agent",
    "web_search",
    "web_fetch",
)

_NORM = re.compile(r"[^a-z0-9]+")


def _norm(name: str) -> str:
    return _NORM.sub("", (name or "").lower())


class ToolRouter:
    def __init__(self, mcp_budget: int = 8):
        self.mcp_budget = mcp_budget

    def resolve(self, registry: ToolRegistry, name: str) -> tuple[Optional[Tool], Optional[str]]:
        """Return (tool, error). Error is a suggestion string when unresolved."""
        if not name:
            return None, "Empty tool name."
        direct = registry.get(name)
        if direct is not None:
            return direct, None
        alias = ALIASES.get(name.lower()) or ALIASES.get(_norm(name))
        if alias and registry.get(alias) is not None:
            return registry.get(alias), None
        target = _norm(name)
        for t in registry:
            if _norm(t.name) == target:
                return t, None
        candidates = list(registry.names())
        close = difflib.get_close_matches(name, candidates, n=3, cutoff=0.62)
        if not close:
            close = difflib.get_close_matches(target, [_norm(n) for n in candidates], n=3, cutoff=0.72)
            inv = {_norm(n): n for n in candidates}
            close = [inv[c] for c in close if c in inv]
        if len(close) == 1:
            return registry.get(close[0]), None
        hint = f"Unknown tool {name!r}."
        if close:
            hint += f" Did you mean {', '.join(close)}?"
        else:
            hint += f" Available: {', '.join(candidates)}."
        return None, hint

    def schemas(
        self,
        registry: ToolRegistry,
        query: str,
        plan: Optional[RoutePlan] = None,
        preferred_tools: Optional[list[str]] = None,
    ) -> list[dict]:
        """JSON schemas to send the model this turn, native tools first."""
        q_tokens = set(tokenize(query or ""))
        preferred: list[str] = list(preferred_tools or [])
        if plan is not None:
            for s in plan.activate:
                preferred.extend(s.allowed_tools)
        preferred_set = set(preferred)

        native: list[Tool] = []
        mcp: list[tuple[float, Tool]] = []
        for t in registry:
            if getattr(t, "mcp_server", None):
                hay = set(tokenize(f"{t.name} {t.description}"))
                inter = q_tokens & hay if q_tokens else set()
                score = (len(inter) / max(len(q_tokens), 1)) if q_tokens else 0.0
                if t.name in preferred_set:
                    score = max(score, 0.8)
                mcp.append((score, t))
            else:
                native.append(t)

        mcp.sort(key=lambda p: (-p[0], p[1].name))
        kept_mcp = [t for sc, t in mcp if sc > 0][: self.mcp_budget]
        if not kept_mcp:
            kept_mcp = [t for _, t in mcp[: min(3, len(mcp))]]

        order = {n: i for i, n in enumerate(CORE)}
        native.sort(key=lambda t: (0 if t.name in preferred_set else 1, order.get(t.name, 50), t.name))
        return [t.schema() for t in native + kept_mcp]
