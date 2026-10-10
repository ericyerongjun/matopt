# 05 — The SUBAGENT Subsystem of ZCode core

Scope: the 22 files of `apps/zcode-cli/packages/core/src/subagent/`, its Agent-tool handlers, and the
runtime methods/helpers that wire it up. Every claim below carries a `path:line` reference. All paths are
relative to the repository root `/Users/eric/projects/ZCode`.

The one-sentence summary: **a subagent is a second `AgentRuntime` with its own session id and its own
system prompt, but the parent's event store, the parent's permission broker route, the parent's MCP
connection pool, and (when configured) a persistent per-profile memory directory.** It reports back
either as the `Agent` tool's return value (foreground) or as a synthetic *model-only* user message in the
parent's runtime command queue (background).

---

## 0. File inventory (all 22 files)

| File | Lines | Role |
|---|---|---|
| `subagent/runner.ts` | 2142 | The whole lifecycle: `SubagentPort` impl, foreground/background, abort, registry, notifications, artifacts |
| `subagent/profile.ts` | 351 | `AgentProfile` type, built-in profiles, Markdown→profile parsing, prompt formatting |
| `subagent/profile-frontmatter.ts` | 243 | Loose YAML frontmatter reader (no YAML dependency) |
| `subagent/context-builder.ts` | 196 | `SubagentContextBuilder` — child system prompt assembly |
| `subagent/system-prompt.ts` | 40 | Common notes + `<env>` block for children |
| `subagent/persistent-memory.ts` | 110 | Memory root resolution + `MEMORY.md` load |
| `subagent/persistent-memory-prompt.ts` | 169 | The persistent-memory system prompt |
| `subagent/tool-event-mirror.ts` | 154 | Child tool/permission events → parent session projection |
| `subagent/computer-use-policy.ts` | 117 | Deny official Computer-Use MCP/skill inside subagents |
| `subagent/explore.ts` | 72 | `EXPLORE_AGENT_TYPE` + Explore system prompt |
| `subagent/explore-tools.ts` | 57 | Explore tool allowlists |
| `subagent/borrowed-mcp-port.ts` | 71 | Read-only, scoped view over the parent MCP port |
| `subagent/message-steering.ts` | 59 | `RuntimeTaskMessageSink` → `steerTurn` |
| `subagent/coordinator-response.ts` | 52 | `CoordinatorResponsePort` → parent queue |
| `subagent/completion-notification.ts` | 51 | `<task-notification>` text for a finished agent |
| `subagent/mcp-config.ts` | 37 | `mcp__server__tool` / `mcp__server__*` parsing |
| `subagent/interaction-origin.ts` | 33 | `InteractionRequestOrigin` for child→user questions |
| `subagent/general-purpose.ts` | 24 | `general-purpose` type + system prompt |
| `subagent/tool-policy.ts` | 22 | Forced child disallow rules (plan tools) |
| `subagent/profile-model-selection.ts` | 8 | Frontmatter → `ModelSelection` codec |
| `subagent/runtime-task-registry.ts` | 1 | Re-export of `runtime-task/registry.js` |
| `subagent/index.ts` | 12 | Barrel (note: does **not** export `profile-frontmatter`/`persistent-memory`) |

---

## 1. How a subagent is DEFINED

### 1.1 The `AgentProfile` type — verbatim

`subagent/profile.ts:18-38`:

```ts
export type AgentProfileSource = "built-in" | "project" | "user";
export type AgentMemoryScope = "user" | "project" | "local";

export interface AgentProfile {
  background?: boolean;
  color?: "red" | "blue" | "green" | "yellow" | "purple" | "orange" | "pink" | "cyan";
  description: string;
  disallowedTools?: readonly string[];
  injectAgentsMd?: boolean;
  maxTurns?: number;
  mcpServers?: readonly string[];
  memory?: AgentMemoryScope;
  modelSelection?: ModelSelection;
  name: string;
  path?: string;
  permissionMode?: AgentPermissionMode;
  skills?: readonly string[];
  source: AgentProfileSource;
  systemPrompt: string;
  tools?: readonly string[];
}
```

Supporting types at `profile.ts:10-19`:

```ts
export const DEFAULT_SUBAGENT_TYPE = GENERAL_PURPOSE_AGENT_TYPE;

export type BuiltInSubagentModelSelectionOverrides = Partial<
  Record<typeof DEFAULT_SUBAGENT_TYPE | typeof EXPLORE_AGENT_TYPE, ModelSelection>
>;

export type AgentPermissionMode = "auto" | "plan";
```

Validation sets — the enumerations a frontmatter value must hit or be silently dropped
(`profile.ts:51-64`):

```ts
const VALID_COLORS = new Set<NonNullable<AgentProfile["color"]>>([
  "red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan",
]);

const VALID_PERMISSION_MODES = new Set<AgentPermissionMode>(["auto", "plan"]);

const VALID_MEMORY_SCOPES = new Set<AgentMemoryScope>(["user", "project", "local"]);
```

### 1.2 The frontmatter parser

Two layers. **Layer 1** splits `---` fence from body — `profile-frontmatter.ts:1-19`:

```ts
export function splitMarkdownFrontmatter(content: string): {
  body: string;
  frontmatter?: string;
} {
  const normalized = content.replace(/^\uFEFF/u, "");
  if (!normalized.startsWith("---")) {
    return { body: normalized };
  }

  const lines = normalized.split(/\r?\n/u);
  if (lines[0]?.trim() !== "---") return { body: normalized };
  const endIndex = lines.findIndex((line, index) => index > 0 && line.trim() === "---");
  if (endIndex < 0) return { body: normalized };

  return {
    frontmatter: lines.slice(1, endIndex).join("\n"),
    body: lines.slice(endIndex + 1).join("\n"),
  };
}
```

The closing fence is the *second* line that is exactly `---`; no closing fence means "no frontmatter"
and the whole file becomes the body.

**Layer 2** is a hand-rolled YAML subset — `profile-frontmatter.ts:26-40` (entry) and
`profile-frontmatter.ts:42-101` (the scanner):

```ts
export function parseAgentFrontmatter(frontmatter: string): ParsedAgentFrontmatter {
  const { bareValueKeys, invalidNestedListKeys, values } = parseLooseFrontmatter(frontmatter);
  if (bareValueKeys.has("tools")) {
    delete values.tools;
  }
  return {
    mcpServers: parseMcpServerNames(
      values.mcpServers,
      Object.prototype.hasOwnProperty.call(values, "mcpServers"),
      bareValueKeys.has("mcpServers"),
      invalidNestedListKeys.has("mcpServers"),
    ),
    values,
  };
}
```

Key grammar (`profile-frontmatter.ts:84`):

```ts
const keyValue = line.match(/^([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.*)$/u);
```

Rules the scanner implements:
- `key:` with nothing after it opens a **pending list** (`values[key] = []`, `bareValueKeys.add(key)`)
  and subsequent `- item` lines append (`frontmatter.ts:57-74`).
- A `- item` under a `mcpServers` key that looks like a mapping (`{...}` or `foo: bar`, unquoted) marks
  the key `invalidNestedListKeys` — this is the guard against a block-mapping silently degrading into an
  empty server list, which would widen the child's MCP scope (`frontmatter.ts:61-63`, `76-81`, comment at
  `76-77`: *"块状 mapping 不是 server-name 列表，不能被空数组吞掉后扩大 child MCP scope"*).
- Scalars: `true`/`false` → boolean, `/^\d+$/` → number, `[a, b]` → array (top-level comma split with
  quote/paren/brace/bracket depth tracking, `frontmatter.ts:206-243`), `{...}` → `JSON.parse` or raw
  string, otherwise unquoted string.
- Inline comments: `#` preceded by whitespace and outside quotes (`frontmatter.ts:182-194`).
- A bare `tools:` with no value is **deleted**, so `parseToolList` never sees an empty array
  (`frontmatter.ts:28-30`) — but an explicit `tools: []` **is** honoured as "no tools"
  (`profile.ts:276-278`).

### 1.3 Every supported frontmatter key

Assembled in `parseAgentProfileFromMarkdown` (`profile.ts:154-227`). The returned profile object
(`profile.ts:206-226`):

```ts
  return {
    ...(memoryDiagnostic ? { diagnostic: memoryDiagnostic } : {}),
    profile: {
      name,
      description,
      source: input.source,
      systemPrompt: parsed.body.trim(),
      ...(input.path ? { path: input.path } : {}),
      ...(modelSelection ? { modelSelection } : {}),
      ...(color ? { color } : {}),
      ...(permissionMode ? { permissionMode } : {}),
      ...(maxTurns ? { maxTurns } : {}),
      ...(memory ? { memory } : {}),
      ...optionalList("tools", frontmatter.tools),
      ...optionalList("disallowedTools", frontmatter.disallowedTools),
      ...optionalList("skills", frontmatter.skills),
      ...optionalBoolean("background", frontmatter.background),
      ...optionalBoolean("injectAgentsMd", frontmatter.injectAgentsMd),
      ...(mcpServers === undefined ? {} : { mcpServers }),
    },
  };
```

| Key | Type | Handling | Line |
|---|---|---|---|
| `name` | string, **required** | `scalarString`, trimmed, non-empty | `profile.ts:171-175` |
| `description` | string, **required** | `\\n` unescaped to newline | `profile.ts:172-178` |
| `model` / selection keys | `ModelSelection` | delegated to `parseSubagentMarkdownSelection` | `profile.ts:180`, `profile-model-selection.ts:4-8` |
| `color` | one of the 8 names | invalid → dropped silently | `profile.ts:181`, `313-318` |
| `permissionMode` | `"auto" \| "plan"` | **ignored for `source: "project"`** | `profile.ts:182-185` |
| `maxTurns` | positive int (number or digit string) | | `profile.ts:186`, `327-334` |
| `memory` | `user \| project \| local` | invalid → diagnostic `agent_invalid_memory_scope` but profile still returned | `profile.ts:187-195`, `229-233` |
| `mcpServers` | list of *parent* server names | must be a real list; otherwise diagnostic `agent_invalid_mcp_servers` and **no profile** | `profile.ts:196-204` |
| `tools` | list or comma/space string, `Tool(args)` stripped to `Tool` | | `profile.ts:219`, `248-268`, `340-351` |
| `disallowedTools` | same parser as `tools` | | `profile.ts:220` |
| `skills` | list of skill names | | `profile.ts:221`, `294-307` |
| `background` | boolean or `"true"`/`"false"` | | `profile.ts:222`, `282-292` |
| `injectAgentsMd` | boolean or string | | `profile.ts:223`, `282-292` |

Diagnostic codes emitted: `agent_missing_frontmatter` (`profile.ts:161-167`),
`agent_missing_required_frontmatter` (`profile.ts:235-246` — message
`Agent frontmatter must include ${field}`), `agent_invalid_memory_scope`, `agent_invalid_mcp_servers`.

The project-source permission guard, comment at `profile.ts:183-184`:

```ts
  // 项目级 subagent markdown 属于仓库输入，不能通过 frontmatter 把
  // child runtime 提升到 bypass/yolo；权限模式只接受用户级或受信插件配置。
  const permissionMode = input.source === "project" ? undefined : parsedPermissionMode;
```

…and it is stripped a second time at bootstrap (`bootstrap/src/subagents.ts:115-124`), so a project
Markdown file cannot reach `permissionMode` even if it bypasses the parser path.

### 1.4 On-disk discovery: globs, directories, search order

Discovery lives **outside** `subagent/`, in `bootstrap/src/subagents.ts` (it reads `node:fs`, so it cannot
live in the pure core package). Roots, `bootstrap/src/subagents.ts:52-57`:

```ts
  const migration = await migrateUserSubagentMarkdown(join(input.storageRoot, "agents"));
  await migrateSubagentStateFile(join(input.storageRoot, "v2", "agents-state.json"));
  const roots = [
    { path: join(input.storageRoot, "agents"), source: "user" as const },
    { path: join(input.workingDirectory, ".zcode", "agents"), source: "project" as const },
  ];
```

So the two directory globs are:

- **user:** `<storageRoot>/agents/**/*.{md,markdown>}` (`storageRoot` is `config.storage.dir`, resolved at
  `bootstrap/src/app/create-app.ts:200`)
- **project:** `<cwd>/.zcode/agents/**/*.{md,markdown}`

The file walk is **recursive** and sorted, `bootstrap/src/subagents.ts:320-336`:

```ts
function listMarkdownFiles(root: string): string[] {
  if (!existsSync(root)) return [];
  if (!statSync(root).isDirectory()) return [];

  const result: string[] = [];
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const path = join(root, entry.name);
    if (entry.isDirectory()) {
      result.push(...listMarkdownFiles(path));
      continue;
    }
    if (entry.isFile() && /\.(md|markdown)$/iu.test(entry.name)) {
      result.push(path);
    }
  }
  return result.sort((left, right) => left.localeCompare(right));
}
```

Extension regex: `/\.(md|markdown)$/iu` — case-insensitive, both `.md` and `.markdown`.

**Plugins** are a third source, non-recursive and name-keyed (`bootstrap/src/subagents.ts:136-145`):

```ts
  for (const plugin of input.plugins) {
    if (!plugin.enabled) continue;
    const agents = plugin.components.find((group) => group.kind === "agent")?.items ?? [];
    for (const agent of agents) {
      const filePath = join(plugin.rootPath, "agents", `${agent.name}.md`);
      const result = parsePluginAgentProfile({ filePath, logger: input.logger, plugin });
```

Plugin profiles are namespaced `"<plugin.name>:<bareName>"` (`subagents.ts:234-239`) and *additionally* get
a bare-name alias when that bare name is unique and not reserved (`subagents.ts:159-174`).

Reserved names (`bootstrap/src/subagents.ts:47`):

```ts
const RESERVED_AGENT_NAMES = new Set(["general-purpose", "Explore"]);
```

**Search / precedence order.** Assembly order in `create-app.ts:206-235`:

```ts
    const zcodeSubagentProfileOutcome = await loadZCodeAgentProfiles({
      logger, storageRoot, workingDirectory,
    });
    const zcodeSubagentProfiles = zcodeSubagentProfileOutcome.profiles;
    ...
    const pluginSubagentProfiles = loadPluginAgentProfiles({
      logger,
      plugins: pluginOutcome.plugins,
      reservedProfileNames: zcodeSubagentProfiles.map((profile) => profile.name),
      modelSelectionOverrides: zcodeSubagentProfileOutcome.pluginAgentModelSelectionOverrides,
    }).profiles;
    ...
    const subagentProfiles = [...zcodeSubagentProfiles, ...pluginSubagentProfiles];
```

Then `normalizeAgentProfiles` (`subagent/profile.ts:89-113`) seeds the map with built-ins **first** and
lets every loaded profile overwrite by name:

```ts
  const active = new Map<string, AgentProfile>();
  active.set(DEFAULT_SUBAGENT_TYPE, createBuiltInGeneralPurposeAgentProfile({...}));
  active.set(EXPLORE_AGENT_TYPE, createBuiltInExploreAgentProfile({...}));
  for (const profile of profiles) {
    active.set(profile.name, profile);
  }
  return Array.from(active.values());
```

Effective precedence for a given name: **plugin > project/user Markdown > built-in**. Note the subtlety
that within the zcode loader the `roots` array is `[user, project]` and both are pushed into one flat
list, so for a duplicate name *project wins* (it is pushed later and `active.set` is last-write-wins).
`isBuiltInExploreAgentProfile` requires **both** the name and `source === "built-in"`
(`profile.ts:82-87`) so a user/profile shadow does not inherit Explore's read-only behaviour.

Disabled profiles: `v2/agents-state.json` `disabledAgentIds` filters **user** profiles only
(`bootstrap/src/subagents.ts:304-318`), keyed by `createAgentStateId({name, scope: "user", source: "user"})`.

---

## 2. BUILT-IN agent types

### 2.1 The `AgentType` constants

`contracts/src/tools/agent.ts:11-16`:

```ts
export const AgentType = {
  GeneralPurpose: "general-purpose",
  Explore: "Explore",
} as const;

export type AgentType = string;
```

The same two literals are declared in core — `subagent/general-purpose.ts:5` and `subagent/explore.ts:7`:

```ts
export const GENERAL_PURPOSE_AGENT_TYPE = "general-purpose" as const;
export const EXPLORE_AGENT_TYPE = "Explore" as const;
```

`subagent/profile.ts:10`: `export const DEFAULT_SUBAGENT_TYPE = GENERAL_PURPOSE_AGENT_TYPE;`
The default is applied in the handler at `tool/handlers/agent.ts:176`:
`const agentType = parsed.subagent_type ?? AgentType.GeneralPurpose;`

### 2.2 `general-purpose` system prompt — verbatim

`subagent/general-purpose.ts:7-24`:

```ts
export function buildGeneralPurposeSystemPrompt(): string {
  return [
    "You are an agent for ZCode CLI. Given the user's message, you should use the tools available to complete the task. Complete the task fully—don't gold-plate, but don't leave it half-done. When you complete the task, respond with a concise report covering what was done and any key findings — the caller will relay this to the user, so it only needs the essentials.",
    "",
    "Your strengths:",
    "- Searching for code, configurations, and patterns across large codebases",
    "- Analyzing multiple files to understand system architecture",
    "- Investigating complex questions that require exploring many files",
    "- Performing multi-step research tasks",
    "",
    "Guidelines:",
    "- For file searches: search broadly when you don't know where something lives. Use Read when you know the specific file path.",
    "- For analysis: Start broad and narrow down. Use multiple search strategies if the first doesn't yield results.",
    "- Be thorough: Check multiple locations, consider different naming conventions, look for related files.",
    "- NEVER create files unless they're absolutely necessary for achieving your goal. ALWAYS prefer editing an existing file to creating a new one.",
    "- NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested.",
  ].join("\n");
}
```

### 2.3 `Explore` system prompt — verbatim

`subagent/explore.ts:20-68`:

```ts
export function buildExploreAgentPrompt(options: ExploreAgentPromptOptions): string {
  const searchGuidelines = options.embeddedSearchEnabled
    ? [
        "- Use `find` via Bash for broad file pattern matching",
        "- Use `grep` via Bash for searching file contents with regex",
      ]
    : [
        "- Use Glob for broad file pattern matching",
        "- Use Grep for searching file contents with regex",
      ];
  const bashReadOnlyCommands = options.embeddedSearchEnabled
    ? "ls, git status, git log, git diff, find, grep, cat, head, tail"
    : "ls, git status, git log, git diff, find, cat, head, tail";

  return [
    "You are ZCode Explore, a file search and codebase research specialist for ZCode CLI. You excel at thoroughly navigating and exploring codebases.",
    "",
    "=== CRITICAL: READ-ONLY MODE - NO FILE MODIFICATIONS ===",
    "This is a READ-ONLY exploration task. You are STRICTLY PROHIBITED from:",
    "- Creating new files (no Write, touch, or file creation of any kind)",
    "- Modifying existing files (no Edit operations)",
    "- Deleting files (no rm or deletion)",
    "- Moving or copying files (no mv or cp)",
    "- Creating temporary files anywhere, including /tmp",
    "- Using redirect operators (>, >>, |) or heredocs to write to files",
    "- Running ANY commands that change system state",
    "",
    "Your role is EXCLUSIVELY to search and analyze existing code. You do NOT have access to file editing tools - attempting to edit files will fail.",
    "",
    "Your strengths:",
    "- Rapidly finding files using glob patterns",
    "- Searching code and text with powerful regex patterns",
    "- Reading and analyzing file contents",
    "",
    "Guidelines:",
    ...searchGuidelines,
    "- Use Read when you know the specific file path you need to read",
    `- Use Bash ONLY for read-only operations (${bashReadOnlyCommands})`,
    "- NEVER use Bash for: mkdir, touch, rm, cp, mv, git add, git commit, npm install, pip install, or any file creation/modification",
    "- Adapt your search approach based on the thoroughness level specified by the caller",
    "- Communicate your final report directly as a regular message - do NOT attempt to create files",
    "",
    "NOTE: You are meant to be a fast agent that returns output as quickly as possible. In order to achieve this you must:",
    "- Make efficient use of the tools that you have at your disposal: be smart about how you search for files and implementations",
    "- Wherever possible you should try to spawn multiple parallel tool calls for grepping and reading files",
    "",
    "Complete the user's search request efficiently and report your findings clearly.",
  ].join("\n");
}
```

The legacy alias funnels into the same builder and ignores the environment fields
(`explore.ts:70-72`):

```ts
export function buildExploreSystemPrompt(options: LegacyExploreSystemPromptOptions): string {
  return buildExploreAgentPrompt({ embeddedSearchEnabled: options.embeddedSearchEnabled });
}
```

Note the built-in Explore *profile* stores `systemPrompt: ""` (`profile.ts:77`) — the empty string is the
sentinel that makes the runtime build the prompt lazily with the embedded-search decision, see
`runtime/methods/subagent.ts:127-130`:

```ts
      const baseAgentPrompt =
        builtInExplore && request.systemPrompt?.trim() === ""
          ? buildExploreAgentPrompt({ embeddedSearchEnabled })
          : request.systemPrompt?.trim();
```

### 2.4 `system-prompt.ts` — the notes + env block appended to *every* child

`subagent/system-prompt.ts:10-19` (verbatim):

```ts
export function buildSubagentCommonNotes(): string {
  return [
    "Notes:",
    "- Agent threads always have their cwd reset between bash calls, as a result please only use absolute file paths.",
    "- In your final response, share file paths (always absolute, never relative) that are relevant to the task. Include code snippets only when the exact text is load-bearing (e.g., a bug you found, a function signature the caller asked for) — do not recap code you merely read.",
    "- For clear communication with the user the assistant MUST avoid using emojis.",
    '- Do not use a colon before tool calls. Text like "Let me read the file:" followed by a read tool call should just be "Let me read the file." with a period.',
    "- Do NOT Write report/summary/findings/analysis .md files. Return findings directly as your final assistant message — the parent agent reads your text output, not files you create.",
  ].join("\n");
}
```

`subagent/system-prompt.ts:21-40` (verbatim):

```ts
export function buildSubagentEnvironmentContext(
  options: SubagentEnvironmentContextOptions,
): string {
  const { envInfo, model } = options;
  const modelLine = model
    ? [`You are powered by the model named ${model.providerId}/${model.modelId}.`]
    : [];

  return [
    "Here is useful information about the environment you are running in:",
    "<env>",
    `Working directory: ${envInfo.cwd}`,
    `Is directory a git repo: ${isEnvInfoGitRepository(envInfo) ? "Yes" : "No"}`,
    `Platform: ${envInfo.platform}`,
    `Shell: ${envInfo.shell}`,
    `OS Version: ${envInfo.osVersion}`,
    "</env>",
    ...modelLine,
  ].join("\n");
}
```

### 2.5 Built-in profile records

`profile.ts:66-80` (Explore) and `profile.ts:115-130` (general-purpose) — verbatim:

```ts
export function createBuiltInExploreAgentProfile(
  options: { modelSelection?: ModelSelection } = {},
): AgentProfile {
  return {
    name: EXPLORE_AGENT_TYPE,
    description:
      'Read-only search agent for broad fan-out searches - when answering means sweeping many files, directories, or naming conventions and you only need the conclusion, not the file dumps. It reads excerpts rather than whole files, so it locates code; it doesn\'t review or audit it. Specify search breadth: "medium" for moderate exploration, "very thorough" for multiple locations and naming conventions.',
    color: "cyan",
    injectAgentsMd: false,
    ...(options.modelSelection ? { modelSelection: options.modelSelection } : {}),
    source: "built-in",
    systemPrompt: "",
    tools: ["Bash", "Glob", "Grep", "Read", "WebFetch", "WebSearch", "TodoWrite"],
  };
}
```

```ts
export function createBuiltInGeneralPurposeAgentProfile(
  options: { modelSelection?: ModelSelection } = {},
): AgentProfile {
  return {
    name: DEFAULT_SUBAGENT_TYPE,
    description:
      "General-purpose agent for researching complex questions, searching for code, and executing multi-step tasks. When you are searching for a keyword or file and are not confident that you will find the right match in the first few tries use this agent to perform the search for you.",
    // 内置子智能体使用显式身份色，避免 UI 按名称 hash 后把 general-purpose 显示为红色。
    color: "blue",
    injectAgentsMd: true,
    ...(options.modelSelection ? { modelSelection: options.modelSelection } : {}),
    source: "built-in",
    systemPrompt: buildGeneralPurposeSystemPrompt(),
    tools: ["*"],
  };
}
```

Explore's tool surface has a second, embedded-search variant — `explore-tools.ts:4-22`:

```ts
export const EXPLORE_AGENT_ALLOWED_TOOLS = [
  "Bash", "Glob", "Grep", "Read", "WebFetch", "WebSearch", "TodoWrite",
] as const;

export const EXPLORE_AGENT_EMBEDDED_SEARCH_ALLOWED_TOOLS = [
  "Bash", "Read", "WebFetch", "WebSearch", "TodoWrite",
] as const;
```

with the deliberate design note at `explore-tools.ts:1-3`: *"白名单刻意不含任何文件写工具（Write/Edit/ApplyPatch），因此 Bash 是唯一的副作用入口，只读语义靠 Explore prompt 约束。"*

### 2.6 How profiles are advertised to the parent model

`profile.ts:132-152`:

```ts
export function formatAgentProfilesForPrompt(
  profiles: readonly AgentProfile[],
  options: { embeddedSearchEnabled?: boolean } = {},
): string | null {
  const active = normalizeAgentProfiles(profiles);
  if (active.length === 0) return null;

  return [
    "Available agent types and the tools they have access to:",
    ...active.map((profile) => {
      const tools = isBuiltInExploreAgentProfile(profile)
        ? formatExploreAllowedToolsForAgentDescription(options)
        : profile.tools
          ? filterSubagentChildToolNames(profile.tools, profile.disallowedTools)
          : undefined;
      const toolText = typeof tools === "string" ? tools : tools?.join(", ");
      const suffix = toolText ? ` (Tools: ${toolText})` : "";
      return `- ${profile.name}: ${profile.description}${suffix}`;
    }),
  ].join("\n");
}
```

---

## 3. SPAWN

### 3.1 The runner entry function signature

`subagent/runner.ts:131` — the port factory:

```ts
export function createExploreSubagentPort(options: ExploreSubagentPortOptions): SubagentPort {
```

with `ExploreSubagentPortOptions` at `runner.ts:109-129` (verbatim):

```ts
export interface ExploreSubagentPortOptions {
  runExploreAgent: (
    request: ExploreSubagentRuntimeRequest,
    options?: SubagentRunOptions,
  ) => Promise<ExploreSubagentRuntimeResult>;
  emitParentEvent: (event: SessionEvent, traceContext: TraceContext) => Promise<void>;
  // background completion 必须同步写入父 runtime command queue；
  // 返回 undefined 可让 TypeScript 拒绝 async enqueue，避免 fake-notified。
  enqueueParentTaskNotification?: EnqueueParentTaskNotification;
  outputRootDir?: string;
  profiles?: readonly AgentProfile[];
  builtInModelSelectionOverrides?: Partial<
    Record<"general-purpose" | "Explore", import("@zcode/shared").ModelSelection>
  >;
  runtimeTaskRegistry?: RuntimeTaskRegistry;
  createAgentId?: () => string;
  getAllowedTools?: (profile: AgentProfile) => readonly string[];
  inactivityTimeoutMs?: number;
  autoBackgroundMs?: number;
  logger?: Logger;
}
```

The port's four entry points (`runner.ts:140-581`):

```ts
  const port: SubagentPort & { start: NonNullable<SubagentPort["start"]> } = {
    async launch(rawRequest: SubagentLaunchRequest, launchOptions?: SubagentLaunchOptions): Promise<AgentOutput> {
    async run(rawRequest: SubagentRunRequest, runOptions?: SubagentRunOptions): Promise<AgentOutput> {
    async start(rawRequest: SubagentStartRequest, startOptions?: SubagentStartOptions): Promise<AgentBackgroundedOutput> {
```
plus `getTask`, `backgroundTask`, `waitForTask`, `stopTask`, `sendMessage` (`runner.ts:528-580`).

`runExploreAgent`'s request object is `ExploreSubagentRuntimeRequest` (`runner.ts:68-90`), and its result
is `ExploreSubagentRuntimeResult` (`runner.ts:92-96`):

```ts
export interface ExploreSubagentRuntimeResult {
  response: string;
  traceId: TraceContext["traceId"];
  events: SessionEvent[];
}
```

### 3.2 Launch path, end to end

```
model emits tool_use "Agent"
  → tool/handlers/agent.ts:174  agentHandler
      AgentInputSchema.parse(input)                        agent.ts:175
      agentType = parsed.subagent_type ?? "general-purpose" agent.ts:176
      context.subagentPort required, else SUBAGENT_UNAVAILABLE agent.ts:178-191
      request = { sessionId, turnId, parentToolCallId: context.toolCallId,
                  agentType, description, prompt,
                  callerCanReadOutputFile, workingDirectory, workspaceRoot, trace } agent.ts:193-210
      context.subagentPort.launch({...request, runInBackground}, {signal, model, modelOverride}) agent.ts:211-221
  → subagent/runner.ts:141  port.launch
      resolveAgentProfileForRequest(profiles, rawRequest)   runner.ts:145
      backgroundRequested = runInBackground === true || profile.background === true  runner.ts:147-148
      if background && launchOptions.modelOverride.background === "deny" → BACKGROUND_UNAVAILABLE  runner.ts:150-164
      background → port.start(...)   else → port.run(...)    runner.ts:165-170
  → subagent/runner.ts:173  port.run (foreground)
      createSubagentLifecycle(...)  → agentId, childSessionId, output files, trace ctx  runner.ts:178, 800-847
      registry.register(snapshot{isBackgrounded:false,status:"running"})  runner.ts:181-189
      writeAgentMetadataFile(..., "running")   runner.ts:191  (failure → registry.remove, throw)
      createSubagentTaskAbortController(...)   runner.ts:199-203
      createSubagentActivityWatchdog(...)      runner.ts:208-215
      runAgentToCompletion(...)                runner.ts:220-257
         └─ onSessionReady → emit SubagentSpawned + readyGate.resolve()
      await guardSubagentPromiseWithAbort(readyGate.promise, ...)   runner.ts:260-265
      Promise.race([completion, waitForBackgroundRequest, autoBackgroundTimer])  runner.ts:302-316
  → runtime/methods/subagent.ts:93   runExploreAgent   (the real child runtime)
```

`port.start` is the same shape but registers `isBackgrounded: true` up front, calls `runBackgroundAgent`,
and returns `createAgentBackgroundedOutput` immediately (`runner.ts:439-526`).

### 3.3 Inherited vs isolated — exact table

The child runtime is constructed at `runtime/methods/subagent.ts:239`:
`const childRuntime = new AgentRuntime(request.sessionId, { ...config }, { ...deps })`.

| Aspect | Behaviour | Evidence |
|---|---|---|
| **parent session id** | carried on the request (`request.sessionId`), used for trace attributes, artifact directory, and as `parentSessionId` of the child | `runner.ts:816-834`; `subagent.ts:270` |
| **child session id** | **new**, `createSessionId(\`subagent_${agentId}\`)`; `agentId` is `agent_${crypto.randomUUID()}` | `runner.ts:805-806` |
| **event store** | **shared instance** (`eventStore: this.eventStore`) — child events persist under the child session id, through the parent's store | `subagent.ts:296` |
| **event sinks / UI** | child raw events go to parent sinks with `sessionId` rewritten to the child; a *mirrored* subset also goes to the parent session | `subagent.ts:332-364`; `tool-event-mirror.ts:38-99` |
| **permission broker** | **wrapped, routed to parent session**; `sessionId` rewritten to `context.parentSessionId`, `origin` keeps the child | `child-client-ports.ts:42-59`; `subagent-interaction-broker.ts:31-38` |
| **permission service** | built-in Explore → **fresh** `new PermissionService(defaultPermissionConfig)`; general-purpose + custom → parent's | `subagent.ts:315-318` |
| **permission mode** | frontmatter `permissionMode` > (`auto`\|`plan`); `undefined` → Explore gets `yolo`, everything else inherits the parent mode | `subagent.ts:170-174`, `473-488` |
| **tools** | profile `tools`/`disallowedTools` filtered; `["*"]` or `[]` means "inherit parent's available tools"; MCP tools re-derived from the parent startup snapshot; `Agent`/`Task` always removed; `RespondToCoordinator` always appended; plan tools always removed | `runner.ts:2124-2137`; `subagent.ts:490-526`, `541-548`; `tool-policy.ts:4-13` |
| **nested subagents** | **disabled** — child config hardcodes `subagents: { enabled: false }` | `subagent.ts:284-287` |
| **model selection** | override (Core Server) > explicit profile `modelSelection` > parent selection; a *concrete inherited model object* wins over the profile selection when the caller passed `options.model` | `subagent-selection.ts:15-58`; `subagent.ts:100-111`, `186-204` |
| **working directory** | `request.workingDirectory` = parent tool context's cwd; `envInfo.cwd` is overridden to it; the child is told *"cwd reset between bash calls"* | `subagent.ts:147`, `251`; `system-prompt.ts:13` |
| **workspace root** | `request.workspaceRoot` (parent's), used for project-scope memory and metadata | `subagent.ts:137`; `runner.ts:2014` |
| **memory root** | `memoryRoot: persistentMemory?.rootDir` — **undefined unless** the profile declares `memory:` *and* memory is enabled | `subagent.ts:327`; `persistent-memory.ts:78-79` |
| **MCP** | borrowed read-only view of the parent's startup snapshot; lifecycle mutations throw | `borrowed-mcp-port.ts:9-71`; `subagent.ts:557-619` |
| **skills** | `FilteredSkillPort` over the parent's port, narrowed to `profile.skills`; official Computer-Use skills rejected | `subagent.ts:709-722`, `724-832` |
| **agent telemetry** | `agentTelemetry.port` inherited; causation is `"child"` for foreground, `"linked_root"` for background | `subagent.ts:291-295` |
| **model request admission** | inherited (`modelRequestAdmission: this.modelRequestAdmission`) | `subagent.ts:300` |
| **context/skills of parent** | only `userInstructions` (AGENTS.md) when `injectAgentsMd !== false`, plus `currentDate`; **Project Context is not inherited** | `subagent.ts:96-99`, `263`, `264-267` (comment `262`) |
| **misc config inherited** | `modelContextBudgetStrategy`, `modelStreaming`, `bashTimeoutPolicy`, `midConversationSystem`, `bashShellSelection`, `embeddedSearchBackend`, `nativeSearchEnhancementsEnabled`, `dynamicWorkflowEnabled`, `toolDisallowlist` | `subagent.ts:250-283` |
| **maxTurns** | `request.maxTurns ?? config.subagents?.maxTurns ?? 4` | `subagent.ts:269` |
| **child `taskType`** | `"subagent_child"` (this is what gates notification sealing) | `subagent.ts:271` |

Child artifacts on disk (`runner.ts:808-815`):

```ts
  const agentOutputDir = join(
    options.outputRootDir ?? join(tmpdir(), "zcode-agents"),
    request.sessionId,
    agentId,
  );
  const metadataFile = join(agentOutputDir, "metadata.json");
  const outputFile = join(agentOutputDir, "output.txt");
  const taskOutputFile = join(agentOutputDir, "task.output");
```

`metadata.json` embeds a **full `profileSnapshot`** plus cwd/workspaceRoot/prompt/status
(`runner.ts:1995-2020`).

The child's first turn is a synthetic, non-user turn (`subagent.ts:399-406`):

```ts
        return await childRuntime.executeTurn(request.prompt, undefined, {
          abortSignal: options?.signal,
          inputSource: "subagent",
          inputPresentation: "coordinator_input",
          traceContext: request.traceContext,
        });
```

…and the `finally` seals child notifications and cancels its background tasks (`subagent.ts:407-419`).

Agent-type resolution is fuzzy on purpose — exact name first, then NFKC + lowercase + whitespace/dash/
underscore-stripped comparison, with an explicit *ambiguous* error (`runner.ts:757-793`):

```ts
function normalizeAgentTypeForMatch(agentType: string): string | undefined {
  const normalized = agentType
    .trim()
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[\p{White_Space}\p{Pd}_]+/gu, "");
  return normalized.length > 0 ? normalized : undefined;
}
```

The canonical name is written back into the request so toolset/events/metadata all agree
(`runner.ts:710-718`).

---

## 4. The management TOOLS

### 4.1 Name literals

| Tool | Literal | Declared at |
|---|---|---|
| `Agent` | `"Agent"` | `tool/compat.ts:1`, `handlers/agent.ts:227` |
| `Task` (alias) | `"Task"` | `tool/compat.ts:2`, `handlers/agent.ts:292` |
| `TaskOutput` | `"TaskOutput"` | `contracts/src/tools/task-output.ts:4` |
| `TaskStop` | `"TaskStop"` | `contracts/src/tools/task-stop.ts:4` |
| `SendMessage` | `"SendMessage"` | `contracts/src/tools/send-message.ts:4` |
| `RespondToCoordinator` | `"RespondToCoordinator"` | `contracts/src/tools/respond-to-coordinator.ts:4` |

`Task`/`Agent` are aliases (`tool/compat.ts:1-14`):

```ts
const AGENT_TOOL_NAME = "Agent";
export const TASK_TOOL_NAME = "Task";

const subagentDispatchToolNames = new Set<string>([AGENT_TOOL_NAME, TASK_TOOL_NAME]);
...
export function isSubagentDispatchToolName(toolName: string | undefined): boolean {
  return toolName ? subagentDispatchToolNames.has(toolName) : false;
}
```

Both are stripped from child tool allowlists (`subagent.ts:516`) — **no recursive agent spawning**.

### 4.2 `Agent` — exact input params

`contracts/src/tools/agent.ts:18-33` (verbatim):

```ts
export const AgentInputSchema = z.object({
  description: z.string().describe("A short (3-5 word) description of the task"),
  prompt: z.string().describe("The task for the agent to perform"),
  subagent_type: z
    .string()
    .optional()
    .describe("The type of specialized agent to use for this task"),
  // subagent 模型由 Settings / Markdown profile 统一决定；若把调用级
  // model 暴露给父模型，历史 tool call 会持续生成旧 override 并覆盖当前配置。
  run_in_background: z
    .boolean()
    .optional()
    .describe(
      "Set to true to run this agent in the background. You will be notified when it completes.",
    ),
});
```

There is **no `model` parameter** — deliberately (comment at `agent.ts:25-26`). Model choice is
Settings/Markdown-profile only.

Tool metadata (`handlers/agent.ts:224-280`), key fields: `readOnly: true`, `destructive: false`,
`concurrentSafe: true`, `maxOutputBytes: MAX_AGENT_MODEL_BYTES` where
`const MAX_AGENT_MODEL_BYTES = 120_000` (`agent.ts:21`), `timeout: { kind: "none" }`,
`permission.permission: "subagent"`. Cancellation message (`agent.ts:268-273`):

```ts
  cancellation: {
    supported: true,
    cleanup: "bestEffort",
    userVisibleMessage:
      "Agent was cancelled before the subagent returned findings or background launch completed",
  },
```

Provider description, verbatim highlights (`handlers/agent.ts:102-125`) — includes the agent list from
`formatAgentProfilesForPrompt` and these operating rules:

```
"When using the Agent tool, specify a subagent_type parameter to select which agent type to use. If omitted, the general-purpose agent is used."
...
"- The agent's final message is returned to you as the tool result; it is not shown to the user — relay what matters.",
"- A new Agent call starts fresh, so the prompt must be self-contained.",
"- `run_in_background: true` runs the agent asynchronously; you'll be notified when it completes.",
"- When you launch multiple agents for independent work, send them in a single message with multiple tool uses so they run concurrently.",
```

`canReadOutputFile` is derived from the parent's provider-visible tools (`agent.ts:200`, `282-285`):

```ts
function canReadBackgroundOutputFile(toolNames: readonly string[] | undefined): boolean {
  const names = new Set(toolNames ?? []);
  return names.has("Read") || names.has("Bash");
}
```

### 4.3 `TaskOutput`

`contracts/src/tools/task-output.ts:4-31`:

```ts
export const TASK_OUTPUT_TOOL_NAME = "TaskOutput";
export const TASK_OUTPUT_ALIASES = [
  "AgentOutputTool",
  "BashOutputTool",
  "AgentOutput",
  "BashOutput",
] as const;
...
export const TaskOutputInputSchema = z
  .object({
    task_id: z.string().describe("The task ID to get output from"),
    block: semanticBoolean(z.boolean().default(true)).describe("Whether to wait for completion"),
    timeout: z.number().min(0).max(600_000).default(30_000).describe("Max wait time in ms"),
  })
  .strict();
```

`semanticBoolean` accepts the strings `"true"`/`"false"` (`task-output.ts:70-78`). The provider JSON
schema re-marks all three as required (`task-output.ts:35-40`).

The provider description is explicitly **DEPRECATED** and warns about the local_agent case
(`task-output.ts:12-23`) — verbatim:

```
DEPRECATED: Background tasks return their output file path in the tool result, and you receive a <task-notification> with the same path when the task completes.
- For bash tasks: prefer using the Read tool on that output file path — it contains stdout/stderr.
- For local_agent tasks: use the Agent tool result directly. Do NOT Read the .output file — it is a symlink to the full subagent conversation transcript (JSONL) and will overflow your context window.
- For remote_agent tasks: prefer using the Read tool on the output file path — it contains the streamed remote session output (same as bash).
...
```

Behaviour constants (`handlers/task-output.ts:29-38`):

```ts
const TASK_OUTPUT_DEFAULT_LENGTH = 32_000;
const TASK_OUTPUT_MAX_LENGTH = 160_000;
const TASK_OUTPUT_PERSIST_THRESHOLD_CHARS = 100_000;
const TASK_OUTPUT_RESULT_BUDGET_BYTES = 400_000;
const TASK_OUTPUT_PERSIST_PREVIEW_CHARS = 2_000;
const TASK_OUTPUT_POLL_INTERVAL_MS = 100;
```

`block: false` → `not_ready` if still running; `block: true` polls every 100 ms up to `timeout` then
returns `timeout` (`task-output.ts:55-80`, `229-243`). Reading the output marks the task `notified` so
the completion notification is not also delivered (`task-output.ts:253-257`, comment at `60-62`).
Error codes: `TASK_ID_REQUIRED: 1`, `TASK_NOT_FOUND: 2` (`task-output.ts:35-38`).
`TaskOutput` has **no alias for `TaskStop`**-style `shell_id`; only `task_id`.

### 4.4 `TaskStop`

`contracts/src/tools/task-stop.ts:4-14`:

```ts
export const TASK_STOP_TOOL_NAME = "TaskStop";

export const TaskStopInputSchema = z
  .object({
    task_id: z.string().optional().describe("The ID of the background task to stop"),
    shell_id: z
      .string()
      .optional()
      .describe("Deprecated: use task_id instead"),
  })
  .strict();
```

Handler reads both (`handlers/task-stop.ts:29`): `const taskId = parsed.task_id ?? parsed.shell_id;`
Aliases (`task-stop.ts:97`): `aliases: ["KillShell", "KillBash"]`. Provider description
(`handlers/task-stop.ts:18-25`):

```
- Stops a running background task by its ID
- Takes a task_id parameter identifying the task to stop
- Returns a success or failure status
- Use this tool when you need to terminate a long-running task
```

The model-initiated stop is labelled (`task-stop.ts:50-55`):

```ts
  const result = await context.backgroundTaskControlPort.stopBackgroundTask(taskId, {
    // 模型自己停的：终态通知会说「stopped by you」，而不是把它写成用户的决定。
    initiator: "model",
    strict: true,
    traceContext: context.traceContext,
  });
```

### 4.5 `SendMessage`

`contracts/src/tools/send-message.ts:4-25`:

```ts
export const SEND_MESSAGE_TOOL_NAME = "SendMessage";
export const SEND_MESSAGE_MAX_CONTENT_CHARS = 20_000;

export const SendMessageInputSchema = z
  .object({
    to: z
      .string()
      .min(1)
      .max(200)
      .describe("Recipient: local agent ID returned by Agent (format agent_<uuid>)."),
    summary: z
      .string()
      .min(1)
      .max(200)
      .describe("A 5-10 word summary shown as a preview in the UI."),
    message: z
      .string()
      .min(1)
      .max(SEND_MESSAGE_MAX_CONTENT_CHARS)
      .describe("Plain text message content"),
  })
  .strict();
```

Output carries the delivery mode (`send-message.ts:36`):
`delivery: z.enum(["queued", "steered", "resumed_background"]).optional()`.

Provider description (`handlers/send-message.ts:25-35`) shows a JSON example and the rule *"Your plain
text output is NOT visible to other agents — to communicate, you MUST call this tool."*
It is refused on off-peak turns (`send-message.ts:50-53`, `16-23`) — hint:
`"Spawn a new foreground Agent with the full context instead of resuming a completed one."`
Permission: `"agent.message.send"` (`send-message.ts:106`). `MAX_SEND_MESSAGE_MODEL_BYTES = 4096`,
timeout fixed at 10 000 ms with `allowCallOverride: false` (`send-message.ts:15`, `124-128`).

### 4.6 `RespondToCoordinator` — the child's outbound channel

`contracts/src/tools/respond-to-coordinator.ts:4-16`:

```ts
export const RESPOND_TO_COORDINATOR_TOOL_NAME = "RespondToCoordinator";
export const RESPOND_TO_COORDINATOR_MAX_CONTENT_CHARS = 20_000;

export const RespondToCoordinatorInputSchema = z
  .object({
    summary: z.string().min(1).max(200).describe("Short summary of the response."),
    message: z
      .string()
      .min(1)
      .max(RESPOND_TO_COORDINATOR_MAX_CONTENT_CHARS)
      .describe("Response or progress update for the coordinator."),
  })
  .strict();
```

Handler requires the child scope (`handlers/respond-to-coordinator.ts:30-42`):

```ts
  if (context.runtimeScope !== "subagent" || !context.coordinatorResponsePort) {
    throw createCoreError(
      CoreErrorType.ConfigurationError,
      "Coordinator response port is not configured for RespondToCoordinator",
```

`modelInstructions` (`respond-to-coordinator.ts:57-64`), verbatim:

```ts
    modelInstructions: [
      'When you receive "The coordinator sent a message while you were working:" (or the legacy "Message from coordinator:" prefix), use this tool to answer it.',
      "Use this tool for a concise response or progress update to the coordinator.",
      "When replying while work remains, do not use assistant text as the reply.",
      "Place this call before or alongside the next work tool call when possible.",
      "Continue the current task unless the coordinator explicitly changed or ended it.",
      "Do not use this tool as a substitute for the final task result.",
    ],
    allowedInPlanMode: true,
```

Permission `"agent.message.respond"` (`respond-to-coordinator.ts:82`). It is force-added to every child's
allowlist regardless of profile tools (`subagent.ts:541-548`):

```ts
function appendCoordinatorResponseTool(toolNames: readonly string[]): readonly string[] {
  if (toolNames.includes(RESPOND_TO_COORDINATOR_TOOL_NAME)) {
    return toolNames;
  }

  // child 控制通道不受 profile 工具列表约束；全局 toolDisallowlist 仍在 runtime 注册边界生效。
  return [...toolNames, RESPOND_TO_COORDINATOR_TOOL_NAME];
}
```

---

## 5. HOW SUBAGENTS REPORT BACK

### 5.1 `completion-notification.ts` — verbatim

`subagent/completion-notification.ts:1-51`:

```ts
import type { ModelUsage } from "@zcode/contracts";
import { formatTaskNotification } from "../runtime-task/notification.js";

type LocalAgentTaskNotificationStatus = "completed" | "failed" | "stopped";

interface LocalAgentTaskNotificationInput {
  agentId: string;
  agentType: string;
  description: string;
  error?: string;
  outputFile: string;
  parentToolCallId: string;
  result?: string;
  status: LocalAgentTaskNotificationStatus;
  totalDurationMs?: number;
  totalTokens?: number;
  totalToolUseCount?: number;
  usage?: ModelUsage;
}

export function formatLocalAgentTaskNotification(input: LocalAgentTaskNotificationInput): string {
  return formatTaskNotification({
    agentId: input.agentId,
    description: input.description,
    error: input.error,
    outputFile: input.outputFile,
    result: input.result,
    status: input.status,
    subagentType: input.agentType,
    summary: formatLocalAgentNotificationSummary(input),
    taskId: input.agentId,
    taskType: "local_agent",
    toolUseId: input.parentToolCallId,
    usage: {
      durationMs: input.totalDurationMs,
      modelUsage: input.usage,
      toolUseCount: input.totalToolUseCount,
      totalTokens: input.totalTokens,
    },
  });
}

function formatLocalAgentNotificationSummary(
  input: Pick<LocalAgentTaskNotificationInput, "agentType" | "description" | "error" | "status">,
): string {
  const summary = `Agent ${input.agentType} task "${input.description}" ${input.status}.`;
  const error = input.status === "failed" && input.error?.trim() ? input.error : undefined;
  // 失败 summary 是 Agent 卡“子智能体输出”的来源，必须保留原前缀并
  // 直接追加与 <error> 相同的失败原因；completed/stopped 文案保持不变。
  return error ? `${summary} ${error}` : summary;
}
```

The actual XML is rendered by `runtime-task/notification.ts:144-158` (verbatim):

```ts
function formatLocalAgentTaskNotification(input: TaskNotificationInput): string {
  const lines = ["<task-notification>", `<task-id>${escapeXml(input.taskId)}</task-id>`];
  if (input.toolUseId) lines.push(`<tool-use-id>${escapeXml(input.toolUseId)}</tool-use-id>`);
  if (input.outputFile) lines.push(`<output-file>${escapeXml(input.outputFile)}</output-file>`);
  lines.push(`<status>${escapeXml(input.status)}</status>`);
  lines.push(`<summary>${escapeXml(input.summary)}</summary>`);
  if (input.result !== undefined) lines.push(`<result>${escapeXml(input.result)}</result>`);
  if (input.error !== undefined) lines.push(`<error>${escapeXml(input.error)}</error>`);

  const usage = formatLocalAgentUsage(input.usage);
  if (usage) lines.push(usage);
  lines.push("</task-notification>");

  return truncateTaskNotification(lines.join("\n"));
```

Usage renders as one line (`notification.ts:333-348`), e.g.
`<usage><subagent_tokens>123</subagent_tokens><tool_uses>7</tool_uses><duration_ms>9000</duration_ms></usage>`.
Total cap: `TASK_NOTIFICATION_MAX_CHARS = 120_000` (`notification.ts:18`), truncation appends
`"\n[truncated]"` (`notification.ts:380-383`).

### 5.2 `tool-event-mirror.ts` — key functions verbatim

Constants (`tool-event-mirror.ts:16-36`):

```ts
const SUBAGENT_TOOL_CALL_ID_PREFIX = "tool_subagent";
const SUBAGENT_EVENT_SOURCE = "subagent";

const MIRRORED_TOOL_EVENT_TYPES = new Set<SessionEventType>([
  SessionEventType.ToolCallScheduled,
  SessionEventType.ToolCallStarted,
  SessionEventType.ToolCallProgress,
  SessionEventType.ToolCallResult,
  SessionEventType.ToolCallError,
]);

const MIRRORED_INTERACTION_EVENT_TYPES = new Set<SessionEventType>([
  SessionEventType.PermissionRequested,
  SessionEventType.PermissionResolved,
  SessionEventType.PermissionDenied,
]);

interface SubagentToolEventMirrorContext extends SubagentInteractionOriginContext {
  background: boolean;
  toolNameByChildToolCallId?: Map<string, string>;
}
```

The mirror function (`tool-event-mirror.ts:38-99`):

```ts
export function mirrorSubagentToolEvent(
  event: SessionEvent,
  context: SubagentToolEventMirrorContext,
): SessionEvent | undefined {
  if (
    !MIRRORED_TOOL_EVENT_TYPES.has(event.type) &&
    !MIRRORED_INTERACTION_EVENT_TYPES.has(event.type)
  ) {
    return undefined;
  }

  const payload = asRecord(event.payload);
  const childToolCallId = stringField(payload, "toolCallId");
  if (!childToolCallId) return undefined;

  const toolCallId = mirroredToolCallId(context.agentId, childToolCallId);
  const toolName =
    stringField(payload, "toolName") ?? context.toolNameByChildToolCallId?.get(childToolCallId);
  if (toolName) {
    context.toolNameByChildToolCallId?.set(childToolCallId, toolName);
  }

  if (MIRRORED_INTERACTION_EVENT_TYPES.has(event.type)) {
    // V4 只从父 task 的实时投影生成阻塞交互；仅转发 broker request 会让
    // 子 agent 的 permission / AskUserQuestion 停留在子 session，父界面无法响应。
    const mirroredPayload = {
      ...payload,
      toolCallId,
      ...(toolName ? { toolName } : {}),
      childSessionId: context.childSessionId,
      ...(context.background ? { background: true } : {}),
      ...(event.type === SessionEventType.PermissionRequested
        ? { origin: buildSubagentInteractionOrigin(context, event.turnId) }
        : {}),
    };

    return createSessionEvent(event.type, context.parentSessionId, mirroredPayload, {
      traceId: event.traceId,
      turnId: context.parentTurnId,
    });
  }

  const mirroredPayload = {
    ...payload,
    toolCallId,
    ...(toolName ? { toolName } : {}),
    ...mirrorScheduleFields(payload, context.agentId),
    agentId: context.agentId,
    agentType: context.agentType,
    ...(context.background ? { background: true } : {}),
    childSessionId: context.childSessionId,
    childToolCallId,
    description: context.description,
    parentToolCallId: context.parentToolCallId,
    source: SUBAGENT_EVENT_SOURCE,
  };

  return createSessionEvent(event.type, context.parentSessionId, mirroredPayload, {
    traceId: event.traceId,
    turnId: context.parentTurnId,
  });
}
```

ID namespacing (`tool-event-mirror.ts:132-134`):

```ts
function mirroredToolCallId(agentId: string, childToolCallId: string): ToolCallId {
  return `${SUBAGENT_TOOL_CALL_ID_PREFIX}_${agentId}_${childToolCallId}` as ToolCallId;
}
```

`mirrorScheduleFields` rewrites `dependencies`, `schedule.executionOrder` and
`schedule.parallelGroups` through the same prefix so the parent's scheduler view stays consistent
(`tool-event-mirror.ts:101-130`).

The mirror is invoked from the child's event sink (`subagent.ts:332-363`), which first forwards the
**raw child event** to parent sinks with the child's `sessionId` (so the UI's child topic keeps
streaming), then forwards the mirrored event with the parent's session id. Raw child events are
*notified* only — not re-appended — so nothing is double-persisted (`subagent.ts:335-343`).

### 5.3 Foreground: the parent gets a normal tool result

`runAgentToCompletion` builds `AgentCompletedOutput` (`runner.ts:1170-1186`):

```ts
  const output: AgentCompletedOutput = {
    status: "completed",
    agentId: lifecycle.agentId,
    agentType: request.agentType,
    description: request.description,
    prompt: request.prompt,
    content: [
      {
        type: "text",
        text: childResult.response,
      },
    ],
    totalToolUseCount,
    totalDurationMs,
    ...(totalTokens === undefined ? {} : { totalTokens }),
    ...(usage === undefined ? {} : { usage }),
  };
```

…and `formatAgentOutputForModel` turns it into the tool result text
(`handlers/agent.ts:130-172`, key branch `137-150`):

```ts
  if (data.status !== "async_launched") {
    const childText = data.content.map((block) => block.text).join("\n");
    const childContent =
      childText.trim().length > 0 ? [childText] : ["(Subagent completed but returned no output.)"];
    const usageLines = [
      ...(data.totalTokens === undefined ? [] : [`subagent_tokens: ${data.totalTokens}`]),
      `tool_uses: ${data.totalToolUseCount}`,
      `duration_ms: ${data.totalDurationMs}`,
    ];
    return [
      ...childContent,
      `agentId: ${data.agentId} (use SendMessage with to: '${data.agentId}' to continue this agent)`,
      `<usage>${usageLines.join("\n")}</usage>`,
    ].join("\n");
  }
```

So: **foreground result = the child's final assistant text, in-band, as the `Agent` tool result**, with
usage and a `SendMessage` continuation hint. The child text is never shown to the user directly — the
provider description says so explicitly (`agent.ts:113`).

### 5.4 Background: enqueue path

Step 1 — the runner formats and enqueues (`runner.ts:1830-1896`), verbatim core:

```ts
function enqueueBackgroundNotification(
  options: ExploreSubagentPortOptions,
  registry: RuntimeTaskRegistry,
  taskId: string,
  message: string,
  traceContext: TraceContext,
): boolean {
  if (!options.enqueueParentTaskNotification) {
    options.logger?.warn("Skipped subagent background notification without parent queue", {...});
    return false;
  }

  const task = registry.get(taskId);
  if (!task || task.notified) {
    options.logger?.debug("Skipped duplicate subagent background notification", {...});
    return false;
  }

  try {
    options.enqueueParentTaskNotification({
      originMeta: {
        backgroundSource: "subagent",
        title: task.description.trim() || taskId,
        workId: taskId,
      },
      taskId,
      text: message,
      traceContext,
    });
  } catch (error) {
    options.logger?.warn("Failed to enqueue subagent background notification", {...});
    return false;
  }

  registry.update(taskId, (current) =>
    current.notified
      ? current
      : {
          ...current,
          notified: true,
        },
  );
  options.logger?.info?.("Subagent background notification enqueued", {...});
  return true;
}
```

Note the return type contract `EnqueueParentTaskNotification = (notification) => undefined`
(`runner.ts:105-107`) — returning `undefined` rather than a promise is what makes a fake-notified bug a
type error (comment at `runner.ts:115-116`).

Step 2 — the runtime bridge is synchronous on purpose (`subagent.ts:79-87`):

```ts
    enqueueParentTaskNotification: (notification) => {
      this.enqueueBackgroundTaskNotification({
        originMeta: notification.originMeta,
        taskId: notification.taskId,
        text: notification.text,
        traceContext: notification.traceContext,
      });
      return undefined;
    },
```

Step 3 — `enqueueBackgroundTaskNotification` writes the runtime command queue and the durable ledger
(`runtime/methods/background-notifications.ts:44-82`):

```ts
  const commandId = createRuntimeCommandId();
  this.enqueueRuntimeCommand({
    branchGeneration,
    createdAt: new Date(),
    id: commandId,
    mode: "task-notification",
    priority: "next",
    source: "background_task",
    originMeta: notification.originMeta,
    taskId: notification.taskId,
    text: notification.text,
    toolName: notification.toolName,
    traceContext: notification.traceContext,
  });
  // wake 入账本（admitted）。runtime 命令队列是纯内存的，账本是唯一
  // durable 痕迹——崩溃重启后后台子进程已死、通知不可恢复，resume 会把残留
  // admitted 收口为 discarded(session_resumed)（留痕不静默，同一语义）。
  const admission = this.sessionStore?.saveSessionInput?.({
    id: String(commandId),
    sessionID: this.sessionId,
    kind: "backgroundNotification",
    delivery: "queue",
    payload: { text: notification.text, ... },
  });
```

Guards before that: `shuttingDown` drops it (`background-notifications.ts:20-30`); a stale
`branchGeneration` drops it (`31-43`).

Step 4 — the queue drains and persists one synthetic message per batch
(`background-notifications.ts:166-218`):

```ts
  await this.ensureContextInitialized(firstCommand.traceContext);
  const messageID = createMessageId();
  const inputPresentation = midTurn ? "task_notification_steer" : "task_notification";
  this.messageHistory.addUser(text, runtimeInputMetadata(inputPresentation));
  await this.persistSyntheticUserNoticeForSession({
    messageID,
    metadata: {
      inputPresentation,
      ...(originMeta ? { originMeta } : {}),
      visibility: "model-only",
    },
    sessionId: this.sessionId,
    source: "background_task",
    text,
    traceContext: firstCommand.traceContext,
    visibility: "model-only",
  });
```

Batching is deliberate (comment `192-193`): many pending notifications produce **one** synthetic
message, not one model turn per notification.

**So the parent conversation receives background results as a `source: "background_task"`,
`visibility: "model-only"` synthetic user message** — the model sees it, the UI does not render it as a
user turn's content. `mode: "task-notification"`, `priority: "next"`, `source: "background_task"`
(`command-queue.ts:65-73`).

### 5.5 Child → parent mid-flight replies

`RespondToCoordinator` → `createCoordinatorResponsePort` (`coordinator-response.ts:19-52`) which calls
the injected sync `enqueue`:

```ts
      try {
        options.enqueue({
          responseId,
          agentId: options.agentId,
          agentType: options.agentType,
          childSessionId: options.childSessionId,
          childToolCallId: String(request.childToolCallId),
          ...(options.parentToolCallId ? { parentToolCallId: options.parentToolCallId } : {}),
          summary: request.summary,
          message: request.message,
          traceContext: request.trace,
        });
        return {
          status: "success",
          responseId,
          message: "Response was queued for the coordinator.",
        };
```

`enqueue` is `this.enqueueSubagentMessage` (`subagent.ts:308-314`), which enqueues a
`mode: "subagent-message"`, `priority: "next"`, `source: "subagent_message"` command
(`subagent-messages.ts:43-73`) after a branch-generation fence (`29-42`). The text is XML-wrapped
(`subagent-messages.ts:9-23`):

```ts
function formatSubagentMessage(input: {
  agentId: string;
  agentType: string;
  summary: string;
  message: string;
}): string {
  return [
    "<subagent-message>",
    `<agent-id>${escapeXml(input.agentId)}</agent-id>`,
    `<agent-type>${escapeXml(input.agentType)}</agent-type>`,
    `<summary>${escapeXml(input.summary)}</summary>`,
    `<message>${escapeXml(input.message)}</message>`,
    "</subagent-message>",
  ].join("\n");
}
```

…and drained into a model-only synthetic user message (`subagent-messages.ts:77-106`):

```ts
export async function persistSubagentMessageCommand(
  this: AgentRuntimeInternal,
  command: SubagentMessageRuntimeCommand,
  midTurn = false,
): Promise<MessageId> {
  await this.ensureContextInitialized(command.traceContext);
  const messageID = createMessageId();
  const inputPresentation = midTurn ? "subagent_reply_steer" : "subagent_reply";
  this.messageHistory.addUser(command.text, runtimeInputMetadata(inputPresentation));
  await this.persistSyntheticUserNoticeForSession({
    messageID,
    sessionId: this.sessionId,
    source: "subagent_message",
    text: command.text,
    traceContext: command.traceContext,
    visibility: "model-only",
    metadata: {
      inputPresentation,
      subagentMessage: { responseId, agentId, agentType, childSessionId, childToolCallId, parentToolCallId },
    },
  });
  return messageID;
}
```

Input presentations (`contracts/src/interfaces/runtime-input-presentation.ts:6-10`) —
`"coordinator_steer"`, `"coordinator_input"`, `"subagent_reply_steer"`, `"subagent_reply"`,
`"task_notification_steer"`. Mid-turn variants are chosen by
`runtime/methods/runtime-command-active-loop.ts:76`.

### 5.6 Summary of the three reporting channels

| Channel | Direction | Mechanism | Visibility |
|---|---|---|---|
| Foreground completion | child → parent model | `Agent` tool result via `formatAgentOutputForModel` | in-band tool result |
| Background completion | child → parent model | `task-notification` command → synthetic user message | `model-only` |
| Answer / progress | child → parent model | `RespondToCoordinator` → `subagent-message` command → synthetic user message | `model-only` |
| Steering | parent model → child | `SendMessage` → `steerTurn` | injected into child's active turn |
| Tool/permission activity | child → parent UI | `mirrorSubagentToolEvent` → parent session event | session events |

---

## 6. Background vs foreground, and concurrency caps

### 6.1 The decision

Three independent ways an agent becomes background:

1. **Caller asked**: `run_in_background: true` → `parsed.run_in_background === true` →
   `rawRequest.runInBackground` → `backgroundRequested` (`handlers/agent.ts:214`; `runner.ts:147-148`).
2. **Profile asked**: `AgentProfile.background === true` from Markdown frontmatter — same
   `||` expression (`runner.ts:147-148`).
3. **Escalation while running**: `registry.requestBackground(agentId)` from either
   `Promise.race([... backgroundRequestPromise ...])` (explicit user/UI action via
   `SubagentPort.backgroundTask`, `runner.ts:532-538`) or the **auto-background timer**
   (`runner.ts:291-316`, `660-699`), enabled by `config.subagents.autoBackgroundMs`.

The denial case is explicit (`runner.ts:149-168`): when the caller supplies a one-shot model override
(idle-time/off-peak execution) whose `background` policy is `"deny"`, backgrounding throws
`AgentErrorCode.BACKGROUND_UNAVAILABLE` — *"Idle-time tasks do not support background agents. Run this
agent in the foreground."* A foreground model override also disables both the auto-background timer and
the background-request race (`runner.ts:204-207`, `294`, `302`): `borrowedForegroundAgentIds` prevents
`backgroundTask()` from backgrounding it at all (`runner.ts:532-538`).

Auto-background timeout normalization (`runner.ts:653-658`):

```ts
function normalizeAutoBackgroundMs(value: number | undefined): number | undefined {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    return undefined;
  }
  return Math.trunc(value);
}
```

Foreground agent idleness is bounded by a watchdog (`runner.ts:208-215`) whose timeout defaults to
`DEFAULT_MODEL_STREAM_IDLE_TIMEOUT_MS` and is overridable by `config.subagents.inactivityTimeoutMs`
(`runtime/types.ts:140-141`). On firing it aborts the task with `CoreErrorType.ToolTimeout`,
`retryable: true`, context `{ code: CHILD_RUNTIME_FAILED, idleMs, timeoutMs }`
(`runner.ts:1221-1255`).

### 6.2 Concurrency caps — the constant values

There is **no dedicated subagent-count cap**. Concurrency is governed by the tool scheduler:

- `tool/scheduler.ts:48`: `const DEFAULT_MAX_CONCURRENCY = 10;`
- The `Agent` tool is `concurrentSafe: true` **and** `readOnly: true`
  (`handlers/agent.ts:229-231`; `READ_ONLY_TOOLS` at `scheduler.ts:233-243` is not what marks Agent
  read-only here — the entry sets `readOnly: true` directly).
- `groupByParallel` flushes a parallel group once `parallelGroup.length >= this.maxConcurrency`
  (`scheduler.ts:185-192`), so **at most 10 parallel `Agent` calls per parallel group**.
- The child runtime reuses the parent's scheduler instance:
  `toolScheduler: deps.toolScheduler ?? defaultScheduler` (`subagent.ts:319`), where
  `defaultScheduler` is `defaultToolScheduler` (`runtime/deps.ts:266`).
- Overridable per runtime via `toolConcurrency: { maxConcurrency?: number }` (`runtime/types.ts:157`).

Additional numeric caps and defaults relevant to subagents:

| Constant | Value | Where |
|---|---|---|
| `DEFAULT_MAX_CONCURRENCY` (tool scheduler) | `10` | `tool/scheduler.ts:48` |
| default `maxTurns` for a child | `4` | `runtime/methods/subagent.ts:269` |
| `MAX_AGENT_MODEL_BYTES` | `120_000` | `tool/handlers/agent.ts:21` |
| `MAX_SEND_MESSAGE_MODEL_BYTES` | `4096` | `tool/handlers/send-message.ts:15` |
| `MAX_RESPOND_TO_COORDINATOR_MODEL_BYTES` | `4_096` | `tool/handlers/respond-to-coordinator.ts:14` |
| `MAX_TASK_STOP_MODEL_BYTES` | `100_000` | `tool/handlers/task-stop.ts:14` |
| `SEND_MESSAGE_MAX_CONTENT_CHARS` | `20_000` | `contracts/src/tools/send-message.ts:5` |
| `RESPOND_TO_COORDINATOR_MAX_CONTENT_CHARS` | `20_000` | `contracts/src/tools/respond-to-coordinator.ts:5` |
| `TaskOutput` default length / max | `32_000` / `160_000` chars | `handlers/task-output.ts:29-30` |
| `TaskOutput` poll interval | `100` ms | `handlers/task-output.ts:34` |
| `TaskOutput` timeout | default `30_000`, max `600_000` ms | `contracts/src/tools/task-output.ts:29` |
| `TASK_NOTIFICATION_MAX_CHARS` | `120_000` | `runtime-task/notification.ts:18` |
| SendMessage / TaskStop / RespondToCoordinator timeout | `10_000` ms, no override | `handlers/*.ts` |
| Plan-mode research fan-out guidance | `3` Explore agents | `runtime/helpers/runtime-reminders.ts:23,33` |

The plan-mode reminder is the only place a number of *agents* appears in the prompt
(`runtime-reminders.ts:23`): `const planResearchAgentCount = 3;` used as
`` `2. **Launch up to ${planResearchAgentCount} ${EXPLORE_AGENT_TYPE} agents IN PARALLEL** (single message, multiple tool calls)` ``.
The Agent tool description instead tells the model to batch independent launches in one message
(`handlers/agent.ts:116`).

---

## 7. Subagent context construction (`context-builder.ts`)

**The parent conversation is NOT inherited.** A child gets a freshly-built system prompt plus exactly one
user turn: `request.prompt` (`subagent.ts:399`). There is no transcript slice, no summarised parent
history, no tool-result replay. The Agent tool description states the consequence plainly
(`handlers/agent.ts:114`): *"A new Agent call starts fresh, so the prompt must be self-contained."*

What *is* shared is configuration, not conversation: `currentDate` and `userInstructions` (AGENTS.md)
when `injectAgentsMd !== false` (`subagent.ts:96-99`, `263`), with the explicit comment at
`subagent.ts:262`: *"child 只复用父 runtime 已解析的 instructions snapshot；Project Context 仍不继承。"*

`SubagentContextBuilder` (`context-builder.ts:30-104`) extends the main `ContextBuilder` but bypasses its
system assembly. Section list, built in this order (`context-builder.ts:106-163`):

1. `buildCliPrefixSection()` — `context-builder.ts:107`
2. **Subagent Agent Prompt** — source `subagent_agent_prompt`, `cacheHint: "stable"`, prefixed with a
   single `\n` (`context-builder.ts:108-120`)
3. **Subagent Notes** — source `subagent_notes`, `cacheHint: "stable"`, prefixed `\n\n`
   (`context-builder.ts:122-129`)
4. **Subagent Environment** — source `subagent_environment`, `cacheHint: "dynamic"`, prefixed `\n\n`
   (`context-builder.ts:130-139`)
5. Request-user-context section, if any (`context-builder.ts:142-147`)
6. Current-date section, if any (`context-builder.ts:148-151`)
7. Skills section, if `config.skills` (`context-builder.ts:152-160`)

`orderSubagentSections` puts every `injectionTarget === "system"` section before `meta_user`
(`context-builder.ts:165-170`). Every system section gets a provider cache breakpoint
(`context-builder.ts:51-61`):

```ts
    const systemMessages = orderedSections
      .filter((section) => section.injectionTarget === "system")
      .map(
        (section): ModelInputMessage => ({
          role: "system",
          content: section.content,
          // subagent context builder 不走 main ContextBuilder 的 system 组装，
          // 仍需在每段稳定 child system prompt 上保留 provider cache breakpoint。
          cacheControl: EPHEMERAL_CACHE_CONTROL,
        }),
      );
```

Skills and other meta-user content become `metaUserAttachments` with sources `"skills_listing"` and
`"context_prefix"` (`context-builder.ts:62-96`).

The agent prompt itself is assembled in the runtime, not the builder (`subagent.ts:127-143`):

```ts
      const baseAgentPrompt =
        builtInExplore && request.systemPrompt?.trim() === ""
          ? buildExploreAgentPrompt({ embeddedSearchEnabled })
          : request.systemPrompt?.trim();
      ...
      // 空 agent prompt 不是一个语义段；先硬拼 `\n\n` 会把缺失段的边界
      // 泄漏到 persistent Memory 开头。这里只组合非空正文，block 左边界由 builder 统一添加。
      const agentPrompt = [baseAgentPrompt, persistentMemory?.prompt]
        .filter((part): part is string => typeof part === "string" && part.length > 0)
        .join("\n\n");
```

So the final child system prompt is:
`CLI prefix` + `profile.systemPrompt` + **persistent memory prompt (if any)** + `Subagent Notes` +
`Subagent Environment` (+ AGENTS.md / date / skills sections).

---

## 8. Steering

### 8.1 `message-steering.ts` — verbatim

`subagent/message-steering.ts:1-59`:

```ts
import type { TraceContext, TurnSteerInput, TurnSteerResult } from "@zcode/contracts";
import type { RuntimeTaskMessageSink } from "../runtime-task/registry.js";

interface SteerableRuntime {
  steerTurn(input: string | TurnSteerInput): Promise<TurnSteerResult>;
}

export function createSubagentMessageSink(
  runtime: SteerableRuntime,
  request: { traceContext: TraceContext },
): RuntimeTaskMessageSink {
  return {
    async send(message) {
      const result = await steerSubagentMessage(runtime, request, message);
      if (result.kind === "rejected") {
        throw new Error(`Subagent message rejected: ${result.reason}`);
      }
      return "steered";
    },
  };
}

async function steerSubagentMessage(
  runtime: SteerableRuntime,
  request: { traceContext: TraceContext },
  message: { id: string; message: string; summary?: string; traceContext?: TraceContext },
): Promise<TurnSteerResult> {
  const input = formatSubagentCoordinatorMessage(message);
  for (let attempt = 0; attempt < 20; attempt++) {
    const result = await runtime.steerTurn({
      delivery: "guide",
      inputPresentation: "coordinator_steer",
      input,
      inputId: message.id,
      traceContext: message.traceContext ?? request.traceContext,
    });
    if (result.kind !== "rejected" || result.reason !== "no_active_turn") {
      return result;
    }
    await sleep(10);
  }
  return runtime.steerTurn({
    delivery: "guide",
    inputPresentation: "coordinator_steer",
    input,
    inputId: message.id,
    traceContext: message.traceContext ?? request.traceContext,
  });
}

function formatSubagentCoordinatorMessage(message: { message: string; summary?: string }): string {
  const summary = message.summary?.trim();
  if (!summary) return message.message;
  return [summary, "", message.message].join("\n");
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
```

Key: 20 retries × 10 ms = up to ~200 ms of waiting for the child to have an active turn
(`no_active_turn` is the only retried rejection), then one final attempt. Delivery is `"guide"`
(injected into the running turn), presentation `"coordinator_steer"`.

### 8.2 The sink registration / drain path in the runner

The child runtime hands its sink back via `request.registerMessageSink` (`subagent.ts:397`), which the
runner wired at `runner.ts:1151` to `createMessageSinkRegistration` (`runner.ts:1401-1413`):

```ts
function createMessageSinkRegistration(
  options: ExploreSubagentPortOptions,
  lifecycle: SubagentLifecycle,
  registry: RuntimeTaskRegistry,
): (sink: RuntimeTaskMessageSink) => void {
  return (sink) => {
    registry.update(lifecycle.agentId, (task) => ({
      ...task,
      messageSink: sink,
    }));
    void flushPendingMessages(options, lifecycle, registry, sink);
  };
}
```

`flushPendingMessages` drains messages that arrived before the sink existed
(`runner.ts:1415-1442`) and re-queues the undelivered tail on error.

`sendMessageToLocalAgent` decides the three delivery modes (`runner.ts:900-953`):

```ts
  const message = createRuntimeTaskPendingMessage(request);
  if (!isTerminalRuntimeTask(task)) {
    return deliverMessageToRunningAgent(registry, task, message);
  }
  return resumeTerminalAgentInBackground(options, profiles, registry, abortControllers, task, request, message);
```

- live sink → `"steered"`, and on sink throw → queue → `"queued"` (`runner.ts:936-953`)
- no sink yet → queue → `"queued"`
- terminal task → `resumeTerminalAgentInBackground` → `"resumed_background"` (`runner.ts:955-1062`)

The pending message record is stamped as coordinator-origin (`runner.ts:1064-1079`):

```ts
  return {
    id: `msg_${crypto.randomUUID()}`,
    isMeta: true,
    message: request.message,
    origin: {
      kind: "coordinator",
      toolCallId: String(request.parentToolCallId),
    },
    queuedAt: new Date(),
    summary: request.summary,
    traceContext: request.trace,
  };
```

### 8.3 The `subagent-messages` runtime methods

Both functions are quoted in §5.5: `enqueueSubagentMessage` (`runtime/methods/subagent-messages.ts:25-75`)
and `persistSubagentMessageCommand` (`:77-106`). The branch fence is worth repeating
(`subagent-messages.ts:29-42`):

```ts
  const branchGeneration =
    this.runtimeTaskRegistry.get(input.agentId)?.branchGeneration ?? this.branchGeneration;
  if (branchGeneration !== this.branchGeneration) {
    this.logger?.debug("Dropped stale-branch subagent response", {...});
    return undefined;
  }
```

---

## 9. Persistent subagent memory

### 9.1 Storage roots

`subagent/persistent-memory.ts:12-31` (verbatim):

```ts
function sanitizePersistentAgentMemoryKey(agentName: string): string {
  const key = agentName.replace(/[^a-zA-Z0-9_-]/g, "-");
  return key === "" ? "unknown" : key;
}

function resolvePersistentAgentMemoryRoot(input: {
  agentName: string;
  scope: AgentMemoryScope;
  storageRoot: string;
  workspaceRoot: string;
}): string {
  const agentKey = sanitizePersistentAgentMemoryKey(input.agentName);
  if (input.scope === "user") {
    return join(input.storageRoot, "agent-memory", agentKey);
  }
  const workspace = resolve(input.workspaceRoot);
  return input.scope === "project"
    ? join(workspace, ".zcode", "agent-memory", agentKey)
    : join(workspace, ".zcode", "agent-memory-local", agentKey);
}
```

| `memory:` scope | Root |
|---|---|
| `user` | `<storageRoot>/agent-memory/<agentKey>` |
| `project` | `<workspaceRoot>/.zcode/agent-memory/<agentKey>` (VCS-shared) |
| `local` | `<workspaceRoot>/.zcode/agent-memory-local/<agentKey>` (machine-local) |

Enablement gate (`persistent-memory.ts:33-37`):

```ts
function isPersistentAgentMemoryEnabled(
  memory: MemoryRuntimeConfig | undefined,
): memory is MemoryRuntimeConfig & { storageRoot: string } {
  return memory?.enabled === true && memory.use !== false && Boolean(memory.storageRoot);
}
```

### 9.2 Tool projection and load

If a profile declares `memory` **and** has an explicit `tools` list, `Write` and `Edit` are appended
(`persistent-memory.ts:10`, `39-47`):

```ts
const PERSISTENT_MEMORY_TOOLS = ["Write", "Edit"] as const;
...
function withPersistentAgentMemoryTools(profile: AgentProfile): AgentProfile {
  if (!profile.memory || profile.tools === undefined) return profile;

  const tools = [...profile.tools];
  for (const tool of PERSISTENT_MEMORY_TOOLS) {
    if (!tools.includes(tool)) tools.push(tool);
  }
  return tools.length === profile.tools.length ? profile : { ...profile, tools };
}
```

`projectPersistentAgentMemoryTools(config)` applies that to `config.subagents.profiles`
(`persistent-memory.ts:49-63`).

`loadPersistentAgentMemory` (`persistent-memory.ts:70-110`) creates the directory, reads `MEMORY.md`
(tolerating absence), and returns `{ rootDir, prompt }`:

```ts
  let indexContent = "";
  try {
    indexContent = (await input.fileSystemPort.readTextFile({ path: join(rootDir, "MEMORY.md") }))
      .content;
  } catch {
    // 不存在或不可读的 MEMORY.md 都使用基线的 empty index 文案。
  }

  return {
    rootDir,
    prompt: buildPersistentAgentMemoryPrompt({
      indexContent,
      rootDir,
      scope: input.profile.memory,
    }),
  };
```

Called from `subagent.ts:131-138` with the parent runtime's `fileSystemPort`, `this.config.memory`,
`request.profile` and `request.workspaceRoot`; the resulting `rootDir` becomes the child's `memoryRoot`
(`subagent.ts:327`).

### 9.3 The prompt

`subagent/persistent-memory-prompt.ts:6-140` is one large `PERSISTENT_AGENT_MEMORY_PROMPT_TEMPLATE`
array; the builder substitutes two placeholders and appends the index
(`persistent-memory-prompt.ts:150-169`):

```ts
export function buildPersistentAgentMemoryPrompt(input: {
  indexContent: string;
  rootDir: string;
  scope: AgentMemoryScope;
}): string {
  const memoryRoot = input.rootDir.endsWith(sep) ? input.rootDir : `${input.rootDir}${sep}`;
  const indexContent = formatMemoryIndexContent(input.indexContent);
  const prompt = PERSISTENT_AGENT_MEMORY_PROMPT_TEMPLATE.replace(
    "<MEMORY_ROOT>/",
    memoryRoot,
  ).replace("<SCOPE_GUIDANCE>", SCOPE_GUIDANCE[input.scope]);
  return [
    prompt,
    "",
    "## MEMORY.md",
    "",
    indexContent ||
      "Your MEMORY.md is currently empty. When you save new memories, they will appear here.",
  ].join("\n");
}
```

Scope guidance lines (`persistent-memory-prompt.ts:142-148`):

```ts
const SCOPE_GUIDANCE: Record<AgentMemoryScope, string> = {
  user: "- Since this memory is user-scope, keep learnings general since they apply across all projects",
  project:
    "- Since this memory is project-scope and shared with your team via version control, tailor your memories to this project",
  local:
    "- Since this memory is local-scope (not checked into version control), tailor your memories to this project and machine",
};
```

The prompt body defines four memory types (`user`, `feedback`, `project`, `reference`), a
"what NOT to save" list, and the two-step save protocol — write a frontmatter file, then add a
one-line pointer to `MEMORY.md` (`persistent-memory-prompt.ts:89-114`). Two hard limits are stated in
the prompt itself:

- `"MEMORY.md` is always loaded into your conversation context — lines after 200 will be truncated, so
  keep the index concise" (`:110`)
- each index entry is one line under ~150 characters (`:108`)

The prompt is injected as part of the child's **agent prompt** section, not as a separate section
(`subagent.ts:141-143`) — so it lands in a `cacheHint: "stable"` system block.

---

## 10. Lifecycle

### 10.1 Cancellation

Per-task abort handle (`runner.ts:612-651`):

```ts
interface SubagentTaskAbortHandle {
  abort(reason?: unknown): void;
  detachParent(): void;
  dispose(): void;
  signal: AbortSignal;
}
```

```ts
function createSubagentTaskAbortController(
  abortControllers: Map<string, AbortController>,
  agentId: string,
  parentSignal?: AbortSignal,
): SubagentTaskAbortHandle {
  const controller = new AbortController();
  abortControllers.set(agentId, controller);
  const onParentAbort = (): void => {
    controller.abort(parentSignal?.reason ?? new Error(`Subagent task aborted: ${agentId}`));
  };
  if (parentSignal?.aborted) {
    onParentAbort();
  } else {
    parentSignal?.addEventListener("abort", onParentAbort, { once: true });
  }

  const detachParent = (): void => {
    parentSignal?.removeEventListener("abort", onParentAbort);
  };
  const dispose = (): void => {
    detachParent();
    if (abortControllers.get(agentId) === controller) {
      abortControllers.delete(agentId);
    }
  };
  ...
```

`detachParent()` is exactly what backgrounding does — a background agent survives the parent turn
(`runner.ts:319`). The map is keyed by `agentId`; `abortControllers` is created once per
`createExploreSubagentPort` call (`runner.ts:133`).

Because a child runtime may never settle after abort, the runner wraps every awaited promise
(`runner.ts:1269-1325`), with the comment at `1277-1278`:

```ts
  // 子运行时或模型适配器在 abort 后可能永不 settle；外层 Agent 必须自己监听
  // 父 signal，否则 `Agent` 工具会一直停在 running，直到用户手动 Stop。
```

A `CoreError` reason is propagated verbatim; anything else becomes
`CoreErrorType.ToolCancelled` / `AgentErrorCode.CHILD_RUNTIME_FAILED` with the message
*"Agent was cancelled before the subagent returned findings or background launch completed"*
(`runner.ts:1289-1312`), matching the tool entry's `cancellation.userVisibleMessage`
(`handlers/agent.ts:271-272`).

Explicit `TaskStop` goes through `port.stopTask` (`runner.ts:547-566`):

```ts
      const task = registry.get(taskId);
      if (!task || task.type !== "local_agent") return task;
      if (isTerminalRuntimeTask(task)) return task;

      const stopped = createBackgroundStoppedTask(registry, task);
      if (!stopped) return undefined;
      return finalizeBackgroundStopped(options, registry, stopped, () => {
        abortControllers
          .get(taskId)
          ?.abort(new Error(`${BACKGROUND_AGENT_STOPPED_STATE.message}: ${taskId}`));
        abortControllers.delete(taskId);
      });
```

with the terminal-state vocabulary at `runner.ts:1655-1661`:

```ts
const BACKGROUND_AGENT_STOPPED_STATE = {
  backgroundEventStatus: "cancelled",
  message: "Background agent task stopped.",
  notificationStatus: "stopped",
  registryStatus: "killed",
  subagentEventStatus: "stopped",
} as const;
```

`finalizeBackgroundStopped` **rolls the registry back and throws** if the notification could not be
enqueued (`runner.ts:1706-1718`):

```ts
  const enqueued = enqueueBackgroundNotification(options, registry, stopped.task.taskId, notification, stopped.traceContext);
  if (!enqueued) {
    registry.register(stopped.previousTask);
    throw new Error(
      `Background agent task stopped notification was not enqueued: ${stopped.task.taskId}`,
    );
  }
```

On the child side, cancellation also cancels the child's *own* background bash tasks and seals its
notifications (`subagent.ts:407-419`).

### 10.2 Runtime task registry

`subagent/runtime-task-registry.ts` is a one-line re-export of `runtime-task/registry.js`.
`InMemoryRuntimeTaskRegistry` (`runtime-task/registry.ts:112-282`) holds
`Map<taskId, RuntimeTaskSnapshot>` plus two waiter sets (terminal / background-request).
Terminal statuses (`registry.ts:103-110`):

```ts
const TERMINAL_STATUSES = new Set<RuntimeTaskSnapshot["status"]>([
  "completed",
  "failed",
  "cancelled",
  "killed",
  "stopped",
  "lost",
]);

export function isTerminalRuntimeTask(task: Pick<RuntimeTaskSnapshot, "status">): boolean {
  return TERMINAL_STATUSES.has(task.status);
}
```

Subagent-specific snapshots are built at `runner.ts:1444-1469` with `taskId === agentId`,
`type`/`taskType` `"local_agent"`, `childSessionId`, `outputFile`, `parentToolCallId`,
`parentSessionId`, `turnId`, `traceContext` (`runner.ts:1464-1467`).

Registry operations used by the subsystem:

- `register` / `update` / `remove` (`registry.ts:118-166`) — every write also wakes waiters.
- `requestBackground(id)` (`registry.ts:145-152`) — refuses terminal tasks, resolves background waiters.
- `queueMessage` / `drainMessages` (`registry.ts:168-184`) — the pending-steering-message buffer.
- `waitForBackgroundRequest` / `waitForTerminal` (`registry.ts:186-230`) — abortable waits.
- `branchGeneration` stamping (`registry.ts:118-126`, `128-130`) — rewind fencing.
- `hasRunningBackgroundRuntimeTask(registry)` (`registry.ts:288-292`) — the parent's "should I defer
  shutdown" predicate.

`RuntimeTaskMessageSink` (`registry.ts:39-41`):

```ts
export interface RuntimeTaskMessageSink {
  send(message: RuntimeTaskPendingMessage): Promise<"queued" | "steered">;
}
```

### 10.3 Sealing background notifications

Sealing exists so a finishing child cannot inject bash notifications into a parent that has already
moved on. `runtime/methods/background-notifications.ts:85-98`:

```ts
export function sealBackgroundTaskNotifications(
  this: AgentRuntimeInternal,
  input: SealBackgroundTaskNotificationsInput,
): void {
  if (this.config.taskType !== "subagent_child") return;
  this.backgroundTaskNotificationsSealed = true;
  this.backgroundTaskNotificationSealReason = input.reason;
  this.logger?.info?.("Subagent runtime background task notifications sealed", {
    ...traceContextToLogContext(input.traceContext ?? this.rootTraceContext),
    event: "runtime.background_task_notifications.sealed",
    module: "core.runtime",
    reason: input.reason,
  });
}
```

Called exactly once per child turn, in the `finally` (`subagent.ts:407-419`):

```ts
      } finally {
        const cancelled = options?.signal?.aborted === true;
        childRuntime.sealBackgroundTaskNotifications({
          reason: cancelled ? "subagent_cancelled" : "subagent_terminal",
          traceContext: request.traceContext,
        });
        if (cancelled) {
          await childRuntime.cancelRunningRuntimeBackgroundTasks({
            reason: "subagent_cancelled",
            traceContext: request.traceContext,
          });
        }
      }
```

Two seal reasons: `"subagent_cancelled"` and `"subagent_terminal"`. Enforcement when a queued
notification is about to be consumed (`background-notifications.ts:220-246`) — only *bash* notifications
from a sealed child are suppressed; agent completions still flow:

```ts
  if (
    !shouldSuppressSealedSubagentBashNotification({
      isSubagentChildRuntime: this.config.taskType === "subagent_child",
      notificationSealed: this.backgroundTaskNotificationsSealed,
      registryTask,
      toolName: command.toolName,
    })
  ) {
    return false;
  }
```

### 10.4 Interaction broker — child questions reaching the user

`subagent/interaction-origin.ts:18-33` (verbatim):

```ts
export function buildSubagentInteractionOrigin(
  context: SubagentInteractionOriginContext,
  childTurnId?: TurnId,
): InteractionRequestOrigin {
  return {
    kind: "subagent",
    agentId: context.agentId,
    agentType: context.agentType,
    childSessionId: context.childSessionId,
    ...(childTurnId ? { childTurnId } : {}),
    description: context.description,
    parentSessionId: context.parentSessionId,
    ...(context.parentToolCallId ? { parentToolCallId: context.parentToolCallId } : {}),
    ...(context.parentTurnId ? { parentTurnId: context.parentTurnId } : {}),
  };
}
```

`runtime/helpers/subagent-interaction-broker.ts:16-41` (verbatim):

```ts
export function createSubagentInteractionBroker(
  parentBroker: PermissionBrokerPort,
  context: SubagentInteractionBrokerContext,
): PermissionBrokerPort {
  return {
    requestPermission(
      request: PermissionBrokerRequest,
      options?: PermissionBrokerRequestOptions,
    ): Promise<PermissionBrokerResult> {
      // 子 agent 的 permission / AskUserQuestion / ExitPlanMode 都需要父 task 的 UI 响应；
      // broker request 对外路由到父 session，origin 保留 child 归属，便于 UI 与日志识别来源。
      //
      // 本包装可以叠加。`sessionId` 由**外层**（离客户端更近的一层）
      // 最后改写，所以任意深度最终都落到根会话；`origin` 反过来保留**内层**已有值，
      // 归属永远是真正发起请求的那个子代理，不会被外层覆盖成中间层。
      return parentBroker.requestPermission(
        {
          ...request,
          sessionId: context.parentSessionId,
          origin: request.origin ?? buildSubagentInteractionOrigin(context, request.turnId),
        },
        options,
      );
    },
  };
}
```

The two-identity model is documented at `runtime/helpers/child-client-ports.ts:5-14` —
*"账本身份 sessionId"* (ledger: event persistence/transcript/trace/session store — child's own) vs
*"路由身份"* (routing: every agent→app reverse request — must be the **root** session, because clients
only know the root session). Derivation is centralised in `deriveChildClientPorts`
(`child-client-ports.ts:42-59`), which also rewrites `providerRuntimeHeadersPort` calls to the parent
session (`child-client-ports.ts:66-78`).

For the subagent, this is invoked at `subagent.ts:208-226` and spread into the child deps at
`subagent.ts:303-307`, with the comment at `303-306`:

```ts
          // 子 runtime 自己仍使用 request.sessionId 做事件持久化和 trace 归档；对外阻塞交互
          // （permission / AskUserQuestion / provider runtime headers）一律路由回父 session——
          // 桌面 UI 只认识父 task 的 sessionId。派生收敛在 deriveChildClientPorts 一处，
          // dwf actor 与 legacy workflow child 走同一条。
```

Belt-and-braces: the same child permission/question events are *also* re-projected as parent-session
events by `mirrorSubagentToolEvent` (see §5.2, the `MIRRORED_INTERACTION_EVENT_TYPES` branch), with the
comment at `tool-event-mirror.ts:61-62`:

```ts
    // V4 只从父 task 的实时投影生成阻塞交互；仅转发 broker request 会让
    // 子 agent 的 permission / AskUserQuestion 停留在子 session，父界面无法响应。
```

### 10.5 Child MCP lifecycle (borrowed, never owned)

`subagent/borrowed-mcp-port.ts:34-69` — the child gets a snapshot-scoped port whose lifecycle methods
throw:

```ts
  const rejectLifecycleMutation = (): never => {
    // child 借用 parent adapter，不拥有 MCP 连接生命周期。
    throw new Error("Subagent MCP port cannot mutate parent connection lifecycle");
  };

  return {
    snapshot,
    port: {
      async callTool(request, options) {
        if (isDenied(request.serverName)) {
          throw new Error(SUBAGENT_COMPUTER_USE_UNAVAILABLE_MESSAGE);
        }
        if (!connectedServerNames.has(request.serverName)) {
          throw new Error(`Subagent MCP server is outside visible scope: ${request.serverName}`);
        }
        return parentPort.callTool(request, options);
      },
      async close() {
        // child 结束时不向 parent adapter 传播 close。
      },
      async connectConfiguredServers() {
        return rejectLifecycleMutation();
      },
      async connectServer() {
        return rejectLifecycleMutation();
      },
      async disconnectServer() {
        return rejectLifecycleMutation();
      },
      async listTools() {
        return [...snapshot.tools];
      },
      async status() {
        return { ...snapshot.statuses };
      },
    },
  };
```

Computer Use is hard-denied inside subagents (`computer-use-policy.ts:9-11`):

```ts
export const SUBAGENT_COMPUTER_USE_UNAVAILABLE_CODE = "SUBAGENT_COMPUTER_USE_UNAVAILABLE" as const;
export const SUBAGENT_COMPUTER_USE_UNAVAILABLE_MESSAGE =
  "Computer Use is not available in subagent" as const;
```

…checked as a preflight (`subagent.ts:149-158`) and again with the live descriptor set
(`subagent.ts:175-180`), and enforced at the skill port (`subagent.ts:744-770`, `834-878`).

### 10.6 Terminal transitions and idempotence

All terminal writers guard against double-finalisation
(`runner.ts:1501-1502`, `1580-1581`, `1667-1668`):

```ts
  const current = registry.get(lifecycle.agentId);
  if (current && isTerminalRuntimeTask(current)) return;
```

Foreground completion also strips runtime-only message state before persisting the terminal snapshot
(`runner.ts:1471-1474`):

```ts
function withoutRuntimeMessageState(task: RuntimeTaskSnapshot): RuntimeTaskSnapshot {
  const { messageSink: _messageSink, pendingMessages: _pendingMessages, ...snapshot } = task;
  return snapshot;
}
```

Terminal side effects, in order — background completion (`runner.ts:1494-1571`):
write artifacts → format notification → `registry.update(status:"completed")` →
`enqueueBackgroundNotification` → `BackgroundTaskCompleted` event → `SubagentStopped` event → log.
Background failure is the same with `selectExecutionErrorMessage(error)` to surface the root cause under
a turn-failure wrapper (`runner.ts:1583-1586`). Foreground completion emits only `SubagentStopped`
(no notification, because the result is already the tool return value) (`runner.ts:339-379`).

---

## Appendix A — Event and identifier vocabulary

| Thing | Value / shape | Source |
|---|---|---|
| agent id | `agent_${crypto.randomUUID()}` | `runner.ts:805` |
| child session id | `createSessionId(\`subagent_${agentId}\`)` | `runner.ts:806` |
| mirrored tool call id | `tool_subagent_${agentId}_${childToolCallId}` | `tool-event-mirror.ts:132-134` |
| event `source` | `"subagent"` | `tool-event-mirror.ts:17` |
| runtime task type | `"local_agent"` | `runner.ts:1464-1466` |
| child runtime `taskType` | `"subagent_child"` | `subagent.ts:271` |
| queue mode (notification) | `"task-notification"`, source `"background_task"` | `command-queue.ts:65-73` |
| queue mode (reply) | `"subagent-message"`, source `"subagent_message"` | `command-queue.ts:75-88` |
| queue priority | `"next"` (= rank 1 of now/next/later) | `command-queue.ts:131-135` |
| notification `originMeta.backgroundSource` | `"subagent"` | `runner.ts:1861-1865` |
| interruption origin `kind` | `"subagent"` | `interaction-origin.ts:23` |
| error codes | `agent_subagent_unavailable`, `agent_background_unavailable`, `agent_unknown_type`, `agent_child_runtime_failed` | `contracts/src/tools/agent.ts:131-136` |

## Appendix B — Things that surprised me while reading

1. **`index.ts` does not export everything.** `subagent/index.ts:5-12` exports explore, context-builder,
   system-prompt, general-purpose, profile, explore-tools, runner, runtime-task-registry — but *not*
   `profile-frontmatter`, `persistent-memory`, `computer-use-policy`, `mcp-config`, `tool-policy`,
   `message-steering`, `coordinator-response`, `completion-notification`, `interaction-origin`,
   `tool-event-mirror`, `borrowed-mcp-port`. Consumers import those by deep path.
2. **`Explore` is not merely a prompt.** It gets a *fresh* `PermissionService(defaultPermissionConfig)`
   and defaults to `permissionMode: yolo` when the profile specifies nothing, whereas general-purpose and
   custom profiles inherit the parent's permission service and mode (`subagent.ts:315-318`, `473-488`).
   The read-only guarantee is prompt + tool allowlist + separate permission service, three layers.
3. **The `run(prompt, ...)` argument order in `executeTurn`** is
   `executeTurn(request.prompt, undefined, { inputSource: "subagent", inputPresentation: "coordinator_input" })`
   — the second positional argument is explicitly `undefined`.
4. **`TaskOutput` on a `local_agent` is discouraged, not blocked**, and reading it sets `notified` so the
   push notification is suppressed (`handlers/task-output.ts:253-257`).
5. **A background notification is a *user* message**, added with `messageHistory.addUser` — but with
   `visibility: "model-only"`, so it is a synthetic user notice, not user speech
   (`background-notifications.ts:178-191`).
6. **`maxTurns` defaults to 4**, not unlimited (`subagent.ts:269`).
