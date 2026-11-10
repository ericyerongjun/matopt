"""Connect MatOpt's own MCP server to well-known MCP clients.

    python -m app.agent.mcp.export                 # list clients
    python -m app.agent.mcp.export cursor          # print the config snippet
    python -m app.agent.mcp.export cursor --write  # merge it into the client's config (a .bak is kept)
    python -m app.agent.mcp.export vscode --http http://localhost:8000/mcp

stdio (default) launches ``python -m app.agent.mcp`` from this checkout, so the
client gets the persistent kernel and workspace tools without the web server
running. ``--http`` points at a running backend instead; when the operator has
set ``MCP_AUTH_TOKEN`` the snippet references ``${MATOPT_MCP_TOKEN}`` (or the
client's own variable syntax) and never contains the token itself.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

SERVER_NAME = "matopt"
TOKEN_ENV = "MATOPT_MCP_TOKEN"
BACKEND_DIR = Path(__file__).resolve().parents[3]


def _python() -> str:
    venv = BACKEND_DIR / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(venv if venv.exists() else Path(sys.executable))


def stdio_spec() -> dict:
    return {"command": _python(), "args": ["-m", "app.agent.mcp"], "env": {"PYTHONPATH": str(BACKEND_DIR)},
            "cwd": str(BACKEND_DIR)}


def _app_support(*parts: str) -> Path:
    home = Path.home()
    system = platform.system()
    if system == "Darwin":
        return home / "Library" / "Application Support" / Path(*parts)
    if system == "Windows":
        return Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / Path(*parts)
    return home / ".config" / Path(*parts)


@dataclass(frozen=True)
class Client:
    id: str
    name: str
    path: Optional[Callable[[], Path]]   # user-level config file, or None when it is printed only
    fmt: str                             # json-mcpServers | vscode | zed | codex-toml | claude-code
    note: str = ""


CLIENTS: tuple[Client, ...] = (
    Client("claude-desktop", "Claude Desktop", lambda: _app_support("Claude", "claude_desktop_config.json"),
           "json-mcpServers", "Restart Claude Desktop after writing."),
    Client("claude-code", "Claude Code", None, "claude-code", "Run the printed command, or add the JSON to .mcp.json."),
    Client("cursor", "Cursor", lambda: Path.home() / ".cursor" / "mcp.json", "json-mcpServers"),
    Client("vscode", "VS Code (Copilot agent mode)", None, "vscode",
           "Save as .vscode/mcp.json in a workspace, or run 'MCP: Add Server' and paste it."),
    Client("windsurf", "Windsurf", lambda: Path.home() / ".codeium" / "windsurf" / "mcp_config.json", "json-mcpServers"),
    Client("codex", "OpenAI Codex CLI", lambda: Path.home() / ".codex" / "config.toml", "codex-toml"),
    Client("gemini", "Gemini CLI", lambda: Path.home() / ".gemini" / "settings.json", "json-mcpServers"),
    Client("zed", "Zed", None, "zed", "Add under \"context_servers\" in Zed's settings.json."),
)
BY_ID = {c.id: c for c in CLIENTS}


def _token_needed() -> bool:
    try:
        from app.config import settings
        return bool(settings.mcp_auth_token)
    except Exception:  # noqa: BLE001 - export must work without a configured backend
        return bool(os.environ.get("MCP_AUTH_TOKEN"))


def server_entry(client: Client, http_url: Optional[str] = None) -> dict:
    """The server entry in the client's own dialect."""
    if http_url:
        headers = {"Authorization": f"Bearer ${{{TOKEN_ENV}}}"} if _token_needed() else {}
        if client.fmt == "vscode":
            headers = {"Authorization": f"Bearer ${{env:{TOKEN_ENV}}}"} if headers else {}
            return {"type": "http", "url": http_url, **({"headers": headers} if headers else {})}
        if client.id == "windsurf":
            return {"serverUrl": http_url, **({"headers": headers} if headers else {})}
        if client.id == "gemini":
            return {"httpUrl": http_url, **({"headers": headers} if headers else {})}
        if client.fmt == "codex-toml":
            return {"url": http_url, **({"bearer_token_env_var": TOKEN_ENV} if headers else {})}
        return {"type": "http", "url": http_url, **({"headers": headers} if headers else {})}
    spec = stdio_spec()
    if client.fmt == "vscode":
        return {"type": "stdio", "command": spec["command"], "args": spec["args"], "env": spec["env"]}
    if client.id in ("gemini", "codex") or client.fmt == "codex-toml":
        return spec
    return {"command": spec["command"], "args": spec["args"], "env": spec["env"]}


def _toml_value(v) -> str:
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{json.dumps(k)} = {_toml_value(x)}" for k, x in v.items()) + " }"
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return json.dumps(v)


def snippet(client: Client, http_url: Optional[str] = None) -> str:
    entry = server_entry(client, http_url)
    if client.fmt == "codex-toml":
        return f"[mcp_servers.{SERVER_NAME}]\n" + "".join(f"{k} = {_toml_value(v)}\n" for k, v in entry.items())
    if client.fmt == "vscode":
        return json.dumps({"servers": {SERVER_NAME: entry}}, indent=2)
    if client.fmt == "zed":
        return json.dumps({"context_servers": {SERVER_NAME: entry}}, indent=2)
    if client.fmt == "claude-code":
        if http_url:
            header = f" --header \"Authorization: Bearer ${TOKEN_ENV}\"" if "headers" in entry else ""
            return f"claude mcp add --transport http --scope user {SERVER_NAME} {http_url}{header}"
        env = " ".join(f"-e {k}={v}" for k, v in entry["env"].items())
        return f"claude mcp add --scope user {env} {SERVER_NAME} -- {entry['command']} {' '.join(entry['args'])}"
    return json.dumps({"mcpServers": {SERVER_NAME: entry}}, indent=2)


def write(client: Client, http_url: Optional[str] = None, path: Optional[Path] = None) -> Path:
    """Merge the entry into the client's config file, keeping a ``.bak`` of the original."""
    if path is None:
        if client.path is None:
            raise ValueError(f"{client.name} has no single config file; pass --path or paste the snippet")
        path = client.path()
    path = Path(path).expanduser()
    entry = server_entry(client, http_url)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    if client.fmt == "codex-toml":
        import tomllib
        data = tomllib.loads(original) if original.strip() else {}
        if SERVER_NAME in data.get("mcp_servers", {}):
            raise ValueError(f"{path} already has [mcp_servers.{SERVER_NAME}]; edit it by hand")
        text = original.rstrip() + ("\n\n" if original.strip() else "") + snippet(client, http_url)
    else:
        data = json.loads(original) if original.strip() else {}
        if not isinstance(data, dict):
            raise ValueError(f"{path} does not contain a JSON object")
        key = {"vscode": "servers", "zed": "context_servers"}.get(client.fmt, "mcpServers")
        data.setdefault(key, {})[SERVER_NAME] = entry
        text = json.dumps(data, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if original:
        shutil.copyfile(path, path.with_name(path.name + ".bak"))
    path.write_text(text, encoding="utf-8")
    return path


def describe() -> list[dict]:
    return [{"id": c.id, "name": c.name, "path": str(c.path()) if c.path else None, "note": c.note} for c in CLIENTS]


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Connect MatOpt's MCP server to an MCP client.")
    p.add_argument("client", nargs="?", choices=sorted(BY_ID))
    p.add_argument("--http", metavar="URL", help="use a running backend's HTTP endpoint (e.g. http://localhost:8000/mcp)")
    p.add_argument("--write", action="store_true", help="merge into the client's config file (keeps a .bak)")
    p.add_argument("--path", type=Path, help="config file to write instead of the client's default")
    args = p.parse_args(argv)
    if not args.client:
        for c in describe():
            print(f"{c['id']:15} {c['name']:30} {c['path'] or '(prints a snippet)'}")
        return 0
    client = BY_ID[args.client]
    if args.write:
        try:
            path = write(client, args.http, args.path)
        except (ValueError, OSError) as exc:
            print(f"Not written: {exc}", file=sys.stderr)
            return 1
        print(f"Added '{SERVER_NAME}' to {path}. {client.note}".strip())
        return 0
    print(snippet(client, args.http))
    if client.note:
        print(f"\n# {client.note}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
