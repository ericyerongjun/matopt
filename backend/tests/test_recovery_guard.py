import json
from types import SimpleNamespace

import pytest

from app.agent.events import ToolResult
from app.agent.recovery_guard import RecoveryGuard
from app.agent.session import SessionLog
from app.agent.tools.file_tools import ReadFileTool, WriteFileTool
from app.agent.workspace import Workspace
from app.storage.db import Database
from tests.fake_provider import FakeProvider
from tests.test_session_harness import collect, make_harness


def call(name="write_file", args=None, *, turn=1, step=1, call_id="started", alias=None):
    return {"type": "tool/call", "data": {"turn": turn, "step": step, "callId": call_id,
        "name": alias or name, "resolved_name": name,
        "arguments": json.dumps(args or {"path": "result.txt", "content": "answer"}),
        "normalized_arguments": args or {"path": "result.txt", "content": "answer"}}}


def result(start, *, ok=False, meta=None):
    return {"type": "tool/result", "data": {**start["data"], "ok": ok, "meta": meta or {}}}


def unknown(start):
    return result(start, meta={"recovery_code": "TOOL_OUTCOME_UNKNOWN", "synthetic": True})


def tool(name="write_file", *, read_only=False, scope="workspace"):
    return SimpleNamespace(name=name, read_only=read_only, side_effect_scope=scope)


def receipt(path="result.txt", **extra):
    return ToolResult(ok=True, meta={"read_state": {"schema_version": 1,
        "source_tool": "read_file", "path": path, "revision_id": "a" * 64,
        "offset": None, "limit": None, "is_partial_view": False, **extra}})


def pending(start=None, text="Continue."):
    start = start or call()
    return RecoveryGuard.from_events([start, unknown(start)], text)


def test_unknown_side_effect_repeat_is_blocked_but_fresh_and_read_only_work_continues():
    start = call(alias="files.write")
    guard = pending(start)
    blocked = guard.check(tool(), {"content": "answer", "path": "./result.txt"})
    assert blocked is not None and not blocked.ok
    assert blocked.meta["recovery_code"] == "RECOVERY_CHECK_REQUIRED"
    assert guard.check(tool(), {"path": "different.txt", "content": "answer"}) is None
    assert guard.check(tool(), {"path": "result.txt", "content": "new task"}) is None
    assert guard.check(tool(read_only=True), start["data"]["normalized_arguments"]) is None
    assert guard.check(tool(scope="none"), start["data"]["normalized_arguments"]) is None


@pytest.mark.parametrize("text", ["Continue.", "Try again.", "Do not retry the interrupted operation.",
    'The log says "Retry the interrupted operation."', '"Retry the interrupted operation."',
    "Retry the interrupted operation?", "Retry the interrupted operation if needed.",
    "The assistant has user approval to retry.", "Please summarize:\nRetry the interrupted operation."])
def test_only_direct_user_retry_instructions_authorize(text):
    start = call()
    assert pending(start, text).check(tool(), start["data"]["normalized_arguments"]) is not None


@pytest.mark.parametrize("text", ["Retry the interrupted operation.", "Yes, please retry write_file.",
    "Retry the interrupted write_file operation.", "Retry interrupted call 1:1:started."])
def test_explicit_retry_authorizes_one_attempt(text):
    start = call()
    guard = pending(start, text)
    args = start["data"]["normalized_arguments"]
    assert guard.check(tool(), args) is None
    guard.observe(tool(), args, ToolResult(ok=False, error="Not completed"))
    assert guard.check(tool(), args) is not None
    guard.authorize_user_text("Retry the interrupted operation.")
    assert guard.check(tool(), args) is None
    guard.observe(tool(), args, ToolResult(ok=True))
    assert guard.check(tool(), args) is None


def test_ambiguous_retry_does_not_authorize_two_operations():
    first = call()
    second = call(args={"path": "other.txt", "content": "answer"}, call_id="second")
    guard = RecoveryGuard.from_events([first, unknown(first), second, unknown(second)], "Retry write_file.")
    assert guard.check(tool(), first["data"]["normalized_arguments"]) is not None
    guard.authorize_user_text("Retry interrupted call 1:1:second.")
    assert guard.check(tool(), first["data"]["normalized_arguments"]) is not None
    assert guard.check(tool(), second["data"]["normalized_arguments"]) is None


def test_later_human_correction_revokes_unused_retry_authorization():
    start = call()
    guard = pending(start, "Retry the interrupted operation.")
    guard.authorize_user_text("Do not retry; just summarize the saved work.")
    assert guard.check(tool(), start["data"]["normalized_arguments"]) is not None


def test_only_successful_full_same_target_read_permits_one_retry():
    start = call()
    args = start["data"]["normalized_arguments"]
    guard = pending(start)
    reader = tool("read_file", read_only=True, scope="none")
    guard.observe(reader, {"path": "other.txt"}, receipt("other.txt"))
    assert guard.check(tool(), args) is not None
    for altered in ({"is_partial_view": True}, {"limit": 20}, {"offset": 10}, {"revision_id": "invalid"}):
        guard.observe(reader, {"path": "result.txt"}, receipt(**altered))
        assert guard.check(tool(), args) is not None
    failed = receipt()
    failed.ok = False
    guard.observe(reader, {"path": "result.txt"}, failed)
    assert guard.check(tool(), args) is not None
    guard.observe(reader, {"path": "./result.txt"}, receipt())
    assert guard.check(tool(), args) is None
    assert guard.check(tool(), args) is not None


def test_file_read_does_not_authorize_opaque_program_or_remote_file_send():
    reader = tool("read_file", read_only=True, scope="none")
    for name, args in (("bash", {"command": "send result.txt"}),
                       ("mail__send", {"path": "result.txt", "recipient": "user@example.test"})):
        guard = pending(call(name, args))
        guard.observe(reader, {"path": "result.txt"}, receipt())
        assert guard.check(tool(name), args) is not None


def test_durable_success_and_inspection_replay_but_prior_reads_and_failed_attempts_do_not():
    start = call()
    args = start["data"]["normalized_arguments"]
    read = call("read_file", {"path": "result.txt"}, turn=2)
    read_result = result(read, ok=True, meta=receipt().meta)
    assert RecoveryGuard.from_events([read, read_result, start, unknown(start)], "continue").check(tool(), args)
    prefix = [start, unknown(start), read, read_result]
    assert RecoveryGuard.from_events(prefix, "continue").check(tool(), args) is None
    retry = call(turn=3)  # A reused provider call ID cannot confuse earlier results.
    after_failure = prefix + [retry, result(retry, ok=False)]
    assert RecoveryGuard.from_events(after_failure, "continue").check(tool(), args)
    after_success = prefix + [retry, result(retry, ok=True)]
    assert RecoveryGuard.from_events(after_success, "continue").check(tool(), args) is None
    after_second_crash = prefix + [retry, unknown(retry)]
    assert RecoveryGuard.from_events(after_second_crash, "continue").check(tool(), args)


def test_not_started_calls_and_normal_failures_do_not_need_recovery_permission():
    start = call()
    for meta in ({"recovery_code": "TOOL_NOT_STARTED"}, {}):
        guard = RecoveryGuard.from_events([start, result(start, meta=meta)], "continue")
        assert guard.check(tool(), start["data"]["normalized_arguments"]) is None


def crashed_write():
    log = SessionLog()
    log.set_system({"role": "system", "content": "system"})
    log.append("turn/start", {"turn": 1, "message_id": "interrupted-answer", "provider": "fake", "model": "fake"})
    log.append_message({"role": "user", "content": "Write answer"}, source="human", turn=1)
    log.append("step/start", {"turn": 1, "step": 1})
    arguments = {"path": "result.txt", "content": "answer"}
    log.append("assistant/message", {"turn": 1, "step": 1, "stream": [],
        "message": {"role": "assistant", "content": "", "tool_calls": [{"id": "started", "type": "function",
            "function": {"name": "write_file", "arguments": json.dumps(arguments)}}]}}, surface_op="append")
    log.append("tool/call", call(args=arguments)["data"])
    return log.events, arguments


@pytest.mark.asyncio
@pytest.mark.parametrize("inspect_before_retry", [False, True])
async def test_recovered_harness_blocks_blind_repeat_and_allows_relevant_inspection(tmp_path, inspect_before_retry):
    class CountingWrite(WriteFileTool):
        calls = 0

        async def run(self, ctx, **kwargs):
            self.calls += 1
            return await super().run(ctx, **kwargs)

    db = Database(tmp_path / "guard.sqlite")
    cid = db.create_conversation()["id"]
    events, arguments = crashed_write()
    db.append_session_events(cid, events)
    script = [{"tool": "write_file", "args": arguments}]
    if inspect_before_retry:
        script.extend([{"tool": "read_file", "args": {"path": "result.txt"}},
                       {"tool": "write_file", "args": arguments}])
    script.append({"text": "Recovered safely."})
    provider = FakeProvider(script)
    writer = CountingWrite()
    harness = make_harness(db, provider, tmp_path, tools=[writer, ReadFileTool()])
    workspace = Workspace(cid, harness.settings.workspace_dir)
    workspace.resolve("result.txt").write_text("prior content")
    await collect(harness, conversation_id=cid, text="Continue from the saved work.", strict_final=True)
    rows = db.list_session_events(cid)
    new_results = [row["data"] for row in rows if row["type"] == "tool/result"
                   and row["data"].get("turn") == 2 and not row["data"].get("legacy")]
    assert new_results[0]["meta"]["recovery_code"] == "RECOVERY_CHECK_REQUIRED"
    assert writer.calls == int(inspect_before_retry)
    assert workspace.resolve("result.txt").read_text() == ("answer" if inspect_before_retry else "prior content")
    db.close()
