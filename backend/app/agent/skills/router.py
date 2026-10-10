"""
Lexical skill router.

The model is a poor skill picker on its own (it under-triggers). This router
scores every skill against the user turn *before* the LLM runs, using:

- exact / hyphenless name hits
- file-extension overlap from the query, attachments, and workspace
- token overlap with the description (minus a "do not trigger" clause)
- a small synonym table for MatOpt domain skills

High-confidence matches are auto-activated (SKILL.md injected, folder copied
into the workspace). Everything else stays a one-line catalog entry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from app.agent.skills.loader import Skill, SkillRegistry

TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[a-z0-9]+)?", re.I)
EXT_RE = re.compile(r"\.(pdf|docx?|dotx|pptx?|potx|xlsx?|xlsm|csv|tsv|gif|png|jpe?g|html?)\b", re.I)
NEGATIVE_RE = re.compile(r"do not (?:trigger|use)\b.*", re.I | re.S)
DELEGATION_RE = re.compile(
    r"\b(?:(?:run|use|call|ask|invoke)\s+(?:the\s+)?lambda(?![-\w])|run the data agent|"
    r"autonomous exploration|full analysis pipeline)\b", re.I,
)
STOP = {
    "the", "a", "an", "to", "of", "and", "or", "for", "with", "when", "use", "this", "that",
    "skill", "skills", "any", "from", "into", "your", "user", "using", "should", "than",
    "other", "also", "even", "just", "over", "onto", "about", "after", "before",
}

# Extra cues that descriptions may not spell out as a file extension.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "xlsx": ("spreadsheet", "excel", "workbook", "tabular"),
    "pptx": ("slides", "deck", "presentation", "powerpoint"),
    "docx": ("word", "memo", "letterhead", "letter"),
    "pdf": ("pdf", "form field", "watermark"),
    "sympy-compute": ("symbolic", "simplify", "integral", "derivative", "antiderivative", "ode", "closed form", "factor", "sympy"),
    "statistical-inference": ("p-value", "pvalue", "anova", "bootstrap", "confidence interval", "hypothesis", "glm", "t-test", "ttest", "a/b"),
    "numerical-optimization": ("minimize", "minimise", "maximize", "optimise", "optimize", "l-bfgs", "bfgs", "least squares", "curve_fit"),
    "ml-experiment": ("sklearn", "classifier", "cross-validation", "pipeline", "roc", "random forest", "k-means"),
    "math-verification": ("verify", "equivalence", "sanity check", "boxed", "identity"),
    "lambda-data-agent": ("lambda", "autonomous exploration", "data agent", "lambda agent"),
    "lambda-eda": ("dataset", "dataframe", "eda", "exploratory", "csv", "parquet", "pandas profiling", "explore the table"),
    "lambda-report": ("data report", "latex report", "analysis pdf", "eda report", "analysis report"),
    "lambda-slides": ("analysis slides", "data deck", "insight slides", "eda slides"),
    "lambda-notebook": ("analysis notebook", "ipynb", "jupyter notebook", "export notebook"),
    "mcp-builder": ("mcp", "model context protocol", "fastmcp"),
    "skill-creator": ("create a skill", "author a skill", "skill.md"),
    "frontend-design": ("ui", "typography", "landing page"),
    "webapp-testing": ("playwright", "browser test"),
}

EXT_TO_SKILLS: dict[str, tuple[str, ...]] = {
    ".pdf": ("pdf",),
    ".docx": ("docx",),
    ".doc": ("docx",),
    ".dotx": ("docx",),
    ".pptx": ("pptx",),
    ".ppt": ("pptx",),
    ".potx": ("pptx",),
    ".xlsx": ("xlsx",),
    ".xls": ("xlsx",),
    ".xlsm": ("xlsx",),
    # A table attachment can suggest an EDA playbook, but is not a request to
    # start a delegated analysis pipeline. That requires task/explicit cues.
    ".csv": ("lambda-eda", "statistical-inference"),
    ".tsv": ("lambda-eda", "statistical-inference"),
    ".parquet": ("lambda-eda",),
    ".gif": ("slack-gif-creator",),
}


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text or "") if t.lower() not in STOP and len(t) > 1]


def _split_negative(description: str) -> tuple[str, str]:
    m = NEGATIVE_RE.search(description or "")
    if not m:
        return description or "", ""
    return (description or "")[: m.start()], m.group(0)


@dataclass
class ScoredSkill:
    skill: Skill
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class RoutePlan:
    activate: list[Skill] = field(default_factory=list)
    ranked: list[ScoredSkill] = field(default_factory=list)
    catalog_names: list[str] = field(default_factory=list)

    def as_prompt(self, catalog_limit: int = 16) -> tuple[str, str]:
        """Return (catalog markdown, concatenated active bodies)."""
        shown = self.catalog_names[:catalog_limit]
        extra = max(0, len(self.catalog_names) - catalog_limit)
        lines = []
        active_names = {s.name for s in self.activate}
        for n in shown:
            mark = " ACTIVE" if n in active_names else ""
            hit = next((r for r in self.ranked if r.skill.name == n), None)
            desc = (hit.skill.description if hit else "").replace("\n", " ")
            lines.append(f"- `{n}`{mark}: {desc}")
        if extra:
            lines.append(f"- … {extra} more; call `search_skills` to find them.")
        catalog = "\n".join(lines)
        bodies = []
        for s in self.activate:
            bodies.append(f"### Skill `{s.name}`\n\n{s.body}")
        return catalog, "\n\n".join(bodies)


class SkillRouter:
    def __init__(
        self,
        registry: SkillRegistry,
        auto_activate_max: int = 2,
        auto_threshold: float = 0.38,
        margin: float = 0.08,
    ):
        self.registry = registry
        self.auto_activate_max = auto_activate_max
        self.auto_threshold = auto_threshold
        self.margin = margin

    def score_one(self, skill: Skill, query: str, file_exts: set[str]) -> ScoredSkill:
        q = query or ""
        q_low = q.lower()
        q_tokens = set(tokenize(q))
        compact_q = re.sub(r"[^a-z0-9]+", "", q_low)
        name = skill.name
        compact_name = name.replace("-", "")
        positive, negative = _split_negative(skill.description)
        pos_tokens = set(tokenize(positive))
        reasons: list[str] = []
        score = 0.0

        if name in q_low or name.replace("-", " ") in q_low:
            return ScoredSkill(skill, 1.0, ["name-in-query"])
        if name == "lambda-data-agent" and DELEGATION_RE.search(q):
            # A requested delegated workflow should beat incidental workspace
            # extensions, while a table alone must not activate the full loop.
            return ScoredSkill(skill, 1.0, ["delegated-workflow-request"])
        if compact_name and compact_name in compact_q and len(compact_name) >= 4:
            score = max(score, 0.92)
            reasons.append("compact-name")

        query_exts = {m.group(0).lower() for m in EXT_RE.finditer(q)}
        query_exts |= file_exts
        for ext in query_exts:
            mapped = EXT_TO_SKILLS.get(ext, ())
            if name in mapped:
                score = max(score, 0.72)
                reasons.append(f"ext:{ext}")
            elif ext.lstrip(".") and ext.lstrip(".") in name:
                score = max(score, 0.7)
                reasons.append(f"ext-name:{ext}")

        if q_tokens and pos_tokens:
            inter = q_tokens & pos_tokens
            if inter:
                overlap = 2 * len(inter) / (len(q_tokens) + len(pos_tokens))
                precision = len(inter) / max(len(q_tokens), 1)
                bump = min(0.65, 0.35 * precision + 0.5 * overlap)
                if bump > 0:
                    score += bump
                    reasons.append(f"tokens:{','.join(sorted(inter)[:6])}")

        for cue in SYNONYMS.get(name, ()):
            if cue in q_low:
                score = min(1.0, score + 0.22)
                reasons.append(f"syn:{cue}")

        if negative:
            neg_only = set(tokenize(negative)) - pos_tokens
            if len(q_tokens & neg_only) >= 2:
                score *= 0.25
                reasons.append("negative-clause")

        return ScoredSkill(skill, float(min(1.0, score)), reasons)

    def rank(self, query: str, file_exts: Optional[set[str]] = None) -> list[ScoredSkill]:
        exts = file_exts or set()
        scored = [self.score_one(s, query, exts) for s in self.registry]
        scored.sort(key=lambda r: (-r.score, r.skill.name))
        return scored

    def plan(
        self,
        query: str,
        attachments: Optional[Iterable[dict]] = None,
        files: Optional[Iterable[dict | str]] = None,
    ) -> RoutePlan:
        file_exts: set[str] = set()
        for a in attachments or []:
            name = str(a.get("path") or a.get("name") or "")
            file_exts.add(Path(name).suffix.lower())
        for f in files or []:
            path = f if isinstance(f, str) else str(f.get("path") or f.get("name") or "")
            file_exts.add(Path(path).suffix.lower())
        file_exts.discard("")
        ranked = self.rank(query, file_exts)
        activate: list[Skill] = []
        if ranked:
            top = ranked[0]
            second = ranked[1].score if len(ranked) > 1 else 0.0
            if top.score >= self.auto_threshold or (top.score >= 0.28 and top.score - second >= self.margin):
                activate.append(top.skill)
            for r in ranked[1 : self.auto_activate_max + 2]:
                if len(activate) >= self.auto_activate_max:
                    break
                if r.score >= self.auto_threshold and r.score >= (top.score if activate else 0) - 0.15:
                    activate.append(r.skill)
        catalog = [r.skill.name for r in ranked]
        return RoutePlan(activate=activate, ranked=ranked, catalog_names=catalog)

    def search(self, query: str, limit: int = 8) -> list[ScoredSkill]:
        ranked = self.rank(query)
        hits = [r for r in ranked if r.score > 0][:limit]
        return hits or ranked[:limit]
