# Codex integration

Started 30 October 2026. Source: [openai/codex](https://github.com/openai/codex) at
`67f0994b`, `codex-rs/` (Apache-2.0, Rust, ~2M lines across ~120 crates). MatOpt is
Python and an analytical agent, not a coding agent, so nothing is vendored or
translated line by line. Each mechanic below is reimplemented in Python against
MatOpt's own harness, tools and session log, and credited in `NOTICE`.

## What Codex has and where MatOpt stands

| Area | Codex (crate / file) | MatOpt before | Decision |
| --- | --- | --- | --- |
| Turn loop | `core/src/session/turn.rs` | `harness._agent_loop` — same shape, plus verify/revise | Keep MatOpt's loop |
| Sampling retries | `core/src/responses_retry.rs`, `protocol/src/error.rs`, `async-utils/backoff.rs` | one retry, fixed 1.5 s, string-matched errors | **Port**: typed classification, exponential backoff with jitter, Retry-After |
| Token accounting | `context_manager/history.rs::get_total_token_usage` | last prompt tokens only | **Port**: last usage + estimate of items added since |
| Context-overflow recovery | `compact.rs` (`remove_first_item`) | none | **Port**: compact and retry on context-length errors |
| Mid-turn compaction | `session/turn.rs` (`token_limit_reached && needs_follow_up`) | cuts only at user-run boundaries | **Port** |
| Interrupt marker | `context/turn_aborted.rs` | synthetic tool closers only | **Port**: model-visible "previous turn was stopped" note |
| History normalisation | `context_manager/normalize.rs` | `session/repair.py`, parity assertion | MatOpt already equal or better |
| Rollout / resume | `rollout`, `session/rollout_reconstruction.rs` | SQLite event log, `fork`, parity | Equal |
| Remote / async compaction v2 | `compact_remote_v2*.rs` | — | Not portable (OpenAI backend only) |
| Output capture & truncation | `utils/head-tail-buffer`, `utils/output-truncation` | unbounded capture, 2/3 head truncation | **Port**: capped head/tail buffer, 50/50 truncation with counts |
| Exec result format | `tools/mod.rs::format_exec_output_for_model` | error appended after output | **Port**: status header first |
| unified_exec sessions | `core/src/unified_exec/` (`exec_command` + `write_stdin`) | blocking `bash` only | **Port**: background sessions + `bash_poll` |
| apply_patch | `apply-patch/` | single-string `edit_file` | **Port** the fuzzy matcher (`seek_sequence.rs`) into `edit_file` |
| Parallel tools | `tools/parallel.rs` (RwLock) | `scheduler.schedule_calls` | Equal |
| Secret redaction | `secrets/src/sanitizer.rs` | none | **Port** |
| Env policy | `protocol/shell_environment.rs` | `child_env.build_child_env` (allowlist) | MatOpt stricter; keep |
| Login profiles | `--noprofile`-style spawn | `bash -lc` sources agent-writable profiles | **Fix** |
| Sandbox policy | `sandboxing/`, `protocol::SandboxPolicy` | none | **Port**: policy + seatbelt (macOS) + bwrap (Linux) wrappers |
| execpolicy | `execpolicy/` (prefix rules, strictest wins) | substring denylist | **Port**: prefix rules with load-time examples |
| Hooks | `hooks/` (14 events, JSON stdin/stdout, exit 2 blocks) | none | **Port**: PreToolUse, PostToolUse, UserPromptSubmit, SessionStart, Stop |
| AGENTS.md | `core/src/agents_md.rs` | none | **Port** |
| MCP client | `codex-mcp/`, `config/mcp_types.rs` | bare names for one server (shadowing bug), global timeout | **Port**: `mcp__server__tool` names, collision refusal, per-server enable/timeouts/allow-deny |
| Skills | `skills/`, `ext/skills/` | `agent/skills/` | MatOpt comparable |
| Turn diff | `core/src/turn_diff_tracker.rs` | mtime snapshots, deletions ignored | **Port** |
| File search | `file-search/` (nucleo fuzzy) | `fnmatch` listing | **Port**: fuzzy `find_files` tool |
| Approvals UI | `tools/orchestrator.rs`, `approvals.rs` | `needs_approval` declared, unread | Later: needs frontend protocol |
| Network proxy | `network-proxy/` | none | Later |
| Docker exec-server | `exec-server/` | none | Later: separate service |
| Memories, plugins, layered config, feature registry | `memories/`, `plugin/`, `config/`, `features/` | none | Later |
| TUI, app-server, cloud tasks, login, realtime, Windows sandbox, code mode (V8) | — | — | Not applicable |

## Status (30 October 2026)

Landed, each with tests; the backend suite passes (659 tests).

| Mechanic | MatOpt module | Notes |
| --- | --- | --- |
| Non-login shell | `tools/shell_tool.py` | `bash --noprofile --norc -c`; agent-written profiles never run |
| Private IPython dir | `kernel.py` | per-start `IPYTHONDIR`; agent-written startup files never run |
| Bounded capture | `exec_output.HeadTailBuffer` | 1 MiB head/tail for bash and kernel output; chunked reads |
| Status-first exec output | `exec_output.exec_header` | exit code, wall time, truncation, then `Output:` |
| Secret redaction | `exec_output.redact_secrets`, `execution.py` | every tool result, `REDACT_TOOL_OUTPUT` |
| MCP naming and policy | `mcp/client.py` | never shadows a native tool (`mcp__server__tool`); per-server `enabled`, `startup_timeout_sec`, `tool_timeout_sec`, `enabled_tools`, `disabled_tools` |
| Typed retries | `llm/retry.py`, `harness._stream_llm` | status/type classification, 200 ms × 2ⁿ ± 10 %, Retry-After, `LLM_MAX_RETRIES`; a stop ends the wait |
| Token estimate | `harness._estimated_prompt_tokens` | last usage + 4-char estimate of items added since |
| Overflow recovery | `harness.ContextOverflow` | context-length errors compact and redispatch the step (twice per turn at most) |
| Mid-turn compaction | `compaction.plan_intra_turn_cut` | summarises earlier steps of one oversized turn; request, steers and newest step kept |
| Interrupt marker | `prompts.build_turn_context` | next turn is told the previous one was stopped |
| Hooks | `hooks.py` | SessionStart, UserPromptSubmit, PreToolUse (deny / rewrite, re-validated), PostToolUse, Stop; exit 2 blocks; off by default |
| AGENTS.md | `instructions.py` | operator scope in the system prompt, workspace scope in the turn context, 32 KiB cap, override files |
| Exec rules | `exec_rules.py` | argv prefix rules, strictest wins, load-time examples, TOML extension; replaces the substring denylist |
| OS sandbox | `sandbox.py` | Seatbelt / bubblewrap for bash, lean and the kernel; `SANDBOX_MODE` (default `off`); fails closed |
| Turn diff | `turn_diff.py`, `GET /api/conversations/{id}/turns/{turn}/diff` | write/edit baselines, exec changes from a turn-start cache, deletions; `workspace/changes` session event |
| Tolerant edits | `text_match.py`, `edit_file` | trailing / surrounding whitespace, Unicode punctuation; still unique |
| Fuzzy file search | `find_files` tool | subsequence scoring, segment and file-name bonuses, smart case |
| Background exec | `tools/exec_sessions.py`, `bash_poll` | `bash` with `background: true`; poll, input, kill; 8 per conversation, LRU eviction, 1 h lifetime |

Known limits:

- The Seatbelt profile starts from `(allow default)` and denies file writes,
  protected reads and network. Codex's `(deny default)` profile would need
  tuning for NumPy, SciPy, Matplotlib and PyTorch. With network off, the
  kernel's loopback IOPub pipe cannot bind, so output written by forked
  subprocesses is not captured. Loopback stays closed on purpose: allowing it
  would expose the backend's own API on localhost.
- bubblewrap needs unprivileged user namespaces, which Docker's default
  seccomp profile blocks. Inside the production container, use
  `SANDBOX_MODE=external` plus container hardening, or a custom seccomp
  profile. The bwrap argv is unit-tested only; it has not run on a Linux host.
- `prompt` exec rules refuse, because there is no approval broker yet.
- The data agent's standalone `local_code_execution.py` is not wrapped.
- Live `tool_progress` deltas (the private terminal stream) are not redacted.
  Only the result the model sees is.

Still open (needs frontend or deployment work): the approval broker and UI, a
domain-allowlist network proxy, a per-workspace Docker executor (Codex's
exec-server), memories, plugins, layered config and a feature registry.

## Phase 2: sub-agents, memories, guardian, code mode, config, plugins

Investigated 30 October 2026 over the remaining crates. Verdicts:

| Crate(s) | Decision |
| --- | --- |
| `core/src/agent/`, `agent-roles`, `agent-graph-store`, multi-agent tools (v2) | **Port**: `agent/subagents/` — roles, spawn/wait/send/followup/interrupt/list, shared `RunBudget`, `subagents` table |
| `message-board`, `message-board-mcp`, `agent-message-board-client` | Skip the board (Slack-like channels between agents). Port the direct mailbox only: child results and `send_message` reach the parent at its next step |
| `agent-identity`, `runtime-agent-instance` | Not applicable: Ed25519/JWT registration of Codex runtimes with ChatGPT |
| `memories`, `ext/memories` | **Port**: `agent/memory/` — extraction per idle conversation, consolidation into `memory_summary.md`, read path in the system prompt, note/search/read tools |
| `guardian-context`, `core/src/guardian/` | **Port**: `agent/guardian.py` — model review of approval-requiring calls, Codex verdict schema, circuit breaker |
| `code-mode*` | **Adapt**: the Python kernel gets a `tools` object that calls MatOpt tools over a private Unix socket through the same dispatch path |
| `config`, `config-schema`, `features` | **Port**: TOML user layer + profiles under env, feature registry with stages, settings API |
| `plugin`, `core-plugins` (local subset) | **Port**: manifest bundling skills / MCP servers / hooks; install, enable, disable |
| `external-agent-migration` | **Port** a subset: detect and import Claude Code / Cursor / Codex MCP servers, skills and instruction files |
| `codex-mcp`, `rmcp-client` (resources, prompts, elicitation) | **Port** resources (`list_mcp_resources`, `read_mcp_resource`) and per-server status |
| `utils/critical-path`, `direct-tool-timing`, `analytics` (critical path only) | **Port**: per-turn inference / tool / wait / overhead breakdown |
| `keyring-store`, `secrets` | Later (desktop only) |
| `attachment-store`, `history`, `message-history`, `rollout`, `rollout-trace`, `file-system`, `core-api`, `protocol`, `app-server-protocol`, `http-client`, `websocket-*`, `async-utils`, `install-context`, `build-info`, `connectors`, `mermaid`, `plugin-host`, `shell-command` | Skip: MatOpt already has the equivalent (session log, OTel, httpx, retry), or the crate is TUI / ChatGPT-specific. `mermaid` renders to terminal box art; the web UI renders Mermaid itself |

### Contracts shared by backend and frontend

Public run events (journaled by `runs.project_public_event`, validated in `frontend/src/services/stream.ts`):

- `subagent` `{id, path, nickname, role, kind: started|completed|errored|interrupted|closed, status, task, summary?}`
- `approval_request` `{id, tool, title, summary, reason, risk}` and `approval_resolved` `{id, decision: allow|deny|timeout, by: user|guardian}`
- `guardian_review` `{call_id, tool, outcome: allow|deny|error, risk_level, user_authorization, rationale}`
- `workspace_changes` `{turn, files: [{path, status, added?, removed?, binary?, untracked?}]}`; saved assistant content carries `turn`

REST:

- `GET /api/settings`: features, security (read-only), instructions, MCP server status, config layers
- `PUT /api/features/{key}` `{enabled}`: writes the user TOML layer; security settings are never writable here
- `GET /api/conversations/{id}/agents`: sub-agents of a conversation
- `GET /api/conversations/{id}/jobs`: background shell jobs
- `POST /api/runs/{run_id}/approvals/{approval_id}` `{decision: allow|deny}`
- `GET /api/memories`, `POST /api/memories/notes`, `PUT|DELETE /api/memories/notes/{id}`, `DELETE /api/memories`, `PUT /api/conversations/{id}/memory` `{mode: enabled|disabled}`
- `GET /api/plugins`, `POST /api/plugins/install` `{path}`, `POST /api/plugins/{id}/enable|disable`, `DELETE /api/plugins/{id}`
- `GET /api/migration/detect`, `POST /api/migration/import` `{items: [id]}`

### Phase 2 status (30 October 2026)

Landed with tests: backend 749 passed and 1 skipped; frontend 358 passed and the build passes. One live end-to-end run against DeepSeek covered two sub-agents in parallel, the Agents tab, the changes chip and diff tab, and a user-denied approval of `git push`. That run had sandbox `workspace-write` with network on, and sub-agents, memories, code mode and approvals (`user`) all enabled.

| Mechanic | MatOpt module | Notes |
| --- | --- | --- |
| Sub-agents | `agent/subagents.py`, `subagents` table, `GET /api/conversations/{id}/agents` | `spawn_agent`, `wait_agent`, `send_message`, `followup_task`, `interrupt_agent` and `list_agents`. Roles: default, analyst, researcher, reviewer, plus `<data_dir>/agents/*.toml`. Each child gets its own kernel and shares the parent's budget and cancellation. The parent waits for its children before answering. Depth is limited to 1. |
| Approvals | `agent/approvals.py`, `POST /api/runs/{run}/approvals/{id}` | `APPROVALS_REVIEWER=off\|user\|guardian`. Triggers: exec rules that say `prompt`, destructive tools, and write-capable MCP tools. |
| Guardian | `agent/guardian.py` | A single structured reviewer call with Codex's trust model and verdict schema. Fails closed, and a circuit breaker stops the turn after repeated denials. |
| Memories | `agent/memory/`, `/api/memories*` | Phase 1 extraction from idle conversations, phase 2 consolidation, and a read path in the system prompt. Tools: `memory_search`, `memory_read`, `memory_note`. Per-chat opt-out. Conversations that read the web are not learned from. |
| Code mode | `agent/kernel_tools.py` | `tools.<name>(...)` inside Python cells, carried over a private Unix socket and checked by token. Calls go through the normal execution boundary, using an allow-list of read-only and compute tools. |
| Layered config and features | `config.py` (`~/.matopt/config.toml`, `[profiles]`, `[features]`), `features.py`, `GET /api/settings`, `PUT /api/features/{key}` | Security settings are read-only through the API. `SETTINGS_API_WRITABLE=false` turns off every configuration write. |
| Plugins | `agent/plugins/`, `/api/plugins*` | Manifest that bundles skills, MCP servers and hooks. Plugin hooks still need `HOOKS_ENABLED`. |
| Config import | `agent/migration/`, `/api/migration/*` | Imports MCP servers, skills, instructions and hooks from Claude Code, Cursor and Codex. Never overwrites existing entries. |
| MCP resources and status | `mcp/resources.py`, `McpHub.status()` | `list_mcp_resources` and `read_mcp_resource`. |
| Critical path | `session/critical_path.py` | Each trajectory turn gets an inference / tools / auto-review / user-wait / overhead breakdown. |
| Frontend | `SettingsDialog`, `features/changes/`, `features/runs/` (`ApprovalCard`, `AgentsPanel`, `JobsPanel`), stream reducers | Settings (features, security, integrations, memories, instructions), a Changes tab and chip, Agents and Jobs inspector tabs, approval cards, guardian badges, and background-job badges. |

Known limits:

- Sub-agents have no durable session log of their own. Their task, status and report are stored, but their individual model calls are not.
- Guardian reviews are not saved in the answer, so the badges are live-only.
- There is no `list_mcp_resource_templates` tool, and no MCP prompts, elicitation or OAuth yet.
- Plugin and memory changes are not hot-reloaded: MCP servers and tool registration take effect on restart.
- Keyring-backed secrets are not ported.

## Mat and Opt modes

The composer offers three modes. API callers choose one with `mode` on `POST /api/chat/stream`; a request without `mode` keeps the full toolset, as before modes existed.

| Mode | For | Tools |
| --- | --- | --- |
| **Mat** | Chatting and explaining: answers, derivations, proofs, calculations, figures | Everything except `write_file`, `edit_file`, `bash`, `bash_poll`, `data_agent` and the sub-agent tools. Python stays available for computing and plotting. |
| **Opt** | Working on the user's files, folders and code | The full toolset |
| **Auto** | Choosing Mat or Opt for each message | `app/agent/intent.py` `classify` |

How Auto decides:

- **Opt signals:** a verb that changes something aimed at files, folders or code; "my/this code"; a path, or the name of a file in the workspace; pasted code with "fix".
- **Mat signals:** questions, explanation requests and greetings.
- **When neither clearly wins:** it keeps the previous turn's mode, or chooses Mat when there is no previous turn. Mat is the safe choice, since it can still compute, read and plot.
- **Upgrading mid-turn:** an Auto turn that chose Mat can move itself to Opt with `switch_to_opt` when the work turns out to need file changes. A Mat turn the user picked never upgrades; the model suggests switching instead.
- **Enforcement:** tools outside the current mode are hidden from the model and also refused at the execution boundary (`TOOL_NOT_IN_MODE`).
- **Bilingual:** the classifier handles English and Chinese phrasing.

Events and storage: a public `mode` event carries `{mode, source: user|auto, reason, switched?}`, and the saved answer content carries `mode`.

Codex has the same split as user-chosen collaboration modes (chat vs agent, `collaboration-mode-templates`); the automatic choice is MatOpt's addition.
