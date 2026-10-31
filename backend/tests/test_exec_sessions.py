"""Background bash sessions (Codex unified_exec semantics)."""

import asyncio

import pytest

from app.agent.tools.base import ToolContext
from app.agent.tools.exec_sessions import MAX_SESSIONS, BashPollTool
from app.agent.tools.shell_tool import ShellTool
from app.agent.workspace import Workspace


@pytest.fixture
async def shell():
    tool = ShellTool()
    yield tool
    await tool.sessions.shutdown()


def make_ctx(tmp_path, cid="c"):
    async def emit(_):
        pass
    return ToolContext(cid, Workspace(cid, tmp_path), emit, asyncio.Event(), call_id="x")


@pytest.mark.asyncio
async def test_background_job_yields_then_polls_to_completion(tmp_path, shell):
    ctx = make_ctx(tmp_path)
    first = await shell(ctx, {"command": "echo start; sleep 1; echo done > out.txt; echo end",
                              "background": True, "yield_seconds": 0.3})
    assert first.ok and first.meta["running"]
    assert "Process running with session ID" in first.output and "start" in first.output
    sid = first.meta["session_id"]
    poll = BashPollTool(shell.sessions)
    result = None
    for _ in range(20):
        result = await poll(ctx, {"session_id": sid, "wait_seconds": 1})
        if not result.meta["running"]:
            break
    assert result.ok and result.meta["exit_code"] == 0 and "end" in result.output
    assert "start" not in result.output, "only output since the previous read"
    assert any(a.path == "out.txt" for a in result.artifacts)


@pytest.mark.asyncio
async def test_poll_sends_input_and_kill_stops(tmp_path, shell):
    ctx = make_ctx(tmp_path)
    started = await shell(ctx, {"command": "read line; echo got:$line; sleep 30", "background": True,
                                "yield_seconds": 0.25})
    sid = started.meta["session_id"]
    poll = BashPollTool(shell.sessions)
    answered = await poll(ctx, {"session_id": sid, "input": "hello\n", "wait_seconds": 1})
    assert "got:hello" in answered.output and answered.meta["running"]
    stopped = await poll(ctx, {"session_id": sid, "kill": True, "wait_seconds": 0})
    assert not stopped.ok and not stopped.meta["running"] and "stopped on request" in stopped.output


@pytest.mark.asyncio
async def test_sessions_are_per_conversation_and_bounded(tmp_path, shell):
    a, b = make_ctx(tmp_path, "a"), make_ctx(tmp_path, "b")
    started = await shell(a, {"command": "sleep 30", "background": True, "yield_seconds": 0.25})
    poll = BashPollTool(shell.sessions)
    other = await poll(b, {"session_id": started.meta["session_id"]})
    assert other.meta["code"] == "EXEC_SESSION_UNKNOWN"
    for _ in range(MAX_SESSIONS + 2):
        await shell(a, {"command": "true", "background": True, "yield_seconds": 0.25})
    assert len([s for s in shell.sessions._sessions.values() if s.conversation_id == "a"]) <= MAX_SESSIONS


@pytest.mark.asyncio
async def test_background_respects_exec_policy(tmp_path, shell):
    result = await shell(make_ctx(tmp_path), {"command": "sudo sleep 1", "background": True})
    assert not result.ok and result.meta["code"] == "EXEC_POLICY_FORBIDDEN"
