# MatOpt

An agent development environment for mathematics, statistics, ML and CS.
The frontend centers the conversation, with a collapsible chat sidebar and an
optional canvas for interactive results. It uses a MatOpt theme with system
typography and licensed ZCode primitives. A persistent Python
kernel, bash, document OCR, verification, Agent Skills and MCP tools execute
through the streaming harness.

## Quick start

Use Node.js 22+ for the frontend and Python 3.11+ for the backend.

```bash
cp backend/.env.example backend/.env   # then add DEEPSEEK_API_KEY
./scripts/setup.sh
# terminal 1
cd backend && .venv/bin/python main.py
# terminal 2
cd frontend && npm run dev
```

Open http://localhost:5173. Docker: `docker compose up --build` (http://localhost:3000).
Development also starts an isolated mini-app runtime on port 5174; Compose
serves it on port 3001. Both the frontend and backend must be restarted after
updating. [Interactive UI setup and architecture](docs/INTELLIGENT-UI.md).

The only live model is **DeepSeek V4.1 Flash**, using API ID `deepseek-flash`
for the harness, verification, auxiliary requests and LAMBDA. Thinking effort
is Off, Low, High or Max. [Official model documentation](https://api-docs.deepseek.com/quick_start/pricing/).
ZCode source mappings and UI checks: [ADE migration](docs/ZCODE-ADE-MIGRATION.md).
See [docs/PROTOCOL.md](docs/PROTOCOL.md) and [backend/README.md](backend/README.md).

Attach a PDF or screenshot and ask your question. The configured DeepSeek vision
model reads it before the reasoning turn, preserving equations and tables in a
page-labelled Markdown file. Progress, page coverage and any unreadable content
are reported in the conversation. Originals stay in the workspace; native PDF
text extraction remains available as a fallback. See
[document reading and limits](backend/README.md#pdfs-screenshots-and-scanned-documents) for the
configuration and supported formats.

## Skills and MCP

- Catalog: [`skills/`](skills/README.md) — MatOpt playbooks, split [LAMBDA](https://github.com/AMA-CMFAI/LAMBDA) data skills (`/lambda-eda`, `/lambda-report`, `/lambda-slides`, `/lambda-notebook`, `/lambda-data-agent`), plus the full [anthropics/skills](https://github.com/anthropics/skills) tree.
- Data agent: the original [LAMBDA](https://github.com/AMA-CMFAI/LAMBDA) backend is vendored as `backend/lambda_app/` and mounted at `/api/v1`. The harness tool is `lambda_data_agent`.
- Type `/` in the composer for a Cursor/Claude-style list of skills and tools. `/frontend-design` (or `/python`) loads that playbook or prefers that tool — including mid-turn steering, not only the first prompt.
- Select `/web-search` or `/web-fetch` to enable web tools for the current turn (open-source `ddgs`, or SearXNG / Tavily / Brave). Skills and tools share the slash menu; selecting one never sends the message.
- Interactive results appear in the conversation. **Open in canvas** expands a selected result and shares its parameters with the inline view. **Files** opens document previews; **Activity** contains safe work labels, the bounded verification outcome and usage. Internal reasoning, agent exchanges and raw tool traces stay private. While the agent is running, type to steer the current turn.
- Requested verification runs before final publication, with at most two checks and one complete correction. Final text streams from the checked result, then the chat refreshes its saved state in place without losing the next draft.
- Native controls, charts and tables use validated schemas. More complex mini-apps run on the isolated origin. Python recomputations happen only through declared host actions, with saved parameters, explicit cancellation and replay protection.
- The harness **routes** the user turn against skill descriptions and file types, auto-loads the best match, and resolves tool-name aliases/typos.
- Inspect the catalog: `GET /api/skills`, `GET /api/tools`.
- The conversation kernel is also an MCP server at `http://localhost:8000/mcp` (stdio: `cd backend && .venv/bin/python -m app.agent.mcp`). Extra MCP servers: copy `mcp.json.example` to `mcp.json`.
