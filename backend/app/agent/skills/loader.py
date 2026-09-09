"""Parse Agent Skills folders (https://agentskills.io/specification)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import yaml

logger = logging.getLogger(__name__)

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    source: str = "local"
    license: Optional[str] = None
    compatibility: Optional[str] = None
    metadata: dict[str, str] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)

    @property
    def skill_md(self) -> Path:
        return self.path / "SKILL.md"


def parse_skill_md(text: str) -> tuple[dict, str]:
    """Return (frontmatter dict, markdown body). Raises ValueError on bad YAML."""
    m = FRONTMATTER_RE.match(text or "")
    if not m:
        raise ValueError("SKILL.md must start with YAML frontmatter delimited by ---")
    meta = yaml.safe_load(m.group(1)) or {}
    if not isinstance(meta, dict):
        raise ValueError("SKILL.md frontmatter must be a mapping")
    return meta, (m.group(2) or "").strip()


def _as_str_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items() if v is not None}


def _allowed_tools(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [p for p in str(raw).split() if p]


def _bundle_files(root: Path) -> list[str]:
    out: list[str] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.name == "SKILL.md" or p.name.startswith(".") or "__pycache__" in p.parts:
            continue
        out.append(str(p.relative_to(root)).replace("\\", "/"))
        if len(out) >= 80:
            break
    return out


def load_skill_dir(path: Path, source: str = "local") -> Skill:
    md = path / "SKILL.md"
    if not md.is_file():
        raise ValueError(f"No SKILL.md in {path}")
    meta, body = parse_skill_md(md.read_text(encoding="utf-8"))
    name = str(meta.get("name") or path.name).strip()
    if not NAME_RE.match(name) or len(name) > MAX_NAME:
        raise ValueError(f"Invalid skill name {name!r} in {path}")
    if name != path.name:
        logger.warning("Skill name %r does not match directory %s; using directory name", name, path.name)
        name = path.name
    description = str(meta.get("description") or "").strip()
    if not description:
        raise ValueError(f"Skill {name} is missing a description")
    if len(description) > MAX_DESCRIPTION:
        description = description[: MAX_DESCRIPTION - 1].rstrip() + "…"
    metadata = _as_str_map(meta.get("metadata"))
    return Skill(
        name=name,
        description=description,
        body=body,
        path=path.resolve(),
        source=source,
        license=str(meta["license"]).strip() if meta.get("license") else None,
        compatibility=str(meta["compatibility"]).strip() if meta.get("compatibility") else None,
        metadata=metadata,
        allowed_tools=_allowed_tools(meta.get("allowed-tools") or meta.get("allowed_tools")),
        files=_bundle_files(path),
    )


class SkillRegistry:
    """All discoverable skills, keyed by name. Later directories win on conflict."""

    def __init__(self, skills: Optional[Iterable[Skill]] = None):
        self._skills: dict[str, Skill] = {}
        for s in skills or []:
            self.add(s)

    def add(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return sorted(self._skills)

    def all(self) -> list[Skill]:
        return [self._skills[n] for n in self.names()]

    def __len__(self) -> int:
        return len(self._skills)

    def __iter__(self):
        return iter(self.all())

    def catalog_lines(self, names: Optional[Iterable[str]] = None) -> str:
        chosen = list(names) if names is not None else self.names()
        lines = []
        for n in chosen:
            s = self._skills.get(n)
            if s is None:
                continue
            desc = s.description.replace("\n", " ")
            lines.append(f"- `{s.name}` ({s.source}): {desc}")
        return "\n".join(lines)

    @classmethod
    def discover(cls, directories: Iterable[Path]) -> "SkillRegistry":
        reg = cls()
        for directory in directories:
            d = Path(directory)
            if not d.is_dir():
                continue
            source = _source_label(d)
            for child in sorted(p for p in d.iterdir() if p.is_dir()):
                if child.name.startswith(".") or not (child / "SKILL.md").is_file():
                    continue
                try:
                    reg.add(load_skill_dir(child, source=source))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Skipping skill at %s: %s", child, exc)
        return reg


def _source_label(directory: Path) -> str:
    name = directory.name.lower()
    if name == "anthropic":
        return "anthropic"
    if name == "matopt":
        return "matopt"
    if name == "lambda":
        return "lambda"
    return name
