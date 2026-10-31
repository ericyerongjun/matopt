"""Local plugins bundling skills, MCP servers and hooks (Codex ``core-plugins``)."""

from app.agent.plugins.manager import PluginManager, plugins_root
from app.agent.plugins.manifest import ManifestError, PluginManifest, load_manifest
from app.agent.plugins.store import PluginExistsError, PluginNotFoundError, PluginStore

__all__ = [
    "ManifestError",
    "PluginExistsError",
    "PluginManager",
    "PluginManifest",
    "PluginNotFoundError",
    "PluginStore",
    "load_manifest",
    "plugins_root",
]
