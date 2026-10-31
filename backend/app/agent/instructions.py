"""AGENTS.md instruction files, ported from Codex (``core/src/agents_md.rs``).

Codex concatenates ``AGENTS.md`` files from the project root down to the
working directory, preferring an ``AGENTS.override.md`` in the same folder,
and caps the total at ``project_doc_max_bytes`` (32 KiB). MatOpt has no repo
tree per conversation, so the same rules apply to two scopes:

operator scope   ``~/.matopt/`` then ``<data_dir>/``. Stable for every
                 conversation, so it joins the system prompt and keeps the
                 provider's prefix cache intact.
workspace scope  the conversation workspace root. The user (or the agent)
                 can write it, so it is per-turn context, clearly labelled as
                 workspace content rather than operator policy.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

FILENAMES = ("AGENTS.override.md", "AGENTS.md")


def _read_dir(directory: Path) -> str:
    for name in FILENAMES:
        path = directory / name
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            logger.debug("Could not read %s", path, exc_info=True)
    return ""


def _join(directories: Iterable[Path], max_bytes: int) -> str:
    parts: list[str] = []
    used = 0
    for directory in directories:
        text = _read_dir(directory)
        if not text:
            continue
        encoded = text.encode("utf-8")
        if used + len(encoded) > max_bytes:
            text = encoded[: max(0, max_bytes - used)].decode("utf-8", errors="ignore")
            if text:
                parts.append(text + "\n[AGENTS.md truncated]")
            break
        parts.append(text)
        used += len(encoded)
    return "\n\n".join(parts)


def operator_instructions(settings: Any) -> str:
    if not getattr(settings, "agents_md_enabled", True):
        return ""
    dirs = [Path.home() / ".matopt", Path(settings.data_dir)]
    return _join(dict.fromkeys(dirs), int(getattr(settings, "agents_md_max_bytes", 32 * 1024)))


def workspace_instructions(root: Path, settings: Any) -> str:
    if not getattr(settings, "agents_md_enabled", True):
        return ""
    return _join([Path(root)], int(getattr(settings, "agents_md_max_bytes", 32 * 1024)))
