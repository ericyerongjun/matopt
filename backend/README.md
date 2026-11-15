# MatOpt backend

The FastAPI service runs the agent harness, Python kernels, document reading,
speech services, skills, MCP integrations and the data-analysis backend. The
React frontend and HTML preview runtime are separate Compose services.

For initial setup, follow [Run with Docker](../README.md#run-with-docker).
Commands on this page run from the repository root.

## Container operation

```sh
docker compose up --build -d
docker compose ps
docker compose logs -f --tail=100 backend
```

The API is at http://localhost:8000; the application is at
http://localhost:3000. `/health` reports server readiness, `/api/providers`
reports configured model routes, and `/api/speech/status` reports speech
availability and loaded models. See [the protocol](../docs/PROTOCOL.md) for
request and streaming contracts.

Compose builds `backend/Dockerfile` from the repository root. The image includes
Python dependencies, document utilities, Node.js and pnpm for artifact workflows.
The server runs as the non-root `matopt` user. The container uses
`SANDBOX_MODE=external`; native Seatbelt/bubblewrap wrapping is not enabled inside
this container. Conversations share the container boundary.

## Configuration

Edit `backend/.env`, using [.env.example](.env.example) as the settings reference.
Apply changes by recreating the services:

```sh
docker compose up -d --force-recreate
```

Recreation preserves the data volume but restarts kernels. A plain
`docker compose restart` does not reload the environment file. Changes to image
contents require `docker compose up --build -d`.

| Setting | Purpose |
| --- | --- |
| `DEEPSEEK_API_KEY` | Required for model requests and multimodal document reading |
| `DEEPSEEK_BASE_URL` | Optional provider endpoint override |
| `SPEECH_ENABLED`, `SPEECH_PREFETCH` | Voice availability and background model warmup; both default to `true` |
| `DOCUMENT_AUTO_PARSE`, `DOCUMENT_OCR_ENABLED` | Automatic attachment reading and multimodal OCR; both default to `true` |
| `WEB_SEARCH_ENABLED` | Web-tool registration; defaults to `true` |
| `DIAGNOSTICS_ENABLED` | Opt-in raw diagnostic endpoints; defaults to `false` |
| `MCP_CONFIG_PATH` | Optional path to an external-tool configuration inside the container |

Compose supplies `HOST=0.0.0.0`, `PORT=8000`, `DATA_DIR=/app/data`,
`SANDBOX_MODE=external`, the bundled skill paths and
`ALLOW_UNAUTHENTICATED_BIND=true`. These values override the same keys in
`backend/.env`. The published ports remain bound to the host's loopback address.
This configuration is for use on the presentation machine. A remote deployment
needs an explicit authentication and hostname configuration; do not simply
change the port bindings to expose it.

In external sandbox mode, unauthenticated API calls originating inside the
backend container are rejected. `/health` remains available there. Use the
browser or a host-side client for API checks; do not disable this rule for a
preflight script.

### Model and response behavior

The configured live model is `deepseek-flash`, labelled DeepSeek V4.1 Flash in
the UI. The harness, verification, auxiliary requests and data agent use this
route. **Ultrafast Demo** is the default browser profile: it uses real
non-thinking inference and avoids selected redundant requests. Turning it off
uses the configured model default. The UI has no manual reasoning-effort selector.

Shared [academic response guidance](app/academic_guidance.md) controls notation,
derivations and explanation style. After editing that file, rebuild the backend
image. Specialization comes from guidance, tools, skills and context; MatOpt does
not fine-tune the model.

### Web tools

The agent can search when a question needs current information. Search uses
`ddgs` by default; `SEARXNG_URL`, `TAVILY_API_KEY` or `BRAVE_SEARCH_API_KEY` can
configure alternatives. `/web-search` and `/web-fetch` select tools explicitly.
API clients can set `web_search: false` for a turn; `WEB_SEARCH_ENABLED=false`
disables registration globally. Respect a user's instruction not to browse.

## Persistent data and execution

| Container path | Contents |
| --- | --- |
| `/app/data/matopt.sqlite3` | Conversations and persistent application records |
| `/app/data/workspaces/` | Per-conversation files, uploads and generated artifacts |
| `/app/data/models/` | Downloaded speech models |
| `/app/data/mcp.env` | Connector credentials written by the integration API |
| `/app/skills/` | Repository skills mounted read-only by Compose |

The named `backend-data` volume preserves `/app/data` across ordinary shutdowns
and image rebuilds. Python variables persist only while their conversation's
kernel is alive. Kernel recycling or a backend restart loses that in-memory
state, so save important results as workspace files. `docker compose down`
keeps the volume; adding `-v` deletes it.

Tool execution checks arguments, permissions, timeouts and cancellation. File
editing requires a current read; re-read after Python, shell commands or another
process changes a file. Child environments use an allowlist, and tool output
redacts recognized credential patterns. These controls do not provide separate
container isolation for each conversation.

## PDFs screenshots and scanned documents

Newly attached PDFs and raster images are read before the assistant answers.
The backend validates bytes, renders PDF pages locally, normalizes images and
sends pages to the configured vision route. It uses the same DeepSeek API key.
Originals remain in `uploads/`; page-labelled Markdown, coverage and failure
notes are saved under `parsed/` in the conversation workspace.

| Setting | Default |
| --- | ---: |
| `DOCUMENT_OCR_MAX_PAGES` | 500 pages per reading |
| `DOCUMENT_OCR_CONCURRENCY` | 8 pages in flight |
| `DOCUMENT_OCR_MAX_FILES` | 20 documents per automatic turn |
| `DOCUMENT_OCR_MAX_FILE_MB` | 200 MB per file |
| `DOCUMENT_OCR_MAX_DIMENSION` | 2048 pixels per side |
| `DOCUMENT_OCR_MAX_PIXELS` | 32,000,000 decoded image pixels |
| `DOCUMENT_OCR_PAGE_TIMEOUT` | 90 seconds |
| `DOCUMENT_OCR_TIMEOUT` | 1800 seconds per document |

These are bounds, not latency promises. Large documents add model requests and
cost. Omitted or unreadable content is reported; check uncertain symbols against
the original. Complete readings are cached by source bytes, page range and
parser configuration. Stopping a turn cancels outstanding provider requests.

The `parse_document` tool accepts `auto`, `multimodal` or `fast` local extraction
and explicit page ranges. Set `DOCUMENT_AUTO_PARSE=false` for manual reading or
`DOCUMENT_OCR_ENABLED=false` to disable vision OCR. A local text fallback does
not establish that scanned text or diagrams were understood.

## Run limits and checking

Native workers, retries, checking and delegated model requests share a run
ledger. Document OCR is recorded in that ledger but exempt from the model-attempt
cap; it still observes the deadline.

| Setting | Default | Scope |
| --- | ---: | --- |
| `RUN_MAX_MODEL_ATTEMPTS` | 80 | Capped model admissions, including retries and delegated work |
| `RUN_MAX_TOOL_STARTS` | 160 | Native and delegated handler admissions |
| `RUN_TIMEOUT_SECONDS` | 1800 | Absolute deadline, including paused time |

Local tool limits may be stricter. Admitted attempts remain charged after errors
or cancellation; validation and policy rejections do not consume admissions.
Auxiliary title and follow-up requests are outside this execution budget.
Exhaustion ends with an explicit incomplete outcome and any available partial
results. These ceilings do not guarantee convergence or mathematical correctness.

The default operation policy blocks repeated matching failures while allowing
corrected arguments. Unknown side effects require inspection before retrying.
Repeated successful reads produce advisories, not proof of completion. See
[the loop-policy analysis](../docs/LOOP-POLICY-EXPERIMENT.md) and
[Codex integration](../docs/CODEX-INTEGRATION.md).

**Check results** allows at most two checks and one complete correction per
checking episode, under the shared run budget. Inconclusive outcomes stay labelled.
LLM checks can miss errors. Optional Lean checking requires an installed Lean
toolchain and checks only the submitted formal statement; it is not provisioned
by the standard Compose image.

Optional `MODEL_PRICING` entries supply token rates for usage estimates.
`MAX_TURN_COST` and `MAX_CONVERSATION_COST` can stop the next request at an
observed spend limit. Missing usage or pricing prevents exact enforcement, and
one request can exceed a limit. No provider prices are built in.

## History diagnostics and MCP

Session logging stores execution records alongside chat data. Public history
and exports contain final answers, committed outputs and safe activity labels;
raw reasoning and verifier reports stay private. Raw event, request and trajectory
routes return 404 unless `DIAGNOSTICS_ENABLED=true`. The usage view uses recorded
evidence rather than treating missing token data as zero.

The backend exposes the workspace MCP endpoint at `http://localhost:8000/mcp/`.
Call `open_workspace`, retain its `conversation_id`, and pass that ID on every
subsequent tool call. Concurrent work in the same workspace can return
`WORKSPACE_BUSY`. Workspace IDs select state; they are not credentials. Set
`MCP_AUTH_TOKEN` or the `MCP_OAUTH_*` settings before exposing MCP remotely.

External catalogs use exact tool IDs and explicit aliases. `search_tools`
discovers metadata and `load_tool_schemas` loads selected schemas. See
[the tool-discovery notes](../docs/TOOL-DISCOVERY-EXPERIMENT.md).
Container integrations need paths and services reachable from the container;
`localhost` inside it is not the host machine. A host `mcp.json` is not mounted
by the default Compose file. Configure a persistent container path and an
explicit mount when using a custom configuration.

For native development, tests, Lean installation and optional tracing, use the
[development guide](../docs/DEVELOPMENT.md).
