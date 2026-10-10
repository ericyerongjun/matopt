import math

import pytest

from app.agent.session import SessionLog, derive_messages, derive_request
from app.agent.session.events import decode_source_seqs, encode_source_seqs
from app.agent.session.repair import open_turn_closers
from app.agent.session.stream import AssistantStreamAccumulator, expand_stream
from app.storage.db import Database


def seed_log():
    log = SessionLog()
    log.set_system({"role": "system", "content": "system"})
    return log


@pytest.mark.parametrize("value", [{1: "not a string key"}, {"value": (1, 2)},
                                  {"value": math.nan}, {"value": math.inf}, {"value": object()},
                                  {"value": 2 ** 53}])
def test_rejects_lossy_json_before_append(value):
    log = seed_log()
    before = log.events
    with pytest.raises((ValueError, TypeError)):
        log.append("plan/write", value)
    assert log.events == before


def test_detached_snapshots_and_contiguous_sequences():
    log = seed_log()
    data = {"steps": [{"content": "first"}]}
    log.append("plan/write", data)
    data["steps"][0]["content"] = "mutated"
    snapshot = log.events
    snapshot[-1]["data"]["steps"][0]["content"] = "mutated again"
    assert log.events[-1]["data"]["steps"][0]["content"] == "first"
    with pytest.raises(ValueError, match="contiguous"):
        log.append("plan/write", {}, seq=9)
    assert len(log.events) == 2


def test_positional_replacement_retains_shadowed_history_and_protects_head():
    log = seed_log()
    for text in ("a", "b", "c"):
        log.append_message({"role": "user", "content": text})
    log.append("user/message", {"message": {"role": "user", "content": "ab"}},
               surface_op={"op": "replace", "startSeq": 1, "endSeq": 2}, source_event_seqs=[1, 2])
    # Surface order is now [0,4,3]; numeric range comparison would reject this.
    log.append("user/message", {"message": {"role": "user", "content": "abc"}},
               surface_op={"op": "replace", "startSeq": 4, "endSeq": 3}, source_event_seqs=[4, 3])
    assert derive_messages(log.events) == [{"role": "system", "content": "system"},
                                           {"role": "user", "content": "abc"}]
    assert [e["data"]["message"]["content"] for e in log.events[:4]] == ["system", "a", "b", "c"]
    log.set_system({"role": "system", "content": "updated"})
    assert len(derive_messages(log.events)) == 2
    before = log.events
    with pytest.raises(ValueError, match="protected"):
        log.append("user/message", {"message": {"role": "user", "content": "bad"}},
                   surface_op={"op": "replace", "startSeq": 6, "endSeq": 5}, source_event_seqs=[6, 5])
    with pytest.raises(ValueError, match="every shadowed"):
        log.append("system/message", {"message": {"role": "system", "content": "bad"}},
                   surface_op={"op": "replace", "startSeq": 6, "endSeq": 6}, source_event_seqs=[0])
    assert log.events == before


def test_unknown_events_require_explicit_ignorable_and_cannot_poison_surface():
    log = seed_log()
    with pytest.raises(ValueError, match="Unknown non-ignorable"):
        log.append("future/thing", {})
    log.append("future/thing", {"opaque": "okay"}, ignorable=True)
    assert derive_messages(log.events) == [{"role": "system", "content": "system"}]
    with pytest.raises(ValueError, match="Log-only"):
        log.append("plan/write", {}, surface_op="append")
    with pytest.raises(ValueError, match="require append"):
        log.append("user/message", {"message": {"role": "user", "content": "missing marker"}})


def test_source_range_encoding_roundtrips_and_rejects_future_references():
    seqs = [1, 2, 3, 8, 9, 11]
    assert encode_source_seqs(seqs) == [[1, 3], 8, 9, 11]
    assert decode_source_seqs(encode_source_seqs(seqs), 12) == seqs
    with pytest.raises(ValueError, match="earlier"):
        decode_source_seqs([12], 12)
    with pytest.raises(ValueError, match="duplicates"):
        decode_source_seqs([[1, 3], 2], 12)


def test_compact_stream_is_lossless_for_interleaved_delta_and_usage_samples():
    chunks = [
        (1000.25, {"type": "reasoning-delta", "index": 0, "text": "think"}),
        (1000.5, {"type": "reasoning-delta", "index": 0, "text": " again"}),
        (1000.75, {"type": "text-delta", "index": 0, "text": "a"}),
        (1001.0, {"type": "usage", "usage": {"prompt_tokens": 4, "completion_tokens": 1}}),
        (1001.25, {"type": "tool-call-delta", "index": 0, "id": "c", "name": "read_file", "argumentsDelta": '{ "p"'}),
        (1001.5, {"type": "tool-call-delta", "index": 0, "id": "c", "name": "read_file", "argumentsDelta": ': "x" }'}),
        (1001.75, {"type": "finish", "reason": "tool_calls"}),
    ]
    stream = AssistantStreamAccumulator()
    for timestamp, chunk in chunks:
        stream.push(timestamp, chunk)
    packed = stream.finish()
    assert len(packed) == 5
    assert expand_stream(packed) == [{"time": timestamp, "chunk": chunk} for timestamp, chunk in chunks]


def crashed_prefix():
    log = seed_log()
    log.append("turn/start", {"turn": 1, "message_id": "recovered-message", "provider": "fake", "model": "fake"})
    log.append_ui_message("user", {"text": "work", "attachments": []}, "original-user")
    log.append_message({"role": "user", "content": "work"}, source="human", turn=1)
    log.append("step/start", {"turn": 1, "step": 1})
    log.append("request/header", {"turn": 1, "step": 1, "attempt": 1,
                                  "header": {"config": {"provider": "fake", "model": "fake"}}})
    log.append("assistant/message", {"turn": 1, "step": 1, "stream": [], "text": "doing work",
        "message": {"role": "assistant", "content": "doing work", "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": "write_file", "arguments": '{ "path": "x" }'}}
            for call_id in ("started", "not-started")]}}, surface_op="append")
    log.append("tool/call", {"turn": 1, "step": 1, "callId": "started", "name": "write_file", "arguments": '{ "path": "x" }'})
    return log


@pytest.mark.asyncio
async def test_crash_repair_appends_deterministic_conservative_results_and_ui_view(tmp_path):
    db = Database(tmp_path / "log.sqlite")
    cid = db.create_conversation()["id"]
    prefix = crashed_prefix().events
    db.append_session_events(cid, prefix)
    expected = open_turn_closers(prefix)
    recovered = await SessionLog.open(db, cid)
    assert recovered.events == prefix + expected
    results = [e for e in expected if e["type"] == "tool/result"]
    assert [e["data"]["meta"]["recovery_code"] for e in results] == ["TOOL_OUTCOME_UNKNOWN", "TOOL_NOT_STARTED"]
    assert "Do not retry blindly" in results[0]["data"]["message"]["content"]
    assert "Retry it if it is still needed" in results[1]["data"]["message"]["content"]
    assert all(e["time"] == prefix[-1]["time"] for e in expected)
    assert recovered.events[-1]["data"]["reason"] == {"kind": "interrupted"}
    assert [e["type"] for e in expected] == ["tool/result", "tool/result", "step/end", "ui/message", "turn/end"]
    messages = db.list_messages(cid)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[-1]["content"]["recovered"] is True
    assert messages[-1]["content"]["parts"][0]["text"] == "doing work"
    assert (await SessionLog.open(db, cid)).events == recovered.events
    db.close()


def test_atomic_batch_validation_does_not_leave_a_partial_row_or_ui_projection(tmp_path):
    db = Database(tmp_path / "log.sqlite")
    cid = db.create_conversation()["id"]
    log = seed_log()
    log.append_ui_message("user", {"text": "valid"}, "ui-id")
    bad = {"seq": 99, "time": 1.0, "type": "plan/write", "data": {}}
    with pytest.raises(ValueError, match="contiguous"):
        db.append_session_events(cid, log.events + [bad])
    assert db.list_session_events(cid) == []
    assert db.list_messages(cid) == []
    db.append_session_events(cid, log.events)
    assert db.list_messages(cid)[0]["id"] == "ui-id"
    with pytest.raises(ValueError, match="contiguous"):
        db.append_session_events(cid, log.events)
    assert len(db.list_session_events(cid)) == 2
    db.delete_conversation(cid)
    assert db.list_session_events(cid) == []
    db.close()


def test_request_reconstruction_uses_historical_prefix_not_latest_surface():
    log = crashed_prefix()
    header_seq = next(e["seq"] for e in log.events if e["type"] == "request/header")
    request = derive_request(log.events[:header_seq + 1])
    assert request == {"messages": [{"role": "system", "content": "system"}, {"role": "user", "content": "work"}],
                       "tools": None, "config": {"provider": "fake", "model": "fake"}}
    log.append_batch(open_turn_closers(log.events))
    assert request["messages"] != derive_messages(log.events)
