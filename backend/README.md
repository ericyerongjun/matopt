# MatOpt backend

The agentic FastAPI service: a tool-calling harness with a persistent Python kernel,
workspace files, Agent Skills routing, an MCP sandbox, DeepSeek multimodal document reading,
the data-analysis backend at `/api/v1`, and streaming SSE events.

## Environment

```bash
cp backend/.env.example backend/.env
```

Set `DEEPSEEK_API_KEY`. The only live route is DeepSeek V4.1 Flash
(`deepseek-flash`), including verification, title generation, follow-ups and
the data agent. The docked composer exposes thinking effort: `off`, `low`, `high`,
`max`. Historical model labels remain in the ledger; known obsolete saved
selections normalize to Flash for new requests.
[Official API documentation](https://api-docs.deepseek.com/quick_start/pricing/).

Web search is available automatically for questions that need current information;
the agent searches and cites sources without requiring a button or slash command.
Stable explanations and self-contained tasks do not need browsing, and user requests
not to browse are respected. `/web-search` and `/web-fetch` remain available for explicit use.
API clients can disable web tools for a turn with `web_search: false`; `WEB_SEARCH_ENABLED=false`
disables registration globally. Search uses open-source `ddgs` by default. Optional:
`SEARXNG_URL`, `TAVILY_API_KEY`, or `BRAVE_SEARCH_API_KEY`. Mid-turn steering
is `POST /api/chat/{id}/steer` (`message`, optional `skills` / `tools`). Local alignment checks are on via `ALIGN_MONITOR`. The Docker image includes the repo `skills/` tree at `/app/skills`.

## Local run (no uv)

```bash
# from repo root
./scripts/setup.sh
cd backend && .venv/bin/python main.py
```

Python comes from Homebrew (`python@3.12`). Dependencies are plain pip.

- API: http://localhost:8000
- Health: http://localhost:8000/health
- Providers: http://localhost:8000/api/providers
- Protocol: [docs/PROTOCOL.md](../docs/PROTOCOL.md)
- Data agent: http://localhost:8000/api/v1/health

## MCP workspace identity

The sandbox MCP endpoint is `/mcp/`. Call `open_workspace` and retain its
`conversation_id`; pass that ID on **every** subsequent tool call. HTTP session
state no longer selects an implicit workspace. Existing clients must refresh the
tool catalog and supply the new required argument.

For example, call `read_file` with `{"conversation_id":"<returned-id>","path":"report.md"}`
before calling `edit_file` for that same workspace and current file version.
Read observations and uncertain execution receipts survive independent requests
and process recovery through the existing session log. An interrupted mutation
requires inspecting its outcome before retrying. Concurrent native chat or MCP
work in the same workspace returns `WORKSPACE_BUSY`.

Native chat and MCP use the same validation, execution policy, timeout and
cancellation service. MCP results retain structured content and supported media;
ordinary chat uses a separate public activity projection that excludes raw tool
arguments, stdout, reasoning and verifier reports. Workspace IDs are routing
identities, not authentication credentials; deployment access policy is unchanged.

For remote catalogs, execution accepts exact tool IDs and explicit native aliases.
`search_tools` discovers metadata; `load_tool_schemas` adds selected tools for the
next model request. See the [tool discovery experiment](../docs/TOOL-DISCOVERY-EXPERIMENT.md)
for measured recall, schema-size tradeoffs and remaining limitations.

## PDFs, screenshots and scanned documents

Newly attached PDFs and raster images are read automatically before the first
assistant answer, using the same `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL` and
`deepseek-flash` route as the chat. Restart the backend after changing `.env`.
No OCR vendor or additional key is required. DeepSeek's documented
[vision input](https://api-docs.deepseek.com/guides/vision/) is a user message
with standard `image_url` content blocks containing inline base64 images.

The backend validates file bytes, renders PDFs locally with PyMuPDF, normalizes
image orientation and dimensions, then sends one page per request. The prompt
transcribes text, LaTeX mathematics and tables faithfully; chart and diagram
descriptions are separated from observed text. Unreadable text is marked
`[unclear]` or `[illegible]`. Originals remain in `uploads/`; downloadable
Markdown readings with page labels, scope and any failure notes appear under
`parsed/`, accessible through Files. The answer receives the reading
automatically. Long readings can be inspected with `read_file`.

Defaults limit each reading to 12 pages, six automatic documents per turn,
25 MB per file, 32 million decoded image pixels, 2048 pixels per image side,
90 seconds per page and 300 seconds per document. Omitted pages/files are
reported explicitly; use `parse_document` with `pages="13-24"` for another
batch. Each page is compressed below 8 MiB, beneath the provider request limit.
See `DOCUMENT_OCR_*` in `.env.example` for the configurable bounds.

Successful complete readings are cached by source bytes, page range and parser
configuration. Stopping a turn cancels outstanding provider requests. Reading failures are reported in the saved reading; embedded PDF text can be used as a labelled fallback,
without claiming that scanned text or diagrams were read. Empty/unreadable
images return errors rather than invented readings. OCR is still model output,
so uncertain mathematical symbols should be checked against the original.

`parse_document` remains available explicitly with `auto` (default),
`multimodal` or `fast` (local extraction). Set
`DOCUMENT_AUTO_PARSE=false` to make reading manual; `DOCUMENT_OCR_ENABLED=false`
disables multimodal OCR. Existing CSV/Excel handling is unchanged. OCR calls
appear as accounted side tasks in the session ledger; page-image payloads are
not duplicated into chat history or the ledger (source/image hashes are stored).

## Session history and usage

`SESSION_LOG_ENABLED=true` (default) stores a versioned event ledger in SQLite
alongside the chat messages. Opening a conversation repairs interrupted turns
before returning its public chat view. Exact internal execution records remain
in the ledger for provider replay; public history and exports contain only the
selected final answer, committed outputs and safe activity labels. Usage in the
UI uses this ledger; imported history is marked as missing token evidence. Set the flag to `false` to use the legacy replay path.

Inspection routes are documented in [docs/PROTOCOL.md](../docs/PROTOCOL.md).
Raw events, requests and trajectory endpoints return 404 by default. Operators
can explicitly set `DIAGNOSTICS_ENABLED=true` for local debugging; normal chat
and Activity never fetch these endpoints. Numeric usage remains available.

Verification skips trivial greetings and is bounded to two checks, one complete
correction and a small tool-step budget per checking episode. An applied user
instruction can start another episode; all episodes consume the same run
allowance. Private receipts record check identity, the candidate/source hash,
result generation and checking rounds entered. That counter includes a round
blocked before model admission; the separate budget ledger counts admissions.
Checking stops on correct or inconclusive
results and never makes an unchecked correction after the last round.

Normal mode can publish useful partial results and stream a final writing pass.
Its checks concern the preceding candidate; they do not prove that every sentence
in the subsequent writing pass is correct. Strict mode consolidates partial
findings before checking and returns the checked wording without another rewrite.
Failed or inconclusive checks produce an honest notice. Raw reasoning, agent
messages and verifier reports remain outside the public chat.

## Run limits and loop recovery

Every native worker, checker, OCR request and delegated data-agent call shares one
run allowance, even with session logging disabled:

| Setting | Default | Scope |
| --- | ---: | --- |
| `RUN_MAX_MODEL_ATTEMPTS` | 80 | Worker, final writer, checks, OCR, child model requests and each actual retry |
| `RUN_MAX_TOOL_STARTS` | 160 | Native execution admissions and delegated child handler admissions |
| `RUN_TIMEOUT_SECONDS` | 1800 | Absolute monotonic deadline, including paused time |

These are engineering defaults, not experimentally optimal settings. Local
per-tool limits can be stricter. An admitted attempt remains charged after error
or cancellation; validation and policy rejection do not consume an admission.
The delegated wrapper and each of its admitted child handlers count separately.
Title/follow-up suggestions are auxiliary UI tasks outside this execution budget.

Stop and deadline expiry interrupt quiet provider streams as well as tool work.
Child cleanup remains owned. Local extraction uses threads that cannot be
force-killed: if cleanup exceeds its grace period, further tool execution in
that workspace is blocked until the thread actually exits. Exhausting an
allowance saves available partial results and ends with an explicit incomplete
outcome; it does not dispatch an extra final-writing request. Missing or estimated
provider usage stays unknown rather than becoming zero. A stopped final stream
retains its already-public prefix, and a truncated delegated response is an
explicit incomplete result. Admission limits are
independent of exact token accounting and the observed-spend limits below.

`LOOP_POLICY=operation` (default) limits repeat failures for the canonical tool,
normalized arguments, applied user instruction generation and trusted file
revision. Corrected arguments remain eligible after another operation fails.
`LOOP_POLICY=legacy` retains the former whole-tool block for comparison or
rollback. The existing uncertain-side-effect recovery guard takes precedence.
Repeated successful reads are advisory only; identical stdout, an unchanged
objective or a quiet polling interval never proves completion. See the
[loop-policy comparison](../docs/LOOP-POLICY-EXPERIMENT.md).

`MODEL_PRICING` accepts a JSON object keyed by `provider/model`. Each entry
uses operator-supplied rates per million tokens: `input`, `output`, optional
`cache_read`/`cache_write`, `currency` and `as_of`. No prices are built in.
`MAX_TURN_COST` and `MAX_CONVERSATION_COST` optionally stop the next request
when observed spend reaches the limit. A single request can exceed a limit;
missing usage, pricing or imported history prevents exact enforcement.

The file tools require a current read before editing or overwriting an
existing file. Re-read after Python, bash or an external process changes it.
This freshness guard and child environment filtering operate inside the
existing execution environment; container isolation remains on the roadmap.

The recovered implementation and remaining phases are recorded in
[docs/DSH-RECOVERY.md](../docs/DSH-RECOVERY.md).

## Docker Compose

From the repository root:

```bash
docker compose up --build
```

Frontend: http://localhost:3000 · backend: http://localhost:8000
