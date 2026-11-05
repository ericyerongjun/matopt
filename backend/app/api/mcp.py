"""MCP connectors: one-click connections to well-known MCP servers, and configs
that connect MatOpt's own MCP server to other apps.

Connecting edits ``mcp.json`` (secrets go to ``<data_dir>/mcp.env``, never into
``mcp.json`` or a response) and then reconnects the hub, so new tools are
usable from the next turn without a restart. Writes go through
``require_operator_writes`` (registered in ``app/api/__init__.py``).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agent.mcp import export
from app.agent.mcp.catalog import ConnectorManager
from app.config import settings
from app.runtime import get_runtime

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class ConnectRequest(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)    # path / text inputs
    secrets: dict[str, str] = Field(default_factory=dict)   # secret inputs (stored server-side only)


class EnabledRequest(BaseModel):
    enabled: bool


def _manager() -> ConnectorManager:
    return ConnectorManager(settings.resolved_mcp_config_path(), settings.resolved_mcp_env_path())


def _status() -> list[dict]:
    return get_runtime().mcp.status()


async def _reload() -> list[dict]:
    return await get_runtime().reload_mcp()


def _connector(connector_id: str) -> dict:
    row = next((c for c in _manager().list(_status()) if c["id"] == connector_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown connector {connector_id!r}")
    return row


@router.get("/connectors")
async def list_connectors():
    try:
        return {"connectors": _manager().list(_status()), "enabled": settings.mcp_enabled}
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=f"mcp.json is invalid: {exc}") from None


@router.post("/connectors/{connector_id}")
async def connect(connector_id: str, body: ConnectRequest):
    try:
        _manager().connect(connector_id, body.values, body.secrets)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown connector {connector_id!r}") from None
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    await _reload()
    return {"connector": _connector(connector_id)}


@router.patch("/connectors/{connector_id}")
async def set_enabled(connector_id: str, body: EnabledRequest):
    if not _manager().set_enabled(connector_id, body.enabled):
        raise HTTPException(status_code=404, detail="Connector is not connected")
    await _reload()
    return {"connector": _connector(connector_id)}


@router.delete("/connectors/{connector_id}")
async def disconnect(connector_id: str):
    try:
        removed = _manager().disconnect(connector_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown connector {connector_id!r}") from None
    if removed:
        await _reload()
    return {"removed": removed}


@router.post("/reload")
async def reload_servers():
    return {"servers": await _reload()}


@router.get("/clients")
async def list_clients():
    return {"clients": export.describe()}


@router.get("/clients/{client_id}")
async def client_config(client_id: str, http: Optional[bool] = False):
    client = export.BY_ID.get(client_id)
    if client is None:
        raise HTTPException(status_code=404, detail=f"Unknown client {client_id!r}")
    url = (settings.mcp_public_url or "http://localhost:8000").rstrip("/") + settings.mcp_mount_path if http else None
    return {"client": client_id, "name": client.name, "snippet": export.snippet(client, url),
            "path": str(client.path()) if client.path else None, "note": client.note,
            "command": f"cd backend && .venv/bin/python -m app.agent.mcp.export {client_id} --write"
                       + (f" --http {url}" if url else "")}
