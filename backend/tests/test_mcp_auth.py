"""Optional authentication on the HTTP MCP transport, as mounted by the app."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("fastmcp")

from httpx import ASGITransport, AsyncClient

import app.agent.mcp.sandbox as sandbox
from app.agent.mcp.auth import StaticBearer, build_mcp_auth
from app.config import settings

INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
HEADERS = {"accept": "application/json, text/event-stream"}
OAUTH = {"mcp_oauth_issuer": "https://auth.example.com/", "mcp_oauth_jwks_uri": "https://auth.example.com/jwks.json",
         "mcp_public_url": "https://matopt.example.com"}


def config(**overrides):
    base = {"mcp_auth_token": None, "mcp_oauth_issuer": None, "mcp_oauth_jwks_uri": None, "mcp_oauth_audience": None,
            "mcp_oauth_scopes": "", "mcp_public_url": None, "mcp_mount_path": "/mcp"}
    return SimpleNamespace(**{**base, **overrides})


def test_auth_is_opt_in_and_incomplete_oauth_fails_closed():
    assert build_mcp_auth(config()) is None
    assert isinstance(build_mcp_auth(config(mcp_auth_token="s" * 32)), StaticBearer)
    with pytest.raises(ValueError):
        build_mcp_auth(config(mcp_auth_token="short"))
    for partial in ({"mcp_oauth_issuer": "https://auth.example.com/"}, {**OAUTH, "mcp_public_url": None}):
        with pytest.raises(ValueError):
            build_mcp_auth(config(**partial))


@pytest.fixture
def mounted(monkeypatch):
    """Build the real FastAPI app with the given MCP settings."""
    def build(**overrides):
        for key, value in {"mcp_enabled": True, **overrides}.items():
            monkeypatch.setattr(settings, key, value)
        monkeypatch.setattr(sandbox, "_mcp", None)
        from app.main import create_app
        app = create_app()
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://matopt.test")
        # Accepted requests reach the MCP session manager; run only its lifespan.
        client.mcp_asgi = next(route.app for route in app.routes if getattr(route, "path", "") == settings.mcp_mount_path)
        return client
    yield build
    sandbox._mcp = None


@pytest.mark.asyncio
async def test_static_bearer_guards_the_mounted_transport(mounted):
    async with mounted(mcp_auth_token="correct-horse-battery-staple") as client:
        assert (await client.post("/mcp/", json=INITIALIZE, headers=HEADERS)).status_code == 401
        wrong = {**HEADERS, "authorization": "Bearer correct-horse-battery-stapl"}
        assert (await client.post("/mcp/", json=INITIALIZE, headers=wrong)).status_code == 401
        right = {**HEADERS, "authorization": "Bearer correct-horse-battery-staple"}
        async with client.mcp_asgi.lifespan(client.mcp_asgi):
            response = await client.post("/mcp/", json=INITIALIZE, headers=right)
    assert response.status_code == 200 and "matopt-sandbox" in response.text


@pytest.mark.asyncio
async def test_oauth_resource_server_advertises_discoverable_metadata(mounted):
    async with mounted(**OAUTH) as client:
        challenge = await client.post("/mcp/", json=INITIALIZE, headers=HEADERS)
        assert challenge.status_code == 401
        url = "https://matopt.example.com/.well-known/oauth-protected-resource/mcp/"
        assert f'resource_metadata="{url}"' in challenge.headers["www-authenticate"]
        metadata = (await client.get("/.well-known/oauth-protected-resource/mcp/")).json()
        forged = await client.post("/mcp/", json=INITIALIZE, headers={**HEADERS, "authorization": "Bearer not-a-jwt"})
    assert metadata["resource"] == "https://matopt.example.com/mcp/"
    assert metadata["authorization_servers"] == ["https://auth.example.com/"]
    assert forged.status_code == 401
