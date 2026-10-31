"""Plugin manifests, a local subset of Codex's (``codex-rs/plugin/src/manifest.rs``,
``core-plugins/src/manifest.rs`` and ``loader.rs``).

A plugin is a folder that bundles skills, MCP servers and hooks behind one
manifest at ``<plugin>/.matopt-plugin/plugin.json``. Codex's
``.codex-plugin/plugin.json`` and Claude Code's ``.claude-plugin/plugin.json``
are read too, so an existing plugin folder installs unchanged::

    {"name": "lab-tools", "version": "1.2.0", "description": "...",
     "keywords": ["chemistry"],
     "skills": "./skills",               # a path or a list of paths
     "mcpServers": "./.mcp.json",        # a path, or an inline mcpServers map
     "hooks": "./hooks/hooks.json",      # a path, a list, or an inline hooks map
     "interface": {"displayName": "Lab tools", "category": "science"}}

Components left out fall back to Codex's defaults (``loader.rs``:
``DEFAULT_SKILLS_DIR_NAME``, ``DEFAULT_MCP_CONFIG_FILE``,
``DEFAULT_HOOKS_CONFIG_FILE``) when those files exist.

Every component path is relative to the plugin root: absolute paths, ``..``
and symlinks that resolve outside the root are refused, as Codex's
``resolve_manifest_paths`` does, so a manifest cannot make MatOpt read files
it did not ship. A broken *component* is recorded in ``errors`` and dropped;
only an unreadable manifest or an invalid name fails the whole plugin.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from app.agent.hooks import parse_hooks_config

MANIFEST_DIRS = (".matopt-plugin", ".codex-plugin", ".claude-plugin")
MANIFEST_FILE = "plugin.json"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
MAX_NAME = 64

DEFAULT_SKILLS_DIR = "skills"
DEFAULT_MCP_FILE = ".mcp.json"
DEFAULT_HOOKS_FILE = "hooks/hooks.json"

# Placeholders Codex fills in plugin hook commands (``PLUGIN_ROOT`` and the
# Claude-compatible ``CLAUDE_PLUGIN_ROOT``); MatOpt adds its own spelling.
ROOT_PLACEHOLDERS = ("${PLUGIN_ROOT}", "${CLAUDE_PLUGIN_ROOT}", "${MATOPT_PLUGIN_ROOT}")


class ManifestError(ValueError):
    """The plugin cannot be loaded at all (no manifest, bad JSON, bad name)."""


def validate_name(name: Any) -> str:
    if not isinstance(name, str) or len(name) > MAX_NAME or not NAME_RE.fullmatch(name):
        raise ManifestError(
            f"Invalid plugin name {name!r}: use lowercase letters, digits, '-' and '_' "
            f"(starting with a letter or digit, at most {MAX_NAME} characters)"
        )
    return name


@dataclass
class PluginManifest:
    root: Path
    manifest_path: Path
    name: str
    version: Optional[str] = None
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    skill_dirs: list[Path] = field(default_factory=list)
    mcp_servers: dict[str, dict] = field(default_factory=dict)
    hooks: dict[str, list] = field(default_factory=dict)   # raw {event: [groups]}
    interface: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def skill_names(self) -> list[str]:
        names: list[str] = []
        for d in self.skill_dirs:
            try:
                children = sorted(p for p in d.iterdir() if p.is_dir())
            except OSError:
                continue
            names.extend(c.name for c in children
                         if not c.name.startswith(".") and (c / "SKILL.md").is_file())
        return names


def find_manifest(root: Path) -> Optional[Path]:
    for directory in MANIFEST_DIRS:
        path = root / directory / MANIFEST_FILE
        if path.is_file():
            return path
    return None


def resolve_component_path(root: Path, raw: Any) -> Path:
    """A manifest path, confined to the plugin root."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("path must be a non-empty string")
    text = raw.strip().replace("\\", "/")
    pure = PurePosixPath(text)
    if pure.is_absolute() or re.match(r"^[A-Za-z]:", text) or text.startswith("~"):
        raise ValueError(f"path {raw!r} must be relative to the plugin root")
    if ".." in pure.parts:
        raise ValueError(f"path {raw!r} must not contain '..'")
    path = root.joinpath(*[p for p in pure.parts if p != "."])
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError(f"path {raw!r} resolves outside the plugin root") from None
    return path


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"{path.name} not found") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path.name}: {exc}") from None


def _as_list(raw: Any) -> list:
    return raw if isinstance(raw, list) else [raw]


def _load_skills(root: Path, raw: Any, errors: list[str]) -> list[Path]:
    if raw is None:
        default = root / DEFAULT_SKILLS_DIR
        return [default] if default.is_dir() else []
    dirs: list[Path] = []
    for entry in _as_list(raw):
        try:
            path = resolve_component_path(root, entry)
        except ValueError as exc:
            errors.append(f"skills: {exc}")
            continue
        if not path.is_dir():
            errors.append(f"skills: {entry!r} is not a directory")
        elif (path / "SKILL.md").is_file():
            # SkillRegistry.discover reads the *children* of each directory.
            errors.append(f"skills: {entry!r} is a single skill; point 'skills' at its parent folder")
        elif path not in dirs:
            dirs.append(path)
    return dirs


def _server_map(raw: Any) -> dict:
    if isinstance(raw, dict) and isinstance(raw.get("mcpServers"), dict):
        return raw["mcpServers"]
    if isinstance(raw, dict) and isinstance(raw.get("servers"), dict):
        return raw["servers"]
    if isinstance(raw, dict):
        return raw
    raise ValueError("expected an object of MCP servers")


def _load_mcp(root: Path, raw: Any, errors: list[str]) -> dict[str, dict]:
    if raw is None:
        default = root / DEFAULT_MCP_FILE
        if not default.is_file():
            return {}
        raw = DEFAULT_MCP_FILE
    try:
        if isinstance(raw, str):
            servers = _server_map(_read_json(resolve_component_path(root, raw)))
        else:
            servers = _server_map(raw)
    except ValueError as exc:
        errors.append(f"mcpServers: {exc}")
        return {}
    valid: dict[str, dict] = {}
    for name, config in servers.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            errors.append(f"mcpServers: invalid server name {name!r}")
        elif not isinstance(config, dict) or not (config.get("command") or config.get("url")):
            errors.append(f"mcpServers: {name!r} needs a 'command' or a 'url'")
        else:
            valid[name] = config
    return valid


def _hooks_map(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("hooks config must be an object")
    hooks = raw.get("hooks", raw)
    if not isinstance(hooks, dict):
        raise ValueError("'hooks' must be an object")
    return hooks


def _load_hooks(root: Path, raw: Any, errors: list[str]) -> dict[str, list]:
    if raw is None:
        default = root / DEFAULT_HOOKS_FILE
        if not default.is_file():
            return {}
        raw = DEFAULT_HOOKS_FILE
    sources: list[Any] = []
    if isinstance(raw, dict):
        sources.append(raw)
    else:
        for entry in _as_list(raw):
            try:
                sources.append(_read_json(resolve_component_path(root, entry)))
            except ValueError as exc:
                errors.append(f"hooks: {exc}")
    merged: dict[str, list] = {}
    for source in sources:
        try:
            hooks = _hooks_map(source)
            # Validate with the engine's own parser so a plugin can never ship a
            # hooks file that HookEngine would later refuse as a whole.
            parse_hooks_config({"hooks": hooks})
        except (ValueError, re.error) as exc:
            errors.append(f"hooks: {exc}")
            continue
        for event, groups in hooks.items():
            merged.setdefault(event, []).extend(groups)
    return merged


def load_manifest(root: Path) -> PluginManifest:
    root = Path(root)
    if not root.is_dir():
        raise ManifestError(f"{root} is not a directory")
    path = find_manifest(root)
    if path is None:
        raise ManifestError(
            f"No plugin manifest in {root} (expected {MANIFEST_DIRS[0]}/{MANIFEST_FILE})"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Cannot read {path}: {exc}") from None
    if not isinstance(raw, dict):
        raise ManifestError(f"{path} must contain a JSON object")
    name = validate_name(raw.get("name"))
    errors: list[str] = []
    version = raw.get("version")
    keywords = raw.get("keywords") or []
    interface = raw.get("interface") or {}
    return PluginManifest(
        root=root,
        manifest_path=path,
        name=name,
        version=str(version) if version is not None else None,
        description=str(raw.get("description") or ""),
        keywords=[str(k) for k in keywords] if isinstance(keywords, list) else [],
        skill_dirs=_load_skills(root, raw.get("skills"), errors),
        mcp_servers=_load_mcp(root, raw.get("mcpServers"), errors),
        hooks=_load_hooks(root, raw.get("hooks"), errors),
        interface={k: interface[k] for k in ("displayName", "category", "shortDescription")
                   if isinstance(interface, dict) and k in interface},
        errors=errors,
    )


def substitute_root(value: Any, root: Path) -> Any:
    """Fill ``${PLUGIN_ROOT}`` (and its aliases) in strings, recursively."""
    if isinstance(value, str):
        for placeholder in ROOT_PLACEHOLDERS:
            value = value.replace(placeholder, str(root))
        return value
    if isinstance(value, list):
        return [substitute_root(v, root) for v in value]
    if isinstance(value, dict):
        return {k: substitute_root(v, root) for k, v in value.items()}
    return value
