"""Second round of the security review: approvals, files, SSRF, MCP, sandbox, body limits."""
import socket
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app import security
from app.agent.approvals import ApprovalBroker, ApprovalContext, approval_trigger, decide
from app.agent.exec_rules import ExecPolicy
from app.agent.mcp import sandbox as mcp_sandbox
from app.agent.sandbox import build_policy, bwrap_argv, seatbelt_profile
from app.agent.tools import web_search
from app.config import settings as app_settings
from app.data.data_agent_host import IsolatedStaticFiles
from app.security import HttpBoundary, file_headers


def tool(name, **attrs):
    return SimpleNamespace(name=name, **attrs)


@pytest.fixture
def sandboxed(monkeypatch):
    monkeypatch.setattr(app_settings, "sandbox_mode", "workspace-write")
    monkeypatch.setattr(app_settings, "sandbox_backend", "seatbelt")


# ── approvals ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("code", [
    'import subprocess\nsubprocess.run(["git", "push", "origin", "main"])',
    'import subprocess as sp\nsp.check_output("git push", shell=True)',
    'from subprocess import Popen\nPopen(["git", "push"])',
    'import os\nos.system("git push origin main")',
    '!git push origin main',
    '%%bash\ngit push origin main\n',
])
def test_python_cannot_sidestep_a_prompt_rule(sandboxed, code):
    assert approval_trigger(tool("python"), {"code": code})[0] == "exec_rule_prompt"


def test_python_forbidden_commands_are_denied_without_asking(sandboxed):
    trigger = approval_trigger(tool("python"), {"code": 'import os\nos.system("sudo rm x")'})
    assert trigger[0] == "exec_rule_forbidden"


@pytest.mark.parametrize("command", ["bash -c 'git push'", "sh -lc 'git push'", "eval git push",
                                     """python -c 'import os; os.system("git push")'"""])
def test_nested_commands_reach_the_rules(sandboxed, command):
    assert approval_trigger(tool("bash", exec_policy=ExecPolicy()), {"command": command})[0] == "exec_rule_prompt"


@pytest.mark.parametrize("command", [
    "export PATH=/tmp/node/bin:$PATH\nWS=/tmp/welcome-demo\nls $WS/skills/\ncd /tmp && echo ready",
    "bash -c 'WS=/tmp/demo; echo ready'",
    "env",
])
def test_assignment_only_shell_segments_do_not_crash_approval(sandboxed, command):
    assert approval_trigger(tool("bash", exec_policy=ExecPolicy()), {"command": command}) is None


def test_assignments_do_not_skip_nested_command_approval(sandboxed):
    command = "WS=/tmp/demo\nbash -c 'A=1; git push origin main'"
    assert approval_trigger(tool("bash", exec_policy=ExecPolicy()), {"command": command})[0] == "exec_rule_prompt"


def test_ordinary_code_does_not_ask_inside_the_sandbox(sandboxed):
    assert approval_trigger(tool("python"), {"code": 'import subprocess\nsubprocess.run(["git", "status"])\nx = 1 != 2'}) is None
    assert approval_trigger(tool("bash", exec_policy=ExecPolicy()), {"command": "git status"}) is None


def test_top_level_forbidden_bash_is_left_to_the_shell_tool(sandboxed):
    assert approval_trigger(tool("bash", exec_policy=ExecPolicy()), {"command": "sudo ls"}) is None


@pytest.mark.parametrize("mode,backend,expected", [("off", "auto", True), ("workspace-write", "none", True),
                                                   ("workspace-write", "seatbelt", False), ("external", "auto", False)])
def test_exec_tools_ask_only_without_a_sandbox(monkeypatch, mode, backend, expected):
    monkeypatch.setattr(app_settings, "sandbox_mode", mode)
    monkeypatch.setattr(app_settings, "sandbox_backend", backend)
    trigger = approval_trigger(tool("python"), {"code": "1"})
    assert (trigger is not None and trigger[0] == "unsandboxed") if expected else trigger is None
    assert approval_trigger(tool("bash_poll"), {"session_id": "s"}) is None
    assert approval_trigger(tool("read_file", destructive=False), {}) is None


@pytest.mark.asyncio
async def test_decide_refuses_forbidden_and_deny_reviewer_and_allows_unsandboxed_when_off():
    async def emit(_):
        pass
    ctx = SimpleNamespace(conversation_id="c", cancel=None, call_id="x")
    off = ApprovalContext("off", ApprovalBroker(), emit)
    assert (await decide(off, ctx, tool("python"), {}, "exec_rule_forbidden", "no")).decision == "deny"
    assert (await decide(off, ctx, tool("python"), {}, "unsandboxed", "why")).decision == "allow"
    deny = ApprovalContext("deny", ApprovalBroker(), emit)
    assert (await decide(deny, ctx, tool("python"), {}, "unsandboxed", "why")).decision == "deny"


# ── active content ──────────────────────────────────────────────────────

@pytest.mark.parametrize("media_type", ["text/html", "image/svg+xml", "application/xhtml+xml", "text/xml", None,
                                        "application/octet-stream", "TEXT/HTML; charset=utf-8"])
def test_active_or_unknown_types_download_in_a_sandbox(media_type):
    headers = file_headers(media_type, "x.html")
    assert headers["Content-Disposition"].startswith("attachment")
    assert headers["Content-Security-Policy"].startswith("sandbox")
    assert headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize("media_type", ["image/png", "application/pdf", "text/plain; charset=utf-8", "application/json"])
def test_display_only_types_stay_inline(media_type):
    headers = file_headers(media_type, "x")
    assert headers["Content-Disposition"].startswith("inline") and "Content-Security-Policy" not in headers
    assert file_headers(media_type, "x", download=True)["Content-Disposition"].startswith("attachment")


def test_filenames_cannot_break_the_header():
    value = file_headers("text/plain", 'a"b\r\nc é.txt')["Content-Disposition"]
    assert "\r" not in value and "\n" not in value and 'filename="a_b__c _.txt"' in value and "%C3%A9" in value


@pytest.mark.parametrize("name", ["evil.html", "evil.xhtml", "evil.xht", "evil.svg"])
def test_static_mounts_never_serve_active_content(tmp_path, name):
    (tmp_path / name).write_text("<script>alert(1)</script>")
    app = FastAPI()
    app.mount("/uploads", IsolatedStaticFiles(directory=str(tmp_path)))
    response = TestClient(app).get(f"/uploads/{name}")
    assert response.status_code == 200 and response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["content-security-policy"].startswith("sandbox")


def test_static_mounts_keep_images_inline(tmp_path):
    (tmp_path / "a.png").write_bytes(b"\x89PNG")
    app = FastAPI()
    app.mount("/images", IsolatedStaticFiles(directory=str(tmp_path)))
    response = TestClient(app).get("/images/a.png")
    assert response.headers["content-type"] == "image/png" and response.headers["content-disposition"].startswith("inline")
    assert response.headers["x-content-type-options"] == "nosniff"


# ── web_fetch ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("address", ["127.0.0.2", "10.0.0.1", "169.254.169.254", "100.64.0.1", "::1", "fd00::1",
                                     "::ffff:127.0.0.1", "224.0.0.1", "2002:7f00:1::", "0.0.0.0"])
def test_non_public_addresses_are_refused(address):
    assert not web_search._is_public_ip(address)


def test_public_addresses_are_allowed():
    assert web_search._is_public_ip("93.184.216.34") and web_search._is_public_ip("2606:2800:220:1:248:1893:25c8:1946")


def fake_dns(monkeypatch, table):
    async def getaddrinfo(self, host, port, **_):
        if host not in table:
            raise socket.gaierror("unknown")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in table[host]]
    monkeypatch.setattr("asyncio.base_events.BaseEventLoop.getaddrinfo", getaddrinfo)


def fetch_with(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(web_search.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


async def run_fetch(url):
    async def progress(_):
        pass
    return await web_search.WebFetchTool().run(SimpleNamespace(progress=progress), url=url)


@pytest.mark.asyncio
async def test_fetch_pins_the_checked_address_and_keeps_the_name(monkeypatch):
    fake_dns(monkeypatch, {"example.com": ["93.184.216.34"]})
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, text="<p>hello</p>")
    fetch_with(monkeypatch, handler)
    result = await run_fetch("https://example.com/a?b=1")
    assert result.ok and "hello" in result.output
    assert str(seen[0].url) == "https://93.184.216.34/a?b=1"
    assert seen[0].headers["host"] == "example.com" and seen[0].extensions["sni_hostname"] == "example.com"


@pytest.mark.asyncio
async def test_fetch_rechecks_every_redirect_hop(monkeypatch):
    fake_dns(monkeypatch, {"example.com": ["93.184.216.34"], "rebind.example": ["127.0.0.1"]})
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "http://rebind.example/secret"})
    fetch_with(monkeypatch, handler)
    result = await run_fetch("https://example.com/")
    assert not result.ok and len(seen) == 1


@pytest.mark.asyncio
async def test_fetch_refuses_a_name_with_any_private_address_and_stops_redirect_loops(monkeypatch):
    fake_dns(monkeypatch, {"mixed.example": ["93.184.216.34", "10.0.0.1"], "example.com": ["93.184.216.34"]})
    fetch_with(monkeypatch, lambda request: httpx.Response(302, headers={"location": "/again"}))
    assert not (await run_fetch("https://mixed.example/")).ok
    result = await run_fetch("https://example.com/")
    assert not result.ok and "redirect" in result.error.lower()


# ── MCP ─────────────────────────────────────────────────────────────────

class FakeDb:
    def __init__(self):
        self.rows = {}

    def get_conversation(self, cid):
        return self.rows.get(cid)

    def create_conversation(self, title, conversation_id="generated"):
        self.rows[conversation_id] = {"id": conversation_id, "meta": {}}
        return self.rows[conversation_id]

    def update_conversation(self, cid, **fields):
        self.rows[cid].update(fields)


def mcp_runtime():
    db = FakeDb()
    return SimpleNamespace(db=db, workspace=lambda cid: SimpleNamespace(list_files=lambda: []))


def test_oauth_clients_only_reach_their_own_workspaces(monkeypatch):
    runtime = mcp_runtime()
    box = mcp_sandbox.McpSandbox(runtime)
    runtime.db.create_conversation("native", conversation_id="native")
    monkeypatch.setattr(mcp_sandbox, "_principal", lambda: "alice")
    mine = box.open_workspace("mine")["conversation_id"]
    assert box._accessible(mine)
    with pytest.raises(ValueError):
        box.open_workspace("native")
    monkeypatch.setattr(mcp_sandbox, "_principal", lambda: "bob")
    assert not box._accessible(mine)
    with pytest.raises(ValueError):
        box.open_workspace("mine")


def test_operator_connections_reach_any_workspace(monkeypatch):
    runtime = mcp_runtime()
    runtime.db.create_conversation("native", conversation_id="native")
    monkeypatch.setattr(mcp_sandbox, "_principal", lambda: None)
    box = mcp_sandbox.McpSandbox(runtime)
    assert box._accessible("native") and box.open_workspace("native")["conversation_id"] == "native"


# ── sandbox ─────────────────────────────────────────────────────────────

def linux_settings(tmp_path, **kw):
    return SimpleNamespace(**{"data_dir": tmp_path / "data", "sandbox_mode": "workspace-write", "sandbox_network": True,
                              "sandbox_backend": "bwrap", "api_auth_token": None, **kw})


def test_bwrap_network_needs_the_api_token(tmp_path):
    ws = tmp_path / "w"
    ws.mkdir()
    assert not build_policy(linux_settings(tmp_path), ws).network
    assert build_policy(linux_settings(tmp_path, api_auth_token="x" * 16), ws).network


def test_seatbelt_keeps_network_because_it_blocks_the_api_ports(tmp_path):
    ws = tmp_path / "w"
    ws.mkdir()
    settings = linux_settings(tmp_path, sandbox_backend="seatbelt", port=8000, cors_origins=[])
    policy = build_policy(settings, ws)
    assert policy.network and 8000 in policy.blocked_loopback_ports


def test_mcp_config_is_denied(tmp_path):
    ws = tmp_path / "w"
    ws.mkdir()
    config = tmp_path / "mcp.json"
    config.write_text("{}")
    settings = linux_settings(tmp_path, sandbox_backend="seatbelt", resolved_mcp_config_path=lambda: config)
    policy = build_policy(settings, ws)
    assert config.resolve() in policy.deny_read
    assert f'(deny file-read* (subpath "{config.resolve()}"))' in seatbelt_profile(policy)


def test_each_conversation_gets_its_own_temp_directory(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir(), second.mkdir()
    settings = linux_settings(tmp_path, sandbox_backend="seatbelt")
    a, b = build_policy(settings, first), build_policy(settings, second)
    assert a.temp_dir != b.temp_dir and Path(a.temp_dir).is_dir()
    assert a.child_env()["TMPDIR"] == a.temp_dir and a.child_env()["TEMP"] == a.temp_dir
    # The directory is short enough for Python's AF_UNIX sockets beneath it.
    assert len(a.temp_dir) + len("/pymp-xxxxxxxx/listener-xxxxxxxx") < 104
    parent, own = Path(a.temp_dir).resolve().parent, Path(a.temp_dir).resolve()
    assert parent in a.deny_read and own in a.allow_read and own in a.writable_roots
    assert Path(b.temp_dir).resolve() not in a.writable_roots
    profile = seatbelt_profile(a)
    assert profile.index(f'(deny file-read* (subpath "{parent}"))') < profile.index(f'(allow file-read* (subpath "{own}"))')


def test_bwrap_masks_are_not_undone_by_a_later_temp_bind(tmp_path):
    ws = tmp_path / "w"
    ws.mkdir()
    policy = build_policy(linux_settings(tmp_path), ws)
    argv = bwrap_argv(policy)
    parent, own = Path(policy.temp_dir).resolve().parent, Path(policy.temp_dir).resolve()
    temp_root = next(root for root in policy.writable_roots if parent.is_relative_to(root))
    position = lambda flag, path: next(i for i in range(len(argv) - 1) if argv[i] == flag and argv[i + 1] == str(path))
    assert position("--bind", temp_root) < position("--tmpfs", parent) < position("--bind", own)


# ── HTTP boundary ───────────────────────────────────────────────────────

def boundary_config(**kw):
    return SimpleNamespace(**{"cors_origins": [], "allowed_hosts": ["*"], "mcp_public_url": None, "host": "127.0.0.1",
                              "api_auth_token": None, "sandbox_mode": "workspace-write", "max_request_body_mb": 1,
                              "settings_api_writable": True, "mcp_auth_token": None, "mcp_mount_path": "/mcp", **kw})


def boundary_app(**kw):
    app = FastAPI()

    @app.get("/api/x")
    async def x():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/mcp/x")
    async def mcp():
        return {"ok": True}

    @app.post("/api/echo")
    async def echo(request: Request):
        return {"size": len(await request.body())}

    @app.post("/api/memories/notes")
    async def note():
        return {"ok": True}

    app.add_middleware(HttpBoundary, config=boundary_config(**kw))
    return app


@pytest.mark.parametrize("client", [("127.0.0.1", 5000), ("::1", 5000), ("172.20.0.3", 5000)])
def test_external_mode_refuses_callers_on_this_host(client):
    with TestClient(boundary_app(sandbox_mode="external"), client=client, base_url="http://172.20.0.3:8000") as http:
        assert http.get("/api/x").status_code == 403
        assert http.get("/health").status_code == 200


def test_external_mode_uses_the_peer_nginx_saw_and_allows_other_hosts():
    http = TestClient(boundary_app(sandbox_mode="external"), client=("172.20.0.9", 5000))
    assert http.get("/api/x").status_code == 200
    assert http.get("/api/x", headers={"X-Real-IP": "127.0.0.1"}).status_code == 403
    assert http.get("/mcp/x", headers={"X-Real-IP": "127.0.0.1"}).status_code == 403


def test_a_token_replaces_the_host_rule():
    token = "t" * 16
    http = TestClient(boundary_app(sandbox_mode="external", api_auth_token=token), client=("127.0.0.1", 5000))
    assert http.get("/api/x").status_code == 401
    assert http.get("/api/x", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_other_modes_keep_local_callers():
    assert TestClient(boundary_app(), client=("127.0.0.1", 5000)).get("/api/x").status_code == 200


def test_body_cap_uses_content_length_and_counts_chunked_bodies():
    http = TestClient(boundary_app())
    assert http.post("/api/echo", content=b"x" * 10).json() == {"size": 10}
    assert http.post("/api/echo", content=b"x" * (1024 * 1024 + 1)).status_code == 413

    def chunks():
        for _ in range(3):
            yield b"x" * (512 * 1024)
    assert http.post("/api/echo", content=chunks()).status_code == 413


def test_settings_api_flag_covers_memory_writes():
    assert TestClient(boundary_app()).post("/api/memories/notes").status_code == 200
    assert TestClient(boundary_app(settings_api_writable=False)).post("/api/memories/notes").status_code == 403


def test_harden_process_marks_the_process_non_dumpable_only_on_linux(monkeypatch):
    calls = []
    monkeypatch.setattr(security.ctypes, "CDLL", lambda *a, **k: SimpleNamespace(prctl=lambda *args: calls.append(args) or 0))
    monkeypatch.setattr(security.sys, "platform", "darwin")
    security.harden_process()
    assert calls == []
    monkeypatch.setattr(security.sys, "platform", "linux")
    security.harden_process()
    assert calls == [(security.PR_SET_DUMPABLE, 0, 0, 0, 0)]


# ── deployment files ────────────────────────────────────────────────────

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name", ["Dockerfile", "backend/Dockerfile"])
def test_images_keep_the_code_root_owned(name):
    text = (REPO / name).read_text()
    assert "chown -R matopt:matopt /app/data" in text and "chown -R matopt:matopt /app &&" not in text
    assert not any(line.rstrip().endswith("matopt:matopt /app") for line in text.splitlines())
    assert "--no-proxy-headers" in text


def test_nginx_caps_request_bodies():
    text = (REPO / "frontend/nginx.conf.template").read_text()
    assert "client_max_body_size 1024m;" in text and "client_max_body_size 0" not in text
