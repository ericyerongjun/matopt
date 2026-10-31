"""Find importable configuration from other coding agents, a subset of Codex's
``external-agent-migration`` (``source/cla.rs``, ``source_cla.rs``,
``source/cur.rs``, ``mcp.rs``, ``model.rs``).

Detection only reads. It returns one item per source file or folder, each with
a stable id (a hash of source, scope, type and path), so the UI can show the
list and send back the ids it wants imported; the importer re-runs detection
and acts on the same items, never on paths supplied by the client.

Sources and what is read (``home`` and ``project_dirs`` are injected so tests
never touch the real home directory):

Claude Code  ``~/.claude.json`` ``mcpServers`` and ``projects[<dir>].mcpServers``,
             ``<project>/.mcp.json``, ``~/.claude/skills``, ``<project>/.claude/skills``,
             ``~/.claude/CLAUDE.md``, ``<project>/CLAUDE.md``, ``<project>/.claude/CLAUDE.md``,
             hooks in ``~/.claude/settings.json`` and ``<project>/.claude/settings.json``.
Cursor       ``~/.cursor/mcp.json``, ``<project>/.cursor/mcp.json``, ``<project>/.cursorrules``,
             ``~/.cursor/skills``, ``<project>/.cursor/skills``.
Codex        ``~/.codex/config.toml`` ``[mcp_servers.*]``, ``~/.codex/skills``,
             ``~/.codex/AGENTS.md``.

MCP entries are normalised to the FastMCP ``mcpServers`` shape MatOpt's hub
reads (``command``/``args``/``env``/``cwd`` or ``url``/``headers``/``transport``).
Disabled servers are left out, as Codex's ``mcp_server_is_disabled`` does.
``conflicts`` lists names already present at the MatOpt destination; the
importer never overwrites them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from app.agent.hooks import EVENTS, parse_hooks_config

logger = logging.getLogger(__name__)

SOURCE_LABELS = {"claude": "Claude Code", "cursor": "Cursor", "codex": "Codex"}
# MatOpt-side policy keys (mcp/client.py SERVER_POLICY_KEYS) carried over from Codex TOML.
POLICY_KEYS = ("startup_timeout_sec", "tool_timeout_sec", "enabled_tools", "disabled_tools")
REMOTE_TRANSPORTS = {"http": "http", "streamable-http": "http", "streamable_http": "http", "sse": "sse"}
MARKER_PREFIX = "<!-- matopt-migration:"


@dataclass
class MigrationTargets:
    """Where imported items land. Injected in tests."""

    mcp_config: Path
    skills_dir: Path
    agents_md: Path
    hooks_path: Path

    @classmethod
    def from_settings(cls, settings: Any, matopt_home: Path) -> "MigrationTargets":
        hooks = getattr(settings, "hooks_path", None) or Path(settings.data_dir) / "hooks.json"
        return cls(
            mcp_config=Path(settings.resolved_mcp_config_path()),
            skills_dir=Path(matopt_home) / "skills",
            agents_md=Path(matopt_home) / "AGENTS.md",
            hooks_path=Path(hooks),
        )


@dataclass
class DetectedItem:
    source: str            # claude | cursor | codex
    scope: str             # home | project
    item_type: str         # mcp_servers | skills | instructions | hooks
    path: Path
    summary: str
    count: int
    payload: dict = field(default_factory=dict)
    key: str = ""          # disambiguates several items read from one file
    conflicts: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        raw = f"{self.source}\0{self.scope}\0{self.item_type}\0{self.path}\0{self.key}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @property
    def marker(self) -> str:
        return f"{MARKER_PREFIX} {self.source} {self.path} -->"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "scope": self.scope,
            "item_type": self.item_type,
            "path": str(self.path),
            "summary": self.summary,
            "count": self.count,
            "conflicts": list(self.conflicts),
        }


# ── readers ───────────────────────────────────────────────────────────────

def _read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, ValueError):
        logger.info("Migration: skipping unreadable %s", path)
        return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip() if path.is_file() else ""
    except OSError:
        return ""


def _str_map(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(k): v if isinstance(v, str) else json.dumps(v) for k, v in raw.items()}


def normalize_json_server(config: Any) -> Optional[dict]:
    """A Claude Code / Cursor ``mcpServers`` entry in MatOpt's shape, or None."""
    if not isinstance(config, dict) or config.get("disabled") is True or config.get("enabled") is False:
        return None
    kind = str(config.get("type") or config.get("transport") or "").lower()
    if isinstance(config.get("command"), str) and config["command"].strip():
        out: dict[str, Any] = {"command": config["command"]}
        args = config.get("args")
        if isinstance(args, list):
            out["args"] = [str(a) for a in args]
        if config.get("env"):
            out["env"] = _str_map(config["env"])
        if isinstance(config.get("cwd"), str):
            out["cwd"] = config["cwd"]
        return out
    if isinstance(config.get("url"), str) and config["url"].strip():
        out = {"url": config["url"]}
        if kind in REMOTE_TRANSPORTS:
            out["transport"] = REMOTE_TRANSPORTS[kind]
        if config.get("headers"):
            out["headers"] = _str_map(config["headers"])
        return out
    return None


def normalize_codex_server(config: Any, notes: list[str], name: str) -> Optional[dict]:
    """A Codex ``[mcp_servers.<name>]`` table (``config/src/mcp_types.rs``) in MatOpt's shape."""
    if not isinstance(config, dict) or config.get("enabled") is False:
        return None
    out: dict[str, Any]
    if isinstance(config.get("command"), str) and config["command"].strip():
        out = {"command": config["command"]}
        if isinstance(config.get("args"), list):
            out["args"] = [str(a) for a in config["args"]]
        if config.get("env"):
            out["env"] = _str_map(config["env"])
        if isinstance(config.get("cwd"), str):
            out["cwd"] = config["cwd"]
        if config.get("env_vars"):
            notes.append(f"{name}: env_vars (inherited variables) are not supported; set them in env")
    elif isinstance(config.get("url"), str) and config["url"].strip():
        out = {"url": config["url"], "transport": "http"}
        if config.get("http_headers"):
            out["headers"] = _str_map(config["http_headers"])
        if config.get("bearer_token_env_var") or config.get("env_http_headers"):
            notes.append(f"{name}: headers read from environment variables were not converted")
    else:
        return None
    for key in POLICY_KEYS:
        if key in config:
            out[key] = config[key]
    return out


def _servers_from_map(raw: Any) -> dict[str, dict]:
    servers: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return servers
    for name, config in raw.items():
        if isinstance(name, str) and name.strip():
            normalized = normalize_json_server(config)
            if normalized is not None:
                servers[name] = normalized
    return servers


def _same_dir(raw: str, directory: Path) -> bool:
    try:
        return Path(raw).expanduser().resolve() == directory.resolve()
    except (OSError, ValueError):
        return False


def _skill_names(directory: Path) -> list[str]:
    try:
        return sorted(c.name for c in directory.iterdir()
                      if c.is_dir() and not c.is_symlink() and not c.name.startswith(".")
                      and (c / "SKILL.md").is_file())
    except OSError:
        return []


def _filter_hooks(raw: Any) -> tuple[dict[str, list], list[str]]:
    """Keep the events and command handlers MatOpt's engine accepts."""
    hooks = raw.get("hooks") if isinstance(raw, dict) else None
    if not isinstance(hooks, dict):
        return {}, []
    kept: dict[str, list] = {}
    dropped: list[str] = []
    for event, groups in hooks.items():
        if event not in EVENTS or not isinstance(groups, list):
            dropped.append(str(event))
            continue
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            handlers = [h for h in group["hooks"]
                        if isinstance(h, dict) and h.get("type", "command") == "command"]
            if not handlers:
                continue
            candidate = {**group, "hooks": handlers}
            try:
                parse_hooks_config({"hooks": {event: [candidate]}})
            except (ValueError, re.error):
                continue
            kept.setdefault(event, []).append(candidate)
    return kept, dropped


# ── detection ─────────────────────────────────────────────────────────────

def _mcp_item(source: str, scope: str, path: Path, servers: dict, *, key: str = "",
              notes: Optional[list[str]] = None) -> Optional[DetectedItem]:
    if not servers:
        return None
    summary = f"{len(servers)} MCP server(s): {', '.join(sorted(servers))}"
    if key:
        summary += f" (project {key})"
    return DetectedItem(source, scope, "mcp_servers", path, summary, len(servers),
                        {"servers": servers, "notes": list(notes or [])}, key=key)


def _skills_item(source: str, scope: str, directory: Path) -> Optional[DetectedItem]:
    names = _skill_names(directory) if directory.is_dir() else []
    if not names:
        return None
    return DetectedItem(source, scope, "skills", directory,
                        f"{len(names)} skill(s): {', '.join(names)}", len(names), {"names": names})


def _instructions_item(source: str, scope: str, path: Path) -> Optional[DetectedItem]:
    text = _read_text(path)
    if not text:
        return None
    lines = text.count("\n") + 1
    return DetectedItem(source, scope, "instructions", path,
                        f"{path.name}: {lines} line(s)", 1, {"text": text})


def _hooks_item(source: str, scope: str, path: Path) -> Optional[DetectedItem]:
    kept, dropped = _filter_hooks(_read_json(path))
    if not kept:
        return None
    count = sum(len(g) for g in kept.values())
    summary = f"{count} hook group(s): {', '.join(sorted(kept))}"
    if dropped:
        summary += f"; unsupported events skipped: {', '.join(sorted(dropped))}"
    return DetectedItem(source, scope, "hooks", path, summary, count, {"hooks": kept, "dropped": dropped})


def _home_items(home: Path, project_dirs: list[Path]) -> Iterable[Optional[DetectedItem]]:
    claude_json = home / ".claude.json"
    data = _read_json(claude_json)
    if isinstance(data, dict):
        yield _mcp_item("claude", "home", claude_json, _servers_from_map(data.get("mcpServers")))
        projects = data.get("projects")
        if isinstance(projects, dict):
            for raw_path, entry in projects.items():
                if isinstance(entry, dict) and any(_same_dir(raw_path, d) for d in project_dirs):
                    yield _mcp_item("claude", "project", claude_json,
                                    _servers_from_map(entry.get("mcpServers")), key=str(raw_path))
    yield _skills_item("claude", "home", home / ".claude" / "skills")
    yield _instructions_item("claude", "home", home / ".claude" / "CLAUDE.md")
    yield _hooks_item("claude", "home", home / ".claude" / "settings.json")

    cursor = _read_json(home / ".cursor" / "mcp.json")
    if isinstance(cursor, dict):
        yield _mcp_item("cursor", "home", home / ".cursor" / "mcp.json", _servers_from_map(cursor.get("mcpServers")))
    yield _skills_item("cursor", "home", home / ".cursor" / "skills")

    codex_toml = home / ".codex" / "config.toml"
    try:
        codex = tomllib.loads(codex_toml.read_text(encoding="utf-8")) if codex_toml.is_file() else {}
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        logger.info("Migration: skipping unreadable %s", codex_toml)
        codex = {}
    tables = codex.get("mcp_servers")
    if isinstance(tables, dict):
        notes: list[str] = []
        servers = {}
        for name, table in tables.items():
            normalized = normalize_codex_server(table, notes, name)
            if normalized is not None:
                servers[name] = normalized
        yield _mcp_item("codex", "home", codex_toml, servers, notes=notes)
    yield _skills_item("codex", "home", home / ".codex" / "skills")
    yield _instructions_item("codex", "home", home / ".codex" / "AGENTS.md")


def _project_items(project: Path) -> Iterable[Optional[DetectedItem]]:
    mcp_json = _read_json(project / ".mcp.json")
    if isinstance(mcp_json, dict):
        yield _mcp_item("claude", "project", project / ".mcp.json", _servers_from_map(mcp_json.get("mcpServers")))
    yield _skills_item("claude", "project", project / ".claude" / "skills")
    yield _instructions_item("claude", "project", project / "CLAUDE.md")
    yield _instructions_item("claude", "project", project / ".claude" / "CLAUDE.md")
    yield _hooks_item("claude", "project", project / ".claude" / "settings.json")

    cursor = _read_json(project / ".cursor" / "mcp.json")
    if isinstance(cursor, dict):
        yield _mcp_item("cursor", "project", project / ".cursor" / "mcp.json",
                        _servers_from_map(cursor.get("mcpServers")))
    yield _instructions_item("cursor", "project", project / ".cursorrules")
    yield _skills_item("cursor", "project", project / ".cursor" / "skills")


def _existing_mcp_names(path: Path) -> set[str]:
    data = _read_json(path)
    if not isinstance(data, dict):
        return set()
    servers = data.get("mcpServers") or data.get("servers") or {}
    return set(servers) if isinstance(servers, dict) else set()


def _existing_hook_events(path: Path) -> set[str]:
    data = _read_json(path)
    hooks = data.get("hooks", data) if isinstance(data, dict) else None
    return {e for e, g in hooks.items() if g} if isinstance(hooks, dict) else set()


def _set_conflicts(items: list[DetectedItem], targets: MigrationTargets) -> None:
    mcp_names = _existing_mcp_names(targets.mcp_config)
    skill_names = set(_skill_names(targets.skills_dir)) if targets.skills_dir.is_dir() else set()
    if targets.skills_dir.is_dir():
        skill_names |= {p.name for p in targets.skills_dir.iterdir()}
    agents_md = _read_text(targets.agents_md)
    hook_events = _existing_hook_events(targets.hooks_path)
    for item in items:
        if item.item_type == "mcp_servers":
            item.conflicts = sorted(set(item.payload["servers"]) & mcp_names)
        elif item.item_type == "skills":
            item.conflicts = sorted(set(item.payload["names"]) & skill_names)
        elif item.item_type == "instructions":
            item.conflicts = [targets.agents_md.name] if item.marker in agents_md else []
        elif item.item_type == "hooks":
            item.conflicts = sorted(set(item.payload["hooks"]) & hook_events)


def scan(home: Path, project_dirs: Iterable[Path], targets: MigrationTargets) -> list[DetectedItem]:
    home = Path(home)
    projects = list(dict.fromkeys(Path(p) for p in project_dirs))
    found: list[DetectedItem] = []
    seen: set[tuple] = set()
    candidates = list(_home_items(home, projects))
    for project in projects:
        if project.is_dir():
            candidates.extend(_project_items(project))
    for item in candidates:
        if item is None:
            continue
        try:
            resolved = item.path.resolve()
        except OSError:
            resolved = item.path
        # A project that is the home directory would list the same file twice.
        dedupe = (item.item_type, resolved, item.key)
        if dedupe in seen:
            continue
        seen.add(dedupe)
        found.append(item)
    _set_conflicts(found, targets)
    return found


def detect(home: Path, project_dirs: Iterable[Path], targets: MigrationTargets) -> list[dict]:
    return [item.to_dict() for item in scan(home, project_dirs, targets)]
