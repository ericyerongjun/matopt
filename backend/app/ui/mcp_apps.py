"""MCP Apps projection of saved mini-app surfaces.

A standard MCP Apps host (ext-apps 2026-01-26) renders the static viewer
resource and passes it the tool result. The saved HTML, state and a
surface-scoped token travel in the result ``_meta`` so they reach the view
without entering model context. The in-app runtime in ``frontend/sandbox`` is
unaffected.

The token authorizes the app-only state and action tools for exactly one
surface. The viewer keeps it in a closure, so model-authored mini-app scripts
running in the same frame cannot read it or address other surfaces.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

VIEWER_URI = "ui://matopt/mini-app-viewer.html"
SURFACE_META = "matopt/surface"
_VIEWER = Path(__file__).with_name("mcp_app_viewer.html")


def viewer_html() -> str:
    return _VIEWER.read_text(encoding="utf-8")


def _key(data_dir: Path) -> bytes:
    """A persistent server secret, so views survive a server restart."""
    path = Path(data_dir) / "mcp_apps.key"
    try:
        return bytes.fromhex(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return bytes.fromhex(path.read_text().strip())
        with os.fdopen(fd, "w") as handle:
            handle.write(secrets.token_hex(32))
        return bytes.fromhex(path.read_text().strip())


def surface_token(data_dir: Path, cid: str, sid: str) -> str:
    return hmac.new(_key(data_dir), f"matopt-ui\0{cid}\0{sid}".encode(), hashlib.sha256).hexdigest()


def verify_token(data_dir: Path, cid: str, sid: str, token: object) -> bool:
    return isinstance(token, str) and hmac.compare_digest(surface_token(data_dir, cid, sid), token)


def project_surface(surface: dict, cid: str, data_dir: Path, *, initial: bool = True) -> tuple[str, dict, dict | None]:
    """Return (model text, structured summary, view-only meta) for one surface.

    Native surfaces have no HTML to host; they degrade to their readable
    fallback, which the viewer also shows when meta is absent. Only the result
    that mounts a view carries the HTML and token: mini-app scripts that run
    after mounting may listen to later host messages.
    """
    summary = {"surface_id": surface["id"], "revision": surface["revision"], "kind": surface["kind"],
               "title": surface["title"], "state": surface["state"]}
    if surface["kind"] != "mini_app" or not surface.get("app"):
        return surface["fallback"], summary, None
    meta = {SURFACE_META: {
        "conversation_id": cid, "id": surface["id"], "revision": surface["revision"], "title": surface["title"],
        "fallback": surface["fallback"], "state": surface["state"],
        "computed_state": surface["state"] if surface.get("computed_state") is None else surface["computed_state"],
        "actions": [{k: a[k] for k in ("id", "label", "description", "input_schema") if k in a} for a in surface["actions"]],
    }}
    if initial:
        meta[SURFACE_META].update(html=surface["app"]["html"], token=surface_token(data_dir, cid, surface["id"]))
    return surface["fallback"], summary, meta
