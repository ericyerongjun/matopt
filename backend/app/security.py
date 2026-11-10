"""The backend's HTTP boundary: where it may listen and who may call it.

MatOpt's API runs code, so three checks sit in front of every route:

* ``Host`` must name this server, which stops DNS rebinding.
* A browser's state-changing request must come from a configured origin or
  from the page's own origin, which stops cross-site form posts.
* With ``API_AUTH_TOKEN`` set, ``/api`` and the file mounts require that token,
  as a bearer header or as the cookie from the sign-in page. Browsers cannot
  attach headers to ``<img>`` file links, hence the cookie.

Without a token the API is open to whatever can reach it, so ``check_bind``
refuses to start on a non-loopback address unless told the network in front is
already private. ``/mcp`` authenticates itself (``app.agent.mcp.auth``).
"""

from __future__ import annotations

import hashlib
import hmac
import html
import ipaddress
from typing import Any, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse

from app.config import settings

COOKIE_NAME = "matopt_session"
COOKIE_MAX_AGE = 30 * 24 * 3600
MIN_TOKEN_LENGTH = 16
LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
TOKEN_PREFIXES = ("/api/", "/uploads/", "/images/")
PUBLIC_PATHS = {"/api/auth/status", "/api/auth/login"}


def is_loopback(host: str) -> bool:
    name = host.strip("[]").lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def check_bind(config: Any) -> None:
    """Refuse a configuration that would publish an unauthenticated API."""
    token = config.api_auth_token
    if token and len(token) < MIN_TOKEN_LENGTH:
        raise RuntimeError(f"API_AUTH_TOKEN must be at least {MIN_TOKEN_LENGTH} characters.")
    if token or is_loopback(config.host) or config.allow_unauthenticated_bind:
        return
    raise RuntimeError(
        f"Refusing to listen on {config.host} without authentication: anyone who can reach this "
        "address could run code on this machine. Set HOST=127.0.0.1 for local use, or set "
        "API_AUTH_TOKEN (at least 16 characters) to expose it."
    )


def _hostname(netloc: str) -> Optional[str]:
    try:
        name = urlsplit(f"//{netloc}").hostname
    except ValueError:
        return None
    return name.lower() if name else None


def _session_value(token: str) -> str:
    """The cookie holds a derivative, so it never reveals the token itself."""
    return hmac.new(token.encode(), b"matopt-session", hashlib.sha256).hexdigest()


def is_authenticated(conn: HTTPConnection, token: str) -> bool:
    scheme, _, supplied = conn.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and hmac.compare_digest(supplied.strip().encode(), token.encode()):
        return True
    return hmac.compare_digest(conn.cookies.get(COOKIE_NAME, "").encode(), _session_value(token).encode())


class HttpBoundary:
    """Pure ASGI, so streamed responses pass through without buffering."""

    def __init__(self, app: Any, config: Any = settings):
        self.app = app
        self.config = config
        origins = list(config.cors_origins)
        self.any_origin = "*" in origins
        self.origins = {origin.rstrip("/").lower() for origin in origins}
        hosts = [*LOOPBACK_NAMES, *config.allowed_hosts]
        hosts += [name for name in (_hostname(urlsplit(o).netloc) for o in origins if o != "*") if name]
        public = urlsplit(config.mcp_public_url or "").netloc
        if public:
            hosts.append(_hostname(public) or "")
        if config.host not in ("0.0.0.0", "::"):
            hosts.append(config.host)
        self.any_host = "*" in hosts
        self.hosts = {host.lower() for host in hosts}

    def _origin_allowed(self, origin: str, host: str) -> bool:
        origin = origin.rstrip("/").lower()
        return self.any_origin or origin in self.origins or urlsplit(origin).netloc == host.lower()

    def _needs_token(self, path: str) -> bool:
        return bool(self.config.api_auth_token) and path.startswith(TOKEN_PREFIXES) and path not in PUBLIC_PATHS

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        conn = HTTPConnection(scope)
        host = conn.headers.get("host", "")
        method = scope.get("method", "GET")
        origin = conn.headers.get("origin")
        if not self.any_host and _hostname(host) not in self.hosts:
            await self._reject(scope, receive, send, 400, "Invalid Host header. Add this host to ALLOWED_HOSTS.")
        elif origin is not None and (scope["type"] == "websocket" or method not in SAFE_METHODS) \
                and not self._origin_allowed(origin, host):
            await self._reject(scope, receive, send, 403, "Cross-origin request refused.")
        elif method != "OPTIONS" and self._needs_token(scope["path"]) and not is_authenticated(conn, self.config.api_auth_token):
            await self._reject(scope, receive, send, 401, "Authentication required.")
        else:
            await self.app(scope, receive, send)

    @staticmethod
    async def _reject(scope: dict, receive: Any, send: Any, status: int, detail: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)


auth_router = APIRouter(prefix="/api/auth", tags=["auth"])

_LOGIN_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                               "base-uri 'none'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
}


def _login_page(error: str = "") -> str:
    message = f'<p role="alert">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in to MatOpt</title>
<style>body{{font:16px system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0;padding:16px;box-sizing:border-box}}
form{{display:grid;gap:12px;width:min(320px,100%)}}input,button{{font:inherit;padding:8px}}p{{margin:0;color:#b42318}}</style></head>
<body><form method="post" action="/api/auth/login"><h1>Sign in to MatOpt</h1>{message}
<label for="token">Access token</label>
<input id="token" name="token" type="password" autocomplete="current-password" required autofocus>
<button type="submit">Sign in</button></form></body></html>"""


@auth_router.get("/status")
async def auth_status(request: Request) -> dict:
    token = settings.api_auth_token
    return {"auth_required": bool(token), "authenticated": not token or is_authenticated(request, token)}


@auth_router.get("/login", response_class=HTMLResponse)
async def login_form() -> HTMLResponse:
    return HTMLResponse(_login_page(), headers=_LOGIN_HEADERS)


@auth_router.post("/login", response_class=HTMLResponse)
async def login(request: Request, token: str = Form(...)):
    expected = settings.api_auth_token
    if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
        return HTMLResponse(_login_page("That token is not valid."), status_code=401, headers=_LOGIN_HEADERS)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(COOKIE_NAME, _session_value(expected), max_age=COOKIE_MAX_AGE, httponly=True,
                        samesite="strict", secure=request.url.scheme == "https", path="/")
    return response
