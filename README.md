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

Shared [academic response guidance](backend/app/academic_guidance.md) governs
mathematical notation, derivations, research rigor, and adaptive explanation depth.
It applies to the main assistant, final answer writer, verifier, and LAMBDA data
agent. Role-specific evidence limits and output formats still apply. Edit this
document and restart the backend to update the guidance; no new API setting is needed.

Attach a PDF or screenshot and ask your question. The configured DeepSeek vision
model reads it before the reasoning turn, preserving equations and tables in a
page-labelled Markdown file. Progress, page coverage and any unreadable content
are reported in the conversation. Originals stay in the workspace; native PDF
text extraction remains available as a fallback. See
[document reading and limits](backend/README.md#pdfs-screenshots-and-scanned-documents) for the
configuration and supported formats.

## Voice tutor

- **Talk:** hold **Right ⌥ Option** (browsers cannot see the fn/Globe key) or click the composer's mic. Speech is transcribed on the backend by Whisper (`small`, CPU) and placed in the composer; quick taps and Option+letter typing never record.
- **Listen:** turn on *Speak* in the **Tutor** panel, or use **Read aloud** on any answer. Replies are voiced locally by Kokoro (English, 中文, 日本語, Español, Français, Italiano, Português, हिन्दी) and start in about a second; code, tables and display equations are summarised rather than read out.
- **Hands-free:** *Talk* mode sends what you say and speaks the reply; talking interrupts it.
- **Avatar:** pick a built-in tutor in the panel: fan-made anime tributes to Andrej Karpathy (default) and Jensen Huang, built from VRoid's CC0 base model by `scripts/build_tutor_avatar.py` and not affiliated with either. They lip-sync, blink, think while the agent works and smile when it is done. The panel's picture button loads your own `.vrm` instead (for example from VRoid Studio); it is kept only in your browser.
- **Language:** the composer's language picker sets the reply language, the voice and the transcription hint; *Auto* follows you.
- Speech models (~800 MB) download once into the data volume at first start. `SPEECH_ENABLED=false` turns voice off; see the GPL note for espeak-ng in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Quality, cost and observability

- **Evals:** [`evals/`](evals/README.md) holds graded problems with computed reference answers; `scripts/run_evals.py` runs them through the live harness and reports pass rate, time, tokens and prompt-cache reuse against a saved baseline.
- **Prompt caching:** the system prompt is stable across turns and per-turn facts travel in a `<turn-context>` message, so the provider cache covers earlier history ([measurement](docs/PROMPT-CACHE-EXPERIMENT.md)). Long chats are compacted into a rolling summary instead of dropping turns.
- **Formal proofs:** `scripts/setup_lean.sh` installs Lean 4 (optionally with Mathlib); the `lean_check` tool then machine-checks proofs and rejects `sorry`, `admit` and new axioms.
- **Tracing:** `pip install -r backend/requirements-otel.txt` and set `OTEL_EXPORTER_OTLP_ENDPOINT` to export each turn as OpenTelemetry GenAI spans (Langfuse, Phoenix, Jaeger).
- **Pinned installs:** `backend/constraints.txt` records versions verified together; setup and Docker install with it. CI runs backend and frontend tests, the build, the vendor provenance check and `docker compose build`.

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
