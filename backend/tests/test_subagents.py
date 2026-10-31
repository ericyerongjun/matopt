"""Sub-agents (Codex multi-agent v2 port) run through the real harness."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.agent.events import Artifact, ToolResult
from app.agent.harness import AgentHarness, RunRequest
from app.agent.subagents import SubAgentManager, subagent_tools
from app.agent.tools.base import Tool, ToolRegistry
from app.config import settings
from app.storage.db import Database
from tests.fake_provider import FakeProvider, FakeRegistry


class WriteOut(Tool):
    name = "write_out"
    description = "Write a named output."
    parameters = {"type": "object", "properties": {"name": {"type": "string"}}}

    async def run(self, ctx, name="x", **_):
        (ctx.workspace.root / name).write_text(name)
        return ToolResult(ok=True, output=f"wrote {name}", artifacts=[Artifact(kind="file", name=name, path=name)])


class RoutingProvider(FakeProvider):
    """Answers by who is asking: the parent, or a child identified by its nickname."""

    def __init__(self):
        super().__init__([])
        self.parent_steps = 0

    async def stream(self, messages, tools, options):
        system = messages[0].get("content") or ""
        joined = json.dumps(messages)
        if "You are Ada" in system or "You are Gauss" in system:
            child = "primes" if "You are Ada" in system else "fib"
            done = any(m.get("role") == "tool" for m in messages)
            self.script = [{"text": f"{child} saved to {child}.txt"}] if done else \
                [{"tool": "write_out", "args": {"name": f"{child}.txt"}}]
        else:
            self.parent_steps += 1
            if self.parent_steps == 1:
                self.script = [{"tools": [
                    {"tool": "spawn_agent", "args": {"task_name": "primes", "message": "Write primes.", "role": "default"}},
                    {"tool": "spawn_agent", "args": {"task_name": "fib", "message": "Write fib.", "role": "default"}}]}]
            elif "[Sub-agent report]" in joined or "Both files are ready" in joined:
                self.script = [{"text": "Both files are ready."}]
            else:
                self.script = [{"text": "Waiting for the helpers."}]
        async for delta in super().stream(messages, tools, options):
            yield delta


@pytest.fixture
def rig(tmp_path):
    db = Database(tmp_path / "agents.sqlite")
    config = settings.model_copy(update={"data_dir": tmp_path, "skill_auto_activate": False, "data_agent_enabled": False,
                                         "subagents_enabled": True, "verify_rounds": 0, "memories_enabled": False})
    provider = RoutingProvider()
    harness = AgentHarness(db, SimpleNamespace(peek=lambda cid: None), config, FakeRegistry(provider),
                           ToolRegistry([WriteOut(), *subagent_tools()]))
    yield SimpleNamespace(db=db, harness=harness, provider=provider, cid=db.create_conversation()["id"], config=config)
    db.close()


@pytest.mark.asyncio
async def test_parallel_children_share_the_budget_and_report_back(rig):
    events = [e.to_dict() async for e in rig.harness.run(RunRequest(rig.cid, "Make primes and fib files in parallel."))]
    types = [e["type"] for e in events]
    assert "error" not in types, [e for e in events if e["type"] == "error"]
    agents = {row["nickname"]: row for row in rig.db.list_subagents(rig.cid)}
    assert {a["status"] for a in agents.values()} == {"completed"}
    assert agents["Ada"]["summary"] == "primes saved to primes.txt"
    workspace = rig.config.workspace_dir / rig.cid
    assert (workspace / "primes.txt").exists() and (workspace / "fib.txt").exists()
    kinds = [(e["nickname"], e["kind"]) for e in events if e["type"] == "subagent"]
    assert ("Ada", "completed") in kinds and ("Gauss", "completed") in kinds
    end = next(e for e in events if e["type"] == "message_end")
    assert "Both files are ready." in json.dumps(end["content"])


@pytest.mark.asyncio
async def test_manager_limits_and_lookup(rig):
    manager = SubAgentManager.__new__(SubAgentManager)
    manager.agents, manager.spawned = {}, 6
    manager.settings = SimpleNamespace(subagent_max_per_turn=6)
    with pytest.raises(ValueError, match="maximum"):
        await manager.spawn("x", "y", "default", "none")


def test_subagent_tools_hidden_without_a_manager():
    from app.agent.tools.base import ToolContext
    ctx = SimpleNamespace(state={})
    assert not any(t.is_provider_visible(ctx) for t in subagent_tools())
    ctx.state["subagents"] = object()
    assert all(t.is_provider_visible(ctx) for t in subagent_tools())
