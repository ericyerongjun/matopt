"""Plugin management API (contract in docs/CODEX-INTEGRATION.md, Phase 2).

Installing copies a local folder into the plugin store; it never fetches from
the network. MCP servers are connected once at startup, so any change that
touches a plugin with MCP servers answers ``restart_required: true`` until the
runtime learns to reload its hub.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.agent.plugins import ManifestError, PluginExistsError, PluginManager, PluginNotFoundError

router = APIRouter(prefix="/api", tags=["plugins"])


def get_plugin_manager() -> PluginManager:
    from app.config import settings

    return PluginManager(settings)


class InstallRequest(BaseModel):
    path: str
    reinstall: bool = False


def _changed(plugin: dict) -> dict:
    return {"plugin": plugin, "restart_required": bool(plugin.get("mcp_servers"))}


@router.get("/plugins")
async def list_plugins(manager: PluginManager = Depends(get_plugin_manager)):
    return {"plugins": manager.list()}


@router.post("/plugins/install")
async def install_plugin(body: InstallRequest, manager: PluginManager = Depends(get_plugin_manager)):
    try:
        plugin = manager.install(body.path, reinstall=body.reinstall)
    except PluginExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except (ManifestError, ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _changed(plugin)


def _toggle(manager: PluginManager, plugin_id: str, op: str) -> dict:
    try:
        return _changed(getattr(manager, op)(plugin_id))
    except PluginNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown plugin {plugin_id!r}") from None


@router.post("/plugins/{plugin_id}/enable")
async def enable_plugin(plugin_id: str, manager: PluginManager = Depends(get_plugin_manager)):
    return _toggle(manager, plugin_id, "enable")


@router.post("/plugins/{plugin_id}/disable")
async def disable_plugin(plugin_id: str, manager: PluginManager = Depends(get_plugin_manager)):
    return _toggle(manager, plugin_id, "disable")


@router.delete("/plugins/{plugin_id}")
async def remove_plugin(plugin_id: str, manager: PluginManager = Depends(get_plugin_manager)):
    return _toggle(manager, plugin_id, "remove")
