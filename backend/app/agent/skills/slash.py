"""Parse leading ``/skill-name`` and ``/tool_name`` tokens (Cursor / Claude / Codex)."""

from __future__ import annotations

import re
from typing import Iterable

# Hyphens (skills) and underscores (tools) both count as a command token.
SLASH_RE = re.compile(r"^/([a-z0-9]+(?:[-_][a-z0-9]+)*)(?:\s+|$)")


def _index(names: Iterable[str]) -> dict[str, str]:
    catalog: dict[str, str] = {}
    for raw in names:
        name = (raw or "").strip()
        if not name:
            continue
        catalog[name.lower()] = name
        catalog[name.lower().replace("_", "-")] = name
        catalog[name.lower().replace("-", "_")] = name
    return catalog


def split_slash_commands(
    text: str,
    skills: Iterable[str],
    tools: Iterable[str],
) -> tuple[str, list[str], list[str]]:
    """Strip known ``/skill`` and ``/tool`` prefixes. Unknown tokens stay in the message."""
    skill_names: list[str] = []
    tool_names: list[str] = []
    rest = (text or "").lstrip()
    skill_cat = _index(skills)
    tool_cat = _index(tools)
    while True:
        m = SLASH_RE.match(rest)
        if not m:
            break
        token = m.group(1).lower()
        skill = skill_cat.get(token)
        tool = tool_cat.get(token)
        if skill is not None:
            if skill not in skill_names:
                skill_names.append(skill)
        elif tool is not None:
            if tool not in tool_names:
                tool_names.append(tool)
        else:
            break
        rest = rest[m.end() :].lstrip()
    return rest, skill_names, tool_names


def split_slash_skills(text: str, known: Iterable[str]) -> tuple[str, list[str]]:
    """Strip known ``/skill`` prefixes. Unknown tokens are left in the message."""
    rest, names, _tools = split_slash_commands(text, known, [])
    return rest, names


def default_slash_prompt(skill_names: list[str], tool_names: list[str] | None = None) -> str:
    bits: list[str] = []
    tools = tool_names or []
    if len(skill_names) == 1:
        bits.append(f"Use the `{skill_names[0]}` skill.")
    elif skill_names:
        listed = ", ".join(f"`{n}`" for n in skill_names)
        bits.append(f"Use the {listed} skills.")
    if len(tools) == 1:
        bits.append(f"Prefer the `{tools[0]}` tool for this request.")
    elif tools:
        listed = ", ".join(f"`{n}`" for n in tools)
        bits.append(f"Prefer the {listed} tools for this request.")
    return " ".join(bits)
