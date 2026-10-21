"""The kernel MCP server exposes saved mini-apps as standard MCP Apps views."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastmcp")

from fastmcp import Client

from app.agent.events import ToolResult
from app.agent.harness import AgentHarness
from app.agent.mcp.sandbox import build_mcp
from app.agent.tools.base import ToolContext, ToolRegistry
from app.agent.workspace import Workspace
from app.config import settings
from app.storage.db import Database
from app.ui.mcp_apps import SURFACE_META, VIEWER_URI, surface_token
from tests.fake_provider import FakeProvider, FakeRegistry

_EXAMPLES = Path(__file__).resolve().parents[2] / "scripts" / "ui_examples.py"
_spec = importlib.util.spec_from_file_location("matopt_ui_examples", _EXAMPLES)
ui_examples = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ui_examples)

SINE = {"id": "sine-wave", "kind": "mini_app", "title": "Sine wave explorer",
        "fallback": "A sine wave with amplitude 1 and adjustable frequency. x is in radians.",
        "app": {"html": ui_examples.MINI_APP}, "state": {"frequency": 2}}
SCHEMA = {"type": "object", "properties": {"frequency": {"type": "integer", "minimum": 1, "maximum": 8}}, "required": ["frequency"]}


class NoKernels:
    def peek(self, cid):
        return None


class FakePython:
    """Writes the handler result where the service's wrapper would."""
    def __init__(self, *, fail=False):
        self.calls = 0
        self.fail = fail

    async def run(self, ctx, code, timeout):
        self.calls += 1
        if self.fail:
            return ToolResult(ok=False, error="Numerical calculation failed")
        dest = re.search(r"_MatoptUIPath\('([^']+)'\)", code).group(1)
        values = json.loads(re.search(r"_matopt_ui_json\.loads\('([^']*)'\)", code).group(1))
        Path(dest).write_text(json.dumps({"state": values, "fallback": f"Frequency {values['frequency']} rad⁻¹."}))
        return ToolResult(ok=True, output="computed")


@pytest.fixture
def rig(tmp_path):
    db = Database(tmp_path / "apps.sqlite")
    config = settings.model_copy(update={"data_dir": tmp_path, "skill_auto_activate": False, "lambda_enabled": False})
    harness = AgentHarness(db, NoKernels(), config, FakeRegistry(FakeProvider([])), ToolRegistry([]))
    cid = db.create_conversation()["id"]
    ctx = ToolContext(cid, Workspace(cid, config.workspace_dir), lambda _: None, asyncio.Event(), state={})
    manifest = harness.ui.register(ctx, "sine-wave", "recompute", "Recompute", SCHEMA,
                                   "def compute(values):\n    return {'state': values}")
    harness.ui.commit(ctx, {**SINE, "actions": [manifest]})
    harness.ui.python = FakePython()
    runtime = SimpleNamespace(db=db, harness=harness)
    yield SimpleNamespace(db=db, harness=harness, service=harness.ui, cid=cid, config=config,
                          token=surface_token(tmp_path, cid, "sine-wave"), mcp=build_mcp(runtime))
    db.close()


def args(rig, **extra):
    return {"conversation_id": rig.cid, "surface_id": "sine-wave", "token": rig.token, **extra}


@pytest.mark.asyncio
async def test_tool_declares_ui_resource_and_audiences(rig):
    async with Client(rig.mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
        assert tools["open_ui_app"].meta["ui"]["resourceUri"] == VIEWER_URI
        assert tools["open_ui_app"].annotations.readOnlyHint is True
        # A view runs model-authored HTML: it may call only the two surface-scoped tools.
        app_callable = {name for name, t in tools.items() if "app" in t.meta["ui"]["visibility"]}
        assert app_callable == {"save_ui_state", "run_ui_action"}
        assert all(t.meta["ui"]["visibility"] == ["app"] for name, t in tools.items() if name in app_callable)
        assert all(t.meta["ui"]["visibility"] == ["model"] for name, t in tools.items() if name not in app_callable)
        listed = {str(r.uri): r for r in await client.list_resources()}
        assert listed[VIEWER_URI].mimeType == "text/html;profile=mcp-app"
        [content] = await client.read_resource(VIEWER_URI)
    assert content.mimeType == "text/html;profile=mcp-app"
    html = content.text
    for method in ("ui/initialize", "ui/notifications/initialized", "ui/notifications/tool-result", "tools/call",
                   "ui/notifications/size-changed", "ui/resource-teardown", "ui/message", "ui/update-model-context",
                   '"2026-01-26"', SURFACE_META, "save_ui_state", "run_ui_action"):
        assert method in html, method
    # Self-contained under the host's default CSP: no external scripts, styles or fetches.
    assert not re.search(r"<script[^>]+src=|<link\b|fetch\(|XMLHttpRequest|WebSocket", html)


@pytest.mark.asyncio
async def test_saved_mini_app_reaches_view_but_not_model_text(rig):
    async with Client(rig.mcp) as client:
        result = await client.call_tool("open_ui_app", {"conversation_id": rig.cid, "surface_id": "sine-wave"})
    assert not result.is_error
    assert result.content[0].text == SINE["fallback"]
    assert result.structured_content == {"surface_id": "sine-wave", "revision": 1, "kind": "mini_app",
                                         "title": "Sine wave explorer", "state": {"frequency": 2}}
    surface = result.meta[SURFACE_META]
    assert surface["html"] == ui_examples.MINI_APP and surface["state"] == surface["computed_state"] == {"frequency": 2}
    assert surface["token"] == rig.token and surface["conversation_id"] == rig.cid
    assert [a["id"] for a in surface["actions"]] == ["recompute"] and surface["actions"][0]["input_schema"]["properties"]
    assert rig.token not in json.dumps([c.model_dump() for c in result.content]) + json.dumps(result.structured_content)


@pytest.mark.asyncio
async def test_native_unknown_and_disabled_surfaces_degrade_to_text(rig):
    rig.service.store.commit(rig.cid, {"id": "taylor", "kind": "native", "title": "Taylor", "fallback": "Degree 2.",
                                       "nodes": [{"id": "m", "type": "markdown", "props": {"text": "x"}}]})
    async with Client(rig.mcp) as client:
        native = await client.call_tool("open_ui_app", {"conversation_id": rig.cid, "surface_id": "taylor"})
        assert not native.is_error and native.content[0].text == "Degree 2." and not (native.meta or {}).get(SURFACE_META)
        for bad in ({"conversation_id": rig.cid, "surface_id": "missing"},
                    {"conversation_id": rig.cid, "surface_id": "../etc"},
                    {"conversation_id": "no-such-conversation", "surface_id": "sine-wave"}):
            assert (await client.call_tool("open_ui_app", bad, raise_on_error=False)).is_error
        rig.service.settings = rig.config.model_copy(update={"ui_mini_apps_enabled": False})
        disabled = await client.call_tool("open_ui_app", {"conversation_id": rig.cid, "surface_id": "sine-wave"}, raise_on_error=False)
    assert disabled.is_error and not (disabled.meta or {}).get(SURFACE_META)


@pytest.mark.asyncio
async def test_app_tools_require_the_surface_token(rig):
    rig.service.store.commit(rig.cid, {"id": "other", "kind": "mini_app", "title": "Other", "fallback": "Other.",
                                       "app": {"html": "<p>x</p>"}, "state": {"n": 1}})
    async with Client(rig.mcp) as client:
        for bad in ({"token": ""}, {"token": "0" * 64}, {"surface_id": "other"}, {"conversation_id": "x"}):
            saved = await client.call_tool("save_ui_state", {**args(rig, revision=1, state={"frequency": 3}), **bad}, raise_on_error=False)
            assert saved.is_error and "not authorized" in saved.content[0].text
            ran = await client.call_tool("run_ui_action", {**args(rig, action_id="recompute", revision=1, values={"frequency": 3},
                                                                  idempotency_key="k1"), **bad}, raise_on_error=False)
            assert ran.is_error and "not authorized" in ran.content[0].text
    assert rig.service.store.get(rig.cid, "sine-wave")["revision"] == 1 and rig.service.python.calls == 0


@pytest.mark.asyncio
async def test_save_state_validates_revision_and_busy_lease(rig):
    async with Client(rig.mcp) as client:
        saved = await client.call_tool("save_ui_state", args(rig, revision=1, state={"frequency": 5}))
        assert saved.structured_content == {"surface_id": "sine-wave", "revision": 2, "state": {"frequency": 5}}
        stale = await client.call_tool("save_ui_state", args(rig, revision=1, state={"frequency": 6}), raise_on_error=False)
        assert stale.is_error and stale.structured_content == {"code": "UI_CONFLICT", "revision": 2, "state": {"frequency": 5}}
        undeclared = await client.call_tool("save_ui_state", args(rig, revision=2, state={"command": "rm"}), raise_on_error=False)
        assert undeclared.is_error and undeclared.structured_content["code"] == "UI_STATE_INVALID"
        lease = rig.harness.claim_external_run(rig.cid)
        try:
            busy = await client.call_tool("save_ui_state", args(rig, revision=2, state={"frequency": 7}), raise_on_error=False)
        finally:
            rig.harness.release_external_run(rig.cid, lease)
        assert busy.is_error and busy.structured_content["code"] == "UI_CONFLICT" and busy.structured_content["revision"] == 2
    assert rig.service.store.get(rig.cid, "sine-wave")["state"] == {"frequency": 5}


@pytest.mark.asyncio
async def test_action_recomputes_once_and_replays_by_idempotency_key(rig):
    call = args(rig, action_id="recompute", revision=1, values={"frequency": 4}, idempotency_key="click-1")
    async with Client(rig.mcp) as client:
        first = await client.call_tool("run_ui_action", call)
        assert not first.is_error and first.content[0].text == "Frequency 4 rad⁻¹."
        assert first.structured_content["status"] == "complete" and first.structured_content["revision"] == 2
        surface = first.meta[SURFACE_META]
        assert surface["state"] == surface["computed_state"] == {"frequency": 4}
        assert "token" not in surface and "html" not in surface  # mini-app scripts may observe later messages
        replay = await client.call_tool("run_ui_action", call)
        assert replay.structured_content == first.structured_content and rig.service.python.calls == 1
        reused = await client.call_tool("run_ui_action", {**call, "values": {"frequency": 5}}, raise_on_error=False)
        assert reused.is_error and reused.structured_content["code"] == "UI_CONFLICT"
        stale = await client.call_tool("run_ui_action", {**call, "idempotency_key": "click-2"}, raise_on_error=False)
        assert stale.is_error and stale.structured_content == {"code": "UI_CONFLICT", "revision": 2, "state": {"frequency": 4}}
        out_of_range = await client.call_tool("run_ui_action", {**call, "revision": 2, "values": {"frequency": 99}, "idempotency_key": "click-3"},
                                              raise_on_error=False)
        assert out_of_range.is_error and out_of_range.structured_content["code"] == "UI_ACTION_INVALID"
        rig.service.python = FakePython(fail=True)
        failed = await client.call_tool("run_ui_action", {**call, "revision": 2, "idempotency_key": "click-4"}, raise_on_error=False)
        assert failed.is_error and failed.structured_content == {"code": "UI_ACTION_FAILED", "revision": 2}
    assert not rig.harness.is_running(rig.cid)
    assert rig.service.store.get(rig.cid, "sine-wave")["computed_state"] == {"frequency": 4}
