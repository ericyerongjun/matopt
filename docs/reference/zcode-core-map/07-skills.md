# 07 — SKILLS in ZCode core

Scope: the `Skill` tool handler, the skill catalog context section, the agent-side discovery/load adapter
(`@zcode/adapters/skills`), the desktop-side walker/service, the shared scan policy, the bundled skill pack,
plugin skill roots, and every consumer of the loaded/not-loaded judgement.

Repo paths below are relative to `/Users/eric/projects/ZCode/`; paths under `~/` are absolute on this machine.
All code quotes are verbatim (comments included, comments are Chinese in several files).

> **Two findings up front, because they contradict the usual mental model.**
>
> 1. **`disable-model-invocation` and `allowed-tools` are inert in `SKILL.md`.** Nothing in the repo reads
>    either key out of a skill's frontmatter. `grep -rn 'disable-model-invocation'` matches exactly two files,
>    and both are `SKILL.md` files themselves (`.agents/skills/dep-refs/SKILL.md:4`,
>    `.agents/skills/dogfood/SKILL.md:5`). `allowed-tools` is read only from **custom command** frontmatter
>    (`apps/zcode-cli/packages/adapters/src/commands/index.ts:27,206`) and is *written* by the plugin-command
>    generator (`apps/zcode-cli/packages/adapters/src/plugins/index.ts:836`). Both keys still have one effect on
>    skills: they are outside the safe-key whitelist, so they flip `safeToAutoLoad` to `false`
>    (`apps/zcode-cli/packages/adapters/src/skills/index.ts:21-27,231`) — a flag that is reported to the user
>    (`apps/zcode-cli/packages/cli/src/skills-command.ts:160,228`) and read by nothing else. **A skill marked
>    `disable-model-invocation: true` is still listed in the catalog and is still callable by the model.**
> 2. **The catalog is not a system message and not wrapped in `<available_skills>`.** The agent path renders a
>    plain-text bullet list into a **user-role `<system-reminder>` attachment** with source `skills_listing`
>    (§3). The only `<available_skills>` in the codebase is a *different*, desktop-only mechanism
>    (`packages/services/src/skills/skillsService.ts:264-275`) that injects **whole skill bodies** into a user
>    prompt when the user types `$<name>` — see §3.5.

---

## 1. Discovery: roots, order, scan rules

### 1.1 There are no globs

The only filename literal is `SKILL.md`, matched exactly (case-sensitive) — `packages/shared/src/skill-scan-policy.ts:12`:

```ts
/** 技能定义文件名。 */
export const SKILL_FILE_NAME = "SKILL.md";
```

Candidates are built with `join(root, "SKILL.md")` and `join(root, entry.name, "SKILL.md")`
(`apps/zcode-cli/packages/adapters/src/skills/scan.ts:58,70`), never by pattern matching.

### 1.2 Root resolution and precedence

`apps/zcode-cli/packages/adapters/src/skills/roots.ts:20-59` builds the ordered root list:

```ts
export async function resolveDefaultSkillRoots(
  workingDirectory: string,
  options: SkillRootResolutionOptions = {},
): Promise<SkillRoot[]> {
  const resolvedWorkingDirectory = resolve(workingDirectory);
  const roots: SkillRoot[] = [];
  const includeZcode = options.includeZcodeSkills ?? true;
  const home = options.homeDirectory ?? homedir();
  let priority = 0;
  const nextPriority = () => {
    priority += PRIORITY_STEP;
    return priority;
  };

  for (const extraRoot of options.extraRoots ?? []) {
    roots.push(
      root(
        resolveConfiguredRoot(extraRoot, resolvedWorkingDirectory),
        "project",
        "zcode",
        nextPriority(),
      ),
    );
  }

  if (includeZcode) {
    roots.push(...skillRootsForBase(home, "user", nextPriority));
  }

  const projectDirectories = await resolveProjectSkillDirectories(resolvedWorkingDirectory);
  for (const directory of projectDirectories) {
    if (includeZcode) {
      roots.push(...skillRootsForBase(directory, "project", nextPriority));
    }
  }

  roots.push(...(options.extraResolvedRoots ?? []));

  return roots;
}
```

with the per-base pair (`roots.ts:94-105`):

```ts
function skillRootsForBase(
  baseDirectory: string,
  scope: SkillRoot["scope"],
  nextPriority: () => number,
): SkillRoot[] {
  // 合并而不是 fallback：用户可能同时安装原生 `.zcode` skill 和兼容 `.agents` skill。
  // 同一级别仍保持 `.zcode` 优先，后续同名按 root 顺序解析。
  return [
    root(join(baseDirectory, ZCODE_DIR, SKILLS_DIR), scope, "zcode", nextPriority()),
    root(join(baseDirectory, AGENTS_DIR, SKILLS_DIR), scope, "agents", nextPriority()),
  ];
}
```

`PRIORITY_STEP = 10` (`roots.ts:8`), `ZCODE_DIR = ".zcode"`, `AGENTS_DIR = ".agents"`, `SKILLS_DIR = "skills"`
(`roots.ts:9-11`). Discovery then iterates **ascending by priority**
(`apps/zcode-cli/packages/adapters/src/skills/index.ts:71`: `for (const root of roots.toSorted((a, b) => a.priority - b.priority))`).

| # | Root | Scope / source | Priority | Where |
|---|---|---|---|---|
| 1 | each `skills.roots` entry from config (`~/` expanded, relative resolved against cwd) | `project` / `zcode` | 10, 20, 30 … | `roots.ts:34-43,121-126` |
| 2 | `<home>/.zcode/skills` | `user` / `zcode` | next | `roots.ts:45-47,102` |
| 3 | `<home>/.agents/skills` | `user` / `agents` | next | `roots.ts:103` |
| 4 | `<cwd>/.zcode/skills`, `<cwd>/.agents/skills` | `project` / `zcode`,`agents` | next pair | `roots.ts:49-54` |
| 5 | `<parent>/.zcode/skills`, `<parent>/.agents/skills` … up to **and including the git worktree root** | `project` | next pairs | `roots.ts:61-73` |
| 6 | plugin skill roots | `system` (official plugin) or `user` (other) / `plugin` | 1 000 + | `plugins/index.ts:104,149,672-706` |
| 7 | bundled skill pack `<pack>/skills` | `system` / `bundled` | 1 000 000 | `bootstrap/src/app/bundled-skills.ts:49,79-86` |

The project walk (`roots.ts:61-83`) collects `workingDirectory` and every ancestor until the first directory
containing `.git`; if no `.git` exists anywhere up to `/`, only the working directory is used (`roots.ts:63`).
Consequence: in a git repo, **every** ancestor up to the repo root contributes two skill roots, and in a
monorepo that means a skill in `<repo>/.agents/skills` is visible from any subpackage.

`extraResolvedRoots` are appended after the numeric sequence but carry their own absolute priorities
(1 000+ for plugins, 1 000 000 for bundled), so the effective global order is exactly the table above.
Note the parameter name `includeZcodeSkills` gates *both* `.zcode` and `.agents` roots (`roots.ts:26,45,51`).

Wiring into the runtime (`apps/zcode-cli/packages/bootstrap/src/app/create-app.ts:745-762`):

```ts
      skillPort:
        configResult.config.features.skill && configResult.config.skills.enabled
          ? (options.skillPort ??
            createNodeSkillAdapter({
              extraRoots: configResult.config.skills.roots,
              extraResolvedRoots: [...pluginOutcome.skillRoots, ...bundledSkillRoots],
              disabledPaths: [
                ...collectDisabledPaths(configResult.config.skillOverrides),
                // 动态工作流关闭时不提供 dynamic-workflows 技能：
                // 十个工具都不在场，再让模型读到「怎么写工作流脚本」只会诱导它去调不存在的工具。
                ...(runtimeConfig.dynamicWorkflowEnabled === false
                  ? collectDynamicWorkflowDisabledSkillPaths(bundledSkillRoots)
                  : []),
              ],
            }))
          : undefined,
```

`disabledPaths` comes from `config.skill["<abs path to SKILL.md>"].enable = false` and is matched against both
the literal resolved path **and** its `realpath`, so a symlink alias cannot bypass the switch
(`adapters/src/skills/index.ts:239-248`).

### 1.3 Per-root scan depth and exclusions (agent path)

`apps/zcode-cli/packages/adapters/src/skills/scan.ts:43-74` — **root itself, then exactly one level of
subdirectories**. No recursion on the agent path:

```ts
export async function scanSkillFilesUnderRoot(
  rootPath: string,
  options: ScanSkillFilesOptions = {},
): Promise<string[]> {
  const followSymlinks = options.followSymbolicLinks ?? true;
  // 根自身是链接（含 junction）时，插件扫描直接判空。
  if (!followSymlinks && (await isSymbolicLink(rootPath))) return [];

  const rootInfo = await stat(rootPath).catch((error) => {
    throwIfUnexpectedScanError(error);
    return null;
  });
  if (!rootInfo?.isDirectory()) return [];

  const files: string[] = [];
  const own = join(rootPath, SKILL_FILE_NAME);
  // 根自身无 SKILL.md 是常态，继续扫一层子目录。
  if (await isLoadableSkillFile(own, followSymlinks)) files.push(own);

  const entries = await readdir(rootPath, { withFileTypes: true });
  for (const entry of entries) {
    const walkable = entry.isDirectory() || (followSymlinks && entry.isSymbolicLink());
    if (!walkable) continue;
    if (!shouldWalkSkillDirectoryEntry(entry.name)) continue;
    // 子目录命中的路径必须校验文件真实存在：分类目录（如 skills/engineering/）
    // 不是技能目录，拼出来的路径不存在；parseSkill 本会静默跳过，但计数与
    // 组件枚举都消费该结果，不校验会把「不存在的技能」算进来。
    const candidate = join(rootPath, entry.name, SKILL_FILE_NAME);
    if (await isLoadableSkillFile(candidate, followSymlinks)) files.push(candidate);
  }
  return files;
}
```

So the layout the agent understands is `root/SKILL.md` (a root that *is* a skill, e.g. a plugin manifest
pointing straight at a skill directory) or `root/<name>/SKILL.md`. `root/<group>/<name>/SKILL.md` is invisible
to the agent — that grouping form is only handled by the desktop walker.

### 1.4 Exclusions and depth constants (shared, pure)

`packages/shared/src/skill-scan-policy.ts`:

```ts
export const SKILL_SCAN_EXCLUDED_DIRECTORY_NAMES: ReadonlySet<string> = new Set([
  "node_modules",
  "dist",
  "build",
  "out",
  "target",
  "vendor",
  "coverage",
  ".cache",
  ".next",
  ".turbo",
  ".venv",
  "__pycache__",
]);
```
(`skill-scan-policy.ts:21-34`; the comment at `:14-20` records that recursing into `node_modules` pushed one
Windows `skills.list` to 69–256 s.)

```ts
/**
 * 递归扫描的最大深度（相对扫描根，根自身为 0）。
 *
 * 真实技能布局很浅：`root/<name>/SKILL.md`，分组场景至多
 * `root/<group>/<name>/SKILL.md`。给到 8 层留足冗余，同时作为
 * symlink/junction 形成的超深目录链的兜底刹车。
 */
export const MAX_SKILL_SCAN_DEPTH = 8;
```
(`skill-scan-policy.ts:36-43`.)

```ts
const SKILL_DISCOVERY_DOT_DIR_ALLOWLIST = new Set([".system"]);

/** 判断递归扫描是否应进入某个子目录项。 */
export function shouldWalkSkillDirectoryEntry(entryName: string): boolean {
  if (SKILL_SCAN_EXCLUDED_DIRECTORY_NAMES.has(entryName)) {
    return false;
  }
  if (!entryName.startsWith(".")) {
    return true;
  }
  return SKILL_DISCOVERY_DOT_DIR_ALLOWLIST.has(entryName);
}
```
(`skill-scan-policy.ts:50-61`.) Every dot-directory except `.system` is skipped; `SKILL_SCAN_EXCLUDED_DIRECTORY_NAMES`
and `MAX_SKILL_SCAN_DEPTH` are consumed by the **desktop** recursive walker
(`packages/services/src/skills/skillDiscoveryWalk.ts:33-107`, depth brake at `:79`) and by the sync
skill-sync walker (`packages/services/src/skill-sync/skillSyncService.ts:231,244`). The agent path uses only
`shouldWalkSkillDirectoryEntry` + `SKILL_FILE_NAME`.

### 1.5 Plugin roots

`apps/zcode-cli/packages/adapters/src/plugins/index.ts:672-706`:

```ts
function resolveComponentRoots<T extends CustomCommandRoot | SkillRoot>(
  key: "commands" | "skills",
  input: { diagnostics: PluginDiagnostic[]; loaded: LoadedPlugin },
  priority: number,
  plugin?: HookPluginContext,
): T[] {
  const paths = parsePathList(input.loaded.manifest[key]);
  const defaultPath = join(input.loaded.rootPath, key);
  if (directoryExists(defaultPath)) {
    paths.unshift(key);
  }
  const roots: T[] = [];
  const seenPaths = new Set<string>();
  for (const rawPath of paths) {
    const path = resolveInside(input.loaded.rootPath, rawPath);
    if (!path) {
      input.diagnostics.push({
        code: "plugin_component_path_invalid",
        message: `Plugin ${key} path escapes plugin root: ${rawPath}`,
        path: input.loaded.manifestPath,
        pluginId: input.loaded.id,
        severity: "error",
      });
      continue;
    }
    if (seenPaths.has(path)) continue;
    seenPaths.add(path);
    roots.push({
      path,
      ...(plugin ? { plugin } : {}),
      ...(key === "skills" ? { pluginId: input.loaded.id } : {}),
      priority,
      scope: input.loaded.source === "official" ? "system" : "user",
      source: "plugin",
    } as T);
  }
  return roots;
}
```

* default convention: `<pluginRoot>/skills/` if that directory exists, plus every path in the manifest's
  `skills` field (either may point at a single skill directory or at a collection — see the comment at
  `scan.ts:5-7`);
* declared-but-empty skill paths raise `plugin_skill_root_empty` (`plugins/index.ts:400-444`);
* plugin roots start at `FIRST_PLUGIN_PRIORITY = 1_000` and increment per plugin (`plugins/index.ts:104,149`);
* the plugin name for `qualifiedName` is found by walking **up to 5 levels** from the skill root looking for
  `.zcode-plugin/plugin.json`, `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`,
  `.cursor-plugin/plugin.json` (`adapters/src/skills/index.ts:29-35,423-436`).

**Symlink trust boundary** (`adapters/src/skills/index.ts:136-143`, `scan.ts:16-23,49,64,105-117`): plugin-scope
scanning passes `followSymbolicLinks: false` (root, subdirectory candidate, and `SKILL.md` file are all
rejected if they are symlinks) because plugin content is untrusted; user/project roots keep following symlinks
so `~/.zcode/skills/<name>` symlink imports work.

### 1.6 Dedup and ordering

`adapters/src/skills/index.ts:71-92`: skills are keyed by **absolute path only**; the first root in priority
order wins for a given path; two different files with the same `name` **both** survive (comment at `:80`: path is
the installation identity, not name); `totalDiscovered` counts every successfully parsed file *before* dedup;
the returned array is sorted by `name.localeCompare`. Loading resolves by `skill.name === requestName ||
skill.qualifiedName === requestName` via `find` (`:108,419-421`), i.e. first match in name-sorted order.

A live root scan of `/Users/eric/projects/ZCode` in this checkout therefore yields, in priority order:

```
10  (configured skills.roots — empty by default)
30  ~/.zcode/skills            ← absent on this machine
40  ~/.agents/skills           ← absent on this machine
50  /Users/eric/projects/ZCode/.zcode/skills        ← absent
60  /Users/eric/projects/ZCode/.agents/skills       ← the 8 project skills
…   (no further ancestors: .git is at the repo root)
1000+ plugin roots (…/plugins/cache/zcode-plugins-official/<name>/<version>/skills)
1e6  apps/zcode-cli/packages/bundled-skills/skills → dynamic-workflows
```

`~/.zcode/skills` **does not exist** on this machine, so the user-scope convention referenced throughout the
code (`services/skills/skillsService.ts:128`, `adapters/src/skills/scan.ts:23`, the deletion guards in
`skillsService`) has no on-disk instance here beyond the plugin cache.

---

## 2. `SKILL.md` format and the frontmatter parser

### 2.1 The agent-side parser (adapters) — a flat YAML subset, hand-written

Frontmatter extraction (`apps/zcode-cli/packages/adapters/src/skills/index.ts:280-288`):

```ts
function extractFrontmatter(content: string): string | null {
  const normalized = content.replace(/^\uFEFF/, "");
  if (!normalized.startsWith("---")) return null;
  const lines = normalized.split(/\r?\n/);
  if (lines[0]?.trim() !== "---") return null;
  const endIndex = lines.findIndex((line, index) => index > 0 && line.trim() === "---");
  if (endIndex <= 0) return null;
  return lines.slice(1, endIndex).join("\n");
}
```

Body extraction and use of the body (`:290-297` and `:113-129`):

```ts
function stripFrontmatter(content: string): string {
  const normalized = content.replace(/^\uFEFF/, "");
  if (!normalized.startsWith("---")) return content;
  const lines = normalized.split(/\r?\n/);
  const endIndex = lines.findIndex((line, index) => index > 0 && line.trim() === "---");
  if (endIndex <= 0) return content;
  return lines.slice(endIndex + 1).join("\n");
}
```

```ts
    const maxBytes = request.maxBytes ?? DEFAULT_MAX_SKILL_BYTES;
    const info = await stat(metadata.path);
    const truncated = info.size > maxBytes;
    const buffer = truncated
      ? await readFirstBytes(metadata.path, maxBytes)
      : await readFile(metadata.path);
    const rawContent = buffer.toString("utf8");
    const content = stripFrontmatter(rawContent).trim();
```

The key/value scanner (`:299-340`):

```ts
function parseFlatYaml(
  frontmatter: string,
  path: string,
  diagnostics: SkillDiagnostic[],
): { values: Record<string, string>; keys: string[] } {
  const values: Record<string, string> = {};
  const keys: string[] = [];
  const lines = frontmatter.split(/\r?\n/);

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] ?? "";
    if (line.trim().length === 0 || line.trim().startsWith("#")) continue;
    if (/^\s/.test(line)) continue;

    const separator = line.indexOf(":");
    if (separator <= 0) {
      diagnostics.push({
        code: "skill_invalid_frontmatter",
        severity: "warning",
        message: `Invalid frontmatter line ${index + 1} in ${basename(path)}`,
        path,
      });
      continue;
    }

    const key = line.slice(0, separator).trim();
    const value = line.slice(separator + 1).trim();
    keys.push(key);
    const blockStyle = parseBlockScalarStyle(value);
    if (blockStyle) {
      // Agent 侧只读顶层 `description: >` 会把后续缩进行跳过，
      // 导致 `.agents/skills` 的合法多行触发说明注入给模型时只剩 `>`。
      const block = readBlockScalar(lines, index + 1, blockStyle);
      values[key] = block.value;
      index = block.nextIndex - 1;
    } else {
      values[key] = value;
    }
  }

  return { values, keys };
}
```

Block scalars and scalar cleanup (`:342-417`):

```ts
function parseBlockScalarStyle(value: string): "folded" | "literal" | null {
  if (/^>[+-]?$/.test(value)) return "folded";
  if (/^\|[+-]?$/.test(value)) return "literal";
  return null;
}
```
```ts
function parseScalar(value: string | undefined): string | undefined {
  if (value === undefined) return undefined;
  const trimmed = value.trim();
  if (trimmed.length === 0) return undefined;
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    return trimmed.slice(1, -1).trim();
  }
  return trimmed;
}
```

Properties worth stating plainly:

* Indented lines are skipped entirely unless they follow a `>`/`|` key — so nested `metadata:` maps are
  *recorded as a key* but their children are not parsed, and `metadata` is never read.
* Only `+`/`-` chomping indicators are tolerated by the style regex and they are then ignored — no chomping
  semantics are applied (`readBlockScalar` folds/joins and `.trim()`s); `>2` / `|2` indentation indicators are
  not recognised at all and the value stays the literal string `">2"`.
* No arrays, no flow collections, no multi-document handling, no type coercion — every value is a string.
* `parseScalar` strips one layer of matching quotes.

### 2.2 Every supported key

`apps/zcode-cli/packages/adapters/src/skills/index.ts:183-236` is the authoritative list:

| Key | Read? | Type | Behaviour |
|---|---|---|---|
| `name` | **yes**, required | string | `parseScalar(values.name)`. If frontmatter is **absent** entirely, falls back to `basename(dirname(path))` (`:183-184`). If frontmatter exists but `name` is missing → **error** diagnostic `skill_missing_name`, skill rejected (`:185-193`). |
| `description` | **yes**, required when frontmatter exists | string (block scalar OK) | Missing with frontmatter present → **error** `skill_missing_description`, rejected (`:194-204`). `>1024` chars → **error** `skill_description_too_long`, rejected (`:206-215`). Without frontmatter, description becomes `""`. |
| `when_to_use` | **yes**, optional | string | `whenToUse`; concatenated into the catalog line as `` `${description} - ${whenToUse}` `` (`context/sections/skills.ts:61-63`). Also shown by `zcode skills list/inspect` and the command center. |
| `license` | no | any | Whitelisted only: keeps `safeToAutoLoad` true. |
| `metadata` | no | map | Whitelisted only: keeps `safeToAutoLoad` true (children skipped by the flat parser). |
| `allowed-tools` | **no** | any | Not read, no warning. Flips `safeToAutoLoad` to `false`. |
| `disable-model-invocation` | **no** | any | Not read, no warning. Flips `safeToAutoLoad` to `false`. Does **not** remove the skill from the catalog. |
| any other key (`version`, `homepage`, `model`, …) | no | any | Not read, and deliberately **not** warned about (`:217-218`: third-party/legacy skills carry extension fields). Flips `safeToAutoLoad` to `false`. |

The whitelist and the derived flag (`:21-27`, `:231-235`):

```ts
const SAFE_FRONTMATTER_KEYS = new Set([
  "name",
  "description",
  "when_to_use",
  "license",
  "metadata",
]);
```
```ts
      safeToAutoLoad: parsed.keys.every((key) => SAFE_FRONTMATTER_KEYS.has(key)),
      frontmatterKeys: parsed.keys,
      policy: {
        allowImplicitInvocation: true,
      },
```

`policy.allowImplicitInvocation` is hard-coded `true` for every skill; nothing consumes it.

### 2.3 The desktop-side parser (services) is a different implementation

`packages/services/src/skills/skillsService.ts:296-357` does **not** share the agent parser: it locates
frontmatter with the regex `/^---\n([\s\S]*?)\n---(?:\n|$)/` after CRLF normalisation, runs a real YAML parse
(`parseYaml`), and on failure falls back to "loose" field reading. Two compatibility quirks live only there:

* `splitFrontmatterAndLeakedBody` (`:357-378`) moves a Markdown heading that leaked before the closing `---`
  back into the body;
* `hasYamlUnsafeLooseInlineField` (`:440-…`) detects unquoted values containing `": "` (e.g. Chinese
  `触发场景: …`) and takes the loose path instead of failing YAML parse.

It also reads only `name` and `description`; its `keys` list (`extractLooseFrontmatterKeys`, `:415-432`) feeds the
same unknown-key bookkeeping. Its own cap is `MAX_DESCRIPTION_LENGTH = 1024` (`skillsService.ts:62,934`), matching
the adapter.

### 2.4 One real frontmatter block, verbatim

`~/.zcode/cli/plugins/cache/zcode-plugins-official/document-skills/0.1.4/skills/pdf/SKILL.md:1-10` — a shipped
plugin skill exercising a folded block scalar, nested `metadata`, and `license`:

```
---
name: pdf
metadata:
  author: Z.AI
  version: "1.1"
description: >
  Professional PDF toolkit covering four production workflows: reports, creative visuals,
  academic LaTeX, and existing PDF processing. Routes automatically by document type and
  supports reports, posters, papers, resumes, extraction, merging, splitting, form filling,
  and format conversion.
license: Proprietary. LICENSE.txt has complete terms
---
```

Before `readBlockScalar` existed, this skill's catalog line read `- pdf: >` (that regression is the explicit
subject of the comment at `adapters/src/skills/index.ts:329-331`). The desktop parser's *loose* path is what
handles the same shape when YAML parsing fails.

And the bundled pack's frontmatter, verbatim
(`apps/zcode-cli/packages/bundled-skills/skills/dynamic-workflows/SKILL.md:1-7`) — the only on-disk skill that
uses `when_to_use`:

```
---
name: dynamic-workflows
description: "Use when writing, debugging, or resubmitting a dynamic-workflow script for the CreateWorkflow tool: choosing subagent topology, typing subagent results, fanning out over files or git, gating loops on world.run commands, testing pieces with EvalWorkflowSnippet, planner-reviewer loops, confirming findings before reporting them, report() salvage, the report shape a run returns, publishing artifacts the user opens, and handling a backgrounded run."
when_to_use: "Only for CreateWorkflow scripts. A single delegation or a few independent lookups belong to the Agent tool instead."
---
```

### 2.5 Skill directory layout in practice

The body is free-form Markdown; sibling files are referenced by relative path from `baseDirectory`. Real
examples: `.agents/skills/react-best-practices/rules/*.md` (70 rule files),
`.agents/skills/agent-browser/references/*.md` + `templates/*.sh`,
`.agents/skills/architecture-governance/scripts/context-package.mjs`,
`.agents/skills/dep-refs/agents/openai.yaml`. The conventions ZCode recognises around a skill are only
`SKILL.md` (this directory) and, for plugin skills, the versioned pack layout
`<plugin>/<version>/skills/<name>/SKILL.md`.

> **Gotcha.** The pdf skill's body (and other plugins) reference `$PDF_SKILL_DIR` / `$DOCX_SKILL_DIR`. The
> `Skill` tool expands only `${CLAUDE_SKILL_DIR}` and `${ZCODE_SKILL_DIR}` (§5.1), with braces. `$PDF_SKILL_DIR`
> is expanded by nothing on the CLI/agent path.

---

## 3. How the catalog reaches the model

### 3.1 The exact renderer

`apps/zcode-cli/packages/core/src/context/sections/skills.ts:39-78` (whole function):

```ts
function buildSkillsContent(skills: SkillMetadata[], budget: number): string {
  const lines = [
    "The following skills are available for use with the Skill tool:",
    "",
  ];

  const sortedSkills = [...skills].sort((a, b) =>
    skillDisplayName(a).localeCompare(skillDisplayName(b)),
  );
  const skillLines = sortedSkills.map((skill) => formatSkillLine(skill, MAX_DESCRIPTION_CHARS));
  const full = [...lines, ...skillLines].join("\n");
  if (full.length <= budget) {
    return full;
  }

  const namesOnly = sortedSkills.map(
    (skill) => `- ${skillDisplayName(skill)}${bareAliasSuffix(skill)} (file: ${skill.path})`,
  );
  return [...lines, ...namesOnly].join("\n");
}

function formatSkillLine(skill: SkillMetadata, maxDescriptionChars: number): string {
  const description = skill.whenToUse
    ? `${skill.description} - ${skill.whenToUse}`
    : skill.description;
  const trimmed =
    description.length > maxDescriptionChars
      ? `${description.slice(0, maxDescriptionChars - 1)}...`
      : description;
  return `- ${skillDisplayName(skill)}: ${trimmed}${bareAliasSuffix(skill)} (file: ${skill.path})`;
}

function skillDisplayName(skill: SkillMetadata): string {
  return skill.qualifiedName ?? skill.name;
}

function bareAliasSuffix(skill: SkillMetadata): string {
  return skill.qualifiedName && skill.qualifiedName !== skill.name
    ? ` (also loadable as ${skill.name})`
    : "";
}
```

Rendered shape (normal mode):

```
The following skills are available for use with the Skill tool:

- <qualifiedName|name>: <description[ - when_to_use]>[ (also loadable as <name>)] (file: <abs path>)
- …
```

Rendered shape (over budget): the same header, then name + alias + file only, no descriptions. Sorted by
display name with `localeCompare`. Exactly **one** catalog text exists for main agents and subagents
(`subagent/context-builder.ts:62-95` builds the same `skills_listing` attachment from the same
`buildSkillsSection`), and the section is omitted entirely when there are zero skills
(`context/sections/skills.ts:18-20`).

There is **no XML or Markdown wrapper around this list** and **no "read the skill before acting" sentence inside
it** — the only structural wrapper is the generic `<system-reminder>` added later (§3.3).

### 3.2 The section and its gate

`context/sections/skills.ts:17-37`:

```ts
export function buildSkillsSection(options: SkillsSectionOptions): ContextSection | null {
  if (options.outcome.skills.length === 0) {
    return null;
  }

  const content = buildSkillsContent(
    options.outcome.skills,
    options.metadataBudget ?? DEFAULT_SKILL_METADATA_BUDGET,
  );

  return {
    name: "Skills",
    source: "skills",
    injectionTarget: "meta_user",
    cacheHint: "dynamic",
    chars: content.length,
    tokens: estimateTokens(content),
    content,
    preview: content.slice(0, 100),
  };
}
```

Pushed by the context builder as step 4 of the assembly (`context/builder.ts:175-187`):

```ts
    // 4. Skills appear as a meta user system-reminder, matching provider block layout.
    // guidanceToolNames 是 runtime 当下的
    // 工具表；一个 Skill 工具未注册的工作流子代理被告知「以下技能可经 Skill 工具使用」，
    // 只会让它相信自己有一个没有的工具。表缺席（测试 / 旧调用方）时保持既有行为。
    if (this.config.skills && this.skillToolAvailable()) {
      const skillsSection = buildSkillsSection({
        outcome: this.config.skills,
        metadataBudget: this.config.skillMetadataBudget,
      });
      if (skillsSection) {
        sections.push(skillsSection);
      }
    }
```

with the gate (`builder.ts:225-228`):

```ts
  private skillToolAvailable(): boolean {
    const names = this.config.guidanceToolNames;
    return names === undefined || names.includes(SKILL_TOOL_NAME);
  }
```

`SKILL_TOOL_NAME = "Skill"` (`builder.ts:36-37`). Populated from `this.skillLoadOutcome`, which is discovered
once per runtime at context init (`runtime/methods/context.ts:65`, `discoverSkillsForContext` at `:213-252`,
and a defensive copy exposed as `getSkillCatalog` at `:76-94`).

### 3.3 It is a system-reminder **attachment** in a user-role message — not a system message

The skills section is collected as its own attachment source (`context/builder.ts:279-307`):

```ts
  private assembleMetaUserAttachments(sections: ContextSection[]): ContextMetaUserAttachment[] {
    const attachments: ContextMetaUserAttachment[] = [];

    const skillsContent = buildSkillsMetaUserBody(
      sections.filter(
        (section) => section.injectionTarget === "meta_user" && section.source === "skills",
      ),
    );
    if (skillsContent) {
      attachments.push({
        source: "skills_listing",
        content: skillsContent,
      });
    }
```

`buildSkillsMetaUserBody` returns the content unchanged (`builder.ts:338-345`) — unlike `context_prefix`, the
skills listing gets **no** "As you answer the user's questions…" boilerplate. The attachment type is
`ContextMetaUserAttachmentSource = "skills_listing" | "context_prefix"` (`context/types.ts:73`).

It becomes a runtime attachment entry (`runtime/methods/context-history-entries.ts:7-16` →
`agent/message-history.ts:326-335`):

```ts
export function systemReminderAttachmentEntry(
  source: SystemReminderSource,
  content: string,
): RuntimeAttachmentEntry {
  return {
    kind: "attachment",
    content,
    metadata: systemReminderRuntimeMetadata(source),
  };
}
```

and at request-build time it is rendered as a **role `user`** message wrapped in `<system-reminder>`
(`runtime/helpers/provider-request-messages.ts:184-203`):

```ts
function renderProjectedEntryToModelMessage(
  entry: ProjectedRuntimeMessageEntry,
): ModelInputMessage {
  if (isRuntimeAttachmentEntry(entry)) {
    const source = entry.metadata.source;
    if (!isKnownSystemReminderSource(source)) {
      throw new Error(`Attachment source ${source} is not a system reminder source`);
    }
    const message: ModelInputMessage = {
      role: "user",
      // incoming 载荷也可能含关闭标签；统一在 provider 包装时转义，canonical 原文不变。
      content: wrapSystemReminderForSource(source, entry.content),
    };
```

The wrapper itself (`system-reminder/source.ts:199-211`):

```ts
export function wrapSystemReminder(body: string | readonly string[]): string {
  const content = typeof body === "string" ? body : body.join("\n");
  if (content.length === 0) {
    throw new Error("System reminder body cannot be empty");
  }
  if (SYSTEM_REMINDER_TAG_PATTERN.test(content)) {
    throw new Error("System reminder body must not include nested system-reminder tags");
  }
  return ["<system-reminder>", content, "</system-reminder>"].join("\n");
}
```

So the final provider payload for the catalog is:

```
role: user
<system-reminder>
The following skills are available for use with the Skill tool:

- electron: Automate Electron desktop apps (VS Code, Slack, …) (file: /Users/eric/projects/ZCode/.agents/skills/electron/SKILL.md)
…
</system-reminder>
```

Lifecycle: `skills_listing` is declared a **request prefix** source
(`system-reminder/source.ts:20,91`):

```ts
export const SYSTEM_REMINDER_PREFIX_SOURCES = ["context_prefix", "skills_listing"] as const;
```
```ts
  skills_listing: descriptor("request_prefix", "request_prefix", true, "sr.skills_listing"),
```

Consequences: it is counted as part of the cache prefix (`agent/message-history.ts:271-303`) and it is
**excluded from compaction summarisation** and preserved as prefix
(`runtime/helpers/compact-selection.ts:141-145`, `:284-289`, `:337-349`).

### 3.4 Where the "load before acting" instruction actually lives

Not in the catalog. Three other places:

1. **The `Skill` tool description** (a real tool-definition string, `tool/handlers/skill.ts:95`):
   `- When a skill matches the user's request, this is a BLOCKING REQUIREMENT: invoke the relevant Skill tool BEFORE generating any other response about the task`
2. **Session guidance**, a genuine `system` section (`context/dynamic-sections.ts:60-62`):

```ts
  if (tools.has("Skill") && hasSkills) {
    lines.push("- When the user types `/<skill-name>`, invoke it via Skill. Only use skills listed in the user-invocable skills section \u2014 don't guess.");
  }
```
   (gated in `context/builder.ts:141-149` on `guidanceToolNames` plus `skills.length > 0`; the phrase
   "user-invocable skills section" is a Claude-Code-ism — no such separate section exists, the sentence means
   the catalog above.)
3. **The workflow skill gate**, as a hard error instead of prose (`tool/handlers/workflow-skill-gate.ts:29-37`,
   error code `428`) — §5.4.

### 3.5 The other catalog: desktop-side `$name` activation with `<available_skills>`

`packages/services/src/skills/skillsService.ts:264-275`:

```ts
function buildActivatedSkillsPromptBlock(skills: SkillSummary[]): string {
  return [
    "<available_skills>",
    ...skills.map((skill) =>
      [
        `<activated_skill name="${escapeXmlAttribute(skill.name)}" path="${escapeXmlAttribute(skill.path)}">`,
        skill.body,
        "</activated_skill>",
      ].join("\n"),
    ),
    "</available_skills>",
  ].join("\n");
}
```

driven by `buildPromptContext` (`:1083-1120`), which scans the user's prompt for `$<kebab-name>`
(`collectMentionedSkillNames`, `:245-255`), selects enabled skills whose `name` matches, appends the whole
body block to the prompt, and appends a line to `~/.zcode/v2/skills-audit.log` (`:278-292`). The design comment
at `:1107-1108` is explicit that this is the *only* path that puts full skill bodies in context on the desktop
side, deliberately not a full catalog injection. This is a **services/desktop** mechanism; the CLI agent path
never emits `<available_skills>`.

---

## 4. The `Skill` tool

**Name literal:** `"Skill"` — declared once in the entry (`tool/handlers/skill.ts:81`) and mirrored as a local
constant in two consumers (`context/builder.ts:36-37`, `agent/loaded-skills.ts:11`, with the comment "contracts
没有常量" — there is no shared constant).

**Description (verbatim, `tool/handlers/skill.ts:82-100`):**

```
Execute a skill within the main conversation

When users ask you to perform tasks, check if any of the available skills match. Skills provide specialized capabilities and domain knowledge.

When users reference a "slash command" or "/<something>", they are referring to a skill. Use this tool to invoke it.

How to invoke:
- Set `skill` to the exact name of an available skill (no leading slash). For plugin-namespaced skills use the fully qualified `plugin:skill` form.
- Set `args` to pass optional arguments.

Important:
- Available skills are listed in system-reminder messages in the conversation
- Only invoke a skill that appears in that list, or one the user explicitly typed as `/<name>` in their message. Never guess or invent a skill name from training data; otherwise do not call this tool
- When a skill matches the user's request, this is a BLOCKING REQUIREMENT: invoke the relevant Skill tool BEFORE generating any other response about the task
- NEVER mention a skill without actually calling this tool
- Do not invoke a skill that is already running
- Do not use this tool for built-in CLI commands (like /help, /clear, etc.)
- If you see a <command-name> tag in the current conversation turn, the skill has ALREADY been loaded - follow the instructions directly instead of calling this tool again
```

Note that the description itself states where the catalog appears: *"Available skills are listed in
system-reminder messages in the conversation."*

**Input schema** (`contracts/src/tools/skill.ts:9-31`):

```ts
const SkillCurrentInputSchema = z.object({
  skill: z
    .string()
    .describe("The name of a skill from the available-skills list. Do not guess names."),
  args: z.string().optional().describe("Optional arguments for the skill"),
});

const LegacySkillInputSchema = z.object({
  name: z.string().min(1),
  args: z.string().optional(),
});

export const SkillInputSchema = z
  .union([SkillCurrentInputSchema, LegacySkillInputSchema])
  .transform((input) => ({
    args: input.args,
    skill: "skill" in input ? input.skill : input.name,
  }));
```

* Provider-facing JSON Schema: `SkillInputJsonSchema = toToolJsonSchema(SkillCurrentInputSchema)` (`:31`) —
  `{ type: "object", properties: { skill: string, args?: string }, required: ["skill"] }` (draft 2020-12, refs
  stripped; `contracts/src/tools/json-schema.ts`).
* Runtime validation accepts the legacy `{ name }` shape and normalises it to `{ skill }`.
* `args` is accepted and **never used** by the handler — it is forwarded nowhere.

**Output** (`contracts/src/tools/skill.ts:33-53`):

```ts
export interface SkillStructuredOutput {
  name: string;
  content: string;
  baseDirectory: string;
  truncated: boolean;
}

export type SkillOutput = string | SkillStructuredOutput;

export const SkillStructuredOutputSchema = z
  .object({
    name: z.string(),
    content: z.string(),
    baseDirectory: z.string(),
    truncated: z.boolean(),
  })
  .strict();

export const SkillOutputSchema = z.union([z.string(), SkillStructuredOutputSchema]);
```

The handler always returns the **string** form (the `<skill_content>` block, §5.1).

**Tool entry metadata** (`tool/handlers/skill.ts:78-150`): `readOnly: true`, `destructive: false`,
`concurrentSafe: true`, `timeoutMs: 30_000`, `maxOutputBytes: 100_000`, `sideEffectScope: "session"`,
`riskLevel: "low"`, `needsApproval: false`; permission `"skill"` with `patternSources: ["toolName"]`,
`denyPriority: "beforeAsk"`; `resultBudget` `{ maxInlineBytes: 100_000, maxModelBytes: 100_000, strategy:
"truncate", preview: { maxBytes: 100_000, direction: "head" } }`; `timeout` 30 s with
`allowCallOverride: false`; `cancellation.supported: true`, `cleanup: "none"`, message *"Skill loading was
cancelled before content was returned"*; trace `required: true`, `recordInput/Output: "summary"`.

**Registration condition:** the tool is only registered when the runtime has a `skillPort`
(`runtime/helpers/runtime-tools.ts:52` `includeSkill: Boolean(runtime.skillPort)`; filtering at
`tool/handlers/index.ts:220`). A session with no skill adapter has neither the tool nor the catalog
(`skillPort === undefined` check in `runtime-tools.ts:204`).

---

## 5. What loading actually does to the conversation

### 5.1 The tool result (verbatim template)

`tool/handlers/skill.ts:36-76`:

```ts
  const loaded = await skillPort.loadSkill(
    {
      name: skill,
      workingDirectory: context.workingDirectory,
      maxBytes: MAX_SKILL_BYTES,
      trace: {
        traceId: context.traceId,
        spanId: context.spanId,
        parentSpanId: context.parentSpanId,
        sessionId: context.sessionId,
        turnId: context.turnId,
      },
    },
    { signal: context.abortSignal },
  );

  context.recordSkillTelemetryMetadata?.({
    ...(loaded.metadata.qualifiedName ? { qualifiedName: loaded.metadata.qualifiedName } : {}),
    ...(loaded.metadata.pluginId ? { pluginId: loaded.metadata.pluginId } : {}),
    source: loaded.metadata.source,
  });

  return [
    `<skill_content name="${loaded.metadata.name}">`,
    `# Skill: ${loaded.metadata.name}`,
    "",
    expandSkillContextVariables(loaded.content, loaded.baseDirectory),
    "",
    `Base directory for this skill: ${loaded.baseDirectory}`,
    "Relative paths in this skill are relative to this base directory.",
    loaded.truncated ? "[Skill content truncated]" : "",
    "</skill_content>",
  ]
    .filter((line) => line.length > 0)
    .join("\n");
}

function expandSkillContextVariables(content: string, baseDirectory: string): string {
  // 只有 Skill 工具加载后才有明确的当前 skill 目录，因此变量替换限定在这里完成。
  return content.replace(/\$\{(CLAUDE_SKILL_DIR|ZCODE_SKILL_DIR)\}/gu, baseDirectory);
}
```

Rendered block (truncated case):

```
<skill_content name="dynamic-workflows">
# Skill: dynamic-workflows
<full SKILL.md body, frontmatter stripped>

Base directory for this skill: /Users/eric/projects/ZCode/apps/zcode-cli/packages/bundled-skills/skills/dynamic-workflows
Relative paths in this skill are relative to this base directory.
[Skill content truncated]
</skill_content>
```

Two quirks worth flagging: `.filter((line) => line.length > 0)` deletes the *intended* blank separator lines, so
`# Skill: <name>` is immediately followed by the body; and `${ZCODE_SKILL_DIR}` / `${CLAUDE_SKILL_DIR}` are
replaced with `baseDirectory` **without escaping**, so a skill body containing those tokens anywhere (even
inside a code fence shown to the user) gets the path substituted. `loaded.metadata.name` is interpolated into an
XML-ish attribute with no escaping.

Telemetry carries exactly `{ qualifiedName?, pluginId?, source }` and deliberately nothing else
(`contracts/src/skills/index.ts:59-64`: "不包含正文或 description"). It is recorded on the trace/session
telemetry path via `recordSkillTelemetryMetadata`, not attached to the tool result itself.

### 5.2 How the conversation changes

Loading appends one ordinary **tool-result message** (role `tool`, `toolCallId` linked to the assistant's
`Skill` call) whose content is the `<skill_content>` block above. There is no separate injection channel: the
markup is the tool result, and it stays in the transcript for the rest of the session, subject to the same
compaction/rewind rules as any other tool result. There is **no dedup and no session-level loaded-set** — calling
`Skill` twice for the same name re-reads the file from disk and appends the block a second time. The only guard
is prose in the tool description ("Do not invoke a skill that is already running").

`zcode skills list/inspect` and the desktop skills page read the same `SkillMetadata` straight from the port
(`discoverSkills` / `loadSkill`), not from the conversation.

### 5.3 The one piece of derived state: `sessionHasLoadedSkill`

`agent/loaded-skills.ts` in full (it is 54 lines; the header comment is the design statement):

```ts
// ============================================================
// 「这段历史里加载过某个技能吗」
// ============================================================
// 工作流创作工具的技能门（tool/handlers/workflow-skill-gate.ts）问的是这一句。判据刻意取自
// runtime 的 provider 可见历史，而不是另立一个会话级 Set：历史就是模型此刻记得的东西——
// compaction 把那次 Skill 调用挤出去，技能正文也一起不在了，门理应重新关上；resume / rewind
// 重建历史时，答案随之重建，不需要第二套 hydration。

import type { RuntimeMessageEntry } from "./message-history.js";

const SKILL_TOOL_NAME = "Skill";

/**
 * 历史里是否有一次**成功完成**的 `Skill` 调用加载了 `skillName`。
 *
 * 成功 = assistant 发出的调用有对应的 tool 结果条目且不是错误。只发出没结果（还在跑、被拒）
 * 或结果 `isError` 都不算。技能名同时认当前形 `{ skill }` 与旧形 `{ name }`
 * （contracts 的 SkillInputSchema 两种都收）。
 */
export function sessionHasLoadedSkill(
  entries: readonly RuntimeMessageEntry[],
  skillName: string,
): boolean {
  const pendingCallIds = new Set<string>();
  for (const entry of entries) {
    if (entry.kind === "attachment") continue;
    const message = entry.message;
    if (message.role === "assistant") {
      for (const call of message.toolCalls ?? []) {
        if (call.name === SKILL_TOOL_NAME && skillInputNames(call.input) === skillName) {
          pendingCallIds.add(call.id);
        }
      }
      continue;
    }
    if (
      message.role === "tool" &&
      message.toolCallId !== undefined &&
      pendingCallIds.has(message.toolCallId) &&
      message.isError !== true
    ) {
      return true;
    }
  }
  return false;
}

function skillInputNames(input: unknown): string | undefined {
  if (input === null || typeof input !== "object") return undefined;
  const fields = input as { skill?: unknown; name?: unknown };
  if (typeof fields.skill === "string") return fields.skill;
  if (typeof fields.name === "string") return fields.name;
  return undefined;
}
```

Wired as a probe, not as state (`runtime/helpers/runtime-tools.ts:201-209`):

```ts
    // 工作流创作工具的技能门（tool/handlers/workflow-skill-gate.ts）：按模型此刻看得见的历史回答
    // 「读过技能没有」。只在会话真有 Skill 工具时给探针——没有 skillPort 的会话装不上那个技能，
    // 门若仍然在场就成了一道谁也过不去的墙。
    ...(deps.skillPort === undefined
      ? {}
      : {
          hasLoadedSkill: (skillName: string) =>
            sessionHasLoadedSkill(runtime.messageHistory.borrowReadOnlyRuntimeEntries(), skillName),
        }),
```

**Persistence across turns and resume:** nothing is persisted or hydrated separately. The judgement is
recomputed from the provider-visible `messageHistory` on every call, so it survives turns for free, and on
resume/rewind it is rebuilt from whatever history the hydrator reconstructs — the header comment calls this out
explicitly ("不需要第二套 hydration"). Because the analysis runs over `borrowReadOnlyRuntimeEntries()`, the
answer describes exactly what the model can currently see.

### 5.4 Compaction: the catalog survives, the loaded body does not

Two opposite behaviours, and they are the whole story:

* The **catalog** (the `skills_listing` attachment) is a request-prefix entry, exempt from summarisation
  (`runtime/helpers/compact-selection.ts:141-145`):

```ts
export function getRuntimeEntriesToSummarize(
  entries: readonly RuntimeMessageEntry[],
): RuntimeMessageEntry[] {
  return entries.filter((entry) => !isRuntimeContextPrefixEntry(entry)).map(cloneRuntimeEntry);
}
```
  with `isRuntimeContextPrefixEntry` returning true for source `context_prefix` **or** `skills_listing`
  (`compact-selection.ts:337-349`), and the prefix preserved verbatim when slicing to fit
  (`:284-289`). So after a compaction the model still has the full skill list — and it should, since it can
  always re-load any of them.
* The **loaded skill body** is an ordinary tool result. Compaction can drop it, and the derived loaded-skill
  judgement then flips back to "not loaded". That is deliberate
  (`tool/handlers/workflow-skill-gate.ts:1-8`):

```ts
// 工作流创作工具的技能加载检查。
// CreateWorkflow、AmendWorkflow、SaveWorkflow 和 EvalWorkflowSnippet 的工具描述保持简短，
// facade 与写作规则由 `dynamic-workflows` 技能提供。提交脚本前必须加载技能：会话历史里没有
// 成功的 `Skill(dynamic-workflows)` 调用时，resolveInput 直接拒绝，避免进入 hook 或显示无效确认窗。
//
// 判据来自模型当前可见的 messageHistory。compaction 移除技能正文及对应调用后，需要重新加载；
// resume/rewind 则随历史一起恢复该判据，不维护第二份会话状态。
```

The gate itself (`workflow-skill-gate.ts:19-39`):

```ts
/** 门在场时的判据；单独导出供探针实现复用。 */
export function isDynamicWorkflowSkillLoaded(context: ToolInputResolutionContext): boolean {
  return context.hasLoadedSkill?.(DYNAMIC_WORKFLOW_SKILL_NAME) ?? true;
}

/**
 * 没读过技能就拒绝。返回 `undefined` 表示放行：技能已加载，或本会话没有探针（见文件头）。
 *
 * @param toolName 拒绝文案里点名的工具，让模型知道重试哪一个。
 */
export function requireDynamicWorkflowSkill(
  context: ToolInputResolutionContext,
  toolName: string,
): ToolHandlerFailure | undefined {
  if (isDynamicWorkflowSkillLoaded(context)) return undefined;
  return {
    result: false,
    errorCode: WORKFLOW_SKILL_NOT_LOADED_CODE,
    message: `${toolName} needs the \`${DYNAMIC_WORKFLOW_SKILL_NAME}\` skill loaded in this session before it accepts a script. Call the Skill tool with skill "${DYNAMIC_WORKFLOW_SKILL_NAME}" first — it carries the facade declarations the script is checked against, the authoring rules and this tool's full contract — then call ${toolName} again. Nothing was started.`,
  };
}
```

`WORKFLOW_SKILL_NOT_LOADED_CODE = 428` (`workflow-skill-gate.ts:17`),
`DYNAMIC_WORKFLOW_SKILL_NAME = "dynamic-workflows"` (`contracts/src/tools/create-workflow.ts:17`). Gates:
`CreateWorkflow` (unless it only *runs* a saved workflow, `workflow-skill-gate.ts:42-49`), `AmendWorkflow`
(unless it only changes metadata, `:51-55`), `SaveWorkflow`, `EvalWorkflowSnippet`. This is the only place in
the core where a skill's presence in history changes tool behaviour.

---

## 6. Limits, with values

| Limit | Value | Location | Effect |
|---|---|---|---|
| `MAX_SKILL_BYTES` | `100_000` | `tool/handlers/skill.ts:16` | passed as `maxBytes` to the port; also `maxOutputBytes`, and both `resultBudget` caps with `strategy: "truncate"`, `preview.direction: "head"` (`:105,125-133`) |
| `DEFAULT_MAX_SKILL_BYTES` | `100_000` | `adapters/src/skills/index.ts:28` | adapter default when `maxBytes` is absent (`:113`) |
| Truncation mechanics | — | `adapters/src/skills/index.ts:114-119,458-467` | `stat().size > maxBytes` → read only the first `maxBytes` bytes (`readFirstBytes`, `Buffer.alloc(maxBytes)`, single positional read); `truncated: true`; the handler appends `[Skill content truncated]` (`handlers/skill.ts:66`) |
| `MAX_DESCRIPTION_LENGTH` | `1024` chars | `adapters/src/skills/index.ts:20,206-215` | longer description → **error** diagnostic `skill_description_too_long`, skill **rejected** from discovery |
| `MAX_DESCRIPTION_LENGTH` (desktop) | `1024` chars | `packages/services/src/skills/skillsService.ts:62,934` | same rule on the services side |
| `MAX_DESCRIPTION_CHARS` | `250` chars | `context/sections/skills.ts:10,60-69` | catalog line truncation: `description.slice(0, 249) + "..."` → at most 252 chars rendered (and `when_to_use` is concatenated *before* the cut) |
| `DEFAULT_SKILL_METADATA_BUDGET` | `20_000` chars | `context/sections/skills.ts:9` | if the joined catalog exceeds the budget, degrade to names-only lines (no descriptions) rather than dropping skills (`:50-57`) |
| `skills.metadataBudget` default | `20_000` | `contracts/src/config/index.ts:330-335` | user-configurable; plumbed via `bootstrap/src/app/runtime-config.ts:145` → `ContextBuilderConfig.skillMetadataBudget` (`context/types.ts:116`) |
| Max number of skills | **none** | — | no cap on `outcome.skills.length`; `totalDiscovered` is unbounded and the budget only removes descriptions |
| `MAX_PLUGIN_MANIFEST_SEARCH_DEPTH` | `5` levels up | `adapters/src/skills/index.ts:29,423-436` | how far above a plugin skill root ZCode looks for the plugin manifest to mint `qualifiedName` |
| `MAX_SKILL_SCAN_DEPTH` | `8` | `packages/shared/src/skill-scan-policy.ts:43` | **desktop/services recursive walker only** (`services/src/skills/skillDiscoveryWalk.ts:79`, `skill-sync/skillSyncService.ts:231`); the agent path scans exactly 1 level |
| Tool timeout | `30_000` ms | `tool/handlers/skill.ts:104,134-138` | `allowCallOverride: false` |
| Cancellation | supported | `tool/handlers/skill.ts:139-143` | abort signal forwarded to the port (`:49`); `throwIfAborted` in the adapter (`index.ts:63,72,75,99,478-482`) |
| Result/telemetry record size | `recordInput/Output: "summary"` | `tool/handlers/skill.ts:144-149` | full skill bodies are not copied into trace records |
| Custom commands (contrast, not skills) | `DEFAULT_MAX_COMMAND_BYTES = 100_000`, `MAX_DESCRIPTION_LENGTH = 1024`, `MAX_SCAN_DEPTH = 12` | `adapters/src/commands/index.ts:23-25` | the sibling `.md` slash-command format has its own, deeper scan |

Practical reading: a skill body up to 100 KB reaches the model in one tool result; a body over 100 KB is cut at
an arbitrary byte offset (`Buffer.toString("utf8")` after a raw byte read — a multi-byte character straddling
the cut is replaced with U+FFFD and the block is flagged truncated). Descriptions over 1024 chars remove the
skill; descriptions over 250 chars are silently shortened in the catalog only, never in the loaded body.

---

## 7. What ZCode adds beyond plain-Markdown progressive disclosure

Baseline for comparison: a directory of `SKILL.md` files with `name` + `description` frontmatter, listed by name
and description, loaded on demand in full. Everything below is machinery ZCode layers on top of that.

**Discovery**
1. **Seven-class root precedence** with numeric priorities: configured `skills.roots` → user `.zcode` → user
   `.agents` → each project directory from cwd up to the git worktree root (`.zcode` before `.agents` per level)
   → plugins (1 000+) → bundled pack (1 000 000) (`roots.ts:20-59,94-105`; `bundled-skills.ts:49`).
2. **A second, non-recursive scan contract for the agent** (root + one level only) that intentionally diverges
   from the desktop walker's depth-8 recursion — and a shared pure policy module
   (`packages/shared/src/skill-scan-policy.ts`) to keep the *exclusion* rules identical across both.
3. **A trust boundary on symlinks**: user/project roots follow them (supported import mechanism); plugin roots
   reject them at root, directory and file granularity (`scan.ts:16-23`).
4. **Path-identity dedup** rather than name dedup, with `totalDiscovered` counting pre-dedup (`index.ts:79-89`).
5. **13 diagnostic codes** (`contracts/src/skills/index.ts:13-26`) covering root-not-found, scan failure (with
   ENOENT-vs-EACCES discipline: `scan.ts:144-155`), read failure, frontmatter problems, name/description
   problems, duplicates, oversize, not-found — plus plugin-scope codes (`plugin_skill_root_empty`,
   `plugin_component_path_invalid`).
6. **Validation that rejects** rather than degrades: no name, no description, or description > 1024 → the skill
   does not exist for the session.
7. **Per-skill enable/disable by absolute path**, matched against both the literal path and `realpath`
   (`index.ts:239-248`), fed from `config.skill["<path>"].enable` — plus a dynamic-workflow kill switch that
   disables the bundled skill by path when the feature is off (`create-app.ts:752-757`).

**Identity and packaging**
8. **Plugin namespacing**: `qualifiedName = "<pluginName>:<skillName>"`, `pluginName`, `pluginId`, found by
   walking ≤5 levels up for one of four manifest layouts; the catalog prints `(also loadable as <bare name>)`
   (`index.ts:250-273,423-436`; `context/sections/skills.ts:75-78`).
9. **A bundled, non-plugin skill pack** shipped with the CLI that resolves through three runtime shapes
   (dev/Electron in place, SEA via a content-hash-named extraction directory, remote prebuild staging), with
   per-file SHA-256 verification, an all-or-nothing required-asset list, idempotent concurrent seeding and
   degraded fallback to a previously seeded pack (`bootstrap/src/app/bundled-skills.ts:27-31,107-203`).
10. **Two independent frontmatter parsers** — a dependency-free flat-YAML subset for the agent and a real-YAML
    parser with loose fallbacks for the desktop — that must agree on `name`/`description`
    (`adapters/src/skills/index.ts:299-417` vs `services/skills/skillsService.ts:296-450`).
11. **A key whitelist producing `safeToAutoLoad`** (and a UI/CLI field to show it) — a safety signal that is
    computed and surfaced but never enforced (`index.ts:231`).

**Delivery to the model**
12. **A budgeted two-mode catalog renderer** with per-line description truncation and a names-only degradation
    path (`context/sections/skills.ts:39-69`), gated on the `Skill` tool actually being registered
    (`context/builder.ts:179,225-228`).
13. **Catalog delivery as a compaction-exempt request-prefix system-reminder attachment** with its own source id
    `skills_listing`, distinct from `context_prefix` and free of the latter's boilerplate
    (`builder.ts:282-292`; `source.ts:20,91`; `compact-selection.ts:141-145,337-349`).
14. **A second delivery mechanism for explicit user mentions**: `$name` in a prompt injects the full body as
    `<available_skills><activated_skill …>` plus an append-only audit log entry
    (`skillsService.ts:245-292,1083-1120`).

**Tool contract**
15. **A fully specified tool entry**: input JSON Schema + legacy-input union, structured output type, permission
    entry (`permission: "skill"`, before-ask deny priority), telemetry metadata limited to
    `{qualifiedName, pluginId, source}`, a head-truncating result budget, 30 s non-overridable timeout, abort
    propagation, and `concurrentSafe: true` (`tool/handlers/skill.ts:78-150`).
16. **`${ZCODE_SKILL_DIR}` / `${CLAUDE_SKILL_DIR}` substitution** against the skill's own directory, performed
    only at load time (`tool/handlers/skill.ts:73-76`) — plus a base-directory footer so relative paths in the
    body are actionable.
17. **A history-derived load gate** (`sessionHasLoadedSkill`) used as a hard precondition with a dedicated error
    code 428 for four workflow-authoring tools, with explicit compaction/resume semantics and no second source
    of truth (`agent/loaded-skills.ts`; `runtime-tools.ts:201-209`; `workflow-skill-gate.ts`).
18. **CLI/UX surfaces**: `zcode skills list` / `zcode skills inspect` (human + `--json`, with diagnostics and
    truncation reporting) (`cli/src/skills-command.ts:140-240`), `/name` slash invocation routed through the
    same tool, the command-center skill picker (`cli/src/command-center/handlers/skill.ts:50`), and the desktop
    skills settings page that writes the enable/disable overrides.

In short: relative to plain progressive disclosure, ZCode adds a **discovery/precedence layer**, a
**validation + identity + packaging layer**, a **budgeted catalog delivery layer** with two independent
renderers, a **fully specified tool contract**, and **one behavioural gate** — while leaving the two keys that
would normally implement model-invocation policy (`disable-model-invocation`, `allowed-tools`) unread.

---

## 8. Skills present on disk

### 8.1 Project scope — `/Users/eric/projects/ZCode/.agents/skills/` (found via the git worktree root; 8 skills)

| Name in frontmatter | Directory | Description (verbatim, long ones elided) | Extra frontmatter keys |
|---|---|---|---|
| `agent-browser` | `agent-browser/` | Browser automation CLI for AI agents. Use when the user needs to interact with websites, including navigating pages, filling forms, clicking buttons, taking screenshots, extracting data, testing web apps, or automating any browser task. Triggers include requests to "open a website", "fill out a form", "click a button", "take a screenshot", "scrape data from a page", "test this web app", "login to a site", "automate browser actions", or any task requiring programmatic web interaction. | `allowed-tools` |
| `ai-elements` | `ai-elements/` | Build AI chat interfaces using ai-elements components — conversations, messages, tool displays, prompt inputs, and more. Use when the user wants to build a chatbot, AI assistant UI, or any AI-powered chat interface. | — |
| `architecture-governance` | `architecture-governance/` | Apply the repository's architecture policy to code changes by generating a bounded context package, checking module and layer boundaries, and reporting baseline-aware violations. Use for any code change; skip for documentation-only work. | — |
| `dep-refs` | `dep-refs/` | Use when needs to inspect TypeScript export references in the z-code workspace, list exports from a file, verify whether an export is unused before deletion, investigate who imports a symbol during refactors, or combine pnpm knip unused-export results with pnpm dep:refs symbol-level reference tracing. | `disable-model-invocation: true` *(inert — §2.2)* |
| `dogfood` | `dogfood/` | Systematically explore and test a web application to find bugs, UX issues, and other problems. Use when asked to "dogfood", "QA", "exploratory test", "find issues", "bug hunt", "test this app/site/platform", or review the quality of a web application. Produces a structured report with full reproduction evidence -- step-by-step screenshots, repro videos, and detailed repro steps for every issue -- so findings can be handed directly to the responsible teams. | `allowed-tools`, `disable-model-invocation: true` *(both inert)* |
| `electron` | `electron/` | Automate Electron desktop apps (VS Code, Slack, Discord, Figma, Notion, Spotify, etc.) using agent-browser via Chrome DevTools Protocol. Use when the user needs to interact with an Electron app, automate a desktop app, connect to a running app, control a native app, or test an Electron application. Triggers include "automate Slack app", "control VS Code", "interact with Discord app", "test this Electron app", "connect to desktop app", or any task requiring automation of a native Electron application. | `allowed-tools` |
| `feature-boundary-planner` | `feature-boundary-planner/` | Map a ZCode behavior change to current UI surfaces, state owners, protocol commands, persistence, and validation. Use for impact analysis, product-boundary planning, or an implementation handoff grounded in the checked-out source. | — |
| `vercel-react-best-practices` | `react-best-practices/` | React and Next.js performance optimization guidelines from Vercel Engineering. This skill should be used when writing, reviewing, or refactoring React/Next.js code to ensure optimal performance patterns. Triggers on tasks involving React components, Next.js pages, data fetching, bundle optimization, or performance improvements. | `license: MIT`, `metadata` |

Two things to note in this table: the last row's **directory name differs from the skill name**
(`react-best-practices/` → `vercel-react-best-practices`), and because frontmatter `name` wins, that skill is
loadable only as `vercel-react-best-practices`; and the two `disable-model-invocation: true` skills are still
advertised and still callable, per §2.2.

Supporting files in these directories (not skills themselves, read via relative paths from `baseDirectory`):
`agent-browser/references/*.md` (7), `agent-browser/templates/*.sh` (3),
`architecture-governance/references/` + `scripts/context-package.mjs` + `agents/`,
`feature-boundary-planner/references/` (incl. `zcode-feature-graph.yaml`) + `agents/`,
`dep-refs/agents/openai.yaml`, `react-best-practices/rules/*.md` (72 files, 70 rules + template + README) +
`metadata.json` + `README.md`,
`ai-elements/references/` + `scripts/`, `dogfood/references/` + `templates/`.

### 8.2 Bundled pack — `apps/zcode-cli/packages/bundled-skills/skills/` (1 skill, priority 1 000 000)

| Name | Path | Description | `when_to_use` |
|---|---|---|---|
| `dynamic-workflows` | `bundled-skills/skills/dynamic-workflows/SKILL.md` (92 387 bytes) | Use when writing, debugging, or resubmitting a dynamic-workflow script for the CreateWorkflow tool: choosing subagent topology, typing subagent results, fanning out over files or git, gating loops on world.run commands, testing pieces with EvalWorkflowSnippet, planner-reviewer loops, confirming findings before reporting them, report() salvage, the report shape a run returns, publishing artifacts the user opens, and handling a backgrounded run. | Only for CreateWorkflow scripts. A single delegation or a few independent lookups belong to the Agent tool instead. |

Required assets, all-or-nothing (`bootstrap/src/app/bundled-skills.ts:27-31`): `SKILL.md`, `patterns.md`,
`examples.md`. A missing pack logs "Bundled skill pack unavailable"; disabling the dynamic-workflow feature
removes this skill by path (`create-app.ts:752-757`).

### 8.3 In-repo plugin skills (plugin scope, discovered when the plugin is installed)

| Name | Path | Description |
|---|---|---|
| `control-browser` | `apps/zcode-cli/packages/browser-use-plugin/skills/control-browser/SKILL.md` | Use when opening, navigating, inspecting, testing, clicking, typing, filling, screenshotting, or verifying web pages and local HTTP targets (localhost, 127.0.0.1, ::1) inside ZCode, including browser/web-UI automation, rendered-page scraping, frontend checks, and visible page-state reading. Prefer this over Computer Use for anything that stays inside a web page, unless the user explicitly asks for Computer Use. Main agent only. |
| `web-gui-tester` | `apps/zcode-cli/packages/browser-use-plugin/skills/web-gui-tester/SKILL.md` | Use the browser automation tooling available in the session to test web frontends interactively in a purely GUI-based, black-box manner: simulate real user clicks, text input, scrolling, and other actions; use screenshots for visual verification and read-only DOM cross-validation; produce a final test report. |

### 8.4 Installed plugin cache — `~/.zcode/cli/plugins/cache/zcode-plugins-official/` (32 distinct skill names across plugin versions)

These live under the plugin roots that the adapter scans (`<plugin>/<version>/skills/<name>/SKILL.md`). Whether a
given one reaches the catalog depends on the plugin being enabled; the plugin *data* directory lists these
plugins as installed: `browser-use`, `computer-use`, `document-skills`, `documents`, `image-search`,
`node-repl-host`, `pdf`, `plugin-creator`, `presentations`, `skill-creator`, `spreadsheets`, `zcode-guide`
(all `@zcode-plugins-official`), plus `android-emulator`, `ios-simulator`, `restore-legacy-sessions` and
`superpowers` in the cache.

| Plugin (versions in cache) | Skills |
|---|---|
| `document-skills` (0.1.0, 0.1.4) | `docx`, `pdf`, `pptx`, `xlsx` — e.g. `pdf`: *Professional PDF toolkit covering four production workflows: reports, creative visuals, academic LaTeX, and existing PDF processing…*; `xlsx`: *Use this skill any time a spreadsheet file is the primary input or output…* |
| `documents` (0.1.7) | `docx` — *Complete DOCX document creation, editing, and analysis capabilities with support for revisions, comments, formatting preservation, and text extraction…* |
| `presentations` (0.1.7) | `pptx` — *Use this skill any time a presentation file is the primary input or output…* |
| `pdf` (0.1.7) | `pdf` — *Professional PDF toolkit covering four production workflows…* |
| `spreadsheets` (0.1.7) | `xlsx` |
| `browser-use` (0.4.2, 0.5.1) | `control-browser`, `web-gui-tester` |
| `computer-use` (0.5.14, 0.6.3) | `computer-use` — *Use when a task needs a native desktop app's own UI or the OS. For anything inside a web page, use Browser Use. Main agent only.* |
| `android-emulator` (0.1.0) | `android-dev` — *Build, run, inspect, and lightly automate Android apps with the android-emulator MCP tools.* |
| `ios-simulator` (0.1.0) | `ios-dev` — *Build, run, inspect, and lightly automate iOS simulator apps with the ios-simulator MCP tools.* |
| `skill-creator` (0.1.0) | `skill-creator` — *Create new skills, edit existing skills, and iterate wording…* |
| `plugin-creator` (0.1.1) | `plugin-creator` — *Create or update ZCode plugin source and a local test marketplace…* |
| `restore-legacy-sessions` (0.1.0) | `restore-legacy-sessions` |
| `superpowers` (5.1.0) | `brainstorming`, `dispatching-parallel-agents`, `executing-plans`, `finishing-a-development-branch`, `receiving-code-review`, `requesting-code-review`, `subagent-driven-development`, `systematic-debugging`, `test-driven-development`, `using-git-worktrees`, `using-superpowers`, `verification-before-completion`, `writing-plans`, `writing-skills` |
| `zcode-guide` (0.1.0, 0.3.0) | `diagnosing-commands`, `diagnosing-hooks`, `diagnosing-mcp`, `diagnosing-plugins`, `diagnosing-skills`, `zcode-configuration-guide` |

### 8.5 Roots that are empty or absent on this machine

| Root | State |
|---|---|
| `~/.zcode/skills` | **does not exist** — the user-scope skill convention referenced by the code has no instance here |
| `~/.agents/skills` | **does not exist** |
| `/Users/eric/projects/ZCode/.zcode/skills` | does not exist (only `.agents/skills` is used in this repo) |
| `config.skills.roots` | empty by default (`contracts/src/config/index.ts:334`), no `~/.zcode/cli/config.json` on this machine |
| `~/.zcode/cli/config.json` | absent → no `skill.<path>.enable=false` overrides in effect (path convention: `adapters/src/config/config-factory.ts:37,124`, `adapters/src/config/file-config.adapter.ts:61`; override mapping: `adapters/src/config/schema.ts:192-205,538-560`) |

---

## Appendix — file map

| Concern | File |
|---|---|
| `Skill` tool entry, `<skill_content>` template, telemetry, budgets | `apps/zcode-cli/packages/core/src/tool/handlers/skill.ts` |
| Tool input/output schemas | `apps/zcode-cli/packages/contracts/src/tools/skill.ts` |
| Catalog renderer + limits | `apps/zcode-cli/packages/core/src/context/sections/skills.ts` |
| Catalog injection + `skills_listing` attachment | `apps/zcode-cli/packages/core/src/context/builder.ts:175-187,279-307,338-345` |
| Session guidance one-liner | `apps/zcode-cli/packages/core/src/context/dynamic-sections.ts:44-74` |
| Attachment → entry | `apps/zcode-cli/packages/core/src/runtime/methods/context-history-entries.ts`; `agent/message-history.ts:326-335` |
| Attachment → provider message + `<system-reminder>` | `apps/zcode-cli/packages/core/src/runtime/helpers/provider-request-messages.ts:184-203`; `system-reminder/source.ts:20,91,190-211` |
| Discovery/parse/load adapter | `apps/zcode-cli/packages/adapters/src/skills/index.ts` |
| Root precedence | `apps/zcode-cli/packages/adapters/src/skills/roots.ts` |
| Scan rules (sync + async) | `apps/zcode-cli/packages/adapters/src/skills/scan.ts` |
| Shared scan policy constants | `packages/shared/src/skill-scan-policy.ts` |
| Desktop recursive walker | `packages/services/src/skills/skillDiscoveryWalk.ts` |
| Desktop skills service (`<available_skills>`, `$name`) | `packages/services/src/skills/skillsService.ts` |
| Runtime discovery entry | `apps/zcode-cli/packages/core/src/runtime/methods/context.ts:65,76-94,213-252` |
| Plugin skill roots | `apps/zcode-cli/packages/adapters/src/plugins/index.ts:104,149,387-444,649-706` |
| Bundled pack + SEA seeding | `apps/zcode-cli/packages/bootstrap/src/app/bundled-skills.ts` |
| Loaded-skill judgement | `apps/zcode-cli/packages/core/src/agent/loaded-skills.ts`; `runtime/helpers/runtime-tools.ts:201-209` |
| Workflow skill gate (code 428) | `apps/zcode-cli/packages/core/src/tool/handlers/workflow-skill-gate.ts` |
| Compaction treatment | `apps/zcode-cli/packages/core/src/runtime/helpers/compact-selection.ts:141-145,284-289,337-349` |
| CLI list/inspect | `apps/zcode-cli/packages/cli/src/skills-command.ts` |
