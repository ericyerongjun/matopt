"""Exact tool dispatch and bounded, metadata-only discovery.

Retrieval proposes names; only exact registered IDs or the documented aliases
can execute. Parameter schemas are read only for the tools being advertised or
explicitly loaded. Discovery never changes capability/visibility gates.
"""

from __future__ import annotations

import difflib
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from app.agent.skills.router import RoutePlan, tokenize
from app.agent.tools.base import Tool, ToolContext, ToolRegistry

ALIASES = {
    "read": "read_file", "write": "write_file", "edit": "edit_file",
    "ls": "list_files", "list": "list_files", "shell": "bash", "sh": "bash",
    "terminal": "bash", "execute": "python", "run": "python", "code": "python",
    "repl": "python", "search": "grep", "find": "grep", "plan": "update_plan",
    "todo": "update_plan", "skill": "load_skill", "skills": "search_skills",
    "wolfram": "wolfram_alpha", "equivalent": "check_equivalence",
    "ocr": "parse_document", "parse": "parse_document", "lambda": "lambda_data_agent",
    "data_agent": "lambda_data_agent", "eda": "lambda_data_agent", "web": "web_search",
    "websearch": "web_search", "search_web": "web_search", "fetch": "web_fetch",
    "browse": "web_fetch",
}

CORE = (
    "python", "bash", "read_file", "write_file", "edit_file", "list_files", "grep",
    "load_skill", "search_skills", "search_tools", "load_tool_schemas", "update_plan",
    "parse_document", "check_equivalence", "lambda_data_agent", "web_search", "web_fetch",
)

# Deliberately small, auditable vocabulary expansion. It improves recall but is
# never used to resolve a call. Unseen paraphrases still require model discovery.
_SYNONYMS = (
    ("search", "find", "lookup", "locate"),
    ("create", "add", "new"),
    ("update", "edit", "modify", "change"),
    ("delete", "remove", "trash"),
    ("read", "get", "retrieve", "fetch"),
    ("email", "mail", "inbox"),
    ("calendar", "schedule", "appointment"),
    ("spreadsheet", "workbook", "excel"),
    ("document", "doc"),
    ("image", "picture", "photo"),
    ("permission", "access", "sharing"),
    ("repository", "repo"),
    ("issue", "ticket"),
    ("chat", "conversation"),
)
_EXPANSIONS = {word: set(group) - {word} for group in _SYNONYMS for word in group}
_CAMEL = re.compile(r"([a-z])([A-Z])")


def _terms(text: str) -> list[str]:
    words = tokenize(_CAMEL.sub(r"\1 \2", text))
    return [word[:-3] + "y" if len(word) > 4 and word.endswith("ies")
            else word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us"))
            else word for word in words]


@dataclass(frozen=True)
class ToolMatch:
    name: str
    description: str
    server: str | None
    read_only: bool
    risk_level: str
    score: float

    def as_dict(self) -> dict:
        return {"name": self.name, "description": self.description, "server": self.server,
                "read_only": self.read_only, "risk_level": self.risk_level, "score": round(self.score, 4)}


class ToolRouter:
    def __init__(self, mcp_budget: int = 8, small_catalog_limit: int = 32):
        self.mcp_budget = max(0, int(mcp_budget))
        self.small_catalog_limit = max(0, int(small_catalog_limit))
        self._index_key: tuple = ()
        self._documents: list[tuple[Tool, Counter]] = []
        self._frequency: Counter = Counter()
        self._average_length = 1.0

    def resolve(self, registry: ToolRegistry, name: str) -> tuple[Optional[Tool], Optional[str]]:
        """Resolve exact IDs or explicit aliases; fuzzy matches are suggestions only."""
        if not isinstance(name, str) or not name:
            return None, "Empty tool name."
        direct = registry.get(name)
        if direct is not None:
            return direct, None
        alias = ALIASES.get(name.lower())
        if alias and registry.get(alias) is not None:
            return registry.get(alias), None
        candidates = registry.names()
        close = difflib.get_close_matches(name, candidates, n=3, cutoff=0.62)
        hint = f"Unknown tool {name!r}. No tool was executed."
        if close:
            hint += f" Did you mean {', '.join(close)}? Use the exact registered ID."
        else:
            hint += f" Search with search_tools. Available examples: {', '.join(candidates[:8])}."
        return None, hint

    @staticmethod
    def _visible(registry: ToolRegistry, ctx: ToolContext | None = None) -> list[Tool]:
        if ctx is not None:
            return [tool for tool in registry if tool.is_provider_visible(ctx)]
        return [tool for tool in registry if tool.provider_visible]

    def _index(self, tools: list[Tool]) -> None:
        # Detect replacement/metadata edits without asking every tool for its
        # parameter schema. One catalog is cached to bound per-process memory.
        key = tuple((id(tool), tool.name, tool.description, getattr(tool, "mcp_server", ""),
                     tuple(getattr(tool, "discovery_keywords", ()))) for tool in tools)
        if key == self._index_key:
            return
        documents: list[tuple[Tool, Counter]] = []
        frequency: Counter = Counter()
        for tool in tools:
            name = _terms(tool.name)
            terms = Counter(name * 3 + _terms(tool.description[:8000])
                            + _terms(str(getattr(tool, "mcp_server", "")))
                            + _terms(" ".join(getattr(tool, "discovery_keywords", ()))))
            documents.append((tool, terms))
            frequency.update(terms.keys())
        self._index_key, self._documents, self._frequency = key, documents, frequency
        self._average_length = sum(sum(terms.values()) for _, terms in documents) / max(1, len(documents)) or 1.0

    def search(self, registry: ToolRegistry, query: str, limit: int = 8,
               *, ctx: ToolContext | None = None) -> list[ToolMatch]:
        """BM25 over names/descriptions/server/optional keywords, without schemas.

        Positive matches only: no alphabetical fallback claims relevance when
        there is no evidence. Callers may refine the query or use exact IDs.
        """
        self._index(self._visible(registry, ctx))
        query_text = (query or "")[:2000]
        query_terms = set(_terms(query_text))
        mentioned_ids = set(re.findall(r"[\w.-]+", query_text.lower()))
        if not query_terms:
            return []
        weights = {term: 1.0 for term in query_terms}
        for term in query_terms:
            for alternative in _EXPANSIONS.get(term, ()):
                weights.setdefault(alternative, 0.35)
        count = len(self._documents)
        matches = []
        for tool, terms in self._documents:
            length = sum(terms.values())
            score = 0.0
            for term, weight in weights.items():
                tf = terms.get(term, 0)
                if not tf:
                    continue
                df = self._frequency[term]
                idf = math.log(1 + (count - df + 0.5) / (df + 0.5))
                score += weight * idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / self._average_length))
            if tool.name.lower() in mentioned_ids:
                score += 10
            if score > 0:
                matches.append(ToolMatch(tool.name, tool.description[:500], getattr(tool, "mcp_server", None),
                                         bool(tool.read_only), tool.risk_level, score))
        matches.sort(key=lambda match: (-match.score, match.name))
        return matches[:max(1, min(int(limit), 20))]

    def load_schemas(self, registry: ToolRegistry, names: list[str], *, ctx: ToolContext | None = None) -> list[dict]:
        """Load selected schemas atomically; reject unknown or unavailable names.

        This API does not execute a tool or grant permissions. The caller owns
        per-run activation, and should add returned canonical names to its
        loaded-tools list before refreshing the provider's schemas.
        """
        visible = {tool.name for tool in self._visible(registry, ctx)}
        selected: dict[str, Tool] = {}
        for name in names:
            tool, error = self.resolve(registry, name)
            if tool is None:
                raise ValueError(error)
            if tool.name not in visible:
                raise ValueError(f"Tool {tool.name!r} is unavailable for this turn.")
            selected[tool.name] = tool
        return [tool.schema() for tool in selected.values()]

    def schemas(self, registry: ToolRegistry, query: str, plan: Optional[RoutePlan] = None,
                preferred_tools: Optional[list[str]] = None, *, loaded_tools: Optional[list[str]] = None,
                ctx: ToolContext | None = None) -> list[dict]:
        """Native core plus retrieval; selected tools are never evicted by ranking."""
        visible = self._visible(registry, ctx)
        preferred = list(preferred_tools or []) + list(loaded_tools or [])
        if plan is not None:
            preferred.extend(name for skill in plan.activate for name in skill.allowed_tools)
        preferred_set: set[str] = set()
        for name in preferred:
            # Explicit aliases remain compatible; unknown selections cannot
            # silently become a different tool via fuzzy matching.
            tool = registry.get(name) or registry.get(ALIASES.get(name.lower(), ""))
            if tool is not None:
                preferred_set.add(tool.name)
        order = {name: index for index, name in enumerate(CORE)}
        native = sorted((tool for tool in visible if not getattr(tool, "mcp_server", None)),
                        key=lambda tool: (tool.name not in preferred_set, order.get(tool.name, 50), tool.name))
        remote = {tool.name: tool for tool in visible if getattr(tool, "mcp_server", None)}
        if len(visible) <= self.small_catalog_limit:
            selected = sorted(remote, key=lambda name: (name not in preferred_set, name))
        else:
            # Rank remote tools separately so native matches cannot consume the
            # remote shortlist. Pins are additional to, not inside, the budget.
            selected = sorted(name for name in remote if name in preferred_set)
            if self.mcp_budget:
                ranked = self.search(ToolRegistry([tool for name, tool in remote.items() if name not in preferred_set]),
                                     query, limit=min(20, self.mcp_budget), ctx=ctx)
                selected.extend(match.name for match in ranked)
        return [tool.schema() for tool in native + [remote[name] for name in selected]]
