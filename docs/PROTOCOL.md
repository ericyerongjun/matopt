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
  plan: PlanStep[]; stats: { llm_calls: number; tool_calls: number };
}
interface Usage { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number; reasoning_tokens?: number; cached_tokens?: number }
interface PlanStep { content: string; status: "pending" | "in_progress" | "completed" }
```

### Parts (ordered; this is what the UI renders)

```ts
type Part =
  | { type: "text"; text: string; phase?: Phase }
  | { type: "reasoning"; text: string; phase?: Phase }
  | { type: "tool_call"; id: string; name: string; arguments: Record<string, unknown>;
      result: ToolResult | null; status: "running" | "completed" | "error" | "stopped"; phase?: Phase }
  | { type: "phase"; phase: "verify" | "revise"; round: number }
  | { type: "verification"; verdict: "correct" | "incorrect" | "uncertain"; report: string; round: number }
  | { type: "skills"; names: string[]; auto?: boolean }
  | { type: "options"; items: string[] }
  | { type: "monitor"; level: string; summary: string; tool?: string };
type Phase = "answer" | "verify" | "revise";   // default "answer"

interface ToolResult { ok: boolean; output: string; artifacts: Artifact[]; error: string | null; meta: Record<string, unknown> }
interface Artifact {
  kind: "image" | "plotly" | "html" | "file" | "markdown" | "json" | "html_file";
  name: string; path?: string; url?: string; mime?: string; size?: number;
  data?: unknown;   // plotly: {data, layout, config?}; html: string (DataFrame table)
}
```

Tool names and their arguments: `python {code, timeout?, reset?}`, `bash {command, timeout?}`,
`read_file {path, start_line?, max_lines?}`, `write_file {path, content}`, `edit_file {path, old_string, new_string, replace_all?}`,
`list_files {pattern?, path?}`, `grep {pattern, path?, glob?, case_sensitive?, max_results?}`,
`parse_document {path, backend?, pages?, lang?}`, `check_equivalence {expression_a, expression_b}`,
`update_plan {steps: PlanStep[]}`, `wolfram_alpha {query}` (only when configured),
`search_skills {query, limit?}`, `load_skill {name}`, `lambda_data_agent {message, autonomous?, language?}`,
`web_search {query, max_results?}` and `web_fetch {url}` (only advertised when the client sends `web_search: true`).
Extra MCP tools appear as `{server}_{tool}` when `mcp.json` lists servers.
`result.meta` for `python` includes `status`, `duration_ms`, `execution_count`, `stdout`, `stderr`; for `bash` `exit_code`, `duration_ms`;
for `update_plan` `steps`; for `load_skill` `name`, `path`, `files`.

A `skills` SSE event (`{names, auto}`) is emitted when the router auto-activates playbooks. `status` also names them.

## Skills

`GET /api/skills` → `{skills: [{name, description, source, license, files}]}`.
`GET /api/skills/{name}` → full `SKILL.md` body plus metadata.

Skill folders live in `skills/matopt/` and `skills/anthropic/` (Agent Skills spec). The harness scores the user message and attachments, injects up to two matching `SKILL.md` bodies, and copies the folder into the conversation workspace at `skills/<name>/`.

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
- Extra servers: repo-root `mcp.json` (`mcpServers` map, same shape as Claude Code).

## Files

| Method | Path | Notes |
|---|---|---|
| GET | `/api/conversations/{id}/files` | `{files: [{path, size, mtime, url, kind, name}]}` |
| POST | `/api/conversations/{id}/files` | multipart, field `files` (repeatable). Returns `{files: [{name, path, url, size, kind, card}]}`; `card` is a Markdown dataset summary for CSV/XLSX/… or null |
| DELETE | `/api/conversations/{id}/files/{path}` | |
| GET | `/api/files/{id}/{path}?download=1` | serves any workspace file (images inline) |

Workspace layout: `uploads/` (user files), `figures/` (captured plots: `fig_001.png`, `plot_001.json`), `parsed/` (document Markdown), anything else the agent writes.
**Relative links** in assistant Markdown such as `![caption](figures/fig_001.png)` or `parsed/paper/images/x.jpg` must be rewritten by the UI to `/api/files/{conversation_id}/<path>`.

## Providers

`GET /api/providers` →
```ts
{
  default_provider: string|null; default_model: string|null; default_reasoning: string; verify_available: true;
  levels: {id: "off"|"low"|"medium"|"high"|"max"; label: string}[];   // global slider order
  providers: {
    id: string; label: string; configured: boolean; env_key: string; docs_url: string; default_model: string;
    models: { id: string; label: string; family: string; description: string; context_window: number; max_output: number;
              reasoning_levels: string[]; default_reasoning: string; tags: string[] }[];
  }[];
}
```
The reasoning slider shows only the stops in `model.reasoning_levels` (ordered as in `levels`); default position = `default_reasoning`.

## Chat stream

`POST /api/chat/stream` with
```ts
{ conversation_id?: string; message: string; attachments?: Attachment[]; provider?: string; model?: string;
  reasoning?: string; verify?: boolean; temperature?: number; skills?: string[]; web_search?: boolean }
```
`skills` force-activates those playbooks for the turn (even when auto-activate is off). Leading `/skill-name` tokens in `message` do the same and are stripped before the LLM sees the prompt.
`web_search: true` advertises `web_search` / `web_fetch` (open metasearch: `ddgs`, or SearXNG / Tavily / Brave when configured).
`POST /api/chat/{id}/steer` `{message}` folds a correction into the running turn at the next tool boundary (Claude/Astra-style mid-turn steering).

Response: `text/event-stream`; each frame is `data: <json>\n\n` with a `type` field. Errors before streaming: HTTP 400 (no provider configured), 409 (run already in progress).
`POST /api/chat/{conversation_id}/stop` cancels (kernel interrupted, partial turn persisted, stream ends with `stopped`).

Event order for a typical turn:

| type | fields | meaning |
|---|---|---|
| `conversation` | `conversation_id, title` | always first; the id may be new |
| `message_start` | `message_id, provider, model, reasoning` | assistant turn begins |
| `status` | `message, id?` | human readable progress line (retrying, OCR running…) |
| `skills` | `names, auto` | playbooks activated for this turn (`auto` is false when the user picked `/skill`) |
| `options` | `items` | next-step chips the user can pick |
| `monitor` | `level, summary, tool` | local alignment watch/block on a tool call |
| `steer_applied` | `text` | mid-turn steering was folded into the job |
| `reasoning_delta` | `delta` | model thinking text (when the provider exposes it) |
| `text_delta` | `delta` | visible assistant text |
| `tool_call_start` | `id, name` | the model started emitting a tool call (show a card immediately) |
| `tool_call_delta` | `id, delta` | streamed JSON of the arguments (for `python` the `code` field streams in; show it live if you can parse partial JSON, otherwise show raw) |
| `tool_call_ready` | `id, name, arguments` | arguments parsed; tool is executing (`arguments` is an object; `{_raw}` if unparsable) |
| `tool_progress` | `id, delta` | live stdout while the tool runs |
| `plan` | `steps` | plan checklist changed |
| `tool_result` | `id, name, ok, output, error, artifacts, meta` | tool finished |
| `usage` | `prompt_tokens, completion_tokens, …` | one LLM call's usage |
| `phase` | `phase: "verify"\|"revise", round` | verify mode only |
| `verification` | `verdict, report, round` | verifier report (verify mode) |
| `message_end` | `message_id, status, content` | the persisted assistant message (`content` is `AssistantContent`) — use it to replace the streamed state |
| `done` | `conversation_id, message_id` | success (never sent after `error`) |
| `stopped` | `conversation_id, message_id` | cancelled |
| `error` | `message` | failure; the stream ends after it (after `message_end`) |
| `keepalive` | `ts` | heartbeat every 10 s while waiting |

Reasoning, text and tool calls interleave across steps: build the message as an ordered `parts[]` — a `reasoning_delta` after a `tool_result` opens a *new* reasoning part, a `text_delta` after a tool result opens a new text part, etc. Tool-call parts are keyed by `id`.

## Rendering notes

- Assistant text: Markdown + KaTeX (`$…$`, `$$…$$`), fenced code, `mermaid`, tables; rewrite relative image/file links as above.
- `python` tool card: code (syntax highlighted), status pill (running / ok / error), duration, stdout/stderr, `text/html` artifacts (DataFrames) in a scrollable container, `image` artifacts inline, `plotly` artifacts rendered with `react-plotly.js` from `artifact.data` (fallback: fetch `url`).
- `bash` card: command + terminal-style output. File tools: compact one-liners with the path. `parse_document`: backend/statistics + link to the Markdown. `check_equivalence`: verdict badge. `update_plan`: checklist with progress.
- `reasoning` parts: collapsible "Thinking" block, collapsed once the answer text starts.
- `verification` parts: card with verdict colour (green/red/amber) and the report; `phase` parts render as a small divider ("Verifying…", "Revising…").
- Stopped/errored messages keep their partial parts and show a small notice.
- Tool traces, plan, monitor chips, and figures live in the compact Work pane (not the main bubble).
- `options` parts render as next-step chips under the answer; clicking one starts the next turn.
