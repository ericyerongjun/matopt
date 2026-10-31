"""Import detected items into MatOpt, a subset of Codex's
``external-agent-migration`` (``service.rs``, ``mcp.rs``, ``rewrite.rs``).

The importer re-runs detection and acts only on items whose ids the caller
selected, so a request can name *what* to import but never a path. Every
write is additive and atomic (temp file in the same directory, then
``os.replace``):

mcp_servers   merged into ``settings.resolved_mcp_config_path()``; a name that
              already exists is kept as it is. ``env`` and header values are
              copied verbatim (they may hold tokens), so a new file is created
              ``0600`` and result details name servers, never values.
skills        each skill folder copied to ``~/.matopt/skills/<name>`` unless that
              name exists; symlinks are not followed.
instructions  appended to ``~/.matopt/AGENTS.md`` under a header naming the
              source, after Codex's ``RewriteProfile`` term rewrite. A marker
              comment makes a second import of the same file a no-op.
hooks         merged into ``hooks.json`` (only events in ``hooks.EVENTS``,
              identical groups deduplicated). Importing never sets
              ``hooks_enabled``: running commands stays an operator decision.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable, Optional

from app.agent.hooks import parse_hooks_config
from app.agent.migration.detect import SOURCE_LABELS, DetectedItem, MigrationTargets, scan

# ── term rewrite (Codex rewrite.rs) ───────────────────────────────────────
# Codex rewrites the source's doc-file name to AGENTS.md and the agent's name
# outside URLs and e-mail addresses. Codex also replaces a bare, any-case
# "claude"; MatOpt only replaces the capitalised product name and leaves
# lower-case paths such as ``~/.claude/`` alone, since they still name real
# files on the user's machine.
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>()\"']+|[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_BEFORE = r"(?<![\w./~-])"
_AFTER = r"(?![\w/-]|\.\w)"
_REWRITES: dict[str, list[tuple[re.Pattern[str], str]]] = {
    "claude": [
        (re.compile(r"(?<![\w-])CLAUDE\.md(?![\w-])", re.IGNORECASE), "AGENTS.md"),
        (re.compile(_BEFORE + r"claude[ _-]?code" + _AFTER, re.IGNORECASE), "MatOpt"),
        (re.compile(_BEFORE + r"Claude" + _AFTER), "MatOpt"),
    ],
    "cursor": [
        (re.compile(r"(?<![\w-])\.cursorrules(?![\w-])"), "AGENTS.md"),
        (re.compile(_BEFORE + r"Cursor" + _AFTER), "MatOpt"),
    ],
    "codex": [],
}


def rewrite_instructions(text: str, source: str) -> str:
    rules = _REWRITES.get(source, [])
    if not rules:
        return text

    def terms(chunk: str) -> str:
        for pattern, replacement in rules:
            chunk = pattern.sub(replacement, chunk)
        return chunk

    out: list[str] = []
    pos = 0
    for match in _URL_RE.finditer(text):
        out.append(terms(text[pos:match.start()]))
        out.append(match.group())
        pos = match.end()
    out.append(terms(text[pos:]))
    return "".join(out)


# ── atomic writes ─────────────────────────────────────────────────────────

def _atomic_write(path: Path, text: str, *, new_mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode: Optional[int] = path.stat().st_mode & 0o777
    except FileNotFoundError:
        mode = new_mode
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _load_json_object(path: Path) -> dict:
    """The current file, ``{}`` if missing; raises on a file we must not clobber."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"{path} is not valid JSON; fix it before importing ({type(exc).__name__})") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _dump(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _result(item_id: str, status: str, detail: str) -> dict:
    return {"id": item_id, "status": status, "detail": detail}


# ── per-type importers ────────────────────────────────────────────────────

def _import_mcp(item: DetectedItem, targets: MigrationTargets) -> dict:
    data = _load_json_object(targets.mcp_config)
    key = "mcpServers" if "mcpServers" in data or "servers" not in data else "servers"
    servers = data.setdefault(key, {})
    if not isinstance(servers, dict):
        raise ValueError(f"{targets.mcp_config}: '{key}' must be an object")
    added, skipped = [], []
    for name, config in item.payload["servers"].items():
        if name in servers:
            skipped.append(name)
        else:
            servers[name] = config
            added.append(name)
    notes = list(item.payload.get("notes") or [])
    parts = []
    if added:
        _atomic_write(targets.mcp_config, _dump(data))
        parts.append(f"added {', '.join(added)} to {targets.mcp_config}")
        if any(item.payload["servers"][n].get("env") or item.payload["servers"][n].get("headers") for n in added):
            parts.append("env/header values were copied as-is (they may contain secrets)")
        parts.append("restart MatOpt to connect them")
    if skipped:
        parts.append(f"kept existing {', '.join(skipped)}")
    parts.extend(notes)
    return _result(item.id, "imported" if added else "skipped", "; ".join(parts))


def _ignore_links(directory: str, names: list[str]) -> set[str]:
    return {n for n in names if os.path.islink(os.path.join(directory, n))}


def _import_skills(item: DetectedItem, targets: MigrationTargets) -> dict:
    targets.skills_dir.mkdir(parents=True, exist_ok=True)
    copied, skipped = [], []
    for name in item.payload["names"]:
        dest = targets.skills_dir / name
        if dest.exists():
            skipped.append(name)
            continue
        staging = Path(tempfile.mkdtemp(prefix=".import-", dir=targets.skills_dir))
        try:
            shutil.copytree(item.path / name, staging / name, ignore=_ignore_links)
            os.rename(staging / name, dest)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        copied.append(name)
    parts = []
    if copied:
        parts.append(f"copied {', '.join(copied)} to {targets.skills_dir}")
    if skipped:
        parts.append(f"kept existing {', '.join(skipped)}")
    return _result(item.id, "imported" if copied else "skipped", "; ".join(parts))


def _import_instructions(item: DetectedItem, targets: MigrationTargets) -> dict:
    try:
        current = targets.agents_md.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = ""
    if item.marker in current:
        return _result(item.id, "skipped", f"already imported into {targets.agents_md}")
    label = SOURCE_LABELS.get(item.source, item.source)
    body = rewrite_instructions(item.payload["text"], item.source).strip()
    block = f"{item.marker}\n## Imported from {label}: {item.path}\n\n{body}\n"
    text = (current.rstrip() + "\n\n" + block) if current.strip() else block
    _atomic_write(targets.agents_md, text, new_mode=0o644)
    return _result(item.id, "imported", f"appended to {targets.agents_md}")


def _import_hooks(item: DetectedItem, targets: MigrationTargets) -> dict:
    data = _load_json_object(targets.hooks_path)
    # hooks.json may be {"hooks": {...}} or the bare event map (parse_hooks_config
    # accepts both); write back the wrapped form.
    if "hooks" in data:
        hooks, out = data["hooks"], data
    else:
        hooks = data
        out = {"hooks": hooks}
    if not isinstance(hooks, dict):
        raise ValueError(f"{targets.hooks_path}: 'hooks' must be an object")
    added = 0
    for event, groups in item.payload["hooks"].items():
        existing = hooks.setdefault(event, [])
        for group in groups:
            if group not in existing:
                existing.append(group)
                added += 1
    parse_hooks_config(out)   # never write a file HookEngine would reject
    parts = []
    if added:
        _atomic_write(targets.hooks_path, _dump(out))
        parts.append(f"added {added} hook group(s) to {targets.hooks_path}; hooks run only when "
                     "HOOKS_ENABLED is set, and matchers name the source agent's tools")
    else:
        parts.append("all hook groups already present")
    if item.payload.get("dropped"):
        parts.append(f"unsupported events skipped: {', '.join(sorted(item.payload['dropped']))}")
    return _result(item.id, "imported" if added else "skipped", "; ".join(parts))


_IMPORTERS = {
    "mcp_servers": _import_mcp,
    "skills": _import_skills,
    "instructions": _import_instructions,
    "hooks": _import_hooks,
}


def import_items(ids: Iterable[str], *, home: Path, project_dirs: Iterable[Path],
                 targets: MigrationTargets) -> list[dict]:
    """Import the selected items; one result per distinct id, in request order."""
    by_id = {item.id: item for item in scan(home, project_dirs, targets)}
    results = []
    for item_id in dict.fromkeys(ids):
        item = by_id.get(item_id)
        if item is None:
            results.append(_result(item_id, "failed", "unknown item; run detection again"))
            continue
        try:
            results.append(_IMPORTERS[item.item_type](item, targets))
        except (OSError, ValueError, re.error) as exc:
            results.append(_result(item_id, "failed", str(exc)))
    return results
