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

Agent code shares this machine, so two more rules assume it may be the caller:
under ``SANDBOX_MODE=external`` a request from this host without a token is
refused, and ``harden_process`` keeps the backend's memory and environment
unreadable to its own children.
"""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import html
import ipaddress
import logging
import re
import sys
from typing import Any, Optional
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
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
OPERATOR_WRITE_PREFIXES = ("/api/memories",)
PR_SET_DUMPABLE = 4
logger = logging.getLogger(__name__)

# Types a browser only displays. Anything else (HTML, SVG, XML, XHTML, unknown)
# could run script as this origin if opened directly, so it is downloaded.
INLINE_MEDIA_TYPES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "application/pdf", "application/json",
    "text/plain", "text/markdown", "text/csv", "text/x-python",
    "audio/wav", "audio/x-wav", "audio/mpeg", "audio/ogg", "video/mp4", "video/webm",
})
ISOLATED_FILE_CSP = ("sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
                     "form-action 'none'; base-uri 'none'")


def file_headers(media_type: Optional[str], filename: str, *, download: bool = False) -> dict[str, str]:
    """Response headers for serving a stored file from the application's origin."""
    inline = not download and (media_type or "").split(";")[0].strip().lower() in INLINE_MEDIA_TYPES
    plain = re.sub(r'[^A-Za-z0-9._ -]', "_", filename) or "file"
    headers = {"X-Content-Type-Options": "nosniff",
               "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename=\"{plain}\"; "
                                      f"filename*=UTF-8''{quote(filename, safe='')}"}
    if not inline:
        headers["Content-Security-Policy"] = ISOLATED_FILE_CSP
    return headers


def harden_process() -> None:
    """Make /proc/<pid>/environ and mem of this process unreadable to its children.

    Agent code runs as the backend's own user, and in a container that is the
    only barrier around API keys in the environment. execve resets the flag, so
    kernels and shells start normally.
    """
    if not sys.platform.startswith("linux"):
        return
    try:
        if ctypes.CDLL(None, use_errno=True).prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
            logger.warning("Could not mark the backend process non-dumpable (errno %d).", ctypes.get_errno())
    except (OSError, AttributeError):
        logger.warning("Could not mark the backend process non-dumpable.", exc_info=True)


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
        self.max_body = int(getattr(config, "max_request_body_mb", 1024)) * 1024 * 1024

    def _origin_allowed(self, origin: str, host: str) -> bool:
        origin = origin.rstrip("/").lower()
        return self.any_origin or origin in self.origins or urlsplit(origin).netloc == host.lower()

    def _needs_token(self, path: str) -> bool:
        return bool(self.config.api_auth_token) and path.startswith(TOKEN_PREFIXES) and path not in PUBLIC_PATHS

    def _from_agent_host(self, scope: dict, conn: HTTPConnection) -> bool:
        """A tokenless API call from inside the container that also runs agent code.

        ``SANDBOX_MODE=external`` has no OS sandbox to close loopback, but browsers
        and the frontend proxy arrive from other addresses. ``X-Real-IP`` is the
        peer nginx saw, which catches a child going through the proxy.
        """
        config = self.config
        if getattr(config, "sandbox_mode", None) != "external" or config.api_auth_token:
            return False
        path, mount = scope["path"], getattr(config, "mcp_mount_path", "/mcp")
        is_mcp = path == mount or path.startswith(mount.rstrip("/") + "/")
        if not path.startswith(TOKEN_PREFIXES) and not (is_mcp and not getattr(config, "mcp_auth_token", None)):
            return False
        own = (scope.get("server") or ("",))[0]
        peers = ((scope.get("client") or ("",))[0], conn.headers.get("x-real-ip", ""))
        return any(peer and (is_loopback(peer) or peer == own) for peer in peers)

    def _too_large(self, conn: HTTPConnection) -> bool:
        length = conn.headers.get("content-length", "")
        return length.isdigit() and int(length) > self.max_body

    def _capped(self, receive: Any) -> Any:
        """Stops a request whose declared length was absent or false."""
        received = 0

        async def capped_receive() -> dict:
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > self.max_body:
                raise HTTPException(status_code=413, detail="Request body is too large.")
            return message

        return capped_receive

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
        elif method != "OPTIONS" and self._from_agent_host(scope, conn):
            await self._reject(scope, receive, send, 403, "Calls from this host are refused without API_AUTH_TOKEN.")
        elif method not in SAFE_METHODS and scope["path"].startswith(OPERATOR_WRITE_PREFIXES) \
                and not getattr(self.config, "settings_api_writable", True):
            await self._reject(scope, receive, send, 403, "Configuration changes through the API are disabled.")
        elif scope["type"] == "http" and self._too_large(conn):
            await self._reject(scope, receive, send, 413, "Request body is too large.")
        else:
            limited = scope["type"] == "http" and scope["path"].startswith("/api/")
            await self.app(scope, self._capped(receive) if limited else receive, send)

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


# At import, before the server has started any child.
harden_process()
