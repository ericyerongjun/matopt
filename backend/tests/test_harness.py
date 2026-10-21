import asyncio
import dataclasses
import json
from typing import Optional

import pytest

import app.agent.harness as harness_module
from app.agent.events import ToolResult
from app.agent.harness import (
    REPEAT_LIMIT,
    AgentHarness,
    RunRequest,
    TurnState,
    _expand_parts,
    _failure_fingerprint,
    _next_consecutive_failures,
    _parse_arguments,
    _record_failures,
)
from app.agent.tools.base import Tool, ToolRegistry
from app.agent.tools.file_tools import ListFilesTool
from app.agent.tools.python_tool import PythonTool
from app.agent.tools.router import ToolRouter
from app.agent.tools.web_search import WebFetchTool, WebSearchTool
from app.llm.base import ToolCallSpec
from app.agent.kernel import KernelPool
from app.agent.workspace import Workspace
from app.config import settings
from app.runtime import build_tools
from app.storage.db import Database
from tests.fake_provider import FakeProvider, FakeRegistry


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "t.sqlite3")


def make_harness(db, provider):
    kernels = KernelPool(lambda cid: Workspace(cid, settings.workspace_dir))
    return AgentHarness(db, kernels, settings, FakeRegistry(provider), build_tools()), kernels


async def collect(harness, req):
    # Tool-policy tests use exact-output mode; live final synthesis has its own
    # controlled streaming tests instead of consuming this tool-loop script.
    req = dataclasses.replace(req, strict_final=True)
    events = []
    async for ev in harness.run(req, internal_events=True):
        events.append(ev.to_dict())
    await harness.wait_side_tasks()
    return events


@pytest.mark.asyncio
async def test_tool_loop_persists_parts(db):
    provider = FakeProvider([
        {"reasoning": "let me compute", "tool": "python", "args": {"code": "print(6*7)\n6*7"}},
        {"text": "The answer is $\\boxed{42}$."},
    ])
    harness, kernels = make_harness(db, provider)
    try:
        events = await collect(harness, RunRequest(conversation_id=None, text="What is 6*7?"))
    finally:
        await kernels.shutdown_all()
    types = [e["type"] for e in events]
    assert types[0] == "conversation" and types[1] == "message_start"
    assert "reasoning_delta" in types and "tool_call_start" in types and "tool_call_ready" in types and "tool_result" in types
    assert types[-1] == "done" and types[-2] == "message_end"
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["ok"] and "42" in result["output"]
    cid = events[0]["conversation_id"]
    msgs = db.list_messages(cid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    parts = msgs[1]["content"]["parts"]
    assert [p["type"] for p in parts] == ["reasoning", "tool_call", "text"]
    assert msgs[1]["content"]["status"] == "complete"
    assert msgs[1]["content"]["usage"]["total_tokens"] == 30
    # second call saw the tool message
    second = provider.calls[1]
    assert second[-1]["role"] == "tool" and "42" in second[-1]["content"]
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"][0]["function"]["name"] == "python"
    assert "reasoning_content" in second[-2]  # deepseek-style replay


@pytest.mark.asyncio
async def test_replay_and_collapse(db):
    provider = FakeProvider([
        {"tool": "list_files", "args": {}},
        {"text": "first answer"},
        {"text": "second answer"},
    ])
    harness, kernels = make_harness(db, provider)
    try:
        e1 = await collect(harness, RunRequest(conversation_id=None, text="hi"))
        cid = e1[0]["conversation_id"]
        await collect(harness, RunRequest(conversation_id=cid, text="again"))
    finally:
        await kernels.shutdown_all()
    history = provider.calls[-1]
    # Each turn is a harness <turn-context> message followed by the user's text.
    roles = [m["role"] for m in history]
    assert roles[0] == "system" and roles[1] == "user" and history[1]["content"].startswith("<turn-context>")
    assert roles[2] == "user" and history[2]["content"] == "hi"
    assert roles[3] == "assistant" and "tool_calls" in history[3]
    assert roles[4] == "tool"
    assert roles[5] == "assistant" and history[5]["content"] == "first answer"
    assert roles[6] == "user" and history[6]["content"].startswith("<turn-context>")
    assert roles[7] == "user" and history[7]["content"] == "again"


@pytest.mark.asyncio
async def test_bad_arguments_and_unknown_tool(db):
    provider = FakeProvider([
        {"tool": "nope", "args": {"x": 1}},
        {"text": "ok"},
    ])
    harness, kernels = make_harness(db, provider)
    try:
        events = await collect(harness, RunRequest(conversation_id=None, text="x"))
    finally:
        await kernels.shutdown_all()
    res = next(e for e in events if e["type"] == "tool_result")
    assert not res["ok"] and "Unknown tool" in res["error"]
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_stop_mid_run(db):
    provider = FakeProvider([
        {"tool": "python", "args": {"code": "import time\nfor i in range(50):\n    time.sleep(0.2)"}},
        {"text": "should not reach"},
    ])
    harness, kernels = make_harness(db, provider)
    events = []
    try:
        async def stopper():
            while not events or not any(e["type"] == "tool_call_ready" for e in events):
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.8)
            cid = events[0]["conversation_id"]
            assert harness.stop(cid)
        task = asyncio.create_task(stopper())
        async for ev in harness.run(RunRequest(conversation_id=None, text="loop"), internal_events=True):
            events.append(ev.to_dict())
        await task
    finally:
        await kernels.shutdown_all()
    assert events[-1]["type"] == "stopped"
    cid = events[0]["conversation_id"]
    msgs = db.list_messages(cid)
    assert msgs[-1]["content"]["status"] == "stopped"
    assert any(p["type"] == "tool_call" for p in msgs[-1]["content"]["parts"])


def test_parse_arguments():
    assert _parse_arguments('{"a": 1}') == ({"a": 1}, None)
    assert _parse_arguments("") == ({}, None)
    obj, err = _parse_arguments('{"a": 1} trailing')
    assert obj == {"a": 1} and err is None
    obj, err = _parse_arguments("[1,2]")
    assert obj is None and err


def test_expand_parts_shapes():
    from tests.fake_provider import FakeProvider
    p = FakeProvider([])
    parts = [
        {"type": "reasoning", "text": "think"},
        {"type": "tool_call", "id": "c1", "name": "python", "arguments": {"code": "1"}, "result": {"ok": True, "output": "1", "artifacts": [], "error": None, "meta": {}}},
        {"type": "tool_call", "id": "c2", "name": "python", "arguments": {"code": "2"}, "result": None},
        {"type": "text", "text": "final"},
    ]
    msgs = _expand_parts(parts, p, 1000)
    assert msgs[0]["role"] == "assistant" and len(msgs[0]["tool_calls"]) == 2
    assert msgs[1]["role"] == "tool" and msgs[1]["content"] == "1"
    assert msgs[2]["role"] == "tool" and "stopped" in msgs[2]["content"]
    assert msgs[3] == {"role": "assistant", "content": "final", "reasoning_content": ""}
    assert json.loads(msgs[0]["tool_calls"][0]["function"]["arguments"]) == {"code": "1"}


@pytest.mark.asyncio
async def test_slash_skill_force_activates_and_persists(db):
    from app.agent.skills.loader import SkillRegistry, load_skill_dir
    from app.config import REPO_ROOT

    skill = load_skill_dir(REPO_ROOT / "skills" / "matopt" / "sympy-compute", source="matopt")
    registry = SkillRegistry([skill])
    provider = FakeProvider([{"text": "expanded"}])
    kernels = KernelPool(lambda cid: Workspace(cid, settings.workspace_dir))
    harness = AgentHarness(db, kernels, settings, FakeRegistry(provider), build_tools(registry), skills=registry)
    try:
        events = await collect(
            harness,
            RunRequest(conversation_id=None, text="/sympy-compute expand (x+1)^2", skills=["sympy-compute"]),
        )
    finally:
        await kernels.shutdown_all()
    skills_ev = next(e for e in events if e["type"] == "skills")
    assert skills_ev["names"] == ["sympy-compute"] and skills_ev["auto"] is False
    cid = events[0]["conversation_id"]
    msgs = db.list_messages(cid)
    assert msgs[0]["content"]["text"].startswith("/sympy-compute")
    assert msgs[1]["content"]["parts"][0]["type"] == "skills"
    user = [m for m in provider.calls[0] if m["role"] == "user"][-1]
    content = user["content"] if isinstance(user["content"], str) else str(user["content"])
    assert "expand (x+1)^2" in content
    assert "/sympy-compute" not in content
    assert "sympy-compute" in content


@pytest.mark.asyncio
async def test_steer_slash_skill_activates_mid_turn(db):
    from app.agent.skills.loader import SkillRegistry, load_skill_dir
    from app.config import REPO_ROOT

    skill = load_skill_dir(REPO_ROOT / "skills" / "anthropic" / "frontend-design", source="anthropic")
    registry = SkillRegistry([skill])
    conv = db.create_conversation(title="landing")
    cid = conv["id"]

    class SteerAfterFirstCall(FakeProvider):
        async def stream(self, messages, tools, options):
            async for delta in super().stream(messages, tools, options):
                yield delta
            if len(self.calls) == 1:
                assert harness.enqueue_steer(cid, "/frontend-design make the hero distinctive")

    provider = SteerAfterFirstCall([
        {"tool": "list_files", "args": {}},
        {"text": "here is a distinctive page"},
    ])
    kernels = KernelPool(lambda cid: Workspace(cid, settings.workspace_dir))
    harness = AgentHarness(db, kernels, settings, FakeRegistry(provider), build_tools(registry), skills=registry)
    try:
        events = await collect(harness, RunRequest(conversation_id=cid, text="keep going on the page"))
    finally:
        await kernels.shutdown_all()
    skills_ev = [e for e in events if e["type"] == "skills"]
    assert skills_ev and "frontend-design" in skills_ev[-1]["names"]
    assert skills_ev[-1]["auto"] is False
    assert any(e["type"] == "steer_applied" and "frontend-design" in e["text"] for e in events)
    assert len(provider.calls) >= 2
    blob = json.dumps(provider.calls[1])
    assert "frontend-design" in blob
    assert "Ground your designs" in blob or "distinct visual identity" in blob
    msgs = db.list_messages(cid)
    assert any(m["role"] == "user" and "/frontend-design" in (m["content"] or {}).get("text", "") for m in msgs)


@pytest.mark.asyncio
async def test_steer_is_stored_after_the_answer_it_corrected(db):
    """A mid-turn steer must not be persisted ahead of the answer it steers.

    Regression: the steer used to be written the moment it was drained, while
    the assistant row only landed in `_run_inner`'s `finally` — so stored order
    was `user, user(steer), assistant`. `_replay` rebuilds history from stored
    rows in seq order, so the next turn showed the model its own correction
    *before* the answer it corrected.
    """
    conv = db.create_conversation(title="steer order")
    cid = conv["id"]

    class SteerAfterFirstCall(FakeProvider):
        async def stream(self, messages, tools, options):
            async for delta in super().stream(messages, tools, options):
                yield delta
            if len(self.calls) == 1:
                assert harness.enqueue_steer(cid, "actually use a log scale")

    provider = SteerAfterFirstCall([
        {"tool": "list_files", "args": {}},
        {"text": "done, using a linear scale"},
    ])
    kernels = KernelPool(lambda cid: Workspace(cid, settings.workspace_dir))
    harness = AgentHarness(db, kernels, settings, FakeRegistry(provider), build_tools())
    try:
        await collect(harness, RunRequest(conversation_id=cid, text="plot the series"))
    finally:
        await kernels.shutdown_all()

    roles = [m["role"] for m in db.list_messages(cid)]
    assert roles == ["user", "assistant", "user"], roles

    stored_steer = db.list_messages(cid)[-1]
    assert stored_steer["content"]["text"] == "actually use a log scale"
    assert stored_steer["content"]["steer"] is True

    # And the rebuilt history must show the correction last, not first.
    replayed = harness._replay(db.list_messages(cid), provider)
    user_turns = [m["content"] for m in replayed if m["role"] == "user"]
    assert user_turns, replayed
    assert "log scale" in user_turns[-1]
    assert "log scale" not in user_turns[0]


@pytest.mark.asyncio
async def test_slash_tool_is_preferred(db):
    provider = FakeProvider([{"text": "plotted"}])
    harness, kernels = make_harness(db, provider)
    try:
        events = await collect(harness, RunRequest(conversation_id=None, text="/python plot sin(x)"))
    finally:
        await kernels.shutdown_all()
    assert events[-1]["type"] == "done"
    user = [m for m in provider.calls[0] if m["role"] == "user"][-1]["content"]
    assert "python" in user and "plot sin(x)" in user
    schemas = provider.calls[0]  # stream sees tools via the call, not stored on messages
    assert "plot sin(x)" in json.dumps(provider.calls[0])


def test_failure_fingerprint_and_repeat_block():
    tools = ToolRegistry([PythonTool()])
    router = ToolRouter()
    state = TurnState()
    res = ToolResult(ok=False, error="NameError: name 'UploadedFile' is not defined")
    calls = [ToolCallSpec(id="1", name="python", arguments="{}")]
    assert "NameError" in _failure_fingerprint("python", res)
    newly = []
    for _ in range(REPEAT_LIMIT):
        newly = _record_failures(calls, [res], state, tools, router)
    assert newly == ["python"]
    assert "python" in state.blocked_tools
    blocked = ToolResult(ok=False, error="blocked", meta={"blocked_repeat": True})
    assert _record_failures(calls, [blocked], state, tools, router) == []


def test_soft_success_does_not_reset_hard_failure_streak():
    tools = ToolRegistry([PythonTool(), ListFilesTool()])
    router = ToolRouter()
    hard = [ToolCallSpec(id="p", name="python", arguments="{}")]
    soft = [ToolCallSpec(id="l", name="list_files", arguments="{}")]
    bad = [ToolResult(ok=False, error="NameError: x")]
    ok = [ToolResult(ok=True, output=".")]
    assert _next_consecutive_failures(2, soft, ok, tools, router) == 2
    assert _next_consecutive_failures(2, hard, bad, tools, router) == 3
    assert _next_consecutive_failures(2, hard, ok, tools, router) == 0


@pytest.mark.asyncio
async def test_identical_python_failures_are_blocked(db, monkeypatch):
    from app.agent.kernel import KernelSession

    provider = FakeProvider([
        {"tool": "python", "args": {"code": "raise NameError('UploadedFile')"}},
        {"tool": "python", "args": {"code": "raise NameError('UploadedFile')"}},
        {"tool": "python", "args": {"code": "raise NameError('UploadedFile')"}},
        {"tool": "python", "args": {"code": "raise NameError('UploadedFile')"}},
        {"text": "stopped retrying"},
    ])
    harness, kernels = make_harness(db, provider)
    executes = {"n": 0}
    original = KernelSession.execute

    async def wrapped(self, *args, **kwargs):
        executes["n"] += 1
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(KernelSession, "execute", wrapped)
    try:
        events = await collect(harness, RunRequest(conversation_id=None, text="keep failing"))
    finally:
        await kernels.shutdown_all()
    results = [e for e in events if e["type"] == "tool_result"]
    assert executes["n"] == REPEAT_LIMIT
    assert any((e.get("meta") or {}).get("blocked_repeat") for e in results)
    assert any(e["type"] == "done" for e in events)


# ── plan §3.8a: provider visibility, turn-stopping declarations ──────────


class RecorderTool(Tool):
    """Synthetic tool: records exactly what the harness hands it.

    `__call__` is overridden so the test can hold the *object* the harness
    passed, not just the copy `run(**kwargs)` unbundles.
    """

    name = "recorder"
    description = "records its input"
    read_only = True

    def __init__(self, name: str = "recorder", *, provider_visible: bool = True,
                 stop_turn_on_success: bool = False, fail: bool = False,
                 required_flags: tuple[str, ...] = ()):
        self.name = name
        self.provider_visible = provider_visible
        self.stop_turn_on_success = stop_turn_on_success
        self.requires_flags = required_flags
        self.fail = fail
        self.calls = 0
        self.received: Optional[dict] = None
        self.seen: Optional[dict] = None

    async def __call__(self, ctx, arguments):
        self.received = arguments
        return await super().__call__(ctx, arguments)

    async def run(self, ctx, **kwargs):
        self.calls += 1
        self.seen = kwargs
        if self.fail:
            return ToolResult(ok=False, output="", error="recorder was told to fail")
        return ToolResult(ok=True, output="recorded")


class SchemaRecordingProvider(FakeProvider):
    """FakeProvider that keeps the tool schemas each model call was offered."""

    def __init__(self, script):
        super().__init__(script)
        self.schemas: list[list[dict]] = []

    async def stream(self, messages, tools, options):
        self.schemas.append(list(tools or []))
        async for delta in super().stream(messages, tools, options):
            yield delta


def schema_names(schemas: list[dict]) -> set[str]:
    return {(s.get("function") or {}).get("name") for s in schemas}


def make_harness_for(db, provider, tools, **kwargs):
    kernels = KernelPool(lambda cid: Workspace(cid, settings.workspace_dir))
    return AgentHarness(db, kernels, settings, FakeRegistry(provider), tools, **kwargs), kernels


def tool_call_part(db, cid: str, index: int = 0) -> dict:
    parts = db.list_messages(cid)[-1]["content"]["parts"]
    return [p for p in parts if p["type"] == "tool_call"][index]


@pytest.mark.asyncio
async def test_provider_invisible_tool_is_not_advertised(db):
    hidden = RecorderTool("hidden_one", provider_visible=False)
    shown = RecorderTool("shown_one")
    provider = SchemaRecordingProvider([{"text": "done"}])
    harness, kernels = make_harness_for(db, provider, ToolRegistry([hidden, shown]))
    try:
        await collect(harness, RunRequest(conversation_id=None, text="hi"))
    finally:
        await kernels.shutdown_all()
    names = schema_names(provider.schemas[0])
    assert "shown_one" in names
    assert "hidden_one" not in names


@pytest.mark.asyncio
async def test_web_tools_are_automatic_with_per_turn_opt_out(db):
    """Ordinary requests can browse; an explicit opt-out does not leak into later turns."""
    provider = SchemaRecordingProvider([{"text": label} for label in "abcde"])
    tools = ToolRegistry([WebSearchTool(), WebFetchTool(), ListFilesTool()])
    harness, kernels = make_harness_for(db, provider, tools)
    try:
        first = await collect(harness, RunRequest(conversation_id=None, text="Who is the current president?"))
        cid = first[0]["conversation_id"]
        assert {"web_search", "web_fetch"} <= schema_names(provider.schemas[-1])

        await collect(harness, RunRequest(conversation_id=cid, text="Use only my notes", web_search=False))
        assert not schema_names(provider.schemas[-1]) & {"web_search", "web_fetch"}

        await collect(harness, RunRequest(conversation_id=cid, text="What is the weather now?"))
        assert {"web_search", "web_fetch"} <= schema_names(provider.schemas[-1])

        await collect(harness, RunRequest(conversation_id=cid, text="Check online", web_search=True))
        assert {"web_search", "web_fetch"} <= schema_names(provider.schemas[-1])

        # A slash command names a tool, not a gate: the tool's own declaration
        # switches Web Search on (`_enable_tool_flags`).
        await collect(harness, RunRequest(conversation_id=cid, text="/web-search latest IMO results", web_search=False))
        assert {"web_search", "web_fetch"} <= schema_names(provider.schemas[-1])
    finally:
        await kernels.shutdown_all()


@pytest.mark.asyncio
async def test_steer_can_switch_the_web_tools_on(db):
    """The steer path enables the same declared gate (no name check)."""
    conv = db.create_conversation(title="steer web")
    cid = conv["id"]

    class SteerWeb(SchemaRecordingProvider):
        async def stream(self, messages, tools, options):
            async for delta in super().stream(messages, tools, options):
                yield delta
            if len(self.calls) == 1:
                assert harness.enqueue_steer(cid, "/web-search who won")

    provider = SteerWeb([{"tool": "list_files", "args": {}}, {"text": "done"}])
    tools = ToolRegistry([WebSearchTool(), WebFetchTool(), ListFilesTool()])
    harness, kernels = make_harness_for(db, provider, tools)
    try:
        await collect(harness, RunRequest(conversation_id=cid, text="find it", web_search=False))
    finally:
        await kernels.shutdown_all()
    assert not schema_names(provider.schemas[0]) & {"web_search", "web_fetch"}
    assert {"web_search", "web_fetch"} <= schema_names(provider.schemas[-1])


@pytest.mark.asyncio
async def test_automatic_web_search_executes_without_manual_selection(db, monkeypatch):
    """Exercise the real tool gate and result replay without a network dependency."""
    from app.agent.tools import web_search as web_module

    searched = []

    async def search(query, limit, config):
        searched.append(query)
        return [{"title": "Official forecast", "href": "https://example.com/forecast", "body": "Sunny today."}], "test"

    monkeypatch.setattr(web_module, "search_web", search)
    provider = SchemaRecordingProvider([
        {"tool": "web_search", "args": {"query": "Hong Kong weather today official forecast"}},
        {"text": "Today's forecast is sunny. [Forecast](https://example.com/forecast)"},
    ])
    harness, kernels = make_harness_for(db, provider, ToolRegistry([WebSearchTool(), WebFetchTool()]))
    try:
        events = await collect(harness, RunRequest(conversation_id=None, text="What is the weather in Hong Kong?"))
    finally:
        await kernels.shutdown_all()
    assert searched == ["Hong Kong weather today official forecast"]
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["ok"] and "https://example.com/forecast" in result["output"]
    assert provider.calls[1][-1]["role"] == "tool"
    assert "Sunny today" in provider.calls[1][-1]["content"]
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_web_search_opt_out_blocks_an_unadvertised_model_call(db, monkeypatch):
    from app.agent.tools import web_search as web_module

    async def unexpected_search(*args, **kwargs):
        pytest.fail("A disabled web tool must not make a network call")

    monkeypatch.setattr(web_module, "search_web", unexpected_search)
    provider = SchemaRecordingProvider([
        {"tool": "web_search", "args": {"query": "news"}},
        {"text": "Web access is disabled for this turn."},
    ])
    harness, kernels = make_harness_for(db, provider, ToolRegistry([WebSearchTool(), WebFetchTool()]))
    try:
        events = await collect(harness, RunRequest(conversation_id=None, text="Use my notes", web_search=False))
    finally:
        await kernels.shutdown_all()
    assert not schema_names(provider.schemas[0]) & {"web_search", "web_fetch"}
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_stop_turn_on_success_ends_the_turn(db):
    stopper = RecorderTool("finish_now", stop_turn_on_success=True)
    provider = FakeProvider([
        {"text": "submitting", "tool": "finish_now", "args": {"payload": {"nested": [1, 2]}}},
        {"text": "this must never be reached"},
    ])
    harness, kernels = make_harness_for(db, provider, ToolRegistry([stopper]))
    try:
        events = await collect(harness, RunRequest(conversation_id=None, text="finish"))
    finally:
        await kernels.shutdown_all()
    assert stopper.calls == 1
    assert len(provider.calls) == 1              # the loop never asked the model again
    assert events[-1]["type"] == "done"
    assert not any(e["type"] == "text_delta" and "never be reached" in e.get("delta", "") for e in events)
    cid = events[0]["conversation_id"]
    result = tool_call_part(db, cid)["result"]
    assert result["ok"] and result["meta"]["stop_turn"] is True


@pytest.mark.asyncio
async def test_failed_terminal_tool_does_not_end_the_turn(db):
    stopper = RecorderTool("finish_now", stop_turn_on_success=True, fail=True)
    provider = FakeProvider([
        {"tool": "finish_now", "args": {}},
        {"text": "recovered"},
    ])
    harness, kernels = make_harness_for(db, provider, ToolRegistry([stopper]))
    try:
        events = await collect(harness, RunRequest(conversation_id=None, text="finish"))
    finally:
        await kernels.shutdown_all()
    assert stopper.calls == 1
    assert len(provider.calls) == 2              # the failure is repairable in-session
    assert events[-1]["type"] == "done"


# ── plan §3.8c: normalize tool input exactly once ────────────────────────

# Whitespace-heavy, nested, and streamed as raw text: the model's bytes are not
# what anything downstream should see.
RAW_NESTED = (
    '  { "query" : "  spaced  out  " ,\n'
    '    "options" : { "nested" : { "list" : [ 1 , 2 , { "deep" : " x " } ] } } ,\n'
    '    "flag" : true }   '
)
EXPECTED_NESTED = {
    "query": "  spaced  out  ",
    "options": {"nested": {"list": [1, 2, {"deep": " x "}]}},
    "flag": True,
}


def patch_monitor(monkeypatch) -> list:
    """Capture the exact object `inspect_tool_call` is handed."""
    from app.agent import execution as execution_module
    seen: list = []
    real = execution_module.inspect_tool_call
    monkeypatch.setattr(
        execution_module, "inspect_tool_call",
        lambda name, args: (seen.append(args), real(name, args))[1],
    )
    return seen


@pytest.mark.asyncio
async def test_normalized_arguments_are_identical_for_every_consumer(db, monkeypatch):
    tool = RecorderTool("echo_args")
    provider = FakeProvider([{"tool": "echo_args", "raw_args": RAW_NESTED}, {"text": "done"}])
    monitor_seen = patch_monitor(monkeypatch)
    harness, kernels = make_harness_for(db, provider, ToolRegistry([tool]))
    try:
        events = await collect(harness, RunRequest(conversation_id=None, text="hi"))
    finally:
        await kernels.shutdown_all()

    ready = next(e for e in events if e["type"] == "tool_call_ready")
    assert ready["arguments"] == EXPECTED_NESTED
    assert tool.seen == EXPECTED_NESTED                       # what the handler ran with
    assert monitor_seen == [EXPECTED_NESTED]                  # what the monitor inspected
    # Same object, not merely equal: nothing re-parsed or re-serialized the
    # arguments between the monitor, the event and the handler.
    assert monitor_seen[0] is ready["arguments"] is tool.received
    assert tool_call_part(db, events[0]["conversation_id"])["arguments"] == EXPECTED_NESTED


@pytest.mark.asyncio
async def test_resolve_input_normalization_reaches_every_consumer(db, monkeypatch):
    class StrippingTool(RecorderTool):
        """Normalizes the model's input into the execution fact (§3.8c)."""

        name = "strip_args"

        def resolve_input(self, arguments: dict) -> dict:
            def clean(value):
                if isinstance(value, str):
                    return value.strip()
                if isinstance(value, list):
                    return [clean(v) for v in value]
                if isinstance(value, dict):
                    return {k: clean(v) for k, v in value.items()}
                return value

            return clean(arguments)

    tool = StrippingTool("strip_args")
    raw = '{ "note" : "  padded  " , "nested" : { "list" : [ " a " , " b\\n" ] } }'
    expected = {"note": "padded", "nested": {"list": ["a", "b"]}}
    provider = FakeProvider([{"tool": "strip_args", "raw_args": raw}, {"text": "done"}])
    monitor_seen = patch_monitor(monkeypatch)
    harness, kernels = make_harness_for(db, provider, ToolRegistry([tool]))
    try:
        events = await collect(harness, RunRequest(conversation_id=None, text="hi"))
    finally:
        await kernels.shutdown_all()

    ready = next(e for e in events if e["type"] == "tool_call_ready")
    assert ready["arguments"] == expected
    assert tool.seen == expected
    assert monitor_seen[0] is ready["arguments"] is tool.received
    assert tool_call_part(db, events[0]["conversation_id"])["arguments"] == expected


@pytest.mark.asyncio
async def test_unparseable_arguments_never_reach_the_tool(db, monkeypatch):
    tool = RecorderTool("echo_args")
    raw = '{"query": "unterminated'
    provider = FakeProvider([{"tool": "echo_args", "raw_args": raw}, {"text": "done"}])
    monitor_seen = patch_monitor(monkeypatch)
    harness, kernels = make_harness_for(db, provider, ToolRegistry([tool]))
    try:
        events = await collect(harness, RunRequest(conversation_id=None, text="hi"))
    finally:
        await kernels.shutdown_all()

    assert tool.calls == 0 and monitor_seen == []
    ready = next(e for e in events if e["type"] == "tool_call_ready")
    assert ready["arguments"] == {"_raw": raw}
    result = next(e for e in events if e["type"] == "tool_result")
    assert not result["ok"] and "Could not parse tool arguments as JSON" in result["error"]
    assert tool_call_part(db, events[0]["conversation_id"])["arguments"] == {"_raw": raw}


@pytest.mark.asyncio
async def test_failed_normalization_never_reaches_the_tool(db, monkeypatch):
    class BrokenTool(RecorderTool):
        name = "broken_args"

        def resolve_input(self, arguments: dict) -> dict:
            raise ValueError("cannot normalize this")

    tool = BrokenTool("broken_args")
    provider = FakeProvider([{"tool": "broken_args", "args": {"a": 1}}, {"text": "done"}])
    monitor_seen = patch_monitor(monkeypatch)
    harness, kernels = make_harness_for(db, provider, ToolRegistry([tool]))
    try:
        events = await collect(harness, RunRequest(conversation_id=None, text="hi"))
    finally:
        await kernels.shutdown_all()

    assert tool.calls == 0 and monitor_seen == []
    ready = next(e for e in events if e["type"] == "tool_call_ready")
    assert ready["arguments"] == {"_raw": '{"a": 1}'}
    result = next(e for e in events if e["type"] == "tool_result")
    assert not result["ok"] and "Could not normalize tool arguments: ValueError" in result["error"]
