# dsh trajectory / session-log / telemetry / token-usage map

Repo: `/Users/eric/projects/deepseek-harness` (TypeScript/pnpm monorepo, Cordis plugins).
Everything below was read from source; quoted text is verbatim from the file cited.

---

## 0. One-paragraph orientation

`Session` is an **append-only log of typed `SessionEvent`s** and nothing else is authoritative.
LLM message history, token accounting, titles, outlines, telemetry, persistence and transcripts are
all *derived folds* over that log. The doctrine is stated at
`packages/core/session/README.md:88`:

> The package is built on event sourcing: a `Session` is an append-only log of typed `SessionEvent`s,
> and everything else — model history, transcripts, telemetry, titles, persistence — derives from that
> stream. […] **Model-visible means logged**: anything that reaches a model request must be
> reconstructable from the log.

The in-memory log never blocks on I/O (`packages/core/session/src/index.ts:685-773`); durability is a
separate seam (`ctx.sessionPersistence`) that subscribes to `session/event` and drains on
`session/flush`.

---

## 1. Event vocabulary

### 1.1 Where it is defined

| Thing | File:line |
|---|---|
| `SessionEventMap` (core, 13 members) | `packages/core/session/src/types.ts:281-428` |
| `SessionEvent<T>` envelope | `packages/core/session/src/types.ts:493-516` |
| `SurfaceEventType` (5 message-producing types) | `packages/core/session/src/types.ts:439-444` |
| `SurfaceOp` | `packages/core/session/src/types.ts:462-464` |
| `SurfaceIntent<T>` (append() options) | `packages/core/session/src/types.ts:470-478` |
| `TurnEndReasonMap` | `packages/core/session/src/types.ts:201-232` |
| `EpochHeader` | `packages/core/session/src/types.ts:240-251` |
| `RequestContext` | `packages/core/session/src/types.ts:254-263` |
| `RequestHeaderReason` | `packages/core/session/src/types.ts:273` |
| `SessionHeader` | `packages/core/session/src/types.ts:94-131` |
| `SESSION_FORMAT_VERSION = 4` | `packages/core/session/src/types.ts:89` |
| `KNOWN_SESSION_EVENT_TYPES` (all 56 first-party types) | `packages/core/session/src/known-event-types.ts:29-77` (generated) |
| Full generated catalog with resolved payloads | `docs/persistence-catalog.md` (8780 lines, generated) |

`SessionEventMap` is **merge-extensible**: plugins add members with
`declare module '@deepseek-ai/dsh-session/types' { interface SessionEventMap { ... } }`.
There are 40+ such declaration sites (list in §1.5).

### 1.2 The envelope

```ts
export type SessionEvent<T extends SessionEventType = SessionEventType> = {
  [K in SessionEventType]: {
    type: K
    seq: SessionSeq            // monotonic, == log index, contiguous from 0
    time: number               // Unix epoch ms
    data: SessionEventMap[K]
    /** Marks an event a reader may safely skip when it does not recognize `type`. */
    ignorable?: true
  } & (K extends SurfaceEventType ? SurfaceIntent<K> : {
    surfaceOp?: never
    sourceEventSeqs?: never
  })
}[T]
```

* `seq = log.length` is an invariant — the constructor rejects a seed whose `seq !== index`
  (`packages/core/session/src/index.ts:576-578`).
* `ignorable` is the **forward-compatibility mechanism**: an unknown type *without* `ignorable: true`
  must make a reader refuse the whole session rather than silently dropping it. Rationale in
  `packages/core/session/src/known-event-types.ts:1-27`.
* Surface events **must** carry `surfaceOp`; log-only events must not carry `surfaceOp` or
  `sourceEventSeqs` (enforced at `packages/core/session/src/surface.ts:308-333`).

### 1.3 Core event payloads (verbatim field lists)

| Event | Payload | Surface? | Notes |
|---|---|---|---|
| `turn/start` | `{ turn: number }` | no | opens turn before claiming queued input |
| `turn/end` | `{ turn: number; reason: TurnEndReason }` | no | closes turn |
| `step/start` | `{ turn: number; step: number }` | no | one model call + the tool executions it requested |
| `step/end` | `{ turn: number; step: number }` | no | appended exactly once per entered step, in a `finally` |
| `user/message` | `UserMessage` (i.e. `{ id, role:'user', content: ContentBlock[], source: MessageSource }`) | **yes** | human prompt, injected context, or goal continuation round |
| `developer/message` | `{ turn, step, message: DeveloperMessage, headerSeq?: SessionSeq }` | **yes** | `headerSeq` required exactly when tool *additions* are present |
| `system/message` | `{ turn, step, message: SystemMessage }` | **yes** | rendered system prompt; surface node 0 |
| `assistant/message` | `{ turn, step, message: AssistantMessage, stream: AssistantStreamRecord[], usage?: TokenUsage, interrupted?: true }` | **yes** | **carries the token accounting**; forbids `sourceEventSeqs` |
| `assistant/attempt` | `{ turn, step, stream: AssistantStreamRecord[] }` | no | failed/retried/cancelled attempt that committed no surface message; usage only inside its stream |
| `tool/call` | `{ turn, step, callId: ToolCallId, name: string, arguments: string }` | no | `arguments` is the raw JSON string exactly as the model produced it |
| `tool/result` | `{ turn, step, message: ToolResultMessage, error?: {name,code,reason?}, meta?: JsonValue }` | **yes** | `error` allowed only when `message.isError === true`; `meta` opaque tool presentation payload |
| `request/header` | `{ header: EpochHeader, reason: RequestHeaderReason, startsSeries?: true }` | no | latest snapshot reconstructs the request envelope |
| `request/context` | `RequestContext` = `{ provider, model, contextWindow?, systemPromptUpdate? }` | no | route capacity; logged only when route/capacity/mode changes |
| `session/end-seed` | `{ inherited?: true }` | no | separates inherited/restored history from live work; **the `inherited: true` marker is where the fork cut is recorded** |

`TurnEndReasonMap` variants: `completed`, `aborted { reason: TurnEndCancelCause }`, `blocked`,
`error { error: LlmFailure }`, `max-tokens`, `interrupted`, `forked`.
`TurnEndCancelCause = AgentCancelCause | { kind: 'legacy' }` and
`AgentCancelCause = {kind:'user'} | {kind:'parent'} | {kind:'hook',reason} | {kind:'disposed'}`.
The loop emits neither `interrupted` (crash repair) nor `forked` (fork-seed construction) live.

```ts
export interface EpochHeader {
  config: LlmCallConfig              // {provider, model, reasoningEffort?, temperature?, maxTokens?, stop?}
  adapterDefaults?: LlmCallConfigAdapterDefaults   // {reasoningEffort?: true, maxTokens?: true}
  tools?: ToolSchema[]               // absent for a tool-less request; `tools: []` is rejected
  system?: never                     // retired: system prompts are system/message surface nodes
}
```

### 1.4 Message / stream / content types (referenced by payloads)

| Type | File:line | Shape |
|---|---|---|
| `TokenUsage` | `packages/llm/llm/src/types.ts:175-192` | `{ inputTokens, outputTokens, totalTokens?, cacheReadTokens?, cacheWriteTokens?, reasoningTokens? }` — **counts are DISJOINT**; `inputTokens` is uncached input only, billed input = input + cacheRead + cacheWrite |
| `StreamChunk` | `packages/llm/llm/src/types.ts:452-464` | `block-start \| text-delta \| reasoning-delta \| tool-call-delta \| block-end \| { type:'usage'; usage: TokenUsage } \| { type:'finish'; reason: FinishReason; replayState?: ReplayEnvelope }` |
| `AssistantStreamRecord` | `packages/llm/llm/src/assistant-stream.ts:20-47` | `{type:'text-chunks',time0,index,dt:number[],texts:string[]}` \| `{type:'reasoning-chunks',…}` \| `{type:'tool-call-chunks',time0,index,dt,id,name?,args:string[]}` \| `{type:'chunk',time,chunk}` |
| `MessageBase` | `packages/llm/llm/src/message.ts:141-149` | `{ id: MessageId; content: readonly ContentBlock[]; source: MessageSource }` |
| `MessageSourceMap` | `packages/llm/llm/src/message.ts:110-116` | `user:{kind:'user'}`, `model:{kind:'model',provider,model,replayState?}`, `tool:{kind:'tool',callId}`, `'system-prompt':{kind:'system-prompt'}` (merge-extensible) |
| `ContentBlockMap` | `packages/llm/llm/src/types.ts:137-145` | `text`, `reasoning`, `image`, `file`, `tool-call`, `tool-addition`, `tool-removal` |
| `FinishReasonMap` | `packages/llm/llm/src/types.ts:156-165` | `stop`, `tool-calls`, `max-tokens`, `aborted{failure}`, `error{failure}` |
| `LlmCallConfig` | `packages/llm/llm/src/call-config.ts:23-33` | `{provider, model, reasoningEffort?, temperature?, maxTokens?, stop?}` |
| `LlmModelContext` | `packages/llm/llm/src/types.ts:344-347` | `{ contextWindow: number }` — "Maximum combined request and response context in tokens" |

### 1.5 Plugin-contributed (log-only) events

`KNOWN_SESSION_EVENT_TYPES` (`packages/core/session/src/known-event-types.ts:29-77`) lists exactly
these 56 types. Core 13 are above; the rest, with payload and declaring file:

| Event | Payload (verbatim fields) | Declared in |
|---|---|---|
| `agent/inbox/spliced` | `{ target: InboxTarget; start: number; removedCount?: number; inserted: UserMessage[]; outcome?: 'canceled' }` | `packages/core/agent/src/types.ts:230` |
| `tool/ptc-dispatch-start` | `PtcDispatchStartEventData` (parent `run_code` call id, opaque `subCallId`, tool `name`, JSON-normalized `arguments`) | `packages/core/tools/src/types.ts:28` |
| `tool/ptc-dispatch` | `PtcDispatchEventData` (pairing ids, name, args, `content`+`isError`+optional `error`) | `packages/core/tools/src/types.ts:28` |
| `llm/retry` | `{ retryId, turn, step, provider, mode:'normal'\|'always', policyKey, retry, maxRetries?, delayMs, failure: LlmFailure }` | `packages/llm/llm-retry/src/types.ts:7` |
| `llm/retry-started` | `{ retryId, turn, step, retry }` | same |
| `hook/invoked` | `{ turn, point: string, dialect: HookDialect, matcher?: string, handlerId: string }` | `packages/hooks/hook-protocol/src/types.ts:9` |
| `hook/result` | `{ turn, point, handlerId, decision: string, exitCode?, stderrSummary?, durationMs }` | same |
| `todo/write` | `{ todos: TodoItem[] }` (whole-list snapshot, latest wins) | `packages/todo/tool-todo/src/types.ts:29` |
| `workspace/changes` | `{ turn: number }` (summary held on host, served by `workspaceChanges.summary`) | `packages/deliverables/workspace-changes/src/types.ts:100` |
| `deliverables/presented` | `{ turn, callId: ToolCallId, files: PresentedFile[] }` | `packages/deliverables/tool-present/src/types.ts:13` |
| `session/title` | `SessionTitleEventData` = `{ title, messageSeqs: SessionSeq[], source: {kind:'fallback'}\|{kind:'provider',provider,model?}\|{kind:'user'} }` | `packages/session/session-title/src/index.ts:72` |
| `session/title-llm-request` | (title model call envelope) | `packages/session/session-title-llm/src/index.ts:50` |
| `session-log-deepseek/delivery-accepted` | `{ sessionId, sessionFormatVersion?, throughSeq }` | `packages/session/session-log-deepseek/src/types.ts:79` |
| `compaction/start` | `{ compactionId: CompactionId; sourceCommandId?: CommandId; turn: number \| null }` | `packages/compaction/compaction/src/types.ts:18` |
| `compaction/summary` | `{ compactionId, sourceCommandId?, summary: ContentBlock[], shadowedRange:{start,end}, shadowedSeqs: SessionSeq[], shadowedTokenCount: number, provider, model, maxTokens?, usage?: TokenUsage } & ({rawOutput: ContentBlock[]; llmStreamCall: true} \| {rawOutput?; llmStreamCall?: never})` | same |
| `compaction/end` | `{ compactionId, sourceCommandId?, turn: number \| null, error?: string }` | same |
| `compaction/prune` | `{ shadowedRange:{start,end}, shadowedSeqs: SessionSeq[], shadowedTokenCount: number }` | same |
| `image/offload` | `{ targets: ImageOffloadTarget[] }`, `ImageOffloadTarget = { seq: SessionSeq; imageIndexes: number[] }` | `packages/compaction/compaction-image-offload/src/projection.ts:17` (`@messageProjection`) |
| `plan/mode` | `{ active: boolean }` | `packages/plan/plan-mode/src/index.ts:47` |
| `sandbox/mode` | `{ mode: SandboxMode; source?: 'delegation' }` | `packages/sandbox/sandbox-policy/src/session-mode.ts:25` |
| `permission/preset` | `{ preset: string }` | `packages/interaction/permission-presets/src/index.ts:52` |
| `approval/asked` | `{ id: ApprovalRequestId; toolName: string; callId?: ToolCallId; reason?: string }` | `packages/interaction/user-approval/src/types.ts:35` |
| `approval/decided` | `{ id: ApprovalRequestId; outcome: ApprovalOutcome }` | same |
| `approval/policy` | (policy snapshot) | `packages/interaction/user-approval/src/index.ts:31` |
| `command/run` | `{ commandId: CommandId; name: string; args?: string; source: CommandSource }` | `packages/interaction/commands/src/types.ts:94` |
| `command/done` | `{ commandId, kind:'success'\|'error', text?: string, sourceEventSeq?: SessionSeq }` | same |
| `model/selection` | (model choice) | `packages/api/session-controller/src/types.ts:35` |
| `agent-preset/selected` | `{ agentPreset: string }` | `packages/preset/agent-preset-registry/src/session.ts:21` |
| `subagent/catalog` | `SubagentCatalogEvent` | `packages/subagent/subagent/src/catalog.ts:38` |
| `subagent/descriptor` | `SubagentDescriptorData` | `packages/subagent/subagent/src/descriptor.ts:30` |
| `subagent/model-selection-policy` | `{ allowedModels: AllowedModelRoute[] }` | `packages/subagent/tool-subagent/src/model-selection-state.ts:10` |
| `goal/change` | `GoalChangeMeta` (complete post-mutation goal state or clear tombstone) | `packages/goal/goal/src/domain.ts:62` |
| `schedule/change` | (schedule mutation) | `packages/schedule/schedule/src/types.ts:353` |
| `feedback/record`, `feedback/message-put`, `feedback/message-delete` | (user feedback records) | `packages/feedback/*/src/types.ts` |
| `tool-workflow/run-start` \| `agent-start` \| `agent-end` \| `run-end` | run identity / member sequence / outcome / terminal reason | `packages/workflow/tool-workflow/src/types.ts:42` |
| `team/member`, `team/message/queued`, `team/message/delivered`, `team/task` | (agent-team records) | `packages/experimental/agent-team/src/types.ts:230` |
| `web/deepseek-search-llm-request` | (search model call record) | `packages/web/web-search-deepseek/src/provider.ts:80` |

**Guest implication for a Python port:** the vocabulary is open. Ship the `ignorable` marker plus the
refuse-on-unknown rule; do not register event names as a compatibility gate (that design was
explicitly rejected — see the header comment of `known-event-types.ts`).

---

## 2. `deriveMessages()` — projecting model history from the log

### 2.1 The surface

Only the five `SurfaceEventType`s can enter the surface, and only via a logged `surfaceOp`:

```ts
export type SurfaceOp =
  | 'append'
  | { op: 'replace'; startSeq: SessionSeq; endSeq: SessionSeq }
```

`replace` shadows an **inclusive surface-position span** (not a numeric seq interval) and inserts the
new node in its place. Two hard guards:

* `assertSourceEventReferences` — the replacing node's `sourceEventSeqs` must cite **every** shadowed
  surface node, must be non-empty, unique and strictly earlier (`packages/core/session/src/surface.ts:336-371`).
* `assertSystemHeadRewrite` — surface node 0 may be rewritten **only** by a `system/message` over
  exactly that node; later system nodes are ordinary history a compaction may shadow
  (`packages/core/session/src/surface.ts:499-513`).
* `tool/result` replacement may change **only** `content` (`surface.ts:462-491`).

### 2.2 The per-node projection function

`deriveEventMessage(event, projectedMessages?)` — `packages/core/session/src/surface.ts:120-158`:

```
'user/message'                     -> event.data verbatim (a UserMessage)
'system/message' | 'developer/message' | 'assistant/message'
                                   -> event.data.message, or null when content.length === 0
'tool/result'                      -> event.data.message
default (turn/*, step/*, attempts, plugin log-only) -> null
```

The empty-content rule matters: a max-tokens step that streamed nothing still records an
`assistant/message` to host its `stream`/`usage`/provider/model, but must not inject a content-less
turn into the provider transcript.

### 2.3 The cached incremental fold

`Session.deriveMessages()` (`packages/core/session/src/index.ts:860-881`) walks `surface.nodes`,
projecting only nodes it has not seen, and rebuilds from scratch whenever
`surface.contentGeneration` changes (a replacement or a plugin message projection). Returned array is
fresh per call; the `Message` objects are shared and deep-frozen.

`foldSurface(events, projections)` (`surface.ts:600-615`) is the detached replay equivalent; it returns
`SurfaceFoldResult = { nodes, replacements: SurfaceFoldReplacement[], projectedMessages }` where
`SurfaceFoldReplacement = { seq, start, end, shadowedSeqs }`.

`SurfaceManager` (`surface.ts:618-718`) is the live validator: `validateNext(candidate)` plans the
transition (pure) before the log push, `_processDelta()` commits it afterwards. `Session.append`
calls `validateNext` **before** mutating `this.log`, so a rejected event never enters the log
(`index.ts:751-761`).

### 2.4 Plugin-owned message projections

A plugin whose event *changes existing message content* declares `@messageProjection` on its
`SessionEventMap` member and registers a pure interpreter:

```ts
export interface SessionMessageProjection<T extends SessionEventType = SessionEventType> {
  type: T
  project(event: SessionEvent<T>, context: SessionMessageProjectionContext): ReadonlyMap<SessionSeq, Message>
}
export interface SessionMessageProjectionContext {
  nodes: readonly SessionSeq[]
  events: readonly SessionEvent[]
  baseSeq: SessionLogOffset
  messages: ReadonlyMap<SessionSeq, Message>
}
```

Registration: `ctx.sessions.registerMessageProjection(projection)` (`index.ts:942-950`).
`MESSAGE_PROJECTION_EVENT_TYPES` (`known-event-types.ts:80-82`) currently contains only
`'image/offload'`. A missing interpreter makes reads **refuse**, and unloading a used definition
blocks cached reads (`surface.ts:704-717`).

### 2.5 How "model-visible means logged" is enforced

Not one check — a stack of them:

1. **Compile time**: `Session.append<T>` accepts surface intent only for `SurfaceEventType`
   (`index.ts:722-726`); non-surface types cannot carry `surfaceOp`/`sourceEventSeqs`.
2. **Append time**: `snapshotJsonValue(data)` must succeed (rejects BigInt, function, symbol,
   `undefined`, negative zero, non-finite numbers, cycles, sparse arrays, Map/Set/Date/class instances)
   — `index.ts:732-739`; then `validateSessionEventData` + `surfaceManager.validateNext`.
3. **Seed/restore time**: `assertSessionEventEnvelope` rejects any envelope key outside
   `{type,seq,time,data,surfaceOp,sourceEventSeqs,ignorable}` and enforces `seq === index`
   (`index.ts:205-246, 568-588`).
4. **Relation invariants** (`packages/core/session/src/invariant.ts`): turn/step numbering, execution
   enclosure (assistant/attempt/message/tool/call/tool/result/system/developer/request-header must
   name the currently open turn **and** step), `tool/result` requires a prior `tool/call` for the same
   callId within the step (except synthetic `TOOL_NOT_STARTED` closers), `request/header` and
   `request/context` must be inside an open turn.
5. **Durability checkpoint**: `packages/session/session-checkpoint-policy/src/index.ts` delays the
   downstream model stream until the logged request prefix is durable —
   `afterCheckpoint()` awaits `ctx.sessions.flush(session)` before `next()`; top-level tool dispatch
   likewise flushes before the tool body; `agent/pre-step` flushes the preceding batch.
6. **Persistence verification**: when a current-generation file is verified, the embedded stream is
   fully replayed and must agree with `message.content`, `usage` and `message.source.replayState`
   (`generation.ts:assertCurrentAssistantStreams`, `packages/session/session-persistence-jsonl/src/generation.ts`).

---

## 3. On-disk session format (enough to write a Python reader)

### 3.1 Directory layout

`Config.root` is **required, with no default** (`packages/session/session-persistence-jsonl/src/index.ts:90-101`).

```
<root>/
  <projectKey(cwd)>/
    <encodeSegment(sessionId)>/
      session.v4.jsonl.zstd        # current generation, default compression
      session.v3.jsonl.zstd        # retained historical generation (after migration)
      session.migration.<hex>.tmp  # transient staging file (0600)
  _no-cwd/                         # cwd === undefined
```

```ts
export function projectDir(root, cwd)            // join(root, '_no-cwd' | projectKey(cwd))
export function sessionDir(root, cwd, id)        // join(projectDir(...), encodeSegment(id))
export function generationLogPath(root, cwd, id, version, compression)
export function logPath(root, cwd, id, compression)  // version = SESSION_FORMAT_VERSION
```
(`packages/session/session-persistence-jsonl/src/format.ts:254-311`)

`projectKey` (`format.ts:225-248`) is **lossy and human-navigable**: `/`, `\`, `:` collapse into a
single `-`; unsafe code units become `~XXXX`; leading dashes trimmed (empty → `root`); wrapped as
`--<slug.slice(0,251)>--`.

`encodeSegment` (`format.ts:199-222`) is **injective over all JS strings**: `[A-Za-z0-9._-]` stay
literal, everything else (including `~`) becomes `~XXXX` (uppercase hex, 4 digits); `.` → `~002E`,
`..` → `~002E~002E`.

Filename (`packages/session/session-format/src/filename.ts:14-34` + `format.ts:42-67`):

* v0 → `session.jsonl` (no version component)
* vN (N ≥ 1) → `session.vN.jsonl`
* compression `zstd` appends `.zstd` → `session.v4.jsonl.zstd`
* Canonical regex: `/^session(?:\.v([1-9][0-9]*))?\.jsonl$/u` — temporary, uppercase, leading-zero and
  `.v0`-tagged names are *not* canonical.

### 3.2 Byte-level record layout

Plain text inside the (optionally zstd-compressed) frames:

* **Line 1 (header record)** — one JSON object, newline-terminated:
  ```json
  {"type":"session","version":4,"id":"…","createdAt":1730000000000,
   "cwd":"/abs/path","parentSession":"…","isSeeded":false,
   "origin":"subagent","delegationDepth":0,"agentPreset":"…"}
  ```
  Required keys: `type, version, id, createdAt, isSeeded, delegationDepth`.
  Optional: `cwd, parentSession, origin, agentPreset`. **No other keys are allowed**
  (`format.ts:83-96`, `format.ts:157-183`). Retired policy fields `sandboxMode` / `approvalPolicy`
  are refused (`format.ts:97-108`).
* **Lines 2..N (event records)** — one JSON object per line, one line per event
  (`eventLines(events) = events.map(eventLine).join('\n')`, `format.ts:313-323`), and the writer
  appends a trailing `\n` for the batch.
* **Event row keys**: exactly `type, seq, time, data`, plus optional `ignorable, sourceEventSeqs,
  surfaceOp`. Any unexpected key is a decode error (`session-format-v1-to-v2/src/codec.ts:22-24,
  132-150`).
* `sourceEventSeqs` is stored **range-encoded**: each element is a bare `number` or an inclusive
  `[start, end]` pair; a strictly increasing run of ≥3 consecutive seqs becomes a pair
  (`packages/core/session/src/seq-ranges.ts:22-40`; physical codec at
  `session-format-v1-to-v2/src/codec.ts:222-241`).

**The fork cut is not in the header.** The header carries `isSeeded` only; the exact
`inheritedEventCount` is the `seq` of the **last `session/end-seed` event whose payload is
`{"inherited":true}`**:

> The current physical header stored as the first JSONL record. The exact inherited cut lives on the
> last tagged `session/end-seed` event. — `format.ts:79-82`

Decoders derive it: `inheritedEventCount = event.seq` when a tagged marker is seen
(`session-format-v1-to-v2/src/codec.ts:151-157`), and both `isSeeded && cut === undefined` and
`!isSeeded && cut !== undefined` are hard errors.

### 3.3 zstd framing

`packages/session/session-persistence-jsonl/src/zstd.ts`:

* The container is a **concatenation of independently decodable, checksummed zstd frames**
  (`ZSTD_c_checksumFlag: 1`).
* Materialization writes **two frames**: frame 1 = the header line *only*, frame 2 = the first event
  batch (`index.ts:1285-1299`). `assertIndependentHeaderFrame` / `assertZstdHeaderFrame` require frame
  1's plaintext to be exactly one newline-terminated line.
* Every later durable append is **one additional frame** containing that batch's lines
  (`encodeEventBatch`, `index.ts:1302-1305`).
* `scanZstdFrames(buffer, maxFrames?)` walks frame structure without decompressing
  (magic `0xFD2FB528`, frame header descriptor, block headers) and returns
  `{ frames: {start,end}[], tornStart? }`. Reserved bits / reserved block type / bad magic are
  corruption errors; EOF inside the final frame yields `tornStart`.
* A torn final frame is partially recovered with `decompressZstdPrefix` (`ZSTD_e_flush`), its complete
  newline-terminated prefix is decoded, and `tornTruncateTo` records the byte offset the writer will
  `truncate()` + `fsync()` before its first new append.

### 3.4 Failure policy: strict vs recoverable

`SessionFormatRecovery = 'strict' | 'recoverable'`. `SessionLogScanner` defaults to **recoverable**
for current-generation reads (`format.ts:386-...`): after the first invalid row it *suppresses*
subsequent rows until it meets a row whose `type === 'turn/end'`, at which point it rethrows (the
sentinel exists so a partially-written turn cannot silently become the session's tail). `strict` is
used by the isolated verifier and by current-generation validation. The scan result carries
`committedBytes` — the offset up to which the prefix is valid.

### 3.5 Write path / atomicity

* **Materialize** (first write): `mkdir -p` root/project/session (0700) with a parent-dir `fsync` after
  each level, write a temp file, `link(tmp, final)` (not `rename`, so a concurrent materializer gets
  `EEXIST` instead of clobbering), `fsync` the session dir, then best-effort `rm` the temp
  (`index.ts:1195-1235`).
* **Append**: `open(path,'a')`, record `size` before, `writeFile`, `fsync`; on failure close and
  `truncate` back to the old size so the unchanged cursor retries cleanly (`index.ts:1324-1362`).
* **Migration publish**: temp `session.migration.<hex>.tmp`, `fsync`, verify in an isolated worker,
  re-`stat` the source identity, `link()` to the current-generation name, dir fsync
  (`generation.ts:publishPreparedMigration`).
* **Write lease**: `SessionWriteLease.acquire(dir, id)` takes a kernel file lock inside the session
  directory (`packages/session/session-persistence-jsonl/src/lease.ts`), so repair can only run under
  write ownership.
* **Live batching**: `LIVE_WRITE_BATCH_MAX_DELAY_MS = 200` (`storage.ts:31`). `session/event` is
  synchronous fire-and-forget → the handle buffers a copy; the first pending event arms a fixed
  deadline; expiry starts one durable append. `session/flush` cancels the wait and drains through
  quiescence.

### 3.6 Migration chain

`SESSION_FORMAT_VERSION = 4`. Adjacent, streaming, compiled into a unique complete chain
(`packages/session/session-format/src/chain.ts` − `createSessionFormatChain`,
`defineSessionFormatMigration`, `CompiledSessionFormatChain.plan()` slices `migrations[from..]`).

| Edge | Package / declaration | What it changes |
|---|---|---|
| v0 → v1 | `packages/session/session-format-v0-to-v1/src/migration.ts` — `sessionFormatV0ToV1`, name `'@deepseek-ai/dsh-session-format-v0-to-v1'` | "Identity format edge that promotes released v0 into released v1": normalizes legacy message ids / retry ids, renames the legacy `provenance` assistant-source key to `source`, refuses payloads the v1 writer cannot emit |
| v1 → v2 | `packages/session/session-format-v1-to-v2/src/migration.ts` — `sessionFormatV1ToV2` | "embeds released-v1 top-level Assistant chunks into v2 attempt events": groups `assistant/chunk` rows into `AssistantStreamRecord[]` on `assistant/message` / `assistant/attempt` (uses `AssistantStreamAccumulator`), dense renumbering of `seq` |
| v2 → v3 | `packages/session/session-format-v2-to-v3/src/migration.ts` — `sessionFormatV2ToV3` | "Streaming system-prompt promotion followed by canonical V3 envelope conversion": pulls `request/header.header.system` out into `system/message` surface nodes (including an empty head before the first step), renames `agentPreset: 'code'` → `'ptc'`, renames PTC vocabulary, remaps referenced seqs, canonicalizes envelopes and hash-generated message ids |
| v3 → v4 | `packages/session/session-format-v3-to-v4/src/migration.ts` — `sessionFormatV3ToV4` / `createSessionFormatV3ToV4(children)` | "Append historical child facts after converting V3 source events to V4": first-class tool-role results (`tool-role.ts:liftToolResult`), producer-owned message sources (`sources.ts:rewriteV3MessageSource`), `forked` added to `turn/end.reason`, `developer/message` with tool additions/removals (`developer.ts`), system-message field tightening, namespacing of unknown `ignorable` events to `plugin:<type>` (`extension-identities.ts`). **Body restoration requires explicit historical child evidence** — `createStage()` throws without it |

Catalog assembly: `packages/session/session-format-catalog/src/generated.ts` (`sessionFormatCatalog`,
`sessionFormatCatalogOptions`, codecs v0..v4, `currentEncoder = releasedV4SessionFormatCodec`),
`current.ts` (`validateInstalledCurrentSessionArtifact`),
`children.ts` (`createSessionFormatCatalogWithChildren(children)`).

Header-only reads never touch event rows: `SessionFormatCatalog.readHeader(value)` returns
`{status: 'current'|'migration-required'|'unsupported'|'malformed', storedVersion, targetVersion, header|reason}`
(`session-format/src/types.ts:250-264`). `stat`/`list` use exactly this
(`index.ts:437-509`); for a historical generation `stat` folds the whole historical corpus revision
into the returned `revision` token.

Release bookkeeping: `docs/session-format-status.md` (`latestFinalizedVersion: 4`,
`latestReleasedVersion: 3`, evidence tag `dsh-v0.1.5-alpha.1`) and
`docs/persistence-changes/historical-formats/v0..v3.md` + `vN.schema.json`.

---

## 4. Fork / resume / transcript / replay

### 4.1 Fork at an event boundary

```ts
export function buildForkSeed(events: readonly SessionEvent[], boundary: SessionSeq): SessionEvent[] {
  const prefix = events.slice(0, boundary + 1)
  prefix.push({ type: 'session/end-seed', seq: SessionSeq(boundary + 1),
                time: events[boundary]!.time, data: { inherited: true } })
  return prefix.concat(openTurnClosers(prefix, { kind: 'forked' }))
}
```
(`packages/core/session/src/fork.ts:21-30`)

Order is contractual: **copied prefix → tagged `session/end-seed {inherited:true}` → synthetic
closers**. `inheritedEventCount` counts only copied source events, *excluding* the closers
(`index.ts:1245-1246`).

`SessionStore.fork(source, boundary?, childSessionId?)` (`index.ts:1255-1273`):

* accepts a live `Session` object or live `SessionId` (`_resolveForkSource` rejects a non-live
  instance with `SESSION_NOT_LIVE`);
* `boundary` is an **inclusive source event seq**, defaulting to the current last event;
* `_forkBoundary` rejects a non-contiguous boundary (`INVALID_BOUNDARY`);
* child inherits `cwd`, gets `parentSession`, `isSeeded: true`, the exact `inheritedEventCount`;
* errors: `SessionForkError` with `SessionForkErrorCode = 'SESSION_NOT_FOUND' | 'SESSION_NOT_LIVE' |
  'SESSION_ALREADY_EXISTS' | 'INVALID_BOUNDARY'` (`index.ts:904-916`).

The `Session` constructor re-validates the cut (`index.ts:592-622`): a seeded session requires an
explicit `inheritedEventCount`; `inheritedEventCount > log.length` throws; a fresh seed must either
equal its inherited prefix or already carry the tagged marker; **only the constructor and
`buildForkSeed` may create `session/end-seed`**. If the seed lacks the tagged marker the constructor
appends it; a non-seeded restore appends a plain `session/end-seed {}` when the log does not already
end in one.

`packages/api/session-controller/src/commands.ts:255` is the Remote-path fork (also via
`buildForkSeed`). Live fork policy is documented in `docs/subsystems/session.md:677-683`.

### 4.2 Resume

`AgentLoop.resumeWith()` — `packages/core/agent-loop/src/index.ts:816-885`:

1. `persistence.open(id, 'write', { signal })` **first** — taking write ownership excludes a
   concurrent resume.
2. `handle.read(0, undefined)` returns the physically valid contiguous log (never a torn tail).
3. `const closers = interruptedTurnClosers(persisted); if (closers.length > 0) await handle.append(closers)`
   — repair is written back through the same handle as an ordinary batch.
4. `SessionPreparation.create(ctx.sessions.prepare(id, { seed: [...persisted, ...closers], meta,
   inheritedEventCount, eventState }))` then `appendUnstoredSuffix` for anything committed but not yet
   durable.

Read-only observers do the same balance **in memory only**:
`readColdSessionLog(persistence, sessionId, signal)` in
`packages/session-query/session-query/src/cold-read.ts:42-57` returns
`{ eventState, header, inheritedEventCount, events: [...events, ...interruptedTurnClosers(events)] }`.

### 4.3 Transcript export

There is **no Markdown/HTML transcript**. "Transcript" = the canonical JSONL, zipped with its media:

`packages/session-query/session-log-export/src/archive.ts`:

* `SESSION_LOG_FILENAME = sessionFormatLogFilename(SESSION_FORMAT_VERSION)` (i.e. `session.v4.jsonl`)
* `serializeSessionLog(...)` — "serialized here as canonical JSONL — one header line, then one line
  per validated event — so every backend (JSONL, SQLite, future) exports identically"
* `sessionLogZipEntries(deps, …)` yields, in zip order: the root log, then
  `subagents/<id>/session.v4.jsonl` per descendant, then `media/<attachmentId>.<ext>`
  (content-addressed) and `files/<prefix>/<digest>/<name>`
* `streamSessionLogZip(...)` streams through fflate; `sessionLogZipFilename(sessionId)`
* `flushLiveSessionLog(deps, id, signal)` calls the SessionStore flush barrier before reading a live log
* Surfaced as the `/export` command plus the authenticated Host route `SESSION_LOG_EXPORT_PATH`
  (`packages/session-query/session-log-export/src/routes.ts`), registered by
  `packages/session-query/session-log-export/src/index.ts`.

**Human transcript projection.** For UI display, the correct source is *append-origin* surface events,
not the folded surface: `isAppendSurfaceEvent(event)` (`packages/core/session/src/surface.ts:89-93`)
selects events whose `surfaceOp === 'append'`. Rationale in the JSDoc: "The model-visible surface
deliberately shadows replaced ranges, so it is the wrong source for a human transcript — a landed
replacement would erase conversation the user already saw."

### 4.4 Replay

There is no deterministic re-execution of models. What exists:

* Every settled attempt durably embeds its **exact timed stream** (`AssistantStreamRecord[]`), so the
  provider output is replayable byte-for-byte without re-calling anything.
  `assembleAssistantStream` / `assembleAssistantStream`'s readers live in
  `packages/llm/llm/src/assistant-stream.ts`; `expandAssistantStream` is the validating path at a
  durable boundary; `lastAssistantStreamChunk(stream, 'usage')` is the canonical usage read.
* `ReplayEnvelope` + `AssistantMessage.source.replayState` let an adapter replay a provider response
  it owns (`packages/llm/llm/src/types.ts:430`).
* Verification replays streams to cross-check the assembled message: `assertCurrentAssistantStreams`
  in `packages/session/session-persistence-jsonl/src/generation.ts` (content, `usage`, `replayState`).
* `Session.create(id, seed, header, inheritedEventCount, projections)` /
  `Session.fromRestore(...)` are the detached-replay primitives
  (`packages/core/session/src/index.ts:507-548`).

---

## 5. Token usage and cost accounting — end to end

### 5.1 Where token counts enter

1. **Provider adapter** emits a `usage` chunk. DeepSeek:
   `packages/llm/llm-deepseek/src/translate.ts:34-41` (`updateUsage`), `:159-160`:
   ```ts
   usage.totalTokens = usage.inputTokens + usage.outputTokens + (usage.cacheReadTokens ?? 0) + (usage.cacheWriteTokens ?? 0)
   yield { type: 'usage', usage }
   ```
   `TokenUsage` is defined at `packages/llm/llm/src/types.ts:175-192`; counts are **disjoint** and the
   comment states providers that fold cache hits into `prompt_tokens` (DeepSeek) must subtract them out.
2. **`BlockAssembler`** keeps the last one: `packages/llm/llm/src/assembler.ts:85-86` (case `'usage'`),
   exposed as `assembler.usage` (`:181-184`).
3. **Agent loop**: `AssistantStreamAttempt` wraps the stream for one step
   (`packages/core/agent-loop/src/agent.ts:427-433`), exposes `get usage()` from the assembler
   (`packages/core/agent-loop/src/assistant-stream.ts:126-129`), and settles:
   * success → `session.append('assistant/message', { turn, step, message, ...(usage && {usage}), stream }, {surfaceOp:'append'})`
     (`agent.ts:521-525`)
   * cancellation with delivered content → the same event with `interrupted: true`
     (`agent.ts:451-459`)
   * cancellation with no content, or error/abort finish → `session.append('assistant/attempt', {turn, step, stream})`
     (`agent.ts:462-476`, `:485-495`) — **no top-level `usage` field; the usage chunk lives inside `stream`.**

So: **`assistant/message.usage` is the committed-message authority; `assistant/attempt` usage is
recoverable only by scanning its embedded stream** (`lastAssistantStreamChunk(stream,'usage')`).

### 5.2 Storage / aggregation structures

| Layer | Type | File |
|---|---|---|
| Per-attempt exact aggregation | `TurnTokenUsage` = `{ uncachedInputTokens, outputTokens, totalTokens, cacheReadTokens?, cacheWriteTokens?, reasoningTokens?, routes?: TurnTokenUsageRoute[] }` | `packages/llm/token-meter/src/turn-usage.ts:13-27` |
| Whole-log cumulative | `TokenUsageProjection` = `{ uncachedInputTokens, outputTokens, cacheReadTokens, cacheWriteTokens }` | `packages/llm/token-meter/src/projection.ts:11-16` |
| Context occupancy | `ContextPressureProjection` = `{ pressureTokens?, projectedTokens?, contextWindow? }` | `projection.ts:26-45` |
| Context composition | `ContextBreakdownProjection` = `{ systemTokens, toolsTokens, messageTokens }` | `projection.ts:53-61` |
| Whole-log timing/counts | `SessionStatsProjection` = `{ turns, steps, llmMs, toolMs, ttftMs, ttftSteps, decodeMs, decodeTokens }` | `packages/session/session-stats/src/types.ts:19-36` |
| Live measurement | `TokenMeasurement` = `{ logRevision, baseline, surfaceDeltaTokens, totalTokens, surfaceTokens, nodes: TokenSurfaceNode[] }` | `packages/llm/token-meter/src/types.ts:20-32` |

Aggregation is **incremental projection folds over the log, never recomputation from scratch**:

* `tokenUsageProjectionDefinition` (`packages/llm/token-meter/src/usage-projection.ts:117-171`,
  `stateVersion: 2`): on `assistant/message`/`assistant/attempt` take the last usage sample; if the
  same `(turn, step)` reports again, **replace** that step's contribution instead of adding
  (`addReplacing`); `llm/retry-started` clears the `last` slot so a retried attempt adds a new one.
  `reasoningTokens` is deliberately *not* accumulated (already inside `outputTokens`).
* `contextPressureProjectionDefinition` (`usage-projection.ts:173-235`, `stateVersion: 5`): independent
  last-wins slots — newest usage sample supplies the numerator, newest `request/context.contextWindow`
  the denominator; a running `surfaceTokens` rides `foldSurfaceProjection` so `projectedTokens =
  max(0, pressureTokens + surfaceTokens - sampledSurfaceTokens)` reacts the instant compaction shadows
  a span.
* `contextBreakdownProjectionDefinition` (`breakdown-projection.ts`, `stateVersion: 5`): heuristic
  composition; last nonempty surviving `system/message` counts as system tokens.
* `sessionStatsProjectionDefinition` (`packages/session/session-stats/src/projection.ts:113-212`,
  `stateVersion: 1`): `step/end` is the counted step (appended once per entered step in a `finally`);
  `turns` increments when the turn number of a counted `step/end` differs from `lastTurn`; `llmMs`
  accumulates `step/start → assistant/message`; `ttftMs` from the first non-empty delta
  (`assistantStreamFirstTokenTime`); `decodeMs`/`decodeTokens` only on steps that also report valid
  `usage.outputTokens`; `toolMs` pairs `tool/call`→`tool/result` by callId and drops leftovers at
  `turn/end`.
* `deriveTurnTokenUsage(events)` (`turn-usage.ts:178-...`) is the **strict per-turn** fold: it walks a
  complete `turn/start … turn/end` slice as a state machine over
  `step/start → assistant/attempt | assistant/message → llm/retry / llm/retry-started → step/end`, and
  returns `undefined` (all-or-nothing) on any missing boundary, non-integer, negative, or
  contradictory `totalTokens`. `normalizeUsage` rejects `reasoningTokens > outputTokens` and requires
  the exact total to be consistent with disjoint buckets. Consumers: `turn-tail.ts:91` in the web
  client.

### 5.3 Where it is computed

* **`ctx.tokenMeter`** — `packages/llm/token-meter/src/index.ts`, class `TokenMeter`.
  `measure(session, requestHeader?)` returns a deep-frozen `TokenMeasurement`; state is a per-session
  `WeakMap<Session, ReplayState>` caught up event-by-event (`_sync`), eager on `session/event` for
  sessions a consumer already read.
* The **heuristic estimator** (`estimate.ts`) is deliberately tiny and fixed:
  `CHARS_PER_TOKEN = 4` (`:13`), `BLOCK_OVERHEAD = 4` (`:16`), `ROLE_OVERHEAD = 4` (`:19`);
  `estimateMessage`, `estimateSystemMessage` (system prompt = text density + role overhead, no
  per-block overhead), `estimateToolsTokens(header) = ceil(JSON.stringify(tools).length / 4) + 4`.
* **Route pricing is token pricing, not money**: `LlmImageRequestPricing` /
  `LlmImageRequestPrice` (`packages/llm/llm/src/types.ts:199-220`) let an adapter price image
  occurrences in *visual tokens plus model-visible text*; DeepSeek implements it in
  `packages/llm/llm-deepseek/src/request-pricing.ts`, consumed by
  `packages/llm/token-meter/src/route-pricing.ts:priceSurface`.
* Baseline selection in `measure()`: provider usage is reused **only** when the anchored call's
  canonical header equals the current one **and** `usageTokens(usage) >= estimatedAnchorTokens`;
  otherwise the whole envelope + surface is repriced heuristically. `usageTokens()` sums the four
  disjoint buckets.

### 5.4 Cost / pricing / budget — what does NOT exist

**There is no monetary cost model anywhere in `src/`.** Verified by repo-wide grep:

* No `costUsd`, `usdCents`, `pricePerMillion`, per-token money table, invoice or spend accumulator in
  any `packages/*/*/src` or `apps/*/src`.
* Every occurrence of "pricing" refers to *token* pricing (image request projection) — see §5.3.
* `packages/llm/llm-pi-ai/src/catalog.ts:36` explicitly notes the absence of a spend fact:
  "reports spend — so this is the absence of a fact, not a configurable rate."
* No token *budget* enforcement either. `grep -rn "budget" packages/*/*/src` finds only
  (a) `LlmImageRequestBudget` (image byte budget for a request,
  `packages/llm/llm/src/types.ts:356`), and (b) unrelated byte budgets in
  `packages/experimental/ptc-runtime-python`. The `ContextPressureProjection` doc states the value is
  "a user-facing reference, not a billing or gating input" (`packages/llm/token-meter/src/projection.ts:26-27`).
* Machine-readable "usage"/"cost" references in the UI are the token buckets only; the desktop app has
  a `usage` **platform webview** that opens the account's billing page
  (`apps/desktop/src/platform-view.ts:79,169`) — external, not computed by dsh.

A Python harness that wants money would have to add a rate table; dsh gives it the four disjoint
buckets and per-route attribution (`TurnTokenUsage.routes = {provider, model}[]`), which is exactly the
key you need to price.

### 5.5 Context-window accounting

* Capacity enters the log via `request/context.contextWindow` (`RequestContext`,
  `packages/core/session/src/types.ts:254-263`), sourced from `LlmModelContext.contextWindow`
  (`packages/llm/llm/src/types.ts:344-347`). It is **route metadata, not request-envelope state** —
  kept out of `EpochHeader` so a route change does not register as a request `change`.
* Occupancy display math (`packages/client/ui-conversation/src/client/context-occupancy.ts:15-26`):
  ```ts
  const usedTokens = pressure?.projectedTokens ?? pressure?.pressureTokens
  percent = Math.min(100, Math.round(usedTokens / pressure.contextWindow * 100))
  ```
* Compaction thresholds (see §6) use the same `contextWindow`.

---

## 6. Compaction

### 6.1 Events and lock

`packages/compaction/compaction/src/types.ts:18-…` (payloads in §1.5). The bracket is a **log-recorded
lock**: `compaction/start` is appended first (synchronously adjacent to the idle/log validation),
then summarization, then `compaction/summary`, then the replacing `user/message`, then
`compaction/end`. Releasing the lock last means a crash leaves a detectable orphaned
`compaction/start` rather than a false "finished". An unmatched start **before a newer
`session/end-seed`** is stale evidence from a prior lifecycle and is ignored
(`packages/compaction/compaction-basic/src/region.ts:566-…`, `inspectCompactionEntryState`).

The replacement node is a `user/message` with
`surfaceOp: {op:'replace', startSeq, endSeq}` and
`sourceEventSeqs: [startEvent.seq, summaryEvent.seq, ...shadowedSeqs]`
(`region.ts:commitCompactionBody`). Its source comes from
`compactCheckpointSource(compactionId, sourceCommandId?)` /
`isCompactCheckpointSource(source)` in `packages/compaction/compaction/src/checkpoint.ts` —
`{ kind: 'compact-checkpoint', compactionId, sourceCommandId? }`.

Shadow-price protocol: the surface `replace` event is priced by the log-only metering event
**immediately before it** — `compaction/summary` for a summarizing compaction, `compaction/prune` for
a model-free prune — which states `shadowedTokenCount` and `shadowedSeqs` so a pure consumer can
subtract without retaining per-node prices.

### 6.2 When

`packages/compaction/compaction-basic/src/config.ts` defaults:

| Knob | Default | Meaning |
|---|---|---|
| `thresholdRatio` | `0.8` | window fraction for pressure |
| `headroomTokens` | `65_536` | additional pressure headroom beyond the routed output reservation |
| `retainRatio` | `0.16` | recent context retained verbatim, as a fraction of (window − reserved output) |
| `retainTokens` | — | absolute alternative to `retainRatio` (mutually exclusive) |
| `maxTokens` | `headroomTokens` | generation cap for the summarization call |
| `compactionRetries` | `1` | extra attempts while still above threshold |
| `maxOverflowRetries` | `1` | retries after canonical `CONTEXT_WINDOW_EXCEEDED_CODE` |
| `auto` | `true` | registers automatic listeners |

Capacity math (`resolveCompactSpec`, `config.ts:172-232`):
```
messageBudgetTokens   = contextWindow - reservedCompletionTokens
pressureBudgetTokens  = messageBudgetTokens - headroomTokens      // must be > 0
thresholdTokens       = floor(min(contextWindow * thresholdRatio, pressureBudgetTokens))
retainTokens          = retainTokens ?? floor(messageBudgetTokens * retainRatio)
// require retainTokens < thresholdTokens
```
`reservedCompletionTokens` = the logged envelope's `maxTokens` else the adapter's `defaultMaxTokens`
else 0 (`compaction-basic/src/index.ts:65-68`).

Triggers (`compaction-basic/src/index.ts:148-235`):
* `agent/pre-step` waterfall → `compactIfNeeded(agent, 'pressure', signal)`; failures are logged and
  the turn continues (a misconfigured target warns once per `provider/model`).
* `agent/request-error` → only when `failure.code === CONTEXT_WINDOW_EXCEEDED_CODE`; overflow bypasses
  the threshold and retained-tail policy and forces one useful balanced reduction; returns
  `{ kind: 'retry' }` **only if `session.surface.replaceGeneration` advanced** (so a prune that landed
  before a later summary failure still counts as progress). Retry budget resets on `agent/status:
  'idle'` and on the next successful `assistant/message`.
* Manual `/compact` → `compactNow(agent, signal, sourceCommandId?)` runs in `agent.runMaintenance`,
  writes a standalone `turn: null` bracket, and flushes after a closed attempt.
  `ManualCompactionErrorCode = 'busy'|'cancelled'|'changed'|'summary'|'commit'|'persistence'`.

Range selection (`region.ts:117-161`, `selectCompactableRange`): start at surface node 0 unless node 0
is a `system/message` (then node 1); walk backwards accumulating `node.tokens` until
`retainTokens` is reached; then walk the cut further back until `toolPairingBalancedBefore(session, seq)`
holds so no assistant tool-call/result pair is split. Returns `null` when nothing is compactable.
`ctx.toolResultPruner.pruneSession(session)` is invoked **before** range selection once pressure
qualifies, and the meter is re-measured after.

Stability: `stability: 'whole-surface'` for automatic (assert the kept nodes and their prices are
unchanged after the async summarization), `'selected-span'` for manual. Mismatch ⇒
`SurfaceChangedError` ⇒ manual code `'changed'`.

### 6.3 Summarization strategy

`packages/compaction/compaction-basic/src/summarizer.ts`:

* **No separate summarizer system prompt.** The call replays the conversation's own system prompt,
  tools and leading messages, then appends the directive as the **final user message**, so the request
  is a genuine prefix of the last routed request and the provider's KV cache is reused.
* `COMPACTION_INSTRUCTION` (`:37-70`) demands a fixed Markdown skeleton — `## Primary Request and
  Intent`, `## Key Technical Concepts`, `## Files and Code`, `## Errors and Fixes`, `## Pending Jobs`,
  `## Current Work`, `## Next Step`, `## Critical Context` — with rules: keep every section in order,
  "(none)" for empties, preserve exact paths/commands/errors/identifiers, and if a
  `<compacted-summary>` block already exists, merge rather than copy it forward.
* `frameSummary(summary)` wraps the result as a durable checkpoint node:
  `CHECKPOINT_PREAMBLE` + `<compacted-summary>` + blocks + `</compacted-summary>`. The preamble tells
  the model to treat it as established background and continue without acknowledging it.
* `summarizeWithLlm` returns `SummaryResult = { summary, rawOutput, llmStreamCall: true, provider,
  model, maxTokens?, usage? }`. Images are refused (`UNSUPPORTED_CONTENT`); a truncated (`max-tokens`)
  or errored/aborted finish becomes a fail-closed error.
* Target resolution order: configured `summarizationProvider/Model` → the session's latest logged
  `request/header.config` → `AgentOptions.provider/model`.
* The single override hook is `protected summarize(input, agent, signal)`
  (`compaction-basic/src/index.ts:247-257`); `regionDependencies()` also wires a
  `compaction/summary-error` waterfall for recovery.

### 6.4 Other compaction plugins

* `packages/compaction/compaction-tool-result-pruner/src/types.ts`: `ToolResultPruneConfig`
  `{ thresholdChars = 8192, headChars = 4096, tailChars = 1024 }`; `PrunedEntry`
  `{ originalSeq, replacementSeq, callId, charsBefore, charsAfter }`; `PruneResult`
  `{ pruned, charsRemoved }`. Emits `compaction/prune` + a `tool/result` surface replacement that may
  change **only content**.
* `packages/compaction/compaction-image-offload/src/projection.ts`: `image/offload`
  `{ targets: {seq, imageIndexes[]}[] }`, marked `@messageProjection`; the interpretation is a pure
  projection that marks occurrences offloaded without changing message identity.
* `packages/compaction/command-compact/src/index.ts`: the `/compact` command.

---

## 7. Interruption and error recovery

### 7.1 The repair module

`packages/core/session/src/repair.ts`:

```ts
export const TOOL_NOT_STARTED = 'TOOL_NOT_STARTED'
export const TOOL_OUTCOME_UNKNOWN = 'TOOL_OUTCOME_UNKNOWN'
export type OpenTurnCloseCause = { kind: 'interrupted' } | { kind: 'forked' }

export function openTurnClosers(events, cause): SessionEvent[]          // :53
export class ToolCallRecovery { observe(event); results() }             // :105
export function interruptedTurnClosers(events): SessionEvent[]          // :209
```

`openTurnClosers` scans for the last unmatched `turn/start` / `step/start`, then emits, in order:
unmatched tool results first (insertion order = assistant order), an open `step/end`, then
`turn/end { reason: { kind: cause.kind } }`. Seqs continue the log; **timestamps reuse the last real
event's `time`** so the closers are deterministic and never invent a future time. A balanced or empty
log returns `[]`.

`ToolCallRecovery.observe` tracks `Map<ToolCallId, {turn, step, callSeq?}>` seeded from
`assistant/message` content `tool-call` blocks; `tool/call` records `callSeq`; an **append-origin**
`tool/result` in the same `(turn, step)` clears the entry; any `turn/start`, `turn/end` or `step/end`
clears everything.

`results()` produces conservative error results with deterministic ids
`` `${cause.kind}-tool-result-${callId}-${seq}` `` and cause-specific wording. The `interrupted`
wording tells the model the outcome is unknown and to retry only if read-only/idempotent, otherwise
verify external state first; the `forked` wording additionally says the parent may have completed it
after the fork point. Error codes: `ToolOutcomeUnknownError/TOOL_OUTCOME_UNKNOWN` when a `tool/call`
was recorded, `ToolNotStartedError/TOOL_NOT_STARTED` otherwise. When `callSeq` exists the synthetic
result cites it via `sourceEventSeqs: [callSeq]`.

`invariant.ts:142` explicitly permits the exception:
```ts
const syntheticNotStarted = event.data.message.isError === true && event.data.error?.code === TOOL_NOT_STARTED
if (!trace.pendingCalls.has(callId) && !syntheticNotStarted) fail(...)
```

### 7.2 Which settlement gets logged

`packages/core/agent-loop/src/agent.ts:443-500` (quoted in §5.1):

| Situation | Event |
|---|---|
| stream threw after `started`, turn aborted, delivered content non-empty | `assistant/message` with `interrupted: true` |
| stream threw after `started`, no delivered content | `assistant/attempt` |
| finish reason `error`/`aborted` | `assistant/attempt`, then the `agent/request-error` waterfall decides retry vs throw |
| normal finish | `assistant/message` (with `usage` when reported) |

`live.settle(...)` publishes the terminal `agent/assistant-stream` frame **after** the durable append
commits (`assistant-stream.ts:74-78`), so the client can never see a frame the log does not contain.

### 7.3 Retry logging

`packages/llm/llm-retry/src/types.ts:16-47`:

```ts
export type LlmRetryEventData =
  | { retryId; turn; step; provider; mode: 'normal'; policyKey; retry; maxRetries; delayMs; failure: LlmFailure }
  | { retryId; turn; step; provider; mode: 'always'; policyKey; retry; delayMs; failure: LlmFailure }
export interface LlmRetryStartedEventData { retryId: RetryId; turn: number; step: number; retry: number }
```

`llm/retry` is written **before** the wait, `llm/retry-started` **after** the wait succeeds and before
the next attempt. Both are log-only. The usage folds use `llm/retry-started` as the slot-closing edge
so the retried attempt contributes a *new* billed attempt rather than overwriting the failed one
(`usage-projection.ts:121-127`).

### 7.4 Torn-tail / crash-tail policy

* Persistence **never truncates a semantically interrupted turn** — a turn can be huge and its events
  were durable before the crash. Only the incomplete fragment of a *physical* tail that never resolved
  its append is discarded; complete records recovered from a torn zstd frame are durably rewritten by
  the write path before the handle's first new append
  (`docs/subsystems/persistence.md`, section "Crash recovery preserves an interrupted turn").
* Read-only observers balance the log in memory; only a write owner appends closers.

---

## 8. Reports / observability / analytics

### 8.1 What is computed, where, and rendered where

| Analytic | Computed by (file) | Result type | Rendered by |
|---|---|---|---|
| Whole-log turns / steps / LLM ms / tool ms / TTFT / decode ms / decode tokens | `packages/session/session-stats/src/projection.ts:113-212` (`sessionStatsProjectionDefinition`) | `SessionStatsProjection` (`session-stats/src/types.ts:19-36`) | `packages/client/ui-chat/src/client/chat/StatsPills.tsx` (Time pill + dialog) |
| Whole-log cumulative token buckets | `packages/llm/token-meter/src/usage-projection.ts:117-171` (`tokenUsage`) | `TokenUsageProjection` | `StatsPills.tsx` `UsagePill` (total, cache-hit %, input / cacheRead / cacheWrite / output rows) |
| Context occupancy % and composition | `usage-projection.ts:173-235` (`contextPressure`), `breakdown-projection.ts` (`contextBreakdown`) | `ContextPressureProjection`, `ContextBreakdownProjection` | `packages/client/ui-conversation/src/client/skeleton/ContextMeter.tsx` (ring + portal panel) via `packages/client/ui-conversation/src/client/context-occupancy.ts` |
| Per-turn exact usage | `packages/llm/token-meter/src/turn-usage.ts:178` (`deriveTurnTokenUsage`), called client-side at `packages/client/ui-chat/src/client/conversation-nodes/turn-tail.ts:91` | `TurnTokenUsage` | `packages/client/ui-chat/src/client/chat/TurnUsagePanel.tsx` |
| Per-step TTFT / decode / output tokens | `packages/client/ui-chat/src/client/contract/turn-metrics.ts:34` (`assistantStepReading`) | `StepReading` | turn-tail / StatsPills dialog |
| Whole-log turn outline (prompt + response previews, `turn/start` seq) | `packages/session/session-turn-outline/src/projection.ts` | `TurnOutlineEntry[]` | history rail / turn navigation |
| Session title | `packages/session/session-title/src/**` + `session-title-llm` / `session-title-first-prompt-llm` / `session-title-all-prompts-llm` | `SessionTitleSnapshot` | session list / header |
| Session list, search, lineage, event traces, event windows | `packages/session-query/session-query/src/index.ts` (`SessionQueryEngine`) | `SessionRecord`, `SessionEventSearchHit`, `SessionLineageTrace`, `SessionEventTrace`, `SessionEventWindow`, `SessionLogSnapshot`, `SessionSurfaceSnapshot` | Web session browser; tool `tool-session-query` |
| Full-text index | `packages/session-query/session-query-sqlite/src/{schema,query,index}.ts` | SQLite FTS5 | backing store behind the above |
| Durable projection checkpoints | `packages/session/session-projection-cache/src/index.ts` | per-session projection snapshots | zero-I/O list reads, cold-fold acceleration |

There is **no dashboard page** in `apps/web/src` (it contains only `main.ts`, `preview.ts`, stubs);
the analytics surface is the composer stats strip + context meter inside the chat, plus the session
browser built on `session-query`.

### 8.2 The projection seam

`packages/session/session-projection/src/types.ts` defines two merge-extensible tables:

```ts
export interface SessionProjectionMap {}        // client-visible keys, values are wire-JSON whole values
export interface SessionProjectionStateMap {}   // host fold state (must be plain JSON, cache-persistable)
```

Each domain registers a `ProjectionDefinition<Key, State>` with
`{ key, stateVersion, stateSchema, init, apply, wire: { viewSchema, view } }` on
`ctx.sessionProjections`. Because the state must survive the projection cache, every fold state is
plain JSON with an explicit `stateVersion` and a strict schema. The change feed is gated by
`Object.is` on the returned state object, so an "uninteresting" event must **return the same
reference** — every fold in §8.1 does exactly that.

### 8.3 session-query + SQLite

* `SessionQueryEngine` (abstract service `ctx.sessionQuery`, `session-query/src/index.ts:98`):
  `observeSession`, `searchSessions`, `searchEvents` (abstract), `listSessions`,
  `readSurface`, `readLog`, `readEventWindow`, `traceLineage`, `traceEvent`, `observeTitle`, …
* Filters: `SessionResultFilter` (`id|cwd|created-at|parent|availability`), `SessionEventResultFilter`
  (`seq|time|type|surface|text`); arrays are ANDed, list values ORed; `text` is a literal
  case-insensitive whitespace-flexible scan over extracted semantic text, independent of FTS.
* `SessionEventSurface = 'current' | 'shadowed' | 'log-only'` — classification reuses the same
  `foldSurface()` transitions as model-history derivation.
* SQLite schema (`session-query-sqlite/src/schema.ts`): `SESSION_QUERY_SQLITE_SCHEMA_VERSION = 8`,
  `SESSION_QUERY_SQLITE_APPLICATION_ID = 0x44534851`. Tables:
  * `search_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1), global_generation INTEGER NOT NULL) STRICT`
  * `persisted_sessions(id TEXT PRIMARY KEY, version, created_at, cwd, parent_session, seed_length,
    delegation_depth, agent_preset, revision TEXT, generation INTEGER) STRICT`
  * `persisted_docs` FTS5 `(text, session_id UNINDEXED, seq UNINDEXED, type UNINDEXED, time UNINDEXED,
    surface UNINDEXED, codepoint_length UNINDEXED, tokenize='unicode61')`
  * plus TEMP mirrors `live_sessions` and `temp.live_docs` for live sessions.
  * It is a **disposable derived index**: a version mismatch drops and rebuilds; an unknown
    application id or unrecognized user table refuses to touch the file.

### 8.4 Telemetry

`packages/session/session-telemetry` (capture) + `packages/session/session-telemetry-otel`
(backend) + `packages/telemetry/otel` (transport).

The outbound vocabulary is exactly one record type
(`docs/subsystems/session-telemetry.md`, `packages/session/session-telemetry/src/coordinator.ts`):

```ts
interface SessionTelemetryRecord {
  sourceEvent?: { sessionId: SessionId; envelope: Omit<SessionEvent,'data'> }  // absent for ops records
  channel: 'ledger' | 'ops'
  time: number
  severity: 'info' | 'warn' | 'error'
  attributes: Record<string, string | number>
  body: unknown
}
```

Attribute keys (`coordinator.ts:290-304`): ledger records carry
`session.id`, `session.format_version`, `event.type`, `event.seq`, plus optional `session.cwd`,
`session.parent_id`, and `session.seed_length` for a seeded session. Ops records carry
`telemetry.op` (`'agent-error'` | `'shutdown'`), `session.id`, and for `agent-error` also
`agent.id`, `turn`, `step`, `error.name`. Severity is pre-mapped: `error` for a tool result with
`isError`, a `turn/end` with an `error` reason, and `agent-error`; otherwise `info`.

There is a **redaction waterfall** `session-telemetry/record(record, next)` — the seam ships *no*
rules; with no listener mounted records go out exactly as captured. A throwing listener withholds
that one record fail-closed. Redaction applies to the exported copy only; the canonical log is never
rewritten.

Delivery is best-effort and at-least-once: the cursor marks *handed off*, not delivered, so receivers
must **dedupe ledger records on `(session.id, session.format_version, event.seq)`**; ops records
deliberately omit that identity and tolerate duplicates.

The OTel backend is **logs-only** — I found no span or metric instrument anywhere in
`packages/session/session-telemetry-otel/src/index.ts` or `packages/telemetry/otel/src/*`. It uses
`LoggerProvider` + a log record processor; `SESSION_LOG_MAX_REQUEST_BYTES = 4_000_000`
(`packages/telemetry/otel/src/session-log.ts:16`) is the collector request ceiling, with default
`maxQueueSize: 2048`, `maxExportBatchSize: 512`. Resource attributes:
`service.name`, `service.version`, `user.id` (anonymous). Capture is `on-demand` and requires a new
explicit user feedback submission; `sharing: 'full' | 'feedback-only' | 'disabled'` is the
deployment-selected disclosure.

`packages/telemetry/otel/src/event-log.ts` is the separate product-analytics channel
(`OTelEventRecord { eventName, body, timestamp, severityNumber?, attributes? }`, count-batched).

### 8.5 Invariants and filesystem deliverables

* `packages/runtime-diagnostics/invariants/src/index.ts`: `InvariantInstaller`,
  `InvariantFailure`, `InvariantError` with `code = 'INVARIANT'` and `packageName`; the service takes
  `{ enabled, package_allowlist, package_blocklist }`. Every package ships an `./invariant` companion;
  `packages/core/session/src/invariant.ts` is the session-contract one (see §2.5).
* `packages/deliverables/tool-present` records `deliverables/presented {turn, callId, files}`;
  `packages/deliverables/workspace-changes` records `workspace/changes {turn}` and serves the diff
  summary from a host-side store keyed by that event's seq.

---

## 9. Prioritized port list for a Python agent harness

Ordered by value/effort. Each row names the exact dsh file to copy the semantics from.

### P0 — the substrate (port these or the rest is impossible)

1. **Append-only typed event log as the single source of truth, with message history derived**
   — `packages/core/session/src/index.ts` (`Session.append`, `deriveMessages`),
   `packages/core/session/src/surface.ts` (`deriveEventMessage`, `foldSurface`, `SurfaceManager`).
   Python: `dataclass`/`TypedDict` union + a `Session` class holding `list[Event]`, `seq = len(log)`,
   and a cached surface-node list.
2. **Explicit surface operations instead of destructive edits** — `SurfaceOp`
   (`packages/core/session/src/types.ts:462-464`). Every compaction/rewrite appends a node that
   *shadows* a span and cites the shadowed seqs. This is what makes transcripts, telemetry and
   time-travel free. Includes the `sourceEventSeqs` coverage rule
   (`surface.ts:336-371`) and the node-0 system-prompt protection (`surface.ts:499-513`).
3. **Refuse-on-unknown with an explicit `ignorable` escape hatch** —
   `packages/core/session/src/known-event-types.ts:1-27` and
   `packages/session/session-persistence-jsonl/src/format.ts:consumeEventLine`. Default to *required*;
   a forgotten opt-in over-refuses rather than silently resuming a gutted session.
4. **Durable settlement per model attempt: assembled message OR raw attempt, never both, never
   neither** — `packages/core/agent-loop/src/agent.ts:443-500` plus the compact timed-stream record
   format `packages/llm/llm/src/assistant-stream.ts:20-47` (delta-run packing with `time0` + `dt[]`
   is a big, cheap size win versus one record per delta).
5. **Crash repair by appending closers, not by truncating** —
   `packages/core/session/src/repair.ts:53-211`. Deterministic ids and reused timestamps keep repair
   idempotent and diffable.

### P1 — economics and context control

6. **Disjoint token buckets + per-attempt replacement semantics** —
   `packages/llm/llm/src/types.ts:175-192` (disjointness contract) and
   `packages/llm/token-meter/src/usage-projection.ts:117-171` (a retried `(turn, step)` **replaces**
   its earlier contribution; `llm/retry-started` closes the slot).
7. **Provider usage as an anchor + signed heuristic delta, instead of naive re-estimation** —
   `packages/llm/token-meter/src/index.ts:measure()` (baseline selection) and
   `usage-projection.ts:173-235` (`pressureTokens` + running `surfaceTokens` − `sampledSurfaceTokens`
   ⇒ `projectedTokens`). This is how occupancy reacts to compaction even though compaction reports no
   usage of its own. The `usageTokens(usage) >= estimatedAnchorTokens` guard is the non-obvious bit.
8. **O(1) incremental projection folds with a `stateVersion` and a strict schema** —
   `packages/session/session-projection/src/types.ts` +
   `packages/session/session-stats/src/projection.ts` (return the *same object* for uninteresting
   events so a change feed stays quiet) +
   `packages/session/session-projection-cache/src/index.ts` (durable checkpoints).
9. **Context-window budgeting with an explicit reserved-output deduction and a retained tail** —
   `packages/compaction/compaction-basic/src/config.ts:172-232` (`resolveCompactSpec`). The
   `messageBudget = contextWindow − reservedCompletion` subtraction is the one people forget.
10. **Cache-friendly summarization: replay the real prefix, append the directive as the last user
    message** — `packages/compaction/compaction-basic/src/summarizer.ts:37-131`. Also the fixed
    section skeleton and the "if a prior `<compacted-summary>` exists, merge don't copy" rule.
11. **Log-recorded lock bracket for long, crashable operations** —
    `packages/compaction/compaction-basic/src/region.ts:compactSurfaceRegion`. `start` first,
    `end` last, `error` on the close; an unmatched start before a newer seed boundary is stale.
12. **Shadow-price protocol**: a log-only metering event (emitting the shadow price + shadowed seqs)
    immediately precedes the surface replacement — `packages/compaction/compaction/src/types.ts`
    (`compaction/summary`, `compaction/prune`). Lets a pure consumer price a rewrite in O(1).

### P2 — history, branching, and repair ergonomics

13. **Fork = copy prefix + tag the cut + synthesize closers** —
    `packages/core/session/src/fork.ts:21-30`. The cut lives in the log (`session/end-seed
    {inherited:true}`), not in metadata, so a forked log is self-describing. Include the
    `inheritedEventCount` validation rules (`packages/core/session/src/index.ts:592-622`).
14. **Missing-tool-result recovery with model-facing retry guidance** —
    `packages/core/session/src/repair.ts:21-51` (`CLOSER_TEXT`). The wording distinguishing
    "started but unknown outcome" from "never started", and the idempotence warning, is the portable
    part; the codes are `TOOL_NOT_STARTED` / `TOOL_OUTCOME_UNKNOWN`.
15. **Human transcript = append-origin events, model history = folded surface** —
    `packages/core/session/src/surface.ts:89-93` (`isAppendSurfaceEvent`). Two projections, one log;
    this is why compaction never erases what a user saw.
16. **Whole-log statistics that survive paging and compaction** —
    `packages/session/session-stats/src/projection.ts` (`step/end` as the counted event, because it
    lands in a `finally`; counting assembled messages would overcount max-tokens hosts and undercount
    cancelled steps). `ttftMs` = first non-empty delta; `decodeMs` only when usage reported.
17. **Turn outline for navigable history** —
    `packages/session/session-turn-outline/src/projection.ts` + `types.ts` (`TurnOutlineEntry`
    carrying the `turn/start` seq so a client can page back to exactly that turn).
18. **Forward-upload / telemetry with an identity tuple receivers can dedupe on** —
    `packages/session/session-telemetry/src/coordinator.ts` (attribute keys) and the dedupe contract
    `(session.id, session.format_version, event.seq)`. Also the redaction-waterfall design: the seam
    ships no rules, so exported data is exactly as clean as the deployment makes it, and a throwing
    listener withholds one record instead of failing the loop.
19. **Token-cost table: not in dsh — you must add it.** What dsh gives you to hang it on is
    `TurnTokenUsage.routes` (`packages/llm/token-meter/src/turn-usage.ts:7-27`) plus the four disjoint
    buckets; note `reasoningTokens ⊆ outputTokens` (do not double count) and that billed input is
    `inputTokens + cacheReadTokens + cacheWriteTokens`.

### P3 — polish worth stealing

20. **Lossless-JSON validation at the append boundary** — reject BigInt/functions/NaN/-0/cycles/
    Map/Set/Date at the source (`packages/core/session/src/index.ts:732-739`), so the durable log can
    never diverge from the in-memory one.
21. **Range-encoded `sourceEventSeqs`** — `packages/core/session/src/seq-ranges.ts:22-40`.
22. **Injective path encoding for user-controlled ids** — `encodeSegment` / `projectKey`
    (`packages/session/session-persistence-jsonl/src/format.ts:199-248`).
23. **Two-frame zstd materialization + one frame per append, with structural torn-tail recovery** —
    `packages/session/session-persistence-jsonl/src/zstd.ts:48-130` and `index.ts:1285-1299`. Python
    equivalent: `zstandard` frames with `write_checksum=True`, first frame = header line only.
24. **Isolated-worker verification of a migrated generation before publication, plus a
    prefix-digest re-check** — `generation.ts:publishPreparedMigration`, `verifyCurrentGeneration`.
25. **`link()`-based exclusive publication instead of `rename()`** —
    `index.ts:1209-1235`; `rename` silently clobbers, `link` gives you `EEXIST` ⇒ safe concurrent
    materialization.
26. **Bounded write-behind window with a fixed (not reset) deadline and a flush barrier** —
    `packages/session/session-persistence-jsonl/src/storage.ts:31,250-285` (`LIVE_WRITE_BATCH_MAX_DELAY_MS
    = 200`) plus `packages/session/session-checkpoint-policy/src/index.ts` (flush before provider
    dispatch and before top-level tool bodies — fail-closed at every side-effect boundary).

---

## 10. Quick file index

```
packages/core/session/src/types.ts             SessionEventMap, SessionEvent, SurfaceOp, TurnEndReasonMap,
                                               EpochHeader, RequestContext, SessionHeader, SESSION_FORMAT_VERSION
packages/core/session/src/index.ts             Session (append/deriveMessages/requestHeader/toolHistory),
                                               SessionStore (create/prepare/enter/announce/flush/fork)
packages/core/session/src/surface.ts           deriveEventMessage, foldSurface, SurfaceManager, validators
packages/core/session/src/repair.ts            openTurnClosers, ToolCallRecovery, interruptedTurnClosers
packages/core/session/src/fork.ts              buildForkSeed
packages/core/session/src/invariant.ts         session event relations
packages/core/session/src/known-event-types.ts KNOWN_SESSION_EVENT_TYPES, MESSAGE_PROJECTION_EVENT_TYPES
packages/core/session/src/seq-ranges.ts        encodeSeqRanges / decodeSeqRanges
packages/core/session/src/request-header.ts    canonicalHeader, headerEquals, foldRequestHeader
packages/core/agent-loop/src/agent.ts          loop, settlement, header logging
packages/core/agent-loop/src/index.ts          AgentLoop.resumeWith (crash repair on resume)
packages/core/agent-loop/src/assistant-stream.ts  AssistantStreamAttempt
packages/llm/llm/src/types.ts                  TokenUsage, StreamChunk, ContentBlockMap, LlmModelContext
packages/llm/llm/src/message.ts                Message union, MessageSourceMap
packages/llm/llm/src/assistant-stream.ts       AssistantStreamRecord + readers
packages/llm/llm/src/call-config.ts            LlmCallConfig, LlmCallConfigAdapterDefaults
packages/llm/llm-deepseek/src/translate.ts     provider usage translation
packages/llm/token-meter/src/index.ts          TokenMeter.measure
packages/llm/token-meter/src/estimate.ts       CHARS_PER_TOKEN/BLOCK_OVERHEAD/ROLE_OVERHEAD estimators
packages/llm/token-meter/src/usage-projection.ts     tokenUsage + contextPressure folds
packages/llm/token-meter/src/breakdown-projection.ts contextBreakdown fold
packages/llm/token-meter/src/turn-usage.ts     deriveTurnTokenUsage (strict per-turn fold)
packages/llm/token-meter/src/projection.ts     projection value types + SessionProjectionMap merges
packages/llm/token-meter/src/route-pricing.ts  priceSurface (token pricing, not money)
packages/llm/llm-retry/src/types.ts            llm/retry, llm/retry-started payloads
packages/session/session-format/src/*.ts       chain/catalog/codec contract, filename rules
packages/session/session-format-v{0..3}-to-v{1..4}/src/  adjacent migrations + frozen codecs
packages/session/session-format-catalog/src/   generated catalog, current validation, children
packages/session/session-persistence/src/handle.ts  SessionHandle contract (durability semantics)
packages/session/session-persistence-jsonl/src/format.ts     paths, header line, SessionLogScanner
packages/session/session-persistence-jsonl/src/index.ts      create/open/stat/list/persistBatch/repair
packages/session/session-persistence-jsonl/src/storage.ts    live write-behind buffer, handle
packages/session/session-persistence-jsonl/src/zstd.ts       frame scan/compress/torn recovery
packages/session/session-persistence-jsonl/src/generation.ts migration publication + verification
packages/session/session-checkpoint-policy/src/index.ts      flush barriers
packages/session/session-stats/src/projection.ts             sessionStats fold
packages/session/session-projection/src/types.ts             projection tables
packages/session/session-projection-cache/src/index.ts       durable projection checkpoints
packages/session/session-turn-outline/src/projection.ts      turnOutline fold
packages/session/session-title/src/types.ts                  title event + snapshot
packages/session/session-telemetry/src/coordinator.ts        capture, attributes, redaction waterfall
packages/session/session-telemetry-otel/src/index.ts         OTel backend (logs only)
packages/telemetry/otel/src/session-log.ts                   byte-bounded OTLP log queue (4e6 bytes)
packages/session-query/session-query/src/index.ts            SessionQueryEngine
packages/session-query/session-query/src/types.ts            query/record/trace types
packages/session-query/session-query/src/cold-read.ts        readColdSessionLog (in-memory balancing)
packages/session-query/session-query-sqlite/src/schema.ts    FTS5 schema
packages/session-query/session-log-export/src/archive.ts     canonical-JSONL ZIP export
packages/compaction/compaction/src/types.ts                  compaction/* events, CompactionResult
packages/compaction/compaction/src/checkpoint.ts             compactCheckpointSource + predicate
packages/compaction/compaction-basic/src/config.ts           thresholds / retention / budget math
packages/compaction/compaction-basic/src/index.ts            triggers, compactIfNeeded/compactNow
packages/compaction/compaction-basic/src/region.ts           range selection + transaction
packages/compaction/compaction-basic/src/summarizer.ts       prompt, frameSummary, summarizeWithLlm
packages/compaction/compaction-tool-result-pruner/src/types.ts  prune config + result types
packages/runtime-diagnostics/invariants/src/index.ts         invariant registry
packages/client/ui-chat/src/client/chat/StatsPills.tsx       stats + usage pills
packages/client/ui-chat/src/client/chat/TurnUsagePanel.tsx   per-turn usage panel
packages/client/ui-conversation/src/client/skeleton/ContextMeter.tsx  context ring + breakdown
packages/client/ui-conversation/src/client/context-occupancy.ts  occupancy math
packages/client/ui-chat/src/client/contract/turn-metrics.ts  per-step TTFT/decode readings
docs/subsystems/session.md                     in-memory model, surface, fork, turn-end reasons
docs/subsystems/persistence.md                 durability seam, flush checkpoint, crash recovery
docs/subsystems/session-query.md               query vocabulary
docs/subsystems/session-telemetry.md           telemetry record/attributes/redaction
docs/subsystems/compaction.md                  compaction seam
docs/subsystems/token-meter.md                 TokenMeasurement / TokenSurfaceNode
docs/subsystems/session-projection.md          projection registry
docs/persistence-catalog.md                    generated: every event + resolved payload (8780 lines)
docs/session-format-status.md                  writer constant vs finalized vs released versions
```
