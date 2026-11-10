---
name: matopt-sdk
description: >
  Build programs on top of MatOpt: drive the agent from code through the
  HTTP API and its server-sent event stream, or use MatOpt's kernel and
  workspace tools from another app through its MCP server. Use for /matopt-sdk
  or "call MatOpt from my script/app".
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Build with MatOpt (API + MCP)

Two integration surfaces; the full contract is `docs/PROTOCOL.md`.

## 1. Agent API (HTTP + SSE)

`POST /api/chat/stream` with `{"message": "...", "conversation_id"?: "...", "provider"?, "model"?, "mode"?: "auto"|"mat"|"opt",
"skills"?: [...], "tools"?: [...]}` returns `text/event-stream`. Events of interest: `conversation`
(`conversation_id`), `text_delta` (`delta`), `artifact`, `message_end` (`content`, canonical — replace streamed text),
`done`, `stopped`, `error`. Reattach with `GET /api/runs/{run_id}/events?after=<seq>`; stop with
`POST /api/chat/{conversation_id}/stop`.

```python
import json, httpx

def ask(message: str, base="http://localhost:8000", conversation_id=None) -> tuple[str, str]:
    body = {"message": message, **({"conversation_id": conversation_id} if conversation_id else {})}
    text, cid = "", conversation_id
    with httpx.stream("POST", f"{base}/api/chat/stream", json=body, timeout=None) as r:
        r.raise_for_status()
        event = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:])
                kind = data.get("type", event)
                if kind == "conversation":
                    cid = data["conversation_id"]
                elif kind == "text_delta":
                    text += data["delta"]
                elif kind == "message_end" and isinstance(data.get("content"), str):
                    text = data["content"]
                elif kind == "error":
                    raise RuntimeError(data.get("message"))
    return cid, text
```

Check the exact SSE framing against a live response once (`curl -N -X POST ... -H 'content-type: application/json'`)
and adapt the parser; other endpoints: `/api/conversations`, `/api/files`, `/api/skills`, `/api/tools`,
`/api/automations`, `/api/mcp/connectors`.

## 2. MatOpt as an MCP server

The persistent kernel and workspace are exported as MCP tools (`open_workspace`, `python`, `bash`, `read_file`,
`write_file`, `edit_file`, `list_files`, `grep`, `parse_document`, `check_equivalence`, …):

- stdio: `cd backend && .venv/bin/python -m app.agent.mcp`
- HTTP: `http://localhost:8000/mcp` (set `MCP_AUTH_TOKEN` before exposing it beyond localhost)
- Ready-made client configs: `cd backend && .venv/bin/python -m app.agent.mcp.export <client>` for `claude-desktop`,
  `claude-code`, `cursor`, `vscode`, `windsurf`, `codex`, `zed`, `gemini`.

## Steps
1. Ask which surface fits (driving the agent → API; giving another agent MatOpt's kernel → MCP).
2. Write a minimal working client in the user's language, run it against the local server, then add error handling
   and reconnection only as needed. Never embed tokens in code; read them from the environment.
