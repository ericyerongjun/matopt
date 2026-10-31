"""Installed plugins on disk, after Codex's ``core-plugins/src/store.rs``.

Layout under ``settings.plugins_dir`` (default ``<data_dir>/plugins``)::

    <root>/<name>/            a copy of the plugin folder
    <root>/state.json         {"enabled": {"<name>": true|false}}

Codex installs by staging the plugin next to its final place and renaming it
in, so a crash mid-copy never leaves a half-written plugin that the loader
would pick up; MatOpt does the same with a ``.staging-*`` directory in the
store root (same filesystem, so the rename is atomic). Symlinks are not copied:
a link could point anywhere on the host, and the store must only hold files
the plugin itself shipped. ``state.json`` is replaced atomically as well; a
missing entry means enabled, matching Codex's default for a fresh install.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from app.agent.plugins.manifest import PluginManifest, load_manifest, validate_name

logger = logging.getLogger(__name__)

STATE_FILE = "state.json"
SKIP_NAMES = frozenset({".git", "__pycache__", ".DS_Store", "node_modules"})


class PluginExistsError(FileExistsError):
    pass


class PluginNotFoundError(KeyError):
    pass


def write_json_atomic(path: Path, data: object, mode: Optional[int] = None) -> None:
    """Write JSON through a temp file in the same directory and ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _ignore(directory: str, names: list[str]) -> set[str]:
    return {n for n in names if n in SKIP_NAMES or os.path.islink(os.path.join(directory, n))}


class PluginStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def state_path(self) -> Path:
        return self.root / STATE_FILE

    def plugin_root(self, name: str) -> Path:
        return self.root / validate_name(name)

    # ── state ─────────────────────────────────────────────────────────
    def read_state(self) -> dict:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"enabled": {}}
        except (OSError, ValueError):
            logger.warning("Ignoring unreadable plugin state %s", self.state_path)
            return {"enabled": {}}
        enabled = raw.get("enabled") if isinstance(raw, dict) else None
        if not isinstance(enabled, dict):
            enabled = {}
        return {"enabled": {k: v for k, v in enabled.items() if isinstance(v, bool)}}

    def is_enabled(self, name: str) -> bool:
        return self.read_state()["enabled"].get(name, True)

    def set_enabled(self, name: str, enabled: Optional[bool]) -> None:
        state = self.read_state()
        if enabled is None:
            state["enabled"].pop(name, None)
        else:
            state["enabled"][name] = enabled
        write_json_atomic(self.state_path, state)

    # ── plugins ───────────────────────────────────────────────────────
    def names(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir()
                      if p.is_dir() and not p.name.startswith(".") and not p.is_symlink())

    def is_installed(self, name: str) -> bool:
        return self.plugin_root(name).is_dir()

    def install(self, source: Path, *, reinstall: bool = False) -> PluginManifest:
        source = Path(source).expanduser()
        if not source.is_absolute():
            raise ValueError("Plugin path must be absolute")
        source = source.resolve()
        manifest = load_manifest(source)        # validate before copying anything
        self.root.mkdir(parents=True, exist_ok=True)
        root = self.root.resolve()
        if source == root or root in source.parents:
            raise ValueError("Cannot install a plugin from inside the plugin store")
        dest = self.root / manifest.name
        if dest.exists() and not reinstall:
            raise PluginExistsError(f"Plugin {manifest.name!r} is already installed")
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=self.root))
        try:
            staged = staging / manifest.name
            shutil.copytree(source, staged, ignore=_ignore, symlinks=False)
            installed = load_manifest(staged)   # the copy must load on its own
            if installed.name != manifest.name:
                raise ValueError("Plugin manifest changed during install")
            backup = None
            if dest.exists():
                backup = self.root / f".trash-{manifest.name}-{uuid.uuid4().hex[:8]}"
                os.rename(dest, backup)
            try:
                os.rename(staged, dest)
            except BaseException:
                if backup is not None:
                    os.rename(backup, dest)
                raise
            if backup is not None:
                shutil.rmtree(backup, ignore_errors=True)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        if manifest.name not in self.read_state()["enabled"]:
            self.set_enabled(manifest.name, True)
        return load_manifest(dest)

    def remove(self, name: str) -> None:
        dest = self.plugin_root(name)
        if not dest.is_dir():
            raise PluginNotFoundError(name)
        trash = self.root / f".trash-{name}-{uuid.uuid4().hex[:8]}"
        os.rename(dest, trash)          # disappears from listings atomically
        shutil.rmtree(trash, ignore_errors=True)
        self.set_enabled(name, None)
