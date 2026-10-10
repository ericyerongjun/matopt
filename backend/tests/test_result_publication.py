"""Public partial results through the real harness, projection and run journal."""
import asyncio
from copy import deepcopy
import json

import pytest

from app.agent.events import ev
from app.agent.harness import RunRequest
from app.agent.presentation import public_content
from app.agent.results import ResultPublisher, public_result, result_parts
from app.agent.runs import RunManager, project_public_event
from app.agent.tools.result_tool import PublishResultTool
from app.storage.db import Database
from tests.test_agent_activity import rig, Capture, REASONING, INTERNAL_TEXT, STDOUT
from tests.test_runs import ControlledHarness, replay


def block(**changes):
    return {"id": "m_result:result:answer", "revision": 1, "kind": "markdown", "status": "provisional",
            "content": "The result is **42**.", "evidence_refs": [], "created_at": 1, **changes}


def publish(content="The result is **42**.", **args):
    return {"tool": "publish_result", "args": {"id": "answer", "content": content, **args},
            "text": INTERNAL_TEXT, "reasoning": REASONING}


@pytest.mark.asyncio
async def test_partial_precedes_slow_work_and_final_manifest_is_authoritative(rig):
    rig.harness.tools.register(PublishResultTool())
    rig.provider.script = [publish(), {"tool": "python", "args": {"job": "slow"}}, {"text": "Final result: **42**."}]
    capture = Capture(rig.harness, RunRequest(rig.cid, "Calculate two results and explain them.")).start()
    try:
        await asyncio.wait_for(rig.compute.started_slow.wait(), 5)
        partial = next(e for e in capture.events if e["type"] == "result_block")
        assert partial["block"]["content"] == "The result is **42**."
        assert partial["block"]["status"] == "provisional"
        assert not any(e["type"] in {"text_delta", "final_start", "message_end"} for e in capture.events)
        rig.compute.release_slow.set()
        await capture.finish()
        end = next(e for e in capture.events if e["type"] == "message_end")
        parts = end["content"]["parts"]
        assert parts[0]["text"] == "Final result: **42**."
        result = next(p["block"] for p in parts if p["type"] == "result_block")
        assert result["id"] == partial["block"]["id"] and result["revision"] == 2
        assert result["status"] == "superseded"
        saved = rig.db.get_message(end["message_id"])["content"]
        assert public_content(saved) == end["content"] == public_content(end["content"])
        assert 1 <= len(end["content"]["work"]["milestones"]) <= 12
        assert end["content"]["work"]["tool_count"] == 1
        assert all(canary not in json.dumps(capture.events) for canary in (REASONING, INTERNAL_TEXT, STDOUT))
    finally:
        await capture.close()


@pytest.mark.asyncio
async def test_strict_mode_never_publishes_provisional_content_even_after_reload(rig):
    rig.harness.tools.register(PublishResultTool())
    rig.provider.script = [publish("PROVISIONAL_ONLY_CANARY"), {"text": "The final answer is 42."}]
    capture = Capture(rig.harness, RunRequest(rig.cid, "Solve this task.", strict_final=True)).start()
    await capture.finish()
    assert not any(e["type"] == "result_block" for e in capture.events)
    assert "PROVISIONAL_ONLY_CANARY" not in json.dumps(capture.events)
    end = next(e for e in capture.events if e["type"] == "message_end")
    saved = rig.db.get_message(end["message_id"])["content"]
    assert saved["strict_final"] is True
    assert "PROVISIONAL_ONLY_CANARY" not in json.dumps(public_content(saved))


@pytest.mark.asyncio
async def test_stop_retains_only_explicit_provisional_answer(rig):
    rig.harness.tools.register(PublishResultTool())
    rig.provider.script = [publish(), {"tool": "python", "args": {"job": "slow"}}]
    capture = Capture(rig.harness, RunRequest(rig.cid, "Compute these results.")).start()
    try:
        await asyncio.wait_for(rig.compute.started_slow.wait(), 5)
        assert rig.harness.stop(rig.cid)
        rig.compute.release_slow.set()
        await capture.finish()
        end = next(e for e in capture.events if e["type"] == "message_end")
        assert end["status"] == "stopped"
        assert end["content"]["parts"][0]["block"]["status"] == "provisional"
        assert not any(p["type"] == "text" for p in end["content"]["parts"])
        assert INTERNAL_TEXT not in json.dumps(end)
    finally:
        await capture.close()


@pytest.mark.asyncio
async def test_applied_steer_supersedes_old_partial_before_new_publication(rig):
    rig.harness.tools.register(PublishResultTool())
    rig.provider.script = [publish("Old scope."), {"tool": "python", "args": {"job": "slow"}},
                           publish("Updated scope."), {"text": "Final updated result."}]
    capture = Capture(rig.harness, RunRequest(rig.cid, "Compute these results.")).start()
    try:
        await asyncio.wait_for(rig.compute.started_slow.wait(), 5)
        assert rig.harness.enqueue_steer(rig.cid, "Use the updated scope.")
        rig.compute.release_slow.set()
        await capture.finish()
        blocks = [e["block"] for e in capture.events if e["type"] == "result_block"]
        assert [b["revision"] for b in blocks] == [1, 2, 3, 4]
        assert [b["status"] for b in blocks] == ["provisional", "superseded", "provisional", "superseded"]
        assert blocks[1]["content"] == "Old scope." and blocks[2]["content"] == "Updated scope."
    finally:
        await capture.close()


@pytest.mark.asyncio
async def test_unknown_evidence_is_rejected_and_observed_source_is_allowed(rig):
    rig.harness.tools.register(PublishResultTool())
    rig.provider.script = [publish(evidence_refs=["https://invented.example/source"]),
                           {"tool": "web_search", "args": {"query": "current source"}},
                           publish(evidence_refs=["https://example.test/current"]), {"text": "Final supported result."}]
    capture = Capture(rig.harness, RunRequest(rig.cid, "Look up and explain this current result.")).start()
    await capture.finish()
    blocks = [e["block"] for e in capture.events if e["type"] == "result_block"]
    assert len(blocks) == 2 and blocks[0]["revision"] == 1
    assert blocks[0]["evidence_refs"] == ["https://example.test/current"]
    assert "invented.example" not in json.dumps(capture.events)


@pytest.mark.parametrize("change", [{"revision": True}, {"revision": 0}, {"created_at": float("nan")},
    {"created_at": -1}, {"evidence_refs": [False]}, {"content": "x" * 50001}, {"kind": "reasoning"}])
def test_invalid_blocks_never_cross_public_projection(change):
    value = block(**change)
    assert public_result(value) is None
    assert project_public_event(ev("result_block", message_id="m_result", block=value)) is None
    assert not public_content({"parts": [{"type": "result_block", "block": value}], "final_text": ""})["parts"]


def test_revision_tombstones_and_nested_allowlist():
    values = [block(content="Old"), block(revision=3, status="withdrawn", content="PRIVATE_WITHDRAWN"), block(revision=2)]
    parts = result_parts([{"type": "result_block", "block": value} for value in values])
    assert len(parts) == 1 and parts[0]["block"]["content"] == ""
    projected = project_public_event(ev("result_block", message_id="m_result", block=block(reasoning=REASONING), private=REASONING))
    assert REASONING not in json.dumps(projected.to_dict())
    assert project_public_event(ev("result_block", message_id="another", block=block())) is None


@pytest.mark.asyncio
async def test_publisher_keeps_revision_when_persistence_fails_and_rejects_closed_phase():
    parts, emitted = [], []
    fail = True
    async def record(value):
        if fail:
            raise OSError("Journal unavailable")
        emitted.append(deepcopy(value))
    publisher = ResultPublisher("m_result", parts, publish=record, observed_refs=lambda: set(), allowed=lambda: True)
    with pytest.raises(OSError):
        await publisher.record("answer", "A useful answer.")
    assert not parts
    fail = False
    assert (await publisher.record("answer", "A useful answer."))["revision"] == 1
    await publisher.finalize()
    with pytest.raises(ValueError, match="unavailable"):
        await publisher.record("answer", "Too late.")


@pytest.mark.asyncio
async def test_restart_retains_latest_public_results_without_rerunning(tmp_path):
    path = tmp_path / "restart.sqlite"
    original = Database(path)
    cid = original.create_conversation()["id"]
    original.create_run("r_partial", cid, "hash")
    original.append_run_event("r_partial", "message_start", {"message_id": "m_result"}, message_id="m_result", status="running")
    for value in [block(), block(revision=2, content="Updated result."), block()]:
        original.append_run_event("r_partial", "result_block", {"message_id": "m_result", "block": value})
    original.close()
    restored = Database(path)
    harness = ControlledHarness(restored)
    manager = RunManager(restored, harness)
    try:
        await manager.recover()
        events = await replay(manager, "r_partial")
        end = next(e for e in events if e["type"] == "message_end")
        assert end["status"] == "stopped"
        assert end["content"]["parts"][0]["block"] == block(revision=2, content="Updated result.")
        assert public_content(restored.get_message("m_result")["content"]) == end["content"]
        assert harness.calls == 0
        await manager.recover()
        assert events == await replay(manager, "r_partial")
    finally:
        await manager.shutdown()
        restored.close()
