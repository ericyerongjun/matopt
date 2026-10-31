import json
import stat
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.hooks import parse_hooks_config
from app.agent.migration import MigrationTargets, detect, import_items, rewrite_instructions
from app.api.migration import MigrationContext, get_migration_context, router


def write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def skill(directory: Path, name: str) -> None:
    write(directory / name / "SKILL.md", f"---\nname: {name}\ndescription: d\n---\nbody\n")


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "proj"
    project.mkdir()
    write(home / ".claude.json", {
        "mcpServers": {
            "fs": {"type": "stdio", "command": "npx", "args": ["fs"], "env": {"TOKEN": "s3cret"}},
            "off": {"command": "x", "disabled": True},
        },
        "projects": {str(project): {"mcpServers": {"proj-db": {"type": "http", "url": "https://db.test/mcp",
                                                               "headers": {"Authorization": "Bearer t"}}}},
                     "/elsewhere": {"mcpServers": {"nope": {"command": "x"}}}},
    })
    write(project / ".mcp.json", {"mcpServers": {"existing": {"command": "y"}, "fresh": {"command": "z"}}})
    skill(home / ".claude" / "skills", "alpha")
    skill(home / ".claude" / "skills", "taken")
    write(home / ".claude" / "CLAUDE.md", "Claude Code should read CLAUDE.md. See https://claude.ai/CLAUDE.md "
                                           "and ~/.claude/skills.")
    write(project / ".claude" / "settings.json", {"hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo pre"},
                                                     {"type": "prompt", "prompt": "x"}]}],
        "Notification": [{"hooks": [{"type": "command", "command": "echo n"}]}],
    }})
    write(home / ".cursor" / "mcp.json", {"mcpServers": {"cur": {"url": "https://c.test/sse", "type": "sse"}}})
    write(project / ".cursorrules", "Cursor rules in .cursorrules")
    skill(project / ".cursor" / "skills", "beta")
    write(home / ".codex" / "config.toml", """
[mcp_servers.docs]
command = "docs-mcp"
args = ["--stdio"]
env = { KEY = "v" }
tool_timeout_sec = 30
env_vars = ["HOME"]

[mcp_servers.remote]
url = "https://r.test/mcp"
http_headers = { "X-Team" = "a" }

[mcp_servers.disabled]
command = "q"
enabled = false
""")
    skill(home / ".codex" / "skills", "gamma")
    write(home / ".codex" / "AGENTS.md", "Codex notes")

    matopt = home / ".matopt"
    targets = MigrationTargets(
        mcp_config=tmp_path / "mcp.json", skills_dir=matopt / "skills",
        agents_md=matopt / "AGENTS.md", hooks_path=tmp_path / "data" / "hooks.json")
    write(targets.mcp_config, {"mcpServers": {"existing": {"command": "keep"}}})
    skill(targets.skills_dir, "taken")
    return home, [project], targets


def by(items, source, item_type, scope=None):
    found = [i for i in items if i["source"] == source and i["item_type"] == item_type
             and (scope is None or i["scope"] == scope)]
    assert found, (source, item_type, scope)
    return found


def test_detect(env):
    home, projects, targets = env
    items = detect(home, projects, targets)
    ids = [i["id"] for i in items]
    assert len(ids) == len(set(ids))
    assert ids == [i["id"] for i in detect(home, projects, targets)]   # stable

    claude_mcp = {i["scope"]: i for i in by(items, "claude", "mcp_servers")}
    assert claude_mcp["home"]["count"] == 1 and "fs" in claude_mcp["home"]["summary"]
    project_mcp = [i for i in by(items, "claude", "mcp_servers", "project")]
    assert {i["path"] for i in project_mcp} == {str(home / ".claude.json"), str(projects[0] / ".mcp.json")}
    mcp_json = next(i for i in project_mcp if i["path"].endswith(".mcp.json"))
    assert mcp_json["conflicts"] == ["existing"]
    assert "nope" not in json.dumps(items)

    assert by(items, "claude", "skills")[0]["conflicts"] == ["taken"]
    assert by(items, "cursor", "skills")[0]["count"] == 1
    assert by(items, "codex", "skills")[0]["count"] == 1
    assert len(by(items, "claude", "instructions")) == 1
    assert by(items, "cursor", "instructions")[0]["path"].endswith(".cursorrules")
    assert by(items, "codex", "instructions")
    hooks = by(items, "claude", "hooks")[0]
    assert hooks["count"] == 1 and "Notification" in hooks["summary"]
    codex_mcp = by(items, "codex", "mcp_servers")[0]
    assert codex_mcp["count"] == 2
    assert "s3cret" not in json.dumps(items)


def test_import_everything(env):
    home, projects, targets = env
    items = detect(home, projects, targets)
    results = import_items([i["id"] for i in items] + ["missing"], home=home, project_dirs=projects,
                           targets=targets)
    status = {r["id"]: r for r in results}
    assert status["missing"]["status"] == "failed"
    assert all(r["status"] == "imported" for r in results if r["id"] != "missing"), results
    assert all("s3cret" not in r["detail"] for r in results)

    mcp = json.loads(targets.mcp_config.read_text())["mcpServers"]
    assert mcp["existing"] == {"command": "keep"}
    assert mcp["fs"] == {"command": "npx", "args": ["fs"], "env": {"TOKEN": "s3cret"}}
    assert mcp["proj-db"] == {"url": "https://db.test/mcp", "transport": "http",
                              "headers": {"Authorization": "Bearer t"}}
    assert mcp["cur"]["transport"] == "sse"
    assert mcp["docs"] == {"command": "docs-mcp", "args": ["--stdio"], "env": {"KEY": "v"}, "tool_timeout_sec": 30}
    assert mcp["remote"] == {"url": "https://r.test/mcp", "transport": "http", "headers": {"X-Team": "a"}}
    assert "disabled" not in mcp and "off" not in mcp and "fresh" in mcp

    assert sorted(p.name for p in targets.skills_dir.iterdir()) == ["alpha", "beta", "gamma", "taken"]
    assert (targets.skills_dir / "taken" / "SKILL.md").read_text().startswith("---\nname: taken")

    agents = targets.agents_md.read_text()
    assert "MatOpt should read AGENTS.md" in agents
    assert "https://claude.ai/CLAUDE.md" in agents and "~/.claude/skills" in agents
    assert "MatOpt rules in AGENTS.md" in agents
    assert "Codex notes" in agents
    assert agents.count("Imported from") == 3

    hooks = json.loads(targets.hooks_path.read_text())
    parse_hooks_config(hooks)
    assert list(hooks["hooks"]) == ["PreToolUse"]
    assert hooks["hooks"]["PreToolUse"][0]["hooks"] == [{"type": "command", "command": "echo pre"}]
    assert stat.S_IMODE(targets.hooks_path.stat().st_mode) == 0o600

    # Second run: everything is already there.
    again = import_items([i["id"] for i in detect(home, projects, targets)], home=home,
                         project_dirs=projects, targets=targets)
    assert all(r["status"] == "skipped" for r in again), again
    assert targets.agents_md.read_text() == agents


def test_import_refuses_to_clobber_invalid_mcp_json(env):
    home, projects, targets = env
    targets.mcp_config.write_text("{broken")
    item = by(detect(home, projects, targets), "claude", "mcp_servers", "home")[0]
    [result] = import_items([item["id"]], home=home, project_dirs=projects, targets=targets)
    assert result["status"] == "failed"
    assert targets.mcp_config.read_text() == "{broken"


def test_new_mcp_json_is_private(env):
    home, projects, targets = env
    targets.mcp_config.unlink()
    item = by(detect(home, projects, targets), "claude", "mcp_servers", "home")[0]
    [result] = import_items([item["id"]], home=home, project_dirs=projects, targets=targets)
    assert result["status"] == "imported" and "secrets" in result["detail"]
    assert stat.S_IMODE(targets.mcp_config.stat().st_mode) == 0o600


def test_rewrite():
    text = "Claude Code and claude-code use CLAUDE.md; ask Claude. Keep ~/.claude/x, claude.ai and a@claude.com"
    out = rewrite_instructions(text, "claude")
    assert out == "MatOpt and MatOpt use AGENTS.md; ask MatOpt. Keep ~/.claude/x, claude.ai and a@claude.com"
    assert rewrite_instructions("Cursor reads .cursorrules", "cursor") == "MatOpt reads AGENTS.md"
    assert rewrite_instructions("Claude", "codex") == "Claude"


def test_api(env):
    home, projects, targets = env
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_migration_context] = lambda: MigrationContext(home, projects, targets)
    client = TestClient(app)
    items = client.get("/api/migration/detect").json()["items"]
    skills = by(items, "codex", "skills")[0]
    r = client.post("/api/migration/import", json={"items": [skills["id"]]}).json()
    assert r["results"][0]["status"] == "imported" and r["restart_required"] is True
    assert (targets.skills_dir / "gamma").is_dir()
