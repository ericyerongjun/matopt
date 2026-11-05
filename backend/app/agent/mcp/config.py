"""Load extra MCP servers from mcp.json (Claude-style mcpServers map).

String values may reference environment variables as ``${NAME}`` or
``${NAME:-default}`` (the Claude Code / VS Code convention), so tokens stay
out of ``mcp.json``. Values come from ``<data_dir>/mcp.env`` (written by the
connector API, mode 0600) first, then the process environment. A server whose
variables are unset is reported as failed instead of connecting with an empty
credential.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


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


def read_raw_config(path: Path) -> dict[str, Any]:
    """The whole file (other top-level keys preserved), or an empty skeleton."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"mcpServers": {}}
    if not isinstance(raw, dict):
        raise ValueError("mcp.json must contain a JSON object")
    if not isinstance(raw.get("mcpServers", {}), dict):
        raise ValueError("mcp.json mcpServers must be an object")
    raw.setdefault("mcpServers", {})
    return raw


def _atomic_write(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_raw_config(path: Path, raw: dict[str, Any]) -> None:
    _atomic_write(Path(path), json.dumps(raw, indent=2, ensure_ascii=False) + "\n")


# ── secrets file (KEY=value lines) ───────────────────────────────────────

def read_env_file(path: Path | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if path is None:
        return out
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if ENV_NAME_RE.match(key):
            out[key] = json.loads(value) if value.startswith('"') else value
    return out


def set_env_value(path: Path, key: str, value: str | None) -> None:
    """Set (or with ``None`` remove) one variable; the file is written 0600."""
    if not ENV_NAME_RE.match(key):
        raise ValueError(f"Invalid environment variable name {key!r}")
    if value is not None and ("\n" in value or "\r" in value):
        raise ValueError("Secret values must be a single line")
    values = read_env_file(path)
    if value is None:
        values.pop(key, None)
    else:
        values[key] = value
    text = "# MCP connector secrets written by MatOpt. Do not commit.\n" + "".join(
        f"{k}={json.dumps(v)}\n" for k, v in sorted(values.items()))
    _atomic_write(Path(path), text, mode=0o600)


# ── ${VAR} expansion ────────────────────────────────────────────────────

def referenced_vars(value: Any) -> set[str]:
    if isinstance(value, str):
        return {m.group(1) for m in VAR_RE.finditer(value)}
    if isinstance(value, dict):
        return set().union(*(referenced_vars(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(referenced_vars(v) for v in value)) if value else set()
    return set()


def expand_vars(value: Any, env: Mapping[str, str]) -> tuple[Any, list[str]]:
    """Return (expanded copy, names that were unset and had no default)."""
    missing: list[str] = []

    def sub(text: str) -> str:
        def one(m: re.Match) -> str:
            name, default = m.group(1), m.group(2)
            if env.get(name):
                return env[name]
            if default is not None:
                return default
            if name not in missing:
                missing.append(name)
            return ""
        return VAR_RE.sub(one, text)

    def walk(v: Any) -> Any:
        if isinstance(v, str):
            return sub(v)
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v

    return walk(value), missing


def secret_env(path: Path | None) -> dict[str, str]:
    """Variables visible to ``${VAR}`` expansion: the secrets file over the process env."""
    return {**os.environ, **read_env_file(path)}
