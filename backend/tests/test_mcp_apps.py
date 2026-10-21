"""The kernel MCP server exposes saved mini-apps as a standard MCP Apps view."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastmcp")

from fastmcp import Client

from app.agent.mcp.sandbox import build_mcp
from app.config import settings
from app.storage.db import Database
from app.ui.mcp_apps import SURFACE_META, VIEWER_URI
from app.ui.service import UIService

_EXAMPLES = Path(__file__).resolve().parents[2] / "scripts" / "ui_examples.py"
_spec = importlib.util.spec_from_file_location("matopt_ui_examples", _EXAMPLES)
ui_examples = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ui_examples)

SINE = {"id": "sine-wave", "kind": "mini_app", "title": "Sine wave explorer",
        "fallback": "A sine wave with amplitude 1 and adjustable frequency. x is in radians.",
        "app": {"html": ui_examples.MINI_APP}, "state": {"frequency": 2}, "actions": []}


@pytest.fixture
def rig(tmp_path):
    db = Database(tmp_path / "apps.sqlite")
    config = settings.model_copy(update={"data_dir": tmp_path})
    harness = SimpleNamespace(db=db, settings=config)
    harness.ui = UIService(harness)
    runtime = SimpleNamespace(db=db, harness=harness)
    cid = db.create_conversation()["id"]
    return SimpleNamespace(runtime=runtime, harness=harness, cid=cid, mcp=build_mcp(runtime))


@pytest.mark.asyncio
async def test_tool_declares_ui_resource_served_as_mcp_app(rig):
    async with Client(rig.mcp) as client:
        tools = await client.list_tools()
        tool = next(t for t in tools if t.name == "open_ui_app")
        assert tool.meta["ui"]["resourceUri"] == VIEWER_URI
        # A view runs model-authored HTML: it must not be able to call any tool.
        assert all(t.meta["ui"]["visibility"] == ["model"] for t in tools), [t.name for t in tools]
        assert tool.annotations.readOnlyHint is True
        listed = {str(r.uri): r for r in await client.list_resources()}
        assert listed[VIEWER_URI].mimeType == "text/html;profile=mcp-app"
        [content] = await client.read_resource(VIEWER_URI)
    assert content.mimeType == "text/html;profile=mcp-app"
    html = content.text
    for method in ("ui/initialize", "ui/notifications/initialized", "ui/notifications/tool-result",
                   "ui/notifications/size-changed", "ui/resource-teardown", '"2026-01-26"', SURFACE_META):
        assert method in html, method
    # Self-contained under the host's default CSP: no external scripts, styles or fetches.
    assert not re.search(r"<script[^>]+src=|<link\b|fetch\(|XMLHttpRequest|WebSocket", html)


@pytest.mark.asyncio
async def test_saved_mini_app_reaches_view_but_not_model_text(rig):
    rig.harness.ui.store.commit(rig.cid, SINE)
    async with Client(rig.mcp) as client:
        result = await client.call_tool("open_ui_app", {"conversation_id": rig.cid, "surface_id": "sine-wave"})
    assert not result.is_error
    assert result.content[0].text == SINE["fallback"]
    assert result.structured_content == {"surface_id": "sine-wave", "revision": 1, "kind": "mini_app",
                                         "title": "Sine wave explorer", "state": {"frequency": 2}}
    surface = result.meta[SURFACE_META]
    assert surface["html"] == ui_examples.MINI_APP and surface["state"] == {"frequency": 2}
    assert "<script" not in result.content[0].text


@pytest.mark.asyncio
async def test_native_unknown_and_disabled_surfaces_degrade_to_text(rig):
    rig.harness.ui.store.commit(rig.cid, {"id": "taylor", "kind": "native", "title": "Taylor", "fallback": "Degree 2.",
                                          "nodes": [{"id": "m", "type": "markdown", "props": {"text": "x"}}]})
    rig.harness.ui.store.commit(rig.cid, SINE)
    async with Client(rig.mcp) as client:
        native = await client.call_tool("open_ui_app", {"conversation_id": rig.cid, "surface_id": "taylor"})
        assert not native.is_error and native.content[0].text == "Degree 2." and not (native.meta or {}).get(SURFACE_META)
        for args in ({"conversation_id": rig.cid, "surface_id": "missing"},
                     {"conversation_id": rig.cid, "surface_id": "../etc"},
                     {"conversation_id": "no-such-conversation", "surface_id": "sine-wave"}):
            assert (await client.call_tool("open_ui_app", args, raise_on_error=False)).is_error
        rig.harness.settings = rig.harness.settings.model_copy(update={"ui_mini_apps_enabled": False})
        rig.harness.ui.settings = rig.harness.settings
        disabled = await client.call_tool("open_ui_app", {"conversation_id": rig.cid, "surface_id": "sine-wave"}, raise_on_error=False)
    assert disabled.is_error and not (disabled.meta or {}).get(SURFACE_META)
