# 06 — PERMISSION and HOOKS subsystems (ZCode core)

**Source root for every citation in this file:** `/Users/eric/projects/ZCode`. Paths are quoted relative to that root (e.g. `apps/zcode-cli/packages/core/src/permission/service.ts:60`).

**What was read in full for this map**

| Area | Files read end-to-end |
|---|---|
| Permission | `core/src/permission/{service,broker,rule-matching,plan-mode-policy,workflow-draft-path,index}.ts`; `core/src/runtime/{permission-full-access,permission-grant-recovery}.ts`; `core/src/runtime/helpers/{permission-broker,permission-grant-resume,tool-allowlist}.ts`; `core/src/tool/executor/{permission-flow,permission-input-recheck,permission-rules,permission-rules-persistence,permission-capability,permission-suggestions,permission-responder-race,approval-gate,hook-flow,errors}.ts`; `contracts/src/interfaces/{permission.port,permission-full-access}.ts`; `adapters/src/storage/session-store/repositories/local-settings.ts`; `adapters/src/storage/session-store/paths.ts`; `bootstrap/src/zcode-protocol/interaction-broker.ts` |
| Hooks | every file in `core/src/hooks/` (`runner,types,configured-runner,configured-runner-input,configured-runner-callback,runner-helpers,output,workspace-hook-runtime-admission,workspace-hook-review-flow,workspace-hook-trust-coordinator,workspace-hook-trust-evaluation,workspace-hook-policy,workspace-hook-telemetry,workspace-hook-trust-records,workspace-hook-trust-types,workspace-hook-trust-domain,session-mailbox,display-metadata,index`); `core/src/runtime/internal-hook-methods.ts`; `core/src/runtime/methods/hooks.ts`; `contracts/src/hooks/{index,workspace-hook-trust,workspace-hook-trust-store}.ts`; `packages/shared/src/{workspace-hook-config,workspace-hook-trust-store-file}.ts`; `adapters/src/config/{schema,config-merger,project-config.adapter,config-factory}.ts` (relevant regions); `adapters/src/plugins/hook-sources.ts`; `bootstrap/src/app/runtime-config.ts` |

Two grep-only areas are flagged as such where they appear. Where a behaviour is *not* implemented (dead field, unconsumed schema key, unreachable branch) it is called out explicitly in §C.

---

# Part A — PERMISSIONS

## A1. Permission MODES

### A1.1 The mode union (verbatim)

**`apps/zcode-cli/packages/contracts/src/interfaces/session.port.ts:32`**
```ts
export type CollaborationMode = "plan" | "build" | "edit" | "yolo" | "auto";
```

Same five literals are re-declared in the config-file schema, independently of the contract union:

**`apps/zcode-cli/packages/adapters/src/config/schema.ts:13-19`**
```ts
const permissionSchema = z.object({
  mode: z.enum(["plan", "build", "edit", "yolo", "auto"]).optional(),
  allowedTools: z.array(z.string()).optional(),
  disallowedTools: z.array(z.string()).optional(),
  autoApproveHighRisk: z.boolean().optional(),
  allowMediumRiskInAuto: z.boolean().optional(),
});
```

The mode is *persisted per project* in the settings store (§A3.2), and the union is validated on read by `isCollaborationMode` (`adapters/src/storage/session-store/repositories/local-settings.ts:65`).

### A1.2 The decision enum produced by `PermissionService`

**`apps/zcode-cli/packages/core/src/permission/service.ts:60`**
```ts
export type PermissionBehavior = "allow" | "ask" | "deny";
```

**`apps/zcode-cli/packages/core/src/permission/service.ts:62-77`**
```ts
export interface PermissionDecisionResult {
  decision: PermissionBehavior;
  allowed: boolean;
  reason?: string;
  modifiedInput?: unknown;
  escalated: boolean;
  mode: CollaborationMode;
  ruleId: string;
  riskLevel: RiskLevel;
  sideEffectScope?: ModelToolSideEffectScope;
  /**
   * 该 ask 来自工具的 alwaysAsk 声明，不是模式或规则推导出来的。下游（PreToolUse hook 的
   * allow 覆盖）靠这个结构化标记识别"不可抹掉的确认"，而不是去匹配 ruleId 字符串。
   */
  alwaysAsk?: boolean;
}
```

A **second, wider** decision enum exists on the broker/event boundary (it adds `escalate` and `modify`), quoted in §A4.2.

### A1.3 What each mode permits — the branch order in `checkPermission`

The whole traversal is a single ordered function; the *order is the policy*. Signature and head:

**`apps/zcode-cli/packages/core/src/permission/service.ts:97-102`**
```ts
  checkPermission(
    context: PermissionContext,
    toolCapability?: PermissionToolCapability,
    projectRules?: PermissionRuleset | null,
    rulePolicy?: ToolPermissionRulePolicy,
  ): PermissionDecisionResult {
```

Ordered gates (line numbers are the gate's `if`/`return` in `service.ts`):

| # | Gate | Outcome |
|---|---|---|
| 1 | `resolvePlanModeTransitionPermission(context)` — 104-110 | EnterPlanMode → **allow** `tool.plan.enter`; ExitPlanMode outside plan → **deny** `mode.plan.exitOnly` |
| 2 | `capability.requiresUserInteraction` — 112-128 | `disallowedTools` → **deny** `rule.disallowedTools`, else **ask** `tool.userInteraction` |
| 3 | `capability.alwaysAsk` — 131-133 | delegates to `checkAlwaysAsk` (§A1.5) |
| 4 | `mode === "yolo" && !planEnabled` — 136-138 | **allow** `mode.yolo` |
| 5 | `mode === "auto"` — 140-147 | **deny** `mode.auto.unimplemented` |
| 6 | `disallowedTools` — 149-156 | **deny** `rule.disallowedTools` |
| 7 | project `deny` rules — 158-165 | **deny** `rule.project.deny` |
| 8 | project `ask` rules — 167-174 | **ask** `rule.project.ask` |
| 9 | `planEnabled` — 176-178 | `checkPlanMode` (§A1.4) |
| 10 | project `allow` rules — 180-187 | **allow** `rule.project.allow` |
| 11 | `isPreapprovedWebFetchRequest` — 189-196 | **allow** `tool.webfetch.preapproved` |
| 12 | `isPreapprovedWorkflowDraftWrite` — 202-215 | **allow** `tool.workflowDraft.preapproved` |
| 13 | `config.allowedTools` — 217-224 | **allow** `rule.allowedTools` |
| 14 | `mode === "edit"` — 226-228 | `checkEditMode` |
| 15 | fallback — 230 | `checkBuildMode` |

**yolo** (verbatim, `service.ts:136-138`):
```ts
    if (context.mode === "yolo" && !planEnabled) {
      return this.allow(context, capability, "mode.yolo", "Yolo mode bypasses permission prompts");
    }
```
It bypasses *everything except* gates 1–3: an explicit plan transition, a `requiresUserInteraction` tool, and a tool's self-declared `alwaysAsk` still gate. Note it also precedes `disallowedTools` (gate 6) — a documented, deliberate asymmetry (`service.ts:330-331`: "尤其 yolo 目前先于 disallowedTools 放行这一点").

**auto** is reserved but unimplemented (verbatim, `service.ts:140-147`):
```ts
    if (context.mode === "auto") {
      return this.deny(
        context,
        capability,
        "mode.auto.unimplemented",
        "Auto mode is reserved but not implemented yet",
      );
    }
```
The config field `allowMediumRiskInAutoMode` exists (`service.ts:679`) and is defaulted (`service.ts:686`) but is **never read** anywhere — see §C.

**build** (verbatim, `service.ts:450-514`):
```ts
  private checkBuildMode(
    context: PermissionContext,
    capability: ResolvedPermissionCapability,
  ): PermissionDecisionResult {
    if (capability.readOnly && !capability.destructive && !capability.needsApproval) {
      return this.allow(
        context,
        capability,
        "mode.build.readOnly",
        "Build mode allows read-only tools",
      );
    }

    if (capability.riskLevel === "critical") {
      return this.ask(
        context,
        capability,
        "mode.build.criticalRisk",
        "Critical risk tools require explicit approval",
      );
    }

    if (capability.riskLevel === "high" && !this.config.autoApproveHighRisk) {
      return this.ask(
        context,
        capability,
        "mode.build.highRisk",
        "High risk tools require explicit approval",
      );
    }

    if (
      capability.sideEffectScope === "session" &&
      capability.riskLevel === "low" &&
      !capability.destructive &&
      !capability.needsApproval
    ) {
      return this.allow(
        context,
        capability,
        "mode.build.sessionState",
        "Build mode allows low-risk session-local state updates",
      );
    }

    if (
      capability.needsApproval ||
      capability.destructive ||
      capability.sideEffectScope !== "none"
    ) {
      return this.ask(
        context,
        capability,
        "mode.build.sideEffect",
        "Tool has side effects and requires approval",
      );
    }

    return this.allow(
      context,
      capability,
      "mode.build.lowRisk",
      "Build mode allows low-risk tool execution",
    );
  }
```

**edit** narrows build by exactly one branch (verbatim, `service.ts:516-530`):
```ts
  private checkEditMode(
    context: PermissionContext,
    capability: ResolvedPermissionCapability,
  ): PermissionDecisionResult {
    if (capability.permissionName === "edit" && capability.sideEffectScope === "workspace") {
      return this.allow(
        context,
        capability,
        "mode.edit.fileEdit",
        "Edit mode allows file edit tools",
      );
    }

    return this.checkBuildMode(context, capability);
  }
```

**Capability resolution** (what feeds the branches above) — `service.ts:581-608`:
```ts
  private resolveCapability(
    context: PermissionContext,
    toolCapability?: PermissionToolCapability,
  ): ResolvedPermissionCapability {
    return {
      allowedInPlanMode: toolCapability?.allowedInPlanMode ?? false,
      alwaysAsk: toolCapability?.permission?.alwaysAsk ?? toolCapability?.alwaysAsk ?? false,
      readOnly: toolCapability?.readOnly ?? this.isReadOnlyTool(context.toolName),
      destructive: toolCapability?.destructive ?? this.isDestructiveTool(context.toolName),
      requiresUserInteraction:
        toolCapability?.requiresUserInteraction ??
        (toolCapability?.permission?.sideEffectScope ?? toolCapability?.sideEffectScope) ===
          "userInteraction",
      sideEffectScope:
        toolCapability?.permission?.sideEffectScope ??
        toolCapability?.sideEffectScope ??
        (this.isReadOnlyTool(context.toolName) ? "none" : "workspace"),
      riskLevel:
        toolCapability?.permission?.riskLevel ??
        this.getRiskLevel(context.toolName, toolCapability),
      needsApproval:
        toolCapability?.permission?.needsApproval ??
        toolCapability?.needsApproval ??
        !this.isReadOnlyTool(context.toolName),
      permissionCapabilityGroup: toolCapability?.permissionCapabilityGroup,
      permissionName: toolCapability?.permission?.permission,
    };
  }
```
and the name-based fallbacks (`service.ts:537-579`) use three hard-coded sets: read-only = `Read, Glob, Grep, WebSearch, WebFetch, TodoRead, TodoWrite, AskUserQuestion, Agent, Task, Skill`; write = `Write, Edit, ApplyPatch, Bash`; destructive = `Bash`. Because `getRiskLevel` checks write-tools before destructive-tools (`service.ts:546-552`), **`Bash` falls back to `riskLevel: "medium"`, and `isDestructiveTool` is unreachable in the fallback path** — see §C.

The scope union used by the plan-mode/build-mode branches:

**`apps/zcode-cli/packages/contracts/src/model/index.ts:453-460`**
```ts
export type ModelToolSideEffectScope =
  | "none"
  | "workspace"
  | "git"
  | "network"
  | "system"
  | "session"
  | "userInteraction";
```

**`apps/zcode-cli/packages/contracts/src/interfaces/session.port.ts:34`**
```ts
export type RiskLevel = "low" | "medium" | "high" | "critical";
```

### A1.4 plan mode

§A6 below (own section, per the brief).

### A1.5 `alwaysAsk` — the gate no mode can bypass

**`apps/zcode-cli/packages/core/src/permission/service.ts:333-392`**
```ts
  private checkAlwaysAsk(
    context: PermissionContext,
    capability: ResolvedPermissionCapability,
    projectRules?: PermissionRuleset | null,
    rulePolicy?: ToolPermissionRulePolicy,
  ): PermissionDecisionResult {
    if (context.mode === "auto") {
      return this.deny(
        context,
        capability,
        "mode.auto.unimplemented",
        "Auto mode is reserved but not implemented yet",
      );
    }
    if (this.config.disallowedTools.has(context.toolName)) {
      return this.deny(
        context,
        capability,
        "rule.disallowedTools",
        `Tool ${context.toolName} is explicitly disallowed`,
      );
    }
    if (this.matchesProjectRules(projectRules, "deny", context, capability, rulePolicy)) {
      return this.deny(
        context,
        capability,
        "rule.project.deny",
        `Tool ${context.toolName} is denied by project permission rules`,
      );
    }
    // 会话免确认：阻断分支之后、ask 之前。命中即放行，不发 permission 事件、不弹窗；
    // 与 gate 本身一样不看模式（yolo / plan / build 一致）。
    if (this.matchesProjectRules(this.sessionRules, "allow", context, capability, rulePolicy)) {
      return this.allow(
        context,
        capability,
        "rule.session.allow",
        `Tool ${context.toolName} was allowed for this session`,
      );
    }
    // 修订免确认：AmendWorkflow 的前驱是
    // **本会话发起**的 run、且不是用户亲手停下的，即放行。与会话规则同位——阻断分支之后、ask 之前，
    // 不看模式。事实来自 resolveInput 回填的 `predecessor`（journal 的 parent_session_id / stopReason），
    // 不是内存表：重启、冷恢复后依然成立，也没有可播种、可撤销的东西。别的会话的 run、用户停过的
    // run 照常 ask：钥匙是 run 的归属，不是字段的在场。
    if (this.isOwnedWorkflowAmend(context)) {
      return this.allow(
        context,
        capability,
        "rule.session.workflowOwner",
        `Tool ${context.toolName} amends a run this session started`,
      );
    }
    return this.ask(
      context,
      capability,
      "tool.alwaysAsk",
      `Tool ${context.toolName} always requires explicit approval`,
    );
  }
```
The `alwaysAsk` contract field itself:

**`apps/zcode-cli/packages/contracts/src/tools/contract.ts:96-105`**
```ts
  /**
   * Requires explicit user approval in every permission mode. Unlike `needsApproval`, which
   * the permissive modes are allowed to short-circuit, this survives yolo's pass-through and
   * plan mode's read-only pass-through: the tool's unit of work is large enough that no
   * permissiveness setting should be able to run it unattended.
   *
   * It overrides allow-granting branches only, never deny-granting ones — an explicitly
   * disallowed tool stays denied.
   */
  alwaysAsk?: true;
```

`alwaysAsk` is surfaced structurally on every ask result (`service.ts:653`: `...(capability.alwaysAsk ? { alwaysAsk: true } : {})`) and is what stops a PreToolUse `allow` from erasing the gate (§B9).

---

## A2. RULE TYPES and the matching function

### A2.1 Rule types (verbatim)

**`apps/zcode-cli/packages/contracts/src/interfaces/permission.port.ts:10-37`**
```ts
export type PermissionRuleBehavior = "allow" | "deny" | "ask";

export const PermissionCapabilityGroup = {
  OfficialCua: "official_cua",
} as const;

export type PermissionCapabilityGroup =
  (typeof PermissionCapabilityGroup)[keyof typeof PermissionCapabilityGroup];

export interface PermissionRuleValue {
  toolName: string;
  ruleContent?: string;
}

export interface PermissionRuleset {
  version?: 1;
  allow?: PermissionRuleValue[];
  deny?: PermissionRuleValue[];
  ask?: PermissionRuleValue[];
  mode?: CollaborationMode;
  [key: string]: unknown;
}

export type PermissionUpdate = {
  type: "addRules";
  behavior: PermissionRuleBehavior;
  rules: PermissionRuleValue[];
};
```

So a rule is a **tool-name key + optional content pattern**; behavior is which array it sits in (`allow` / `deny` / `ask`); the *scope* is which `PermissionRuleset` instance is handed to `checkPermission` (project rules from the store vs. `sessionRules` in memory vs. the per-tool `rulePolicy` — §A3). A second, tool-specific rule shape exists for shell commands:

**`apps/zcode-cli/packages/contracts/src/tools/bash.ts:329-336`**
```ts
export type ShellPermissionRule = {
  ...
  rules: ShellPermissionRule[];
};
```
(grep-only citation: read via `grep -n` while locating `PermissionRule*`; only the declaration lines are quoted, not the body.)

The per-tool rule hook (how a tool overrides evaluation of *its own* rules):

**`apps/zcode-cli/packages/core/src/tool/types.ts:369-375`**
```ts
export interface ToolPermissionRulePolicy {
  evaluateRules: (
    behavior: PermissionRuleBehavior,
    rules: readonly PermissionRuleValue[],
  ) => boolean;
  suggestedPermissionUpdates: PermissionUpdate[];
}
```

### A2.2 Scope matching — `matchesProjectRules` / `matchesRuleScope`

**`apps/zcode-cli/packages/core/src/permission/service.ts:233-281`**
```ts
  private matchesProjectRules(
    ruleset: PermissionRuleset | null | undefined,
    behavior: PermissionBehavior,
    context: PermissionContext,
    capability: ResolvedPermissionCapability,
    rulePolicy?: ToolPermissionRulePolicy,
  ): boolean {
    const rules = ruleset?.[behavior];
    if (!Array.isArray(rules)) return false;
    const toolRules = rules.filter((rule) =>
      this.matchesRuleScope(rule, context.toolName, capability),
    );
    if (toolRules.length === 0) return false;
    if (rulePolicy) return rulePolicy.evaluateRules(behavior, toolRules);
    return toolRules.some((rule) => this.matchesRule(rule, context, capability));
  }

  private matchesRule(
    rule: PermissionRuleValue,
    context: PermissionContext,
    capability: ResolvedPermissionCapability,
  ): boolean {
    if (!this.matchesRuleScope(rule, context.toolName, capability)) return false;
    if (!rule.ruleContent) return true;

    const subjects = this.ruleSubjects(context.input, context.toolName);
    if (subjects.length === 0) return false;

    return subjects.some((subject) => this.matchesRuleContent(subject, rule.ruleContent!));
  }

  private matchesRuleToolName(ruleToolName: string, contextToolName: string): boolean {
    if (ruleToolName === contextToolName) return true;
    return contextToolName === "Write" && ruleToolName === "Edit";
  }

  private matchesRuleScope(
    rule: PermissionRuleValue,
    contextToolName: string,
    capability: ResolvedPermissionCapability,
  ): boolean {
    if (rule.toolName === OFFICIAL_CUA_PERMISSION_RULE_TOOL_NAME) {
      // 保留 key 只有在当前 tool entry 另行携带宿主验证后的 official_cua
      // capability 时才匹配。同名第三方 MCP、authority 漂移以及旧普通 tool
      // 都不能把可解析的 wire/storage 字符串升级成可信能力。
      return capability.permissionCapabilityGroup === PermissionCapabilityGroup.OfficialCua;
    }
    return this.matchesRuleToolName(rule.toolName, contextToolName);
  }
```

Note the three deliberate asymmetries: (a) a rule with **no** `ruleContent` matches the tool unconditionally (`if (!rule.ruleContent) return true;`); (b) `Edit` rules also match `Write` calls (one-way alias); (c) an `official_cua` rule key only becomes live when the tool entry separately carries the host-verified capability group.

### A2.3 Subject extraction and content patterns

**`apps/zcode-cli/packages/core/src/permission/service.ts:283-320`**
```ts
  private ruleSubjects(input: unknown, toolName: string): string[] {
    if (typeof input === "string") return [input];
    if (!input || typeof input !== "object") return [];

    const record = input as Record<string, unknown>;
    if (toolName === "WebFetch" && typeof record.url === "string") {
      return webFetchRuleSubjects(record.url);
    }

    for (const key of ["command", "url", "file_path", "path", "pattern", "patch_text"]) {
      const value = record[key];
      if (typeof value === "string") return [value];
    }

    return [];
  }
  ...
  private matchesRuleContent(subject: string, ruleContent: string): boolean {
    if (ruleContent.endsWith(":*")) {
      const prefix = ruleContent.slice(0, -2);
      return (
        subject === prefix || subject.startsWith(`${prefix} `) || subject.startsWith(`${prefix}\t`)
      );
    }

    if (ruleContent.includes("*")) {
      return wildcardToRegExp(ruleContent).test(subject);
    }

    return subject === ruleContent;
  }
```

WebFetch rules match on a synthesized `domain:<host>` subject, not the raw URL:

**`apps/zcode-cli/packages/core/src/permission/rule-matching.ts` (entire file, 22 lines)**
```ts
export function wildcardToRegExp(pattern: string): RegExp {
  const escaped = pattern
    .split("*")
    .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join(".*");
  return new RegExp(`^${escaped}$`);
}

function domainRuleSubject(url: string): string | undefined {
  try {
    const parsed = new URL(url.trim());
    const hostname = parsed.hostname.toLowerCase().replace(/\.$/, "");
    return hostname.length > 0 ? `domain:${hostname}` : undefined;
  } catch {
    return undefined;
  }
}

export function webFetchRuleSubjects(url: string): string[] {
  const domain = domainRuleSubject(url);
  return domain ? [domain] : [];
}
```

Default suggestion builder used to populate the approval dialog's "always allow" rule:

**`apps/zcode-cli/packages/core/src/tool/executor/permission-suggestions.ts:5-40`** (constant `PROJECT_RULE_INPUT_KEYS = ["command", "url", "file_path", "path", "pattern"]`, and for capability-grouped tools a single `{ toolName: OFFICIAL_CUA_PERMISSION_RULE_TOOL_NAME }` rule).

### A2.4 The allow/deny dataflow helper

**`apps/zcode-cli/packages/core/src/tool/executor/permission-rules.ts:3-22`**
```ts
export function applyPermissionUpdates(
  current: PermissionRuleset,
  updates: PermissionUpdate[],
): PermissionRuleset {
  let next: PermissionRuleset = {
    ...current,
    version: 1,
  };

  for (const update of updates) {
    if (update.type !== "addRules") continue;
    const existing = getPermissionRules(next, update.behavior);
    next = {
      ...next,
      [update.behavior]: dedupePermissionRules([...existing, ...update.rules]),
    };
  }

  return next;
}
```
Dedupe key is `` `${rule.toolName}\u0000${rule.ruleContent ?? ""}` `` (`permission-rules.ts:39`).

---

## A3. Where rules are PERSISTED

There are **four** rule layers, three of them quoted below plus the config-file layer.

### A3.1 Project rules — SQLite `local_setting` (durable)

**`apps/zcode-cli/packages/adapters/src/storage/session-store/repositories/local-settings.ts:7-50`**
```ts
export async function getProjectPermission(
  db: DatabaseSync,
  projectID: ProjectId,
): Promise<PermissionRuleset | null> {
  const setting = readLocalSetting(db, {
    key: "ruleset",
    namespace: "permission",
    scope: "project",
    scopeID: projectID,
  });
  if (setting) {
    return decodeJson<PermissionRuleset>(setting.value) ?? null;
  }

  const row = db.prepare("select * from permission where project_id = ?").get(projectID) as
    | PermissionRow
    | undefined;
  return row ? (decodeJson<PermissionRuleset>(row.data) ?? null) : null;
}

export async function saveProjectPermission(
  db: DatabaseSync,
  input: {
    projectID: ProjectId;
    permission: PermissionRuleset;
  },
): Promise<PermissionRuleset> {
  const now = Date.now();
  writeLocalSetting(db, {
    key: "ruleset",
    namespace: "permission",
    schemaVersion: 1,
    scope: "project",
    scopeID: input.projectID,
    time: now,
    value: JSON.stringify(input.permission),
  });

  const saved = await getProjectPermission(db, input.projectID);
  if (!saved) {
    throw new Error(`Project permission not found after write: ${input.projectID}`);
  }
  return saved;
}
```
with the upsert (`local-settings.ts:119-138`):
```sql
      insert into local_setting (
        scope, scope_id, namespace, key, value, schema_version, time_created, time_updated
      ) values (?, ?, ?, ?, ?, ?, ?, ?)
      on conflict(scope, scope_id, namespace, key) do update set
        value = excluded.value,
        schema_version = excluded.schema_version,
        time_updated = excluded.time_updated
```

**The on-disk file** — there is no per-project JSON settings file for rules; it is the CLI's single SQLite database:

**`apps/zcode-cli/packages/adapters/src/storage/session-store/paths.ts:6-8`**
```ts
export function getDefaultSessionDbPath(): string {
  return join(homedir(), ".zcode", "cli", "db", "db.sqlite");
}
```
(overridable through `storage.sessionDbPath` / `storage.dir` in the config file, `adapters/src/config/schema.ts:21-24`).

Read/write wiring into the decision path:

**`apps/zcode-cli/packages/core/src/tool/executor/permission-rules-persistence.ts:14-21, 51-53`**
```ts
export async function loadProjectPermissionRuleset(
  deps: ToolExecutorDeps,
): Promise<PermissionRuleset | null> {
  if (!deps.sessionStore) return null;
  const projectId = await resolveProjectId(deps);
  if (!projectId) return null;
  return deps.sessionStore.getProjectPermission(projectId);
}
...
  const current = (await deps.sessionStore.getProjectPermission(projectID)) ?? { version: 1 };
  const next = applyPermissionUpdates(current, updates);
  await deps.sessionStore.saveProjectPermission({ projectID, permission: next });
```
Project id comes from the session row: `` const session = await deps.sessionStore?.getSession(deps.sessionId); return session?.projectID; `` (`permission-rules-persistence.ts:64-67`).

**Port declarations** (verbatim):

**`apps/zcode-cli/packages/contracts/src/interfaces/session-store.port.ts:1250-1255`**
```ts
  getProjectPermission(projectID: ProjectId): Promise<PermissionRuleset | null>;
  saveProjectPermission(input: {
    projectID: ProjectId;
    permission: PermissionRuleset;
  }): Promise<PermissionRuleset>;
```

### A3.2 Permission MODE — same table, different key

**`apps/zcode-cli/packages/adapters/src/storage/session-store/repositories/local-settings.ts:52-86`**
```ts
export function getProjectPermissionMode(
  db: DatabaseSync,
  projectID: ProjectId,
): CollaborationMode | null {
  const setting = readLocalSetting(db, {
    key: "mode",
    namespace: "permission",
    scope: "project",
    scopeID: projectID,
  });
  if (!setting) return null;

  const value = decodeJson<{ mode?: unknown }>(setting.value);
  return isCollaborationMode(value?.mode) ? value.mode : null;
}

export function saveProjectPermissionMode(
  db: DatabaseSync,
  input: {
    mode: CollaborationMode;
    projectID: ProjectId;
  },
): CollaborationMode {
  const now = Date.now();
  writeLocalSetting(db, {
    key: "mode",
    namespace: "permission",
    schemaVersion: 1,
    scope: "project",
    scopeID: input.projectID,
    time: now,
    value: JSON.stringify({ mode: input.mode }),
  });
  return input.mode;
}
```

Port (**`session-store.port.ts:1080-1087`**):
```ts
  getProjectPermissionMode(
    projectID: ProjectId,
  ): CollaborationMode | null | Promise<CollaborationMode | null>;
  saveProjectPermissionMode(input: {
    mode: CollaborationMode;
    projectID: ProjectId;
  }): CollaborationMode | Promise<CollaborationMode>;
```
Write site on every mode change (**`bootstrap/src/app/session-facade.ts:422-437`**):
```ts
    setMode: async (mode: CollaborationMode) => {
      const previousMode = deps.runtime.getMode();
      await deps.runtime.setExecutionState({ mode }, deps.traceContext);
      if (deps.localSettingStore) {
        try {
          await deps.localSettingStore.saveProjectPermissionMode({
            mode: deps.runtime.getMode(),
            projectID: deps.projectID,
          });
```
Read site on startup (`bootstrap/src/app/session-store.ts:43-46`):
```ts
  const mode = store.getProjectPermissionMode(projectID);
  return isPromiseLike(mode) ? undefined : (mode ?? undefined);
```

### A3.3 Session rules — in-memory only, dies with the runtime

**`apps/zcode-cli/packages/core/src/permission/service.ts:84-95`**
```ts
  /**
   * 会话级 allow 规则（「Always allow in this session」）。
   * 一个实例 = 一个 app = 一个会话，所以"随会话消亡"不需要任何额外机制：重启 / 冷恢复 / `/new`
   * 都会造一个空的新实例。只服务 alwaysAsk gate（见 checkAlwaysAsk），普通工具的模式语义不认它。
   */
  private sessionRules: PermissionRuleset = { version: 1 };

  constructor(private config: PermissionConfig = defaultPermissionConfig) {}

  grantSessionPermission(updates: PermissionUpdate[]): void {
    this.sessionRules = applyPermissionUpdates(this.sessionRules, updates);
  }
```
Applied at the answer boundary, and deliberately **not** persisted with `permissionUpdates` (`core/src/tool/executor/permission-flow.ts:390-404`):
```ts
  if (resolvedPermission.sessionPermissionUpdates?.length) {
    // 会话免确认：只进内存里的会话 ruleset，
    // 与上面的项目级持久化互不可见。
    deps.permissionService.grantSessionPermission(resolvedPermission.sessionPermissionUpdates);
```
Broker contract for that channel (**`contracts/src/interfaces/permission.port.ts:79-84`**):
```ts
  /**
   * Session-scoped grants ("Always allow in this session"). Applied to the runtime's
   * in-memory session ruleset only; never persisted with `permissionUpdates`. Synthesized
   * by the broker on the answer side because the wire option schema is strict.
   */
  sessionPermissionUpdates?: PermissionUpdate[];
```

### A3.4 The settings-JSON `permission` block

`~/.zcode/cli/config.json` (user) and `<workspace>/zcode.json` / `<workspace>/.zcode/config.json` (project) carry `permission.*`, validated by `permissionSchema` (§A1.1) and copied into the runtime patch verbatim (`adapters/src/config/schema.ts:401-402`):
```ts
  if (parsed.permission) config.permission = parsed.permission;
```
It is turned into the live `PermissionConfig` **once per app** (`bootstrap/src/app/create-app.ts:342-347`):
```ts
    const permissionService = new PermissionService({
      allowedTools: new Set(configResult.config.permission.allowedTools),
      autoApproveHighRisk: configResult.config.permission.autoApproveHighRisk,
      disallowedTools: new Set(configResult.config.permission.disallowedTools),
      allowMediumRiskInAutoMode: configResult.config.permission.allowMediumRiskInAuto,
    });
```
Config-file locations & precedence: `~/.zcode/cli/config.json` is `DEFAULT_BASE_DIR` + `config.json` (`adapters/src/config/file-config.adapter.ts:62-63, 79-88`), project discovery is `<dir>/zcode.json`, `<dir>/.zcode/config.json` walking up to the git-worktree root (`packages/shared/src/workspace-hook-config.ts:174-179, 335-357`), priority order documented at `adapters/src/config/config-factory.ts:120-127`.

Defaults (**`service.ts:675-687`**):
```ts
export interface PermissionConfig {
  allowedTools: Set<string>;
  disallowedTools: Set<string>;
  autoApproveHighRisk: boolean;
  allowMediumRiskInAutoMode: boolean;
}

export const defaultPermissionConfig: PermissionConfig = {
  allowedTools: new Set(),
  disallowedTools: new Set(),
  autoApproveHighRisk: false,
  allowMediumRiskInAutoMode: false,
};
```

### A3.5 Per-session ruleset column (present in the contract, unused by the check path)

**`apps/zcode-cli/packages/contracts/src/interfaces/session-store.port.ts:164`** and `:189`
```ts
  permission?: PermissionRuleset;
```
This field exists on `SessionInfo` / `CreateSessionInput` (and `:264` on the stable-fork metadata), but `checkPermission` is fed only `loadProjectPermissionRuleset(...)` + `PermissionService.sessionRules` (§A4.1), so the session-row copy is not part of the live decision path in core.

---

## A4. End-to-end decision path

### A4.1 Who calls `PermissionService`

Exactly two call sites exist in `core` (grep: `checkPermission` in `apps/zcode-cli/packages/core/src`):

1. **Primary** — `resolveToolPermission`, invoked from the tool executor's call runner:

   **`apps/zcode-cli/packages/core/src/tool/executor/call-runner.ts:290-300`**
   ```ts
     const permissionResult = await resolveToolPermission(
       deps,
       canonicalToolCall,
       entry,
       executionInput,
       preToolHookResult,
       mode,
       traceContext,
       options?.signal,
       telemetry,
     );
   ```
2. **Re-check after a hook rewrote the input** — `recheckPermissionHookModifiedInput` (see §A4.5).

Context construction + the call itself:

**`apps/zcode-cli/packages/core/src/tool/executor/permission-flow.ts:52-101`**
```ts
  const permissionContext: PermissionContext = {
    toolName: toolCall.name,
    input: executionInput,
    riskLevel: entry.metadata.riskLevel,
    mode,
    prePlanMode: deps.sessionModePort?.getPrePlanMode(),
    planEnabled: deps.sessionModePort?.isPlanEnabled?.(),
    // workflow 草稿免确认要按工作目录解析相对路径，见 PermissionService 的
    // isPreapprovedWorkflowDraftWrite。
    workingDirectory: deps.getWorkingDirectory(),
  };
  const runtimePermissionContext = resolveRuntimePermissionContext(deps);
  const rulePolicy = entry.resolvePermissionRulePolicy?.(executionInput, runtimePermissionContext);
  const suggestedPermissionUpdates =
    rulePolicy?.suggestedPermissionUpdates ??
    buildDefaultPermissionUpdates(toolCall.name, executionInput, entry.permissionCapabilityGroup);

  let projectRules: PermissionRuleset | null;
  try {
    projectRules = await loadProjectPermissionRuleset(deps);
  } catch (error) {
    return {
      allowed: false,
      result: createErrorResult(
        toolCall,
        createCoreError(CoreErrorType.StorageError, "Failed to load project permission rules", {
          cause: error instanceof Error ? error : undefined,
          context: { sessionId: deps.sessionId, toolCallId: toolCall.id, toolName: toolCall.name },
          recoverable: true,
        }),
      ),
    };
  }

  let permissionDecision = deps.permissionService.checkPermission(
    permissionContext,
    resolveRuntimePermissionCapability(entry, executionInput, runtimePermissionContext),
    projectRules,
    rulePolicy,
  );
  permissionDecision = applyPreToolPermissionDecision(permissionDecision, preToolHookResult, mode);
  permissionDecision = applyMemoryFilePermission({
    decision: permissionDecision,
    executionInput,
    memoryRoot: deps.getMemoryRoot?.(),
    toolName: toolCall.name,
    workingDirectory: deps.getWorkingDirectory(),
    workspaceRoot: deps.getWorkspaceRoot(),
  });
```
Capability assembly from the tool entry (`core/src/tool/executor/permission-capability.ts:6-19`):
```ts
export function resolveRuntimePermissionCapability(
  entry: ToolEntry,
  input: unknown,
  context: ToolRuntimePermissionCapabilityContext,
): PermissionToolCapability {
  const runtimeCapability = entry.resolvePermissionCapability?.(input, context);
  return {
    ...entry.metadata,
    ...runtimeCapability,
    // capability group is provenance, not a runtime/model-controlled override.
    permissionCapabilityGroup: entry.permissionCapabilityGroup,
    permission: {
      ...entry.permission,
      ...runtimeCapability?.permission,
    },
  };
}
```

Instances: one `PermissionService` per runtime; surfaces that do not pass one get the *unconfigured* default (`core/src/runtime/agent-runtime.ts:248-249`):
```ts
      deps.permissionService ?? new PermissionService(defaultPermissionConfig);
...
    this.permissionBroker = deps.permissionBroker ?? createDenyPermissionBroker();
```
Exceptions: the read-only Explore subagent builds a fresh default-config service (`core/src/runtime/methods/subagent.ts:315-318`), the project-memory agent likewise (`core/src/runtime/helpers/project-memory-agent.ts:117-118`), and the bootstrap app injects the config-file-derived one (`create-app.ts:342-347`, §A3.4).

### A4.2 Decision enums (service vs broker/event)

Service-side, quoted in §A1.2: `PermissionBehavior = "allow" | "ask" | "deny"`.

Broker/event-side (**`apps/zcode-cli/packages/contracts/src/events/session.events.ts:1032`**):
```ts
export type PermissionDecision = "allow" | "deny" | "escalate" | "modify";
```

**`apps/zcode-cli/packages/contracts/src/interfaces/permission.port.ts:52-100`**
```ts
export interface PermissionBrokerRequest {
  requestId: string;
  sessionId: SessionId;
  turnId?: TurnId;
  traceId: TraceId;
  toolCallId: ToolCallId;
  toolName: string;
  input: unknown;
  mode: CollaborationMode;
  ruleId: string;
  reason: string;
  riskLevel: RiskLevel;
  sideEffectScope?: ModelToolSideEffectScope;
  suggestedPermissionUpdates?: PermissionUpdate[];
  optionsPolicy?: PermissionOptionsPolicy;
  requestedAt: Date;
  origin?: InteractionRequestOrigin;
}

export interface PermissionBrokerResult {
  decision: PermissionDecision;
  reason?: string;
  /** V4 permission feedback 需要保留 provider-visible 原文；只由 broker 结果携带。 */
  preserveReasonFormatting?: boolean;
  reasonSource?: PermissionBrokerReasonSource;
  modifiedInput?: unknown;
  permissionUpdates?: PermissionUpdate[];
  ...
  sessionPermissionUpdates?: PermissionUpdate[];
  resolvedAt?: Date;
}

export interface PermissionBrokerRequestOptions {
  /** 带提交副作用的应答先取得唯一胜者；失败保留用户审批，Hook 不再竞争。 */
  claimResponse?: () => boolean;
  signal?: AbortSignal;
  timeoutMs?: number;
}

export interface PermissionBrokerPort {
  requestPermission(
    request: PermissionBrokerRequest,
    options?: PermissionBrokerRequestOptions,
  ): Promise<PermissionBrokerResult>;
}
```
and the ask-narrowing policy (**`permission.port.ts:43-50`**):
```ts
/**
 * Narrows the option set a permission ask may offer. `no-always-allow` drops the
 * persistent project rule option: some tools carry different code on every call
 * (CreateWorkflow scripts), so "always allow" would permanently disable the gate
 * rather than remember a comparable decision. `session-always-allow` replaces the
 * persistent rule with a session-scoped one that lives only in the runtime's memory.
 */
export type PermissionOptionsPolicy = "no-always-allow" | "session-always-allow";
```

### A4.3 On `allow`

**`permission-flow.ts:123-126`**
```ts
  if (permissionDecision.allowed) {
    telemetry?.setPermissionDecision("not_required");
    return { allowed: true, executionInput };
  }
```

### A4.4 On `deny` (service-level, no broker involved)

**`permission-flow.ts:128-152`**
```ts
  if (permissionDecision.decision === "deny") {
    telemetry?.setPermissionDecision("denied");
    await emitPermissionDenied(deps, toolCall, permissionDecision.reason, traceContext);
    ...
    return {
      allowed: false,
      result: createPermissionErrorResult(toolCall, permissionDecision.reason, {
        decision: permissionDecision.decision,
        mode,
        ruleId: permissionDecision.ruleId,
      }),
    };
  }
```
The error envelope (**`core/src/tool/executor/errors.ts:100-122`**):
```ts
export function createPermissionErrorResult(
  toolCall: ExecutableToolCall,
  reason: string | undefined,
  context: Record<string, unknown>,
  options?: {
    preserveReasonFormatting?: boolean;
  },
): ToolExecutionResult {
  return createErrorResult(
    toolCall,
    createCoreError(
      CoreErrorType.PermissionDenied,
      reason ?? `Permission denied for ${toolCall.name}`,
      {
        context: {
          ...context,
          toolName: toolCall.name,
        },
        recoverable: true,
      },
    ),
    undefined,
    options,
  );
}
```
so a denied tool **does not execute**: the executor returns a failed `ToolExecutionResult` whose `error.type` is `"permission_denied"` (`contracts/src/errors/index.ts:38`, grep-only). Turn-level handling of that error is in `core/src/tool/executor/turn-control.ts:85, 134` (grep-only: `result.error?.type !== CoreErrorType.PermissionDenied` guards).

### A4.5 On `ask` — broker request, interaction id, race, timeouts

Gate *before* the dialog (tool-declared approval preview / bypass; cannot widen an allow):

**`apps/zcode-cli/packages/core/src/tool/executor/approval-gate.ts:36-70`** — `resolveOptionsPolicy` maps `permission.askOptions.allowAlways` (`false` → `"no-always-allow"`, `"session"` → `"session-always-allow"`), then:
```ts
  if (!entry.prepareApproval) {
    return { gate: "ask", ...(optionsPolicy ? { optionsPolicy } : {}) };
  }
  ...
      const gate = entry.prepareApproval(executionInput);
      if (gate.gate === "proceed") return { gate: "proceed" };
```
and a throwing preview keeps the ask:
```ts
    // Bug 预防：负责生成预览的钩子绝不能决定"用户是否被询问"。向执行侧 fail-open 等于
    // 静默运行了一个未获批准的工具，所以这里 ask 照旧成立，只让预览降级。
```

Request id + request event (**`permission-flow.ts:154-172`**):
```ts
  const approval = resolveToolApproval(deps, toolCall, entry, executionInput, traceContext);
  if (approval.gate === "proceed") {
    telemetry?.setPermissionDecision("not_required");
    return { allowed: true, executionInput };
  }

  const requestId = `perm_${crypto.randomUUID()}`;
  telemetry?.markPermissionRequested();
  await emitPermissionRequested(
    deps,
    toolCall,
    executionInput,
    requestId,
    permissionDecision.riskLevel,
    permissionDecision.reason,
    suggestedPermissionUpdates,
    traceContext,
    approval,
  );
```
`emitPermissionRequested` writes `SessionEventType.PermissionRequested` with `{requestId, toolCallId, toolName, riskLevel, reason, input, suggestedPermissionUpdates, display?, optionsPolicy?, fullAccessSupported?}` (`core/src/tool/executor/events.ts:136-171`); payload contract at `contracts/src/events/session.events.ts:956-973`.

Broker request + hook race (**`permission-flow.ts:177-236`**), note the `timeoutMs` wiring:
```ts
  const permissionWaitStartedAt = Date.now();
  try {
    // 这里曾经串行 `await runPermissionRequestHooks(...)`，
    // broker 要等 hook 链返回才启动。...
    const raceOutcome = await racePermissionResponders({
      onHookFailure: (error) => {
        // hook 链故障只令其退赛：辅助应答方的基础设施故障不应替用户做拒绝决定，
        // 确认窗继续等待 broker 应答。
        ...
      },
      requestBroker: (brokerSignal, claimResponse) =>
        deps.permissionBroker.requestPermission(
          {
            input: executionInput,
            mode,
            reason: permissionDecision.reason ?? `Tool ${toolCall.name} requires approval`,
            requestId,
            requestedAt: new Date(),
            riskLevel: permissionDecision.riskLevel,
            ruleId: permissionDecision.ruleId,
            sessionId: deps.sessionId,
            sideEffectScope: permissionDecision.sideEffectScope,
            suggestedPermissionUpdates,
            ...(approval.optionsPolicy ? { optionsPolicy: approval.optionsPolicy } : {}),
            toolCallId: toolCall.id as PermissionBrokerRequest["toolCallId"],
            toolName: toolCall.name,
            traceId: traceContext.traceId,
            turnId: traceContext.turnId ?? deps.turnId,
          },
          {
            signal: brokerSignal,
            claimResponse,
            timeoutMs: deps.permissionTimeoutMs,
          },
        ),
      runHooks: (hookSignal) => runPermissionRequestHooks(...),
      ...(signal === undefined ? {} : { signal }),
    });
```

**Race semantics (broker-first, winner-takes-all)** — `core/src/tool/executor/permission-responder-race.ts:38-93`:
```ts
      const settle = (action: () => void, abortLoser: AbortController) => {
        if (settled) return;
        settled = true;
        action();
        // 先兑现结果再 abort 败者：败者的收尾（子进程终止、反向 RPC 取消）绝不能
        // 反过来阻塞胜者——挂死的就是败者本身。
        abortLoser.abort();
      };

      // broker 先启动：requestPermission 内同步注册应答 deferred，保证确认窗一可见
      // 用户点击就有归宿，不给 hook 留任何独占窗口期。
      input
        .requestBroker(brokerController.signal, () => {
          if (settled || brokerController.signal.aborted) return false;
          brokerClaimed = true;
          hookController.abort();
          return true;
        })
```

**Interaction id.** The interaction id *is* the business `requestId` (`perm_<uuid>`): the v4 registration is keyed by it (`bootstrap/src/zcode-protocol/interaction-broker.ts:70-73`):
```ts
  const response = await raceClientRequestWithV4Interaction(
    context,
    request.requestId,
    options?.signal,
```
and the race helper documents the identity (`bootstrap/src/zcode-protocol/interaction-response-race.ts:9-13`):
```ts
 * - 在 v4Interactions 登记 interactionId（= 业务 requestId，与 v4 投影
 *   PendingInteraction.interactionId 同源）；v4 应答到达时记下 answer 并 abort 内部
 *   controller，使 requestClient 以 ProtocolRequestError(-32021) 拒绝。
```
registration options (**`interaction-broker.ts:503-513`**):
```ts
  return {
    sessionId: String(request.sessionId),
    kind,
    ...(initialAutoResolution ? { initialAutoResolution } : {}),
```
and the reannounce constant (**`interaction-broker.ts:41`**):
```ts
const INTERACTION_REQUEST_REANNOUNCE_INTERVAL_MS = 1_000;
```
```ts
    // 桌面/恢复链路里 UI 可能只从 snapshot 恢复出 pending 交互，
    // 但 host 里原 protocol id 对应的内存登记已丢失。等待用户响应期间按同一业务
    // requestId 重发现有协议请求，让 host 重新登记可响应的 protocolRequestId。
    reannounceIntervalMs: INTERACTION_REQUEST_REANNOUNCE_INTERVAL_MS,
```
(`interaction-broker.ts:340-354`.) The runtime can also resolve a pending ask by **toolCallId** (manual broker resolves by either id: `broker.ts:152-159`; runtime entry point `core/src/runtime/methods/tools.ts:270-295`).

**Timeout constants and values.** The only permission timeout is per-request, optional, and unbounded by default:

* broker option (**`contracts/src/interfaces/permission.port.ts:88-93`**): `timeoutMs?: number` — "带提交副作用的应答先取得唯一胜者" for `claimResponse`, then `signal`, then `timeoutMs`.
* timer implementation (**`core/src/permission/broker.ts:101-119`**):
  ```ts
        if (options?.timeoutMs !== undefined) {
          timeout = setTimeout(() => {
            fail(
              createCoreError(
                CoreErrorType.PermissionTimeout,
                `Permission request timed out after ${options.timeoutMs}ms`,
                ...
                recoverable: true,
  ```
  plus abort handling (`broker.ts:87-99`) producing `CoreErrorType.ToolCancelled` / `"Permission request cancelled"`.
* value source (**`core/src/tool/executor/types.ts:87`, `:193`; `runtime/types.ts:132`**): `permissionTimeoutMs?: number`, threaded at `runtime/helpers/runtime-tools.ts:226` and `tool/executor/impl.ts:33`. **Grep across the repo finds no product surface that sets it**, so in practice `deps.permissionTimeoutMs === undefined` → no timer → the ask waits until `signal` aborts (turn cancel) or the process/turn ends. `CoreErrorType.PermissionTimeout: "permission_timeout"` is defined at `contracts/src/errors/index.ts:38` (grep-only).
* bounded constants *near* the ask path: `INTERACTION_REQUEST_REANNOUNCE_INTERVAL_MS = 1_000` (`interaction-broker.ts:41`); workspace-hook review deadline `10 * 60 * 1000` (§B12); hook execution timeout default `60_000` ms (§B11). The TUI approval prompt has **no** timeout of its own — it resolves on user input or on abort (`tui/src/app-permission.ts:100-127`).

**Who answers, per surface** (grep-guided):
* TUI: `cli/src/tui-prompt-handler.ts:78-91` delegates to `createTuiPermissionRequester`, which short-circuits `CreateWorkflow` / `AmendWorkflow` with a documented CLI-only bypass and **no** `permissionUpdates` (`tui/src/app-permission.ts:31-56`).
* headless `-p`: `createHeadlessPermissionBroker` denies everything except workflow tools (`cli/src/headless-workflow.ts:31-51`).
* no client at all: `DenyPermissionBroker` (`core/src/permission/broker.ts:24-32`):
  ```ts
  export class DenyPermissionBroker implements PermissionBrokerPort {
    async requestPermission(request: PermissionBrokerRequest): Promise<PermissionBrokerResult> {
      return {
        decision: "deny",
        reason: `No permission client configured for ${request.toolName}`,
        resolvedAt: new Date(),
      };
    }
  }
  ```
* protocol/desktop: `createProtocolInteractionBroker` (`bootstrap/src/zcode-protocol/interaction-broker.ts:43-57`), with `AskUserQuestion` and `ExitPlanMode` special-cased; unknown `optionId` falls back to deny (`interaction-broker.ts:191-192`).
* `ManualPermissionBroker` (`core/src/permission/broker.ts:34-160`) provides `resolvePermission` / `rejectPermission` / `getPendingRequest` / `listPendingRequests`, rejecting duplicate pending ids with `CoreErrorType.InvalidStateTransition` (`broker.ts:44-55`).

### A4.6 After the answer

Hook-modified input is re-validated and **re-checked against permissions** (`permission-flow.ts:240-267`):
```ts
    if (permissionHookResult?.decision === "modify") {
      normalizedHookModifiedInput = normalizeToolExecutionInput({...});
      useNormalizedHookModifiedInput = true;
      if (!validateInput(normalizedHookModifiedInput, entry)) {
        // PermissionRequest hook 可以改写目标路径，修改后的输入不能沿用修改前的权限结果。
        const recheck = await recheckPermissionHookModifiedInput({...});
```
(the second `checkPermission` call site — `core/src/tool/executor/permission-input-recheck.ts:50-56` — re-runs with the modified input and returns a deny if the new input is denied, `permission-input-recheck.ts:65-71`).

Broker rejection becomes a deny (**`permission-flow.ts:268-289`**):
```ts
  } catch (error) {
    telemetry?.setPermissionDecision("denied");
    const coreError = isCoreError(error)
      ? error
      : createCoreError(CoreErrorType.PermissionDenied, "Permission request failed", {...});
    await emitPermissionResolved(deps, toolCall, requestId, { decision: "deny", reason: coreError.message, resolvedAt: new Date() }, traceContext);
    return { allowed: false, result: createErrorResult(toolCall, coreError) };
  }
```
Resolution event, deny, escalate (**`permission-flow.ts:291-357`**):
```ts
  const resolvedPermission = {
    ...brokerResult,
    resolvedAt: brokerResult.resolvedAt ?? new Date(),
  };
  const permissionWaitMs = Math.max(0, Math.round(Date.now() - permissionWaitStartedAt));
  await emitPermissionResolved(deps, toolCall, requestId, resolvedPermission, traceContext);
  ...
  if (resolvedPermission.decision === "deny") {
    telemetry?.setPermissionDecision("denied");
    return {
      allowed: false,
      result: createPermissionErrorResult(
        toolCall,
        resolvedPermission.reason,
        {
          decision: resolvedPermission.decision,
          mode,
          reasonSource: resolvedPermission.reasonSource,
          requestId,
          ruleId: permissionDecision.ruleId,
        },
        resolvedPermission.preserveReasonFormatting
          ? { preserveReasonFormatting: true }
          : undefined,
      ),
    };
  }

  if (resolvedPermission.decision === "escalate") {
    telemetry?.setPermissionDecision("denied");
    return {
      allowed: false,
      result: createErrorResult(
        toolCall,
        createCoreError(
          CoreErrorType.PermissionEscalation,
          resolvedPermission.reason ?? `Permission escalation requested for ${toolCall.name}`,
          ...
```
Feedback escalation channel (`contracts/src/interfaces/permission.port.ts:39-41`):
```ts
// 修改原因：workflow Refine 与 plan 反馈同构——
// deny + reason 只有携带专用 source 才允许被升级为真实 user message。
export type PermissionBrokerReasonSource = "plan_approval_feedback" | "workflow_refine_feedback";
```
and the preservation rule that keeps that feedback verbatim (`core/src/tool/executor/errors.ts:25-34`):
```ts
  const reasonSource =
    isCoreError(error) &&
    (error.context?.reasonSource === "plan_approval_feedback" ||
      error.context?.reasonSource === "workflow_refine_feedback")
      ? error.context.reasonSource
      : undefined;
  // ExitPlanMode / workflow Refine 的用户反馈会暂存在 PermissionDenied.message，
  // 后续还要原样转成 steer 输入；这里不能被展示摘要器截断，否则超长反馈不会触发 input_too_large。
  // 普通拒绝附带的自由文本反馈（preserveReasonFormatting）同理原样保留。
```

Then, in order: persist project updates, apply session grants, or accept a `modify` (`permission-flow.ts:359-431`) — persisted-failure is fatal to the call:
```ts
  if (resolvedPermission.permissionUpdates?.length) {
    try {
      await persistProjectPermissionUpdates(deps, resolvedPermission.permissionUpdates, traceContext);
    } catch (error) {
      return { allowed: false, result: createErrorResult(toolCall, createCoreError(CoreErrorType.StorageError, "Failed to persist project permission update", {...})) };
    }
  }
```

---

## A5. Full access + grant recovery + resume — the grant-id lifecycle

### A5.1 Entry point

The dialog's "full access" action calls the runtime with the **permission request id as the grant id** (`bootstrap/src/zcode-protocol/interaction-broker.ts:100-122`):
```ts
    {
      ...createInteractionRegistrationOptions(request, "other"),
      ...(!request.origin &&
      !request.optionsPolicy &&
      options?.claimResponse &&
      context.deps?.sessionStore?.commitPermissionFullAccess
        ? {
            fullAccess: async () => {
              if (!options.claimResponse!()) throw new Error("Permission response already settled");
              options.signal?.throwIfAborted();
              const record = context.sessions.get(String(request.sessionId));
              if (!record) throw new Error("Permission session unavailable");
              const eventId = await record.app.runtime.grantPermissionFullAccess(
                request.requestId,
                options.signal,
              );
              await context.v4Gateway?.waitForPermissionGrantCommit(
                String(request.sessionId),
                eventId,
              );
            },
          }
        : {}),
    },
```

### A5.2 The grant itself

**`apps/zcode-cli/packages/core/src/runtime/permission-full-access.ts:15-110`** (whole function):
```ts
const appliedGrants = new WeakMap<AgentRuntimeInternal, Set<string>>();

/** 完全访问只改当前任务；receipt 固定目标集合，发布失败后不能重新抓取后来队列。 */
export async function grantPermissionFullAccess(
  this: AgentRuntimeInternal,
  interactionId: string,
  signal?: AbortSignal,
): Promise<string> {
  if (!this.sessionStore?.commitPermissionFullAccess) throw new Error("Full access is unsupported");
  if (
    this.permissionFullAccessPending ||
    this.pendingInputReservations.size > 0 ||
    this.pendingInputDrains
  ) {
    throw new Error("Queue mutation is busy; retry approval");
  }
  const unpublished = unpublishedPermissionGrants.get(this);
  if (unpublished && unpublished.interactionId !== interactionId)
    await recoverPendingPermissionGrant(this);
  this.permissionFullAccessPending = true;
  try {
    signal?.throwIfAborted();
    const receiptId = `${this.sessionId}:permission-full-access:${interactionId}`;
    const entries = await this.sessionStore.sessionEntries?.({
      sessionID: this.sessionId,
      type: PERMISSION_FULL_ACCESS_ENTRY,
    });
    const saved = entries?.find((entry) => entry.id === receiptId);
    let event: SessionEvent;
    if (saved) {
      const data = permissionFullAccessReceiptSchema.parse(saved.data);
      if (data.event.sessionId !== this.sessionId || data.interactionId !== interactionId) {
        throw new Error("Permission receipt scope mismatch");
      }
      event = data.event as SessionEvent;
    } else {
      const projection = await this.rebuildProjection();
      const queueItemIds = projection.pendingSteerInputs.map((item) => item.pendingInputId);
      const previous = readRuntimeExecutionState(this);
      const next = { ...previous, mode: "yolo" as const };
      event = this.createEvent(
        SessionEventType.SessionModeChanged,
        {
          ...next,
          previousMode: previous.mode,
          previousPlanEnabled: previous.planEnabled,
          source: "command",
          permissionGrant: { interactionId, queueItemIds },
        },
        this.rootTraceContext,
      );
      signal?.throwIfAborted();
      await this.sessionStore.commitPermissionFullAccess({
        sessionID: this.sessionId,
        queueItemIds,
        signal,
        execution: buildExecutionStateEntry(this.sessionId, next),
        receipt: {
          id: receiptId,
          sessionID: this.sessionId,
          type: PERMISSION_FULL_ACCESS_ENTRY,
          touchSession: false,
          time: { created: Date.now(), updated: Date.now() },
          data: { interactionId, event },
        },
      });
    }
    const payload = event.payload as SessionModeChangedPayload;
    unpublishedPermissionGrants.set(this, {
      interactionId,
      recover: () => grantPermissionFullAccess.call(this, interactionId),
    });
    const ids = new Set(payload.permissionGrant!.queueItemIds);
    // 事务已提交：之后即使传输取消也必须完成内存和投影发布，不能制造半个授权。
    const applied = appliedGrants.get(this) ?? new Set<string>();
    if (!applied.has(interactionId)) {
      this.lastPermissionGrantId = interactionId;
      this.config.mode = payload.mode;
      this.config.planEnabled = payload.planEnabled;
      for (const item of this.activeTurn?.pendingInputs ?? []) {
        if (ids.has(item.id) && item.intent) item.intent = { ...item.intent, mode: "yolo" };
      }
      applied.add(interactionId);
      appliedGrants.set(this, applied);
    }
    const existing = (await this.eventStore.getEvents(this.sessionId)).find(
      (item) => item.id === event.id,
    );
    if (existing) await this.notifyEventSinks(existing, this.rootTraceContext);
    else await this.appendEvent(event, this.rootTraceContext);
    unpublishedPermissionGrants.delete(this);
    return String(event.id);
  } finally {
    this.permissionFullAccessPending = false;
  }
}
```

Grant-id lifecycle, in order:

1. **Id origin** — the permission `requestId` (`perm_<uuid>`, `permission-flow.ts:160`) is passed as `interactionId`.
2. **Receipt id** — `` `${this.sessionId}:permission-full-access:${interactionId}` ``; the receipt is an idempotency key: a re-entrant call finds the saved `session_entry` and reuses its event rather than re-snapshotting the queue.
3. **Frozen target set** — `queueItemIds` is captured from `projection.pendingSteerInputs` *before* the transaction and stored inside the event payload (`permissionGrant: { interactionId, queueItemIds }`), so a retry cannot widen the grant to inputs queued later.
4. **Durability** — execution state + receipt are written in one transaction (`contracts/src/interfaces/session-store.port.ts:1143-1150`):
   ```ts
     /** 审批完全访问：execution、固定队列权限和幂等 receipt 同一事务；无 schema migration。 */
     commitPermissionFullAccess?(input: {
       sessionID: SessionId;
       queueItemIds: string[];
       execution: SessionEntryInfo;
       receipt: SessionEntryInfo;
       signal?: AbortSignal;
     }): Promise<void>;
   ```
   implementation `adapters/src/storage/session-store/repositories/permission-full-access.ts:5-52` (`begin immediate`, rewrites admitted `session_input` payloads' `intent`/`conversationInputIntent` to `mode: "yolo"`, `saveSessionEntry` ×2, `commit`/`rollback`).
5. **Receipt entry type + schema** — `contracts/src/interfaces/permission-full-access.ts:3`:
   ```ts
   export const PERMISSION_FULL_ACCESS_ENTRY = "runtime/permission_full_access";
   ```
   ```ts
   /** 持久化 receipt 是授权重试事实源；损坏时拒绝重试，不能按当前队列重新扩大授权范围。 */
   export const permissionFullAccessReceiptSchema = z
     .object({
       interactionId: z.string().min(1),
       event: z.object({ ..., payload: z.object({
               mode: z.literal("yolo"),
               planEnabled: z.boolean(),
               previousMode: z.enum(["build", "edit", "yolo", "auto", "plan"]),
               previousPlanEnabled: z.boolean(),
               source: z.literal("command"),
               permissionGrant: z.object({
                   interactionId: z.string().min(1),
                   queueItemIds: z.array(z.string().min(1)),
                 }).strict(),
             }).strict() }).strict(),
     })
     .strict()
     .refine(
       (receipt) => receipt.interactionId === receipt.event.payload.permissionGrant.interactionId,
       {
         message: "Permission grant identity mismatch",
       },
     );
   ```
6. **In-memory publication** — `lastPermissionGrantId = interactionId`, `config.mode = payload.mode`, `planEnabled`, pending turn inputs rewritten to yolo; `appliedGrants` (a `WeakMap<runtime, Set<interactionId>>`) makes the in-memory apply once-per-id. The grant affects **only the current task** ("完全访问只改当前任务", line 17).

### A5.3 Recovery of a committed-but-unpublished grant

**`apps/zcode-cli/packages/core/src/runtime/permission-grant-recovery.ts` (entire file, 14 lines)**
```ts
// 不保存第二份权限/队列，只记录事务已提交但事件尚未发布的恢复动作。
export const unpublishedPermissionGrants = new WeakMap<
  AgentRuntimeInternal,
  {
    interactionId: string;
    recover: () => Promise<string>;
  }
>();

export async function recoverPendingPermissionGrant(runtime: AgentRuntimeInternal): Promise<void> {
  await unpublishedPermissionGrants.get(runtime)?.recover();
}
```
Registration happens *after* the commit and before event publication (`permission-full-access.ts:83-86`), deletion after publication (`:105`); a subsequent grant with a different `interactionId` first drives the pending one to completion (`:31-33`).

### A5.4 Resume

**`apps/zcode-cli/packages/core/src/runtime/helpers/permission-grant-resume.ts` (entire file, 33 lines)**
```ts
/** 授权 receipt 在恢复时仅提供辅助标记；格式损坏不能阻断历史及执行状态恢复。 */
export async function restorePermissionGrantMarker(
  runtime: AgentRuntimeInternal,
  traceContext: TraceContext,
): Promise<void> {
  runtime.lastPermissionGrantId = undefined;
  const entries = await runtime.sessionStore?.sessionEntries?.({
    sessionID: runtime.sessionId,
    type: PERMISSION_FULL_ACCESS_ENTRY,
  });
  const lastGrant = entries?.at(-1);
  if (!lastGrant) return;
  const receipt = permissionFullAccessReceiptSchema.safeParse(lastGrant.data);
  if (!receipt.success || receipt.data.event.sessionId !== runtime.sessionId) {
    runtime.logger?.warn("Ignoring invalid permission grant marker during session resume", {
      ...traceContextToLogContext(traceContext),
      event: "session.resume.permission_grant_invalid",
      module: "core.runtime",
      entryId: lastGrant.id,
      reason: receipt.success ? "session_mismatch" : "invalid_receipt",
    });
    return;
  }
  runtime.lastPermissionGrantId = receipt.data.interactionId;
}
```
The id is then surfaced to clients as the resume-time grant marker (`bootstrap/src/zcode-protocol/v4-bridge.ts:1524-1526`):
```ts
        ...(record.app.runtime.lastPermissionGrantId
          ? { permissionGrant: { interactionId: record.app.runtime.lastPermissionGrantId } }
          : {}),
```
Field declared at `core/src/runtime/agent-runtime.ts:338` and `runtime/internal.ts:138`.

**Lifecycle summary:** `permission requestId (perm_…)` → receipt id `"{sessionId}:permission-full-access:{interactionId}"` → durable `session_entry` of type `runtime/permission_full_access` (strict schema, identity cross-checked against the event payload) → in-memory `lastPermissionGrantId` + `mode: "yolo"` + queue-intent rewrite → optional `unpublishedPermissionGrants` recovery action if publication is interrupted → client-visible `permissionGrant.interactionId` → on resume, re-derived from the last receipt (invalid ⇒ cleared + warn, never fatal).

---

## A6. Plan-mode policy decisions

### A6.1 Mode transitions (checked *first*, before every other gate)

**`apps/zcode-cli/packages/core/src/permission/plan-mode-policy.ts:20-43`** (whole function):
```ts
export function resolvePlanModeTransitionPermission(
  context: PlanModeTransitionContext,
): PlanModeTransitionPermission | undefined {
  if (context.toolName === ENTER_PLAN_MODE_TOOL_NAME) {
    return {
      behavior: "allow",
      reason: "EnterPlanMode switches to plan mode without a permission prompt",
      ruleId: "tool.plan.enter",
    };
  }

  if (
    context.toolName === EXIT_PLAN_MODE_TOOL_NAME &&
    !(context.planEnabled ?? context.mode === "plan")
  ) {
    return {
      behavior: "deny",
      reason: "ExitPlanMode can only be used while plan mode is active",
      ruleId: "mode.plan.exitOnly",
    };
  }

  return undefined;
}
```
Applied at `service.ts:104-110`:
```ts
    const planModeTransition = resolvePlanModeTransitionPermission(context);
    if (planModeTransition) {
      return planModeTransition.behavior === "allow"
        ? this.allow(context, capability, planModeTransition.ruleId, planModeTransition.reason)
        : this.deny(context, capability, planModeTransition.ruleId, planModeTransition.reason);
    }
```
`planEnabled` may be true while `mode !== "plan"`; every mode branch that could allow consults it: yolo requires `!planEnabled` (`service.ts:136`), and gate 9 routes to `checkPlanMode` (`service.ts:176-178`).

### A6.2 What plan mode allows

**`apps/zcode-cli/packages/core/src/permission/service.ts:402-448`**
```ts
  private checkPlanMode(
    context: PermissionContext,
    capability: ResolvedPermissionCapability,
  ): PermissionDecisionResult {
    if (capability.readOnly && !capability.destructive) {
      return this.allow(
        context,
        capability,
        "mode.plan.readOnly",
        "Plan mode allows read-only tool execution",
      );
    }

    if (this.isMcpToolCapability(capability) && !capability.destructive) {
      return this.allow(
        context,
        capability,
        "mode.plan.mcp",
        "Plan mode allows non-destructive MCP tool execution",
      );
    }

    if (
      capability.allowedInPlanMode &&
      capability.sideEffectScope === "session" &&
      !capability.destructive &&
      !capability.needsApproval
    ) {
      return this.allow(
        context,
        capability,
        "mode.plan.explicitSessionCapability",
        "Plan mode allows this explicit non-destructive session control action",
      );
    }

    return this.deny(
      context,
      capability,
      "mode.plan.nonReadOnly",
      "Plan mode only allows read-only, non-destructive tools",
    );
  }

  private isMcpToolCapability(capability: ResolvedPermissionCapability): boolean {
    return capability.permissionName === "mcp";
  }
```
Key points: plan mode is **deny-by-default** (`mode.plan.nonReadOnly`), never "ask"; project `deny`/`ask` rules are evaluated *before* plan mode (`service.ts:158-174`), project `allow` rules *after* it (`service.ts:180-187`) — so in plan mode an explicit project allow cannot force a write through. The workflow-draft and WebFetch pre-approvals sit **after** the plan branch for the same reason (`service.ts:198-201`: "plan 模式必须继续拦下一切写入，草稿也是写入").

---

## A7. Tool allowlist

Two independent allowlists exist: the **permission-service** name sets (§A3.4, checked at `service.ts:149-156`/`217-224`), and the **tool-registration** allowlist.

**`apps/zcode-cli/packages/core/src/runtime/helpers/tool-allowlist.ts:78-95`**
```ts
export function resolveBuiltInToolAllowlist(
  config: AgentRuntimeConfig,
): readonly string[] | undefined {
  const normalizedAllowlist = normalizeBuiltInToolAllowlist(config.toolAllowlist);

  if (config.toolset !== "explore") {
    return appendChildControlTool(config, normalizedAllowlist);
  }

  if (!normalizedAllowlist) {
    return appendChildControlTool(config, EXPLORE_AGENT_ALLOWED_TOOLS);
  }

  return appendChildControlTool(
    config,
    normalizedAllowlist.filter((toolName) => EXPLORE_AGENT_ALLOWED_TOOL_SET.has(toolName)),
  );
}
```

**`tool-allowlist.ts:97-123`**
```ts
function appendChildControlTool(
  config: AgentRuntimeConfig,
  allowlist: readonly string[] | undefined,
): readonly string[] | undefined {
  if (!allowlist) {
    return allowlist;
  }

  if (config.taskType === "subagent_child") {
    if (allowlist.includes(RESPOND_TO_COORDINATOR_TOOL_NAME)) {
      return allowlist;
    }
    // Explore 会在 runtime 注册前再次求工具交集，child 控制工具必须在最终结果补回。
    return [...allowlist, RESPOND_TO_COORDINATOR_TOOL_NAME];
  }
  ...
  return allowlist;
}

function normalizeBuiltInToolAllowlist(
  allowlist: readonly string[] | undefined,
): readonly string[] | undefined {
  return allowlist?.map((toolName) => normalizeToolNameAlias(toolName));
}
```

**`tool-allowlist.ts:26-62`** — the structural disallowlist, applied *in addition* to the turn-level one:
```ts
const WORKFLOW_CHILD_DISALLOWED_TOOLS = [
  CREATE_WORKFLOW_TOOL_NAME,
  // AmendWorkflow 与 CreateWorkflow 同一道 alwaysAsk 门、同一种嵌套编排，因同一个根因入列。
  AMEND_WORKFLOW_TOOL_NAME,
  ...
  SAVE_WORKFLOW_TOOL_NAME,
  ...
  RESUME_WORKFLOW_RUN_TOOL_NAME,
  ...
  RESOLVE_WORKFLOW_QUESTION_TOOL_NAME,
] as const;

/**
 * 运行时最终的工具禁用名单：turn 级 `toolDisallowlist` 叠加按 taskType 推导出的结构性禁用。
 * 放在这里而不是各个 child runtime 的构造点，是因为构造点有两个
 * （`workflow-facade.ts` 与 `script-workflow-child-runtime.ts`），两份名单必然漂移。
 */
export function resolveRuntimeDisallowedTools(
  config: AgentRuntimeConfig,
): readonly string[] | undefined {
  if (config.taskType !== "workflow_child") return config.toolDisallowlist;

  const disallowed = new Set(config.toolDisallowlist ?? []);
  for (const toolName of WORKFLOW_CHILD_DISALLOWED_TOOLS) disallowed.add(toolName);
  return [...disallowed];
}
```
The rationale comment (`tool-allowlist.ts:15-25`) is the reason `CreateWorkflow` is absent from workflow children at all: it declares `alwaysAsk`, and a child's ask event is not mirrored to the parent UI, so the request would hang until the permission timeout.

Consumption: `runtime/helpers/runtime-tools.ts:81-85` (`allowedTools: resolveBuiltInToolAllowlist(runtime.config)`, `disallowedTools: resolveRuntimeDisallowedTools(runtime.config)`), refresh path `runtime/methods/embedded-search-branch.ts:41-42`, MCP intersection `runtime/methods/mcp.ts:139-140`.

The workflow-draft write pre-approval (an allowlist of exactly two tools + one directory) is quoted in full in §A2 context; its core predicate:

**`apps/zcode-cli/packages/core/src/permission/workflow-draft-path.ts:38, 60-90`**
```ts
const WORKFLOW_DRAFT_PREAPPROVED_TOOL_NAMES = new Set(["Edit", "Write"]);
...
export function isPreapprovedWorkflowDraftWrite(input: WorkflowDraftWriteInput): boolean {
  if (!WORKFLOW_DRAFT_PREAPPROVED_TOOL_NAMES.has(input.toolName)) return false;
  if (typeof input.workingDirectory !== "string") return false;
  if (!input.input || typeof input.input !== "object") return false;
  const filePath = (input.input as Record<string, unknown>).file_path;
  if (typeof filePath !== "string") return false;
  return isWorkflowDraftPath({ filePath, pathModule: input.pathModule, workingDirectory: input.workingDirectory });
}
...
  return (
    relativePath.length > 0 && !path.isAbsolute(relativePath) && !relativePath.startsWith("..")
  );
```

---

# Part B — HOOKS

## B8. Hook POINT names (verbatim, with path:line)

**Canonical literal set — `apps/zcode-cli/packages/contracts/src/hooks/index.ts:7-17`**
```ts
export const HookEventName = {
  SessionStart: "SessionStart",
  UserPromptSubmit: "UserPromptSubmit",
  PreToolUse: "PreToolUse",
  PermissionRequest: "PermissionRequest",
  PostToolUse: "PostToolUse",
  PostToolUseFailure: "PostToolUseFailure",
  Stop: "Stop",
} as const;

export type HookEventName = (typeof HookEventName)[keyof typeof HookEventName];
```
| Literal | Declared at | Fired from |
|---|---|---|
| `"SessionStart"` | `contracts/src/hooks/index.ts:8` | `core/src/runtime/methods/hooks.ts:33-47` (input carries `source: startup \| resume \| clear \| compact`; `matchValue: source`) |
| `"UserPromptSubmit"` | `contracts/src/hooks/index.ts:9` | `core/src/runtime/methods/hooks.ts:59-73` |
| `"PreToolUse"` | `contracts/src/hooks/index.ts:10` | `core/src/tool/executor/hook-flow.ts:16-46` |
| `"PermissionRequest"` | `contracts/src/hooks/index.ts:11` | `core/src/tool/executor/hook-flow.ts:47-62` (raced with the broker) |
| `"PostToolUse"` | `contracts/src/hooks/index.ts:12` | `core/src/tool/executor/hook-flow.ts:121-146` |
| `"PostToolUseFailure"` | `contracts/src/hooks/index.ts:13` | `core/src/tool/executor/hook-flow.ts:148-186` |
| `"Stop"` | `contracts/src/hooks/index.ts:14` | `core/src/runtime/methods/hooks.ts:87-103` |

The same seven literals are re-stated in five other places (a real drift surface — all currently agree):
* `contracts/src/hooks/index.ts:411-422` (`HooksRuntimeConfigPatchSchema.events`, `.strict()`);
* `contracts/src/hooks/workspace-hook-trust.ts:9-17` (`WORKSPACE_HOOK_SCHEMA_FIELDS.events`);
* `contracts/src/hooks/workspace-hook-trust.ts:188-196` (`workspaceHookEventNameSchema`);
* `packages/shared/src/workspace-hook-config.ts:9-17` (`WORKSPACE_HOOK_EVENT_NAMES`);
* `packages/shared/src/workspace-hook-trust-store-file.ts:24-32` (trust-record `eventAtGrant` enum).
  Note `contracts/src/hooks/workspace-hook-trust.ts:17` uses `"Stop",` while the arrays in `shared` use quotes too; the enums are string-identical.

Hook *outcome* and *source* enums (verbatim):

**`contracts/src/hooks/index.ts:19-36`**
```ts
export const HookOutcome = {
  Success: "success",
  Blocked: "blocked",
  Failed: "failed",
  Cancelled: "cancelled",
  TimedOut: "timed_out",
} as const;

export type HookOutcome = (typeof HookOutcome)[keyof typeof HookOutcome];

export const HookSourceKind = {
  User: "user",
  Plugin: "plugin",
  Project: "project",
  Internal: "internal",
} as const;
```

Lifecycle event types (`contracts/src/events/session.events.ts:153-157`, grep-only): `HookRunStarted: "hook_run_started"`, `HookRunCompleted: "hook_run_completed"`, `HookRunFailed: "hook_run_failed"`, `HookRunBlocked: "hook_run_blocked"`; payload shape `HookRunLifecyclePayload` at `contracts/src/hooks/index.ts:350-375`.

There is **no** `PreCompact`, `Notification`, `SubagentStop` or `SessionEnd` point — the set is exactly these seven.

---

## B9. What a hook can DO

### B9.1 The output contract (what a hook may return)

**`apps/zcode-cli/packages/contracts/src/hooks/index.ts:196-206`**
```ts
export interface HookJSONOutput {
  additionalContext?: string;
  additional_context?: string;
  continue?: boolean;
  decision?: "approve" | "block";
  hookSpecificOutput?: HookSpecificOutput;
  reason?: string;
  stopReason?: string;
  suppressOutput?: boolean;
  systemMessage?: string;
}
```
validated by `HookJSONOutputSchema` (`contracts/src/hooks/index.ts:282-292`).

**`contracts/src/hooks/index.ts:163-194`**
```ts
export type HookSpecificOutput =
  | {
      additionalContext?: string;
      hookEventName: typeof HookEventName.PreToolUse;
      permissionDecision?: HookPermissionDecision;
      permissionDecisionReason?: string;
      updatedInput?: unknown;
    }
  | {
      additionalContext?: string;
      hookEventName: typeof HookEventName.UserPromptSubmit;
    }
  | {
      additionalContext?: string;
      hookEventName: typeof HookEventName.SessionStart;
    }
  | {
      additionalContext?: string;
      hookEventName: typeof HookEventName.PostToolUse;
    }
  | {
      additionalContext?: string;
      hookEventName: typeof HookEventName.PostToolUseFailure;
    }
  | {
      decision?: PermissionRequestHookDecision;
      hookEventName: typeof HookEventName.PermissionRequest;
    }
  | {
      additionalContext?: string;
      hookEventName: typeof HookEventName.Stop;
    };
```

**`contracts/src/hooks/index.ts:150-165`**
```ts
export type PermissionRequestHookDecision =
  | {
      behavior: "allow";
      permissionUpdates?: PermissionUpdate[];
      updatedPermissions?: PermissionUpdate[];
      updatedInput?: unknown;
    }
  | {
      behavior: "deny";
      interrupt?: boolean;
      message?: string;
    };
```

**`contracts/src/hooks/index.ts:58-65`**
```ts
export const HookPermissionDecision = {
  Allow: "allow",
  Ask: "ask",
  Deny: "deny",
} as const;

export type HookPermissionDecision =
  (typeof HookPermissionDecision)[keyof typeof HookPermissionDecision];
```

### B9.2 The merged run result (what the caller sees)

**`apps/zcode-cli/packages/core/src/hooks/types.ts:72-86`**
```ts
export interface HookRunResult {
  additionalContexts: string[];
  blockRequested?: boolean;
  hookPermissionDecisionReason?: string;
  permissionBehavior?: HookPermissionDecision;
  permissionRequestResult?: PermissionRequestHookDecision;
  preventContinuation?: boolean;
  stopShouldContinue?: boolean;
  stopReason?: string;
  updatedInput?: unknown;
}

export interface HookRunner {
  run(input: HookInput, options?: HookRunOptions): Promise<HookRunResult>;
}
```

### B9.3 Normalization rules — `processHookOutput`

**`apps/zcode-cli/packages/core/src/hooks/output.ts:11-65`**
```ts
export function processHookOutput(
  expectedEvent: HookEventName,
  output: HookJSONOutput | void,
): HookRunResult {
  const result: HookRunResult = {
    additionalContexts: [],
  };
  if (!output) return result;

  if (output.continue === false && expectedEvent !== HookEventName.Stop) {
    result.blockRequested = true;
    result.stopReason = output.stopReason ?? output.reason;
    if (shouldPreventContinuation(expectedEvent)) {
      result.preventContinuation = true;
    }
    if (isPermissionEvent(expectedEvent)) {
      result.permissionBehavior = "deny";
    }
  }
  if (expectedEvent === HookEventName.Stop && output.continue === true) {
    result.stopShouldContinue = true;
    result.stopReason = output.stopReason ?? output.reason;
  }
  if (output.decision === "approve" && isPermissionEvent(expectedEvent)) {
    result.permissionBehavior = "allow";
  }
  if (output.decision === "block") {
    result.blockRequested = true;
    result.stopReason = output.stopReason ?? output.reason ?? output.systemMessage;
    if (isPermissionEvent(expectedEvent)) result.permissionBehavior = "deny";
    if (shouldPreventContinuation(expectedEvent)) result.preventContinuation = true;
    if (expectedEvent === HookEventName.Stop) {
      result.stopShouldContinue = true;
      if (output.systemMessage) result.additionalContexts.push(output.systemMessage);
      if (output.reason) result.additionalContexts.push(output.reason);
    }
  }
  if (output.additionalContext) result.additionalContexts.push(output.additionalContext);
  if (output.additional_context) result.additionalContexts.push(output.additional_context);

  const specific = output.hookSpecificOutput;
  if (!specific) return result;
  if (specific.hookEventName !== expectedEvent) {
    throw createCoreError(CoreErrorType.ToolExecutionFailed, "Hook returned wrong event name", {
      context: { expectedEvent, hookEventName: specific.hookEventName },
      recoverable: true,
    });
  }

  applyHookSpecificOutput(result, specific);
  return result;
}
```
Helpers (`output.ts:114-159`):
```ts
function isPermissionEvent(event: HookEventName): boolean {
  return event === HookEventName.PreToolUse || event === HookEventName.PermissionRequest;
}
...
function mergePermissionBehavior(
  current: HookPermissionDecision | undefined,
  next: HookPermissionDecision,
): HookPermissionDecision {
  if (current === "deny" || next === "deny") return "deny";
  if (current === "ask" || next === "ask") return "ask";
  return next;
}

function shouldPreventContinuation(event: HookEventName): boolean {
  return (
    event === HookEventName.PreToolUse ||
    event === HookEventName.PermissionRequest ||
    event === HookEventName.UserPromptSubmit
  );
}
```

Cross-hook merge (`output.ts:67-96`): `additionalContexts` accumulate; the **last** `updatedInput`, `permissionRequestResult` and `hookPermissionDecisionReason` win; deny > ask > allow for `permissionBehavior`; `blockRequested`, `preventContinuation`, `stopShouldContinue` are sticky/last-wins respectively.

### B9.4 Blocking

Inside the runner, the terminal state is `HookRunBlocked` when (`runner.ts:167-173`):
```ts
        const blocked =
          processed.permissionBehavior === "deny" ||
          processed.permissionRequestResult?.behavior === "deny" ||
          processed.preventContinuation ||
          processed.blockRequested;
```
with the reason persisted into the event (`runner.ts:174-183`):
```ts
        const blockReason = blocked
          ? sanitizeHookDisplayText(
              processed.stopReason ??
                processed.hookPermissionDecisionReason ??
                (processed.permissionRequestResult?.behavior === "deny"
                  ? processed.permissionRequestResult.message
                  : undefined) ??
                "Hook blocked execution",
            )
          : undefined;
```
Impact on the tool call is decided downstream, not by the runner:

* **PreToolUse** — **`core/src/tool/executor/hook-flow.ts:187-225`**
  ```ts
  export function applyPreToolPermissionDecision(
    permissionDecision: PermissionDecisionResult,
    hookResult: HookRunResult,
    mode: CollaborationMode,
  ): PermissionDecisionResult {
    if (permissionDecision.decision === "deny") return permissionDecision;
    // alwaysAsk 声明出来的确认（如 workflow 运行确认）不能被 PreToolUse hook
    // 的整体 allow 悄悄抹掉——那等于给"任何模式都要问"开了一个静默后门。自动化仍有正规出口：
    // PermissionRequest hook 可以应答这次弹窗（见 runPermissionRequestHooks）。
    if (
      hookResult.permissionBehavior === "allow" &&
      permissionDecision.decision === "ask" &&
      !permissionDecision.alwaysAsk
    ) {
      return {
        ...permissionDecision,
        allowed: true,
        decision: "allow",
        escalated: false,
        mode,
        reason: hookResult.hookPermissionDecisionReason ?? "Tool was allowed by PreToolUse hook",
        ruleId: "hook.PreToolUse.allow",
      };
    }
    if (hookResult.permissionBehavior === "ask" && permissionDecision.decision === "allow") {
      return {
        ...permissionDecision,
        allowed: false,
        decision: "ask",
        escalated: true,
        mode,
        reason:
          hookResult.hookPermissionDecisionReason ?? "Tool requires approval by PreToolUse hook",
        ruleId: "hook.PreToolUse.ask",
      };
    }
    return permissionDecision;
  }
  ```
  So a PreToolUse hook can escalate allow→ask and de-escalate ask→allow (except `alwaysAsk`), but cannot upgrade a `deny`; `updatedInput` flows separately through the tool-input path.
* **PermissionRequest** — the hook becomes a competing *responder* and its verdict is translated into a broker result (**`hook-flow.ts:56-119`**):
  ```ts
    const decision = hookResult.permissionRequestResult;
    if (hookResult.preventContinuation) {
      return { decision: "deny", reason: hookResult.stopReason ?? "Denied by PermissionRequest hook" };
    }
    if (!decision) {
      if (hookResult.permissionBehavior === "deny") {
        return { decision: "deny", reason: hookResult.stopReason ?? "Denied by PermissionRequest hook" };
      }
      return hookResult.permissionBehavior === "allow"
        ? { decision: "allow", reason: "Allowed by PermissionRequest hook" }
        : undefined;
    }
    if (decision.behavior === "deny") {
      return { decision: "deny", reason: decision.message ?? "Denied by PermissionRequest hook" };
    }
    const permissionUpdates = decision.permissionUpdates ?? decision.updatedPermissions;
    return decision.updatedInput === undefined
      ? { decision: "allow", permissionUpdates, reason: "Allowed by PermissionRequest hook" }
      : { decision: "modify", modifiedInput: decision.updatedInput, permissionUpdates,
          reason: "Allowed with modified input by PermissionRequest hook" };
  ```
  `undefined` = "forfeit", leaving the broker as the only responder (`permission-responder-race.ts:77-87`).
* **Stop** — a blocking Stop hook does not kill the turn; it feeds the continuation loop: `stopShouldContinue === true && additionalContexts.length > 0 && continuationCount < MAX_STOP_HOOK_CONTINUATIONS` (`runtime/methods/hooks.ts:120-129`, `MAX_STOP_HOOK_CONTINUATIONS = 3` at `:10`); `continue: true` from a Stop hook is what sets `stopShouldContinue`.
* **Process exit code 2** is the CLI convention for "block": it is converted into a block payload per event (`configured-runner-callback.ts:195-238`), e.g. for PreToolUse `permissionDecision: "deny"`, for PermissionRequest `decision.behavior = "deny"`, for Stop `decision: "block"`, otherwise `continue: false`.

### B9.5 Modifying (`updatedInput`)

Accepted on PreToolUse and on PermissionRequest (`contracts/src/hooks/index.ts:169, 155`; `output.ts:125-127`, `132`). On the PermissionRequest path, the modification is normalized, schema-validated, and **permissions are re-evaluated** before it is honoured (`permission-flow.ts:240-267`, §A4.6); on PreToolUse it flows through the same normalization pipeline (consumed by the executor before `resolveToolPermission`).

### B9.6 `additionalContext`

Collected into `HookRunResult.additionalContexts` (`output.ts:48-49`, `:128`, `:140`) and injected into model history as a bounded system reminder:

**`core/src/runtime/methods/hooks.ts:106-140`**
```ts
export function injectHookAdditionalContextIntoMessageHistory(
  this: AgentRuntimeInternal,
  eventName: HookEventNameType,
  additionalContexts: readonly string[],
): RuntimeMessageEntry | undefined {
  if (additionalContexts.length === 0) return undefined;
  const entry = systemReminderAttachmentEntry(
    "hook_context",
    formatLifecycleHookAdditionalContextBody(eventName, additionalContexts),
  );
  this.messageHistory.addEntries([entry]);
  return entry;
}
...
const HOOK_CONTEXT_MAX_CHARS = 24_000;
const HOOK_PREVIEW_MAX_CHARS = 4_000;
...
function formatLifecycleHookAdditionalContextBody(
  eventName: HookEventNameType,
  additionalContexts: readonly string[],
): string {
  const body = additionalContexts.map((context, index) => `#${index + 1}\n${context}`).join("\n\n");
  return truncateForHook(
    [`${eventName} hook additional context: `, body].join("\n"),
    HOOK_CONTEXT_MAX_CHARS,
  );
}
```
The built-in session-mailbox hook is the in-tree example of lifecycle-context injection (`core/src/hooks/session-mailbox.ts:11-59`), including a `continue: true` on `Stop` to keep the turn alive and per-message enqueueing on `PostToolUse`.

### B9.7 Diagnostics vs. decisions

**`core/src/hooks/types.ts:23-46`**
```ts
/**
 * 配置 Hook 的执行器可以附带 stderr 等诊断；这些信息只进入生命周期事件，
 * 不参与 Hook 决策，也不会作为 Hook JSON 输出暴露给模型。
 */
export interface HookCallbackDiagnostics {
  errorMessage?: string;
  stderrPreview?: string;
  stdoutPreview?: string;
}

export interface HookCallbackResult {
  kind: "hookCallbackResult";
  diagnostics?: HookCallbackDiagnostics;
  output?: HookJSONOutput;
}
```
Diagnostics are only surfaced when a hook *blocked* (`runner.ts:184`: `const safeDiagnostics = blocked ? sanitizeHookDiagnostics(diagnostics) : undefined;`), and are redacted/size-capped (`runner.ts:441-458`).

---

## B10. How hooks are CONFIGURED

### B10.1 File locations

| Source | Path | Discovery |
|---|---|---|
| User settings | `~/.zcode/cli/config.json` (`permission`-style `hooks` root) | `DEFAULT_CONFIG_FILE = "config.json"` + `DEFAULT_BASE_DIR = "~/.zcode/cli"`, `adapters/src/config/file-config.adapter.ts:62-63, 79-88` |
| Project settings (1) | `<dir>/zcode.json` | `buildWorkspaceHookCandidatePaths`, `packages/shared/src/workspace-hook-config.ts:174-179` |
| Project settings (2) | `<dir>/.zcode/config.json` | same |
| Explicit project config | `--project-config <path>` (kind `"explicit"`) | `discoverWorkspaceHookConfigPaths` `:207-221` |
| Plugin | `<pluginRoot>/hooks/hooks.json`, or `manifest.hooks` (string path or inline object) | `STANDARD_HOOKS_PATH = join("hooks", "hooks.json")`, `adapters/src/plugins/hook-sources.ts:14, 33-42, 60-108` |
| Trust store (not a hook definition) | `<~\|storage.dir>/security/workspace-hook-trust-v1.json` | `SECURITY_DIRECTORY = "security"`, `TRUST_STORE_FILE = "workspace-hook-trust-v1.json"`, `adapters/src/storage/workspace-hook-trust-store.ts:20-21, 128-142` |

Project discovery walks **up** from the working directory to the git-worktree root (`packages/shared/src/workspace-hook-config.ts:335-357`), yielding config directories root-first (`:304, :340`). Canonical-path dedupe guarantees an explicitly-passed file that is also auto-discovered appears once, preserving `discoveryOrder` because it is a digest input (`:193-205, 181-192`).

### B10.2 The settings JSON shape and parser

**`apps/zcode-cli/packages/adapters/src/config/schema.ts:256-305`** (the schema wired into the settings file):
```ts
  .object({
    matcher: z.string().min(1).optional(),
    hooks: z.array(z.discriminatedUnion("type", [hookProcessSchema, hookCommandSchema])).min(1),
  })
  .strict();

const hooksSchema = z
  .object({
    enabled: z.boolean().optional(),
    timeoutMs: positiveNumberSchema.optional(),
    maxOutputBytes: positiveNumberSchema.optional(),
    events: z
      .object({
        SessionStart: z.array(hookMatcherSchema).optional(),
        UserPromptSubmit: z.array(hookMatcherSchema).optional(),
        PreToolUse: z.array(hookMatcherSchema).optional(),
        PermissionRequest: z.array(hookMatcherSchema).optional(),
        PostToolUse: z.array(hookMatcherSchema).optional(),
        PostToolUseFailure: z.array(hookMatcherSchema).optional(),
        Stop: z.array(hookMatcherSchema).optional(),
      })
      .strict()
      .optional(),
  })
  .strict();

export const ZCodeConfigFileSchema = z
  .object({
    $schema: z.string().optional(),
    modelStream: modelStreamSchema.optional(),
    permission: permissionSchema.optional(),
    ...
    hooks: hooksSchema.optional(),
  })
  .passthrough();
```
carried into the runtime patch at `adapters/src/config/schema.ts:421`:
```ts
  if (parsed.hooks) config.hooks = parsed.hooks;
```

The *workspace/trust* side has its own (field-identical, `.passthrough()`-on-hook) schema:

**`packages/shared/src/workspace-hook-config.ts:24-80`**
```ts
export const workspaceHookProcessConfigSchema = z
  .object({
    type: z.literal("process"),
    command: z.string().min(1),
    enabled: z.boolean().optional(),
    args: z.array(z.string()).optional(),
    timeoutMs: positiveNumberSchema.optional(),
    statusMessage: z.string().min(1).optional(),
  })
  .passthrough();

export const workspaceHookCommandConfigSchema = z
  .object({
    type: z.literal("command"),
    command: z.string().min(1),
    enabled: z.boolean().optional(),
    async: z.boolean().optional(),
    shell: z.union([z.literal(true), z.string().min(1)]).optional(),
    timeout: positiveNumberSchema.optional(),
    timeoutMs: positiveNumberSchema.optional(),
    statusMessage: z.string().min(1).optional(),
  })
  .passthrough();

export const workspaceHookMatcherConfigSchema = z
  .object({
    matcher: z.string().min(1).optional(),
    hooks: z.array(z.discriminatedUnion("type", [
          workspaceHookProcessConfigSchema,
          workspaceHookCommandConfigSchema,
        ])).min(1),
  })
  .strict();

export const workspaceHooksConfigSchema = z
  .object({
    enabled: z.boolean().optional(),
    timeoutMs: positiveNumberSchema.optional(),
    maxOutputBytes: positiveNumberSchema.optional(),
    events: z.object({ SessionStart: ..., Stop: ... }).strict().optional(),
  })
  .strict();
```
File reading (`packages/shared/src/workspace-hook-config.ts:250-276`): parse JSON, skip files where `value.hooks === undefined`, `workspaceHooksConfigSchema.parse(value.hooks)`, collect read/parse failures into `errors[]` rather than throwing.

Runtime (execution-side) shape and defaults:

**`contracts/src/hooks/index.ts:303-348, 425-430`**
```ts
export interface HookCommandConfig {
  async?: boolean;
  command: string;
  enabled?: boolean;
  plugin?: HookPluginContext;
  shell?: true | string;
  /** Runtime-only provenance; the public config schema deliberately strips this field. */
  source?: HookConfigSource;
  statusMessage?: string;
  timeout?: number;
  timeoutMs?: number;
  type: "command";
}

export interface HookProcessConfig {
  args?: string[];
  command: string;
  enabled?: boolean;
  plugin?: HookPluginContext;
  source?: HookConfigSource;
  statusMessage?: string;
  timeoutMs?: number;
  type: "process";
}
...
export interface HookMatcherConfig {
  hooks: HookConfig[];
  matcher?: string;
}

export interface HooksRuntimeConfig {
  enabled: boolean;
  events: Partial<Record<HookEventName, HookMatcherConfig[]>>;
  maxOutputBytes: number;
  timeoutMs: number;
}
...
export const DefaultHooksRuntimeConfig: HooksRuntimeConfig = {
  enabled: false,
  events: {},
  maxOutputBytes: 32768,
  timeoutMs: 60000,
};
```

### B10.3 Merge / enable semantics

The executable merge is user-level only for project files — project `hooks` are deliberately removed from the executable patch and kept as a *candidate*:

**`apps/zcode-cli/packages/adapters/src/config/project-config.adapter.ts:61-70, 120-127`**
```ts
  const hooks = result.loaded ? result.config.hooks : undefined;

  if (hooks) {
    diagnostics.push({
      code: "config_project_hooks_pending_trust",
      filePath: result.path,
      message: "Project hooks are pending workspace trust and remain blocked",
      path: "hooks",
      severity: "warning",
    });
  }
...
function normalizeProjectConfig(config: RuntimeConfigPatch, baseDir: string): RuntimeConfigPatch {
  const normalized: RuntimeConfigPatch = config.hooks
    ? (() => {
        const { hooks: _hooks, ...safeConfig } = config;
        // Project Hook declarations are retained only in the immutable candidate side-channel.
        // The executable RuntimeConfigPatch remains hook-free until a later admission phase.
        return safeConfig;
      })()
    : { ...config };
```
and the candidate becomes a single immutable snapshot (`adapters/src/config/config-factory.ts:212-228`):
```ts
  const workspaceHookRuntimeRoot = resolveWorkspaceHookRuntimeRoot([
    DefaultRuntimeConfig.hooks,
    userConfigResult.config.hooks,
    ...projectSummary.hookCandidates.map((candidate) => candidate.hooks),
    envConfig.hooks,
    options.cliOverrides?.hooks,
  ]);
  ...
  // Project Hook events stay outside this executable merge. The snapshot above is the only
  // Phase 1 side-channel and cannot be consumed by configured-runner.
  const merged = mergeConfigs(...configs);
```
User-level hook merging across scopes never lets one layer disable another (`adapters/src/config/config-merger.ts:170-196`):
```ts
  // project hooks 不能整体覆盖 user hooks，空的 project `enabled:false` 也不能
  // 关闭 user hooks。每个配置文件只控制自己的事件，因此仅在该来源启用时追加事件，
  // effective enabled 由任一已启用来源决定。
  if (next.enabled !== false) {
    for (const [eventName, matchers] of Object.entries(next.events ?? {}) as Array<
      [HookEventName, HookMatcherConfig[]]
    >) {
      if (!matchers) continue;
      events[eventName] = [...(events[eventName] ?? []), ...matchers];
    }
  }

  return {
    ...current,
    ...next,
    enabled: current?.enabled === true || next.enabled === true,
    events,
  };
```
Plugin hooks are force-enabled and appended (`bootstrap/src/app/runtime-config.ts:247-276`):
```ts
function mergeRuntimeHooks(
  base: HooksRuntimeConfig | undefined,
  pluginHooks: Partial<Record<HookEventName, HookMatcherConfig[]>> | undefined,
): HooksRuntimeConfig | undefined {
  if (!pluginHooks || Object.values(pluginHooks).every((matchers) => !matchers?.length)) {
    return base;
  }
  ...
  return {
    enabled: true,
    events: mergedEvents,
    maxOutputBytes: base?.maxOutputBytes ?? 32768,
    timeoutMs: base?.timeoutMs ?? 60000,
  };
}
```
Plugin contract for that input (**`contracts/src/plugins/index.ts:251`**, grep-only): `hooks: Partial<Record<HookEventName, HookMatcherConfig[]>>;`.

Per-hook/per-source gates:

**`apps/zcode-cli/packages/core/src/hooks/configured-runner.ts:15-28, 53-54`**
```ts
export function createConfiguredHookRunner(
  options: ConfiguredHookRunnerOptions,
): HookRunner | undefined {
  const configuredHooks = options.config.enabled ? createConfiguredHookRegistrations(options) : [];
  const workspaceHooks = createWorkspaceHookRegistrations(options);
  const hooks = insertWorkspaceHooks(configuredHooks, workspaceHooks);
  if (hooks.length === 0) return undefined;
  return new InMemoryHookRunner({
    defaultTimeoutMs: options.config.timeoutMs,
    emitEvent: options.emitEvent,
    hooks,
    logger: options.logger,
  });
}
...
  return matcherConfig.hooks.flatMap((hook, hookIndex) => {
    if (hook.enabled === false) return [];
```

**`packages/shared/src/workspace-hook-config.ts:154-167`**
```ts
export function resolveWorkspaceHookConfiguredGates(input: {
  sourceEnabled?: boolean;
  declarationEnabled?: boolean;
  runtimeHooksEnabled: boolean;
}) {
  const sourceRootEnabled = input.sourceEnabled !== false;
  const declarationEnabled = input.declarationEnabled !== false;
  return {
    sourceRootEnabled,
    declarationEnabled,
    runtimeHooksEnabled: input.runtimeHooksEnabled,
    configuredEnabled: sourceRootEnabled && declarationEnabled && input.runtimeHooksEnabled,
  };
}
```

`HookRegistration` (what config compiles into) and `source` naming:

**`core/src/hooks/types.ts:48-64`**
```ts
export interface HookRunAdmissionDecision {
  allowed: boolean;
  reasonCode?: string;
  skipLifecycle?: boolean;
}

export interface HookRegistration {
  admission?: (input: HookInput) => HookRunAdmissionDecision;
  async?: boolean;
  callback: HookCallback;
  descriptor?: HookExecutionDescriptor | ((input: HookInput) => HookExecutionDescriptor);
  event: HookEventName;
  matcher?: string;
  source?: string;
  sourceKind?: HookSourceKind;
  timeoutMs?: number;
}
```
**`configured-runner.ts:53-69`** assigns `source` as `` `plugin.${hook.plugin.id}.${event}.${matcherIndex}.${hookIndex}` `` or `` `config.${event}.${matcherIndex}.${hookIndex}` `` (project hooks: `` `project.${entry.reviewItemId}` ``, `:95`), and `sourceKind` as `plugin | hook.source?.kind ?? "internal" | "project"`.

### B10.4 Matchers

**`apps/zcode-cli/packages/core/src/hooks/output.ts:98-112`**
```ts
export function matchesHookMatcher(
  matchValue: string | undefined,
  matcher: string | undefined,
): boolean {
  if (!matcher || matcher === "*") return true;
  if (!matchValue) return false;
  if (/^[a-zA-Z0-9_|]+$/u.test(matcher)) {
    return matcher.split("|").includes(matchValue);
  }
  try {
    return new RegExp(matcher).test(matchValue);
  } catch {
    return false;
  }
}
```
Semantics, precisely: absent or `"*"` matches everything; an identifier-shaped string (`[A-Za-z0-9_|]+`) is a **pipe-separated exact list**; anything else is compiled as a **JavaScript RegExp** (unanchored, so substring matching) and an invalid pattern silently matches nothing.

**`apps/zcode-cli/packages/core/src/hooks/runner-helpers.ts:36-48`**
```ts
export function matchesAnyHookMatcher(
  options: HookRunOptions,
  matcher: string | undefined,
): boolean {
  if (!matcher) return true;
  const matchValues = [
    ...(options.matchValues ?? []),
    ...(options.matchValue ? [options.matchValue] : []),
  ];

  if (matchValues.length === 0) return true;
  return [...new Set(matchValues)].some((matchValue) => matchesHookMatcher(matchValue, matcher));
}
```
Note: for the lifecycle events (`SessionStart`, `UserPromptSubmit`, `Stop`, and the raw `PreToolUse` call shape) the runner is invoked **without** `matchValue` in some paths, in which case a non-empty matcher matches nothing — except `"*"`, which is still short-circuited to `true` by `matchesHookMatcher`. Tool events supply `matchValue: toolName` plus alias values:

**`apps/zcode-cli/packages/core/src/tool/compat.ts:6-23`**
```ts
const hookMatcherAliasesByToolName = new Map<string, readonly string[]>([
  [AGENT_TOOL_NAME, [TASK_TOOL_NAME]],
  [TASK_TOOL_NAME, [AGENT_TOOL_NAME]],
  ["ApplyPatch", ["Write", "Edit"]],
]);
...
export function hookMatcherToolNamesForTool(toolName: string): readonly string[] {
  const aliases = hookMatcherAliasesByToolName.get(toolName) ?? [];
  if (aliases.length === 0) return [toolName];

  const values = new Set<string>([toolName]);
  for (const alias of aliases) values.add(alias);
  return [...values];
}
```
`SessionStart` matches on its `source` instead (`runtime/methods/hooks.ts:46`): `{ matchValue: source, signal }`.

### B10.5 Command/process execution shape

Both `command` (shell) and `process` (argv) hooks receive the input as **compat JSON on stdin**, with an extra temp transcript file, and a plugin env overlay (`core/src/hooks/configured-runner-input.ts:14-68`):
```ts
  const compatible: Record<string, unknown> = {
    ...input,
    agent_type: input.agentName,
    hook_event_name: input.hookEventName,
    permission_mode: input.mode,
    session_id: input.sessionId,
  };
  const tempDir = await mkdtemp(join(tmpdir(), "zcode-hook-"));
  const transcriptPath = join(tempDir, "transcript.jsonl");
  await writeFile(transcriptPath, formatTranscript(input), "utf8");
  compatible.transcript_path = transcriptPath;
  compatible.transcriptPath = transcriptPath;

  if ("toolName" in input) {
    // 这里只补无损 alias，继续保留 ZCode camelCase 字段作为内部主契约。
    compatible.tool_name = input.toolName;
    compatible.tool_input = input.toolInput;
    compatible.tool_use_id = input.toolCallId;
  }
```
Env overlay (`configured-runner-input.ts:74-98`): `CLAUDE_CODE_SESSION_ID`, `CLAUDE_PROJECT_DIR`, `CLAUDE_SESSION_ID`, `ZCODE_PROJECT_DIR`, `ZCODE_SESSION_ID`, plus `CLAUDE_PLUGIN_DATA/ROOT`, `ZCODE_PLUGIN_ID/NAME/ROOT` when a plugin context exists. `${…}` interpolation is supported for those names; `${CLAUDE_SKILL_DIR}`/`${ZCODE_SKILL_DIR}` deliberately throw `ConfigurationError` (`:120-139`). stdout is parsed as JSON only when it starts with `{`; invalid JSON is treated as diagnostic text, but JSON that fails `HookJSONOutputSchema` **throws** (`configured-runner-callback.ts:165-193`).

---

## B11. Execution semantics

### B11.1 Selection, order, insertion point

**`core/src/hooks/runner.ts:52-55`**
```ts
  async run(input: HookInput, options: HookRunOptions = {}): Promise<HookRunResult> {
    const matchingHooks = this.hooks.filter(
      (hook) => hook.event === input.hookEventName && matchesAnyHookMatcher(options, hook.matcher),
    );
```
Order is registration order, which is: configured user/internal hooks first in `Object.entries(config.events)` order → matcher index → hook index (`configured-runner.ts:35-42, 47-72`), with plugin hooks appended by `mergeRuntimeHooks` and **project hooks spliced in before the first non-`user` registration of the same event** (`configured-runner.ts:174-191`):
```ts
function insertWorkspaceHooks(
  configured: HookRegistration[],
  workspace: HookRegistration[],
): HookRegistration[] {
  const result = [...configured];
  for (const event of new Set(workspace.map((registration) => registration.event))) {
    const eventWorkspace = workspace.filter((registration) => registration.event === event);
    const firstNonUserIndex = result.findIndex(
      (registration) => registration.event === event && registration.sourceKind !== "user",
    );
    if (firstNonUserIndex < 0) {
      result.push(...eventWorkspace);
    } else {
      result.splice(firstNonUserIndex, 0, ...eventWorkspace);
    }
  }
  return result;
}
```
Pre-pass over matching hooks computes `skipLifecycle` exclusion and the client-visible `hookCount` (`runner.ts:61-80`) — deliberately *not* reused as the authorization decision:

**`runner.ts:65-69`**
```ts
    // 注意：这里的 admission 结果只用于 skipLifecycle 剔除与 hookCount 计算——
    // 二者取决于「配置是否启用」这一稳定属性。allowed 授权决定绝不能沿用本阶段的
    // 缓存：一次事件匹配多个顺序执行的 Hook 时，前序 Hook 运行期间发生的
    // revoke / policy 收紧 / trust store reload 必须对尚未开始的 Hook 立即生效，
    // 因此循环内在每个 Hook 实际 dispatch 前重新调用 resolveHookRunAdmission。
```

### B11.2 Parallelism: sequential by default, `async` hooks detached

**`runner.ts:82-90, 125-154`**
```ts
    for (const [runtimeIndex, { hook }] of participatingHooks.entries()) {
      const descriptor = resolveHookDescriptor(hook, this.defaultTimeoutMs, input);
      const hookIndex = descriptor.clientVisible ? clientVisibleHookIndex++ : runtimeIndex;
      const hookRunId = crypto.randomUUID();
      const startedAt = Date.now();
      // dispatch 前重新解析授权决定。预扫描结果可能已过时——前序 Hook
      // 执行期间用户 revoke、管理员收紧 policy 或 trust store reload 都会改变
      // 结论。security revision 的价值就在执行边界重验，授权决定不得缓存。
      const admission = resolveHookRunAdmission(hook, input, this.logger);
      if (!admission.allowed) { ... continue; }
      ...
      if (hook.async) {
        // async command 的生命周期独立于当前 turn；输出不得反向改变已经继续执行的动作。
        void this.runBackgroundHook({...}).catch(...);
        continue;
      }
```
So hooks within one event are **strictly sequential** (`await this.runCallbackWithTimeout(...)` inside a `for…of`, `runner.ts:157`), and only command hooks configured with `async: true` run detached (`configured-runner.ts:143`: `async: input.hook.type === "command" && input.hook.async === true`) — their output is never merged into the `HookRunResult` (`runBackgroundHook`, `runner.ts:254-332`, only emits lifecycle events).

### B11.3 Timeout constants and values

**`runner.ts:41-46, 334-379`**
```ts
  constructor(options: HookRunnerOptions = {}) {
    this.defaultTimeoutMs = options.defaultTimeoutMs ?? 60000;
    ...
  }
...
  private async runCallbackWithTimeout(
    hook: HookRegistration,
    input: HookInput,
    hookIndex: number,
    parentSignal?: AbortSignal,
  ): Promise<HookJSONOutput | HookCallbackResult | void> {
    const timeoutMs = hook.timeoutMs ?? this.defaultTimeoutMs;
    const controller = new AbortController();
    const unlink = linkAbortSignal(parentSignal, controller);
    let timer: ReturnType<typeof setTimeout> | undefined;

    try {
      return await new Promise<HookJSONOutput | HookCallbackResult | void>((resolve, reject) => {
        if (controller.signal.aborted) {
          reject(createHookCancelledError());
          return;
        }

        timer = setTimeout(() => {
          controller.abort(HOOK_TIMEOUT_ABORT_REASON);
          reject(createHookTimeoutError(timeoutMs));
        }, timeoutMs);
        timer.unref?.();
        ...
```
Values and precedence:

| Value | Where |
|---|---|
| `60000` ms — `InMemoryHookRunner` fallback | `runner.ts:42`; configured runner passes `options.config.timeoutMs` (`configured-runner.ts:23`) whose default is `DefaultHooksRuntimeConfig.timeoutMs` = `60000` (`contracts/src/hooks/index.ts:429`) |
| `32768` bytes — `maxOutputBytes` default | `contracts/src/hooks/index.ts:428`; also `DEFAULT_WORKSPACE_HOOK_MAX_OUTPUT_BYTES` (`packages/shared/src/workspace-hook-config.ts:8`); applied as `outputLimit: { maxBufferBytes, maxInlineBytes, persistOutput: "none" }` (`configured-runner-callback.ts:47-51, 92-96`) |
| per-hook `timeoutMs` / `timeout` | `packages/shared/src/workspace-hook-config.ts:113-123` — **`timeout` is seconds** for `command` hooks and only used if `timeoutMs` is absent: |
```ts
export function resolveWorkspaceHookTimeoutMs(
  hook: Pick<WorkspaceHookDefinition, "type" | "timeoutMs"> & { timeout?: number },
  defaultTimeoutMs: number,
): number {
  const timeoutMs =
    hook.timeoutMs ??
    (hook.type === "command" && hook.timeout !== undefined
      ? hook.timeout * 1000
      : defaultTimeoutMs);
  return Math.max(1, Math.round(timeoutMs));
}
```
| project-hook resolved timeout / output cap | carried per entry as `resolvedTimeoutMs` / `resolvedMaxOutputBytes` (`contracts/src/hooks/workspace-hook-trust.ts:207-208`), consumed at `configured-runner.ts:93, 97` |
| workspace-hook **review** deadline | `10 * 60 * 1000` (`contracts/src/hooks/workspace-hook-trust.ts:4`), applied at `bootstrap/src/app/workspace-hook-review-request.ts:54` |
| `INTERACTION_REQUEST_REANNOUNCE_INTERVAL_MS` | `1_000` — permission interaction, not a hook (`bootstrap/src/zcode-protocol/interaction-broker.ts:41`) |

Timeouts map to `HookOutcome.TimedOut`; cancellation to `Cancelled`:

**`runner-helpers.ts:66-83`**
```ts
export function createHookTimeoutError(timeoutMs: number): Error {
  return createCoreError(CoreErrorType.ToolTimeout, `Hook timed out after ${timeoutMs}ms`, {
    recoverable: true,
  });
}

export function createHookCancelledError(): Error {
  return createCoreError(CoreErrorType.ToolCancelled, "Hook execution cancelled", {
    recoverable: true,
  });
}

export function resolveHookFailureOutcome(error: unknown): HookOutcome {
  if (!isCoreError(error)) return HookOutcome.Failed;
  if (error.type === CoreErrorType.ToolTimeout) return HookOutcome.TimedOut;
  if (error.type === CoreErrorType.ToolCancelled) return HookOutcome.Cancelled;
  return HookOutcome.Failed;
}
```
with `HOOK_TIMEOUT_ABORT_REASON = Symbol("hook-timeout")` (`runner-helpers.ts:13`) used to distinguish the timer's abort from a parent abort (`runner.ts:353, 362-364`); parent signals cascade via `linkAbortSignal` (`runner-helpers.ts:50-64`).

### B11.4 Failure handling — never fatal to the turn

**`runner.ts:211-248`**
```ts
      } catch (error) {
        const durationMs = Date.now() - startedAt;
        const outcome = resolveHookFailureOutcome(error);
        const errorMessage = sanitizeHookDisplayText(readHookErrorMessage(error));
        await this.emitHookEvent(
          SessionEventType.HookRunFailed,
          ...
          {
            durationMs,
            errorCode: isCoreError(error) ? error.code : undefined,
            errorMessage,
            outcome,
            stderrPreview: errorMessage,
          },
        );

        this.logger?.warn("Hook execution failed", {...});
      }
```
The loop continues to the next hook; the turn is not aborted. Same for detached hooks (`runner.ts:295-331`, event `hook.run.async_failed`). An admission *gate that itself throws* fails closed and reports the untrusted code:

**`runner-helpers.ts:15-34`**
```ts
export function resolveHookRunAdmission(
  hook: HookRegistration,
  input: HookInput,
  logger?: Logger,
): { allowed: boolean; reasonCode?: string; skipLifecycle?: boolean } {
  if (!hook.admission) return { allowed: true };
  try {
    return hook.admission(input);
  } catch (error) {
    // 安全 gate 自身异常时不能继续创建进程或后台任务。
    logger?.warn("Hook admission gate failed closed", {...});
    return { allowed: false, reasonCode: "workspace_hooks_blocked_untrusted" };
  }
}
```
A blocked admission emits `HookRunBlocked` with `outcome: HookOutcome.Blocked`, `durationMs: 0` (`runner.ts:91-112`), and — when the declaration is config-disabled — is *skipped entirely* rather than surfaced (`runner.ts:73`; `workspace-hook-runtime-admission.ts:189`: `...(!item.configuredEnabled ? { skipLifecycle: true } : {})`).

### B11.5 Lifecycle events and redaction

Every hook run emits `HookRunStarted`, then exactly one of `HookRunCompleted` / `HookRunFailed` / `HookRunBlocked` (`runner.ts:113-123, 186-210, 215-232`), with `hookIndex`, `hookCount`, `hookInvocationId`, `hookRunId`, `requestId`, `toolCallId`, `toolName`, `startedAt`, `durationMs`, `outcome`, `truncated`, `outputBytes`, sanitized `stderrPreview`/`stdoutPreview`/`errorMessage` (`runner.ts:381-419`; payload contract `contracts/src/hooks/index.ts:350-375`). `clientVisible` is `sourceKind !== "internal"` (`display-metadata.ts:20`), and only client-visible hooks consume a `hookIndex` (`runner.ts:84`). All display strings pass through `sanitizeHookDisplayText` (URL credentials, `Authorization:` headers, `token|secret|password|api_key|…` key/value and `--flag value` forms → `••••`, `display-metadata.ts:3-66`); telemetry additionally hashes workspace identities (`workspace-hook-telemetry.ts:88-101`).

---

## B12. Workspace-hook trust / admission / review flow

Project hooks are inert on arrival: `loadProjectConfigFile` parses `hooks` with `workspaceHooksConfigSchema`, records the diagnostic `"Project hooks are pending workspace trust and remain blocked"`, and `normalizeProjectConfig` strips `hooks` from the executable patch so only the immutable candidate survives (`adapters/src/config/project-config.adapter.ts:61-70, 120-127`), while `config-factory.ts:212-228` folds every hook-bearing config layer into one `WorkspaceHookBundleSnapshot` (per-file `sourceFiles` with `discoveryOrder`/`configFileKind`/`editable`, per-declaration `hookDeclarationDigest`, plus root `enabled/timeoutMs/maxOutputBytes`) and states that "Project Hook events stay outside this executable merge". Trust is decided per `(workspaceIdentity, hookDeclarationDigest)` pair: `trustKey` is `` `${workspaceIdentity}\u0000${hookDeclarationDigest}` `` (`workspace-hook-trust-evaluation.ts:66-68`), and `evaluateWorkspaceHookEntry` maps each declaration into a trust state by a strict ladder — `policy.mode === "deny"` → `"blocked_policy"`, corrupt store → `"blocked_untrusted"`, `allow_trusted_only` without a record → `"blocked_policy"`, a persistent record → `"trusted_persistent"`, a revoked key → `"revoked"`, a *stale slot* record (same source/event/matcher/index but different digest) → `"stale_digest"`, otherwise `"pending_trust"` — and derives `admissionClass`/`reasonCode` from the frozen `WORKSPACE_HOOK_STATE_ADMISSION_MAP` (`workspace-hook-trust-evaluation.ts:15-68`, `contracts/src/hooks/workspace-hook-trust.ts:87-130`). Effective runnability is the conjunction of trust, config and policy: `effectiveRunnable: mapped.admissionClass === "admitted" && entry.configuredEnabled && policy.mode !== "deny"` (`workspace-hook-trust-evaluation.ts:54-55`), with the Zod schema re-asserting the same invariant (`contracts/src/hooks/workspace-hook-trust.ts:155-185`: "*hook* cannot be runnable", "a configured-disabled hook cannot be runnable"). Policy itself is a three-state union parsed fail-closed — `deny | user_decides | allow_trusted_only` (`contracts/src/hooks/workspace-hook-trust-store.ts:24-45`), defaulting to `{ mode: "user_decides", policyRevision: "builtin:user-decides:v1" }` (`workspace-hook-policy.ts:4-7`), and a provider that throws is converted into `mode: "deny"` with `reasonCode: "workspace_hooks_blocked_by_policy"` (`workspace-hook-trust-coordinator.ts:155-173`). The `WorkspaceHookTrustCoordinator` holds the durable records in a per-session in-memory mirror plus a monotonically bumped `securityRevision` (`bumpRevision`/`validateSecurityRevision`, `workspace-hook-trust-coordinator.ts:138-186`), only permits persistent mutation when `canMutatePersistentTrust(...)`/`assertPersistentTrustMutationAllowed(...)` see `policy.mode === "user_decides"` (`:54-68`), and turns `revoke(...)` into a tombstone (`revokedKeys.add(key)`) plus a revision bump (`:96-107`). Enforcement sits on top in `WorkspaceHookRuntimeAdmission`, whose `evaluateDispatch` re-checks the snapshot digest *and* the declaration digest, the invalidation flag, activation, feature flag, trust-store bootstrap, and the security revision before finally reading `item.effectiveRunnable`, returning typed `WorkspaceHookReasonCode`s (`workspace_hook_declaration_changed`, `workspace_hooks_snapshot_mismatch`, `workspace_hooks_feature_disabled`, `workspace_hooks_trust_store_corrupt`, …) and never constructing a process on a negative answer (`workspace-hook-runtime-admission.ts:116-191`). The runner consults this port twice per event — once pre-pass for `skipLifecycle`/`hookCount` and again immediately before each dispatch, because "授权决定不得缓存" so a revoke or policy tightening during a preceding hook takes effect for the not-yet-started ones (`runner.ts:61-90`), and any exception from the gate fails closed to `workspace_hooks_blocked_untrusted` (`runner-helpers.ts:24-33`). The interactive review is a per-session **generation registry** (`WorkspaceHookReviewFlowRegistry`): `open` refuses a second `pending` flow unless it is the *same* generation (`sameGeneration` compares `reviewFlowId`, `generation`, `interactionId`, `bundleDigest`), `supersede` requires a strictly greater generation on the same flow and settles the old one as `state: "superseded"` with `outcome.reasonCode: "workspace_hooks_review_superseded"`, `validate`/`resolve` reject any target whose `matchesSnapshot`/`matchesGeneration` fail (`workspace_hooks_snapshot_mismatch` / `workspace_hooks_review_superseded`) and refuse to resolve unknown `reviewItemId`s, and `createFlow` arms a `setTimeout` at `deadlineAt` that settles the flow as `state: "timed_out"` with `reasonCode: "workspace_hooks_interaction_timeout"` (`workspace-hook-review-flow.ts:51-266`), the deadline being `createdAt + WORKSPACE_HOOK_REVIEW_TIMEOUT_MS` = 10 minutes (`contracts/src/hooks/workspace-hook-trust.ts:4`; `bootstrap/src/app/workspace-hook-review-request.ts:54`). A positive decision becomes durable trust records built by `createWorkspaceHookTrustRecords`, which pins the *grant-time* fingerprint of each declaration — `eventAtGrant`, `displayCommandAtGrant`, `sourcePathAtGrant`, `sourceDiscoveryOrderAtGrant`, `matcherAtGrant`, `matcherIndexAtGrant`, `hookIndexAtGrant`, `bundleDigestAtGrant`, `appVersionAtGrant` — so any later edit invalidates trust by digest and re-enters review as `stale_digest` rather than silently inheriting it (`workspace-hook-trust-records.ts:4-34`; schema `packages/shared/src/workspace-hook-trust-store-file.ts:38-58`). Persistence is a single locked JSON file at `join(storageRoot, "security", "workspace-hook-trust-v1.json")` resolved from the user config's `storage.dir` (else `~/.zcode`), and it is parsed **all-or-nothing**: `parseWorkspaceHookTrustStoreContent` returns `{status:"ok"}` or `{status:"invalid"}` for both JSON syntax errors and schema failures, because "文件不可信，必须 fail-closed，不得返回任何部分结果" (`adapters/src/storage/workspace-hook-trust-store.ts:20-21, 128-142`; `packages/shared/src/workspace-hook-trust-store-file.ts:78-99`), and the admission layer converts that into `workspace_hooks_trust_store_corrupt` for every declaration (`workspace-hook-runtime-admission.ts:138-147`, with `safeRefreshEvaluation` failing closed if the coordinator throws during activation, `:280-300`). Runtime registration is then a pure projection of admitted entries into `HookRegistration`s whose `admission` closure re-queries the port by `{hookDeclarationDigest, reviewItemId}`, with `source: "project.<reviewItemId>"`, `sourceKind: "project"`, and per-entry `resolvedTimeoutMs`/`resolvedMaxOutputBytes` (`configured-runner.ts:74-101`), placed *before* non-user hooks of the same event (`insertWorkspaceHooks`, `:174-191`), while all of this is observed through redacted telemetry events `workspace_hook.{feature_disabled,review_request_created,trust_selected,review_timeout,review_superseded,snapshot_mismatch,policy_blocked,trust_store_failure,toggle_failure,config_rebuild_failure,revoked,stale_response}` carrying only digests and short reason codes, never commands, paths or trust payloads (`workspace-hook-telemetry.ts:4-18, 54-86`).

---

# Part C — observations, dead code, and drift risks

1. **`allowMediumRiskInAutoMode` is dead.** Declared and defaulted in `PermissionConfig` (`core/src/permission/service.ts:679, 686`) and populated from settings (`bootstrap/src/app/create-app.ts:346`), but never read — `mode === "auto"` always denies with `mode.auto.unimplemented` (`service.ts:140-147, 339-346`).
2. **`isDestructiveTool` is unreachable in the risk fallback.** `getRiskLevel` checks `isWriteTool` before `isDestructiveTool` (`service.ts:546-552`) and the write set contains `Bash` (`:574`), so a tool that relies on the name-based fallback reports `riskLevel: "medium"` while `destructive` is `true`. Only tools that declare `permission.riskLevel`/`riskLevel` explicitly (via `resolvePermissionCapability`) reach `critical`/`high`.
3. **`requiresApproval(...)` has no callers in `core`** (grep finds only the definition, `service.ts:532-535`); the executor uses `checkPermission` directly.
4. **Permission asks have no timeout in practice.** The timer only arms when `options.timeoutMs !== undefined` (`broker.ts:101`), and nothing in the repo assigns `AgentRuntimeConfig.permissionTimeoutMs` (grep-only: `runtime/types.ts:132`, `runtime-tools.ts:226`, `executor/impl.ts:33`). An unanswered dialog holds the tool call (and its turn) until an abort signal or process exit; the `CoreErrorType.PermissionTimeout` path is reachable only for embedders that set the value.
5. **`suppressOutput` is schema-only.** Declared in `HookJSONOutput`/`HookJSONOutputSchema` (`contracts/src/hooks/index.ts:204, 290`) and never consumed anywhere in `apps/zcode-cli/packages` (grep-only).
6. **`HookInput.additionalContext` on stop** — `systemMessage`/`reason` of a blocking Stop hook are pushed into `additionalContexts` (`output.ts:44-45`), which is what drives the continuation loop; a Stop hook that blocks *without* text produces no continuation (`runtime/methods/hooks.ts:124-128`).
7. **Seven hook points are duplicated across five literal lists** (`contracts/src/hooks/index.ts:7-15, 411-422`; `contracts/src/hooks/workspace-hook-trust.ts:9-17, 188-196`; `packages/shared/src/workspace-hook-config.ts:9-17`; `packages/shared/src/workspace-hook-trust-store-file.ts:24-32`) with no shared constant; adding a point requires editing all of them.
8. **Two rule-evaluation worlds.** `matchesProjectRules` delegates wholesale to `rulePolicy.evaluateRules` when a tool supplies one (`service.ts:246`), so for those tools the generic subject/pattern matching in `service.ts:250-320` never runs — rule semantics for e.g. shell tools live with the tool, not in the permission package.
9. **Hook matcher silent-miss modes.** A non-`*` matcher on an invocation that passes no `matchValue` matches nothing (`matchesAnyHookMatcher`, `runner-helpers.ts:46`), and an invalid regex matches nothing (`output.ts:107-111`) — both without diagnostics.
10. **Session-row `permission?: PermissionRuleset`** exists in the store contract (`session-store.port.ts:164, 189, 264`) but is not part of `checkPermission`'s inputs in core; only the project ruleset (§A3.1) and the in-memory session ruleset (§A3.3) are.
