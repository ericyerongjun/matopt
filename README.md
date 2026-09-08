# MatOpt

Agentic assistant for mathematics, statistics, ML and CS. The backend is a
Claude-Code-style harness (Python kernel, bash, files, document OCR, optional
verify/revise) with an **Agent Skills** space and a **MCP** sandbox. The
frontend streams tool calls over SSE.

## Quick start

```bash
cp backend/.env.example backend/.env   # then add an API key
./scripts/setup.sh
# terminal 1
cd backend && .venv/bin/python main.py
# terminal 2
cd frontend && npm run dev
```

Open http://localhost:5173. Docker: `docker compose up --build` (http://localhost:3000).

Supported models: DeepSeek V4 Pro/Flash, GPT-5.6 Terra/Luna, Qwen 3.8 / 3.7 / 3.5.
See [docs/PROTOCOL.md](docs/PROTOCOL.md) and [backend/README.md](backend/README.md).

## Skills and MCP

- Catalog: [`skills/`](skills/README.md) — MatOpt playbooks plus the full [anthropics/skills](https://github.com/anthropics/skills) tree.
- Data agent: the original [LAMBDA](https://github.com/AMA-CMFAI/LAMBDA) backend is vendored as `backend/lambda_app/` and mounted at `/api/v1`. The harness tool is `lambda_data_agent`.
- Type `/` in the composer to pick a skill. Toggle **Web Search** for live results (open-source `ddgs`, or SearXNG / Tavily / Brave).
- Three panes: chats, the thread, and a compact **Work** rail (actions, plan, figures). While the agent is running, type to steer the current turn.
- The harness **routes** the user turn against skill descriptions and file types, auto-loads the best match, and resolves tool-name aliases/typos.
- Inspect loaded skills: `GET /api/skills`.
- The conversation kernel is also an MCP server at `http://localhost:8000/mcp` (stdio: `cd backend && .venv/bin/python -m app.agent.mcp`). Extra MCP servers: copy `mcp.json.example` to `mcp.json`.
