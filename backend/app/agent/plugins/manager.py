"""Install, toggle and read installed plugins, after Codex's
``core-plugins/src/manager.rs`` (local plugins only: no marketplaces, remote
catalogs or npm sources).

The manager is the one place the runtime asks "what do the enabled plugins
contribute?". Each getter re-reads the store, so an API change shows up the
next time the runtime builds its skills, MCP hub or hook engine:

``skill_dirs()``    directories for ``SkillRegistry.discover``.
``mcp_servers()``   an ``mcpServers`` map, names namespaced
                    ``<plugin>__<server>`` so two plugins (or a plugin and
                    ``mcp.json``) cannot shadow each other.
``hook_config()``   a ``hooks.json``-shaped dict merged across plugins, for
                    ``hooks.parse_hooks_config``. It never turns hooks on: that
                    stays ``settings.hooks_enabled``, operator-owned.

A plugin that fails to load is listed with its errors and contributes nothing,
mirroring Codex's ``PluginLoadOutcome``: one bad plugin never breaks startup.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from app.agent.plugins.manifest import ManifestError, PluginManifest, load_manifest, substitute_root, validate_name
from app.agent.plugins.store import PluginNotFoundError, PluginStore

logger = logging.getLogger(__name__)

SERVER_SEPARATOR = "__"


def plugins_root(settings: Any) -> Path:
    configured = getattr(settings, "plugins_dir", None)
    return Path(configured).expanduser() if configured else Path(settings.data_dir) / "plugins"


class PluginManager:
    def __init__(self, settings: Any, *, root: Optional[Path] = None):
        self.settings = settings
        self.store = PluginStore(root or plugins_root(settings))

    # ── loading ───────────────────────────────────────────────────────
    def _load(self, name: str) -> tuple[Optional[PluginManifest], list[str]]:
        try:
            return load_manifest(self.store.plugin_root(name)), []
        except ManifestError as exc:
            return None, [str(exc)]

    def _enabled_manifests(self) -> list[PluginManifest]:
        enabled = self.store.read_state()["enabled"]
        out = []
        for name in self.store.names():
            if not enabled.get(name, True):
                continue
            manifest, errors = self._load(name)
            if manifest is None:
                logger.warning("Plugin %s not loaded: %s", name, "; ".join(errors))
                continue
            out.append(manifest)
        return out

    def _require(self, plugin_id: str) -> str:
        try:
            name = validate_name(plugin_id)
        except ManifestError:
            raise PluginNotFoundError(plugin_id) from None
        if not self.store.is_installed(name):
            raise PluginNotFoundError(plugin_id)
        return name

    def describe(self, name: str) -> dict:
        manifest, errors = self._load(name)
        enabled = self.store.is_enabled(name)
        if manifest is None:
            return {"id": name, "name": name, "version": None, "description": "", "enabled": enabled,
                    "skills": 0, "mcp_servers": [], "hooks": [], "errors": errors}
        return {
            "id": manifest.name,
            "name": manifest.interface.get("displayName") or manifest.name,
            "version": manifest.version,
            "description": manifest.description,
            "keywords": manifest.keywords,
            "category": manifest.interface.get("category"),
            "enabled": enabled,
            "skills": len(manifest.skill_names()),
            "mcp_servers": sorted(manifest.mcp_servers),
            "hooks": sorted(manifest.hooks),
            "errors": manifest.errors,
        }

    # ── API operations ────────────────────────────────────────────────
    def list(self) -> list[dict]:
        return [self.describe(name) for name in self.store.names()]

    def install(self, path: str | Path, *, reinstall: bool = False) -> dict:
        manifest = self.store.install(Path(path), reinstall=reinstall)
        return self.describe(manifest.name)

    def enable(self, plugin_id: str) -> dict:
        name = self._require(plugin_id)
        self.store.set_enabled(name, True)
        return self.describe(name)

    def disable(self, plugin_id: str) -> dict:
        name = self._require(plugin_id)
        self.store.set_enabled(name, False)
        return self.describe(name)

    def remove(self, plugin_id: str) -> dict:
        name = self._require(plugin_id)
        info = self.describe(name)
        self.store.remove(name)
        return info

    # ── integration getters ───────────────────────────────────────────
    def skill_dirs(self) -> list[Path]:
        return [d for m in self._enabled_manifests() for d in m.skill_dirs]

    def mcp_servers(self) -> dict[str, dict]:
        servers: dict[str, dict] = {}
        for manifest in self._enabled_manifests():
            for server, config in manifest.mcp_servers.items():
                servers[f"{manifest.name}{SERVER_SEPARATOR}{server}"] = substitute_root(config, manifest.root)
        return servers

    def hook_config(self) -> dict:
        merged: dict[str, list] = {}
        for manifest in self._enabled_manifests():
            for event, groups in manifest.hooks.items():
                merged.setdefault(event, []).extend(substitute_root(groups, manifest.root))
        return {"hooks": merged}
