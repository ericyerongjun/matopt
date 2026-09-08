"""Load extra MCP servers from mcp.json (Claude-style mcpServers map)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_mcp_config(path: Path | None) -> dict[str, Any]:
    if path is None or not Path(path).is_file():
        return {"mcpServers": {}}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {"mcpServers": {}}
    servers = raw.get("mcpServers") or raw.get("servers") or {}
    if not isinstance(servers, dict):
        servers = {}
    return {"mcpServers": servers}
