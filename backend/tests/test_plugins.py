import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.hooks import parse_hooks_config
from app.agent.plugins import ManifestError, PluginExistsError, PluginManager, PluginNotFoundError, load_manifest
from app.agent.skills.loader import SkillRegistry
from app.api.plugins import get_plugin_manager, router


def write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def make_plugin(base: Path, name="lab-tools", manifest_dir=".matopt-plugin", **extra) -> Path:
    root = base / f"src-{name}"
    write(root / manifest_dir / "plugin.json", {"name": name, "version": "1.0.0",
                                                "description": "Lab helpers", **extra})
    write(root / "skills" / "titrate" / "SKILL.md",
          "---\nname: titrate\ndescription: Titration helper\n---\nBody\n")
    write(root / ".mcp.json", {"mcpServers": {"chem": {"command": "${PLUGIN_ROOT}/bin/chem", "args": ["--x"]}}})
    write(root / "hooks" / "hooks.json", {"hooks": {"PreToolUse": [
        {"matcher": "bash", "hooks": [{"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/check.sh"}]}]}})
    return root


@pytest.fixture
def manager(tmp_path):
    settings = SimpleNamespace(plugins_dir=tmp_path / "store", data_dir=tmp_path / "data")
    return PluginManager(settings)


def test_manifest_defaults_and_alternate_dirs(tmp_path):
    for i, d in enumerate((".matopt-plugin", ".codex-plugin", ".claude-plugin")):
        m = load_manifest(make_plugin(tmp_path, name=f"p{i}", manifest_dir=d))
        assert m.name == f"p{i}"
        assert m.skill_names() == ["titrate"]
        assert list(m.mcp_servers) == ["chem"]
        assert list(m.hooks) == ["PreToolUse"]
        assert m.errors == []


@pytest.mark.parametrize("name", ["Bad", "-x", "a/b", "", "x" * 65, 3])
def test_manifest_rejects_bad_names(tmp_path, name):
    root = tmp_path / "p"
    write(root / ".matopt-plugin" / "plugin.json", {"name": name})
    with pytest.raises(ManifestError):
        load_manifest(root)


def test_manifest_rejects_escaping_paths(tmp_path):
    root = tmp_path / "p"
    write(tmp_path / "outside" / "mcp.json", {"mcpServers": {"x": {"command": "evil"}}})
    write(root / ".matopt-plugin" / "plugin.json", {
        "name": "p", "skills": ["../outside", "/etc"], "mcpServers": "../outside/mcp.json",
        "hooks": {"hooks": {"Nope": []}}})
    os.symlink(tmp_path / "outside", root / "link")
    m = load_manifest(root)
    assert m.skill_dirs == [] and m.mcp_servers == {} and m.hooks == {}
    assert len(m.errors) == 4
    with pytest.raises(ValueError):
        from app.agent.plugins.manifest import resolve_component_path
        resolve_component_path(root, "link/mcp.json")


def test_inline_components(tmp_path):
    root = tmp_path / "p"
    write(root / ".matopt-plugin" / "plugin.json", {
        "name": "p", "mcpServers": {"web": {"url": "https://x.test/mcp"}, "bad": {}},
        "hooks": {"Stop": [{"hooks": [{"command": "echo hi"}]}]},
        "interface": {"displayName": "P!", "category": "science"}})
    m = load_manifest(root)
    assert list(m.mcp_servers) == ["web"]
    assert list(m.hooks) == ["Stop"]
    assert any("bad" in e for e in m.errors)
    assert m.interface == {"displayName": "P!", "category": "science"}


def test_install_enable_disable_remove(manager, tmp_path):
    src = make_plugin(tmp_path)
    os.symlink("/etc/passwd", src / "skills" / "leak")
    info = manager.install(str(src))
    assert info["id"] == "lab-tools" and info["enabled"] is True
    assert info["skills"] == 1 and info["mcp_servers"] == ["chem"] and info["hooks"] == ["PreToolUse"]
    installed = manager.store.plugin_root("lab-tools")
    assert not (installed / "skills" / "leak").exists()
    assert not [p for p in manager.store.root.iterdir() if p.name.startswith(".staging")]

    with pytest.raises(PluginExistsError):
        manager.install(str(src))
    write(src / ".matopt-plugin" / "plugin.json", {"name": "lab-tools", "version": "2.0.0"})
    assert manager.install(str(src), reinstall=True)["version"] == "2.0.0"

    # Integration getters.
    assert manager.skill_dirs() == [installed / "skills"]
    assert "titrate" in SkillRegistry.discover(manager.skill_dirs()).names()
    servers = manager.mcp_servers()
    assert servers == {"lab-tools__chem": {"command": f"{installed}/bin/chem", "args": ["--x"]}}
    hooks = manager.hook_config()
    assert hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == f"{installed}/check.sh"
    parse_hooks_config(hooks)

    manager.disable("lab-tools")
    assert manager.list()[0]["enabled"] is False
    assert manager.skill_dirs() == [] and manager.mcp_servers() == {} and manager.hook_config() == {"hooks": {}}
    assert json.loads(manager.store.state_path.read_text())["enabled"] == {"lab-tools": False}
    manager.enable("lab-tools")
    assert manager.mcp_servers()

    manager.remove("lab-tools")
    assert manager.list() == []
    with pytest.raises(PluginNotFoundError):
        manager.enable("lab-tools")
    with pytest.raises(PluginNotFoundError):
        manager.remove("../x")


def test_broken_plugin_is_listed_with_errors(manager, tmp_path):
    good = make_plugin(tmp_path)
    manager.install(str(good))
    write(manager.store.root / "broken" / ".matopt-plugin" / "plugin.json", "{not json")
    listed = {p["id"]: p for p in manager.list()}
    assert listed["broken"]["errors"] and listed["lab-tools"]["errors"] == []
    assert list(manager.mcp_servers()) == ["lab-tools__chem"]


def test_install_rejects_relative_and_missing(manager, tmp_path):
    with pytest.raises(ValueError):
        manager.install("relative/path")
    with pytest.raises(ManifestError):
        manager.install(str(tmp_path / "missing"))


def test_api(manager, tmp_path):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_plugin_manager] = lambda: manager
    client = TestClient(app)
    src = make_plugin(tmp_path)

    assert client.get("/api/plugins").json() == {"plugins": []}
    r = client.post("/api/plugins/install", json={"path": str(src)})
    assert r.status_code == 200 and r.json()["restart_required"] is True
    assert r.json()["plugin"]["id"] == "lab-tools"
    assert client.post("/api/plugins/install", json={"path": str(src)}).status_code == 409
    assert client.post("/api/plugins/install", json={"path": str(tmp_path / "nope")}).status_code == 400
    assert client.post("/api/plugins/lab-tools/disable").json()["plugin"]["enabled"] is False
    assert client.post("/api/plugins/lab-tools/enable").json()["plugin"]["enabled"] is True
    assert client.post("/api/plugins/other/enable").status_code == 404
    assert client.delete("/api/plugins/lab-tools").json()["restart_required"] is True
    assert client.get("/api/plugins").json() == {"plugins": []}
