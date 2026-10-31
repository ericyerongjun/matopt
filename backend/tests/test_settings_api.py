"""Settings read model, feature toggles in the user TOML, approvals and jobs."""

import asyncio
import tomllib

import pytest
from fastapi.testclient import TestClient

from app.api.settings import write_feature
from app.agent.approvals import ApprovalBroker, approval_trigger
from app.agent.tools.shell_tool import ShellTool
from app.agent.exec_rules import ExecPolicy, _rule


def test_write_feature_preserves_other_lines(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("# mine\nsandbox_mode = \"off\"\n\n[features]\nweb_search = true\n\n[profiles.fast]\nx = 1\n")
    write_feature(path, "memories", True)
    write_feature(path, "web_search", False)
    text = path.read_text()
    data = tomllib.loads(text)
    assert text.startswith("# mine\n")
    assert data["features"] == {"web_search": False, "memories": True}
    assert data["profiles"]["fast"]["x"] == 1
    fresh = tmp_path / "new.toml"
    write_feature(fresh, "subagents", True)
    assert tomllib.loads(fresh.read_text())["features"]["subagents"] is True


def test_settings_endpoint_and_feature_toggle(monkeypatch, tmp_path):
    from app.main import app
    from app.config import settings
    monkeypatch.setenv("MATOPT_CONFIG", str(tmp_path / "config.toml"))
    with TestClient(app) as client:
        body = client.get("/api/settings").json()
        keys = {f["key"] for f in body["features"]}
        assert {"memories", "subagents", "code_mode"} <= keys
        assert body["security"]["sandbox"]["mode"] == settings.sandbox_mode
        response = client.put("/api/features/align_monitor_llm", json={"enabled": True})
        assert response.status_code == 200 and response.json()["enabled"] is True
        assert settings.align_monitor_llm is True
        assert tomllib.loads((tmp_path / "config.toml").read_text())["features"]["align_monitor_llm"] is True
        settings.align_monitor_llm = False
        assert client.put("/api/features/sandbox_mode", json={"enabled": False}).status_code == 404
        assert client.get("/api/conversations/none/jobs").json() == {"jobs": []}


def test_approval_trigger_rules():
    shell = ShellTool(exec_policy=ExecPolicy([_rule(["git", "push"], "prompt", "publishes")]))
    assert approval_trigger(shell, {"command": "git push"}) == ("exec_rule_prompt", "publishes")
    assert approval_trigger(shell, {"command": "ls"}) is None


@pytest.mark.asyncio
async def test_broker_resolves_and_times_out():
    events = []
    async def emit(event):
        events.append(event)
    broker = ApprovalBroker()
    cancel = asyncio.Event()

    async def answer():
        while not broker.pending_for("c"):
            await asyncio.sleep(0.01)
        assert not broker.resolve("other", broker.pending_for("c")[0], "allow"), "conversation-bound"
        assert broker.resolve("c", broker.pending_for("c")[0], "allow")
    task = asyncio.create_task(answer())
    outcome = await broker.ask(emit, "c", cancel, tool="bash", title="t", summary="s", reason="r", risk="high", timeout=5)
    await task
    assert outcome.decision == "allow" and [e.type for e in events] == ["approval_request", "approval_resolved"]
    late = await broker.ask(emit, "c", cancel, tool="bash", title="t", summary="s", reason="r", risk="high", timeout=0.05)
    assert late.decision == "timeout"


@pytest.mark.asyncio
async def test_approved_prompt_rule_runs(tmp_path):
    from app.agent.tools.base import ToolContext
    from app.agent.workspace import Workspace
    async def emit(_):
        pass
    shell = ShellTool(exec_policy=ExecPolicy([_rule(["echo"], "prompt", "test")]))
    ctx = ToolContext("c", Workspace("c", tmp_path), emit, asyncio.Event(), call_id="k")
    assert not (await shell(ctx, {"command": "echo hi"})).ok
    ctx.state["approved_calls"] = {"k"}
    assert (await shell(ctx, {"command": "echo hi"})).ok
