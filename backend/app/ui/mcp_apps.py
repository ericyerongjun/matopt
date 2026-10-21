"""Read-only MCP Apps projection of saved mini-app surfaces.

A standard MCP Apps host (ext-apps 2026-01-26) renders the static viewer
resource and passes it the tool result; the saved HTML and state travel in the
result ``_meta`` so they reach the view without entering model context. The
in-app runtime in ``frontend/sandbox`` is unaffected.
"""
from __future__ import annotations

from pathlib import Path

VIEWER_URI = "ui://matopt/mini-app-viewer.html"
SURFACE_META = "matopt/surface"
_VIEWER = Path(__file__).with_name("mcp_app_viewer.html")


def viewer_html() -> str:
    return _VIEWER.read_text(encoding="utf-8")


def project_surface(surface: dict) -> tuple[str, dict, dict | None]:
    """Return (model text, structured summary, view-only meta) for one surface.

    Native surfaces have no HTML to host; they degrade to their readable
    fallback, which the viewer also shows when meta is absent.
    """
    summary = {"surface_id": surface["id"], "revision": surface["revision"], "kind": surface["kind"],
               "title": surface["title"], "state": surface["state"]}
    if surface["kind"] != "mini_app" or not surface.get("app"):
        return surface["fallback"], summary, None
    meta = {SURFACE_META: {"id": surface["id"], "revision": surface["revision"],
                           "html": surface["app"]["html"], "state": surface["state"]}}
    return surface["fallback"], summary, meta
