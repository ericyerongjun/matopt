"""Public run ownership, replay, restart, and control integration checks."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.agent.events import ev
from app.agent.harness import RunRequest
from app.agent.runs import RunConflict, RunManager, project_public_event
from app.api import chat as chat_api
from app.main import app
from app.storage.db import Database


PRIVATE = "PRIVATE_REPLAY_CANARY_7931"


class ControlledHarness:
    """A slow operation whose effect count detects accidental replay."""
    def __init__(self, db):
        self.db = db
        self.entered = asyncio.Event()
        self.finish = asyncio.Event()
        self.effects = 0
        self.calls = 0
        self.active = set()
        self.cancel = set()
        self.pauses = set()
        self.steers = []

    def is_running(self, cid):
        return cid in self.active

    def stop(self, cid):
        self.cancel.add(cid)
        return cid in self.active

    def pause(self, cid):
        if cid not in self.active:
            return False
        self.pauses.add(cid)
        return True

    def resume(self, cid):
        if cid not in self.active:
            return False
        self.pauses.discard(cid)
        return True

    def enqueue_steer(self, cid, text, skills=None, tools=None):
        if cid not in self.active:
            return False
        self.steers.append(text)
        return True

    async def run(self, req, *, on_claim=None):
        cid = req.conversation_id
        self.active.add(cid)
        if on_claim is not None:
            await on_claim(cid)
        self.calls += 1
        self.entered.set()
        mid = "m_" + req.run_id
        try:
            yield ev("message_start", message_id=mid, model="fake", arguments=PRIVATE)
            yield ev("reasoning_delta", delta=PRIVATE)
            yield ev("tool_progress", delta=PRIVATE)
            yield ev("text_delta", delta=PRIVATE)  # Untyped pre-final prose stays private.
            yield ev("progress", update={"id": "p1", "seq": 1, "text": "Reading the document.",
                                          "created_at": 1, "phase": "work", "reasoning": PRIVATE})
            self.effects += 1
            await self.finish.wait()
            if cid in self.cancel:
                yield ev("stopped", message_id=mid)
                return
            yield ev("final_start", message_id=mid, mode="streaming")
            yield ev("text_delta", delta="Verified answer.")
            content = {"status": "complete", "final_text": "Verified answer.", "final_ready": True,
                       "parts": [{"type": "reasoning", "text": PRIVATE}, {"type": "text", "text": PRIVATE}],
                       "work": {"status": "complete", "tool_count": 1, "steps": []}}
            self.db.add_message(cid, "assistant", content, message_id=mid)
            yield ev("message_end", message_id=mid, status="complete", content=content)
            yield ev("done", message_id=mid)
        finally:
            self.active.discard(cid)


@pytest.fixture
async def rig(tmp_path):
    db = Database(tmp_path / "runs.sqlite3")
    harness = ControlledHarness(db)
    manager = RunManager(db, harness, heartbeat_seconds=.02, stop_grace_seconds=.02)
    yield SimpleNamespace(db=db, harness=harness, manager=manager)
    await manager.shutdown()
    db.close()


async def replay(manager, run_id, after=0):
    return [event.to_dict() async for event in manager.subscribe(run_id, after) if event is not None]


async def started(rig):
    run = await rig.manager.start(RunRequest(None, "Compute the result."), "client-1")
    await rig.harness.entered.wait()
    # Let the producer commit the visible checkpoint and reach its operation.
    for _ in range(20):
        if rig.harness.effects:
            break
        await asyncio.sleep(0)
    return run


@pytest.mark.asyncio
async def test_disconnected_subscriber_does_not_cancel_and_replay_has_no_duplicate_effect(rig):
    run = await started(rig)
    iterator = rig.manager.subscribe(run["id"])
    first = await anext(iterator)
    second = await anext(iterator)
    await iterator.aclose()
    assert rig.manager.get(run["id"])["status"] == "running"
    assert rig.harness.is_running(run["conversation_id"])
    rig.harness.finish.set()
    await rig.manager.wait(run["id"])
    events = await replay(rig.manager, run["id"], second.seq)
    assert all(event["seq"] > second.seq for event in events)
    assert events[-1]["type"] == "done"
    assert rig.harness.calls == rig.harness.effects == 1
    all_events = [first.to_dict(), second.to_dict(), *events]
    assert [event["seq"] for event in all_events] == list(range(1, len(all_events) + 1))
    assert all(event["sequence"] == event["seq"] and event["run_id"] == run["id"] for event in all_events)
    assert PRIVATE not in json.dumps(all_events)


@pytest.mark.asyncio
async def test_idempotent_start_recovers_same_active_and_completed_run(rig):
    request = RunRequest(None, "Compute the result.")
    run = await rig.manager.start(request, "client-1")
    repeated = await rig.manager.start(request, "client-1")
    assert repeated["id"] == run["id"]
    assert len(rig.db.list_conversations()) == 1
    with pytest.raises(RunConflict, match="different request"):
        await rig.manager.start(RunRequest(None, "A different operation."), "client-1")
    with pytest.raises(RunConflict, match="already in progress"):
        await rig.manager.start(RunRequest(run["conversation_id"], "new turn"), "client-2")
    rig.harness.finish.set()
    await rig.manager.wait(run["id"])
    repeated = await rig.manager.start(RunRequest(run["conversation_id"], request.text), "client-1")
    assert repeated["status"] == "completed"
    assert rig.harness.calls == rig.harness.effects == 1


@pytest.mark.asyncio
async def test_stop_explicitly_cancels_an_unresponsive_operation(rig):
    run = await started(rig)
    stopped, run_id = await rig.manager.stop(run["conversation_id"])
    assert stopped and run_id == run["id"]
    await rig.manager.wait(run_id)
    assert not rig.harness.is_running(run["conversation_id"])
    events = await replay(rig.manager, run_id)
    assert events[-1]["type"] == "stopped"
    assert rig.manager.get(run_id)["status"] == "stopped"
    assert any(e["type"] == "control_ack" and e["action"] == "stop" for e in events)


@pytest.mark.asyncio
async def test_restart_seals_abandoned_run_without_repeating_work(tmp_path):
    path = tmp_path / "restart.sqlite3"
    original = Database(path)
    cid = original.create_conversation()["id"]
    original.create_run("r_old", cid, "request-hash", "original-request")
    original.append_run_event("r_old", "progress", {"update": {"text": "Saved work."}}, status="running")
    original.close()
    restored = Database(path)
    harness = ControlledHarness(restored)
    manager = RunManager(restored, harness)
    try:
        await manager.recover()
        first = await replay(manager, "r_old")
        await manager.recover()
        assert await replay(manager, "r_old") == first
        assert first[-1]["type"] == "error" and first[-1]["code"] == "interrupted"
        assert manager.get("r_old")["status"] == "interrupted"
        assert not manager.is_running(cid)
        assert harness.calls == harness.effects == 0
    finally:
        await manager.shutdown()
        restored.close()


@pytest.mark.asyncio
async def test_restart_preserves_completed_activity_and_settles_running_items(rig):
    cid = rig.db.create_conversation()["id"]
    rig.db.create_run("r_crashed", cid, "hash")
    rig.db.append_run_event("r_crashed", "message_start", {"message_id": "m_crashed"}, message_id="m_crashed")
    finished = {"id": "read", "kind": "tool", "phase": "work", "status": "completed", "seq": 2,
                "label": "Read document", "purpose": "Read the document", "started_at": 1, "finished_at": 2,
                "updated_at": 2, "outputs": [{"kind": "file", "label": "Notes", "path": "notes.txt"}]}
    running = {**finished, "id": "compute", "status": "running", "label": "Compute", "finished_at": None, "outputs": []}
    rig.db.append_run_event("r_crashed", "activity", {"item": finished}, status="running")
    rig.db.append_run_event("r_crashed", "activity", {"item": running})
    await rig.manager.recover()
    saved = rig.db.get_message("m_crashed")["content"]
    assert saved["recovered"] is True and saved["status"] == "stopped"
    items = {item["id"]: item for item in saved["work"]["items"]}
    assert items["read"]["status"] == "completed"
    assert items["read"]["outputs"][0]["path"] == "notes.txt"
    assert items["compute"]["status"] == "failed" and items["compute"]["finished_at"] is not None
    events = await replay(rig.manager, "r_crashed")
    canonical = next(event["content"] for event in events if event["type"] == "message_end")
    assert canonical["work"] == saved["work"]
    assert rig.harness.calls == 0


@pytest.mark.asyncio
async def test_restart_after_final_commit_replays_success_without_rerunning(rig):
    cid = rig.db.create_conversation()["id"]
    rig.db.create_run("r_final_commit", cid, "hash")
    content = {"final_text": "Saved answer", "final_ready": True, "status": "complete", "parts": []}
    rig.db.add_message(cid, "assistant", content, "m_saved")
    rig.db.append_run_event("r_final_commit", "message_start", {"message_id": "m_saved"}, status="running", message_id="m_saved")
    await rig.manager.recover()
    assert rig.manager.get("r_final_commit")["status"] == "completed"
    events = await replay(rig.manager, "r_final_commit")
    assert events[-1]["type"] == "done"
    assert next(event["content"]["parts"][0]["text"] for event in events if event["type"] == "message_end") == "Saved answer"
    assert rig.harness.calls == 0


@pytest.mark.asyncio
async def test_shutdown_before_driver_start_still_records_interrupted(rig):
    run = await rig.manager.start(RunRequest(None, "Never dispatch."), "shutdown-race")
    await rig.manager.shutdown()
    assert rig.manager.get(run["id"])["status"] == "interrupted"
    assert rig.harness.calls == 0


@pytest.mark.asyncio
async def test_graceful_shutdown_after_final_commit_keeps_success(rig):
    committed = asyncio.Event()

    async def run(req, *, on_claim=None):
        if on_claim is not None:
            await on_claim(req.conversation_id)
        yield ev("message_start", message_id="m_committed")
        content = {"status": "complete", "final_text": "Durably complete answer.", "final_ready": True, "parts": []}
        rig.db.add_message(req.conversation_id, "assistant", content, "m_committed")
        yield ev("message_end", message_id="m_committed", status="complete", content=content)
        committed.set()
        await asyncio.Event().wait()

    rig.harness.run = run
    item = await rig.manager.start(RunRequest(None, "Finish."), "shutdown-after-commit")
    await committed.wait()
    await rig.manager.shutdown()
    assert rig.manager.get(item["id"])["status"] == "completed"
    events = await replay(rig.manager, item["id"])
    assert events[-1]["type"] == "done"
    assert not any(event["type"] == "error" for event in events)


@pytest.mark.asyncio
async def test_deletion_drain_cannot_recreate_a_queued_conversation(rig):
    run = await rig.manager.start(RunRequest(None, "Queued work."), "delete-before-start")
    await rig.manager.cancel_and_wait(run["conversation_id"])
    assert rig.db.delete_conversation(run["conversation_id"])
    await asyncio.sleep(0)
    assert rig.db.get_conversation(run["conversation_id"]) is None
    assert rig.harness.calls == 0
    assert rig.db.get_run(run["id"]) is None


@pytest.mark.asyncio
async def test_controls_accepted_between_start_and_harness_claim(rig):
    run = await rig.manager.start(RunRequest(None, "Work."), "queued-controls")
    cid = run["conversation_id"]
    assert (await rig.manager.pause(cid))[0]
    assert (await rig.manager.steer(cid, "Use the uploaded data."))[0]
    await rig.harness.entered.wait()
    assert cid in rig.harness.pauses
    assert rig.harness.steers == ["Use the uploaded data."]
    assert (await rig.manager.resume(cid))[0]
    assert cid not in rig.harness.pauses
    rig.harness.finish.set()
    await rig.manager.wait(run["id"])
    assert not (await rig.manager.steer(cid, "Late steer."))[0]


@pytest.mark.asyncio
async def test_preclaim_steer_reaches_fast_real_producer_before_work(tmp_path):
    from tests.fake_provider import FakeProvider
    from tests.test_session_harness import make_harness

    db = Database(tmp_path / "fast-control.sqlite")
    provider = FakeProvider([{"text": "Updated response."}, {"text": "Updated response."}])
    harness = make_harness(db, provider, tmp_path)
    manager = RunManager(db, harness)
    try:
        run = await manager.start(RunRequest(None, "Hello."), "fast-control")
        assert (await manager.steer(run["conversation_id"], "Use Spanish in the answer."))[0]
        await manager.wait(run["id"])
        # This provider never yields to I/O. Waiting for message_start in the
        # SSE consumer delivered the instruction after its producer had ended.
        assert any("[Steer] Use Spanish in the answer." in str(message.get("content", ""))
                   for message in provider.calls[0])
        rows = db.list_session_events(run["conversation_id"])
        assert any(row["type"] == "user/message" and row["data"].get("source") == "steer" for row in rows)
        assert manager.get(run["id"])["status"] == "completed"
    finally:
        await manager.shutdown()
        await harness.wait_side_tasks()
        db.close()


@pytest.mark.asyncio
async def test_finished_producer_does_not_accept_controls_while_consumer_drains(rig):
    run = await started(rig)
    # The public consumer may still be draining after the producer releases
    # its conversation. Only genuinely pre-claim controls can be queued.
    rig.harness.active.discard(run["conversation_id"])
    assert not (await rig.manager.steer(run["conversation_id"], "Too late."))[0]
    assert not (await rig.manager.pause(run["conversation_id"]))[0]
    rig.harness.finish.set()
    await rig.manager.wait(run["id"])


@pytest.mark.asyncio
async def test_subscription_heartbeat_and_late_replay(rig):
    run = await started(rig)
    last = rig.manager.get(run["id"])["last_seq"]
    tail = rig.manager.subscribe(run["id"], last)
    assert await anext(tail) is None
    rig.harness.finish.set()
    events = [event.to_dict() async for event in tail if event]
    assert events[-1]["type"] == "done"
    assert await replay(rig.manager, run["id"], events[-1]["seq"]) == []


@pytest.mark.asyncio
async def test_api_snapshot_replay_idempotency_and_controls(rig, monkeypatch):
    rt = SimpleNamespace(db=rig.db, harness=rig.harness, runs=rig.manager,
                         providers=SimpleNamespace(resolve=lambda *_: None), kernels=SimpleNamespace(peek=lambda _: None))
    monkeypatch.setattr(chat_api, "get_runtime", lambda: rt)
    run = await started(rig)
    cid = run["conversation_id"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/chat/{cid}/run")
        assert response.status_code == 200
        assert response.json()["run"]["id"] == run["id"]
        assert "request_hash" not in response.text and "client_run_id" not in response.text
        assert (await client.post(f"/api/chat/{cid}/pause")).json()["accepted"]
        assert (await client.post(f"/api/chat/{cid}/resume")).json()["accepted"]
        assert (await client.post(f"/api/chat/{cid}/steer", json={"message": "Check the units."})).json()["accepted"]
        rig.harness.finish.set()
        await rig.manager.wait(run["id"])
        response = await client.get(f"/api/runs/{run['id']}/events?after=1", headers={"Last-Event-ID": "2"})
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert min(event["seq"] for event in events) == 3
        assert events[-1]["type"] == "done"
        repeated = await client.post("/api/chat/stream", json={"message": "Compute the result.", "client_run_id": "client-1"})
        assert repeated.status_code == 200
        assert repeated.headers["x-run-id"] == run["id"]
        assert rig.harness.effects == 1
        assert (await client.get("/api/runs/not-a-run")).status_code == 404
        assert (await client.get(f"/api/runs/{run['id']}/events", headers={"Last-Event-ID": "bad"})).status_code == 400
        def unavailable_provider(*_):
            raise ValueError("Provider temporarily unavailable")
        rt.providers.resolve = unavailable_provider
        repeated = await client.post("/api/chat/stream", json={"message": "Compute the result.", "client_run_id": "client-1"})
        assert repeated.status_code == 200
        assert rig.harness.effects == 1


@pytest.mark.asyncio
async def test_http_generator_disconnect_only_closes_observer(rig):
    run = await started(rig)
    request = SimpleNamespace(is_disconnected=_disconnected)
    response = chat_api._stream_response(rig.manager, run["id"], request)
    assert [chunk async for chunk in response.body_iterator] == []
    assert rig.harness.is_running(run["conversation_id"])
    rig.harness.finish.set()
    await rig.manager.wait(run["id"])
    assert rig.manager.get(run["id"])["status"] == "completed"


async def _disconnected():
    return True


def test_public_event_allowlist_and_native_preview_validation():
    for name in ("reasoning_delta", "tool_result", "tool_call_delta", "tool_progress", "plan", "phase", "verification", "agent_message"):
        assert project_public_event(ev(name, text=PRIVATE, arguments=PRIVATE)) is None
    projected = project_public_event(ev("usage", total_tokens=10, reasoning=PRIVATE, completion_tokens=float("nan")))
    assert projected.data == {"total_tokens": 10}
    preview = {"id": "view", "title": "Result", "fallback": "Result", "status": "preview",
               "nodes": [{"id": "body", "type": "markdown", "props": {"text": "Visible result"}}]}
    assert project_public_event(ev("ui_patch", surface=preview)) is not None
    assert project_public_event(ev("ui_patch", surface={**preview, "private": PRIVATE})) is None
    mini = {"id": "app", "kind": "mini_app", "title": "App", "fallback": "App", "status": "preview", "app": {"html": "<p>Complete HTML</p>"}}
    assert project_public_event(ev("ui_start", surface=mini)) is None
    mini["status"] = "committed"
    assert project_public_event(ev("ui_commit", surface=mini)) is not None
