import asyncio
import json

import pytest

from app.agent.harness import AgentHarness, RunRequest, _expand_parts, _parse_arguments
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
    events = []
    async for ev in harness.run(req):
        events.append(ev.to_dict())
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
    roles = [m["role"] for m in history]
    assert roles[0] == "system" and roles[1] == "user"
    assert roles[2] == "assistant" and "tool_calls" in history[2]
    assert roles[3] == "tool"
    assert roles[4] == "assistant" and history[4]["content"] == "first answer"
    assert roles[5] == "user" and history[5]["content"] == "again"


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
        async for ev in harness.run(RunRequest(conversation_id=None, text="loop")):
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
    assert msgs[3] == {"role": "assistant", "content": "final"}
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
