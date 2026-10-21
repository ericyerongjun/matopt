"""End to end over stdio: how local MCP Apps hosts (Claude Desktop, VS Code, Goose) launch MatOpt."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastmcp")

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from app.storage.db import Database
from app.ui.mcp_apps import SURFACE_META, VIEWER_URI
from app.ui.store import UIStore

BACKEND = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_stdio_server_serves_view_and_saves_state(tmp_path):
    db = Database(tmp_path / "matopt.sqlite3")
    cid = db.create_conversation()["id"]
    UIStore(db).commit(cid, {"id": "sine-wave", "kind": "mini_app", "title": "Sine wave explorer", "fallback": "Frequency 2.",
                             "app": {"html": "<p>sine</p>"}, "state": {"frequency": 2}})
    db.close()
    env = {**os.environ, "DATA_DIR": str(tmp_path), "MCP_ENABLED": "false", "LAMBDA_ENABLED": "false",
           # HTTP auth settings never block the local stdio transport.
           "MCP_AUTH_TOKEN": "correct-horse-battery-staple"}
    transport = StdioTransport(command=sys.executable, args=["-m", "app.agent.mcp"], env=env, cwd=str(BACKEND))
    async with Client(transport, timeout=60) as client:
        assert VIEWER_URI in {str(r.uri) for r in await client.list_resources()}
        [view] = await client.read_resource(VIEWER_URI)
        assert view.mimeType == "text/html;profile=mcp-app"
        opened = await client.call_tool("open_ui_app", {"conversation_id": cid, "surface_id": "sine-wave"})
        surface = opened.meta[SURFACE_META]
        saved = await client.call_tool("save_ui_state", {"conversation_id": cid, "surface_id": "sine-wave", "token": surface["token"],
                                                         "revision": surface["revision"], "state": {"frequency": 3}})
    assert opened.content[0].text == "Frequency 2." and surface["html"] == "<p>sine</p>"
    assert saved.structured_content == {"surface_id": "sine-wave", "revision": 2, "state": {"frequency": 3}}
    db = Database(tmp_path / "matopt.sqlite3")
    try:
        assert UIStore(db).get(cid, "sine-wave")["state"] == {"frequency": 3}
    finally:
        db.close()
