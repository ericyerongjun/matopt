"""Parse leading ``/skill-name`` tokens the way Cursor/Claude slash commands work."""

from __future__ import annotations

import re
from typing import Iterable

SLASH_RE = re.compile(r"^/([a-z0-9]+(?:-[a-z0-9]+)*)(?:\s+|$)")


def split_slash_skills(text: str, known: Iterable[str]) -> tuple[str, list[str]]:
    """Strip known ``/skill`` prefixes. Unknown tokens are left in the message."""
    names: list[str] = []
    rest = (text or "").lstrip()
    catalog = {n.lower(): n for n in known}
    while True:
        m = SLASH_RE.match(rest)
        if not m:
            break
        hit = catalog.get(m.group(1).lower())
        if hit is None:
            break
        if hit not in names:
            names.append(hit)
        rest = rest[m.end() :].lstrip()
    return rest, names


def default_slash_prompt(skill_names: list[str]) -> str:
    if not skill_names:
        return ""
    if len(skill_names) == 1:
        return f"Use the `{skill_names[0]}` skill."
    listed = ", ".join(f"`{n}`" for n in skill_names)
    return f"Use the {listed} skills."
