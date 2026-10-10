# 08 — Memory write path (ZCode core)

**Scope:** the parts of the MEMORY subsystem not covered by the earlier map entries — the extraction
agent loop, the extraction-agent tool allowlist and path confinement, the recall/manifest layer, the
`Memory` system-prompt section, `originSessionId` / `node_type` frontmatter injection, and the
scheduler lifecycle. Read fully: every file under `memory/` (including `recall/`) plus the three
runtime helpers and the two context sections that render memory.

**Citation convention:** the first mention of each file gives its repo-relative path; afterwards the
bare filename is used, always with `:line`. All quotes are verbatim from the checkout at
`/Users/eric/projects/ZCode` (read in full, not sampled). Line numbers are 1-based.

| Shorthand | Full path |
| --- | --- |
| `memory-agent-loop.ts` | `apps/zcode-cli/packages/core/src/memory/memory-agent-loop.ts` |
| `memory-file-path.ts` | `apps/zcode-cli/packages/core/src/memory/memory-file-path.ts` |
| `extraction.ts` | `apps/zcode-cli/packages/core/src/memory/extraction.ts` |
| `origin-session.ts` | `apps/zcode-cli/packages/core/src/memory/origin-session.ts` |
| `index-content.ts` | `apps/zcode-cli/packages/core/src/memory/index-content.ts` |
| `directory.ts` | `apps/zcode-cli/packages/core/src/memory/directory.ts` |
| `project-root.ts` | `apps/zcode-cli/packages/core/src/memory/project-root.ts` |
| `recall/manifest.ts` | `apps/zcode-cli/packages/core/src/memory/recall/manifest.ts` |
| `recall/types.ts` | `apps/zcode-cli/packages/core/src/memory/recall/types.ts` |
| `recall/index.ts` | `apps/zcode-cli/packages/core/src/memory/recall/index.ts` |
| `project-memory.ts` | `apps/zcode-cli/packages/core/src/runtime/helpers/project-memory.ts` |
| `project-memory-agent.ts` | `apps/zcode-cli/packages/core/src/runtime/helpers/project-memory-agent.ts` |
| `project-memory-extraction.ts` | `apps/zcode-cli/packages/core/src/runtime/helpers/project-memory-extraction.ts` |
| `sections/memory.ts` | `apps/zcode-cli/packages/core/src/context/sections/memory.ts` |
| `sections/request-user-context.ts` | `apps/zcode-cli/packages/core/src/context/sections/request-user-context.ts` |
| `memory-file-permission.ts` | `apps/zcode-cli/packages/core/src/tool/executor/memory-file-permission.ts` |

**Verification note:** there are no test files of any kind in this checkout (`find` for `*.test.ts` /
`*.spec.ts` / `test` directories under `apps/zcode-cli` returns nothing), so every claim below is
established by reading implementation code only.

---

## 0. Mental model in one paragraph

Memory is a **file-based, agent-maintained store** that lives *outside* the project tree, at
`<cliStorageRoot>/memories/projects/<slug>-<hash>/memory` (`project-root.ts:23`). Two independent
model-driven surfaces touch it. **(a) The main agent** gets a static instruction block (the `Memory`
system section) plus a **one-time snapshot of `MEMORY.md`** in the meta-user context, and writes
memory files itself with ordinary `Write`/`Edit`. **(b) A background extraction agent** is scheduled
after every successful turn; it re-requests the turn's model with the *same* system prompt and the
conversation prefix, plus an extraction instruction, and may `Read`/`Grep`/`Glob`/read-only-`Bash`
freely but may only mutate `.md` files inside the memory root. There is **no vector search, no
scoring, and no semantic recall layer**: the only "recall" primitives are (i) the `MEMORY.md` index
injected into context, (ii) a manifest of existing memory files injected into the *extraction* prompt
so it updates instead of duplicating, and (iii) the main agent's own `Read`/`Grep`/`Glob` over the
memory directory. Ordering is by mtime only.

---

## 1. Entry points and enablement gating

### 1.1 Scheduling site (single, success-only)

`apps/zcode-cli/packages/core/src/runtime/methods/turn.ts:698-703` — the only scheduling call:

```ts
        // 单轮执行策略只抑制本次成功 Turn 的后台提取，不修改 Session Memory 配置。
        if (options?.modelExecution?.memoryExtraction !== "skip") {
          scheduleProjectMemoryExtraction(this, {
            model: loopState.model,
            traceContext: turnTraceContext,
          });
        }
```

It sits on the turn **success** path only (after `rebuildProjection()`); the `catch` branch at
`turn.ts:708+` never schedules. The per-turn escape hatch is
`ModelExecutionContext.memoryExtraction?: "skip"` (`runtime/types.ts:426-427`:
*"仅当前 Turn 跳过自动 Project Memory Extraction；不修改 Session Memory 配置。"*).

### 1.2 Guard chain in `scheduleProjectMemoryExtraction`

`project-memory-extraction.ts:32-43`, quoted in order — each returns silently:

```ts
export function scheduleProjectMemoryExtraction(
  runtime: AgentRuntimeInternal,
  input: { model: ProjectMemoryAgentContext["model"]; traceContext: TraceContext },
): void {
  if (runtime.shuttingDown) return;
  // 原因：headless 只关闭自动 Extraction，必须在读取快照或访问文件前返回，避免后台副作用。
  if (runtime.config.memory?.extractionEnabled === false) return;
  // Bash cd 只改变执行 cwd，project Memory 身份必须继续使用会话 workspace root。
  const memoryRoot = resolveEnabledProjectMemoryRoot(runtime.config, runtime.workspaceRoot);
  if (!memoryRoot) return;
  if (runtime.isRemoteWorkspace()) return;
  if (!runtime.sessionStore || !runtime.fileSystemPort) return;
```

`resolveEnabledProjectMemoryRoot` (`project-memory.ts:4-17`) is the shared gate used by context
loading, the scheduler, and the main-agent tool executor:

```ts
export function resolveEnabledProjectMemoryRoot(
  config: AgentRuntimeConfig,
  workspacePath: string,
): string | undefined {
  const memory = config.memory;
  if (!memory?.enabled || memory.use === false || !memory.cliStorageRoot) return undefined;
  if (!isMainMemoryTaskType(config.taskType)) return undefined;

  return resolveProjectMemoryRoot({
    cliStorageRoot: memory.cliStorageRoot,
    workspaceIdentity: memory.workspaceIdentity,
    workspacePath,
  });
}
```

`isMainMemoryTaskType` (`project-memory.ts:19-26`) admits only:

```ts
  return (
    taskType === undefined ||
    taskType === "interactive" ||
    taskType === "fork" ||
    taskType === "selection_side_chat" ||
    taskType === "workflow_parent"
  );
```

So `subagent_child`, `workflow_child`, and any other task type get **no** memory root at all — no
section, no index, no extraction. `isProjectMemoryEnabled()` (`project-memory-extraction.ts:28-30`)
is just `resolveEnabledProjectMemoryRoot(...) !== undefined`, used by `zcode --memory-bench` to fail
fast (`cli/src/prompt-command.ts:253`) .

### 1.3 Root identity and drift resistance

`project-root.ts:10-33`:

```ts
export function resolveProjectMemoryRoot(input: ProjectMemoryRootInput): string {
  const workspaceIdentity = input.workspaceIdentity?.trim();
  const normalizedWorkspacePath = resolve(input.workspacePath);
  const keySource =
    workspaceIdentity ||
    (process.platform === "win32"
      ? normalizedWorkspacePath.toLowerCase()
      : normalizedWorkspacePath);
  const hash = createHash("sha256").update(keySource).digest("hex").slice(0, 16);
  const slug = workspaceIdentity
    ? "project"
    : sanitizeProjectSlug(basename(normalizedWorkspacePath) || "project");

  return join(input.cliStorageRoot, "memories", "projects", `${slug}-${hash}`, "memory");
}
```

with `sanitizeProjectSlug` = lowercase → `[^a-z0-9._-]+ → "-"` → trim dashes → `.slice(0, 48)` →
fallback `"project"` (`project-root.ts:26-33`).

Stability across resume is explicitly protected in two places:

- `runtime/methods/events.ts:582-586` persists the opaque identity without re-generating it:
  ```ts
    const workspaceIdentity = this.config.memory?.workspaceIdentity?.trim();
    await this.sessionStore.createSession({
      id: this.sessionId,
      projectID: projectIdFromDirectory(directory),
      // Memory workspaceIdentity 是上游提供的不透明隔离键。这里只做类型品牌化，
      // 不能调用会改写字符串的 ID 生成器，否则恢复后的 Memory root 会发生漂移。
      workspaceID: this.config.workspaceIdentity ?? (workspaceIdentity as WorkspaceId | undefined),
```ts
export async function ensureMemoryDirectoryExists(
  fileSystemPort: FileSystemPort,
  rootDir: string,
  traceContext?: TraceContext,
  logger?: Logger,
): Promise<void> {
  try {
    await fileSystemPort.createDirectory({ path: rootDir, trace: traceContext });
  } catch (error) {
    // 目录预创建失败不阻断执行；实际 Write/Edit 仍返回原始文件错误。
    logger?.debug("Memory directory creation failed", {
```

Called from `runtime/methods/context.ts:164` (inside `loadProjectMemoryRoot`, defined at `:147`) and from subagent persistent
memory (`subagent/persistent-memory.ts:87-92`).

---

## 2. `runMemoryAgentLoop` — the extraction agent loop

Full function, `memory-agent-loop.ts:41-119`:

```ts
export async function runMemoryAgentLoop(input: {
  abortSignal?: AbortSignal;
  executeTool: (
    toolCall: ExecutableToolCall,
    options: { abortSignal?: AbortSignal },
  ) => Promise<ToolExecutionResult>;
  maxTurns: number;
  messages: readonly ModelInputMessage[];
  model: Model;
  rootDir: string;
  tools: readonly ModelToolContract[];
  workingDirectory: string;
  workspaceRoot: string;
}): Promise<MemoryAgentLoopResult> {
  const messages = input.messages.map(cloneModelMessage);
  let turns = 0;

  for (; turns < input.maxTurns; turns += 1) {
    input.abortSignal?.throwIfAborted();
    // 只在 Memory 初始快照投影会漏掉 Read 等工具后续产生的媒体；每一次
    // provider 请求都必须在 request-local 副本上执行同一套 capability + budget 策略。
    const mediaProjection = projectMessagesForModelMediaPolicy(
      messages.map(cloneModelMessage),
      input.model.properties.inputFormat,
    );
    const request: ModelRequest = {
      abortSignal: input.abortSignal,
      messages: mediaProjection.messages,
      options: auxiliaryModelOptions(input.model),
      // Memory agent 的 provider request 必须保留 Main 的真实工具目录；执行权限只在 tool-use 边界收窄。
      tools: input.tools as ModelToolContract[],
    };
    const response = await input.model.generateText(request);
    input.abortSignal?.throwIfAborted();

    const toolCalls = response.toolCalls ?? [];
    messages.push(createAssistantMessage(response.text, response.reasoning, toolCalls));
    if (toolCalls.length === 0) {
      turns += 1;
      break;
    }

    const toolMessages = await Promise.all(
      toolCalls.map(async (toolCall): Promise<ModelInputMessage> => {
        const decision = evaluateMemoryAgentToolPolicy({
          rootDir: input.rootDir,
          toolCall,
          tools: input.tools,
          workingDirectory: input.workingDirectory,
          workspaceRoot: input.workspaceRoot,
        });
        if (!decision.allowed) {
          return {
            content: decision.reason,
            isError: true,
            role: "tool",
            toolCallId: toolCall.id,
            toolName: toolCall.name,
          };
        }

        const result = await input.executeTool(
          { id: toolCall.id, input: toolCall.input, name: toolCall.name },
          { abortSignal: input.abortSignal },
        );
        return {
          content: modelContentForToolResult(result),
          isError: isErrorForToolResult(result),
          role: "tool",
          toolCallId: toolCall.id,
          toolName: toolCall.name,
        };
      }),
    );
    messages.push(...toolMessages);
  }

  return { messages, turns };
}
```

### 2.1 Turn structure and what "budget" means

| Fact | Value | Source |
| --- | --- | --- |
| Max turns | `5` | `project-memory-extraction.ts:19` `const EXTRACTION_MAX_TURNS = 5;` |
| Provider requests per extraction | ≤ 5 (one per loop iteration) | `memory-agent-loop.ts:58` |
| Output-token cap per request | `min(5000, model.optionSpecs.maxOutputTokens.max)` | `model/auxiliary-model-options.ts:3,9-15` |
| Reasoning level | forced to `optionSpecs.reasoningLevel.values[0]` (lowest) | same |
| Request media budget | `40 * 1024 * 1024` bytes, re-projected per request | `runtime/helpers/media-budget.ts:28,51-67` |
| Tool-call concurrency | unbounded `Promise.all` over all tool calls in one assistant message | `memory-agent-loop.ts:83` |
| Drain timeout | `60_000` ms, or unbounded when `null` | `project-memory-extraction.ts:20,83-107` |

There is **no token budget accounting and no wall-clock deadline inside the loop** — the only
resource caps are turn count, per-request output tokens, and the per-request media budget. Each
iteration re-projects media on a fresh clone (`memory-agent-loop.ts:62-65`, comment: *"每一次 provider
请求都必须在 request-local 副本上执行同一套 capability + budget 策略"*), because the initial
snapshot cannot cover media produced by `Read` during the loop.

`auxiliaryModelOptions` (`model/auxiliary-model-options.ts:9-15`), quoted in full:

```ts
export function auxiliaryModelOptions(model: Model): Required<ModelOptions> {
  return {
    reasoningLevel: model.optionSpecs.reasoningLevel.values[0]!,
    maxOutputTokens: Math.min(AUXILIARY_MAX_OUTPUT_TOKENS, model.optionSpecs.maxOutputTokens.max),
  };
}
```

### 2.2 The tool surface *granted at the provider boundary* is the full Main catalog

This is the single most counter-intuitive property of the loop. `memory-agent-loop.ts:68-72` sends
`input.tools` — which is `captureProjectMemoryAgentContext`'s snapshot of the **main runtime's**
catalog (`project-memory-agent.ts:75`):

```ts
    tools: runtime.getTools(model).map((tool) => ({ ...tool })),
```

`getTools` (`runtime/methods/config.ts:136-147`) is the runtime's provider-visible catalog —
read-only tools, `Write`, `Edit`, `Bash`, subagent/agent tools, MCP tools, workflow tools, etc.,
ordered by provider-visible order and filtered only for embedded-search visibility and
`WebSearch` capability. So **the extraction agent sees tools it can never run**. The inline comment
states the intent (`memory-agent-loop.ts:70`): *"Memory agent 的 provider request 必须保留 Main 的真实工具
目录；执行权限只在 tool-use 边界收窄。"* Permission is narrowed at the tool-use boundary only, by
`evaluateMemoryAgentToolPolicy`.

Consequently the extraction prompt's tool description (`extraction.ts:54`) is the model-facing
truth, while the catalog is a cache/prompt-consistency artifact.

### 2.3 The extraction prompt (the loop's only input)

`project-memory-extraction.ts:137-145` builds it from the manifest and message count;
`memory-agent-agent`-side assembly is `project-memory-agent.ts:82-97`, which appends the prompt as a
final **user** message on top of the borrowed message-history entries:

```ts
export function buildProjectMemoryAgentProviderMessages(
  runtime: AgentRuntimeInternal,
  context: ProjectMemoryAgentContext,
  prompt: string,
): ModelInputMessage[] {
  const entries: RuntimeMessageEntry[] = [
    ...context.providerEntries,
    { message: { content: prompt, role: "user" } },
  ];
  return buildRuntimeProviderRequestMessages(
    {
      config: { midConversationSystem: context.midConversationSystem },
    },
    { applyCacheControl: true, entries, model: context.model },
  ).messages;
}
```

Because `providerEntries` is
`runtime.messageHistory.borrowReadOnlyRuntimeEntries()` (`project-memory-agent.ts:70`), the extraction
request **includes the context prefix** — the `Memory` system section and the `MEMORY.md` index — which
is why the prompt can say the format rules are "already in your context above"
(`extraction.ts:64`). The snapshot is a member-wise copy taken synchronously before any `await`
(`project-memory-agent.ts:67-70`), with an explicit contract that entries stay immutable:

> *"Extraction 会跨异步边界消费这份成员浅快照；它依赖 RuntimeMessageEntry 进入 MessageHistory 后保持不可变。后续只能 append、整体 replace 或 copy-on-write，禁止原地修改共享的 entry/message/content，否则会污染已调度的 Memory 上下文。"*

Full prompt text (`extraction.ts:51-65`), which is the actual instruction set:

```ts
  return [
    `You are now acting as the memory extraction subagent. Analyze the most recent ~${input.messageCount} messages above and use them to update your persistent memory systems.`,
    "",
    "Available tools: Read, Grep, Glob, read-only Bash (ls/find/cat/stat/wc/head/tail and similar), and Edit/Write for paths inside the memory directory only, and Bash rm with paths inside the memory directory only. All other tools \u2014 MCP, Agent, write-capable Bash, etc \u2014 will be denied.",
    "",
    "You have a limited turn budget. Edit requires a prior Read of the same file, so the efficient strategy is: turn 1 \u2014 issue all Read calls in parallel for every file you might update; turn 2 \u2014 issue all Write/Edit calls in parallel. Do not interleave reads and writes across multiple turns.",
    "",
    `You MUST only use content from the last ~${input.messageCount} messages to update your persistent memories. Do not waste any turns attempting to investigate or verify that content further \u2014 no grepping source files, no reading code to confirm a pattern exists, no git commands.${existingMemories}`,
    "",
    "If nothing is worth saving, output only 'Nothing to save.' Do not explain why.",
    "",
    "If the user explicitly asks you to remember something, save it immediately as whichever type fits best. If they ask you to forget something, find and remove the relevant entry.",
    "",
    "Apply the memory types, what-not-to-save criteria, and frontmatter format from the Memory section of your system prompt \u2014 it is already in your context above.",
  ].join("\n");
```

with the conditional dedup block (`extraction.ts:46-49`):

```ts
  const existingMemories =
    input.manifest.length > 0
      ? `\n\n## Existing memory files\n\n${formatMemoryManifest(input.manifest)}\n\nCheck this list before writing \u2014 update an existing file rather than creating a duplicate.`
      : "";
```

Note the tension the prompt creates: it tells the model not to verify ("no grepping source files")
*after* the system prompt has told it "Edit requires a prior Read of the same file" — hence the
prescribed two-turn read-then-write strategy, which is exactly what the 5-turn budget is sized for.

### 2.4 How writes are validated

Validation happens in three layers, none of which is a content validator:

1. **Loop policy** (`memory-agent-loop.ts:136-140`): `Write`/`Edit` allowed only when
   `file_path` ends with `.md` **and** `resolveSafeMemoryFilePath(...)` returns a path. Quoted:
   ```ts
  if (input.toolCall.name === "Write" || input.toolCall.name === "Edit") {
    return isContainedMarkdownMutation(input)
      ? { allowed: true }
      : denyMemoryAgentTool(input.rootDir);
   }
   ```
2. **Tool handler freshness rules** (`tool/handlers/write.ts:282-288`, `tool/handlers/edit.ts:420-432`):
   `Write`/`Edit` on an existing file require a prior, non-partial read of that path; the extraction
   executor inherits the main runtime's read-state (`project-memory-agent.ts:119-121`):
   ```ts
    // Memory agent 必须继承 Main 已完成的 Read；否则 provider context 说文件已读，
    // Edit 执行边界却会拒绝同一文件，和基线的 cloned tool context 不一致。
    readFileState: new Map(context.readFileState),
   ```
3. **Frontmatter stamping** — `stampMemoryOriginSessionId` runs on every `.md` write inside the root
   (§6).

There is **no validation that the written content actually contains a memory, no schema check of
`type:`, no size check, and no post-write verification pass.** A malformed or empty file is written
as-is; the only schema-ish consumer is the manifest's frontmatter parser, which silently ignores
what it cannot parse (`recall/manifest.ts:109-119`).

### 2.5 Stop conditions

| Condition | Behaviour | Source |
| --- | --- | --- |
| Assistant reply with **zero tool calls** | `turns += 1; break;` → returns `{messages, turns}` | `memory-agent-loop.ts:78-81` |
| Reached `maxTurns` (5) | `for` condition fails; returns `{messages, turns: 5}` | `memory-agent-loop.ts:58,118` |
| `abortSignal` aborted | `throwIfAborted()` before the request and after the response | `memory-agent-loop.ts:59,74` |
| Any tool call denied | **not** a stop condition — a synthetic `isError: true` tool message is appended and the loop continues | `memory-agent-loop.ts:92-100` |
| `generateText` throws | propagates out of the loop | `memory-agent-loop.ts:73` |
| `executeTool` throws | `Promise.all` rejects → propagates out of the loop | `memory-agent-loop.ts:102-114` |

The loop never returns its messages to anyone: the sole caller discards the return value
(`project-memory-extraction.ts:148-162`) and the whole exchange is excluded from transcripts
(`project-memory-agent.ts:50-54`, `skipTranscript: true`). **Tool denials cannot stall the loop
either way** — there is no "N consecutive denials → stop" circuit breaker, so a model that keeps
calling MCP tools burns all 5 turns and returns `"success"` (which advances the cursor; §7.2).

### 2.6 `"Nothing to save."` handling — there is none, literally

`grep -rn 'Nothing to save' apps/zcode-cli/packages` returns exactly **one** hit: the prompt string at
`extraction.ts:60`. There is no parser, no string comparison, no special status. The sentence is a
*model-facing convention* that produces a text-only assistant message, which lands on the generic
zero-tool-call stop condition (`memory-agent-loop.ts:78-81`). Two consequences:

- The optimistic outcome is indistinguishable from a lazy one: a model that answers "Nothing to
  save.", a model that answers "Done!", and a model that answers with an empty string all end the
  loop identically.
- Both end the extraction as `"success"` (`project-memory-extraction.ts:163-164`), so the cursor
  advances and the same messages are never re-extracted. `"no-op"` exists in the status union
  (`extraction.ts:8`) and is honored by the scheduler (`extraction.ts:118`) but **is never returned by
  any implementation** — `executeProjectMemoryExtraction` returns only `"success" | "aborted" |
  "error"`.

### 2.7 Error and abort taxonomy

`project-memory-extraction.ts:126-174` wraps the loop in a detached telemetry span:

```ts
  return telemetry.run(async () => {
    try {
      const manifest = await scanMemoryManifest({
        fileSystem: runtime.fileSystemPort!,
        rootDir: input.snapshot.memoryRoot,
        signal: input.abortSignal,
      });
      if (input.abortSignal.aborted) {
        telemetry.finishCancelled("abort_signal");
        return "aborted" as const;
      }
      ...
      await runMemoryAgentLoop({ ... });
      telemetry.finishCompleted();
      return "success" as const;
    } catch (error) {
      if (input.abortSignal.aborted || isAbortError(error)) {
        telemetry.finishCancelled("abort_signal");
        return "aborted" as const;
      }
      telemetry.finishFailed("execute", "internal", error);
      return "error" as const;
    }
  });
```

`isAbortError` (`project-memory-extraction.ts:176-178`) matches **only** `DOMException` with
`name === "AbortError"`:

```ts
function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}
```

So an abort surfaced as e.g. a plain `Error("aborted")` or a custom `AbortError` class is classified
`"error"` rather than `"aborted"` — a telemetry-only difference, since neither status advances the
cursor.

Telemetry span identity (`project-memory-extraction.ts:117-124`):

```ts
  return telemetry.run(async () => {
    try {
      const manifest = await scanMemoryManifest({
        fileSystem: runtime.fileSystemPort!,
        rootDir: input.snapshot.memoryRoot,
        signal: input.abortSignal,
      });
      if (input.abortSignal.aborted) {
        telemetry.finishCancelled("abort_signal");
        return "aborted" as const;
      }
      const prompt = buildMemoryExtractionPrompt({
        manifest,
        messageCount: input.messageCount,
      });
      const providerMessages = buildProjectMemoryAgentProviderMessages(
        runtime,
        input.snapshot,
        prompt,
      );
      const executor = createProjectMemoryAgentToolExecutor(runtime, input.snapshot);

      await runMemoryAgentLoop({
        abortSignal: input.abortSignal,
        executeTool: (toolCall, options) =>
          executor.execute(toolCall, {
            signal: options.abortSignal,
            traceContext: input.snapshot.traceContext,
          }),
        maxTurns: EXTRACTION_MAX_TURNS,
        messages: providerMessages,
        model: input.snapshot.model,
        rootDir: input.snapshot.memoryRoot,
        tools: input.snapshot.tools,
        workingDirectory: input.snapshot.workingDirectory,
        workspaceRoot: input.snapshot.workspaceRoot,
      });
      telemetry.finishCompleted();
      return "success" as const;
    } catch (error) {
      if (input.abortSignal.aborted || isAbortError(error)) {
        telemetry.finishCancelled("abort_signal");
        return "aborted" as const;
      }
      telemetry.finishFailed("execute", "internal", error);
      return "error" as const;
    }
  });
```ts
  const model = withModelInvocationContext(baseModel, (request) => ({
    // Extraction 是 transcript 的消费者；不把它自己的请求写回同一 model-io 目录，
    // 避免后台链路占用 rollout 槽位并在后续 Extraction 中自反馈。
    metadata: {
      ...traceContextToLogContext(input.traceContext),
      querySource: input.operation,
      skipTranscript: true,
    },
    modelRequestSessionType: "other",
    modelCall: { operation: input.operation },
    refreshRuntimeHeadersBeforeAttempt: createRefreshRuntimeHeadersBeforeModelAttempt(runtime, {
      abortSignal: request.abortSignal,
      model,
      traceContext: input.traceContext,
    }),
    traceContext: input.traceContext,
  }));
```

### 2.8 Tool executor given to the loop

`project-memory-agent.ts:99-129` — same registry/ports as Main, but with three decisive overrides:

```ts
  return createToolExecutor({
    artifactStore: runtime.artifactStore,
    emitEvent: async () => {},
    executionPort: runtime.executionPort,
    fileSystemPort: runtime.fileSystemPort,
    getBashShellSelection: () => getSessionShellSelectionFromConfig(runtime.config),
    getMode: () => "yolo",
    getMemoryRoot: () => context.memoryRoot,
    getWorkingDirectory: () => context.workingDirectory,
    getWorkspaceRoot: () => context.workspaceRoot,
    imageProcessorPort: runtime.imageProcessorPort,
    pdfDocumentPort: runtime.pdfDocumentPort,
    maxConcurrency: runtime.config.toolConcurrency?.maxConcurrency,
    model: context.model,
    permissionBroker: createDenyPermissionBroker(),
    permissionService: new PermissionService(defaultPermissionConfig),
    // Memory agent 必须继承 Main 已完成的 Read；否则 provider context 说文件已读，
    // Edit 执行边界却会拒绝同一文件，和基线的 cloned tool context 不一致。
    readFileState: new Map(context.readFileState),
    registry: runtime.registry,
    runtimeScope: "main",
    sessionId: runtime.sessionId,
    sessionStore: runtime.sessionStore,
    skillPort: runtime.skillPort,
    traceContext: context.traceContext,
  });
```

- `emitEvent: async () => {}` — **memory-agent tool calls emit no session events**, so extraction
  reads/writes never appear in the timeline, transcript, or GUI.
- `getMode: () => "yolo"` — the permission service short-circuits to allow for non-`alwaysAsk`
  tools (`permission/service.ts:130-137`):
  ```ts
    // 声明 alwaysAsk 的工具必须经过用户确认，不能被权限模式的放行分支绕过。
    if (capability.alwaysAsk) {
      return this.checkAlwaysAsk(context, capability, projectRules, rulePolicy);
      }

    const planEnabled = context.planEnabled ?? context.mode === "plan";
    if (context.mode === "yolo" && !planEnabled) {
      return this.allow(context, capability, "mode.yolo", "Yolo mode bypasses permission prompts");
      }
  ```
  `Bash`, `Write`, and `Edit` do **not** declare `alwaysAsk` (only the workflow tools do:
  `tool/handlers/create-workflow.ts:368`, `amend-workflow.ts:329`, `save-workflow.ts:289`). Therefore
  **the loop's own `evaluateMemoryAgentToolPolicy` is the effective security boundary** — the
  permission layer will not second-guess it. This is worth stressing: `resolveWorkspacePath` does not
  hard-block paths outside `workspaceRoot` either (`tool/path-policy.ts:36-39`: *"Current release
  intentionally does not hard-block paths outside workspaceRoot"*), so nothing downstream re-checks
  containment.
- `createDenyPermissionBroker()` — anything that still reaches an `ask` decision (project
  `rule.project.ask` rules, hooks) is denied rather than prompted.

---

## 3. Exact tool allowlist and path confinement

### 3.1 The allowlist

`memory-agent-loop.ts:121-163`, quoted in full:

```ts
function evaluateMemoryAgentToolPolicy(
  input: MemoryAgentToolPolicyInput,
): MemoryAgentToolPolicyDecision {
  const contract = input.tools.find((tool) => tool.name === input.toolCall.name);
  // catalog miss 被合并进 Memory 权限拒绝会让未注册工具没有先走
  // No such tool 错误处理。此分支只闭合原 call id，不进入工具执行面。
  if (!contract) return denyUnavailableMemoryAgentTool(input.toolCall.name);

  if (
    input.toolCall.name === "Agent" ||
    input.toolCall.name.startsWith("mcp__") ||
    contract.sideEffectScope === "network"
  )
    return denyMemoryAgentTool(input.rootDir);

  if (input.toolCall.name === "Write" || input.toolCall.name === "Edit") {
    return isContainedMarkdownMutation(input)
      ? { allowed: true }
      : denyMemoryAgentTool(input.rootDir);
  }

  if (input.toolCall.name === "Bash") {
    const command = stringProperty(input.toolCall.input, "command");
    // Memory Agent 直接复用既有只读分类器，避免在此处二次收窄安全 env、redirect 和后台执行。
    if (
      command &&
      (isRuntimeReadOnlyBashCommand(command, {
        workingDirectory: input.workingDirectory,
        workspaceRoot: input.workspaceRoot,
      }) ||
        isContainedMarkdownBashRemoval(command, input))
    ) {
      return { allowed: true };
    }
    return denyMemoryAgentBash(input.rootDir);
  }

  if (MEMORY_AGENT_READ_ONLY_TOOLS.has(input.toolCall.name)) {
    return { allowed: true };
  }

  return denyMemoryAgentTool(input.rootDir);
}
```

with `memory-agent-loop.ts:39`:

```ts
const MEMORY_AGENT_READ_ONLY_TOOLS = new Set(["Read", "Grep", "Glob"]);
```

**Effective allowlist (exhaustive):**

| Tool | Condition |
| --- | --- |
| `Read`, `Grep`, `Glob` | unconditional |
| `Write`, `Edit` | `input.file_path` is a string ending in `.md` **and** `resolveSafeMemoryFilePath(...) !== undefined` |
| `Bash` | `isRuntimeReadOnlyBashCommand(command, {workingDirectory, workspaceRoot})` **or** `isContainedMarkdownBashRemoval(command, input)` |

**Explicit denials:** `Agent`; every `mcp__*`; any tool whose contract declares
`sideEffectScope === "network"`; any name absent from the catalog (answered with the standard
`<tool_use_error>Error: No such tool available: X</tool_use_error>`); everything else that is not
`Bash` and not in the read-only set. Note the allowlist is **name-based and additive** — a new tool
registered with `sideEffectScope: "read"` and a non-Bash, non-Read/Grep/Glob name is denied by the
fallthrough at `:162`, which is the safe direction.

### 3.2 Bash `rm` carve-out (the only write capable shell path)

`memory-agent-loop.ts:182-211`:

```ts
function isContainedMarkdownBashRemoval(
  command: string,
  input: MemoryAgentToolPolicyInput,
): boolean {
  const analysis = analyzeBashCommand(command);
  if (!isBashCommandPermissionSafe(analysis) || analysis.commands.length !== 1) return false;
  const invocation = analysis.commands[0];
  if (!invocation || invocation.argv[0] !== "rm") return false;
  if (invocation.redirects.length > 0 || invocation.envAssignments.length > 0) return false;

  let afterOptions = false;
  let pathCount = 0;
  for (const argument of invocation.argv.slice(1)) {
    if (!afterOptions) {
      if (argument === "--") {
        afterOptions = true;
        continue;
      }
      if (argument.startsWith("-")) {
        if (argument === "--recursive" || /^-[a-zA-Z]*[rR]/u.test(argument)) return false;
        continue;
      }
    }
    if (/[*?[]/u.test(argument)) return false;
    if (!isAbsolute(argument) || !argument.endsWith(".md")) return false;
    if (resolveContainedPath(argument, input) === undefined) return false;
    pathCount += 1;
  }
  return pathCount > 0;
}
```

Properties: exactly one command; no redirects; no env assignments; **no recursive flags** (both
`--recursive` and any short flag containing `r`/`R`); no glob metacharacters (`*`, `?`, `[`); every
target must be an absolute path ending in `.md` that resolves inside the memory root; at least one
target.

**Asymmetry (finding).** `rm` confinement goes through `resolveContainedMemoryFilePath`
(`memory-agent-loop.ts:213-227`), **not** `resolveSafeMemoryFilePath`. The sensitive-segment blocklist
that Write/Edit honor therefore does **not** apply to `rm`. A path like
`<memoryRoot>/node_modules/x.md` or `<memoryRoot>/.git/notes.md` is policy-allowed for `rm` but
policy-denied for `Write`/`Edit`. Combined with §2.8 (yolo mode ⇒ no permission-layer second
opinion), the loop policy is the only gate.

`rm` is also the only way the extraction agent can delete a memory — consistent with the system
prompt's "delete memories that turn out to be wrong" (`sections/memory.ts:48`) and the extraction
prompt's "If they ask you to forget something, find and remove the relevant entry"
(`extraction.ts:62`).

### 3.3 Path confinement: `resolveSafeMemoryFilePath`

`memory-file-path.ts:43-55`, quoted in full:

```ts
export function resolveSafeMemoryFilePath(input: {
  filePath: string;
  rootDir: string;
  workingDirectory: string;
  workspaceRoot: string;
}): string | undefined {
  const resolvedPath = resolveContainedMemoryFilePath(input);
  if (!resolvedPath) return undefined;
  const relativePath = memoryFileRelativePath(input.rootDir, resolvedPath);
  return relativePath && !containsSensitiveMemoryPathSegment(relativePath)
    ? resolvedPath
    : undefined;
}
```

Two-stage: containment first, then segment filtering.

**Stage 1 — containment** (`memory-file-path.ts:27-41`):

```ts
export function resolveContainedMemoryFilePath(input: {
  filePath: string;
  rootDir: string;
  workingDirectory: string;
  workspaceRoot: string;
}): string | undefined {
  const resolvedPath = resolveWorkspacePath({
    inputPath: input.filePath,
    operation: "write",
    workingDirectory: input.workingDirectory,
    workspaceRoot: input.workspaceRoot,
  });
  const relativePath = relative(input.rootDir, resolvedPath);
  return isContainedRelativePath(relativePath) ? resolvedPath : undefined;
}
```

Note `resolveWorkspacePath` (`tool/path-policy.ts:15-40`) only normalizes: absolute inputs are
`normalize()`d, relative inputs are `resolve()`d against the working directory, and — per the comment
at `:36-39` — **no workspace-root confinement is applied there**. Containment for memory comes
entirely from `relative(rootDir, resolvedPath)` + `isContainedRelativePath`.

`isContainedRelativePath` (`memory-file-path.ts:68-76`):

```ts
function isContainedRelativePath(relativePath: string): boolean {
  return (
    relativePath.length > 0 &&
    relativePath !== ".." &&
    !relativePath.startsWith("../") &&
    !relativePath.startsWith("..\\") &&
    !isAbsolute(relativePath)
  );
}
```

So `rootDir` itself (`relativePath === ""`) is rejected, as are all `..` escapes and any
cross-drive/UNC case that yields an absolute `relative()`. `memoryFileRelativePath`
(`:57-60`) reuses the same predicate and is the helper used by frontmatter stamping and the
`SENSITIVE` filter.

**Stage 2 — `SENSITIVE_MEMORY_PATH_SEGMENTS`** (`memory-file-path.ts:5-25`), verbatim:

```ts
const SENSITIVE_MEMORY_PATH_SEGMENTS = new Set([
  ".git",
  "hooks",
  ".husky",
  ".githooks",
  "node_modules",
  ".vscode",
  ".idea",
  "head",
  "config",
  "objects",
  "refs",
  ".zcode",
  "skills",
  "commands",
  "agents",
  ".cargo",
  ".devcontainer",
  ".yarn",
  ".mvn",
]);
```

Matched per path segment after normalization (`memory-file-path.ts:62-83`):

```ts
function containsSensitiveMemoryPathSegment(relativePath: string): boolean {
  return relativePath
    .split(/[\\/]+/u)
    .some((segment) => SENSITIVE_MEMORY_PATH_SEGMENTS.has(normalizeSensitiveSegment(segment)));
}

function isContainedRelativePath(relativePath: string): boolean {
  return (
    relativePath.length > 0 &&
    relativePath !== ".." &&
    !relativePath.startsWith("../") &&
    !relativePath.startsWith("..\\") &&
    !isAbsolute(relativePath)
  );
}

function normalizeSensitiveSegment(segment: string): string {
  const withoutControls = segment
    .toLowerCase()
    .replace(/[\u200c-\u200f\u202a-\u202e\u206a-\u206f\ufeff]/gu, "");
  return (withoutControls.split(":", 1)[0] ?? "").replace(/[. ]+$/u, "");
}
```ts
export function applyMemoryFilePermission(
  input: MemoryFilePermissionInput,
): PermissionDecisionResult {
  const target = resolveMemoryFileTarget(input);
  if (!target || !input.memoryRoot) return input.decision;

  if (
    !target.endsWith(".md") ||
    resolveSafeMemoryFilePath({
      filePath: target,
      rootDir: input.memoryRoot,
      workingDirectory: input.workingDirectory,
      workspaceRoot: input.workspaceRoot,
    }) === undefined
  ) {
    return input.decision;
  }
  if (preservesExistingPermissionDecision(input.decision)) return input.decision;

  return {
    ...input.decision,
    allowed: true,
    decision: "allow",
    escalated: false,
    reason: "Memory Markdown writes are allowed",
    ruleId: "memory.file.markdown",
  };
}

export function targetsMemoryFile(input: MemoryFileTargetInput): boolean {
  return resolveMemoryFileTarget(input) !== undefined;
}

function resolveMemoryFileTarget(
  input: MemoryFileTargetInput,
): string | undefined {
  if (input.toolName !== "Write" && input.toolName !== "Edit") return undefined;
  if (!input.memoryRoot) return undefined;

  const requestedPath = filePathFromInput(input.executionInput);
  if (!requestedPath) return undefined;
  return resolveContainedMemoryFilePath({
    filePath: requestedPath,
    rootDir: input.memoryRoot,
    workingDirectory: input.workingDirectory,
    workspaceRoot: input.workspaceRoot,
  });
}
```

Semantics — a **permission escalation bypass** for memory `.md` writes, so a memory write does not
prompt in `build`/plan-ish flows:

- Applies only to `Write`/`Edit`, only when a `memoryRoot` is configured, only when the target
  resolves inside it, and only for `.md`. Target resolution here uses the *containment-only*
  resolver; the `resolveSafeMemoryFilePath` check inside `applyMemoryFilePermission` re-imposes the
  sensitive-segment rule before allowing.
- `preservesExistingPermissionDecision` (`memory-file-permission.ts:74-86`) keeps hard denies
  (`deny` with any `ruleId` other than `mode.plan.nonReadOnly`), any `alwaysAsk`, and `ask` from
  `rule.project.ask` / `hook.PreToolUse.ask`. Everything else — including a mode-derived `ask` — is
  converted to `allow` with `ruleId: "memory.file.markdown"`.
- Called at two points in the executor: first evaluation (`tool/executor/permission-flow.ts:96-102`)
  and post-hook re-check (`tool/executor/permission-input-recheck.ts:59-66`), so a PreToolUse hook
  that rewrites `file_path` cannot dodge the rule.
- The main runtime supplies `memoryRoot` via `getMemoryRoot`
  (`runtime/helpers/runtime-tools.ts:223-224`):
  ```ts
    getMemoryRoot: () =>
      deps.memoryRoot ?? resolveEnabledProjectMemoryRoot(runtime.config, runtime.workspaceRoot),
  ```
  which also flows into the handler context at `tool/executor/call-runner.ts:434` as
  `memoryRoot: deps.getMemoryRoot?.()` — that context value is what `stampMemoryOriginSessionId`
  consumes (§6).

The extraction loop's executor also installs this same stack (`project-memory-agent.ts:110,117-118`),
but because it runs in `yolo`, `applyMemoryFilePermission` is a no-op there — the mode allow fires
first.

### 3.5 Denial messages (exact text)

`memory-agent-loop.ts:272-292`:

```ts
// 拒绝结果会进入下一轮 provider request，需按基线区分 Bash 与其他工具的固定文案。
function denyMemoryAgentBash(rootDir: string): MemoryAgentToolPolicyDecision {
  return {
    allowed: false,
    reason: `Only read-only shell commands and rm with all paths inside ${rootDir} are permitted in this context (ls, find, grep, cat, stat, wc, head, tail, and similar)`,
  };
}

function denyMemoryAgentTool(rootDir: string): MemoryAgentToolPolicyDecision {
  return {
    allowed: false,
    reason: `only Read, Grep, Glob, read-only Bash, and Edit/Write within ${rootDir} are allowed`,
  };
}

function denyUnavailableMemoryAgentTool(toolName: string): MemoryAgentToolPolicyDecision {
  return {
    allowed: false,
    reason: `<tool_use_error>Error: No such tool available: ${toolName}</tool_use_error>`,
  };
}
```

Denials are returned as `{content: reason, isError: true, role: "tool", toolCallId, toolName}`
(`memory-agent-loop.ts:92-100`) — the model sees a normal error tool result and gets to retry, which
is why denials do not stop the loop.

Read-only Bash classification is delegated wholesale to the main runtime's classifier
(`tool/handlers/bash-semantics.ts:39-66`), which composes `analyzeBashCommand`,
`isBashCommandPermissionSafe`, env/redirect/wrapper policies and a per-command argv allowlist
(`tool/handlers/bash-readonly-policy-argv.ts:25-45`). The loop's comment (`memory-agent-loop.ts:144`)
explains the reuse: *"Memory Agent 直接复用既有只读分类器，避免在此处二次收窄安全 env、redirect 和后台执行。"*

---

## 4. The recall / manifest layer

### 4.1 Types (the complete "recall type" set)

`recall/types.ts:1-11`, verbatim:

```ts
export const MEMORY_RECALL_TYPES = ["user", "feedback", "project", "reference"] as const;

export type MemoryRecallType = (typeof MEMORY_RECALL_TYPES)[number];

export interface MemoryManifestEntry {
  description?: string;
  filePath: string;
  filename: string;
  mtimeMs: number;
  type?: MemoryRecallType;
}
```

`recall/index.ts` is a two-line barrel exporting `formatMemoryManifest`, `scanMemoryManifest`, and the
types — nothing else. **There are no scoring, ranking, embedding, similarity, or search functions in
the recall layer.** `grep` for `scanMemoryManifest|formatMemoryManifest` finds exactly two consumers:
`extraction.ts:3,48` (formatting into the extraction prompt) and `project-memory-extraction.ts:9,128`
(scanning before extraction). The main agent never sees this manifest.

### 4.2 Manifest format (model-facing)

`recall/manifest.ts:35-44`, verbatim:

```ts
export function formatMemoryManifest(manifest: readonly MemoryManifestEntry[]): string {
  return manifest
    .map((entry) => {
      const type = entry.type ? `[${entry.type}] ` : "";
      const timestamp = new Date(entry.mtimeMs).toISOString();
      const base = `- ${type}${entry.filename} (${timestamp})`;
      return entry.description ? `${base}: ${entry.description}` : base;
    })
    .join("\n");
}
```

Rendered shape: `- [feedback] tooling/prefer-pnpm.md (2025-09-30T18:46:00.000Z): user wants pnpm not npm`.
`filename` is relative to `memoryRoot` with `sep` normalized to `/`
(`recall/manifest.ts:97`: `relative(rootDir, filePath).split(sep).join("/")`). There is **no cap on
description length** — a long `description:` on one file inflates the extraction prompt line
unbounded.

### 4.3 Scan, ordering, caps

`recall/manifest.ts:7-33`, verbatim:

```ts
const MANIFEST_FILE_LIMIT = 200;
const MANIFEST_PREVIEW_LINE_LIMIT = 30;

export async function scanMemoryManifest(input: {
  fileSystem: FileSystemPort;
  rootDir: string;
  signal?: AbortSignal;
}): Promise<MemoryManifestEntry[]> {
  try {
    const paths = await collectMemoryPaths(input.fileSystem, input.rootDir, input.signal);
    const settled = await Promise.allSettled(
      paths.map((filePath) =>
        readManifestEntry(input.fileSystem, input.rootDir, filePath, input.signal),
      ),
    );
    return settled
      .filter(
        (result): result is PromiseFulfilledResult<MemoryManifestEntry> =>
          result.status === "fulfilled",
      )
      .map((result) => result.value)
      .sort((left, right) => right.mtimeMs - left.mtimeMs)
      .slice(0, MANIFEST_FILE_LIMIT);
  } catch {
    return [];
  }
}
```

- **Recursive walk** (`:46-74`): recurse into `entry.kind === "directory"`; keep `file` entries whose
  path passes `isMemoryCandidate`; for `symlink` entries, `stat` the target and keep only if
  `target.kind === "file"`. Symlinks to directories are therefore **not** traversed (no cycle risk),
  and broken symlinks are dropped silently (`:68-70`: *"单个失效的文件 symlink 与单个无法读取的事实文件一样，不影响其他 manifest 项。"*).
- `isMemoryCandidate` (`:76-78`): `filePath.endsWith(".md") && basename(filePath) !== "MEMORY.md"` —
  the index is deliberately excluded from the manifest.
- **Ordering is mtime-descending only** (`:28`); there is no relevance score, no path priority, no
  type weighting. `MANIFEST_FILE_LIMIT` is applied *after* the sort, so it keeps the 200
  most-recently-modified candidate files; older files silently disappear from the dedup list (and can
  therefore be duplicated by the extraction agent).
- Failure handling: per-file failures are dropped by `allSettled`; any throw from the directory walk
  yields `[]` (`:30-32`) — so a transient FS error silently degrades the prompt to "no existing
  memories".
- No depth limit and no total-bytes cap on the walk; cost is bounded only by the 200-file slice after
  the (already complete) read phase — i.e. the previews for *all* candidates are read, and only then
  180+ of them are discarded. On a large memory tree this is O(all files) reads per extraction.

### 4.4 Per-file entry read and frontmatter parsing

`recall/manifest.ts:80-137`:

```ts
async function readManifestEntry(
  fileSystem: FileSystemPort,
  rootDir: string,
  filePath: string,
  signal?: AbortSignal,
): Promise<MemoryManifestEntry> {
  const [stat, preview] = await Promise.all([
    fileSystem.stat({ path: filePath }, { signal }),
    fileSystem.readTextFileRange(
      { path: filePath, offsetLine: 0, limitLines: MANIFEST_PREVIEW_LINE_LIMIT },
      { signal },
    ),
  ]);
  const frontmatter = parseMemoryFrontmatter(preview.content);
  return {
    ...(frontmatter.description ? { description: frontmatter.description } : {}),
    filePath,
    filename: relative(rootDir, filePath).split(sep).join("/"),
    mtimeMs: stat.mtimeMs ?? 0,
    ...(frontmatter.type ? { type: frontmatter.type } : {}),
  };
}

function parseMemoryFrontmatter(content: string): {
  description?: string;
  type?: MemoryRecallType;
} {
  const normalized = content.replace(/^\uFEFF/u, "").replace(/\r\n/gu, "\n");
  const lines = normalized.split("\n");
  if (lines[0] !== "---") return {};

  const end = lines.indexOf("---", 1);
  if (end < 0) return {};
  let parsed: unknown;
  try {
    parsed = parseYaml(lines.slice(1, end).join("\n"));
  } catch {
    return {};
  }
  if (!isRecord(parsed)) return {};

  const description = typeof parsed.description === "string" ? parsed.description : undefined;
  const metadata = isRecord(parsed.metadata) ? parsed.metadata : undefined;
  const typeCandidate = metadata?.type ?? parsed.type;
  const type = isMemoryRecallType(typeCandidate) ? typeCandidate : undefined;
  return {
    ...(description ? { description } : {}),
    ...(type ? { type } : {}),
  };
}
```

Details worth flagging:

- The frontmatter **must** start on the very first line as exactly `---` (BOM allowed, CRLF
  normalized); a leading blank line or a `# Title` before the fence makes the whole frontmatter
  invisible to recall (but `stampMemoryOriginSessionId` uses a *different*, more permissive regex —
  §6 — so a file can be stamped yet unparseable for the manifest).
- The preview is only the **first 30 lines** — a frontmatter block longer than 30 lines, or a
  `description` after a very long frontmatter, is not seen.
- `type` accepts **both** `metadata.type` (the documented shape) and a top-level `type`, with
  `metadata.type` winning. Values are validated against `MEMORY_RECALL_TYPES`; anything else is
  dropped (no error, no diagnostic).
- `description` is only accepted at the top level (not under `metadata`), and an empty string is
  dropped.
- Unknown frontmatter is ignored; parse errors are silent.

### 4.5 How recall *results* re-enter context

There are exactly two context paths, and **neither uses the manifest**:

**(a) `Memory` system section** — static instructions including the absolute memory root. Built by
`sections/memory.ts:8-23`, pushed at `context/builder.ts:151-157`:

```ts
      // Memory
      if (this.config.memoryRoot) {
        const memorySection = buildMemorySection(this.config.memoryRoot);
        if (memorySection) {
          sections.push(memorySection);
        }
      }
```

Section metadata: `injectionTarget: "system"`, `cacheHint: "dynamic"`, `source: "memory"` — so it
lands in the *dynamic system* block, after session guidance and before env-info, per
`orderSectionsForInjection` (`context/builder.ts:310-325`).

**(b) `MEMORY.md` index as meta-user context** — `sections/request-user-context.ts:73-86`:

```ts
function buildProjectMemoryIndexContent(
  memoryRoot: string | undefined,
  indexContent: string | undefined,
): string | null {
  if (!memoryRoot || indexContent === undefined) return null;
  const formatted = formatProjectMemoryIndexContent(indexContent);
  if (!formatted) return null;

  return [
    `Contents of ${join(memoryRoot, "MEMORY.md")} (user's auto-memory, persists across conversations):`,
    "",
    formatted,
  ].join("\n");
}
```

That block is appended after AGENTS.md-style instructions and wrapped in a header that frames the
whole aggregate as overriding instructions (`sections/request-user-context.ts:63-70`):

```ts
  return [
    // 聚合字段标题不能绑定到 AGENTS.md，否则仅有 Project Memory 时缺少标题。

    "# agentsMd",
    "Codebase and user instructions are shown below. Be sure to adhere to these instructions. IMPORTANT: These instructions OVERRIDE any default behavior and you MUST follow them exactly as written.",
    "",
    sections.join("\n\n"),
  ].join("\n");
```

So the memory *index* is delivered as user instructions with override semantics — noteworthy, since
`MEMORY.md` is agent-writable.

**Delivery timing (finding).** `runtime/memoryIndexContent` is loaded exactly once, during context
initialization (`runtime/methods/context.ts:66-68`):

```ts
  this.memoryRoot = await this.loadProjectMemoryRoot(traceContext);
  this.memoryIndexContent = await loadProjectMemoryIndexContent(this, this.memoryRoot);
  this.contextBuilder = this.createContextBuilderFromSnapshot(snapshot, this.memoryRoot, {
    memoryIndexContent: this.memoryIndexContent,
    model,
  });
```

and every later rebuild passes the **cached** value (`runtime/methods/context-refresh.ts:20,35`:
`memoryIndexContent: runtime.memoryIndexContent`). `loadProjectMemoryIndexContent` is only invoked
from that one site. Therefore, within a session, writes to `MEMORY.md` — by the main agent *or* by a
background extraction — do **not** refresh the index in the model's context; the model keeps seeing
the session-start snapshot until a cold resume (`resume.ts:135` sets `contextInitialized = false`) or
a new session. The one exception is the tool-result channel: an agent that `Read`s `MEMORY.md` sees
the live file in that turn.

**Index loading also seeds read-state** (`runtime/methods/context.ts:178-190`):

```ts
    runtime.readFileState.set(createReadFileStateKey(indexPath, undefined, undefined), {
      content: read.content,
      isPartialView: formattedContent !== read.content,
      limit: undefined,
      mtimeMs: normalizeReadFileStateMtimeMs(read.revision?.mtimeMs),
      offset: undefined,
      path: indexPath,
      readAt: runtime.now(),
      revisionId: read.revision?.id,
      sizeBytes: read.sizeBytes,
    });
    return read.content;
```

Two consequences: (i) the return value given to the context builder is the **raw** `read.content`
(formatting happens later in `sections/request-user-context.ts:78`); (ii) `isPartialView` is true whenever the
formatted view differs from the raw text — including the plain `trim()` inside
`formatMemoryIndexContent`. Since `Write`/`Edit` reject a target whose latest read state is missing or
partial (`tool/handlers/write.ts:284`, `tool/handlers/edit.ts:429`), **the first `Write` that creates
`MEMORY.md` succeeds, but any subsequent `Edit`/`Write` to it requires an explicit `Read` in the same
session** even when the file is small. Missing or unreadable index → `undefined` → no section
(`context.ts:191-193`, and `logMemorySkipped` for the disabled cases at `:150-165`).

### 4.6 The `MEMORY.md` formatter (caps + hygiene)

`index-content.ts:3-5` and `:8-38`:

```ts
const MEMORY_INDEX_LINE_LIMIT = 200;
const MEMORY_INDEX_CHARACTER_LIMIT = 25_000;
const LEADING_FRONTMATTER_PATTERN = /^---\s*\n[\s\S]*?---\s*\n?/u;
const HTML_COMMENT_PATTERN = /<!--[\s\S]*?-->/gu;

export function formatProjectMemoryIndexContent(content: string): string {
  const withoutFrontmatter = content.replace(LEADING_FRONTMATTER_PATTERN, "");
  return formatMemoryIndexContent(stripTopLevelMarkdownHtmlComments(withoutFrontmatter));
}

export function formatMemoryIndexContent(content: string): string {
  const trimmed = content.trim();
  if (!trimmed) return "";

  const lines = trimmed.split("\n");
  const lineCount = lines.length;
  const characterCount = trimmed.length;
  const lineTruncated = lineCount > MEMORY_INDEX_LINE_LIMIT;
  const characterTruncated = characterCount > MEMORY_INDEX_CHARACTER_LIMIT;
  if (!lineTruncated && !characterTruncated) return trimmed;

  let truncated = lineTruncated ? lines.slice(0, MEMORY_INDEX_LINE_LIMIT).join("\n") : trimmed;
  if (truncated.length > MEMORY_INDEX_CHARACTER_LIMIT) {
    const finalNewline = truncated.lastIndexOf("\n", MEMORY_INDEX_CHARACTER_LIMIT);
    truncated = truncated.slice(0, finalNewline > 0 ? finalNewline : MEMORY_INDEX_CHARACTER_LIMIT);
  }

  const sizeDescription =
    characterTruncated && !lineTruncated
      ? `${formatBytes(characterCount)} (limit: ${formatBytes(MEMORY_INDEX_CHARACTER_LIMIT)}) — index entries are too long`
      : lineTruncated && !characterTruncated
        ? `${lineCount} lines (limit: ${MEMORY_INDEX_LINE_LIMIT})`
        : `${lineCount} lines and ${formatBytes(characterCount)}`;

  return `${truncated}\n\n> WARNING: MEMORY.md is ${sizeDescription}. Only part of it was loaded. Keep index entries to one line under ~200 chars; move detail into topic files.`;
}
```ts
import { isMap, parseDocument } from "yaml";

import { memoryFileRelativePath } from "./memory-file-path.js";

const MEMORY_FRONTMATTER_PATTERN =
  /^(\uFEFF?---(?:\r\n|\n))([\s\S]*?)((?:\r\n|\n)---)(?=(?:\r\n|\n)|$)/u;

export function stampMemoryOriginSessionId(input: {
  content: string;
  filePath: string;
  memoryRoot: string | undefined;
  sessionId: string;
}): string {
  if (
    !input.memoryRoot ||
    !input.filePath.endsWith(".md") ||
    memoryFileRelativePath(input.memoryRoot, input.filePath) === undefined
  ) {
    return input.content;
  }

  const match = MEMORY_FRONTMATTER_PATTERN.exec(input.content);
  if (!match) return input.content;

  const opening = match[1]!;
  const frontmatter = match[2]!;
  const closing = match[3]!;
  const document = parseDocument(frontmatter);
  if (document.errors.length > 0) return input.content;

  try {
    if (document.get("metadata", true) === undefined) document.set("metadata", {});
    const metadata = document.get("metadata", true);
    if (!isMap(metadata)) return input.content;

    const existingOrigin = metadata.get("originSessionId");
    if (typeof existingOrigin === "string" && existingOrigin.length > 0) return input.content;

    // 基线只在缺少 origin 时序列化 frontmatter，并在同一次写入补齐 node_type。
    metadata.delete("node_type");
    metadata.items.unshift(document.createPair("node_type", "memory"));
    metadata.set("originSessionId", input.sessionId);
  } catch {
    // 非 mapping 的 metadata 不做修复；Memory 文件格式仍由 prompt 约束。
    return input.content;
  }

  const lineEnding = opening.endsWith("\r\n") ? "\r\n" : "\n";
  const stampedFrontmatter = document.toString().replace(/\n$/u, "").replace(/\n/gu, lineEnding);
  return `${opening}${stampedFrontmatter}${closing}${input.content.slice(match[0].length)}`;
}
```

### 6.1 When it applies

Preconditions (`:14-20`): a truthy `memoryRoot`; `filePath` ending in `.md`; and
`memoryFileRelativePath(memoryRoot, filePath) !== undefined` — i.e. the path must be **inside** the
memory root by the containment predicate. Everything else (any project source file, any non-md file)
is returned byte-identical. The function is called from exactly two places, both immediately before
the filesystem write:

- `tool/handlers/write.ts:118-123`:
  ```ts
  const contentToWrite = stampMemoryOriginSessionId({
      content,
      filePath,
    memoryRoot: context.memoryRoot,
      sessionId: context.sessionId,
    });
  ```
- `tool/handlers/edit.ts:501-506`:
  ```ts
  const contentToWrite = stampMemoryOriginSessionId({
    content: input.newContent,
    filePath: input.filePath,
    memoryRoot: input.context.memoryRoot,
      sessionId: input.context.sessionId,
    });
  ```

`context.memoryRoot` is the executor's `deps.getMemoryRoot?.()` (`tool/executor/call-runner.ts:434`),
so **both** the main agent's writes and the extraction agent's writes are stamped, with the writing
session's id.

### 6.2 What is injected

```yaml
---
metadata:
  node_type: memory
  originSessionId: <sessionId>
  ...pre-existing metadata keys...
```

- `node_type: memory` is put **first** in `metadata` (delete-then-`unshift`, `:40-41`) — so it is
  normalized on every qualifying write, even if a prior write placed something else first.
- `originSessionId` is set to the writing session id (`:42`).
- If `metadata` is absent a fresh mapping is created (`:32`); if it exists but is not a YAML map
  (e.g. a string or list) the content is returned unchanged (`:34`);
- **Idempotency / first-writer-wins:** if `metadata.originSessionId` is already a non-empty string,
  the function returns the *original* content untouched (`:36-37`) — so the origin session of a
  memory file is pinned to whoever created it, and later edits (including by extraction in other
  sessions) neither overwrite it nor re-serialize the frontmatter.
- **No frontmatter ⇒ no stamp** (`:22-23`): the regex requires `---` on the first line (BOM
  tolerated) and a closing `---` line. A memory file written without frontmatter never receives
  `originSessionId`/`node_type`. The regex is also more permissive than the manifest parser in one
  respect (it accepts any content between fences, including multi-document YAML), while both require
  the fence at position 0.
- Parse errors or YAML exceptions abort the stamp and return the original content (`:29`, `:43-46`);
  writes are never blocked by stamping.
- Line endings are preserved: the serialized frontmatter is re-expanded with the file's original
  ending (`:48-50`).
- Re-serialization normalizes the frontmatter block through the `yaml` document model (comments and
  formatting inside the block can be rewritten) — but only on the write that adds the origin, because
  of the first-writer-wins early return.

---

## 7. Scheduler lifecycle

### 7.1 The scheduler contract

`extraction.ts:32-40`:

```ts
export interface MemoryExtractionScheduler<
  TSnapshot extends MemoryExtractionSnapshot = MemoryExtractionSnapshot,
> {
  drain(): Promise<void>;
  getCursor(): MessageId | undefined;
  hasPendingWork(): boolean;
  schedule(snapshot: TSnapshot | Promise<TSnapshot>): void;
  shutdown(): void;
}
```

`schedule` accepts either a snapshot or a **promise** of one — the project implementation builds a
promise that resolves after `sessionStore.messages()` and `getSession()` settle
(`project-memory-extraction.ts:55-75`). A rejected promise is converted to `{status: "error"}`
(`extraction.ts:187-194`) and silently skipped, so a failed snapshot never rejects into the caller.

### 7.2 Coalescing and cursor advance

`extraction.ts:85-179`, quoted in full:

```ts
export function createMemoryExtractionScheduler<
  TSnapshot extends MemoryExtractionSnapshot = MemoryExtractionSnapshot,
>(
  execute: (
    input: Omit<MemoryExtractionExecutionInput, "snapshot"> & { snapshot: TSnapshot },
  ) => Promise<MemoryExtractionExecutionStatus>,
): MemoryExtractionScheduler<TSnapshot> {
  let cursor: MessageId | undefined;
  let latestPending: Promise<SnapshotAcquisition<TSnapshot>> | undefined;
  let running: Promise<void> | undefined;
  let shuttingDown = false;
  const shutdownController = new AbortController();

  const processSnapshot = async (snapshot: TSnapshot): Promise<void> => {
    const decision = evaluateMemoryExtraction(snapshot, cursor);
    const snapshotEnd = snapshot.boundaryMessageId;

    if (decision.decision === "skip") {
      if (snapshotEnd) cursor = snapshotEnd;
      return;
    }

    let status: MemoryExtractionExecutionStatus;
    try {
      status = await execute({
        abortSignal: shutdownController.signal,
        messageCount: decision.messageCount,
        snapshot,
      });
    } catch {
      return;
    }

    if (!shuttingDown && (status === "success" || status === "no-op") && snapshotEnd) {
      cursor = snapshotEnd;
    }
  };

  const run = async (first: Promise<SnapshotAcquisition<TSnapshot>>): Promise<void> => {
    try {
      let current: Promise<SnapshotAcquisition<TSnapshot>> | undefined = first;
      while (current && !shuttingDown) {
        const acquisition = await waitForSnapshotAcquisitionOrShutdown(
          current,
          shutdownController.signal,
        );
        if (acquisition.status === "shutdown" || shuttingDown) break;
        if (acquisition.status === "acquired") {
          try {
            await processSnapshot(acquisition.snapshot);
          } catch {
            // 本次 error 不推进 cursor；latest pending 仍按既有 coalescing 语义继续。
          }
        }
        current = shuttingDown ? undefined : latestPending;
        latestPending = undefined;
      }
    } finally {
      if (shuttingDown) latestPending = undefined;
      running = undefined;
    }
  };

  return {
    async drain() {
      while (running) {
        await running;
      }
    },
    getCursor() {
      return cursor;
    },
    hasPendingWork() {
      return running !== undefined || latestPending !== undefined;
    },
    schedule(snapshot) {
      if (shuttingDown) return;
      const acquisition = acquireSnapshot(snapshot);
      if (running) {
        latestPending = acquisition;
        return;
      }

      running = run(acquisition);
    },
    shutdown() {
      if (shuttingDown) return;
      // ZCode 关闭单个 session 后进程仍继续运行；旧 scheduler 只让调用方
      // 放弃等待，running/pending Extraction 仍可能继续请求模型和写 Memory。
      shuttingDown = true;
      latestPending = undefined;
      shutdownController.abort();
    },
  };
}
```

**Coalescing.** `schedule` keeps at most **one running + one pending**. When a run is in flight, the
new acquisition **overwrites** `latestPending` (`:164`) — an older, not-yet-started snapshot is
dropped entirely (not queued). On the next loop step, `current = shuttingDown ? undefined : latestPending; latestPending = undefined;` (`:139-140`) consumes it. Net effect: N turns scheduled while
one extraction runs collapse to a single follow-up extraction over the **newest** boundary, which
also swallows the intermediate turns' messages into one extraction window (the snapshot carries the
full durable branch up to its own boundary, `project-memory-extraction.ts:70-73`, so no data is lost
— only the number of model invocations is reduced).

**Cursor advance rules** (the single most important lifecycle invariant — a wrong advance silently
loses memory forever):

| Outcome | Cursor advanced? | Source |
| --- | --- | --- |
| `skip` / `direct-memory-write` | **yes** → `boundaryMessageId` | `:102-105` |
| `skip` / `no-user-prose` | **yes** → `boundaryMessageId` | `:102-105` |
| snapshot acquisition rejected | no | `:190-193` (`{status:"error"}` branch skipped) |
| `execute` throws | no | `:107-116` (`catch { return; }`) |
| status `"error"` | no | `:118` |
| status `"aborted"` | no | `:118` |
| status `"success"` | **yes**, unless `shuttingDown` | `:118-120` |
| status `"no-op"` | **yes**, unless `shuttingDown` | `:118-120` (dead branch — never returned) |
| `boundaryMessageId` falsy | no | `:118` (defensive; the field is a required `MessageId`) |
| shutting down during/after execute | no | `:118` |

Two asymmetries: (i) the skip path advances the cursor **without** checking `shuttingDown`, so a
skipped snapshot arriving during shutdown still moves the cursor; (ii) `"error"` and `"aborted"` do
**not** advance, so a persistently failing extraction re-attempts the same window on the next
scheduled turn — with coalescing, that means the same (growing) window is retried indefinitely while
failures continue.

### 7.3 Eligibility evaluation (what makes a snapshot runnable)

`extraction.ts:68-83`:

```ts
function evaluateMemoryExtraction(
  snapshot: MemoryExtractionSnapshot,
  cursor: MessageId | undefined,
): MemoryExtractionDecision {
  const messageCount = countMessagesAfterCursor(snapshot.durableMessages, cursor);

  if (containsDirectMemoryWrite(snapshot, cursor)) {
    return { decision: "skip", messageCount, reason: "direct-memory-write" };
  }

  if (!containsEligibleUserProse(snapshot.durableMessages, cursor)) {
    return { decision: "skip", messageCount, reason: "no-user-prose" };
  }

  return { decision: "run", messageCount };
}
```

- `boundaryMessageId` is stored so `messageCount` is always measured against the newest boundary,
  but the cursor — not the boundary — defines the window.
- `containsDirectMemoryWrite` (`:228-255`): scans assistant messages after the cursor for `tool`
  parts whose `tool` is `Write` or `Edit` (`:295-297`) and whose `state.input.file_path` resolves
  inside `memoryRoot` via `resolveContainedMemoryFilePath` (containment-only, no sensitive filter).
  If the main agent already wrote memory this turn, extraction is skipped — avoiding a redundant
  model call right after an explicit "remember this".
- `containsEligibleUserProse` (`:257-276`): requires a `user` message that is not `synthetic` and not
  `visibility: "model-only"` (`:287-293`) with a `text` part that is not `ignored`, not `synthetic`,
  and has `countWords(text) >= MINIMUM_USER_WORDS` where `countWords` is
  `text.split(/\s+/u).filter(Boolean).length` and `MINIMUM_USER_WORDS = 3` (`:6`, `:299-301`).
  So pure tool-result turns, system reminders, and one-word replies ("thanks", "yes") do not trigger
  extraction — but they do not advance the cursor either, so the pending messages accumulate until a
  real user turn arrives.
- Cursor handling is asymmetric when the cursor id is absent from the messages:
  `messagesAfterFoundCursor` returns `undefined` → `containsEligibleUserProse` falls back to the
  **entire** history (`:261` `?? messages`), while `containsDirectMemoryWrite` returns `false`
  (`:232-233`). A stale cursor (e.g. after a rewind removed the message) therefore re-checks the
  whole history for prose while never detecting an in-window direct write.
- `countMessagesAfterCursor` (`:219-226`): cursor not found → `messages.length`.

### 7.4 Snapshot construction (branch/rewind-aware)

`project-memory-extraction.ts:45-80`:

```ts
  const snapshotBase = captureProjectMemoryAgentContext(runtime, {
    memoryRoot,
    model: input.model,
    operation: "project_memory_extract",
    traceContext: input.traceContext,
  });
  const snapshotBoundaryMessageId = runtime.latestConversationMessageId;
  if (!snapshotBoundaryMessageId) return;
  const durableMessages = runtime.sessionStore.messages({ sessionID: runtime.sessionId });
  const session = runtime.sessionStore.getSession(runtime.sessionId);
  const snapshot = Promise.all([durableMessages, session]).then(
    ([messages, scheduledSession]): ProjectMemoryExtractionSnapshot => {
      const activeMessages = selectActiveConversationBranch(messages, {
        branchCutAfterMessageId: scheduledSession?.revert?.branchCutAfterMessageID,
        rewindCreatedMessageId: scheduledSession?.revert?.createdMessageID,
        rewindKeptMessageIds: scheduledSession?.revert?.keptMessageIDs,
        rewindTargetMessageId: scheduledSession?.revert?.targetMessageID,
      });
      const boundaryIndex = activeMessages.findIndex(
        (message) => message.info.id === snapshotBoundaryMessageId,
      );
      if (boundaryIndex < 0) {
        throw new Error("Extraction boundary is missing from the scheduled active branch");
      }
      return {
        ...snapshotBase,
        boundaryMessageId: snapshotBoundaryMessageId,
        durableMessages: activeMessages.slice(0, boundaryIndex + 1),
      };
    },
  );
```

Points of interest: the model/tool/read-state snapshot is captured **synchronously** (before awaiting
the store) so the extraction sees the turn's exact model and read-state; the message window comes
from the **durable session store**, not the in-memory history, then is filtered through
`selectActiveConversationBranch` so rewind/branch-cut sessions do not extract discarded messages; the
boundary must exist on the active branch or the snapshot rejects (→ `{status:"error"}` → no cursor
move, no extraction). Note `snapshotBase` is captured even when the resulting promise later rejects —
its cost (model wrapper creation, context snapshot copy) is paid unconditionally on every successful
turn.

### 7.5 Drain, shutdown, and timeouts

`project-memory-extraction.ts:83-107`:

```ts
export async function drainMemoryExtractions(
  this: AgentRuntimeInternal,
  timeoutMs: number | null = EXTRACTION_DRAIN_TIMEOUT_MS,
): Promise<void> {
  const scheduler = this.memoryExtractionScheduler;
  if (!scheduler) return;
  // benchmark 显式等待自然结束；普通 session close 仍保留原有有界取消清理。
  if (timeoutMs === null) {
    await scheduler.drain();
    return;
  }

  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      scheduler.drain(),
      new Promise<void>((resolve) => {
        timeout = setTimeout(resolve, timeoutMs);
        timeout.unref?.();
      }),
    ]);
  } finally {
    if (timeout) clearTimeout(timeout);
  }
}
```

- Default budget `EXTRACTION_DRAIN_TIMEOUT_MS = 60_000` (`:20`).
- The timer is `unref`'d so it cannot hold the event loop open, and it is cleared in `finally`.
- **The timeout only stops waiting; it does not cancel.** Cancellation comes solely from
  `beginShutdown()` → `shutdown()` → `AbortController.abort()`.

Call sites:

| Caller | Call | Purpose |
| --- | --- | --- |
| `bootstrap/src/app/session-facade.ts:265-269` | `beginShutdown(); await drainMemoryExtractions(60_000);` | Session close: *"关闭入口先阻止新调度并取消在飞 Memory Extraction，再等待取消链路收口。"* |
| `cli/src/prompt-command.ts:325` | `await app.runtime.drainMemoryExtractions(null)` | `--memory-bench` main path: wait indefinitely *before* `close` — *"bench 的正常等待必须先于 close；close 会取消 Extraction"* |
| `cli/src/prompt-command.ts:536` | same | `--memory-bench` target-selection path |

`shutdown()` (`extraction.ts:170-177`) is idempotent, drops the pending snapshot, and aborts; the
`waitForSnapshotAcquisitionOrShutdown` helper (`:196-217`) resolves `{status:"shutdown"}` immediately
if the signal is already aborted, otherwise races the acquisition against the abort event with
listener cleanup in both paths.

Runtime shutdown ordering (`runtime/agent-runtime.ts:331-337`):

```ts
  beginShutdown(): void {
    // ExecutionPort.close() 会把后台 Bash 收口为 cancelled；若允许
    // teardown terminal event 再唤醒模型，并与随后关闭的 session store 竞态。
    this.shuttingDown = true;
    // 关闭单个 session 后进程仍存活，
    // 因此必须先终止该 runtime 的 Extraction，不能只在超时后放弃等待。
    this.memoryExtractionScheduler?.shutdown();
  }
```

Residency interaction (`runtime/methods/residency.ts:21-28`) — a session in the warm pool is not
considered idle while extraction work exists:

```ts
export function hasResidencyBlockingWork(this: AgentRuntimeInternal): boolean {
  return (
    this.hasActiveOrQueuedTurnWork() ||
    this.hasRunningBackgroundTasks() ||
    this.residencyBlockingWorkCount > 0 ||
    (this.memoryExtractionScheduler?.hasPendingWork() ?? false)
  );
}
```

with the header comment explaining why a scheduler-specific probe exists: *"session 常驻池过去只观察
active turn 与 task registry；title、MCP startup、ledger write 等 detached Promise 不在两者中，可能在仍读写
runtime 时被当作 idle 关闭。"*

### 7.6 Enumeration of runtime guards (complete)

| # | Guard | Site | Effect when it fires |
| --- | --- | --- | --- |
| 1 | `runtime.shuttingDown` | `project-memory-extraction.ts:36` | no snapshot, no scheduling |
| 2 | `config.memory.extractionEnabled === false` | `:38` | no snapshot, no FS access (headless `--memory-bench` toggle, `cli/src/prompt-command.ts:234`) |
| 3 | `resolveEnabledProjectMemoryRoot` → `undefined` | `:41`, `project-memory.ts:9-10` | disabled / `use:false` / no `cliStorageRoot` / non-main task type |
| 4 | `runtime.isRemoteWorkspace()` | `:42` | remote workspaces never touch local memory |
| 5 | missing `sessionStore` or `fileSystemPort` | `:43` | no scheduling |
| 6 | `runtime.latestConversationMessageId` missing | `:52` | no scheduling |
| 7 | boundary missing from active branch | `:66-68` | snapshot rejects → `{status:"error"}` → skipped, cursor untouched |
| 8 | `modelExecution.memoryExtraction === "skip"` | `turn.ts:699` | this turn only |
| 9 | `shuttingDown` at schedule time | `extraction.ts:161` | snapshot dropped silently |
| 10 | `shuttingDown` during run | `extraction.ts:126,131,139,143` | loop breaks; pending cleared; cursor not advanced |
| 11 | `abortSignal` before/after each model call | `memory-agent-loop.ts:59,74` | throws; classified `"aborted"` |
| 12 | abort after manifest scan, before prompt build | `project-memory-extraction.ts:133-136` | returns `"aborted"` before any model call |
| 13 | tool not in catalog | `memory-agent-loop.ts:127` | `No such tool available` tool result |
| 14 | tool policy denial | `memory-agent-loop.ts:129-162` | error tool result, loop continues |

---

## 8. Caps, dedup, and size limits (consolidated)

Everything not already covered above, in one table. All values are hard-coded constants (none are
config-driven in this checkout).

| Constant | Value | File:line | Effect |
| --- | --- | --- | --- |
| `EXTRACTION_MAX_TURNS` | `5` | `project-memory-extraction.ts:19` | hard cap on extraction model round-trips |
| `EXTRACTION_DRAIN_TIMEOUT_MS` | `60_000` | `project-memory-extraction.ts:20` | bounded wait at session close (not a cancel) |
| `AUXILIARY_MAX_OUTPUT_TOKENS` | `5_000` | `model/auxiliary-model-options.ts:3` | per-request output cap for the extraction model |
| reasoning level | `optionSpecs.reasoningLevel.values[0]` | `model/auxiliary-model-options.ts:11` | lowest tier forced |
| `DEFAULT_MODEL_REQUEST_MEDIA_BUDGET_BYTES` | `40 * 1024 * 1024` | `runtime/helpers/media-budget.ts:28` | per-request media projection inside the loop |
| `MANIFEST_FILE_LIMIT` | `200` | `recall/manifest.ts:7` | keeps the 200 newest `.md` files (post-sort) in the dedup list |
| `MANIFEST_PREVIEW_LINE_LIMIT` | `30` | `recall/manifest.ts:8` | frontmatter read window per candidate file |
| `MEMORY_INDEX_LINE_LIMIT` | `200` | `index-content.ts:3` | `MEMORY.md` lines injected into context |
| `MEMORY_INDEX_CHARACTER_LIMIT` | `25_000` | `index-content.ts:4` | `MEMORY.md` characters injected into context |
| `MINIMUM_USER_WORDS` | `3` | `extraction.ts:6` | minimum user prose for extraction eligibility |
| slug length | `48` | `project-root.ts:31` | directory-name slug truncation |
| identity hash | `16` hex chars | `project-root.ts:18` | sha256 prefix in the memory root name |
| index-entry guidance | "one line under ~200 chars" | `index-content.ts:37` | prompt-side guidance in the truncation warning |

**Dedup mechanisms — exhaustive.** There is no content hashing, no similarity, no merge, and no
uniqueness constraint anywhere in the subsystem. Dedup is entirely prompt-and-manifest driven:

1. The extraction prompt's `## Existing memory files` block plus *"Check this list before writing —
   update an existing file rather than creating a duplicate."* (`extraction.ts:46-49`).
2. The manifest itself (filename + type + mtime + description) — the only machine-readable memory
   inventory the extraction agent gets.
3. The system section's *"Before saving, check for an existing file that already covers it"*
   (`sections/memory.ts:48`).
4. `originSessionId` first-writer-wins (`origin-session.ts:36-37`) — prevents re-stamping, not
   duplication.

Consequences: two files describing the same fact in different filenames are both kept forever; the
200-file cap can push an existing memory out of the manifest, making a duplicate *likely*; and
nothing ever garbage-collects memories except an `rm` the model chooses to run.

**Other limits/gaps worth recording:** the manifest walk has no depth or byte cap (all candidate
previews are read before the 200-file slice); `description` length is uncapped in the manifest; there
is no cap on how many files extraction may write in one run (bounded only by the 5-turn budget and
model output tokens); the extraction proxy re-sends the whole conversation prefix on every one of the
5 turns with no input-token accounting; and `emitEvent: () => {}` means none of it is observable in
session events.

---

## 9. Findings, asymmetries, and risks

Ordered by likely impact. Each is a direct consequence of quoted code above.

1. **The extraction loop's own policy is the only write boundary.** The provider request carries the
   full main tool catalog (`memory-agent-loop.ts:71`), the executor runs `getMode: () => "yolo"`
   (`project-memory-agent.ts:106`) and the permission service short-circuits yolo for non-`alwaysAsk`
   tools (`permission/service.ts:136-137`), while `Write`/`Edit`/`Bash` declare no `alwaysAsk`
   (only workflow tools do). `resolveWorkspacePath` does not confine to `workspaceRoot`
   (`tool/path-policy.ts:36-39`). Therefore the `isContainedMarkdownMutation` /
   `isContainedMarkdownBashRemoval` checks are load-bearing security, not defense-in-depth.
2. **`rm` bypasses `SENSITIVE_MEMORY_PATH_SEGMENTS`.** Removal uses
   `resolveContainedMemoryFilePath` only (`memory-agent-loop.ts:207,213-227`), so reserved segments
   (`node_modules`, `.git`, `hooks`, `config`, `skills`, …) are `rm`-able inside the memory root but
   not writable by `Write`/`Edit`. Two resolvers, two different guarantees.
3. **`"no-op"` is dead code and `"Nothing to save."` has no handler.** The status union and the
   scheduler's advance rule support `"no-op"` (`extraction.ts:8,118`), but the only implementation
   returns `"success"` unconditionally after the loop (`project-memory-extraction.ts:163-164`); the
   literal string appears only in the prompt (`extraction.ts:60`). A refusal and an empty answer are
   indistinguishable, and both advance the cursor.
4. **Denials never stop the loop.** Denied tool calls produce error tool results and the loop
   continues to `maxTurns` (`memory-agent-loop.ts:92-100`); there is no denial-count circuit breaker,
   and the final status is still `"success"`.
5. **Cursor-advance asymmetry on missing cursor.** With a stale cursor, the prose check scans the
   entire history while the direct-write check scans nothing (`extraction.ts:232-233,261`).
6. **The in-context `MEMORY.md` index is a session-start snapshot.** Loaded once
   (`runtime/methods/context.ts:67`) and re-passed cached on every rebuild
   (`context-refresh.ts:20,35`); writes during the session — including by the main agent right after
   being told to *"add a one-line pointer in `MEMORY.md`"* — are not reflected in the prefix.
7. **Index truncation poisons read-state.** `isPartialView: formattedContent !== read.content`
   (`context.ts:181`) fires on any frontmatter strip, top-level HTML-comment strip, truncation, or
   plain `trim()`; `Write`/`Edit` treat a partial read as "not read" (`write.ts:284`, `edit.ts:429`).
   Practical effect: create `MEMORY.md` freely, but editing it later requires an explicit `Read`.
8. **Memory-agent activity is invisible.** `emitEvent: async () => {}` (`project-memory-agent.ts:106`)
   plus `skipTranscript: true` (`:53`) mean extraction reads/writes/tool denials produce no session
   events and no model-io transcript; the only trace is telemetry
   (`project_memory_extract` / `background` / `trigger: "scheduler"`).
9. **Failed extractions retry forever.** `"error"`/`"aborted"` do not advance the cursor
   (`extraction.ts:114-120`), and the next scheduled turn coalesces to the newest boundary while
   still evaluating from the old cursor — so a deterministic failure (e.g. a consistently unparseable
   context, or a workspaceIdentity mismatch) re-runs on every turn.
10. **Manifest cost is O(all memory files) per extraction.** `collectMemoryPaths` gathers everything,
    all previews are read through `allSettled`, and only then does the `slice(0, 200)` apply
    (`recall/manifest.ts:17-29`).
11. **Prompt/code drift in the documented schema.** `name:` is never read by any code; `description`
    is documented as recall-relevant but is only consumed for extraction dedup; `[[name]]` links are
    never resolved; a top-level `type` is accepted but undocumented.
12. **Stamping requires model-emitted frontmatter.** No frontmatter ⇒ no `originSessionId` /
    `node_type` (`origin-session.ts:22-23`), and the stamp regex and the manifest parser disagree on
    what counts as frontmatter.
13. **`isAbortError` is DOMException-only** (`project-memory-extraction.ts:176-178`), so non-DOM abort
    errors are recorded as internal failures.
14. **Zero test coverage in this checkout** — no `*.test.ts` / `*.spec.ts` / test directories exist
    under `apps/zcode-cli`, for this subsystem or any other.

---

## 10. File → responsibility quick reference

| File | Responsibility |
| --- | --- |
| `memory/memory-agent-loop.ts` | The extraction agent loop: 5-turn bounded model/tool cycle, per-call tool policy, denial messages |
| `memory/memory-file-path.ts` | Containment (`resolveContainedMemoryFilePath`) + sensitive-segment filtering (`resolveSafeMemoryFilePath`, `SENSITIVE_MEMORY_PATH_SEGMENTS`) |
| `memory/extraction.ts` | Eligibility evaluation, extraction prompt, coalescing scheduler (cursor/advance/drain/shutdown) |
| `memory/origin-session.ts` | `node_type: memory` + `originSessionId` frontmatter injection on `.md` writes inside the root |
| `memory/index-content.ts` | `MEMORY.md` formatting: frontmatter/top-level-comment stripping, 200-line / 25 000-char caps, truncation banner |
| `memory/directory.ts` | Best-effort memory-directory pre-creation |
| `memory/project-root.ts` | Deterministic per-workspace memory root (`<cliStorageRoot>/memories/projects/<slug>-<hash>/memory`) |
| `memory/recall/manifest.ts` | Recursive `.md` scan (excluding `MEMORY.md`), frontmatter/description/type parsing, mtime-desc 200-file manifest |
| `memory/recall/types.ts` | `MEMORY_RECALL_TYPES` + `MemoryManifestEntry` (the whole "recall" type surface) |
| `memory/recall/index.ts` | Barrel export of the two manifest functions |
| `runtime/helpers/project-memory.ts` | Enablement + root resolution shared by context, scheduler, and main-agent executor |
| `runtime/helpers/project-memory-agent.ts` | Snapshot capture (model, entries, tools, read-state), provider-message assembly, extraction tool executor (yolo, deny-broker, silent events) |
| `runtime/helpers/project-memory-extraction.ts` | Scheduling guards, branch-aware snapshot, telemetry span, `runMemoryAgentLoop` invocation, bounded/unbounded drain |
| `context/sections/memory.ts` | The verbatim `Memory` system-prompt section |
| `context/sections/request-user-context.ts` | Injects `MEMORY.md` under the `# agentsMd` meta-user block |
| `tool/executor/memory-file-permission.ts` | Permission auto-allow for memory `.md` writes (main agent path) |

**Adjacent but distinct:** `subagent/persistent-memory.ts`, `subagent/persistent-memory-prompt.ts`
(per-agent memory roots and prompt, sharing only `ensureMemoryDirectoryExists` and the path helpers),
and `runtime/methods/subagent.ts:131-141` (which appends that prompt to subagent system prompts).
