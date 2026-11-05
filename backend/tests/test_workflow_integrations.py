"""Slide-derived workflow skills, automations (/loop, /automate) and MCP connectors."""

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.agent.automations import (AutomationService, AutomationStore, next_cron, parse_cron,
                                   CreateAutomationTool, set_automation_service)
from app.agent.mcp import catalog, export
from app.agent.mcp.config import expand_vars, read_env_file, set_env_value
from app.agent.skills.loader import SkillRegistry
from app.agent.skills.router import SkillRouter
from app.agent.tools.base import ToolContext
from app.agent.workspace import Workspace
from app.config import REPO_ROOT, default_skill_group_dirs

WORKFLOW = REPO_ROOT / "skills" / "workflow"
SLIDE_COMMANDS = (
    "openspec-propose", "openspec-review", "openspec-apply", "openspec-archive", "plan-mode",
    "goal", "side", "btw", "review", "review-bugbot", "review-security", "loop", "shell", "automate",
    "autopilot", "canvas", "create-hook", "create-rule", "create-skill", "create-subagent",
    "prompt-blame", "migrate-to-skills", "matopt-sdk", "split-to-prs",
)


# ── skills ──────────────────────────────────────────────────────────────

def test_workflow_group_is_a_default_skill_directory():
    assert REPO_ROOT / "skills" / "workflow" in default_skill_group_dirs(REPO_ROOT)


def test_every_slide_skill_loads_and_commands_are_manual():
    reg = SkillRegistry.discover([WORKFLOW])
    for name in (*SLIDE_COMMANDS, "openspec", "prepare-reading-batch"):
        skill = reg.get(name)
        assert skill is not None, name
        assert skill.source == "workflow"
    assert all(reg.get(n).manual for n in SLIDE_COMMANDS)
    assert not reg.get("openspec").manual and not reg.get("prepare-reading-batch").manual


def test_manual_skills_are_never_auto_activated():
    reg = SkillRegistry.discover([*default_skill_group_dirs(REPO_ROOT)])
    router = SkillRouter(reg)
    for text in ("review this proof for errors", "loop over the list and shell out", "what's the goal of PCA?",
                 "create a skill-like summary", "/review-security please"):
        assert not any(s.manual for s in router.plan(text).activate), text
    assert "review-bugbot" in router.plan("hello").catalog_names


def test_reading_batch_skill_routes_on_its_own_words():
    reg = SkillRegistry.discover([*default_skill_group_dirs(REPO_ROOT)])
    plan = SkillRouter(reg).plan("Prepare a reading batch JSON import file from these resource links")
    assert plan.activate and plan.activate[0].name == "prepare-reading-batch"


def _run(script: Path, *args, cwd):
    return subprocess.run([sys.executable, str(script), *args], cwd=cwd, capture_output=True, text=True)


def test_openspec_helper_propose_archive_cycle(tmp_path):
    script = WORKFLOW / "openspec" / "scripts" / "openspec.py"
    assert _run(script, "new", "build-board", "--capability", "board", cwd=tmp_path).returncode == 0
    # The untouched scaffold still has placeholders, so it must not validate.
    assert _run(script, "validate", "build-board", cwd=tmp_path).returncode == 1
    change = tmp_path / "openspec" / "changes" / "build-board"
    (change / "specs" / "board" / "spec.md").write_text(
        "## ADDED Requirements\n\n### Requirement: Add\nThe system SHALL add a link.\n\n"
        "#### Scenario: Duplicate\n- **WHEN** the URL exists\n- **THEN** nothing is added\n")
    (change / "tasks.md").write_text("## 1\n- [x] 1.1 add\n- [ ] 1.2 test\n")
    assert _run(script, "archive", "build-board", cwd=tmp_path).returncode == 1   # open task
    (change / "tasks.md").write_text("## 1\n- [x] 1.1 add\n- [x] 1.2 test\n")
    out = _run(script, "archive", "build-board", cwd=tmp_path)
    assert out.returncode == 0, out.stdout
    spec = (tmp_path / "openspec" / "specs" / "board" / "spec.md").read_text()
    assert "### Requirement: Add" in spec and "#### Scenario: Duplicate" in spec
    assert not change.exists() and list((tmp_path / "openspec" / "changes" / "archive").iterdir())
    # A later change modifies and renames, keeping order.
    _run(script, "new", "tweak", "--capability", "board", cwd=tmp_path)
    tweak = tmp_path / "openspec" / "changes" / "tweak"
    (tweak / "specs" / "board" / "spec.md").write_text(
        "## MODIFIED Requirements\n\n### Requirement: Add\nThe system MUST add a trimmed link.\n\n"
        "#### Scenario: Trim\n- **WHEN** the URL has spaces\n- **THEN** they are removed\n\n"
        "## RENAMED Requirements\n- FROM: ### Requirement: Add\n- TO: ### Requirement: Add a link\n")
    (tweak / "tasks.md").write_text("- [x] 1.1 trim\n")
    assert _run(script, "archive", "tweak", cwd=tmp_path).returncode == 0
    spec = (tmp_path / "openspec" / "specs" / "board" / "spec.md").read_text()
    assert "### Requirement: Add a link\nThe system MUST add a trimmed link." in spec
    assert "#### Scenario: Duplicate" not in spec


def test_reading_batch_checker(tmp_path):
    script = WORKFLOW / "prepare-reading-batch" / "scripts" / "reading_batch.py"
    batch = tmp_path / "b.json"
    batch.write_text(json.dumps([{"title": " URL - MDN ", "url": " https://developer.mozilla.org/en-US/docs/Web/API/URL "},
                                 {"title": "bad", "url": "javascript:alert(1)"},
                                 {"title": "dup", "url": "https://developer.mozilla.org/en-US/docs/Web/API/URL"}]))
    out = _run(script, str(batch), "--fix", cwd=tmp_path)
    assert out.returncode == 1 and "invalid: 1" in out.stdout and "duplicate: 1" in out.stdout
    assert json.loads(batch.read_text()) == [{"title": "URL - MDN", "url": "https://developer.mozilla.org/en-US/docs/Web/API/URL"}]


# ── automations ─────────────────────────────────────────────────────────

def test_cron_parsing_and_next_run():
    tz = ZoneInfo("Asia/Shanghai")
    start = datetime(2026, 11, 4, 10, 0, tzinfo=tz)   # a Wednesday
    assert next_cron(parse_cron("0 9 * * 1"), start) == datetime(2026, 11, 9, 9, 0, tzinfo=tz)
    assert next_cron(parse_cron("*/15 * * * *"), start).minute == 15
    assert next_cron(parse_cron("30 2 * * mon-fri"), datetime(2026, 11, 6, 10, 0, tzinfo=tz)).day == 9
    assert next_cron(parse_cron("@monthly"), start) == datetime(2026, 12, 1, 0, 0, tzinfo=tz)
    for bad in ("* * *", "61 * * * *", "* * * * 9", "*/0 * * * *"):
        with pytest.raises(ValueError):
            parse_cron(bad)


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _service(tmp_path, busy=lambda cid: False):
    started = []

    async def start(cid, text, row):
        started.append((cid, text))
        return f"run{len(started)}"

    clock = Clock()
    svc = AutomationService(AutomationStore(tmp_path / "automations.json"), tmp_path / "ws", start,
                            is_busy=busy, clock=clock)
    (tmp_path / "ws" / "c1").mkdir(parents=True)
    return svc, started, clock


@pytest.mark.asyncio
async def test_interval_automation_runs_and_stops_after_max_runs(tmp_path):
    svc, started, clock = _service(tmp_path)
    row = svc.create(name="ping", prompt="check it", conversation_id="c1", every_minutes=5, max_runs=2)
    assert await svc.tick() == 0
    clock.t += 300
    assert await svc.tick() == 1
    assert "automations/" in started[0][1] and "check it" in started[0][1]
    clock.t += 300
    assert await svc.tick() == 1
    clock.t += 300
    assert await svc.tick() == 0
    final = svc.list()[0]
    assert final["id"] == row["id"] and final["status"] == "completed" and final["run_count"] == 2


@pytest.mark.asyncio
async def test_watch_pattern_is_quiet_until_the_experiment_finishes(tmp_path):
    svc, started, clock = _service(tmp_path)
    status = tmp_path / "ws" / "c1" / "experiment-status.json"
    status.write_text('{"status": "running", "step": 10}')
    svc.create(name="exp", prompt="Report the result.", conversation_id="c1", every_minutes=5,
               watch_path="experiment-status.json", watch_pattern=r'"status":\s*"(finished|failed)"')
    for step in (20, 30):
        clock.t += 300
        status.write_text(f'{{"status": "running", "step": {step}}}')
        assert await svc.tick() == 0
    clock.t += 300
    status.write_text('{"status": "failed", "error": "OOM"}')
    assert await svc.tick() == 1
    assert "OOM" in started[0][1]
    assert svc.list()[0]["status"] == "completed"
    clock.t += 300
    assert await svc.tick() == 0


@pytest.mark.asyncio
async def test_watch_change_and_busy_retry(tmp_path):
    busy = {"c1": True}
    svc, started, clock = _service(tmp_path, busy=lambda cid: busy.get(cid, False))
    log = tmp_path / "ws" / "c1" / "log.txt"
    log.write_text("a")
    svc.create(name="log", prompt="Summarize new lines.", conversation_id="c1", every_minutes=1, watch_path="log.txt")
    clock.t += 60
    assert await svc.tick() == 0          # unchanged: no turn
    log.write_text("ab")
    clock.t += 60
    assert await svc.tick() == 0          # changed but the conversation is busy
    busy["c1"] = False
    clock.t += 60
    assert await svc.tick() == 1


def test_automation_validation(tmp_path):
    svc, _, _ = _service(tmp_path)
    with pytest.raises(ValueError):
        svc.create(name="x", prompt="y", conversation_id="c1")
    with pytest.raises(ValueError):
        svc.create(name="x", prompt="y", conversation_id="c1", every_minutes=5, cron="* * * * *")
    with pytest.raises(ValueError):
        svc.create(name="x", prompt="y", conversation_id="c1", cron="0 9 * * 1", timezone="Mars/Olympus")
    with pytest.raises(ValueError):
        svc.create(name="x", prompt="y", conversation_id="c1", every_minutes=5, watch_path="../../etc/passwd")
    row = svc.create(name="weekly", prompt="y", conversation_id="c1", cron="0 9 * * 1", timezone="Asia/Shanghai")
    assert row["schedule"] == {"cron": "0 9 * * 1", "timezone": "Asia/Shanghai"}
    when = datetime.fromtimestamp(row["next_run_at"], ZoneInfo("Asia/Shanghai"))
    assert (when.isoweekday(), when.hour, when.minute) == (1, 9, 0)
    assert svc.update(row["id"], enabled=False)["enabled"] is False
    assert svc.delete(row["id"]) and not svc.delete(row["id"])


@pytest.mark.asyncio
async def test_create_automation_tool(tmp_path):
    svc, _, _ = _service(tmp_path)
    set_automation_service(svc)
    try:
        async def emit(_):
            pass
        ctx = ToolContext("c1", Workspace("c1", tmp_path / "ws"), emit, asyncio.Event())
        tool = CreateAutomationTool()
        assert tool.needs_approval and tool.side_effect_scope == "system"
        result = await tool.run(ctx, name="Weekly reading", prompt="List new docs.", cron="0 9 * * 1",
                                timezone="Asia/Shanghai")
        assert result.ok and "Mondays" not in result.output and "Asia/Shanghai" in result.output
        bad = await tool.run(ctx, name="x", prompt="y")
        assert not bad.ok
    finally:
        set_automation_service(None)


def test_automations_api(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    assert client.get("/api/automations").json() == {"automations": []}
    assert client.delete("/api/automations/nope").status_code == 404


# ── MCP connectors ──────────────────────────────────────────────────────

def test_env_expansion_and_secrets_file(tmp_path):
    value, missing = expand_vars({"h": "Bearer ${TOK}", "x": ["${A:-dflt}", 3]}, {"TOK": "t"})
    assert value == {"h": "Bearer t", "x": ["dflt", 3]} and missing == []
    _, missing = expand_vars("${NOPE}", {})
    assert missing == ["NOPE"]
    env = tmp_path / "mcp.env"
    set_env_value(env, "GH", 'tok"en')
    assert read_env_file(env) == {"GH": 'tok"en'}
    assert oct(env.stat().st_mode & 0o777) == "0o600"
    set_env_value(env, "GH", None)
    assert read_env_file(env) == {}
    with pytest.raises(ValueError):
        set_env_value(env, "bad name", "x")


def test_catalog_entries_are_well_formed():
    ids = [c.id for c in catalog.CATALOG]
    assert len(ids) == len(set(ids)) >= 20
    assert {"microsoft-learn", "github", "playwright", "filesystem", "matlab", "jupyter", "wolfram"} <= set(ids)
    learn = catalog.BY_ID["microsoft-learn"]
    assert learn.config == {"url": "https://learn.microsoft.com/api/mcp", "transport": "http"}
    for c in catalog.CATALOG:
        assert ("url" in c.config) != ("command" in c.config), c.id
        assert c.docs.startswith("https://"), c.id
        for inp in c.inputs:
            if inp.kind == "secret":
                assert inp.env and f"${{{inp.env}" in json.dumps(c.config), c.id


def test_connect_writes_mcp_json_without_secrets(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
    cfg, env = tmp_path / "mcp.json", tmp_path / "mcp.env"
    cfg.write_text(json.dumps({"mcpServers": {"mine": {"command": "x"}}, "other": 1}))
    mgr = catalog.ConnectorManager(cfg, env)
    with pytest.raises(ValueError):
        mgr.connect("github", {}, {})                       # token required
    mgr.connect("github", {}, {"token": "ghp_secret"})
    mgr.connect("microsoft-learn", {}, {})
    with pytest.raises(ValueError):
        mgr.connect("filesystem", {"directory": "relative/dir"}, {})
    mgr.connect("filesystem", {"directory": str(tmp_path)}, {})
    raw = json.loads(cfg.read_text())
    assert raw["other"] == 1 and raw["mcpServers"]["mine"] == {"command": "x"}
    assert "ghp_secret" not in cfg.read_text()
    assert raw["mcpServers"]["github"]["headers"]["Authorization"] == "Bearer ${GITHUB_PERSONAL_ACCESS_TOKEN}"
    assert raw["mcpServers"]["filesystem"]["args"][-1] == str(tmp_path)
    rows = {r["id"]: r for r in mgr.list([{"name": "microsoft-learn", "state": "connected", "tools": 3}])}
    assert rows["github"]["installed"] and rows["github"]["missing"] == [] and rows["github"]["secrets"]
    assert rows["microsoft-learn"]["state"] == "connected" and rows["microsoft-learn"]["tool_count"] == 3
    assert not rows["playwright"]["installed"]
    assert "ghp_secret" not in json.dumps(rows)
    assert mgr.set_enabled("github", False) and json.loads(cfg.read_text())["mcpServers"]["github"]["enabled"] is False
    assert mgr.disconnect("github") and "GITHUB_PERSONAL_ACCESS_TOKEN" not in read_env_file(env)


@pytest.mark.asyncio
async def test_hub_expands_vars_and_reports_missing(monkeypatch, tmp_path):
    import fastmcp
    import app.agent.mcp.client as client_module
    from app.agent.mcp.client import McpHub
    seen = {}

    class FakeClient:
        def __init__(self, config):
            name, entry = next(iter(config["mcpServers"].items()))
            seen[name] = entry
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def list_tools(self):
            return [{"name": "microsoft_docs_search", "inputSchema": {"type": "object"},
                     "annotations": {"readOnlyHint": True}}]

    env = tmp_path / "mcp.env"
    set_env_value(env, "GH_TOKEN_TEST", "abc")
    monkeypatch.delenv("CTX_KEY_TEST", raising=False)
    monkeypatch.setattr(fastmcp, "Client", FakeClient)
    monkeypatch.setattr(client_module, "load_mcp_config", lambda _: {"mcpServers": {
        "gh": {"url": "u", "headers": {"Authorization": "Bearer ${GH_TOKEN_TEST}"}, "x-matopt-connector": "github"},
        "ctx": {"url": "u", "headers": {"Authorization": "Bearer ${CTX_KEY_TEST:-}"}},
        "need": {"command": "x", "env": {"T": "${UNSET_VAR_TEST}"}},
    }})
    hub = McpHub(env_path=env)
    await hub.start()
    assert seen["gh"] == {"url": "u", "headers": {"Authorization": "Bearer abc"}}
    assert seen["ctx"] == {"url": "u", "headers": {}}
    status = {row["name"]: row for row in hub.status()}
    assert status["need"]["state"] == "failed" and "UNSET_VAR_TEST" in status["need"]["error"]
    assert status["gh"]["state"] == "connected"
    await hub.stop()


def test_connectors_api(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_config_path", tmp_path / "mcp.json")
    monkeypatch.setattr(settings, "mcp_env_path", tmp_path / "mcp.env")
    client = TestClient(app)
    rows = client.get("/api/mcp/connectors").json()["connectors"]
    assert any(r["id"] == "microsoft-learn" and not r["installed"] for r in rows)
    assert client.post("/api/mcp/connectors/nope", json={}).status_code == 404
    response = client.post("/api/mcp/connectors/microsoft-learn", json={})
    assert response.status_code == 200 and response.json()["connector"]["installed"]
    assert json.loads((tmp_path / "mcp.json").read_text())["mcpServers"]["microsoft-learn"]["url"]
    assert client.delete("/api/mcp/connectors/microsoft-learn").json() == {"removed": True}
    snippet = client.get("/api/mcp/clients/cursor").json()["snippet"]
    assert json.loads(snippet)["mcpServers"]["matopt"]["args"] == ["-m", "app.agent.mcp"]
    assert client.get("/api/mcp/clients/nope").status_code == 404


# ── exporting MatOpt's own MCP server ───────────────────────────────────

def test_export_writes_client_configs(tmp_path):
    cursor = tmp_path / "mcp.json"
    cursor.write_text(json.dumps({"mcpServers": {"other": {"command": "y"}}}))
    export.write(export.BY_ID["cursor"], path=cursor)
    data = json.loads(cursor.read_text())
    assert set(data["mcpServers"]) == {"other", "matopt"} and (tmp_path / "mcp.json.bak").exists()
    codex = tmp_path / "config.toml"
    codex.write_text('model = "x"\n')
    export.write(export.BY_ID["codex"], path=codex)
    import tomllib
    parsed = tomllib.loads(codex.read_text())
    assert parsed["model"] == "x" and parsed["mcp_servers"]["matopt"]["args"] == ["-m", "app.agent.mcp"]
    with pytest.raises(ValueError):
        export.write(export.BY_ID["codex"], path=codex)
    vscode = json.loads(export.snippet(export.BY_ID["vscode"], "http://localhost:8000/mcp"))
    assert vscode["servers"]["matopt"]["type"] == "http"
    assert export.snippet(export.BY_ID["claude-code"]).startswith("claude mcp add")


@pytest.mark.asyncio
async def test_runtime_starts_automation_runs_in_the_conversation(monkeypatch):
    from app.runtime import get_runtime
    rt = get_runtime()
    conv = rt.db.create_conversation(title="loop host", provider="deepseek", model="deepseek-chat")
    seen = []

    async def fake_start(req, client_run_id=None):
        seen.append(req)
        return {"id": "run_x"}

    monkeypatch.setattr(rt.runs, "start", fake_start)
    assert await rt._start_automation_run(conv["id"], "check", {}) == "run_x"
    assert seen[0].conversation_id == conv["id"] and seen[0].text == "check"
    assert (seen[0].provider_id, seen[0].model_id) == ("deepseek", "deepseek-chat")
    with pytest.raises(LookupError):
        await rt._start_automation_run("missing", "check", {})
    assert {"create_automation", "list_automations", "delete_automation"} <= set(rt.tools.names())
