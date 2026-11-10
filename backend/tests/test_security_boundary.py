from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.agent.mcp.auth import StaticBearer, build_mcp_auth
from app.agent.sandbox import SandboxPolicy, build_policy, seatbelt_profile
from app.agent.workspace import Workspace, WorkspaceError
from app.config import Settings, settings
from app.main import app
from app.security import COOKIE_NAME, HttpBoundary, _session_value, check_bind
from data_agent_app.api.v1.files import is_allowed_oss_url
from data_agent_app.services.local_code_execution import LocalCodeExecutionService

TOKEN = "correct-horse-battery-staple"


def config(**overrides):
    base = {"host": "127.0.0.1", "api_auth_token": None, "allow_unauthenticated_bind": False,
            "cors_origins": ["http://localhost:5173"], "allowed_hosts": [], "mcp_public_url": None}
    return SimpleNamespace(**{**base, **overrides})


def boundary_client(**overrides):
    async def echo(_request):
        return PlainTextResponse("ok")

    routes = [Route(path, echo, methods=["GET", "POST"]) for path in ("/api/thing", "/api/auth/status", "/health")]
    wrapped = HttpBoundary(Starlette(routes=routes), config(**overrides))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=wrapped), base_url="http://localhost:8000")


def test_secure_defaults():
    assert Settings.model_fields["host"].default == "127.0.0.1"
    assert Settings.model_fields["sandbox_mode"].default == "workspace-write"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_bind_needs_no_token(host):
    check_bind(config(host=host))


@pytest.mark.parametrize("overrides", [{"api_auth_token": TOKEN}, {"allow_unauthenticated_bind": True}])
def test_open_bind_is_allowed_only_when_declared(overrides):
    check_bind(config(host="0.0.0.0", **overrides))


def test_open_bind_without_token_refuses_to_start():
    with pytest.raises(RuntimeError, match="API_AUTH_TOKEN"):
        check_bind(config(host="0.0.0.0"))


def test_short_token_refuses_to_start():
    with pytest.raises(RuntimeError, match="at least 16"):
        check_bind(config(api_auth_token="short"))


async def test_dns_rebinding_host_is_refused():
    async with boundary_client() as client:
        assert (await client.get("/api/thing", headers={"host": "evil.example"})).status_code == 400
        assert (await client.get("/api/thing")).status_code == 200


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://localhost:3001"])
async def test_foreign_origin_cannot_change_state(origin):
    async with boundary_client() as client:
        assert (await client.post("/api/thing", headers={"origin": origin})).status_code == 403


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://localhost:8000"])
async def test_configured_and_same_origin_requests_pass(origin):
    async with boundary_client() as client:
        assert (await client.post("/api/thing", headers={"origin": origin})).status_code == 200


async def test_token_mode_accepts_bearer_or_session_cookie_only():
    async with boundary_client(api_auth_token=TOKEN) as client:
        assert (await client.get("/api/thing")).status_code == 401
        assert (await client.get("/api/thing", headers={"authorization": "Bearer wrong-token-value"})).status_code == 401
        assert (await client.get("/api/thing", headers={"authorization": f"Bearer {TOKEN}"})).status_code == 200
        cookie = {"cookie": f"{COOKIE_NAME}={_session_value(TOKEN)}"}
        assert (await client.get("/api/thing", headers=cookie)).status_code == 200
        assert (await client.get("/api/auth/status")).status_code == 200
        assert (await client.get("/health")).status_code == 200


async def test_sign_in_sets_an_http_only_cookie(monkeypatch):
    monkeypatch.setattr(settings, "api_auth_token", TOKEN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        refused = await client.post("/api/auth/login", data={"token": "wrong-token-value"})
        assert refused.status_code == 401 and "set-cookie" not in refused.headers
        accepted = await client.post("/api/auth/login", data={"token": TOKEN})
        assert accepted.status_code == 303
        cookie = accepted.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie and TOKEN not in cookie


def test_api_token_also_protects_mcp():
    verifier = build_mcp_auth(SimpleNamespace(mcp_auth_token=None, api_auth_token=TOKEN, mcp_oauth_issuer=None,
                                              mcp_oauth_jwks_uri=None))
    assert isinstance(verifier, StaticBearer)


@pytest.mark.parametrize("conversation_id", ["..", ".", "../x", "a/b", ".hidden", "", "x" * 129])
def test_workspace_rejects_ids_that_are_not_one_directory(conversation_id, tmp_path):
    with pytest.raises(WorkspaceError):
        Workspace(conversation_id, tmp_path)


@pytest.mark.parametrize("conversation_id", ["c_0123abcd", "ocr-tests", "v1.2"])
def test_workspace_accepts_ordinary_ids(conversation_id, tmp_path):
    assert Workspace(conversation_id, tmp_path).root == (tmp_path / conversation_id).resolve()


async def test_dot_dot_conversation_cannot_reach_the_data_directory():
    canary = Path(settings.data_dir) / "canary.txt"
    canary.write_text("keep")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.delete("/api/conversations/%2E%2E")).status_code == 404
        assert (await client.get("/api/files/%2E%2E/canary.txt")).status_code == 400
        assert (await client.delete("/api/conversations/%2E%2E/files/canary.txt")).status_code == 400
    assert canary.read_text() == "keep"


async def test_unused_dangerous_routes_are_gone():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/v1/code/execute", json={"code": "1"})).status_code == 404
        assert (await client.get("/api/v1/files/proxy", params={"url": "https://x"})).status_code == 404
        assert (await client.post("/api/v1/chat/stream", json={})).status_code == 404
        assert (await client.post("/api/v1/files/upload", data={})).status_code == 404
        assert (await client.get("/api/v1/conversations")).status_code == 404
        assert (await client.post("/api/v1/export/report/1", json={})).status_code == 404
        assert (await client.get("/api/v1/health")).status_code == 404


async def test_generated_file_links_still_resolve():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/v1/files/content")).status_code == 422  # route exists, url is required


@pytest.mark.parametrize(("url", "allowed"), [
    ("https://data-app-prod.oss-cn-hongkong.aliyuncs.com/a.png", True),
    ("https://evil.example/x.html?data-app-prod.oss-cn-hongkong.aliyuncs.com", False),
    ("https://data-app-prod.oss-cn-hongkong.aliyuncs.com.evil.example/a", False),
    ("file:///data-app-prod.oss-cn-hongkong.aliyuncs.com", False),
])
def test_oss_allowlist_matches_the_exact_host(url, allowed):
    assert is_allowed_oss_url(url) is allowed


def test_seatbelt_closes_the_backends_ports_when_network_is_on():
    open_network = seatbelt_profile(SandboxPolicy(mode="workspace-write", backend="seatbelt",
                                                  blocked_loopback_ports=(5173, 8000)))
    assert '(deny network-outbound (remote ip "localhost:8000"))' in open_network
    assert '(deny network-outbound (remote ip "localhost:5173"))' in open_network


def test_policy_lists_backend_and_proxy_ports(tmp_path):
    settings_like = SimpleNamespace(data_dir=tmp_path / "data", sandbox_mode="workspace-write", sandbox_network=True,
                                    sandbox_backend="seatbelt", port=8000, cors_origins=["http://localhost:5173"])
    assert build_policy(settings_like, tmp_path / "ws").blocked_loopback_ports == (5173, 8000)


async def test_in_process_execution_refuses_while_sandbox_is_on(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "sandbox_mode", "workspace-write")
    marker = tmp_path / "ran"
    result = await LocalCodeExecutionService().execute(f"open({str(marker)!r}, 'w')", cwd=tmp_path)
    assert result["success"] is False and not marker.exists()
