"""Optional authentication for the HTTP MCP transport.

stdio (``python -m app.agent.mcp``) runs as the local user and needs none.
HTTP ``/mcp`` executes Python and shell commands, so expose it beyond this
machine only with ``MCP_AUTH_TOKEN`` (static bearer) or an OAuth resource
server configuration (``MCP_OAUTH_*`` + ``MCP_PUBLIC_URL``). An incomplete
OAuth configuration raises, which disables the transport rather than serving
it unauthenticated.
"""
from __future__ import annotations

import hmac

from fastmcp.server.auth import AccessToken, RemoteAuthProvider, TokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier


STATIC_CLIENT_ID = "matopt-static-token"


class StaticBearer(TokenVerifier):
    """One shared secret, compared in constant time."""
    def __init__(self, token: str):
        super().__init__()
        self._token = token.encode()

    async def verify_token(self, token: str) -> AccessToken | None:
        if hmac.compare_digest(token.encode(), self._token):
            return AccessToken(token=token, client_id=STATIC_CLIENT_ID, scopes=[])
        return None


def build_mcp_auth(config):
    oauth = (config.mcp_oauth_issuer, config.mcp_oauth_jwks_uri)
    if any(oauth):
        if not all(oauth) or not config.mcp_public_url:
            raise ValueError("MCP OAuth needs MCP_OAUTH_ISSUER, MCP_OAUTH_JWKS_URI and MCP_PUBLIC_URL")
        # The transport is mounted at its own root, so its public base is the
        # mount: the 401 challenge, the RFC 9728 metadata and the resource all
        # resolve to <public>/mcp/... where the mounted app serves them.
        base = config.mcp_public_url.rstrip("/") + config.mcp_mount_path.rstrip("/")
        scopes = [s.strip() for s in config.mcp_oauth_scopes.split(",") if s.strip()] or None
        verifier = JWTVerifier(jwks_uri=config.mcp_oauth_jwks_uri, issuer=config.mcp_oauth_issuer,
                               audience=config.mcp_oauth_audience or base + "/", required_scopes=scopes)
        return RemoteAuthProvider(verifier, [config.mcp_oauth_issuer], base_url=base, resource_name="MatOpt kernel")
    # API_AUTH_TOKEN covers /mcp too, so setting it can never leave this transport open.
    token = config.mcp_auth_token or config.api_auth_token
    if token:
        if len(token) < 16:
            raise ValueError("MCP_AUTH_TOKEN must be at least 16 characters")
        return StaticBearer(token)
    return None
