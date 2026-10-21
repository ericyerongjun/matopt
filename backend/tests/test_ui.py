"""Behavioral checks for negotiation, durable revisions and explicit computation."""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.agent.events import ToolResult
from app.agent.harness import AgentHarness, RunRequest, _compact_ui_history
from app.agent.session.repair import recovered_content
from app.agent.tools.base import ToolContext, ToolRegistry
from app.agent.tools.ui_tools import CreateUIAppTool, RegisterUIActionTool, RenderUITool
from app.agent.workspace import Workspace
from app.config import settings
from app.main import app
from app.storage.db import Database
from app.ui.compiler import UIStreamCompiler, complete_fields
from app.ui.contracts import UISurface
from app.ui.service import validate_input_schema
from app.ui.store import UIConflict, UINotFound
from tests.fake_provider import FakeProvider, FakeRegistry


def native(**overrides):
    return {"id": "taylor", "title": "Taylor approximation", "fallback": "Approximation at degree 2.",
            "nodes": [{"id": "degree", "type": "slider", "props": {"stateKey": "degree", "label": "Degree", "value": 2, "min": 1, "max": 10}},
                      {"id": "equation", "type": "equation", "props": {"latex": "1+x+x^2/2"}}],
            "state": {"degree": 2}, **overrides}


class NoKernels:
    def peek(self, cid):
        return None


class SpyProvider(FakeProvider):
    def __init__(self, script):
        super().__init__(script)
        self.catalogs = []

    async def stream(self, messages, tools, options):
        self.catalogs.append(tools)
        async for item in super().stream(messages, tools, options):
            yield item


@pytest.fixture
def rig(tmp_path):
    db = Database(tmp_path / "ui.sqlite")
    provider = SpyProvider([])
    config = settings.model_copy(update={"data_dir": tmp_path, "skill_auto_activate": False, "lambda_enabled": False})
    harness = AgentHarness(db, NoKernels(), config, FakeRegistry(provider), ToolRegistry([RenderUITool(), CreateUIAppTool(), RegisterUIActionTool()]))
    conv = db.create_conversation()
    async def emit(_):
        pass
    ctx = ToolContext(conv["id"], Workspace(conv["id"], config.workspace_dir), emit, asyncio.Event(), state={
        "ui_service": harness.ui, "ui_client_version": 1, "ui_enabled": True, "ui_native": True, "ui_mini_apps": True, "ui_message_id": "m_surface",
    })
    yield SimpleNamespace(db=db, harness=harness, service=harness.ui, cid=conv["id"], provider=provider, config=config, ctx=ctx)
    db.close()


async def run(rig, script, version=1):
    # The work result and final presentation are separate provider requests.
    final = [{"text": script[-1]["text"]}] if script and script[-1].get("text") else []
    rig.provider.script = [*script, *final]
    events = [event.to_dict() async for event in rig.harness.run(RunRequest(conversation_id=rig.cid, text="Explain Taylor", ui_version=version), internal_events=True)]
    await rig.harness.wait_side_tasks()
    return events


def register(rig, *, code="def compute(values):\n    return {'fallback': 'Computed.'}", input_files=None):
    return rig.service.register(rig.ctx, "taylor", "recompute", "Recompute", {
        "type": "object", "properties": {"degree": {"type": "integer", "minimum": 1, "maximum": 10}}, "required": ["degree"],
    }, code, input_files=input_files)


def setup_action(rig):
    manifest = register(rig)
    surface = rig.service.commit(rig.ctx, native(actions=[manifest]))
    request = {"action_id": "recompute", "revision": surface["revision"], "values": {"degree": 4}, "idempotency_key": "click-1"}
    return surface, request


class FakePython:
    def __init__(self, *, fail=False, block=False):
        self.calls = 0
        self.fail = fail
        self.block = block
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, ctx, code, timeout):
        self.calls += 1
        self.started.set()
        if self.block:
            await self.release.wait()
        if self.fail:
            return ToolResult(ok=False, error="Numerical calculation failed")
        tree = ast.parse(code)
        dest = next(ast.literal_eval(node.args[0]) for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_MatoptUIPath")
        Path(dest).write_text(json.dumps({"state": {"degree": 4}, "fallback": "Approximation at degree 4."}))
        return ToolResult(ok=True, output="computed")


def test_contract_rejects_executable_props_duplicate_ids_nonfinite():
    UISurface.model_validate(native())
    for bad in (
        native(nodes=[{"id": "x", "type": "markdown", "props": {"text": "x", "onClick": "alert(1)"}}]),
        native(nodes=[{"id": "x", "type": "markdown", "props": {"text": "x"}}] * 2),
        native(state={"degree": float("nan")}), native(kind="mini_app"), native(app={"html": "<script>alert(1)</script>"}),
    ):
        with pytest.raises(ValueError):
            UISurface.model_validate(bad)


def test_compiler_only_emits_complete_valid_nodes(rig):
    compiler = UIStreamCompiler(rig.service.store, rig.cid)
    prefix = '{"id":"example","title":"Example","fallback":"Readable explanation","nodes":['
    assert compiler.feed("render_ui", "call", prefix + '{"id":"one","type":"markdown","props":{"text":"unfinished') is None
    complete = prefix + '{"id":"one","type":"markdown","props":{"text":"Complete"}},'
    preview = compiler.feed("render_ui", "call", complete)
    assert preview.type == "ui_start" and preview.data["surface"]["status"] == "preview"
    assert preview.data["surface"]["actions"] == []
    assert compiler.feed("render_ui", "call", complete) is None
    assert compiler.feed("create_ui_app", "app", complete) is None
    changed = complete + '{"id":"two","type":"equation","props":{"latex":"x^2"}}'
    assert compiler.feed("render_ui", "call", changed).type == "ui_patch"
    assert complete_fields('{"id":"x", "nodes":[{"id":') == {"id": "x", "nodes": []}


@pytest.mark.asyncio
async def test_negotiation_stream_commit_and_compact_replay(rig):
    raw_args = json.dumps(native(), indent=2)
    events = await run(rig, [{"tool": "render_ui", "raw_args": raw_args}, {"text": "It improves near zero."}])
    assert {"ui_start", "ui_commit", "message_end"}.issubset({e["type"] for e in events})
    assert any(s["function"]["name"] == "render_ui" for s in rig.provider.catalogs[0])
    commit = next(e["surface"] for e in events if e["type"] == "ui_commit")
    final = next(e for e in events if e["type"] == "message_end")
    assert next(p["surface"] for p in final["content"]["parts"] if p["type"] == "ui") == commit
    assert rig.service.store.get(rig.cid, "taylor") == commit
    logged = rig.db.list_session_events(rig.cid)
    assert any(e["type"] == "ui/surface" for e in logged)
    raw = next(e["data"]["arguments"] for e in logged if e["type"] == "tool/call")
    assert "nodes" in json.loads(raw)
    active_call = next(c for m in rig.provider.calls[1] for c in m.get("tool_calls", []))
    # Removing nodes here made the live model repeatedly repair successful UI.
    assert active_call["function"]["arguments"] == raw_args
    receipt = json.loads(next(m["content"] for m in rig.provider.calls[1] if m.get("role") == "tool"))
    assert receipt["ok"] and receipt["status"] == "committed" and receipt["node_count"] == 2
    assert receipt["node_types"] == ["equation", "slider"]
    assert len(rig.provider.calls) == 3 and final["content"]["stats"]["tool_calls"] == 1
    assert rig.provider.catalogs[-1] is None  # Final rendering cannot call tools.
    await run(rig, [{"text": "A follow-up."}])
    assert "Current interactive output state" in rig.provider.calls[-2][-2]["content"]
    assert "Current interactive output state" not in rig.provider.calls[-2][0]["content"]
    prior = rig.provider.calls[-2]
    assert not any(m.get("tool_calls") for m in prior)
    summary = next(m["content"] for m in prior if "Earlier interactive tool results" in (m.get("content") or ""))
    assert "committed" in summary and "intentionally omitted" in summary
    assert "1+x+x^2/2" not in summary  # source remains in storage, not a malformed replay call


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args,source_key", [
    ("create_ui_app", {"id": "sim", "title": "Simulation", "fallback": "A small simulation.",
                       "html": "<main><p>Complete app</p></main>"}, "html"),
    ("register_ui_action", {"surface_id": "sim", "action_id": "compute", "label": "Compute",
                            "input_schema": {"type": "object", "properties": {}},
                            "code": "def compute(values):\n    return {'fallback': 'Calculated.'}"}, "code"),
])
async def test_ui_definition_stays_exact_until_turn_finishes(rig, tool, args, source_key):
    raw_args = json.dumps(args, indent=2)
    await run(rig, [{"tool": tool, "raw_args": raw_args}, {"text": "Finished."}])
    active_call = next(c for m in rig.provider.calls[1] for c in m.get("tool_calls", []))
    assert active_call["function"]["arguments"] == raw_args
    await run(rig, [{"text": "Next answer."}])
    history = rig.provider.calls[-2]
    assert not any(m.get("tool_calls") for m in history)
    summary = next(m["content"] for m in history if "Earlier interactive tool results" in (m.get("content") or ""))
    assert args[source_key] not in summary and tool in summary


def test_ui_history_summary_keeps_mixed_batches_valid_and_current_calls_exact():
    def call(cid, name, args):
        return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}

    ui_call = call("ui", "render_ui", native())
    other_call = call("read", "read_file", {"path": "data.csv"})
    current = {"role": "assistant", "tool_calls": [call("current", "render_ui", native())]}
    history = [
        {"role": "system", "content": "System"}, {"role": "user", "content": "Earlier request"},
        {"role": "assistant", "content": "Working.", "tool_calls": [ui_call, other_call]},
        {"role": "tool", "tool_call_id": "ui", "content": "[error] Invalid nodes. Repair once."},
        {"role": "tool", "tool_call_id": "read", "content": "CSV data"},
        {"role": "user", "content": "Current request"}, current,
        {"role": "tool", "tool_call_id": "current", "content": '{"status":"committed"}'},
    ]
    _compact_ui_history(history)
    assert history[2]["tool_calls"] == [other_call]
    assert history[3]["tool_call_id"] == "read"
    assert history[4]["role"] == "assistant" and "[error] Invalid nodes" in history[4]["content"]
    assert history[-2] == current
    incomplete = history[-2:]
    incomplete.pop()
    incomplete.append({"role": "user", "content": "After interrupted call"})
    before = json.loads(json.dumps(incomplete))
    _compact_ui_history(incomplete)
    assert incomplete == before


@pytest.mark.asyncio
async def test_text_clients_and_independent_feature_flags(rig):
    await run(rig, [{"text": "Text answer."}], version=None)
    assert not rig.provider.catalogs[0]
    rig.config.ui_native_enabled = False
    await run(rig, [{"text": "Text answer."}])
    catalog = {s["function"]["name"] for s in rig.provider.catalogs[-2] or []}
    assert "render_ui" not in catalog and "create_ui_app" in catalog
    rig.config.ui_mini_apps_enabled = False
    await run(rig, [{"text": "Text answer."}])
    assert not rig.provider.catalogs[-1]


@pytest.mark.asyncio
async def test_hidden_name_cannot_enable_ui_and_repair_once(rig):
    rig.provider.script = [{"tool": "render_ui", "args": native()}, {"text": "Fallback."}]
    events = [e.to_dict() async for e in rig.harness.run(RunRequest(conversation_id=rig.cid, text="/render-ui explain", tools=["render_ui"]), internal_events=True)]
    await rig.harness.wait_side_tasks()
    assert not any(e["type"] == "ui_commit" for e in events)
    bad = native(nodes=[{"id": "bad", "type": "slider", "props": {"stateKey": "degree"}}])
    events = await run(rig, [{"tool": "render_ui", "args": bad}, {"tool": "render_ui", "args": bad}, {"tool": "render_ui", "args": native()}, {"text": "Fallback."}])
    assert not any(e["type"] == "ui_commit" for e in events)
    errors = [e for e in events if e["type"] == "tool_result" and not e["ok"]]
    assert "Repair" in errors[0]["error"] and "repair limit" in errors[-1]["error"].lower()


def test_revision_state_validation_and_immutable_history(rig):
    first = rig.service.commit(rig.ctx, native())
    changed = rig.service.store.patch_state(rig.cid, "taylor", 1, {"degree": 3})
    assert changed["revision"] == 2 and changed["state"]["degree"] == 3
    assert changed["computed_state"] == {"degree": 2}
    assert rig.service.store.get(rig.cid, "taylor", 1) == first
    with pytest.raises(UIConflict):
        rig.service.store.patch_state(rig.cid, "taylor", 1, {"degree": 4})
    for invalid in ({"degree": 100}, {"degree": "code"}, {"undeclared": 1}):
        with pytest.raises(ValueError):
            rig.service.store.patch_state(rig.cid, "taylor", 2, invalid)
    assert rig.service.store.get(rig.cid, "taylor")["revision"] == 2


def test_handler_schema_and_action_scope(rig):
    for schema in ({"type": "object", "$ref": "https://example.com/schema"}, {"type": "array"}):
        with pytest.raises(ValueError):
            validate_input_schema(schema)
    with pytest.raises(ValueError):
        register(rig, code="print('no function')")
    _, request = setup_action(rig)
    other = rig.db.create_conversation()["id"]
    with pytest.raises(UINotFound):
        rig.service.prepare_action(other, "taylor", request)
    with pytest.raises(UINotFound):
        rig.service.prepare_action(rig.cid, "taylor", {**request, "action_id": "unknown"})
    with pytest.raises(ValueError):
        rig.service.prepare_action(rig.cid, "taylor", {**request, "values": {"degree": 4, "code": "evil"}})


@pytest.mark.asyncio
async def test_action_idempotency_revision_and_shared_lease(rig):
    _, request = setup_action(rig)
    fake = rig.service.python = FakePython()
    lease = rig.service.prepare_action(rig.cid, "taylor", request)
    assert rig.harness.is_running(rig.cid)
    for next_request in (request, {**request, "idempotency_key": "another"}):
        with pytest.raises(UIConflict):
            rig.service.prepare_action(rig.cid, "taylor", next_request)
    events = [e.to_dict() async for e in rig.service.actions(lease)]
    assert not rig.harness.is_running(rig.cid) and fake.calls == 1
    assert events[-2]["type"] == "ui_commit" and events[-1]["type"] == "done"
    assert rig.service.store.get(rig.cid, "taylor")["revision"] == 2
    assert rig.service.store.get(rig.cid, "taylor")["computed_state"] == {"degree": 4}
    assert rig.service.prepare_action(rig.cid, "taylor", request)["events"] == events[-2:]
    assert fake.calls == 1
    for next_request in ({**request, "values": {"degree": 5}}, {**request, "idempotency_key": "new"}):
        with pytest.raises(UIConflict):
            rig.service.prepare_action(rig.cid, "taylor", next_request)


@pytest.mark.asyncio
async def test_failure_and_disconnect_preserve_old_result(rig):
    original, request = setup_action(rig)
    fake = rig.service.python = FakePython(fail=True)
    events = [e.to_dict() async for e in rig.service.actions(rig.service.prepare_action(rig.cid, "taylor", request))]
    assert events[-1]["type"] == "ui_error" and rig.service.store.get(rig.cid, "taylor") == original
    assert rig.service.prepare_action(rig.cid, "taylor", request)["status"] == "error" and fake.calls == 1
    fake = rig.service.python = FakePython(block=True)
    request = {**request, "idempotency_key": "disconnect"}
    iterator = rig.service.actions(rig.service.prepare_action(rig.cid, "taylor", request))
    assert (await anext(iterator)).type == "status"
    await fake.started.wait()
    await iterator.aclose()
    assert not rig.harness.is_running(rig.cid) and rig.service.store.get(rig.cid, "taylor") == original
    assert rig.service.prepare_action(rig.cid, "taylor", request)["status"] == "error"


@pytest.mark.asyncio
async def test_missing_input_has_clear_error(rig):
    path = rig.ctx.workspace.save_upload("data.csv", b"x\n1\n")
    manifest = register(rig, input_files=["uploads/data.csv"])
    surface = rig.service.commit(rig.ctx, native(actions=[manifest]))
    path.unlink()
    request = {"action_id": "recompute", "revision": surface["revision"], "values": {"degree": 4}, "idempotency_key": "missing"}
    events = [e.to_dict() async for e in rig.service.actions(rig.service.prepare_action(rig.cid, "taylor", request))]
    assert "Required input is missing" in events[-1]["error"]


def test_restart_recovery_and_orphan_hydration(rig):
    original, request = setup_action(rig)
    rig.service.store.reserve_receipt(rig.cid, "taylor", request["idempotency_key"], request)
    rig.service.store.recover_pending()
    assert rig.service.prepare_action(rig.cid, "taylor", request)["status"] == "interrupted"
    rig.db.add_message(rig.cid, "assistant", {"parts": [], "status": "stopped"}, "m_surface")
    hydrated = rig.service.store.hydrate(rig.cid, rig.db.list_messages(rig.cid))
    assert hydrated[0]["content"]["parts"] == [{"type": "ui", "surface": original}]
    recovered = recovered_content([{"type": "ui/surface", "data": {"surface_id": "taylor", "revision": 1, "fallback": "Explanation"}}], {})
    assert recovered["parts"][0]["surface"]["id"] == "taylor"


@pytest.mark.asyncio
async def test_capabilities_state_action_export_and_busy_api(rig, monkeypatch):
    from app.agent.runs import RunManager
    import app.api.ui as ui_api
    import app.api.conversations as conversations_api
    import app.api.tools as tools_api
    import app.api.chat as chat_api
    rt = SimpleNamespace(db=rig.db, harness=rig.harness, runs=RunManager(rig.db, rig.harness), tools=rig.harness.tools, providers=FakeRegistry(rig.provider), kernels=rig.harness.kernels,
                         workspace=lambda cid: Workspace(cid, rig.config.workspace_dir))
    for module in (ui_api, conversations_api, tools_api, chat_api):
        monkeypatch.setattr(module, "get_runtime", lambda: rt)
    original, request = setup_action(rig)
    rig.service.python = FakePython()
    rig.db.add_message(rig.cid, "assistant", {"parts": [{"type": "ui", "surface": original}]}, "m_surface")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        cap = (await client.get("/api/ui/capabilities")).json()
        assert cap["version"] == 1 and cap["native"] and cap["mini_apps"]
        assert (await client.get("/api/tools")).json()["tools"] == []
        path = f"/api/conversations/{rig.cid}/ui/taylor"
        assert (await client.get(path)).json()["surface"] == original
        assert (await client.patch(path + "/state", json={"revision": 1, "state": {"degree": 3}})).status_code == 200
        assert (await client.patch(path + "/state", json={"revision": 1, "state": {"degree": 4}})).status_code == 409
        request["revision"] = 2
        response = await client.post(path + "/actions", json=request)
        assert response.status_code == 200 and response.headers["x-accel-buffering"] == "no"
        assert '"type": "ui_commit"' in response.text and '"type": "done"' in response.text
        replay = await client.post(path + "/actions", json=request)
        assert '"type": "ui_commit"' in replay.text and rig.service.python.calls == 1
        exported = await client.get(f"/api/conversations/{rig.cid}/export?format=markdown")
        assert "Approximation at degree 4" in exported.text and '"degree": 4' in exported.text
        lease = rig.harness.claim_external_run(rig.cid)
        try:
            assert (await client.patch(path + "/state", json={"revision": 3, "state": {"degree": 5}})).status_code == 409
            assert (await client.post(f"/api/conversations/{rig.cid}/kernel/restart")).status_code == 409
            assert (await client.post("/api/chat/stream", json={"conversation_id": rig.cid, "message": "Busy"})).status_code == 409
        finally:
            rig.harness.release_external_run(rig.cid, lease)

@pytest.mark.asyncio
async def test_repeated_cancellation_does_not_reappend_durable_rows(rig, monkeypatch):
    """Disconnect and response teardown may both cancel one checkpoint waiter."""
    import threading
    log = await rig.harness._open_session(rig.cid)
    log.append("ui/state", {"surface_id": "taylor", "revision": 1, "state": {}}, ignorable=True)
    entered, release = threading.Event(), threading.Event()
    original = rig.db.append_session_events
    def blocked(cid, events):
        entered.set()
        assert release.wait(3)
        original(cid, events)
    monkeypatch.setattr(rig.db, "append_session_events", blocked)
    task = asyncio.create_task(log.flush())
    while not entered.is_set():
        await asyncio.sleep(.001)
    task.cancel()
    await asyncio.sleep(.01)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(.03)
    # Opening/history polling must not retry the rows already committed by the thread.
    await log.flush()
    assert len(rig.db.list_session_events(rig.cid)) == 1

@pytest.mark.asyncio
async def test_legacy_history_survives_ui_only_ledger_and_concurrent_reads(rig, monkeypatch):
    from app.agent.runs import RunManager
    rig.config.diagnostics_enabled = True
    import app.api.ui as ui_api
    import app.api.conversations as conversations_api
    original, request = setup_action(rig)
    user = rig.db.add_message(rig.cid, "user", {"text": "Original question", "attachments": []})
    answer = rig.db.add_message(rig.cid, "assistant", {"parts": [{"type": "text", "text": "Original answer"}, {"type": "ui", "surface": original}], "status": "complete"}, "m_surface")
    rt = SimpleNamespace(db=rig.db, harness=rig.harness, runs=RunManager(rig.db, rig.harness), kernels=rig.harness.kernels, workspace=lambda cid: rig.ctx.workspace)
    monkeypatch.setattr(ui_api, "get_runtime", lambda: rt)
    monkeypatch.setattr(conversations_api, "get_runtime", lambda: rt)
    rig.service.python = FakePython()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        path = f"/api/conversations/{rig.cid}"
        before = await client.get(path)
        assert [m["id"] for m in before.json()["messages"]] == [user["id"], answer["id"]]
        await client.patch(path + "/ui/taylor/state", json={"revision": 1, "state": {"degree": 4}})
        request["revision"] = 2
        response, *reads = await asyncio.gather(client.post(path + "/ui/taylor/actions", json=request), *[client.get(path + suffix) for suffix in ("", "/trajectory", "/usage", "/events")])
        assert all(r.status_code == 200 for r in [response, *reads])
        await run(rig, [{"text": "Follow-up answer"}])
        messages = (await client.get(path)).json()["messages"]
        assert messages[0]["content"]["text"] == "Original question"
        assert messages[1]["content"]["parts"][0]["text"] == "Original answer"
        assert messages[1]["content"]["parts"][1]["surface"]["revision"] == 3
        assert messages[-1]["content"]["parts"][0]["text"] == "Follow-up answer"
        history = rig.provider.calls[-2]
        assert any(m.get("content") == "Original question" for m in history)
        assert any("Original answer" in str(m.get("content")) for m in history)

@pytest.mark.asyncio
async def test_error_without_preview_persists_fallback_and_repair_removes_it(rig):
    bad = native(nodes=[{"id": "bad", "type": "slider", "props": {"stateKey": "degree"}}])
    events = await run(rig, [{"tool": "render_ui", "args": bad}, {"text": ""}])
    assert not any(e["type"] == "ui_start" for e in events)
    parts = next(e["content"]["parts"] for e in events if e["type"] == "message_end")
    assert any(p.get("text") == bad["fallback"] for p in parts)
    events = await run(rig, [{"tool": "render_ui", "args": bad}, {"tool": "render_ui", "args": native()}, {"text": ""}])
    parts = next(e["content"]["parts"] for e in events if e["type"] == "message_end")
    assert any(p.get("type") == "ui" for p in parts)
    assert not any(p.get("text") == bad["fallback"] for p in parts)
    partial = [{"type": "ui/fallback", "data": {"surface_id": "x", "fallback": "Readable fallback"}}]
    assert recovered_content(partial, {})["parts"] == [{"type": "text", "text": "Readable fallback"}]
    partial.append({"type": "ui/surface", "data": {"surface_id": "x", "revision": 1, "fallback": "Fixed"}})
    assert [p["type"] for p in recovered_content(partial, {})["parts"]] == ["ui"]


@pytest.mark.asyncio
async def test_repair_budget_is_per_surface_revision(rig):
    bad = native(nodes=[{"id": "bad", "type": "slider", "props": {"stateKey": "degree"}}])
    other_bad = {**bad, "id": "independent"}
    other_good = native(id="independent")
    events = await run(rig, [{"tool": "render_ui", "args": bad}, {"tool": "render_ui", "args": bad}, {"tool": "render_ui", "args": other_bad}, {"tool": "render_ui", "args": other_good}, {"text": ""}])
    commits = [e["surface"]["id"] for e in events if e["type"] == "ui_commit"]
    assert commits == ["independent"]


def test_select_state_uses_renderer_option_identity(rig):
    surface = native(nodes=[{"id": "choice", "type": "select", "props": {"stateKey": "choice", "label": "Choice", "options": [{"label": "One"}, {"label": "Numeric", "value": 1}], "value": "One"}}], state={"choice": "One"})
    committed = rig.service.commit(rig.ctx, surface)
    assert committed["state"]["choice"] == "One"
    with pytest.raises(ValueError):
        rig.service.store.patch_state(rig.cid, "taylor", 1, {"choice": True})
