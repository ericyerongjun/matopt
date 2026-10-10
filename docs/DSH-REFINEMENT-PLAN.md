# MatOpt × DeepSeek Harness × ZCode — refinement plan

Status: **implementation in progress.** The interrupted DeepSeek session was
recovered on 2026-09-30; see [the verified recovery checkpoint](DSH-RECOVERY.md)
for implemented features, validation, and the remaining roadmap. Detailed
phase descriptions below describe the original target rather than a completion
claim for every item.
Sources read: `/Users/eric/projects/matopt` (own codebase),
`/Users/eric/projects/deepseek-harness` @ checkout `dsh` developer preview
(session format v4 writer, released v3), and `/Users/eric/projects/ZCode`
@ v3.14.3.

This document has three parts:

1. **What dsh actually is** — a structural map, plus a shortlist of what is
   worth taking. dsh is the reference for the **session log, token
   accounting, and design tokens**.
2. **What ZCode's coding harness does** — a map of the parts MatOpt does not
   have at all: the tool contract, read-before-edit state, the tool
   scheduler, background tasks, and dynamic workflows.
3. **What MatOpt should do** — a phased, file-level plan drawing on both.

Nothing here proposes adopting either project's *stack*. MatOpt stays
Python/FastAPI + React/Vite. We borrow **design tokens, protocol vocabulary,
tool-contract discipline, and product mechanics**, and port them into
MatOpt's existing idioms.

The two upstreams are deliberately complementary and they rarely overlap:

| Concern | Take from | Why |
|---|---|---|
| Session log, surface ops, fork/repair | **dsh** | Mature, versioned, invariant-checked, format-migrated |
| Token/cost/context math, projections | **dsh** | Exact all-or-nothing folds, pressure/occupancy model |
| Design tokens, primitives, radius/elevation law | **dsh** | A written standard with tests enforcing it |
| Tool contract (metadata, safety flags, approval) | **ZCode** | Far richer than dsh's; safety flags drive scheduling |
| Read-before-edit freshness guard | **ZCode** | dsh has a policy seam; ZCode has the concrete mechanism |
| Tool scheduling (dependency graph, batching) | **ZCode** | dsh relies on the loop; ZCode schedules explicitly |
| Background tasks, dynamic workflows, cron/off-peak | **ZCode** | dsh has jobs; ZCode has a whole orchestration product |
| Permissions/approval broker with persisted rules | **ZCode** | dsh has approval; ZCode has rule evaluation + suggestions |
| Compaction summarisation prompt design | **dsh** | Cache-friendly prefix replay is a real trick |
| Multi-agent coordination | **ZCode** | Coordinator/worker + task output projection |

---

## Part 1 — Structure of DeepSeek Harness

### 1.1 Repository shape

```
deepseek-harness/
├─ packages/          ~350 packages in 54 groups (the product)
├─ apps/              cli · web · desktop · desktop-host (launchers)
├─ docs/              architecture, subsystem + package references
├─ python/            Python SDK wrapping `dsh --profile sdk`
├─ benchmarks/  native/  scripts/  vendor/  website/  snapshots/
├─ AGENTS.md          agent-facing working rules
└─ cordis / tsdown / vitest configs
```

Everything is a plugin on the [Cordis](https://github.com/cordiverse/cordis)
container: model adapters, the tool registry, the session log, and the agent
loop itself are all replaceable rows in a YAML composition. A running `dsh`
is a **profile** (ordered bundles) plus **patch overlays**.

### 1.2 Package groups that matter for MatOpt

| Group | Packages | Why it matters |
|---|---|---|
| `core/` | `session`, `agent`, `agent-loop`, `tools`, `system-prompt`, `scope` | The loop + the append-only log. The single most valuable idea in the repo. |
| `llm/` | `llm`, `llm-deepseek`, `llm-retry`, **`token-meter`** | Token/context accounting and retry-as-durable-attempt. |
| `session/` | `session-persistence-jsonl`, `session-format-v0..v4`, `session-projection`, **`session-stats`**, `session-title-*`, `session-turn-outline` | Durable format, projections, analytics fold. |
| `session-query/` | `session-query`, `session-query-sqlite`, `session-log-export`, `tool-session-query` | SQL analytics over session logs + a query tool for the agent. |
| `client/` | 63 packages: **`ui-theme`**, **`ui-primitives`**, `ui-conversation`, `ui-chat`, **`ui-trajectory`**, **`ui-deliverables`**, `ui-sidebar*`, `ui-settings*` | The Web UI. Design tokens + component catalog + two view targets. |
| `deliverables/` | `tool-present`, **`workspace-changes`** | Turn-scoped changed-file capture, numstat, unified diffs. |
| `compaction/` | `compaction`, `compaction-basic`, `compaction-tool-result-pruner`, `compaction-image-offload` | Pressure-triggered summarisation and result pruning. |
| `guard/` | `repeat-tool-reminder`, `timeout-policy` | The repeated-tool-failure guard MatOpt already hand-rolled. |
| `telemetry/` | `otel` | Session-log → OpenTelemetry. |
| `skill/` | `skill`, `skill-filesystem`, `tool-skill`, `skill-office` | Agent Skills — MatOpt already has this. |

### 1.3 Two ideas carry most of the value

**(a) The durable session log is the source of truth.**
`packages/core/session/src/types.ts:281` declares `SessionEventMap`. Message
history is *derived* from the log (`deriveMessages()`), never stored
alongside it. The stated invariant is **"model-visible means logged"** — a
runtime check asserts every model request is reconstructable from the log.
Everything downstream (replay, fork, transcripts, telemetry, timeline,
token accounting, cost) is a **fold over the log**.

Vocabulary (all durable, sequence-numbered, `{seq, time, type, data}`):

| Event | Payload |
|---|---|
| `turn/start` / `turn/end` | `{turn}` / `{turn, reason}` |
| `step/start` / `step/end` | `{turn, step}` |
| `system/message` | `{turn, step, message}` |
| `user/message` | `UserMessage` (with `source` distinguishing human / injected / goal) |
| `developer/message` | `{turn, step, message, headerSeq?}` |
| `assistant/message` | `{turn, step, message, stream, usage?, interrupted?}` |
| `assistant/attempt` | `{turn, step, stream}` — a failed/retried/cancelled attempt that produced **no** surface message |
| `tool/call` | `{turn, step, callId, name, arguments}` (raw JSON string) |
| `tool/result` | `{turn, step, message, error?, meta?}` |
| `request/header` | `{header, reason, startsSeries?}` — log-only request configuration |
| `request/context` | route/capacity/system-prompt-update metadata |
| `session/end-seed` | fork/inheritance marker |

Note `assistant/message` **embeds the exact compact timed stream**, and usage
travels with the message — there is deliberately no separate usage record.
Uninterrupted tool results keep call/result adjacency so positional pairing
never needs guesswork.

**(b) The Web UI is a token system plus one primitive catalog.**
`docs/web-styling.md` and `docs/ui-radius.md` are the authoritative rules:

- `ui-theme` owns every global token; **feature packages consume semantic
  aliases and define no global theme**.
- Component styles are **CSS Modules** beside each component; `clsx` for
  composition. *No component library, no Tailwind* (`web-styling.md:17`).
- Feature CSS uses `--dsw-alias-*` only. Literal palette values are banned
  there (`web-styling.md:19`).
- `ui-primitives` is **the only channel that crosses feature packages** — a
  deliberate visual difference belongs in a prop there, not in a second copy.
- Radius is chosen by component *role and size*, not by content
  (`ui-radius.md:30`):

  | Role | Height | Radius | Token |
  |---|---|---|---|
  | Small detail, inline code | <20px | 4 | `--dsw-radius-xs` |
  | Compact control / icon button | 20–28 | 8 | `--dsw-radius-sm` |
  | Standard control, nav row | 32–40 | 12 | `--dsw-radius-md` |
  | Large control, multiline cell | — | 16 | `--dsw-radius-lg` |
  | Independent content card | — | 20 | `--dsw-radius-xl` |
  | Composer, dialog, main panel | — | 28 | `--dsw-radius-panel` |

- **Elevation**: elevated surfaces set `border: 0` and take the shadow, whose
  first layer is a 0.5px hairline. Never pair an alias border *and* an
  elevation shadow.
- Neutral flat borders are **0.5px**. State-colored borders stay 1px.
- Nested geometry: `inner R = max(0, outer R − inset)`.
- `corner-shape: superellipse(1.5)` under `@supports`, with every full-round
  radius explicitly paired with `corner-shape: round`.
- Focus rings, scrollbar skins, and reduced-motion behaviour are
  theme-owned; components do not restyle them.

Token tiers in `ui-theme/src/styles/`:

- `design-platform.css` — `--dsw-static-*` palette (neutral, neutral-bluish,
  deepseek, blue, green, red, amber) and `--dsw-alias-*` semantic aliases
  (`bg-base`, `bg-layer-1..3`, `border-l1..l4`, `label-primary/secondary/
  tertiary/caption`, `link`, `interactive-bg-hover`, `state-*`,
  `markdown-*`, `scrollbar-*`, `specific-sidebar-*`), with a full dark
  override on `body[data-ds-dark-theme]`.
- `base.css` — font stacks, motion curve, the 6-step radius scale.
- `gradient-shadow-text.css` — elevation trio (`--dsw-elevation-panel`,
  `-prominent`, `-soft`), markdown typography ladder, content font delta.
- `corner-shape.css`, `focus.css`, `scrollbar.css` — global behaviours.

### 1.4 Trajectory, reporting, and token accounting

> Paired reference: [`docs/reference/dsh-trajectory-telemetry-map.md`](reference/dsh-trajectory-telemetry-map.md)
> is a 1151-line, line-cited map of the session log, event vocabulary,
> on-disk format, fork/resume, projections, compaction, telemetry, and the
> end-to-end token data flow. Read it before writing any of phases 2–6.

**`ui-trajectory`** is a second *view target* over the same conversation
assembly as Chat. `README.md` describes it as "a turn-aware event ledger with
an interactive timing overview":

- groups User / Assistant / Tool / nested Subtool / compaction records, marks
  turn and step boundaries;
- a record **inspector** for token usage, duration, Input, Output, Timing,
  images, attachments;
- `AssistantTiming` = `{stepStartTime, firstTokenTime, completedTime}`
  (`ui-conversation/src/client/contract/records.ts:51`) ⇒ TTFT and
  throughput;
- a fixed timing overview (Assistant spans split TTFT vs decode), drag to
  range-filter the ledger, wheel to zoom;
- virtual rows: an anchored 50-node tail window that extends without evicting
  its prefix, older pages loaded on demand, streaming follows the tail until
  you scroll up.

**`token-meter`** (`packages/llm/token-meter/`) is the reporting core:

- `deriveTurnTokenUsage(events)` folds one complete turn into exact
  `{uncachedInputTokens, outputTokens, totalTokens, cacheReadTokens?,
  cacheWriteTokens?, reasoningTokens?, routes?}`. Optional buckets appear
  **only when every participating attempt reported them**; any missing
  lifecycle boundary, unsafe count, or contradictory total returns
  `undefined` rather than a wrong number (`src/turn-usage.ts:178`).
- `ctx.tokenMeter.measure(session)` → `{totalTokens, surfaceTokens, nodes}`;
  projections `tokenUsage`, `contextPressure` (`pressureTokens`,
  `projectedTokens`, `contextWindow`), `contextBreakdown`
  (`systemTokens`, `toolsTokens`, `messageTokens`).
- Fixed heuristic: **4 characters ≈ 1 token** plus per-block/role overhead.
  Provider usage is reused only for an *identical canonical request
  envelope*, and never below the heuristic anchor.

**`session-stats`** folds the whole log into the analytics record
(`src/types.ts:22`):

```
turns, steps, llmMs, toolMs,
ttftMs, ttftSteps,        // first-token latency
decodeMs, decodeTokens    // ⇒ throughput
```

**`workspace-changes`** captures turn-start/turn-end snapshots (git tree ids
or whole-file captures), produces `WorkspaceChangedFile[]`
(`{path, display, added, deleted, binary?, oversized?}`), a capped summary
with totals, and on-demand unified diffs with three context lines. `tool-present`
is the explicit "here is your deliverable" tool, rendered as a file card.

**`compaction`** is pressure-triggered, hides superseded entries *without
deleting them*, and is itself logged — so replay is faithful and the UI can
show "compacted here".

### 1.5 What is *not* worth taking

- Cordis, the plugin/profile/bundle system, HMR, the Electron desktop host.
- The five-generation session-format migration chain — MatOpt needs one
  version and a documented upgrade path, not a codec matrix.
- `session-projection-cache` checkpointing, `spill`, `ptc-runtime`,
  subagent providers, ACP/SDK profiles.
- CSS Modules-vs-plain-CSS is a *choice* (see §3.2 step 3); the *token
  discipline* is the part that matters.

---

## Part 2 — ZCode: the coding harness

ZCode v3.14.3 is an AI coding workspace: Electron desktop, browser, TUI, and
a CLI distribution, all sharing one agent runtime. ~1,200 TS files in the
agent alone. It is a *different product* from MatOpt — a code-editing agent,
not a maths/statistics workbench — so the value is concentrated in the
**harness machinery**, not the features.

### 2.1 Where the harness lives

```
ZCode/
├─ apps/zcode-cli/packages/          the agent — this is what matters
│  ├─ core/src/            ~500 files
│  │  ├─ tool/             176 files  tool contract, handlers, executor, scheduler
│  │  ├─ runtime/          164 files  the agent loop, turn/step, permission flows
│  │  ├─ workflow/          31 files  dynamic-workflow glue
│  │  ├─ subagent/          22 files  subagent definitions and control
│  │  ├─ hooks/             19 files  hook points
│  │  ├─ context/ compact/ memory/ session-context/ system-reminder/
│  │  └─ permission/ mcp/ repl/ telemetry/
│  ├─ contracts/           113 files  the shared type vocabulary (ports, schemas)
│  ├─ dynamic-workflow/     94 files  the workflow scripting product
│  ├─ dynamic-workflow-runtime/  5    the workflow sandbox
│  ├─ adapters/ bootstrap/ cli/ tui/ debug/ i18n/ telemetry/
├─ packages/ui, web, client, services, server, rpc, shared
└─ config/, DESIGN.md, CONTEXT.md, AGENTS.md, architecture-policy.yaml
```

`DESIGN.md` (537 lines) is ZCode's design standard — **Tailwind-based**, with a
mandatory `text-ui-*` scale driven by a single `--ui-font-size` variable, and
a radius law based on *container nesting depth* rather than component
importance. That is a genuinely different answer to the same question dsh
answers with a fixed role→radius table (§1.3). Worth reading both before
deciding MatOpt's rule; see §3.2 for the recommendation.

### 2.2 The tool contract — the single most valuable thing here

MatOpt's `Tool` (`tools/base.py:42`) exposes essentially `name`,
`description`, `parameters`, and `read_only`. ZCode's `ToolMetadata`
(`core/src/tool/types.ts:68`) declares:

```ts
export interface ToolMetadata {
  name: string;
  description?: string;
  modelInstructions?: readonly string[];
  allowedInPlanMode?: boolean;
  readOnly: boolean;
  destructive: boolean;
  concurrentSafe: boolean;      // ← drives the scheduler
  requiresUserInteraction?: boolean;
  timeoutMs?: number;
  maxOutputBytes?: number;
  sideEffectScope: ModelToolSideEffectScope;
  riskLevel: RiskLevel;
  needsApproval: boolean;
  providerVisible?: boolean;
  stopTurnOnSuccess?: boolean;  // ← tool declares it can end the turn
  mcpPresentation?: { serverName: string; toolName: string; /* … */ };
}
```

Four of these are load-bearing in ways MatOpt has no equivalent for:

- **`readOnly` / `destructive` / `concurrentSafe` / `sideEffectScope` are
  safety facts, not documentation.** They feed the scheduler directly
  (`apps/zcode-cli/packages/core/src/tool/scheduler.ts:85-103`), so
  parallelism is a *declared property* rather than a heuristic. Compare
  MatOpt's rule at `harness.py:634-635`:
  `parallel = len(parsed) > 1 and len(writes) <= 1`. ZCode's is strictly
  better and is described in §2.3.
- **`stopTurnOnSuccess`** lets a tool end the turn by *declaring* it, instead
  of the loop special-casing a tool name. MatOpt's `submit_result`-equivalent
  behaviour would otherwise be a name check somewhere in `harness.py`.
- **`providerVisible`** — a tool can exist, be callable, and still be hidden
  from the model's schema list. MatOpt approximates this by mutating the
  schema list per step (`current_schemas()`, `harness.py:491`).
- **`riskLevel` + `needsApproval` + `requiresUserInteraction`** are separate
  axes. MatOpt has a single `align_monitor` boolean heuristic
  (`monitor.py:41`).

### 2.3 The tool scheduler — a dependency graph, not a heuristic

`core/src/tool/scheduler.ts` is 245 lines and does something MatOpt does not
attempt at all:

1. Each call becomes a `ToolDependency` node carrying its safety metadata and
   an explicit `dependsOn` list.
2. `topologicalSort` orders them; a cycle is a hard, non-recoverable error
   (`scheduler.ts:138-149`).
3. `groupByParallel` computes a dependency *level* per node, then packs each
   level into parallel groups — **flushing the group whenever a
   non-parallel-safe item appears, and capping at `maxConcurrency` (10)**
   (`scheduler.ts:154-202`).
4. `canRunInParallel` refuses `destructive` outright, honours an explicit
   `concurrentSafe`, falls back to `readOnly`, and finally to
   `sideEffectScope === 'none'` (`scheduler.ts:85-103`).
5. The result is a `ToolSchedule` of `parallelGroups` plus a flat
   `executionOrder`, and the batch emits `batch_start` / `tool_start` /
   `tool_complete` / `batch_complete` events
   (`tool/types.ts:481-490`) — so the UI can show the parallel structure
   rather than a flat list of results.

MatOpt executes calls either all-parallel or strictly sequentially with no
dependency awareness and no concurrency cap. Porting this is a
self-contained ~150-line change to `harness.py::_execute_calls` plus a
metadata field on `Tool`.

### 2.4 Read-before-edit freshness — a real guard, not a policy note

ZCode tracks a `ReadFileStateMap` per session
(`tool/types.ts:206-219`):

```ts
export interface ReadFileStateEntry {
  path: string;
  content: string;
  offset?: number; limit?: number;
  isPartialView: boolean;
  readAt: Date;
  sourceTool?: PersistedReadFileStateTool;
  revisionId?: string;
  mtimeMs?: number;
  sizeBytes?: number;
}
```

The mechanism, which MatOpt lacks entirely:

- `Write`, `Edit`, and even `CreateWorkflow`/`AmendWorkflow` record state, not
  just `Read` (`tool/read-file-state-metadata.ts:11-17`) — because an inline
  draft's bytes *are* a full view of that path.
- Edits are validated against **`mtimeMs` + `sizeBytes` + `revisionId`**, not
  just presence. `createReadFileStateMetadataFromEntry` **refuses to emit
  metadata at all** when freshness fields are missing, with the explicit
  rationale that "a historical file state without freshness metadata must not
  masquerade as read, or it would let a stale write through"
  (`…/tool/read-file-state-metadata.ts:87-92`).
- The state is *persisted into the tool result* and re-parsed on resume
  (`parseReadFileStateMetadata`), so the guard survives a restart — the
  session can be resumed and a stale edit is still caught.
- `bash` reads also feed it (`handlers/bash-read-file-sources.ts`,
  `handlers/bash-read-file-state.ts`), so `cat file` counts as reading it.
- `findLatestReadFileState` deliberately resolves to the **latest** read of a
  path rather than preferring a full view over a ranged one
  (`read-file-state.ts:46-49`) — the comment explains that preferring the full
  read caused persistent false "stale" reports after Bash or a formatter
  touched the file.

MatOpt has **none** of this. Its `edit_file` does a unique-match check
(`file_tools.py`) but a stale edit against a file the model never read, or
read three turns ago before the agent's own `python` tool rewrote it, is
undetectable. This is the highest-value single port in the whole ZCode
codebase for a tool-using agent.

### 2.5 Path policy is deliberately permissive — and that is a decision

`tool/path-policy.ts:33-37` does not block paths outside the workspace root,
with an explicit rationale:

> Current release intentionally does not hard-block paths outside
> workspaceRoot. Cause: subagents may need to inspect user-requested sibling
> repos or external files before the filesystem permission adapter grows
> explicit ask/deny rules for them.

MatOpt's `Workspace.resolve()` does the opposite: it is the single
path-traversal guard and raises on escape (`workspace.py:90-103`). **For
MatOpt that is the right call** and should not be relaxed — a maths sandbox
has no sibling-repo workflow. But the *reason* ZCode can be permissive is
that it has a permission broker to catch what containment would have caught
(§2.6). Record this as an explicit non-port.

### 2.6 Input normalization before approval — the anti-TOCTOU trick

`ToolEntry.resolveInput` (`tool/types.ts:313-330`) normalizes the model's raw
input into "the execution facts that are about to happen", and the doc comment
explains why its *position* is the entire point:

> executor calls it after `validateInput` and before the `PreToolUse` hook;
> the return value replaces `executionInput` directly. … From then on, hooks,
> project permission rules, permission event payloads, `prepareApproval`, and
> the handler all read the **same normalized input** — so three things happen
> at once: policy cannot be bypassed…, it is visible across versions…, and
> **approval and execution are byte-identical**: parse once, carry those bytes
> all the way to the handler, so there is no "approve A, run B".

This is a genuinely important security property that is easy to get wrong and
cheap to copy: normalize once, then let every later consumer read the same
normalized value. MatOpt's `_parse_arguments` (`harness.py:718`) parses the
raw JSON, but the parsed dict is what the monitor sees and what the tool gets,
with no persistence of the normalized form and no guard against a later stage
re-reading the raw string.

### 2.7 What else is here, pending detail

The remaining ZCode subsystems are mapped by the reference reports (Part 5). The
headline items, to be filled in with file-level detail:

- **Background tasks** — registry, buffered output, "poll/replay to the
  model", and stop semantics (`executor/background-task*.ts`,
  `handlers/task-output.ts`, `handlers/task-stop.ts`). MatOpt has fire-and-
  forget subprocesses and no way to background a long kernel run.
- **Dynamic workflows** — a scripting product with create/run/observe/amend/
  save/resume, a sandbox boundary, fan-out/fan-in, and a graph model with
  introspection. This is the most ambitious thing in either upstream and is
  the natural model for MatOpt's "run a parameter sweep / cross-validation
  grid" case.

  **Note: "workflow" in ZCode is two unrelated subsystems**, and they only
  look alike at the tool layer because both feed the same background-task
  tracker (`tool/executor/background-task-registry.ts:203-216`):

  1. **Static / expert workflow** (`core/src/workflow/`, 31 files, ~5.2k
     lines) — a durable long-task engine with a *fixed eight-phase pipeline*
     (clarify → task_analysis → arch_decompose → env_setup → meta_prompt →
     exec → final_critic → complete; `definition.ts:39-48`), a pure snapshot
     reducer for lifecycle (`lifecycle.ts`), a dependency-DAG scheduler, and
     an expert planner→execute→critic loop. Runtime task type
     `local_workflow`; **not cancellable**.
  2. **Dynamic workflow** (`packages/dynamic-workflow/` +
     `dynamic-workflow-runtime/`) — the model *authors a TypeScript script*.
     That script is typechecked against an embedded facade `.d.ts`, analysed
     into a site graph, then **lowered**: types stripped and every facade call
     instrumented with a static site id onto `__host`. The lowered script runs
     in a `vm.createContext` realm inside a spawned child process, and
     `__host.*` is bridged over NDJSON to a **pure, clock-free, journal-
     replaying `WorkflowEngine`**. Runtime task type `local_dynamic_workflow`,
     cancellable via `DynamicWorkflowRunPort.cancel`, with
     `backgroundTaskId ≡ runId`.

     The **journal-replaying, clock-free engine** is the transferable idea
     here, independent of the sandboxing: it makes a long fan-out computation
     deterministic and resumable, so a crashed sweep resumes instead of
     restarting. For MatOpt's parameter sweeps and cross-validation grids that
     is worth more than the scripting surface itself.
- **Subagents** — a coordinator/worker pattern with `send-message`,
  `respond-to-coordinator`, and task output projection.
- **Permissions** — rule types, persistence, evaluation, and *suggested*
  rule updates from a denied call.
- **System reminders** — injected reminder events.
- **Memory** — project memory files.
- **Compaction** — ZCode's own algorithm, to compare against dsh's.
- **Skills** — how ZCode loads and gates them, including a workflow-skill
  gate that answers "is this session currently holding skill X" *from
  provider-visible history*, so the answer correctly becomes "no" after
  compaction (`tool/types.ts:259-264` — a subtle correctness point MatOpt's
  `ctx.state["skills"]` gets wrong, since it survives compaction).
- **Hooks** — hook points and configuration.

### 2.8 What is *not* worth taking from ZCode

- **The Tailwind v4 + Radix stack.** MatOpt's plan commits to dsh's plain-CSS
  token system (§3.2). Two things make this the right call beyond
  consistency: ZCode has no `tailwind.config.*` at all — config and entry are
  the same **2,053-line `styles.css`**, which is a large single point of
  contention; and its light/dark themes are expressed as four separate
  `@theme`/`.theme-zai-*` blocks with the active pair duplicating the
  foundation. dsh's three-tier static→alias structure is easier to keep
  coherent at MatOpt's size.
- The Electron desktop shell, the plugin marketplace, the CDN/sha256 install
  flow, the RPC framework, and the TUI.
- The container-nesting radius law — it is self-consistent but it makes a
  component's radius depend on its *call site*, which is hostile to a shared
  primitive library. dsh's role-based table is the better fit for MatOpt.
- `node-repl`, `read-video`, `browser-use`, the iOS/Android emulator plugins.
- The permissive path policy (§2.5).

**But port this one thing from ZCode's type system, because it is better than
what dsh has.** dsh solves interface font scaling with a fixed ladder of 13
`--dsw-font-*` roles plus a `--dsh-content-font-size` axis that only *content*
follows — so headings, markdown and body copy scale while chrome does not.
ZCode solves it with **one scalar**: every interface size is
`calc(var(--ui-font-size) ± Npx)` over a 7-step ramp (`text-ui-xl` = +4px
through `text-ui-2xs` = −5px), and the runtime mutates **only** that variable:

```ts
// packages/ui/src/lib/uiFontSize.ts
export function applyUiFontSizePx(fontSizePx: number): void {
  // 只更新 UI 字号 Token 的基准变量，避免根 font-size 连带缩放图标、间距和圆角。
  rootStyle.setProperty("--ui-font-size", `${normalizeUiFontSizePx(fontSizePx)}px`);
}
```

with the rule stated as a defect class rather than a preference: "Never
implement interface font scaling by changing `html` or
`document.documentElement.style.fontSize`; update only `--ui-font-size`." The
payoff is that **icons, spacing and radii do not drift** when the user changes
text size, which is exactly the failure mode of `rem`-based scaling. The ramp
is clamped 12–20px, defaults to 14, and is enforced tightly enough that a
restricted `text-ui-2xs` appears **once in 1,555 files** — in timeline axis
furniture, which is precisely where the rule permits it.

Adopt that mechanism on top of dsh's palette: `--mo-ui-font-size` plus a
7-step `--mo-text-ui-*` ramp for MatOpt's interface text, alongside dsh's
markdown ladder for prose. The two are not in conflict — one governs
*chrome*, the other governs *reading*.

---

## Part 3 — What MatOpt should do

### 3.0 How to migrate, not rewrite

**The single most important correction to an earlier draft of this plan: most
of this should be *moved*, not *reimplemented*.** MatOpt already has the
pattern — `backend/lambda_app/` is a vendored upstream with an `UPSTREAM.md`
recording the commit pin and a `scripts/vendor_lambda_backend.py` refresh
script, and `skills/anthropic/` is vendored with `THIRD_PARTY_NOTICES.md`.
Every phase below follows that convention. Rewriting working upstream code by
hand is slower, loses the comments that explain *why* a constant is what it
is, and silently drops the edge cases that upstream learned the hard way.

Two upstream commits are the pins:

| Upstream | Commit | License |
|---|---|---|
| deepseek-harness (`dsh`) | `639ed015397290b3745d163aafe02ffee4aa3f84` | MIT |
| ZCode | `29628c9acdb81b703bbd4080c207a0e7ce5e276e` (v3.14.3) | Apache-2.0 |

Neither repo has a `LICENSE`/`NOTICE` in MatOpt today. Apache-2.0 requires
attribution **and** a `NOTICE` file if the upstream ships one — ZCode has
`NOTICE.md` (27 KB), so MatOpt needs a root `NOTICE` and a
`THIRD_PARTY_NOTICES.md` covering both, before the first vendored byte lands.

#### 3.0.1 Portability classes

Every artifact the plan wants falls into exactly one class. The class
determines the method, and getting it wrong is what causes wasted effort.

**Class A — copy verbatim, zero edits, no adaptation.**
*Any file:copy, no code understanding needed.*

| Artifact | Source | Size |
|---|---|---|
| dsh code-font stack | `ui-theme/src/styles/base.css` (first 12 lines) | 12 lines |
| dsh markdown type ladder | `ui-theme/src/styles/gradient-shadow-text.css:47-271` | ~225 lines |
| dsh `shiki.css` syntax palette | `ui-theme/src/styles/shiki.css` | 31 lines |
| dsh `brand-font.css` + 3 `.woff2` | `ui-theme/src/styles/brand-font.css` + `*.woff2` | 24 lines + 186 KB |

**Class B — copy verbatim then one mechanical rename.**
*Paired `cp` + `sed`. No file needs to be understood to migrate it.*

This is the big one, and it is the whole Phase 1 design system:

| Artifact | Source | Mechanical transform |
|---|---|---|
| dsh theme sheets (7 files) | `packages/client/ui-theme/src/styles/*.css` | `--dsw-` → `--mo-`, `--dsh-` → `--mo-`, `--ds-` → `--mo-` |
| dsh `body[data-ds-dark-theme]` | same | → `body[data-mo-dark-theme]` |
| dsh font `url(./*.woff2)` | `brand-font.css` | unchanged — relative paths survive |

**Verified before writing this:** the dsh theme sheets contain **no `@import`,
no `@plugin`, no `@source`, and no dependencies** apart from three relative
`.woff2` paths. They are standalone CSS. That makes all ~880 lines of
`base.css` + `design-platform.css` + `gradient-shadow-text.css` +
`corner-shape.css` + `focus.css` + `scrollbar.css` a `cp` plus a `sed`, and it
is the reason Phase 1 should be measured in hours rather than days.

**Class C — copy with mechanical substitutions, then verify by test.**
*Same-language, but a dependency or an ambient API must be supplied.*

> **Correction — the primitives are far more portable than an earlier draft
> claimed.** That draft said the `.tsx` files "each need a real look" and that
> only the CSS was mechanically portable. Verified against the pin, that is
> wrong: `packages/client/ui-primitives/package.json` declares **no runtime
> `dependencies` at all** (only an unused `@deepseek-ai/cordis` peer), and
> `Button.tsx` imports exactly three things — `react`, `clsx`, and its own
> `.module.css`. A scan of all 34 `*.module.css`/`.tsx` pairs found **31 fully
> portable** and only 3 entangled with Cordis: `PathLabel.tsx`,
> `TextShimmer.tsx`, `Tooltip.tsx`.
>
> So this is a **direct copy of 31 primitives**, not a reimplementation. The
> entire cost is two additions to MatOpt:
>
> - **`clsx`** in `frontend/package.json` — the only missing runtime dep.
> - **CSS Modules** — Vite already supports `.module.css` with **zero config**,
>   so this is a convention decision, not a build change. It also satisfies
>   `ui-radius.md`'s nested-geometry rules, which global class names cannot
>   express (§3.2 step 3 already proposes adopting them).
>
> Two mechanical transforms when copying: the files reference `--dsw-*` token
> names, which the vendored sheets renamed to `--mo-*` (a `sed` handles it),
> and dsh's locale-as-props pattern means user-facing strings need MatOpt's
> own text rather than a missing `t()`. Take `icons/` too (hand-authored SVG,
> 1658 lines); do **not** take `markdown/` — §3.2 already decides against it
> because MatOpt's KaTeX path is better for maths.
>
> The 3 entangled files are worth taking but need the Cordis hook replaced
> with React: `Tooltip.tsx` → a small portal/positioning hook,
> `TextShimmer.tsx` → props instead of context, `PathLabel.tsx` → inline the
> path helpers. Note `clsx` is genuinely needed; `tailwind-merge` is not
> (MatOpt has no Tailwind).

**Class D — transliterate mechanically; do not rewrite from memory.**
*Language barrier (TS → Python), so each port is a line-by-line translation of
a file that already exists, preserving identifiers, constants and comments.*

These are the harness upgrades. The rule is: **port the file, not the idea.**

> **Attribution correction.** An earlier draft of this table attributed the
> tool scheduler and the read-state helpers to dsh. **They are ZCode's.** No
> `tool/scheduler.ts`, `tool/read-file-state.ts`, `READ_ONLY_TOOLS`, or
> `DEFAULT_MAX_CONCURRENCY` exists anywhere in dsh at the pin — verified by
> `find` and `grep`. Both files live in ZCode. The table below is corrected,
> and the upstream paths are given in full so this cannot drift again.

| Artifact | Upstream | Source path | Notes |
|---|---|---|---|
| Tool scheduler | **ZCode** | `apps/zcode-cli/packages/core/src/tool/scheduler.ts` (245 lines) | Pure algorithm; `READ_ONLY_TOOLS` set and `DEFAULT_MAX_CONCURRENCY = 10` come across unchanged |
| Read-state helpers | **ZCode** | `apps/zcode-cli/packages/core/src/tool/read-file-state.ts` + `read-file-state-metadata.ts` (~330 lines) | Mostly pure; the metadata module is a hand-written validator, so it is a direct translation |
| Edit matcher | **ZCode** | `apps/zcode-cli/packages/core/src/tool/edit-matchers.ts` (13,284 B) | 8 strategies plus the broad-matcher rule |
| Freshness predicate | **ZCode** | `apps/zcode-cli/packages/core/src/tool/handlers/edit.ts:439-469` | Advanced-mtime comparison; strict-full-read escape hatch |
| Read-only command classifier | **ZCode** | `apps/zcode-cli/packages/core/src/tool/handlers/bash-readonly-policy-*.ts` (~15 files) | Mainly allowlist **data**; transliterate the tables verbatim |
| Turn-usage fold | **dsh** | `packages/llm/token-meter/src/turn-usage.ts` (274 lines) | **A state machine — transliterate literally; every `invalid = true` branch is load-bearing** |
| Token estimator | **dsh** | `packages/llm/token-meter/src/estimate.ts` | Three constants and a few branches |
| Crash repair | **dsh** | `packages/core/session/src/repair.ts` | Deterministic id formats and reused timestamps must match exactly |
| Tool-call scheduling | **dsh** | `packages/core/agent-loop/src/tool-calls.ts` (290 lines) | The *alternative* concurrency model — see §3.8b |

For the classifier and matcher work, the useful insight is that the *hard*
part is data, not logic: the read-only classifier's value is in its allowlist
tables (47 any-arg-safe commands, 40 safe env-assignment names, per-command
flag tables with arities, the `FIND_WRITE_OPTIONS` and dangerous-git-global-flag
lists). Transliterate the tables verbatim; only the ~200 lines of evaluation
logic need Python-shaped thinking.

**Class E — reimplement only where nothing exists upstream.**
Genuinely new work, and the list is short:

- MatOpt's **cost table** — dsh has no monetary cost model at all (§3.5).
- The **`Chat | Trajectory | Report` shell** wiring — the view targets are
  MatOpt's own.
- The **trajectory row model** for MatOpt's part shapes — dsh's `ConversationNode`
  union is the template, but the payloads differ.
- The **container per conversation** — neither upstream ships one (§3.9).
- The **report generator** — dsh has no report artifact.

#### 3.0.2 Migration scaffold

Commit `vendor/UPSTREAM.md` and a refresh script *before* the first vendored
file, mirroring `backend/lambda/UPSTREAM.md`:

```
vendor/
  UPSTREAM.md              both pins, licence table, what came from where
  dsh-ui/                  Class A + B — copied theme sheets + fonts
  zcode/                   Class C/D — matcher + classifier tables
scripts/
  vendor_dsh_ui.py         cp + sed rename, idempotent, --check mode
  vendor_zcode_tables.py   extracts the allowlist tables
  vendor_check.sh          fails if a vendored file drifted from its pin
```

The `--check` mode matters: it is what lets MatOpt take an upstream bugfix by
re-running the script and diffing, instead of hand-merging. ZCode's own
`generate-bash-command-registry.mjs` uses exactly this pattern with a sha256
pin and a `--check` CI gate — copy that discipline, not just the data.

**Per-file provenance headers.** Every vendored file gets a two-line header
naming the upstream path and commit, so a reader can find the original:

```css
/* vendored from deepseek-harness @ 639ed015
 * packages/client/ui-theme/src/styles/design-platform.css
 * MIT. Renamed --dsw-* → --mo-*. Regenerate: scripts/vendor_dsh_ui.py */
```

#### 3.0.3 What this changes about effort

The earlier draft of this plan estimated Phase 1 at "~1400 LOC of frontend
work". Reclassified, roughly **880 lines of it are Class B** (`cp` + `sed`),
and the genuinely authored work is the ~12 React wrappers over ported CSS, the
`--mo-ui-font-size` scalar (§2.8), and the component-by-component migration
onto the new tokens. Phase 1 drops from weeks to days.

Phase 7's scheduler and read-state guard (§3.8) are Class D — real translation work,
but of files that already exist and are already correct, which is a very
different task from designing them.

### 3.1 Baseline: where MatOpt stands today

Verified by reading the code:

| Area | MatOpt today | File |
|---|---|---|
| Loop | `AgentHarness._run_inner` → `_agent_loop`: step loop, steam, parallel tool batch, verify/revise | `backend/app/agent/harness.py` |
| State | SQLite `conversations` + `messages(role, content JSON)`; assistant content is an ordered `parts[]` | `backend/app/storage/db.py` |
| Replay | `_replay()` re-renders `parts[]` → LLM messages, char-budget trimming | `harness.py:459` |
| Events | 24 in-memory SSE types; **not durable** | `backend/app/agent/events.py` |
| Usage | `usage` deltas summed into `state.usage`, stored on the assistant message; `cached_tokens`/`reasoning_tokens` captured by the adapter | `openai_compat.py:105` |
| Design | OpenAI/ChatGPT palette, plain CSS, **light-only**, radius scale `6/10/24/9999px` | `frontend/src/styles/tokens.css` |
| Stack | React 19.2 + Vite 6, `lucide-react`, KaTeX, react-markdown, plotly, chart.js | `frontend/package.json` |
| Tests | 1031 LOC pytest over harness/tools/skills/api/lambda; 5 frontend `.test.ts` (~297 LOC, pure utils only) | `backend/tests/`, `frontend/src/**/*.test.ts` |

Frontend facts that shape the plan (from a full read of `frontend/src`):

- **Single-view app.** `App.tsx` renders `ChatPage` unconditionally.
  `react-router-dom@^7.13.0` is a dependency but **imported nowhere**;
  `pages/DocumentPage.tsx` and `pages/SettingsPage.tsx` are 17-line unmounted
  TODO stubs.
- **Plain global CSS, 16 side-effect-imported sheets** with hand-rolled BEM.
  No CSS Modules, no PostCSS/Tailwind config.
- **The token layer is disciplined in intent, leaky in practice.** 73 custom
  properties all in one flat `:root` (`tokens.css:8`); `AnalysisPanel.css`
  alone carries 19 literal hex/rgba values and defines its own private
  `--pane-bg: #f4f4f5`, giving the app three accidental greys. `--color-ash`
  and `--color-sidebar-mist` are the same value; `--color-edge` ==
  `--color-hairline` == `--border-light`. 15 tokens are defined and never
  referenced. `--radius-lg: 24px` is referenced nowhere while raw `28/22/16/14/12px`
  radii are inlined. **There are no spacing, typography, elevation, motion, or
  z-index scales** (z-index values 1/5/8/20/30/40/50/100 are scattered across
  six files).
- **No `:focus-visible` rule anywhere.** No `::selection`, no reduced-motion
  guard, no app-level error boundary.
- **No primitive layer.** Four copy-pasted dropdowns:
  `ChatPage.tsx:143-211`, `ModelPicker.tsx:40-47`,
  `SkillCommandMenu.tsx:87-96`, `ExportButton.tsx:19-45`.
- **The thread deliberately deletes content.** `MessageList.tsx:87-92` skips
  `monitor`, `phase`, `tool_call` and `verification` parts; `SILENT_TOOLS` /
  `CODE_TOOLS` suppress tool cards; `isCommentaryText` hides preamble prose;
  reasoning collapses to a static "Thought" label with no transcript. Tool
  activity becomes one `Work · N actions` chip.
- **`Usage` is accumulated but never displayed.** No tokens, cost, timestamp,
  regenerate, edit, branch, or scroll-to-bottom affordance.
- **`services/stream.ts`'s 22-event reducer is pure and completely untested** —
  the highest-value missing test target in the repo, and `StreamEvent` is an
  untyped `{type: string; [key: string]: unknown}` bag with no exhaustiveness
  checking.
- **Dead surface area:** `ReasoningSlider`, `ExportButton`, `FileUploader`,
  `InlineMathText`, the default export of `ToolCallCard`, hooks `useDocument`
  and `useExport`, and `services/ocr.ts` + `services/document.ts` which call
  `/api/ocr` and `/api/documents/parse` — **routes that do not exist on the
  backend**. `services/suggestions.ts` targets a real route
  (`backend/app/api/suggestions.py:29`) that nothing ever calls, leaving
  `WelcomeScreen` bare.
- Theme-locked subsystems beyond CSS: `mermaid.initialize({theme:"default"})`
  (`MarkdownRenderer.tsx:27`), `PlotRenderer.DEFAULT_LAYOUT`'s hardcoded light
  hex + visible modebar (`PlotRenderer.tsx:21-38`), `ChartRenderer` with no
  colorway, and `react-syntax-highlighter`'s implicit Prism theme.

Concrete gaps this plan closes:

1. **No durable event log.** `Event`s are in-flight queue items only
   (`harness.py:198`); `Event.to_sse` emits `data: …\n\n` with **no `id:` and
   no `retry:`**, so there is no `Last-Event-ID` resumption. A dropped client
   loses the whole in-flight stream, and mid-turn there is nothing to read —
   the assistant message does not exist in SQLite until the turn's `finally`.
   The only persisted trace is the aggregate `parts[]` written once per turn.
2. **No trajectory.** Tool traces/plan/monitor chips are crammed into a
   compact "Work" rail (`AnalysisPanel`), with no timeline, no per-record
   inspector, no timing.
3. **Usage is a running sum, not a record.** `state.usage[k] += v`
   (`harness.py:573`) sums *every* LLM call in the turn — solver, both verify
   rounds and both revise rounds share one `state` object. Per-call usage is
   emitted live and **never persisted**, so solver-vs-verifier cost is
   unrecoverable. There is no pricing anywhere. Side-task calls
   (`_auto_title`, `/api/followups`, `/api/suggestions`) go through
   `complete_text`, which returns only the text and **discards `resp.usage`** —
   every conversation silently bills an unmetered title call. The SDK's own
   `max_retries=2` (`openai_compat.py:27`) and `_stream_llm`'s transient retry
   both discard what the failed attempt consumed.
4. **Compaction is a character budget** (`history_char_budget = 240_000`),
   not pressure against the model's real context window. `ModelSpec.context_window`
   exists (`catalog.py:30`) and is **never consulted**. Trimming runs only at
   turn start, never between steps, and is lossy with no trace.
5. **The system prompt is unrecoverable.** It is rebuilt fresh every turn
   (`harness.py:280` via `_system_prompt`, `:333`) from workspace files,
   dataset cards, the tool list, and the *currently active* skills — so no
   historical record says which skills were active or what the model actually
   read. This is the single sharpest violation of "model-visible means
   logged".
6. **Steering corrupts stored ordering.** `_drain_steer` persists the steer as
   its own `user` message immediately (`harness.py:193`) while the assistant
   message is written only at the end (`:314`). Stored `seq` order becomes
   `user, user(steer), assistant` — the correction appears *before* the answer
   it corrected — and `_replay` faithfully reproduces that inverted order on
   the next turn.
7. **No changed-files record.** `Workspace.snapshot()` / `changed_since()`
   already diff the workspace per tool call, and the results are used to
   discover artifacts — but they are discarded rather than accumulated per
   turn. Nothing says "this turn produced these files."
8. **A god object blocks progress.** `AgentHarness` is 883 lines owning
   conversation lifecycle, slash parsing, skill routing and materialisation,
   history replay, the ReAct loop, LLM streaming and retry, tool dispatch and
   parallelism, failure/repeat policy, the monitor hook, verification,
   plan-option extraction, steering state, and persistence.
9. **Blocking work on the event loop.** `sqlite3` is synchronous (25 call
   sites in async handlers, including `list_messages` every turn), and
   `check_equivalence` (sympy), `web_search` (`ddgs`) and all file-tool I/O
   run inline. `document_tool` shows the correct `asyncio.to_thread` pattern
   but the others do not follow it.
10. **The verify/revise path — the largest feature in the backend — has zero
    tests.** No test touches `req.verify=True`, `_verify_and_revise`,
    `_extract_verdict`, `_extract_tag`, or the `phase`/`verification` events.
    `test_replay_and_collapse` also never reaches `_collapse_parts`, because
    with one prior assistant message `full_from` is always 0.
11. **Dead seams that mislead readers.** `TurnState.plan` is aliased into
    `ctx.state["plan"]` but `UpdatePlanTool` rebinds the key
    (`plan_tool.py:53`), so the field is permanently `[]`;
    `Tool.background` is set and never read; `ChatOptions.parallel_tool_calls`
    is plumbed to the wire but never set by the harness;
    `StreamDelta(kind="finish")` is produced by the adapter and has no branch
    in `_stream_llm`, so `finish_reason` is dropped;
    `Database.update_message` / `get_message` / `delete_messages_from` have
    zero callers; `sandbox._noop_emit` is dead. The `events.py` module
    docstring is also stale (`message_end` and `stopped` payloads are wrong) —
    `docs/PROTOCOL.md` is the accurate source.

Four further gaps that only become visible when compared against ZCode's
coding harness (Part 2) — MatOpt is missing these entirely:

12. **No read-before-edit freshness guard.** `edit_file` enforces unique-match
    but cannot detect an edit against a file the model never read, or read
    three turns ago before the agent's own `python` tool rewrote it. See
    §3.8d.
13. **Parallelism is a one-line heuristic with no dependency awareness and no
    concurrency cap** (`harness.py:634-635`). See §3.8b.
14. **The tool contract carries no safety metadata** beyond a single
    `read_only` boolean, so approval, plan-mode gating, turn termination, and
    schema visibility are all name-based special cases. See §3.8a.
15. **`ctx.state["skills"]` survives compaction** while the model's actual
    visible history no longer does. ZCode answers "is this session currently
    holding skill X" *from provider-visible history*, so the answer correctly
    reverts to "no" after compaction
    (`core/src/tool/types.ts:259-264`). MatOpt's bookkeeping and its prompt
    can therefore disagree.

The **first commits** in §4.5 are chosen to start with the items that unblock
the most: the design layer, the log that everything else folds over, and the
one live correctness hole that is independent of both.

### 3.2 Phase 1 — Design foundation (do this first, it unblocks everything visual)

**Step 1 — port the token system.** New `frontend/src/styles/tokens.css`
replacing the OpenAI palette, written as *three tiers*, mirroring dsh:

```css
/* tier 1 — static palette */        --mo-static-neutral-bluish-900: #1b1b1c;
/* tier 2 — semantic aliases */      --mo-alias-bg-base / -bg-layer-1..3
                                     --mo-alias-border-l1..l4
                                     --mo-alias-label-primary|secondary|tertiary|caption
                                     --mo-alias-link / interactive-bg-hover
                                     --mo-alias-state-{success,warn,error,idle,business}-*
                                     --mo-alias-markdown-* / scrollbar-*
/* tier 3 — domain */                --mo-sidebar-fill, --mo-work-rail-width, …
```

Add `body[data-theme="dark"]` with a full override — MatOpt is light-only
today, and this is the highest-perceived-value single change. Rename the
`--oai-*`/`--color-*` aliases to `--mo-*` in one mechanical pass across the
17 CSS files, and in the same pass:

- delete the duplicate aliases (`--color-ash` / `--color-sidebar-mist`;
  `--color-edge` / `--color-hairline` / `--border-light`) and the 15 unused
  tokens, so a refactor cannot pick the "wrong" name for an identical grey;
- replace `AnalysisPanel.css`'s private `--pane-bg` and all 19 of its literals
  with `--mo-alias-bg-layer-2`, collapsing the app's three accidental greys
  into two real surfaces;
- add the missing scales — `--mo-space-1..12`, `--mo-text-2xs..3xl` with
  leading/weight/tracking, `--mo-elevation-1..3`, `--mo-z-*`,
  `--mo-duration-*`, `--mo-ease-*` — and map every inlined `28/22/16/14/12px`
  radius onto the new 6-step scale by component role (that mapping is
  mechanical, the `ui-radius.md` table is the rubric);
- make the theme-locked renderers theme-aware, not just the CSS:
  `mermaid.initialize({ theme: 'base', themeVariables: … })` driven from a
  token read, `PlotRenderer` layout/colorway from tokens with
  `displayModeBar: false` (modebar on `hover`), a `ChartRenderer` colorway,
  and a Prism style object chosen per theme.

New global sheets ported from dsh, keeping the rationale comments:

| New file | Ported from | Content |
|---|---|---|
| `styles/tokens.css` | `design-platform.css` | palette + aliases + dark override |
| `styles/base.css` | `base.css` | font stacks, `--mo-ease-in-out`, radius scale `4/8/12/16/20/28` |
| `styles/elevation.css` | `gradient-shadow-text.css` | `--mo-elevation-{panel,prominent,soft}` with the 0.5px hairline as shadow layer 1 |
| `styles/corner-shape.css` | `corner-shape.css` | `@supports (corner-shape: superellipse(1.5))` |
| `styles/focus.css` | `focus.css` | `--mo-focus-ring-width: 2px`, pointer-modality suppression |
| `styles/scrollbar.css` | `scrollbar.css` | `@supports not selector(::-webkit-scrollbar)` gate + thumb tokens |

Also adopt dsh's code-font stack verbatim (it deliberately omits a bare
`monospace` tail so Windows CJK does not fall back to SimSun).

**Port fidelity notes.** A line-by-line map of the dsh design system —
every token value, every component's geometry, and what is entangled with
Cordis — is checked in at
[`docs/reference/dsh-web-design-system-map.md`](reference/dsh-web-design-system-map.md).
Read it before writing any CSS. The corrections and specifics that matter
most:

- **The only dark selector in dsh is `body[data-ds-dark-theme]`**, set by
  `ui-layout/src/client/theme-presenter.ts` alongside
  `documentElement.style.colorScheme`. Use `body[data-mo-dark-theme]` and
  mirror that applier (~40 lines) rather than inventing a scheme; add a
  pre-paint `<head>`/bootstrap script like `ui-theme/src/boot-theme.ts` or
  dark mode will flash white on load.
- **There is no spacing scale and no z-index scale in dsh either.** Spacing is
  literal px per component; z-index is literal (documented hotspots: code-block
  banner 6, composer seat 7, back-to-bottom 8, slash-menu seat 9, right panel
  10, frame handles 11, overlay 20, settings 1000). MatOpt should still add
  `--mo-space-*` and `--mo-z-*` — but do not expect to copy them from dsh.
- **One accent colour does a lot of work.** `--dsw-alias-state-business-primary`
  (deepseek-500 `#4176E6` light / deepseek-450 `#7AAAFF` dark) is the focus
  ring, the active tab bar, the text caret, and the running-status label. The
  brand fill is **near-black in light, near-white in dark**
  (`--dsw-alias-brand-primary`), not blue. MatOpt's current teal brand is the
  opposite choice; decide deliberately.
- **Neutral flat borders are 0.5px** and state-coloured borders stay 1px
  (already in the table above, but it is the single most visible difference
  from MatOpt's current `1px solid #e5e5e5`).
- **Radii that are effectively uncapped must pair `corner-shape: round`** —
  avatars, dots, pills, the scrollbar thumb. dsh enforces this with a
  stylesheet test.
- **Refined component geometry** to hit on the first pass: Button `md` H36/R12
  pad `0 14` 14/22, `sm` H28/R8 pad `0 10` 12/18, disabled `opacity: .4`;
  Switch 36×20 thumb 16; Input H32/R12; Pill H24 pad `0 8` 12/18; Tag pad
  `1px 8px` 11/17; SegmentedControl outer R12 pad 4 segments R8 H28;
  SegmentedTabs outer R16 pad 4 segments R12; DisclosureRow 24px;
  MenuSurface R16 (compact R12); Modal R28; toast/tooltip bubble pad `3px 7px`
  R8 with 16px/R4 keycaps.
- **Type ladder** (all `font` shorthand, paired size/line-height): markdown base
  `14px/24px` with a `--dsh-content-font-size` axis (10–22px, default 14),
  secondary 13/20, small 12/18, code-block 11/19, h1 21/30 h2 19/28 h3 18/26,
  weights only 400/500/600/700.
- **Shell geometry worth copying**: centered column
  `clamp(680px, 64%, 920px)`, composer card at column + 32px, transcript flow
  gap 6px (12px around an answer, 16px around a turn header), sidebar
  280px default / 264 min / 420 max / 56 collapsed with auto-collapse at
  1024px, right rail min 300px, settings modal 800×800 R28 with a 188px nav
  and H40/R12 cells.
- **The composer is the signature element**: `border: 0`, R28,
  `--dsw-specific-input-major` fill, `box-shadow: var(--dsw-elevation-soft)`
  with `--dsw-elevation-stroke-color` rebound to `border-l2`, a sticky seat
  with a 36px top fade to the page background, and a scroll region capped at
  336px (14 lines).
- **Markdown is a real subsystem in dsh** (`ui-primitives/src/markdown/`,
  467-line stylesheet, an incremental streaming block-freeze parser with
  `UNSTABLE_TAIL_BLOCKS = 2`, and a single Shiki highlighter). MatOpt should
  **not** replace its KaTeX/react-markdown stack — the math path is better
  suited already — but the *incremental freeze* idea is directly applicable to
  the perf problem in phase 3, and the `--shiki-*` palette is portable if
  `react-syntax-highlighter` is ever swapped.
- Icons: dsh hand-authors its SVG set (1658 lines, Regular stroke 1 / Medium
  stroke 1.3, explicit `size` prop). MatOpt uses `lucide-react`, which is the
  right call to keep — but standardise icon sizing on a token instead of the
  current per-call-site `size={11..20}`.

**Step 2 — build `frontend/src/ui/` primitives.** Port the controls MatOpt
needs, from `packages/client/ui-primitives/src/`:

| Primitive | dsh source | Replaces in MatOpt |
|---|---|---|
| `Button` (`primary/ghost/outline/toolbar`, `sm`=H28/R8, `md`=H36/R12) | `Button.tsx` | ad-hoc `<button className="icon-btn">` |
| `Menu` + `MenuSurface` (R16 outer, 4px pad, R12 items, keyboard walk, focus return) | `Menu.tsx`, `MenuSurface.tsx` | `ModelPicker`, `SkillCommandMenu`, `ChatPage` header menu |
| `Pill`, `Tag` (8 tones) | `Pill.tsx`, `Tag.tsx` | status chips |
| `DisclosureRow` (24px, chevron on hover) | `DisclosureRow.tsx` | reasoning/"Thinking" toggle, tool details |
| `TextShimmer` (300ms delay, 1s sweep, 15° tilt) | `TextShimmer.tsx` | streaming status line |
| `StateDot` (`done/warning/error/idle/ongoing`, phase-locked loader) | `StateDot.tsx` | tool status pills |
| `PathLabel` (subdued dirs, primary filename, left-edge fade) | `PathLabel.tsx` | file paths in `FilesPanel`/tool cards |
| `Tooltip`, `HoverCard`, `Modal`, `ImageLightbox`, `Toast` | same names | hand-rolled equivalents |
| `JsonTree` | `JsonTree.tsx` | raw tool-argument dumps |
| `TerminalBlock`, `ReadBlock`, `SearchBlock`, `DiffBlock` | same names | `ToolCallCard` body per tool |
| `SegmentedControl`, `SegmentedTabs` | same names | Chat/Trajectory switch, Work rail tabs |

Two mechanical decisions, both cheap:

- **Add CSS Modules** (`vite` supports them with a `.module.css` suffix and
  zero config). `ui-radius.md`'s nested-geometry rules are unenforceable with
  global class names, and MatOpt's `MessageList.css`/`ChatPage.css` already
  have specificity fights. Keep the *global* sheets global.
- **Keep plain CSS for feature CSS if you prefer**, but then add a lint check
  that feature CSS uses `--mo-alias-*` and no literal hex. That is the actual
  dsh rule; CSS Modules are just its enforcement mechanism.

**Step 3 — restructure the shell.** Today: `Sidebar | main__stage
(main__thread-col + AnalysisPanel + FilesPanel)` in `pages/ChatPage.tsx`. Adopt
dsh's target/ring shape:

```
Sidebar │ ViewSwitcher (Chat | Trajectory | Report) │ WorkRail (collapsible)
```

Specific defects to fix while restructuring `ChatPage.tsx` (275 lines mixing
conversation state, streaming, file state, menu state, and layout):

- **the composer JSX is written once and rendered in two branches**
  (`ChatPage.tsx:225` and `:248`), so `MathInput` unmounts and the user's draft
  is discarded whenever `showWelcome` flips — render one composer, once;
- `main__status` reserves 22px and hides text with `color: transparent`
  (`ChatPage.css:81-95`) — replace with a real streaming affordance
  (`TextShimmer` + `StateDot`);
- the header has an empty left slot (`.main__header-left`) and a hand-rolled
  "More" menu — give it a title/breadcrumb and mount the existing (currently
  dead) `ExportButton`;
- `useConversations` keeps `activeId` in memory only (`:19`), so a reload
  loses your place. Add routes (`/`, `/c/:id`, `/settings`) — the dependency
  is already installed and unused;
- the <1100px drawer has no backdrop/scrim; errors render as an
  undismissable red strip — add `Toast`.

**Step 4 — prune and test.** Delete the dead surface identified above
(`ReasoningSlider`, `FileUploader`, `InlineMathText`, default-export
`ToolCallCard`, `useDocument`, `useExport`, `services/ocr.ts`,
`services/document.ts`, the deprecated `MathInput` skill props and
`parseSlashSkillTokens` shim, unused `react-router`/`prismjs` type deps).
Then **write reducer tests for `services/stream.ts`** — all 22 events, already
a pure function, currently untested, and the highest-risk file in the
frontend. Wire `services/suggestions.ts` into `WelcomeScreen` while you are
there; the backend route already exists.

**Verification:** one test per primitive (ports of
`packages/client/ui-primitives/tests/`), a `stylesheet-scan` test ported from
`ui-theme/tests/stylesheet-scan.ts` that fails on literal colors in feature
CSS, and the new `stream.ts` reducer suite. Visual check in both themes at
1440 and 390 widths.

**Two decisions Phase 1 makes that are visible and reversible.** Recording
them here because they are deliberate, not incidental, and a reviewer should
be able to accept or veto each with one line:

1. **The brand colour changed.** MatOpt's teal (`#10a37f`) is gone, replaced
   by dsh's scheme: near-black primary in light / near-white in dark, with
   `state-business-primary` (blue, `#4176E6` / `#7AAAFF`) carrying links,
   active states and focus rings. This is §3.2's decision, and it is the
   single most noticeable change in the whole design migration. It is one
   token (`--color-brand`) to revert.
2. **The UI font changed as a side effect.** `--font-sans` was
   `"OpenAI Sans", "Söhne", "Inter", …` — and none of those three was loaded
   by any `@font-face`, so the app was actually rendering Helvetica Neue all
   along. It now uses dsh's stack (SF Pro on macOS). Worth knowing that this
   is a *fix*, not a regression.

**One piece of follow-up work Phase 1 explicitly defers.** The vendored
`corner-shape.css` applies `superellipse(1.5)` to `*` under `@supports`. dsh
pairs every full-round declaration with `corner-shape: round` in the owning
component sheet and enforces the pairing with a stylesheet test. MatOpt's 16
sheets predate that rule, so Phase 1 collects a **12-selector opt-out block in
`tokens.css`** instead of restyling them. That block is a debt: it should be
**dissolved into the component sheets** once they are migrated, because a
central opt-out cannot keep pace as new rounded surfaces are added. Schedule
it with the primitive adoption, since ported dsh components already carry the
pairing correctly.


### 3.3 Phase 2 — Durable session log (the enabling refactor)

This is the change that makes phases 3–6 possible. It is additive: the
existing `messages` table keeps working while a new event log is written
alongside it.

**New module `backend/app/agent/session/`:**

```
session/
├─ events.py       SessionEvent dataclasses + SessionEventType Literal/Enum
├─ log.py          SessionLog: append (seq/time/type/data), contiguous-seq
│                  validation, lossless-JSON check, subscribe(), fork(prefix)
├─ store.py        SQLite persistence: session_events table, WAL, batched
│                  appends, torn-tail repair on open
├─ derive.py       derive_messages(log) -> provider history  (replaces _replay)
├─ project.py      incremental folds: turns/steps index, usage, stats, outline
└─ repair.py       close an interrupted turn (missing turn/end) on open
```

**Event vocabulary for MatOpt** (dsh names kept, reduced to what MatOpt has):

| Event | Notes |
|---|---|
| `turn/start`, `turn/end` | `turn/end.reason` ∈ `completed/aborted/blocked/error/max-tokens/interrupted/forked`. `interrupted` and `forked` are **never emitted live** — only by crash repair and fork seeding. |
| `step/start`, `step/end` | one model call + its tool batch |
| `system/message` | the assembled system prompt (skills, files, datasets) |
| `user/message` | `{content, source: human\|steer\|skill\|attachment}` |
| `assistant/message` | `{message, stream, usage?, interrupted?}` — **stream + usage embedded** |
| `assistant/attempt` | a failed/retried LLM call that committed no surface message |
| `tool/call` | `{callId, name, arguments}` raw JSON string |
| `tool/result` | `{callId, ok, output, artifacts, error?, meta}` |
| `plan/write` | `update_plan` results (replaces the `plan` SSE event as source of truth) |
| `skill/activated` | `{names, auto}` |
| `phase/change`, `verification` | MatOpt's verify/revise, made durable |
| `compaction` | phase 6 |
| `workspace/changes` | phase 5 |
| `request/header`, `request/context` | route, model, reasoning, tool catalog |

**The most important design decision here, and it is not "append-only".**
dsh's log is append-only, but the *model-visible surface* is not — it is a
projection with explicit, logged **surface operations**. Every
message-producing event carries `surfaceOp`:

```
surfaceOp = 'append' | { op: 'replace', startSeq, endSeq }
```

`replace` shadows an inclusive span **of surface positions**, not of numeric
seqs, and the replaced events stay in the log forever. That is what makes
compaction, system-prompt rewriting, and image offload non-destructive — and
it is why "append-only log" and "editable conversation" are not in tension.
Do not design MatOpt's log as pure append; you will hit this on day one.

Two things MatOpt already does by mutation or not at all need replace
semantics:

- **`system/message` is a surface node, not an append.** MatOpt rebuilds the
  prompt every turn *and* on every mid-turn skill activation
  (`harness.py:440-441` rewrites `history[0]` in place). In the log this is a
  logged replacement of the system node, **never a second system message
  accumulating beside the first**. Node 0 is protected as the system head.
- **Compaction (phase 6)** replaces a span with a summary rather than deleting
  history.

Each message event also carries `sourceEventSeqs` — the seqs it was derived
from — which is how a reader answers "what produced this text?". dsh
range-encodes these (a run of ≥3 consecutive seqs becomes `[start, end]`), a
cheap win worth copying. For safety, validate the candidate event **before**
it enters the log so a malformed event can never poison it.

**Schema (additive, `PRAGMA user_version` migration):**

```sql
CREATE TABLE session_events (
  conversation_id TEXT NOT NULL,
  seq             INTEGER NOT NULL,
  time            REAL    NOT NULL,
  type            TEXT    NOT NULL,
  data            TEXT    NOT NULL,   -- lossless JSON
  surface_op      TEXT,               -- NULL | 'append' | '{"op":"replace",...}'
  source_seqs     TEXT,               -- NULL | range-encoded JSON
  ignorable       INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (conversation_id, seq)
);
CREATE TABLE session_usage (          -- folded, for cheap queries
  conversation_id TEXT, turn INTEGER, step INTEGER, attempt INTEGER,
  provider TEXT, model TEXT, reasoning TEXT,
  input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER,
  cache_read_tokens INTEGER, cache_write_tokens INTEGER,
  reasoning_tokens INTEGER, duration_ms INTEGER, ttft_ms INTEGER,
  PRIMARY KEY (conversation_id, turn, step, attempt)
);
```

`ignorable` is dsh's forward-compatibility opt-in: an unknown event type is a
**hard decode error** unless it set `ignorable`, in which case a reader skips
it. Adopt it now — it costs one column and it is the difference between "a
newer MatOpt wrote a new event" and "an older reader corrupts the log".

**Invariants to enforce in code (this is the whole point):**

1. `SessionLog.append()` rejects non-JSON-serializable payloads and any
   non-contiguous `seq`.
2. *Model-visible means logged*: `derive_messages()` must produce byte-equal
   history to what the loop actually sent. Add a debug assertion in
   `_stream_llm` comparing the frozen request to `derive_messages(log)`.
   This is MatOpt's version of dsh's runtime invariant
   (`docs/architecture.md:127`). Two things must be logged that are currently
   rebuilt and thrown away: the **assembled system prompt per request**
   (`system/message`) and the **advertised tool schema list per step**
   (`request/header`). Without those, historical prompts and skill activation
   are unrecoverable.
3. On open, `repair.py` inserts a synthetic `turn/end {reason: 'interrupted'}`
   for a turn with no close, so every reader sees a well-formed log.
4. `messages` becomes a *materialized view* of the log for the current UI,
   written by the same code path, until phase 3 retires it.

**Three behaviour fixes that fall out of the log and should land with it:**

- **Steering ordering.** Today a steer is persisted as its own `user` message
  the moment it is drained, while the assistant message is written at turn
  end — so stored order is `user, user(steer), assistant` and `_replay`
  reproduces the correction *before* the answer. With the log, the steer is
  `user/message {source: 'steer'}` at its true step boundary inside the turn.
  Fix `_replay`/`derive_messages` to place it correctly and add a test that
  asserts stored order for a steered turn.
- **Setup failures.** The outer `except` (`harness.py:323-325`) emits `error`
  with no `message_end` and persists nothing, unlike mid-loop failures. With
  the log, the failed `turn/start` is always closed by a `turn/end`.
- **Attempt records.** Log `assistant/attempt` on the `_stream_llm` transient
  retry and expose the SDK's `max_retries` (set it to 0 and do the retry in
  the harness, so both the attempt and its cost are visible). This is a
  prerequisite for phase 4's per-attempt usage fold.

**Sequencing note on blocking I/O and write batching.** Phase 2 does not fix
the event-loop blocking (`sqlite3`, `sympy`, `ddgs`, file I/O), but the new log
multiplies SQLite writes, so do the minimum:

- batch appends behind a **fixed, non-resetting deadline** — dsh arms a 200ms
  timer when the first event goes pending and later events *join it without
  extending it*, so a busy stream cannot starve the flush
  (`session-persistence-jsonl/src/storage.ts:31`);
- **flush before side effects**, not after: dsh awaits a durability checkpoint
  before provider dispatch and before top-level tool bodies
  (`session-checkpoint-policy/src/index.ts`). That is what makes "the log is
  the truth" survive a crash rather than merely assert it;
- move the write behind `asyncio.to_thread`.

The wider cleanup (sympy, `ddgs`, file I/O off the loop) is a separate,
well-scoped PR — `document_tool.py:101` already shows the correct
`asyncio.to_thread` pattern to copy.

**Crash repair appends, it never truncates.** dsh's `repair.ts` finds the last
unmatched `turn/start`/`step/end` pair and *appends* the missing closers:
unmatched `tool/result`s first (in assistant order), then `step/end`, then
`turn/end {reason: {kind: 'interrupted'}}`. Two details worth copying verbatim:
the synthetic events **reuse the last real event's timestamp** rather than
inventing a future one, so replay stays deterministic; and each synthetic tool
result carries a deterministic id
(`${cause}-tool-result-${callId}-${seq}`) and model-facing text that
distinguishes *never started* (`TOOL_NOT_STARTED`) from *outcome unknown*
(`TOOL_OUTCOME_UNKNOWN`), telling the model to retry only if the tool is
read-only or idempotent and otherwise to verify external state first. A
truncate-based repair would silently rewrite history; an append-based one is
auditable.

**Files touched:** `harness.py` (emit `log.append(...)` beside each existing
`emit(ev(...))`), `storage/db.py` (+schema, +queries), `runtime.py`, and
`_replay()` → `derive_messages()`. Existing `emit()` calls stay, so the SSE
contract in `docs/PROTOCOL.md` does not break and the frontend keeps working
throughout.

**Stream reliability, in the same phase.** Because the log now holds the
truth, three transport defects become both visible and fixable:

- **`to_sse` emits no `id:`** (`events.py:85-87` — only `data: …\n\n`), so
  there is no `Last-Event-ID` resumption. Once events have `seq`, emit
  `id: <seq>` and let a reconnecting client resume from its last seen seq via
  `GET /api/conversations/{id}/events?from_seq=`. This is the single highest-
  value transport change and it is free once phase 2 lands.
- **Disconnect detection is polled only after a yielded event** (`chat.py:73`)
  while the idle heartbeat is 10s, so a cancelled client keeps a run — and its
  billed tokens — alive for up to ~10s. Poll during the keepalive wait too.
- **`asyncio.Queue()` has no `maxsize`** (`harness.py:198`), so a stalled
  client cannot exert backpressure and a long turn's whole token stream buffers
  in RAM. Bound it once `id:`-based resumption exists as the fallback.

Also fix the two documentation drifts while the vocabulary is being written
down: `events.py`'s module docstring has wrong payloads for `message_end` and
`stopped` and omits five event types (`docs/PROTOCOL.md:152-178` is the
accurate source — make it the single reference and delete the duplicate), and
`PROTOCOL.md:70`'s `{server}_{tool}` MCP naming claim does not match
`client.py:104`, which overwrites `tool.name` with the raw listed name while
`mcp_server` is derived by splitting on the first `_` (`client.py:99-101`).

**Verification:** golden-log tests — a scripted fake provider drives
`harness.py`, the resulting log is snapshotted, `derive_messages()` is asserted
against the recorded request bodies, and a reopen-then-replay test proves
determinism. Extend `backend/tests/fake_provider.py`, which already exists.

Two pre-existing test holes must be closed in this phase, because phase 3–6
build on them:

- **verify/revise has no test at all.** `req.verify=True`,
  `_verify_and_revise`, `_extract_verdict` (including the `\boxed{0|1}`
  fallback and the `uncertain` default), `_extract_tag`, and the
  `phase`/`verification` events are entirely uncovered — the largest untested
  feature in the backend, and one that must be logged step by step.
- **`_collapse_parts` and the history-budget trim never run in the suite.**
  `test_replay_and_collapse` uses one prior assistant message while
  `recent_turns_full_replay` is 3, so `full_from` is always 0 and only
  `_expand_parts` executes. Add a fixture with >3 prior turns and a budget
  small enough to force trimming.
- **`app/llm/openai_compat.py` is entirely untested.** `FakeProvider` bypasses
  the adapter, so nothing proves the real wire path: `_usage_dict` (the source
  of every token number in phase 4), the `model_extra` reasoning fallback, and
  the `stream_options` rejection fallback. Recorded HTTP fixtures belong in
  this phase because phase 4 depends on the adapter being right.
- **The SSE contract is untested end to end.** `/api/chat/stream` is only
  exercised for the 400 no-provider path; nothing asserts framing,
  `Event.to_sse`, event ordering over HTTP, or the response headers. With
  `id:`-bearing frames arriving, this becomes a real contract worth pinning.

While the log vocabulary is being written down, also add the missing static
analysis the repo has never had: there is **no `pyproject.toml`, ruff, mypy,
or CI config anywhere** — only `backend/pytest.ini`. A `ruff` + `mypy` config
with the existing `Any`-heavy signatures left unsuppressed is a good forcing
function for the `AgentHarness` split in this phase.

### 3.4 Phase 3 — Trajectory view

**New endpoints:**

```
GET /api/conversations/{id}/events?from_seq=&limit=   # paged event window
GET /api/conversations/{id}/outline                    # turns/steps index
GET /api/conversations/{id}/trajectory?turn=           # assembled records
```

**New frontend module `src/features/trajectory/`**, porting
`packages/client/ui-trajectory/src/client/`:

| Port | From | Behaviour |
|---|---|---|
| `trajectory-record.ts` | same | `ConversationNode` union: `user`, `assistant`, `tool`, `context`, `turn-error`, `turn-end`, `compaction`, `workspace-changes` |
| `TrajectoryTable.tsx` + `trajectory-virtual-rows.ts` | same | anchored tail window, overscan, older-page load-on-click, semantic keys surviving prepends |
| `TrajectoryGroupHeader.tsx` | same | turn boundaries with reason + duration |
| `TrajectoryTimeline.tsx` | same | fixed timing overview: assistant spans split TTFT vs decode, drag to filter, wheel to zoom, right-click to clear |
| `TrajectoryInspector` | `TrajectoryCell` + `code-program.ts` | per-record tabs: **Usage / Duration / Input / Output / Timing** |
| `code-program.ts` | same | numbered highlighted source for `python`/`bash` args, `{}` toggle for raw JSON, exact copy |
| `trajectory-search-index.ts` | same | in-view search |

Store shape: hold the paged event window in a reducer keyed by
`(conversationId)`, and build records with a **pure fold** so the same code
can run on the server later for reports. Do not put trajectory state in
`useChat` — that hook is already 274 lines and owns streaming.

**A decision the redesign must make explicitly.** The thread currently
*deletes* content that dsh surfaces: `MessageList.tsx:87-92` skips `monitor`,
`phase`, `tool_call` and `verification` parts; `SILENT_TOOLS` / `CODE_TOOLS`
suppress tool cards; `isCommentaryText` hides preamble prose; reasoning
collapses to one static "Thought" label with no way to expand it. That is a
coherent product stance (the chat stays prose-only, the Work rail holds the
machine detail) and the Trajectory view is the natural home for what is
currently dropped. Make it deliberate rather than inherited:

| Content | Chat view | Trajectory view |
|---|---|---|
| Prose | full markdown | same |
| Reasoning | collapsed `DisclosureRow` with duration + token count | full recorded text |
| `tool_call` | one `Work · N actions` chip | one row per call, tool-specific block |
| `phase` / `verification` | verdict chip on the answer | boundary row with round number |
| `monitor` | warn/block chip | row with level, summary, tool |
| `plan` | Work rail checklist | `plan/write` row on the timeline |

Also add the affordances the thread is missing and the log now makes cheap:
per-message token/cost readout, timestamp, regenerate (re-run from a
`turn/start` prefix), copy-code on code blocks, a scroll-to-bottom control,
and a "new messages" marker. `Database.update_message` / `delete_messages_from`
already exist with zero callers and are the primitives for edit/regenerate.

**Also in this phase:** move the `AnalysisPanel` contents (plan, monitor
chips, figures) into the Work rail as tabbed panes, and let the Trajectory
view own tool traces. Chat then renders only prose + inline artifacts —
matching dsh's split between `ui-chat` and `ui-trajectory`.

**Add virtualization at the same time.** Nothing in the thread is windowed
today, rows are not memoized, and every `text_delta` re-runs
`MarkdownRenderer`'s three regex preprocessors plus react-markdown for the
growing last message. The Trajectory view needs an anchored, paged row window
anyway; reuse it for long chat threads (or at minimum memoize `MessageRow`
and add `content-visibility: auto`).

**Verification:** fold unit tests over recorded logs (no React), plus a
component test per row kind. Assert the timeline's TTFT equals
`firstTokenTime − stepStartTime` from the log.

### 3.5 Phase 4 — Token, cost, and usage reporting

**Measurement (`backend/app/agent/session/metrics.py`), porting
`token-meter`:**

```python
def derive_turn_usage(events) -> TurnUsage | None:
    """Exact per-attempt fold. Returns None when evidence is incomplete —
    never a partially-right number."""
```

- Normalize each attempt into **four disjoint buckets**: `input_tokens`
  (uncached), `output_tokens`, `cache_read_tokens`, `cache_write_tokens`.
  "Disjoint" is load-bearing: **billed input = input + cacheRead + cacheWrite**,
  and **`reasoning_tokens` is a subset of `output_tokens`** — never add it
  again. dsh's DeepSeek adapter explicitly folds cache hits *out* of
  `prompt_tokens` so the buckets cannot overlap
  (`llm-deepseek/src/translate.ts:34`).
- Optional buckets are summed **only when every attempt reported them**
  (dsh `aggregateAttempts`, `turn-usage.ts:126`).
- If `total_tokens` is present, derive exact prompt = `total − output` and
  require it to be ≥ the known prompt sum; a contradiction invalidates the
  whole turn.
- **Replace-on-retry semantics**: a second `(turn, step)` usage report *replaces*
  that step's contribution rather than adding to it; an `llm/retry-started`
  boundary clears the slot so the retry adds a genuinely new attempt. Getting
  this wrong double-counts every retried step.
- `assistant/attempt` boundaries contribute extra billed attempts — this is
  why retries must be logged, not swallowed.

**Heuristic estimator** for pressure when usage is unavailable: 4
characters/token plus per-block/per-role overhead — dsh's whole estimator is
three constants (`CHARS_PER_TOKEN=4`, `BLOCK_OVERHEAD=4`, `ROLE_OVERHEAD=4`,
`estimate.ts:13`), with tools priced as
`ceil(len(json.dumps(tools)) / 4) + 4`. Keep it one deterministic function so
every consumer agrees.

**Pressure & occupancy** — MatOpt already has `context_window` and
`max_output` per model in `llm/catalog.py`:

```python
class ContextPressure:  # -> GET /api/conversations/{id}/context
    pressure_tokens: int | None   # newest provider-reported prompt size
    projected_tokens: int | None  # what the next request would cost
    context_window: int | None
    breakdown: {system, tools, messages}   # heuristic composition
```

Two subtleties from dsh worth keeping: `pressure_tokens` is the **prompt side
only** (`input + cacheRead + cacheWrite`), so it holds still while a turn
streams instead of jittering; and `projected_tokens` rides as a *signed delta*
(`max(0, pressureTokens + surfaceTokens − sampledSurfaceTokens)`) rather than a
fresh absolute estimate, so it stays correct across a compaction. Provider
usage anchors a measurement **only if** the canonical request envelope matches
*and* its total is ≥ the heuristic anchor; otherwise reprice the whole surface.

**Cost — this is new work, not a port.**

**dsh has no cost model.** A repo-wide grep across every package finds no
`costUsd`, no `pricePerMillion`, no spend accumulator. What dsh calls "route
pricing" is **image token pricing** (`LlmImageRequestPricing`,
`llm-deepseek/src/request-pricing.ts`) plus context-window capacity — nothing
monetary. The docs are explicit that occupancy is "a user-facing reference,
not a billing or gating input".

So MatOpt's cost feature is additions, not translation:

- a `pricing` block per model in `llm/catalog.py`
  (`{input, cache_read, cache_write, output}` per million tokens, plus an
  `as_of` date, because prices move);
- a pure `price(usage, model) -> Decimal` in `metrics.py` over the four
  disjoint buckets;
- provider/model attribution via `TurnTokenUsage.routes`, which dsh supplies
  and is exactly the join key a cost table needs;
- explicit "estimate" labelling wherever a heuristic-derived number is shown,
  and no estimate at all when the bucket set is incomplete.

**Meter everything that bills.** Three currently-invisible cost sources:

- `_auto_title` (`harness.py:690`) — a `complete_text` call on the first turn
  of every conversation. Every conversation silently pays for it.
- `POST /api/followups` and `GET /api/suggestions` — also `complete_text`.
- SDK-internal retries and `_stream_llm`'s transient retry.

Fix at the source: make `complete_text` return `(text, usage)` and put it on
the `LLMProvider` ABC (it is currently only on `OpenAICompatProvider` and
`FakeProvider`, so `_auto_title` would `AttributeError` on any other provider
— swallowed by a bare `except`). Set the SDK `max_retries` to 0 and own the
retry in the harness so each attempt is a logged `assistant/attempt`.

**Budget enforcement** — `ModelSpec.context_window` is currently never
consulted and there is no per-conversation or per-day cap. Phase 4 adds a
configurable `max_turn_cost` / `max_conversation_cost` check at
`step/start`, surfacing a typed error event rather than silently truncating.

**Session stats fold** (`session-stats` port) → `GET /api/conversations/{id}/stats`:

```
turns, steps, llm_ms, tool_ms, ttft_ms, ttft_steps, decode_ms, decode_tokens,
tool_histogram{tool: count}, tokens{input, output, cache_read, cache_write,
reasoning, total}, cache_hit_ratio, cost_estimate
```

### 3.6 Phase 5 — Reports, deliverables, changed files

**Turn-scoped changed files** (`deliverables/workspace-changes` port,
`backend/app/agent/session/changes.py`):

- On `turn/start`, snapshot the workspace (git tree id when the workspace is a
  repo, otherwise whole-file hashes for files under a size cap).
- On `turn/end`, compute `[{path, display, added, deleted, binary?,
  oversized?}]` via `git diff --numstat` or a line diff, cap at `max_files`,
  keep exact totals, append `workspace/changes {turn}`.
- `GET /api/conversations/{id}/turns/{turn}/changes` and
  `.../changes/{index}/diff` returning `WorkspaceFileDiff` (unified hunks,
  three context lines, with the `coarse` fallback when the diff times out).

**UI:** a "Changed files" panel in the Work rail using `DiffBlock` from phase
1; a `PresentedFileCard`-style card when a tool produces a deliverable, and a
`present`-equivalent: extend the existing artifact mechanism so
`ExportButton` outcomes and LAMBDA reports land in one deliverables list.

**Reports:**

```
GET /api/conversations/{id}/report?format=markdown|json|html
CLI:  python -m app.report <conversation_id> --format markdown
```

Markdown report sections: run summary (provider/model/reasoning, status,
wall time), trajectory outline (turns/steps with reason and duration),
per-turn token table (reusing `derive_turn_usage`), tool-call histogram with
failure counts, verification verdicts, changed files with `+/-` counts,
artifacts produced, and cost estimate with an explicit "estimate" label.
This is the artifact the user asked for as "reports".

### 3.7 Phase 6 — Token-aware compaction

Replace `history_char_budget` (`config.py:70`) and the `_replay()` trimming
loop with dsh's model:

1. **Measure pressure** at `agent/pre-step`: `projected_tokens` vs a threshold
   derived from real reserve arithmetic rather than a flat ratio. dsh computes
   `messageBudget = context_window − reservedCompletionTokens`, then
   `pressureBudget = messageBudget − headroomTokens`, then
   `thresholdTokens = floor(min(context_window × thresholdRatio, pressureBudget))`
   with `thresholdRatio` 0.8, `headroomTokens` 65536, `retainRatio` 0.16.
   The reserved-output deduction is the part MatOpt's single `0.75` guess
   omits, and it is what stops compaction firing while a long answer still has
   room to finish.
2. **When over threshold**, select a range: start at node 0 (or 1 if node 0 is
   the system prompt), walk back accumulating tokens until the retain budget is
   met, then **walk the cut further back until no assistant tool-call is
   separated from its result**. Never split a call/result pair — this is the
   same pairing invariant the log gives you for free.
3. **Summarise cache-friendly.** dsh has no separate summarizer system prompt:
   it replays the conversation's *own* system prompt, tools, and leading
   messages, and appends the directive as the **final user message**, so the
   request is a genuine prefix of the last routed request and reuses the KV
   cache. Copy this — it is a large, free cost saving. The instruction mandates
   a fixed Markdown skeleton (Primary Request / Key Technical Concepts /
   Files and Code / Errors and Fixes / Pending Jobs / Current Work / Next Step
   / Critical Context), `(none)` for empty sections, exact paths and commands
   preserved, and a prior `<compacted-summary>` **merged** rather than copied.
4. **Bracket the operation in the log**: append `compaction/start` *first*,
   synchronously with validation, and `compaction/end` **last**, so a crash
   leaves a detectable orphaned start and never a false "finished". A stale
   start before a newer seed boundary is ignored on read.
5. `derive_messages()` honours compaction by substituting the summary for the
   shadowed range — **nothing is deleted**, so the trajectory view still shows
   the full history and the UI can render a "compacted here" boundary.
6. Emit a **shadow-price event immediately before** the replacing surface op,
   carrying the shadowed seqs and their token count, so a pure consumer can
   price the rewrite in O(1) without re-walking the log.
7. Also port the cheap wins: `compaction-tool-result-pruner` (threshold 8192
   chars, keep `head` 4096 / `tail` 1024, run it *before* range selection and
   re-measure) and image offload (replace old images with a placeholder) —
   MatOpt's figures are exactly the payload that blows a context window.

Settings become: `compaction_enabled`, `compaction_threshold_ratio`,
`compaction_headroom_tokens`, `compaction_retain_ratio`,
`reserved_completion_tokens`, `compaction_retries`, `tool_result_recent_chars`,
`tool_result_old_chars`. `history_char_budget` becomes a hard safety cap only.

Auto-compaction triggers on `agent/pre-step` (pressure) **and** on a
context-window-exceeded provider error, where dsh bypasses the thresholds
entirely and retries once only if the surface actually changed — copying that
guard prevents an infinite compaction loop on a request that cannot shrink.

**Two additions from ZCode's compaction** (`core/src/compact/`), which is a
different design worth merging with dsh's rather than replacing it:

- **The summary prompt.** ZCode's `prompt.ts` does three things dsh's does not.
  It wraps the work in an `<analysis>` block that is *stripped from the final
  summary* (`formatCompactSummary`), so the model reasons before committing. It
  instructs the model to preserve **security-relevant constraints verbatim** —
  "sensitive files or data to avoid, operations that must not be performed,
  credential or secret handling rules" — because those must survive
  compaction. And it lists **every user message**, since user feedback is
  exactly what gets lost. Both prompts are worth reading side by side; the
  union is better than either.
- **Tell the model its kernel state was cleared.** ZCode appends "your REPL VM
  state has been cleared as part of this compaction — variables defined in REPL
  calls before this point are no longer accessible; redefine any you still
  need." MatOpt has a **persistent Jupyter kernel** and its compaction will
  drop the *record* of cells the kernel still holds, so the same warning
  applies for the opposite reason. Also worth copying: ZCode points the model
  at an on-disk transcript of the pre-compaction conversation so it can recover
  exact snippets — MatOpt gets this free from phase 2's log.
- **Microcompaction as a separate, cheaper tier.** ZCode prunes old tool
  results on a *ratio* threshold (0.9) with a 2,000-token buffer, keeping the
  last 5, and replacing content with a fixed marker
  (`MICROCOMPACT_CLEARED_TOOL_RESULT_PREFIX`). It also has an **idle
  threshold** (60 minutes) — compaction can trigger on time, not only on
  pressure. MatOpt's kernel already reaps on idle, so a matching "compact
  after idleness" rule fits naturally.

### 3.8 Phase 7 — Harness upgrades from ZCode

Four changes, in dependency order. The first three are independent of the log
and could ship before phase 2 if you want them sooner; the fourth needs it.

**7a. Enrich the tool contract.** MatOpt's `Tool` (`tools/base.py:42`) has
`name`/`description`/`parameters`/`read_only`. Add the safety metadata that
ZCode declares in `ToolMetadata` (`core/src/tool/types.ts:68`), reduced to
what MatOpt can act on:

```python
class ToolMeta(NamedTuple):
    read_only: bool
    destructive: bool
    concurrent_safe: bool          # ← drives 7b
    side_effect_scope: Literal["none", "workspace", "system"]
    risk_level: Literal["low", "medium", "high"]
    needs_approval: bool
    provider_visible: bool = True
    stop_turn_on_success: bool = False
    timeout_ms: int | None = None
    max_output_bytes: int | None = None
```

Two of these replace name-based special-casing in `harness.py`:

- `stop_turn_on_success` — a tool declares that a successful result ends the
  turn, rather than the loop pattern-matching a tool name. ZCode's rationale
  is explicit: it is "an intrinsic capability declaration (like
  concurrentSafe/destructive), read by the executor and not guessed by tool
  name at the call site" (`tool/types.ts:83-88`).
- `provider_visible` — a callable tool can be hidden from the model's schema
  list. MatOpt currently does this by filtering names inside
  `current_schemas()` (`harness.py:491-497`), which is why
  `web_search`/`web_fetch` gating is scattered across three places.

**7b. Replace the parallelism heuristic with a scheduler.** MatOpt decides
parallelism with one expression (`harness.py:634-635`):
`parallel = len(parsed) > 1 and len(writes) <= 1`.

**There are two upstream designs and they are genuinely different. Pick
deliberately.**

| | ZCode — dependency-graph scheduler | dsh — bounded rolling pool |
|---|---|---|
| File | `tool/scheduler.ts` (245 lines) | `core/agent-loop/src/tool-calls.ts` (290 lines) |
| Model | `dependsOn` graph → topological sort → level grouping | exclusive calls are **barriers**; parallel calls use a bounded rolling pool |
| Parallelism decision | declared metadata (`destructive`/`concurrentSafe`/`readOnly`/`sideEffectScope`) | live concurrency mode, **reclassified before start** |
| Ordering guarantee | grouped barriers | dispatch may overlap, but policy/results/result context stay **model-ordered** |
| Dependency support | explicit, from the model | none — no `dependsOn` concept |
| Concurrency cap | `DEFAULT_MAX_CONCURRENCY = 10` | bounded pool |
| Abort behaviour | — | records synthetic error results for **skipped** calls so replay stays valid; drains started calls |

**Recommendation: start with dsh's rolling pool, not ZCode's graph.** Three
reasons. Its abort semantics are the more valuable property — recording a
synthetic result for every skipped call is exactly what keeps a log replayable
after a mid-batch cancellation, and MatOpt's phase 2 log needs that. It
preserves model order for results while still overlapping dispatch, which is
the behaviour a user actually notices. And it needs no `dependsOn`, so it is a
drop-in replacement for the current heuristic rather than a new concept the
model has to be taught. ZCode's graph becomes worth it only once something
genuinely needs declared dependencies — which nothing in MatOpt does today.

Port dsh's `tool-calls.ts` as `backend/app/agent/tools/scheduler.py`:

- classify each call as exclusive or parallel-safe from the tool's safety
  metadata, using dsh's reclassification step so a call's mode is decided
  against live state rather than at parse time;
- exclusive calls form **barriers**; parallel calls run in a **bounded rolling
  pool** with a configurable bound;
- keep **dispatch** overlapping but **results, policy, and result context in
  model order** — this is the invariant that makes the trajectory view and the
  log line up;
- on abort or an internal scheduler failure, stop replenishing the pool, drain
  what started, and **record a synthetic error result for every call that was
  skipped**, reusing dsh's constant name (`TOOL_ABORTED_BEFORE_DISPATCH`) so
  the log stays replays-valid;
- record whether any committed result **concluded the turn**, which is how
  `stop_turn_on_success` (7a) reaches the loop without a name check.

Keep ZCode's `ToolSchedule` shape (`parallel_groups` + flat `execution_order`)
as the *output type* if you also emit `batch_start`/`tool_start`/
`tool_complete`/`batch_complete` events — the trajectory view (phase 3) can
then show the parallel structure instead of a flat result list. That is
ZCode's contribution to this step, and it is worth keeping.

Two behaviours from ZCode's batch runner worth copying: the group runner
**short-circuits on a turn-stopping result**, materializing every tool in all
later groups as a cancelled result rather than silently dropping them; and it
deliberately does **not** skip later groups after a non-`concurrentSafe`
failure, because a local failure was truncating subsequent unrelated calls —
each tool now decides its own fate. MatOpt's `_execute_calls` has no
equivalent of either.

**Also port ZCode's `Edit` matcher while you are in this file.** MatOpt's
`edit_file` requires a single exact match and fails otherwise. ZCode's
`findEditMatch` is an **8-strategy ordered fallback** — `exact`, then
quote-normalized (`''""` → `'"`), line-number-prefix-stripped, escape-normalized,
unicode-escape-normalized, line-trimmed, indentation-flexible, and
block-anchor (first/last line match plus ≥0.8 average line similarity) — where
the first strategy producing candidates wins, and the three "broad" strategies
are **skipped under `replace_all`** because a fuzzy block matcher over a whole
file is too dangerous. Three details make it safe rather than sloppy: it
slices `actual_string` back out of the *original* content so the replacement
is byte-real; it runs **two independent uniqueness gates** (distinct candidate
*values* from the matcher, then occurrence *positions* from the caller); and it
preserves the file's existing quote style and line endings. For MatOpt this
directly reduces the most common failure mode in a read-heavy maths workflow —
a model that has the right intent but reproduces whitespace or quotes
imperfectly.

**And fix result persistence to match.** MatOpt truncates tool output at
`tool_max_output_chars = 12_000` and the text is then *lost*. ZCode separates
**truncation from persistence**: a shared
`<persisted-output>…</persisted-output>` envelope with a 2,000-character
preview, a pointer to the full artifact, and per-tool byte formatting
injection. Two properties are worth copying exactly — a persistence failure
must **never** turn a successful tool call into a failure (it falls back to
truncation), and under a *character*-budgeted provider whose persistence
failed, the original content is returned **untruncated** rather than
double-truncated with different wording. MatOpt already writes workspace
files, so the artifact store is the workspace itself.

**7c. Normalize tool input once, before anything consumes it.** MatOpt's
`_parse_arguments` (`harness.py:718`) produces a dict that the monitor reads
and the tool receives, but nothing guarantees every later stage reads the
same normalized value. ZCode's `resolveInput` runs after validation and
before the approval hook, and the doc comment states the property it buys:

> hooks, project permission rules, permission event payloads,
> `prepareApproval`, and the handler all read the **same normalized input** …
> **approval and execution are byte-identical**: parse once, carry those bytes
> all the way to the handler, so there is no "approve A, run B"
> (`tool/types.ts:313-326`).

For MatOpt this means: parsing and normalizing happens exactly once, the
normalized input is what `inspect_tool_call` (`monitor.py:41`) sees, what the
`tool_call_ready` event carries, and what the tool receives — and the
normalized form is logged, so the trajectory view shows the same bytes the
tool ran. Cheaper than it sounds: it is one function and a rule about who
reads what.

**7d. Read-before-edit freshness guard.** MatOpt has none, and this is the
highest-value single port for a tool-using agent. Its `edit_file`
(`file_tools.py`) enforces unique-match but cannot detect an edit against a
file the model never read, or read three turns ago before the agent's own
`python` tool rewrote it.

**Both upstreams implement this, and the two designs differ in a way that
matters — take dsh's.** dsh already ships read-before-edit as an event gate
(`packages/fs/fs-observation-policy/src/index.ts:65-88`) over a per-session
observation map, and its *semantics* are stronger than ZCode's:

| | ZCode | dsh |
|---|---|---|
| Mechanism | `ReadFileStateMap` in the runtime | `fs/write-intent` / `fs/edit-intent` waterfalls |
| Write precondition | freshness compare (mtime/size/revision) | `createIfAbsent` or **`replaceIfVersion`** — a compare-and-swap |
| Edit precondition | freshness compare | `{ version }` from the prior observation, or `FS_NOT_OBSERVED` |
| Stale detection | recomputed by the tool | enforced **atomically inside the provider** under a per-target lock |

The CAS is the important part: dsh's `writeIntent` returns
`{ kind: 'replaceIfVersion', version }` when the file was previously observed,
and `{ kind: 'createIfAbsent' }` when it was not, and the provider then does
the check **atomically at write time** under a per-target lock — so there is no
window between "the tool checked freshness" and "the write lands". ZCode
compares mtime/size and accepts a residual race. dsh's failure codes are
already the right shape: `FS_NOT_OBSERVED` ("cannot overwrite existing …
without reading it first") and `FS_STALE_VERSION` ("file changed since it was
read").

So port the **dsh gate**, and use ZCode's read-state map only for the parts
dsh does not have — the persistence-across-resume metadata
(`apps/zcode-cli/packages/core/src/tool/read-file-state-metadata.ts`), which is the piece that lets the guard
survive a restart, and the `isPartialView` distinction.

```python
@dataclass
class ReadState:
    path: str
    content: str
    offset: int | None
    limit: int | None
    is_partial_view: bool
    read_at: float
    source_tool: Literal["read_file", "write_file", "edit_file", "python"]
    revision_id: str
    mtime_ms: int
    size_bytes: int
```

Rules, each of which ZCode learned the hard way:

- **Record state from writes and edits too, not just reads**
  (`…/tool/read-file-state-metadata.ts:11-17`) — an inline draft or a full-file write
  *is* a full view of that path. MatOpt's `write_file` should mark the file as
  current.
- **Validate on `mtime_ms` + `size_bytes` + `revision_id`, never on presence.**
  ZCode refuses to emit state metadata at all when the freshness fields are
  missing: "a historical file state without freshness metadata must not
  masquerade as read, or it would let a stale write through"
  (`…/tool/read-file-state-metadata.ts:87-92`).
- **Count `python`/`bash` file access.** ZCode feeds bash reads into the same
  map (`handlers/bash-read-file-sources.ts`). For MatOpt the critical case is
  the persistent Jupyter kernel — a cell that writes `data.csv` must
  invalidate any prior read of it, which means the kernel's post-execute
  `changed_since()` diff (already computed, `workspace.py:126-139`) should
  feed the read-state map rather than being discarded.
- **Resolve to the latest read of a path, not the "best" one.** ZCode's
  comment records that preferring a full view over a ranged one caused
  *persistent false stale reports* after bash or a formatter touched the file
  (`…/tool/read-file-state.ts:46-49`). Store every read; compare against the newest.
- **Persist the metadata into the tool result and re-parse on resume**, so
  the guard survives a restart.
- **`is_partial_view` is not the same as a range read.** `offset`/`limit`
  produce a *range* view, which is fine to edit from; a *partial* view means
  the tool itself truncated what the model saw, and `Edit`/`Write` must reject
  it. ZCode sets the flag only from `truncatedByTokenCap === true`
  (`read.ts:371-373`). Conflating the two either blocks legitimate edits or
  permits edits against content the model never fully saw — MatOpt's
  `read_file` has both `start_line`/`max_lines` and a truncation cap, so it
  will hit this immediately.
- **Add the cheap dedup short-circuit.** If a read finds an entry for the same
  path that is still fresh, return `file_unchanged` rather than the content
  again, with the model-facing text ZCode uses: "Wasted call — file unchanged
  since your last Read. Refer to that earlier tool_result instead." Given
  MatOpt's read-heavy math workflow, this removes a recurring class of
  wasted turns.
- **Invalidate on compaction, and do not leak across subagents.** ZCode clears
  the whole map when compaction runs
  (`runtime/methods/compact-active.ts:625`), so the model must re-read files
  whose content it can no longer see, and it hands a *subagent* a **copy** of
  the map (`runtime/helpers/project-memory-agent.ts:74`) so a child's reads
  never satisfy the parent's guard. Both matter for MatOpt once phase 6 lands.
- On failure, emit a structured, model-actionable error that names the file,
  why it is stale, and the remedy (re-read it) — not a bare refusal. ZCode's
  two exact strings are worth reusing:
  "File has not been read yet. Read it first before writing to it." and
  "File has been modified since read, either by the user or by a linter. Read
  it again before attempting to write it."
- **Match freshness on an *advanced* mtime, not a merely different one**
  (`>` rather than `!=`), and give a strict full read a content-equality
  escape hatch so an external touch with identical bytes is not treated as
  stale (`edit.ts:439-468`). Without this, a formatter that rewrites a file
  with identical content wedges the agent.

**Deliberately not ported: ZCode's permissive path policy.** It does not block
paths outside the workspace root, with the stated rationale that subagents may
need to inspect user-requested sibling repos before a filesystem permission
adapter grows ask/deny rules (`path-policy.ts:33-37`). MatOpt's
`Workspace.resolve()` containment (`workspace.py:90-103`) is correct for a
maths sandbox and stays exactly as it is. ZCode can afford permissiveness
because it has a permission broker; MatOpt has containment instead. Keep it —
but see §3.9, because that containment does not currently cover the kernel.

### 3.9 Phase 8 — The agent action environment (containers)

This is the phase where MatOpt is furthest behind, and the gap is not one of
polish. Both upstreams were built by people who had thought about this; the
comparison is worth reading before writing any code.

#### 3.9.0 What MatOpt actually has today — the honest assessment

Four execution surfaces, and none of them is a boundary:

| Surface | Containment today | Reality |
|---|---|---|
| `python` tool → persistent Jupyter kernel | `cwd` only (`kernel.py:145`, `STARTUP_CODE` does `os.chdir`) | The kernel runs with the **full privileges of the host user**, inherits `os.environ`, and has unrestricted filesystem and network access |
| `bash` tool | 14-substring deny-list (`shell_tool.py:16-19`) + `cwd` | `BLOCKED_PATTERNS` is substring matching on `command.lower()` — trivially bypassed, and it is the *only* gate. Also inherits the full environment |
| `read_file`/`write_file`/`edit_file` | `Workspace.resolve()` containment (`workspace.py:90-103`) | The one real boundary, and it holds — but it is **advisory**, because the kernel bypasses it |
| `parse_document`, `lambda_data_agent` | none | subprocess / vendored backend with the same ambient authority |

Two consequences that matter more than the missing sandbox:

- **`env = dict(os.environ)` is passed to both the shell and the kernel**
  (`shell_tool.py:61`, `kernel.py:421`). MatOpt's own
  `.env` holds `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `QWEN_API_KEY`,
  `WOLFRAM_APP_ID`, `TAVILY_API_KEY`, `BRAVE_SEARCH_API_KEY`. The agent can
  read its own API keys with a one-line `python` cell. Nothing in the current
  design prevents it, and prompt injection is sufficient to cause it.
- **`Workspace.resolve()` containment is bypassable by construction**, because
  the kernel is not subject to it. Path policy on the *file tools* is not a
  boundary for the *kernel*, and the kernel is the tool MatOpt tells the model
  to prefer for computation ("Prefer the python tool for computation").

So MatOpt today is a harness where the model's most-used execution surface has
no containment at all.

#### 3.9.1 What dsh does — same-world confinement, honestly reported

dsh explicitly does **not** provide a container. `SAFETY.md` says so, and
`docs/subsystems/sandbox.md` is blunt: "This is same-world confinement: the
process still shares the host kernel and filesystem; **use a container,
microVM, or remote executor when the whole environment must be isolated**."

What it does provide is a well-designed *policy* layer, and six properties of
it are worth copying regardless of whether MatOpt ever gets a container:

1. **Three modes with narrow scope.** `SandboxMode = 'read-only' |
   'workspace-write' | 'danger-full-access'` governs **filesystem effects
   only** — the doc states plainly that "network and process visibility are
   outside this vocabulary." That honesty matters: it stops anyone believing
   the sandbox contains egress.

   A deep read confirms how narrow this really is, and it is worth stating
   precisely because it reframes what MatOpt would be buying. The profiles
   confine **writes only**: bwrap mounts the whole host root `--ro-bind`, so
   **reads of the entire host are available** (keys, `~/.ssh`, other
   workspaces); Landlock grants `readOnly: ['/']`; Seatbelt is
   `(allow default)` with only `file-write*` denied. There is **no
   `--unshare-net` anywhere in the repository** — grep for `unshare-net`,
   `network-outbound`, or `deny network` returns zero hits. And confinement
   only applies to what goes through a sandboxed *shell executor*: ripgrep
   for `grep`/`glob` is explicitly unconfined, LSP servers, the Codex and
   Claude-Code subagent CLIs, and MCP stdio servers (spawned by the MCP SDK,
   not even through `ctx.subprocess`) all run with the harness's full
   authority.

   So dsh's actual guarantee is: *"the OS prevented this write"* for commands
   through a sandboxed shell, and *"a human or a fail-closed default was
   consulted"* for everything else. Reads, network, process visibility, and
   every other spawner are governed by nothing. MatOpt should not mistake
   porting this for gaining containment — it is a policy layer, and the
   container in Tier 3 is what would actually bound reads and egress.
2. **Fail closed against silent passthrough.** `ctx.sandbox.confine(argv,
   policy, signal)` returns a `ConfinedArgv` or rejects with
   `SANDBOX_UNAVAILABLE`. "Silent unconfined passthrough is never legal for a
   confined policy." A deployment whose sandbox is broken **stops working**
   rather than quietly becoming unprotected.
3. **Enforcement is a reported fact, not a claim.** `SandboxEnforcement =
   'full' | 'partial'`, because older Landlock ABIs and the Windows ACL runner
   cannot govern every promised effect. Callers that need an absolute boundary
   "must not treat it as `full`."
4. **Denial vs. infrastructure failure are distinguished by backend-specific
   dialect.** `denialSignatures` are per-backend (EROFS under bwrap, EACCES
   under Landlock, EPERM under Seatbelt) rather than a cross-backend union,
   because a union "claims denials a given backend never produces."
   `runnerFailureRules` identify the *runner* dying before the command ran, so
   a broken sandbox is reported as infrastructure failure, "never an ordinary
   task failure."
5. **Policy is per call, not per provider.** `SandboxPolicy` carries mode +
   workspace root + session id on every call, so two sessions can confine under
   different policies simultaneously and an approved escalated retry is simply
   a new call with a wider policy.
6. **Escalation is one strictly-wider step, human-approved.** After a denial
   the model may request a wider mode, which goes to approval.

dsh's specific backends: Linux `bwrap`/Landlock, macOS Seatbelt, Windows ACL
restricted token. The Windows one grants capability SIDs, denies the ambient
parent-directory delete right, and applies a Low integrity label — and still
admits its guarantee is partial because hard links alias file objects.

**Approval is a closed set** (`docs/subsystems/approval.md`):
`ApprovalOutcome = 'allowed-once' | 'rejected' | 'cancelled' | 'unavailable'`,
and "callers deny on `rejected`, `cancelled`, and `unavailable`." A missing,
non-owning, throwing, or non-conforming answerer becomes `unavailable` —
failing closed rather than opening the gate. The per-session policy is
`'ask' | 'never'`, where `never` resolves every ask to `rejected`
deterministically **without dispatching any answerer**, and that is enforced
inside the service so a later `prepend`-registered answerer cannot bypass it.
`allowed-once` grants only the action asked about — there is no "allow always"
in the vocabulary, which is why ZCode's persisted rules are a separate
mechanism rather than a fifth outcome.

#### 3.9.2 What ZCode does — command classification and total-access modes

ZCode's layer is broader and more product-shaped: a permission broker with
modes, persisted rules, *suggested* rule updates from a denial, and a
genuinely serious read-only command classifier. Three properties of that
classifier are worth naming, because together they are the best-designed
piece of security engineering in either upstream — and all three are
reusable without any container:

1. **It is a real shell parser, not a pattern matcher.**
   `bash-command-parser.ts` parses with `unbash` into an AST and walks it,
   collecting every invocation, its operator context (`&&`, `||`, `|`,
   sequence), redirects, and env assignments. It is explicitly not a regex
   splitter, and it **fails closed on anything it cannot fully model**: a
   command over 10,000 chars, a parse error, any unsupported AST node
   (subshell, `if`/`for`/`while`/`case`/function), or a trailing `&` all make
   the whole command non-read-only.
2. **Any dynamic word anywhere disqualifies it.** `$(…)`, `<(...)`,
   `$((…))`, `$VAR`, brace/glob expansion, and *any unknown word-part type*
   (the default branch returns "dynamic") mark the command unsafe — with the
   stated reason that "command substitution and process substitution execute
   *before* the main command, so permission checking must not treat them as
   ordinary argv." Unknown word-part types defaulting to *unsafe* rather than
   *safe* is the single most important line in the classifier.
3. **Unknown means refuse.** The evaluation returns a tri-state — `true`
   known read-only, `false` known bad, `undefined` unknown — and **the caller
   treats `undefined` as `false`.** There is an allowlist of 47
   any-argument-safe commands plus per-command flag tables; an unrecognized
   binary, an unlisted flag, a non-allowlisted env assignment (a 44-name
   list), or an output redirect to a real file all fail closed. There is no
   default-allow path and no caching of verdicts.

Two further mechanisms are worth porting independently of the classifier:

- **Risk is lowered per call, not declared once.** `Bash` is registered as
  maximally untrusted (`readOnly: false`, `sideEffectScope: "system"`,
  `riskLevel: "high"`, `needsApproval: true`) and then, for a command the
  classifier accepts, `resolvePermissionCapability` **demotes it on that
  call** to `readOnly: true`, `sideEffectScope: "none"`, `riskLevel: "low"`,
  `needsApproval: false`. Critically, that same resolved verdict is published
  on the `ToolCallStarted` event *before* the handler runs, so anything
  watching — the scheduler, a workflow driver, the UI — learns a write is
  coming without guessing. This is the clean answer to "one tool, wildly
  varying risk", and MatOpt's single static `read_only` boolean cannot express
  it: MatOpt's `bash` is `read_only = False` forever, so phase 7b's scheduler
  must serialize every `bash` call even when it is `ls`.
- **Only the mutating legs of a compound command need approval.**
  `resolveBashPermissionRulePolicy` computes `requiredCommands` by filtering
  out every invocation the classifier proved read-only, so `cd /x && git
  status` needs a rule for `cd` but not for `git status`. Allow-rules then
  require **all** mutating legs to be pre-approved (conjunction), while
  deny-rules need only one leg to match (disjunction) — the fail-closed
  direction in both cases.

There is also a real filesystem-level defence worth knowing about:
`bash-git-runtime-safety.ts` refuses to call a `git` command read-only when
the surrounding `.git` is untrusted — a `.git` *directory* is trusted only if
it has a valid `HEAD`, executable `objects/` and `refs/`, and **no**
`commondir`; a `.git` *file* must be a well-formed `gitdir:` pointer to a
target outside the cwd; bare-repo indicators anywhere up the directory chain
disqualify it. The reason is that `git status` otherwise loads hooks and
config from an attacker-controlled repository, so classifying it read-only
would be a direct code-execution path. MatOpt's `bash` has no equivalent and
its kernel can run `git` freely — another argument for Tier 3.

Its `path-policy.ts` deliberately does **not** block paths outside the
workspace root, justified by a permission broker catching what containment
would have caught. That trade is coherent for a coding agent and wrong for
MatOpt (§3.8, and see the non-port note below).

#### 3.9.3 What MatOpt should do — three tiers

The plan is deliberately tiered so the cheap, high-value work does not wait on
the expensive work.

**Tier 1 — stop leaking secrets, and stop lying about containment. (Days.)**

This is the highest value-per-line change in the entire plan, and it needs no
sandbox at all:

- **Stop passing the ambient environment to the kernel and the shell.** Build
  an explicit allowlist instead of `dict(os.environ)`: `PATH`, `HOME` (pointed
  at a per-conversation dir), `LANG`, `TZ`, `MPLBACKEND`, `PYTHONUNBUFFERED`,
  plus an explicit `MATOPT_*` set. Provider keys, `AWS_*`, `GITHUB_TOKEN`,
  `SSH_AUTH_SOCK`, and everything else stay out. If a tool genuinely needs a
  credential, it gets it through a narrow, audited port rather than through
  process inheritance.

  dsh solves exactly this and the solution is two lines to copy
  (`packages/subprocess/subprocess/src/index.ts:47,66-80`): it scrubs the
  ambient parent environment of credential-shaped names **and** all of its own
  namespaced variables, then merges an explicit `spec.env` *after* the scrub:

  ```ts
  export const SENSITIVE_ENV_PATTERN = /KEY|PASSWORD|SECRET|TOKEN/i
  // ambient parent env minus credential-shaped names and minus ALL DSH_* names;
  // explicit spec.env merges AFTER the scrub
  ```

  A deny-pattern scrub like that is a smaller diff than an allowlist and
  catches the common case; an allowlist is stronger. Do the allowlist, and use
  `SENSITIVE_ENV_PATTERN` as a belt-and-braces assertion that fails loudly if a
  credential-shaped name ever reaches the child environment.
- **Distinguish "no sandbox" from "sandboxed" in the UI and the prompt.** dsh's
  `SandboxEnforcement` idea, minimally applied: MatOpt should report the
  effective containment mode for the session and show it. A user who believes
  the kernel is jailed when it is not is worse off than one who knows.
- **Replace the `BLOCKED_PATTERNS` substring deny-list with an allow/approval
  decision, or delete it.** Fourteen substring patterns provide no security
  and create false confidence; `bash` is a full shell and `python` can do
  anything `bash` can. Either it becomes a real classifier (Tier 2) or it
  should not be presented as protection.

**Tier 2 — port dsh's policy seam and ZCode's classifier. (Weeks.)**

Two design lessons from ZCode's mistakes, both cheap to avoid and both
expensive to discover later. They come first because MatOpt is about to build
the same two things.

**Lesson 1: never advertise a boundary you do not enforce.** ZCode's
`ExecutionRequest` carries an `ExecutionSandboxPolicy`
(`{ enabled, profile?, dangerouslyDisableSandbox? }`), and the Bash tool's
schema tells the model:

> "Set this to true to dangerously override sandbox mode and run commands
> without sandboxing."

No adapter reads it. A repo-wide grep for `ExecutionSandboxPolicy` returns
**two hits — the declaration and the field**; the only `ExecutionPort`
implementation never consults it, and `ExecutionFailureType` even declares a
`"sandbox_violation"` member that no code path can produce. The flag survives
only as a telemetry label. That is worse than having no field at all, because
it teaches both the model and the user that a boundary exists. MatOpt must not
repeat this: if a containment mode appears in a tool schema or the system
prompt, something must enforce it, and when enforcement is unavailable the
call fails closed (§3.9.1) rather than proceeding.

**Lesson 2: `deny` must outrank `allow`, and a mode short-circuit must not
jump the queue.** ZCode evaluates permissions in a fixed order, and its
full-access mode returns early (`permission/service.ts:135-138`):

```ts
if (context.mode === "yolo" && !planEnabled) {
  return this.allow(context, capability, "mode.yolo", "Yolo mode bypasses permission prompts")
}
```

That returns **before** the `disallowedTools` check and **before** the project
`deny` check — so granting full access permanently disables the user's own
hard-deny configuration, for everything except the three tools that declare
`alwaysAsk`. The codebase knows; a comment acknowledges "especially that yolo
currently passes before disallowedTools."

MatOpt's containment mode must be a *widening* of what is permitted, never a
bypass of what is forbidden. Concretely: evaluate `deny` rules first and
unconditionally, then the mode, then `allow` rules — and treat
`danger-full-access` as "no confinement applied to the sandbox modes", not as
"skip the policy engine". This is a five-line ordering decision that becomes
nearly impossible to correct once users have written rules against the broken
order.

With those two settled, the port list:

- **A `SandboxPolicy` for MatOpt**: `mode ∈ {read-only, workspace-write,
  danger-full-access}`, per call, carrying the workspace root and session id —
  dsh's shape, which is the right shape.
- **Fail closed.** A confined run whose backend is unavailable raises
  `SANDBOX_UNAVAILABLE`; it must never silently run unconfined. This is the
  single most important line of the whole design.
- **Report enforcement honestly** (`full`/`partial`), and keep
  `denial_signatures` per backend so a denial is never confused with a broken
  runner.
- **Give the model one denial vocabulary, with the remedy in it.** dsh uses a
  single marker pair for both bash and fs, and the escalation line tells the
  model exactly what to retry
  (`packages/sandbox/sandbox/src/escalation.ts:71-86`):

  ```
  [sandbox: file access denied under <mode> mode]
  [sandbox: escalation available — retry this exact <subject> once with
   sandbox_permissions (the narrowest wider mode that suffices) + justification;
   the approval prompt asks the user]
  ```

  and separately, for an infrastructure failure rather than a policy denial:

  ```
  [sandbox: the sandbox runner itself failed under <mode> mode — the command
   did not run; this is a sandbox problem, not a command failure]
  ```

  That three-way distinction — *policy denied you*, *you may ask for more*,
  *the sandbox is broken* — is exactly what stops a model from thrashing,
  retrying a denied command, or reporting a sandbox outage as a task failure.
  MatOpt's current `"Command refused: contains blocked pattern ..."` collapses
  all three into one unactionable sentence.
- **One exit from the kernel question.** The kernel must be *confined or
  absent*. While the kernel is unconfined, every file tool's containment is
  bypassable, so the honest options are: (a) run the kernel inside the
  container (Tier 3), or (b) gate the kernel behind the same approval as
  `danger-full-access`, so the user knows when they are exposed.
- **Port ZCode's read-only command classifier** for the approval decision
  (`bash-readonly-policy-*`), since it also feeds `concurrentSafe` in phase 7b.
  That is one mechanism paying for two features. Port its three defining
  properties too, not just its tables: a real AST parse rather than regex
  splitting, **dynamic words default to unsafe**, and **unknown resolves to
  refuse** (§3.9.2).
- **Make capability per call, not per tool.** This is the piece that makes
  both tiers work: `bash` should be able to demote itself to
  `read_only: true, side_effect_scope: "none", risk_level: "low",
  needs_approval: false` for a command the classifier accepts, and publish
  that resolved verdict before the handler runs. Without it, phase 7b's
  scheduler must serialize every `bash` call — including `ls` — and MatOpt's
  single static `read_only` boolean stays the ceiling on how good both the
  scheduler and the approval flow can get.

**Tier 3 — an actual container for the agent action environment. (The real fix.)**

Both upstreams point here and neither ships it. For MatOpt it is unusually
well-suited, because the workspace is already the unit of isolation:
`<DATA_DIR>/workspaces/<conversation_id>`, with `uploads/`, `figures/`,
`parsed/`, and a copy of any materialized skill. A container per conversation
is a natural fit.

**Start by hardening the container you already have.** To its credit, the
image already drops to a non-root user (`Dockerfile:35` — `useradd -m matopt`
then `USER matopt`), which is the single most important container-hygiene
measure and more than many projects do. But `docker-compose.yml` declares
**no** `mem_limit`, no `cpus`, no `pids_limit`, no `read_only`, no `cap_drop`,
and no network policy. So the deployment runs the agent's shell and kernel as
an unprivileged user inside a container that nevertheless has all Linux
capabilities, unbounded CPU/memory/PIDs, a fully writable filesystem, and open
egress — and `env = dict(os.environ)` still puts the provider keys inside it.
The container exists; it is simply not containing anything. This is the
cheapest Tier-3 work and it is a handful of compose keys:

```yaml
services:
  backend:
    read_only: true
    tmpfs: ["/tmp"]                     # writable surface is the data volume
    cap_drop: ["ALL"]
    security_opt: ["no-new-privileges:true"]
    mem_limit: 4g
    pids_limit: 512
    cpus: 4
    networks: ["agent"]                 # internal-only by default
networks:
  agent:
    internal: true                      # no egress; add a proxy for opt-in
```

Note that `read_only: true` requires the workspace to live on a named volume
or bind mount, which MatOpt's `DATA_DIR=/app/data` already supports.

Then the substantive change, which is *where* actions execute rather than
*how* the container is configured:

- **One container per conversation**, session-scoped, with the workspace
  bind-mounted at a fixed path. `python` (the Jupyter kernel) and `bash` both
  execute inside it. The kernel becomes a *client* of the container rather
  than a child of the backend process.
- **Filesystem**: read-only root, read-write on the workspace only.
  `uploads/` read-only after ingest; `figures/`/`parsed/` writable.
- **Network**: default **deny**, with an explicit per-session opt-in. This is
  the containment property neither upstream provides, and for a maths
  workbench it is both achievable and valuable — a symbolic derivation needs
  no egress. It also closes the exfiltration path that prompt injection
  needs. Note that dsh's `SandboxMode` explicitly excludes network from its
  vocabulary, so this is a place where MatOpt would go *beyond* both
  upstreams rather than catching up.
- **Resources**: CPU, memory, PID, and wall-clock caps; a disk quota on the
  workspace. Today `agent_max_steps` bounds *model* calls and nothing bounds
  the kernel.
- **No credential inheritance by construction.** The container simply does not
  receive the host environment — Tier 1's allowlist becomes the container's
  `env:` block, enforced by the runtime rather than by Python code.
- **The container is the wrong tool for one thing**: MatOpt's value depends on
  the scientific stack (numpy, scipy, sympy, pandas, matplotlib, plotly,
  scikit-learn) and on a *warm* kernel across turns. A cold container per turn
  would destroy the persistent-kernel feature. So: **container per
  conversation, long-lived, with the kernel inside it** — not per turn, and not
  per tool call.

**One upstream idea worth taking even here:** dsh distinguishes `full` from
`partial` enforcement and reports which one you got
(`docs/subsystems/sandbox.md:30`). A container with `--network=none` and a
read-only root is a *stronger* boundary than anything either upstream ships,
but it still is not a microVM — a kernel exploit crosses it, and a bind mount
of the workspace means the agent can persist changes there. Report that
honestly rather than calling it isolation.

**Non-port, restated:** MatOpt keeps `Workspace.resolve()` containment. ZCode's
permissive path policy is justified by its permission broker and its
sibling-repo workflows; MatOpt has neither and should not adopt it. The two are
complementary, not alternatives — MatOpt wants containment *and* a policy
layer, and today it has containment on the tools that are not the problem.

#### 3.9.4 Sequencing note

Tier 1 is independent of every other phase and should ship **first**, before
the log work — it is days of effort and it closes a credential-exposure hole.
Tier 2 slots beside phase 7 (same tool-contract and scheduler surface). Tier 3
is a project of its own and should be its own effort after phases 2–7 land,
because the container changes *where* the kernel, the shell, and the file
tools execute, which is exactly the surface phases 2 and 7 are rewriting.


**Later, out of scope for this phase** (see Part 2.7 and the reference
reports): background tasks with buffered, replayable output; the
dynamic-workflow product; subagent coordination; the permission broker with
persisted rules and suggested updates; and the system-reminder mechanism.
These are all genuinely valuable and all substantially larger than 7a–7d.

### 3.10 Phase 9 — Long-running work: stop killing it

This phase is small, comes late, and fixes the thing MatOpt users will notice
most in a maths workflow: **a long computation currently gets destroyed.**

**The problem.** `bash` kills the process group when it hits its timeout
(`shell_tool.py`, `killpg`), and the kernel interrupts a cell that exceeds
`tool_timeout`. For a symbolic solve, a parameter sweep, or a
cross-validation grid — exactly MatOpt's workload — the correct outcome is
"this is still running", not "this is gone". The model then has to start over,
and the user pays for the same tokens twice.

**What ZCode does** (verified, reads at the pin): both explicit
`run_in_background` and *automatic* background-on-timeout go through one port
method, `runBashWithBackgroundLifecycle(request, { mode: "explicit" |
"auto_on_timeout" })`, returning a discriminated union
`{kind:"foreground", result} | {kind:"backgrounded", task}`. Output goes to
**files, not memory**; a running task is read as a **head** (30 000 bytes)
rather than the generic tail read, because "tail-reading a running command
shows the model the wrong end of the log"; and terminal notifications carry a
per-stop-reason decision table so the model cannot improvise a resume of a run
the user cancelled.

**What to build, in order:**

1. **Auto-background rather than kill.** Extend the existing `Tool.background`
   field — which today is **set on `web_search`/`web_fetch` and read by
   nothing** (§3.1 gap 11) — into a real capability. On timeout, if the tool is
   background-eligible, transition the running process to backgrounded and
   return a task id with a head of the output, instead of `killpg`.
   Eligibility stays deliberately crude, as upstream's is: not already
   backgrounded, non-empty command, and not a bare `sleep`.
2. **Stream output to a file, tail-bounded on read.** MatOpt's `bash` and
   `python` tools already stream stdout; redirect it to a per-conversation
   file under the workspace rather than buffering in memory, and read a
   bounded head while running.
3. **A `task_output` / `task_stop` tool pair.** `get` returns status plus a
   bounded, tail-preserving excerpt with a pointer to the full file. `stop`
   records **who asked** (`initiator: "user" | "model"`) before cancelling, so
   the notification can say "stopped by you" rather than blaming the user.
4. **The completion notification is a first-class artifact.** It is injected as
   a system notification, not as user input — upstream prefixes it
   `[SYSTEM NOTIFICATION - NOT USER INPUT]` with an explicit "do not interpret
   this as user acknowledgement" — and it states the next action per stop
   reason: `errored` is **not** resumable (replaying the same script fails the
   same way), `interrupted` usually is, `user`-cancelled must not be resumed.
5. **Host faults stay resumable; task failures do not.** This is the same
   distinction as §3.9's, applied to long work: a crash or spawn failure
   settles as `stopped(interrupted)` and is resumable, while a script error
   settles as `errored` and `resume` **refuses** it rather than repeating a
   known-bad computation. MatOpt's `stopped` turns are currently dead ends —
   `Database.update_message` and `delete_messages_from` exist with zero callers
   — and this distinction is what makes wiring them up safe.

**Why this is Phase 9 and not earlier.** It depends on phase 2 for the task
lifecycle to be logged (a background task that outlives a turn must survive a
reload, which only the durable log gives) and on phase 3 for the trajectory
view to show a backgrounded call as an open record rather than a hang. It does
**not** depend on the container work, and it is independent of phase 8.

**Explicitly not in this phase:** ZCode's cron/idle-time automation and the
dynamic-workflow product. Both are real, both are documented in the reference
maps, and both are larger than everything above. If MatOpt ever gains
scheduled work, the one design note worth carrying forward is that **neither
ZCode subsystem implements its own timer** — each hands a schedule to a port
and gets back a record with `nextRunAt`/`lastRunAt`/`runCount`. That
separation is what keeps a clock out of the agent loop.

---

## Part 4 — Sequencing, risk, and non-goals

### 4.1 Order and dependencies

```
Phase 1  design foundation ──────────────┐  (independent; ship first)
Phase 2  durable session log ──┬─ Phase 3  trajectory view
                               ├─ Phase 4  token / cost / stats ── Phase 5  reports & deliverables
                               ├─ Phase 6  compaction
                               └─ Phase 7  harness upgrades (ZCode)
```

- **Phase 1 is independent** and can start immediately; it is where the
  visible win is.
- **Phase 2 gates 3–7** and is the only change that touches `harness.py`
  structurally. Do it behind a flag (`session_log_enabled`, default on in
  dev) so the SSE path in `docs/PROTOCOL.md` never breaks.
- Phases 3 and 4 can run in parallel once 2 lands; both are read-only
  consumers of the log.
- Phase 6 needs phase 4's measurements to be trustworthy.
- **Phase 7 depends on phase 2 for one specific reason**: the read-before-edit
  guard's freshness metadata is persisted *into the tool result* so it
  survives a restart (ZCode `read-file-state-metadata.ts`). Without the log
  there is nowhere to put it. The other three parts of phase 7 — the tool
  contract, the scheduler, and input normalization — are independent of the
  log and could ship earlier if you want them sooner.

### 4.2 Rough sizing

| Phase | Backend | Frontend | Tests |
|---|---|---|---|
| 1 | — | ~1400 LOC (tokens, 8–12 primitives, shell split) | ~250 |
| 2 | ~800 LOC | — | ~350 |
| 3 | ~300 (paged endpoints + fold) | ~1100 | ~350 |
| 4 | ~600 | ~450 (usage chip + dialog, stats panel) | ~400 |
| 5 | ~500 | ~600 | ~250 |
| 6 | ~450 | ~150 (compaction boundary row) | ~300 |
| 7 | ~700 | ~150 (parallel-group display, edit-stale error card) | ~450 |

### 4.3 Risks

| Risk | Mitigation |
|---|---|
| `harness.py` is an 883-line god-object; phase 2 could destabilise it | Log writes are additive `await self.log.append(...)` calls beside existing emits; extract `_run_inner`/`_agent_loop` into a `TurnRunner` in the same PR but keep behaviour byte-identical, proven by golden-log tests |
| Double source of truth (log + `messages`) drifts | Phase 2 keeps `messages` as a view written from the log; add a debug consistency assertion; retire `messages` reads in phase 3 |
| Token counts are approximate → misleading cost | Follow dsh exactly: only show exact numbers when the lifecycle evidence is complete; label heuristic-derived figures as estimates; never blend the two in one number |
| Dark mode regressions across 17 CSS files | Port tokens first, keep both themes on `body[data-theme]`, add a stylesheet-scan test that bans literal colors in feature CSS |
| Scope creep into porting the whole dsh client | `ui-primitives` is a catalogue, not a dependency. Port the ~12 controls MatOpt actually uses; do not port `ui-settings-*`, `ui-dockkit`, `ui-schedule` |
| React 19.2 app with `@types/react@18` in `frontend/package.json` | Fix in phase 1: bump `@types/react`/`@types/react-dom` to 19 before component work |
| Phase 7b's scheduler adds a dependency graph the model may never populate | Ship it with an empty `dependsOn` for every call — behaviour is then identical to today's heuristic plus a concurrency cap, and dependencies become an opt-in a tool or a hook can add later |
| Phase 7d's read-state guard produces false "stale" refusals and blocks legitimate edits | Copy ZCode's two hard-won details verbatim: resolve to the **latest** read of a path rather than preferring a full view, and feed kernel/bash writes into the map so the agent's own `python` cell does not silently invalidate what it just wrote |
| Mixing two upstream design philosophies | Part 2 already commits to dsh's role-based radius table and rejects ZCode's container-nesting law; do not import `text-ui-*` alongside `--mo-*` |
| Porting ZCode breadth instead of depth | ZCode is a 1,200-file agent with a marketplace, TUI, Electron shell and workflow product. This plan ports **four mechanisms**, not a product. Treat anything beyond 7a–7d as a separate initiative |

### 4.4 Non-goals

- No TypeScript rewrite of the backend, no Cordis, no plugin/profile system.
- No Electron/desktop packaging, no plugin marketplace, no CDN install flow,
  no TUI, no RPC framework.
- No multi-generation session-format codecs; one version plus `user_version`
  migrations.
- No background-task system, dynamic-workflow engine, subagent coordinator, or
  permission broker in this effort — they are the right *next* initiative
  after phases 2–7, not part of them.
- No replacement of KaTeX / react-markdown / plotly; dsh's markdown stack is
  not better for MatOpt's math use case.
- No relaxation of `Workspace` path containment (see §3.9).

### 4.5 First commits

1. `frontend: three-tier design tokens + dark theme` — new
   `tokens.css`/`base.css`/`elevation.css`/`corner-shape.css`/`focus.css`/
   `scrollbar.css`, mechanical `--oai-*` → `--mo-alias-*` rename across the 17
   component sheets, `@types/react@19` bump. No behaviour change.
2. `backend: durable session event log` — `app/agent/session/{events,log,store,
   derive,project,repair}.py`, `session_events` + `session_usage` tables,
   harness wiring behind `session_log_enabled`, golden-log tests.
3. `backend: read-before-edit freshness guard` — `tools/read_state.py`, wired
   into `read_file`/`write_file`/`edit_file` and fed by the kernel's
   `changed_since()` diff. Independent of commits 1–2 and closes a live
   correctness hole in `edit_file`.
4. `frontend: ui primitives + shell split` — `src/ui/` primitives ported from
   `ui-primitives`, `ChatPage` decomposed into `shell/` components,
   `ModelPicker`/`SkillCommandMenu`/header menu migrated onto `Menu`.
