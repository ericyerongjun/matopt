# MatOpt API and streaming protocol

Backend: FastAPI at `http://localhost:8000` (Vite dev server proxies `/api` and `/health`).
All JSON. Times are Unix seconds (floats).

## Conversations

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/conversations?limit=200` | | `{conversations: Conversation[]}` (newest first) |
| POST | `/api/conversations` | `{title?, provider?, model?, reasoning?}` | `Conversation` |
| GET | `/api/conversations/{id}` | | `Conversation & {messages: Message[], running: boolean}` |
| PATCH | `/api/conversations/{id}` | `{title?, provider?, model?, reasoning?}` | `Conversation` |
| DELETE | `/api/conversations/{id}` | | `{deleted: true}` (stops run, kills kernel, deletes workspace) |
| POST | `/api/conversations/{id}/kernel/restart` | | `{restarted: true}` |
| GET | `/api/conversations/{id}/export?format=markdown\|ipynb` | | file download (`Content-Disposition`) |
| GET | `/api/conversations/{id}/events?from_seq=-1&limit=200` | Exclusive cursor; limit 1–1000 | `{events: SessionEvent[], next_seq: number, has_more: boolean}` |
| GET | `/api/conversations/{id}/requests/{seq}` | Sequence must point to `request/header` | Exact `{messages, tools, config}` or 404 |
| GET | `/api/conversations/{id}/trajectory` | | Grouped turns/steps/records and whole-log `stats` |
| GET | `/api/conversations/{id}/usage` | | Exact-or-unknown usage, coverage, configured cost, context estimate |
| GET | `/api/conversations/{id}/stats` | | Timing and counts from the whole log |
| GET | `/api/conversations/{id}/context` | | Latest matching route's prompt pressure and labelled estimate |

```ts
interface Conversation {
  id: string; title: string; provider: string|null; model: string|null; reasoning: string|null;
  created_at: number; updated_at: number; meta: Record<string, unknown>;
  message_count?: number; running?: boolean;
}
interface Message {
  id: string; conversation_id: string; seq: number; role: "user" | "assistant";
  content: UserContent | AssistantContent; created_at: number;
}
interface UserContent { text: string; attachments: Attachment[] }
interface Attachment { name: string; path: string; url?: string; size?: number; kind?: string }
interface AssistantContent {
  parts: Part[]; model: string; provider: string; reasoning: string;
  usage: Usage; status: "complete" | "stopped" | "error";
  plan?: PlanStep[]; stats: { llm_calls: number; tool_calls: number };
  presentation: "final"; work: WorkSummary; earlier_drafts?: string[]; notice?: string;
}
interface Usage { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number; reasoning_tokens?: number; cached_tokens?: number }
interface PlanStep { content: string; status: "pending" | "in_progress" | "completed" }
```

### Durable history

The ledger is enabled by default (`SESSION_LOG_ENABLED=true`). Existing message
history is seeded once with a legacy marker; it does not gain exact historical
usage. The ledger retains exact private execution records for provider replay.
Normal conversation reads and exports apply the same public presentation as the
live stream: one final answer, deliverables, public progress and identified activity.
Raw `/events`, `/requests/{seq}` and `/trajectory` endpoints return 404 unless
the operator explicitly enables `DIAGNOSTICS_ENABLED=true`. Normal chat and
Activity never request them; numeric `/usage`, `/stats` and `/context` remain available. Reading the conversation or exporting it repairs interrupted durable
turns before returning materialized messages. Disabling the flag restores the
legacy message replay path.

```ts
interface SessionEvent {
  seq: number; time: number; type: string; data: Record<string, unknown>;
  surfaceOp?: "append" | { op: "replace"; startSeq: number; endSeq: number };
  sourceEventSeqs?: (number | [number, number])[];
  ignorable?: true;
}
```

Sequence numbers are contiguous and session-local. Timestamps are epoch
seconds. Stored values are lossless, browser-safe JSON; nanosecond filesystem
timestamps use decimal strings. Source ranges refer to earlier event sequences.
The surface fold follows live node positions, so replacements are not simply
numeric sequence ranges. A request endpoint freezes the surface, tools and
config at its header.

Trajectory inputs carry `request_seq` plus a config/tools summary. Load the
exact request only when its Input inspector opens. Trajectory timing/counts
cover the whole log independently of paged events or the UI's 50-record tail.
`stats.turns` counts distinct turns with at least one closed step. Step zero
contains the turn input.

`usage.exact` is `null` if any attempt or legacy history lacks enough
evidence. `coverage` reports attempts, reported attempts, incomplete turns,
side tasks and legacy history. The last cumulative usage sample replaces
earlier samples; retry attempts are separate. Auxiliary title/follow-up calls
use paired `llm/side-task` records and are counted once. Prompt tokens include
cache tokens, and reasoning tokens are a subset of output; these subsets are
never added to the total a second time. Optional cache/reasoning buckets are
omitted when not reported across every attempt.

`usage.cost` is `null` without complete usage and operator-supplied
`MODEL_PRICING`. Rates per million tokens are keyed by `provider/model` and
use `input`, `output`, optional `cache_read`/`cache_write`, `currency`, `as_of`.
No current rates are assumed. Optional `MAX_TURN_COST` and
`MAX_CONVERSATION_COST` check observed spend before the next provider request;
one request can exceed a limit. The context estimate uses four characters per
token plus framing and is explicitly approximate.

### Public presentation

```ts
type Part =
  | { type: "text"; text: string; phase?: "answer" }
  | { type: "ui"; surface: UISurface }
  | { type: "artifact"; artifact: Artifact }
  | { type: "options"; items: string[] };
interface WorkSummary {
  status: "running" | "paused" | "complete" | "stopped" | "error";
  tool_count: number;
  verification: {
    status: "not_requested" | "skipped" | "correct" | "incorrect" | "uncertain" | "limit_reached" | "error";
    rounds: number;
  };
  steps: { label: string; status: "running" | "complete" | "error" }[];
  items?: ActivityItem[];
  current_progress?: ProgressUpdate | null;
  started_at?: number; finished_at?: number; duration_ms?: number;
}
interface ProgressUpdate {
  id: string; seq: number; text: string; created_at: number;
  phase: "work" | "check" | "final"; item_id?: string;
}
interface ActivityItem {
  id: string; parent_id: string | null; seq: number;
  kind: "phase" | "tool" | "agent" | "control";
  phase: "work" | "check" | "final";
  label: string; purpose: string; summary?: string; tool?: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | "paused";
  started_at: number | null; updated_at: number; finished_at: number | null;
  attempt: number;
  progress?: { completed: number; total?: number; unit?: string };
  outputs: { kind: "file" | "source" | "artifact" | "ui"; label: string;
    url?: string; path?: string; surface_id?: string }[];
}
interface Artifact {
  kind: "image" | "plotly" | "html" | "file" | "markdown" | "json" | "html_file";
  name: string; path?: string; url?: string; mime?: string; size?: number;
  data?: unknown; digest?: string;
}
```

Legacy `steps` use fixed work labels. New `items` have stable identities, safe
purposes and outcomes, real timestamps, and explicit output links. Parallel calls
remain distinct even when they use the same tool. Item `seq` is its revision,
independent of the run-level event cursor. Public progress is explicitly authored
through the progress mechanism or generated from observed execution milestones;
ordinary model narration is not promoted automatically. Raw reasoning, tool
arguments/stdout, agent messages, and verifier reports are excluded by nested
server allowlists. Public artifact or source links are intentional exceptions
for inspecting outputs and evidence; an extraction link does not make it a final
deliverable. See [live agent execution](AGENT-STREAMING.md) for UX and privacy details.
An older conversation with a separately saved partial correction can include
`earlier_drafts` plus a `notice`. Those drafts remain explicitly labelled and
collapsed; they are not represented as a fully corrected answer.

### Internal execution records

Private persisted parts additionally contain reasoning, tool calls/results,
verification reports, phases, plans and monitoring records. These are for exact
replay and operator diagnostics, not the normal chat renderer or exports.

Tool names and their arguments: `python {code, timeout?, reset?}`, `bash {command, timeout?}`,
`read_file {path, start_line?, max_lines?}`, `write_file {path, content}`, `edit_file {path, old_string, new_string, replace_all?}`,
`list_files {pattern?, path?}`, `grep {pattern, path?, glob?, case_sensitive?, max_results?}`,
`parse_document {path, backend?, pages?, lang?}`, `check_equivalence {expression_a, expression_b}`,
`update_plan {steps: PlanStep[]}`, `wolfram_alpha {query}` (only when configured),
`search_skills {query, limit?}`, `load_skill {name}`, `lambda_data_agent {message, autonomous?, language?}`,
`web_search {query, max_results?}` and `web_fetch {url}` (available by default when server web search is enabled).
Extra MCP tools appear as `{server}_{tool}` when `mcp.json` lists servers.
`result.meta` for `python` includes `status`, `duration_ms`, `execution_count`, `stdout`, `stderr`; for `bash` `exit_code`, `duration_ms`;
for `update_plan` `steps`; for `load_skill` `name`, `path`, `files`.

Raw skill instructions and tool payloads remain internal. Public activity may
show skill loading, identified tools, safe outcomes, output references, and
measured progress. The internal `report_progress` tool accepts brief public
commentary through an explicit contract; it never exposes provider reasoning.

## Skills

`GET /api/skills` → `{skills: [{name, description, source, license, files}]}`.
`GET /api/skills/{name}` → full `SKILL.md` body plus metadata.
`GET /api/tools` → `{tools: [{name, description, kind, source}]}` for the composer `/` menu (hides skill loaders and the internal UI tools).

Skill folders live in `skills/matopt/`, `skills/lambda/`, and `skills/anthropic/` (Agent Skills spec). LAMBDA data work is split like Anthropic's catalog: `/lambda-eda`, `/lambda-report`, `/lambda-slides`, `/lambda-notebook`, `/lambda-data-agent`. The harness scores the user message and attachments, injects up to two matching `SKILL.md` bodies, and copies the folder into the conversation workspace at `skills/<name>/`. Docker images copy that tree to `/app/skills` (compose also bind-mounts it).

## LAMBDA data agent

The original [AMA-CMFAI/LAMBDA](https://github.com/AMA-CMFAI/LAMBDA) FastAPI backend is vendored as `backend/lambda_app/` (package rename only) and mounted at `/api/v1` so LAMBDA's own URLs keep working. MatOpt's harness calls it through the `lambda_data_agent` tool.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/health` | `{status, service: "lambda-api", storage, code_execution}` |
| GET | `/api/v1/public/config` | `{local_mode, turnstile_enabled, turnstile_site_key}` |
| GET/POST | `/api/v1/conversations` | LAMBDA's conversation store (SQLite under `DATA_DIR/lambda/`) |
| POST | `/api/v1/chat/stream` | LAMBDA's original SSE data-agent loop |
| POST | `/api/v1/files/upload` | LAMBDA uploads |
| POST | `/api/v1/code/execute` | LAMBDA local code execution |
| POST | `/api/v1/export/...` | notebooks, Markdown/PDF reports, slides |

Local data lives in `DATA_DIR/lambda/` (not MatOpt `DATA_DIR/workspaces/`). Disable with `LAMBDA_ENABLED=false`.

`lambda_data_agent {message, autonomous?, language?}` forwards workspace `uploads/` into LAMBDA and copies generated files back to `lambda_output/`.

## MCP sandbox

The previous RestrictedPython eval loop is replaced by the conversation kernel, exported as an MCP server:

- Streamable HTTP: `POST http://localhost:8000/mcp` (FastMCP). Call `open_workspace` first.
- stdio: `cd backend && .venv/bin/python -m app.agent.mcp`
- HTTP is unauthenticated unless `MCP_AUTH_TOKEN` (bearer) or `MCP_OAUTH_*` with
  `MCP_PUBLIC_URL` (OAuth resource server; metadata at
  `/.well-known/oauth-protected-resource/mcp/`) is set. stdio never needs it.
- MCP Apps: `open_ui_app` renders a saved mini-app in hosts that support the
  `io.modelcontextprotocol/ui` extension. See [MCP-APPS-EVALUATION.md](MCP-APPS-EVALUATION.md).
- Extra servers: repo-root `mcp.json` (`mcpServers` map, same shape as Claude Code).

## Files

| Method | Path | Notes |
|---|---|---|
| GET | `/api/conversations/{id}/files` | `{files: [{path, size, mtime, url, kind, name}]}` |
| POST | `/api/conversations/{id}/files` | multipart, field `files` (repeatable). Returns `{files: [{name, path, url, size, kind, card}]}`; `card` is a Markdown dataset summary for CSV/XLSX/… or null |
| DELETE | `/api/conversations/{id}/files/{path}` | |
| GET | `/api/files/{id}/{path}?download=1` | serves any workspace file (images inline) |
| GET | `/api/files/{id}/pdf-preview/{path}?page=1` | one PNG page, `X-PDF-Pages` and `X-PDF-Page` headers; 1-based, 25 MB file limit, at most 1600px per side |

Workspace layout: `uploads/` (user files), `figures/` (captured plots: `fig_001.png`, `plot_001.json`), `parsed/` (document Markdown), anything else the agent writes.
**Relative links** in assistant Markdown such as `![caption](figures/fig_001.png)` or `parsed/paper/images/x.jpg` must be rewritten by the UI to `/api/files/{conversation_id}/<path>`.

Attached PDFs and raster images are automatically processed by `parse_document`
before the main answer when document reading is enabled. The configured DeepSeek
vision model receives bounded page images; the assistant receives page-labelled
Markdown and coverage/error notes. Originals stay in `uploads/`, readings in
`parsed/`. Explicit `backend` values are `auto`, `multimodal` and `fast`.
OCR emits controlled public reading progress and internal accounted `llm/side-task` events;
automatic tool calls are marked so recovery does not treat them as missing model
dispatches. See [document reading](../backend/README.md#pdfs-screenshots-and-scanned-documents)
for page, file, image and timeout limits.

## Interactive UI

Chat requests advertise `ui_version: 1`. Compatible clients may receive ordered
`{type:"ui",surface:UISurface}` parts. The generated public types and JSON Schema
come from `backend/app/ui/contracts.py`; clients should keep their readable
fallback when the surface kind is disabled or unavailable.

| Method | Path | Body / response |
|---|---|---|
| GET | `/api/ui/capabilities` | `{version:1,native,mini_apps,node_types}` |
| GET | `/api/conversations/{id}/ui/{surface_id}` | `{surface}` |
| PATCH | `/api/conversations/{id}/ui/{surface_id}/state` | `{revision,state}` → `{surface}` |
| POST | `/api/conversations/{id}/ui/{surface_id}/actions` | `{action_id,revision,values,idempotency_key}` → SSE |

Internally, `ui_start`, `ui_patch` and `ui_commit` each carry a complete `{surface}`
snapshot. Chat stages them until the final answer is ready, then publishes only
committed surfaces. Only committed surfaces enable controls. Internal `ui_error` carries
`{surface_id,revision,error,fallback}`. State edits do not invoke the model;
registered actions execute versioned Python handlers through the conversation
kernel and return a new committed revision. Stale revisions are refused and
idempotency keys replay their original result. Final message snapshots and
history preserve committed surfaces. See [interactive UI architecture](INTELLIGENT-UI.md)
for streaming, repair, state, cancellation and isolated mini-app details.

## Providers

`GET /api/providers` →
```ts
{
  default_provider: string|null; default_model: string|null; default_reasoning: string; verify_available: true;
  levels: {id: "off"|"low"|"high"|"max"; label: string}[];   // thinking effort order
  providers: {
    id: string; label: string; configured: boolean; env_key: string; docs_url: string; default_model: string;
    models: { id: string; label: string; family: string; description: string; context_window: number; max_output: number;
              reasoning_levels: string[]; default_reasoning: string; tags: string[] }[];
  }[];
}
```
The catalog contains exactly one provider/model: `deepseek` / `deepseek-flash`
(DeepSeek V4.1 Flash). `configured` indicates whether `DEEPSEEK_API_KEY` is
present. New turns, verification and auxiliary requests use that route only.
The composer exposes `off`, `low`, `high`, `max`, defaulting to `high`; old
`medium` effort normalizes to `high`. Known obsolete saved model IDs normalize
for new turns; arbitrary providers or models are rejected. Historical ledger
requests keep their original labels.
[DeepSeek API contract](https://api-docs.deepseek.com/api/create-chat-completion/).

## Chat stream

`POST /api/chat/stream` with
```ts
{ conversation_id?: string; message: string; attachments?: Attachment[]; provider?: string; model?: string;
  reasoning?: string; verify?: boolean; strict_final?: boolean; temperature?: number;
  skills?: string[]; tools?: string[]; web_search?: boolean; ui_version?: number; client_run_id?: string }
```
`skills` force-activates those playbooks for the turn (even when auto-activate is off). `tools` prefers those native tools. Leading `/skill-name` and `/tool-name` tokens in `message` do the same and are stripped before the LLM sees the prompt (`/web-search` maps to `web_search`).
Omitting `web_search` (or sending `true`) advertises `web_search` / `web_fetch` by default (open metasearch: `ddgs`, or SearXNG / Tavily / Brave when configured). The agent automatically searches for time-sensitive information and cites current sources; stable tasks do not require browsing. `web_search: false` explicitly disables the capability for a turn. A subsequent explicit slash invocation or steer can enable it. `WEB_SEARCH_ENABLED=false` disables both tools globally. User instructions not to browse are respected.
Response: `text/event-stream`, with `X-Run-Id` identifying the durable run. Each
public event has an SSE `id: <seq>` and a JSON envelope containing `type`,
`run_id`, `conversation_id`, and run-local `seq`. Errors before streaming include
HTTP 400 for invalid provider selection and 409 for a conflicting active run or
reused `client_run_id` with a different request.

`client_run_id` makes accepted starts idempotent: retrying the same request
reattaches to its run instead of repeating execution. The run manager owns the
producer. Closing or losing an SSE connection closes only that subscription.
All public events, including text deltas, are journaled separately from private
session events.

| Method | Path | Behavior |
|---|---|---|
| GET | `/api/chat/{conversation_id}/run` | `{run: Run \| null}` for the active or latest run |
| GET | `/api/runs/{run_id}` | `{run: Run}`, or 404 |
| GET | `/api/runs/{run_id}/events?after=0` | Replays the public event prefix and tails the same run |
| POST | `/api/chat/{conversation_id}/pause` | `{accepted: true, run_id}` or 409; pauses at a safe boundary |
| POST | `/api/chat/{conversation_id}/resume` | `{accepted: true, run_id}` or 409; releases the pause |
| POST | `/api/chat/{conversation_id}/steer` | `{message, skills?, tools?}` → `{accepted: true, run_id}` or 409 |
| POST | `/api/chat/{conversation_id}/stop` | `{stopped: boolean, run_id}`; explicitly cancels execution and interrupts the kernel |

`Run` contains `id`, `conversation_id`, `message_id`, `status`, `created_at`,
`updated_at`, and `last_seq`. Lifecycle statuses are `queued`, `running`,
`paused`, `completed`, `stopped`, `failed`, and `interrupted`.

The events endpoint also accepts `Last-Event-ID`; it uses the later of that
cursor and `after`. Clients deduplicate using `(run_id, seq)`. Reconnection must
subscribe to this endpoint rather than restart the work. A heartbeat is an SSE
comment (`: keepalive`), with no sequence number or execution claim. A closed
connection is not a successful terminal event.

Controls distinguish queued acknowledgment from applied state. Pause waits for
an in-flight operation to finish or reach a safe boundary, including boundaries
during final output. Steering is applied before final writing; requests arriving
too late are rejected rather than claimed as applied. A backend restart repairs
orphaned active runs from saved checkpoints, settles unfinished activity items,
and marks them `interrupted`, retaining completed outputs. If a complete final
answer was committed before its terminal SSE event, recovery marks the run
completed without regenerating it. No model or tool is automatically replayed.
Cross-process execution continuation and multiworker scheduling are not
implemented; the runtime uses process-owned tasks, SQLite, and one Uvicorn worker.
In-process pause/resume does not repeat completed actions.

The public run cursor is unrelated to `/conversations/{id}/events`, which is the
operator-only private session ledger. Normal UI uses public run events and
canonical `/conversations/{id}` state.

`POST /api/followups` accepts `{content, count?, provider?, model?, conversation_id?}`
and returns `{followups, usage}`. Supplying a conversation attributes the
auxiliary attempt to its ledger. `GET /api/suggestions` returns provider usage
when available, but its global welcome prompts have no conversation to charge.

Public events (progress, activity, usage, and controls can interleave):

| type | fields | meaning |
|---|---|---|
| `conversation` | `conversation_id, title` | identifies the chat |
| `message_start` | `message_id, provider, model, reasoning` | turn begins; `reasoning` is the effort setting, never thought text |
| `run_state` | `status` | authoritative run lifecycle, including safe-boundary pause/resume |
| `progress` | `update: ProgressUpdate` | latest useful public update |
| `activity` | `item: ActivityItem` | inserts or updates one identified work item |
| `work` | `work: WorkSummary` | complete public work snapshot |
| `usage` | token counts | one provider call's numeric usage |
| `control_ack` | `action, status, control_id` | queued/applied acknowledgment for pause, resume, stop, or steer |
| `steer_applied` | `text` | acknowledges the user's own steering input |
| `final_start` | `message_id, mode: "streaming" \| "buffered"` | begins the answer; preserve work and existing output identities |
| `text_delta` | `delta` | provider-generated final text, or explicitly buffered strict-mode delivery |
| `ui_start`, `ui_patch` | `surface` | validated native preview versions |
| `ui_commit` | `surface` | completed interactive result |
| `ui_error` | `surface_id, revision?, fallback?, error` | safe interactive-result failure |
| `artifact` | `artifact` | user-facing generated output |
| `options` | `items` | optional next-step chips |
| `message_end` | `message_id, status, content` | canonical public snapshot; replace streamed content, never append a duplicate answer |
| `done` | `conversation_id, message_id` | successful completion |
| `stopped` | `conversation_id, message_id` | cancellation |
| `error` | `message` | safe failure message |

Internal text, reasoning, tool code/stdout and verification reports are never
forwarded as public deltas. Eligible simple replies stream directly. Tool-heavy
runs perform requested checks before a separate tool-free final writing pass,
which streams actual provider deltas from the result bundle and is instructed
not to add research, calculations, or unsupported claims. This writing step does
not guarantee exact-wording preservation. For that requirement, API callers use
`verify: true, strict_final: true`: the accepted answer is buffered through the
checking path and released without an additional rewriting call. Strict mode
alone does not enable checks or imply that an inconclusive check passed.

Validated native UI can update during execution; executable HTML is admitted
only as a complete committed surface. Strict mode withholds intermediate UI.
Cancellation or failure does not promote a private draft into a final answer.
The canonical `message_end` content settles any streamed preview in place.

Verification is opt-in per turn. Trivial greetings/acknowledgements are skipped.
A substantive answer has at most two independent checks and one correction,
with bounded tool steps. Correct or inconclusive results end checking; only an
explicit incorrect verdict permits correction. A correction must replace the
whole answer, including all questions, before the last check. The final check
never starts an unchecked extra revision. Inconclusive, failed or exhausted
checks get an honest short notice. The UI never displays the private report.

## Rendering notes

- Live commentary and active tool items appear above the answer. One collapsed
  work disclosure preserves completed actions, and manual expansion survives
  completion. Activity groups phase/agent/tool entries with purposes, outcomes,
  durations, actual retries, and file/source links; filters select running work,
  failures, or outputs. Private reasoning and raw payloads remain inaccessible.
- Final text uses Markdown, KaTeX, code, Mermaid and tables. Generated outputs
  and validated interactive surfaces remain usable inline and in the canvas.
  Explicit source fences stay literal. Incoming final text renders directly;
  there is no timed animation pretending buffered output is live generation.
- Running indicators use restrained motion and respect reduced motion. Elapsed
  time is based on actual timestamps; percentages require a measured total.
- `earlier_drafts`, when present on legacy messages, stay collapsed and labelled
  as unverified. Copy copies only the current final answer.
- After a terminal stream, GET `/conversations/{id}` reconciles saved messages
  and IDs in place. Preserve the current chat, unsent draft, scroll position and
  newer turns. A failed refresh keeps the terminal streamed answer.
- Optional follow-up generation runs after the busy state ends and cannot
  overwrite a newer conversation or turn.
