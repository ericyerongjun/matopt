# dsh Web UI Design System — implementation map

Repo root: `/Users/eric/projects/deepseek-harness` (all paths below are relative to it).
Everything in this report was read from source; quoted snippets are verbatim.

---

## 1. Authoritative docs (read these first, in this order)

| Doc | Path | What it fixes |
|---|---|---|
| Web UI style reference | `docs/web-styling.md` (39 lines) | Ownership of tokens, component rules (0.5px hairlines, elevation-not-border, menu material, focus, links), pointer to the radius standard |
| Web UI style reference (zh) | `docs/web-styling.zh.md` (39 lines) | 1:1 translation, same section structure (`职责归属 / 组件规则 / 圆角与设置卡片 / 变更系统`) |
| DSH unified corner-radius standard | `docs/ui-radius.md` (115 lines) | Radius scale table (role → token), nested geometry (`inner R = max(0, outer R − inset)`), settings-card material, verification checklist |
| Corner-radius standard (zh) | `docs/ui-radius.zh.md` (115 lines) | 1:1 translation |
| Package READMEs | `packages/client/ui-theme/README.md`, `ui-primitives/README.md`, `ui-conversation/README.md`, `ui-tool/README.md`, `ui-dockkit/README.md` | Behavior contracts per package; `ui-primitives/README.md#component-catalog` is the shopping list |

Verbatim rules that determine most of the look (`docs/web-styling.md:17-30`):

> - Use CSS Modules and `clsx`; do not add a component library or Tailwind.
> - Use `--dsw-alias-*` semantic tokens in feature components. Do not copy static palette values or write literal colors there.
> - Keep theme selectors out of feature component CSS. Light/dark overrides belong to the theme owner.
> - Elevated surfaces (menus, popovers, modals, panels, floating buttons, the composer) set `border: 0` and take `box-shadow: var(--dsw-elevation-panel)`, `var(--dsw-elevation-prominent)`, or the composer's `var(--dsw-elevation-soft)` … Never pair a `--dsw-alias-border-*` border with an lv/elevation shadow.
> - Flat borders and separators that use a neutral `--dsw-alias-border-*` token draw at `0.5px` … Dashed affordances and state-colored borders keep 1px.
> - Clickable artifact links … color through `--dsw-alias-link` at `font-weight: 500`, with no underline at rest and a dotted 3px-offset underline on hover/focus.

Radius table (`docs/ui-radius.md:30-37`, verbatim):

| Role | Typical dimensions | Radius | Token |
|---|---|---|---|
| Small detail | < H20; keycaps, inline code | R4 | `--dsw-radius-xs` |
| Compact control | H20–28; small buttons, compact icon buttons, menu items | R8 | `--dsw-radius-sm` |
| Standard control / single-line cell | H32–40; buttons, inputs, selects, nav rows | R12 | `--dsw-radius-md` |
| Large control / grouped content | large buttons, multiline cells, nested form groups | R16 | `--dsw-radius-lg` |
| Independent content card | settings cards, message bubbles, guide cards | R20 | `--dsw-radius-xl` |
| Main enclosing surface | composer, dialogs, panels | R28 | `--dsw-radius-panel` |

---

## 2. Package & file map

### 2.1 Token / theme layer

```
packages/client/ui-theme/
  src/styles/base.css                  # CSS reset-ish root vars: font stacks, motion, RADIUS SCALE, settings-card aliases
  src/styles/design-platform.css       # THE palette: --dsw-static-* + --dsw-alias-* + --dsw-specific-*, light & dark
  src/styles/gradient-shadow-text.css  # elevation tokens, shadows, menu blur, the whole --dsw-font-* typography ladder
  src/styles/corner-shape.css          # superellipse(1.5) via @supports + --dsw-corner-shape
  src/styles/focus.css                 # --dsw-focus-ring-width: 2px, :focus-visible color, pointer-modality suppression
  src/styles/scrollbar.css             # scrollbar skin + --dsh-scrollbar-* rebinding contract
  src/styles/shiki.css                 # --shiki-* syntax token palette (light/dark)
  src/styles/onboarding.css            # onboarding accents/gradients/card fills
  src/styles/brand-font.css            # @font-face Montserrat 300/400/500 (+ 3 woff2 + OFL)
  src/client/styles.ts                 # imports the 8 sheets with `?inline` and injects <style data-plugin> per ctx.effect
  src/client/AppearanceRow.tsx         # Settings → General: light/dark/system
  src/client/FontSizeRow.tsx           # Settings → General: 10–22px stepper (default 14)
  src/boot-theme.ts                    # pre-plugin <head> style + <body> script that set dark attr + font size before first paint
  src/theme-settings.ts, src/client/settings-store.ts, src/client/index.ts
  tests/*.client.spec.ts               # stylesheet guards: radius, elevation, corner-shape, focus, scrollbar, menu
  tests/expected/radius-exceptions.expected.json
```

Theme application is a **presenter in a different package**: `packages/client/ui-layout/src/client/theme-presenter.ts` writes

```ts
document.documentElement.style.colorScheme = scheme
document.documentElement.setAttribute('data-ds-theme-source', …)   // light|dark|system
if (scheme === 'dark') body.setAttribute('data-ds-dark-theme', '')  // ← the single dark selector
body.style.setProperty('--dsh-content-font-size', `${snapshot.fontSize}px`)
```

`packages/client/ui-theme/src/client/AppearanceRow.tsx` + `FontSizeRow.tsx` are the user-facing controls; `boot-theme.ts` emits the no-flash bootstrap:

```ts
const light = `:root{color-scheme:light}body{background-color:${LIGHT_BACKGROUND};--dsh-boot-bg:${LIGHT_BACKGROUND}}`  // #fff
const dark  = `:root{color-scheme:dark}body{background-color:${DARK_BACKGROUND};--dsh-boot-bg:${DARK_BACKGROUND}}`    // #151517
```

### 2.2 UI kit / primitives (the portable core)

```
packages/client/ui-primitives/          @deepseek-ai/dsh-client-ui-primitives — ZERO Cordis
  src/index.ts                          # 94 export lines: the public surface
  src/Button.tsx + Button.module.css    # variant primary|ghost|outline|toolbar; size md(H36/R12)|sm(H28/R8)
  src/Switch.module.css                 # 36×20 track, 16px thumb, radius 999 + corner-shape: round
  src/Checkbox, Input (H32/R12), Pill (H24 capsule), Tag (11/17 capsule, 8 tones)
  src/SegmentedControl, SegmentedTabs, Menu, MenuSurface, MenuGroup, Tooltip, HoverCard
  src/Modal, RiskConfirmation, Toast, ConnectionIndicator, DisclosureRow, StateDot, TextShimmer
  src/ShortcutKeys, PathLabel, ImageLightbox, ImagePreview, JsonTree, FileTypeIcon
  src/settings-form/{SettingsForm.tsx,fields.tsx,form-model.ts}   # whole settings-page kit
  src/markdown/*                        # see §6
  src/TerminalBlock.tsx ReadBlock.tsx DiffBlock.tsx SearchBlock.tsx WebBlock.tsx   # agent-output cards
  src/icons/index.tsx (1658 lines)      # hand-authored SVG glyph set, Regular(1px)/Medium(1.3px)
  src/plugin-artwork.tsx, guide-artwork.tsx, FishLogo.tsx, BrandWordmark.tsx
  src/code-highlighting.ts, useAnchoredPosition.ts, useAnchoredMaxHeight.ts, useModalLayer.ts,
  src/input-modality.ts, focus.ts, clipboard.ts, keyboard-composition.ts, head-tail-cap.ts
```

Third-party (non-workspace) deps only (`ui-primitives/package.json`): `react`/`react-dom` ^18.2, `clsx` ^2, `katex` ^0.16.47, `shiki` ^4.3.1 + `@shikijs/langs` ^4.3.1, `micromark-*` 2.x, `mdast-util-*` 2/3.x, `anser` ^2.3.5, `diff` ^9, `simple-icons` 16.31.0. Workspace imports are only three: `dsh-client-store` (settings form model), `dsh-util-code-language`, `dsh-util-workspace-path`.

### 2.3 Chat / conversation

```
packages/client/ui-conversation/        # target-neutral assembly + shell + composer (Cordis service: ctx.uiConversation)
  src/client/contract/conversation.ts   # ConversationNodeDefinition, ConversationViewNode, ConversationViewDefinition …
  src/client/contract/slots.ts (523)    # slot prop types (ChatViewSlotProps, ConversationContentProps …)
  src/client/contract/groups.ts, input.ts, records.ts, snapshot.ts, views.ts
  src/client/conversation/assembler.ts (1042)   # event → Context → Node materialization
  src/client/conversation/definition-registry.ts, event-registry.ts, view-registry.ts,
                        group-registry.ts, group-store.ts, location-index.ts (632), assembly.ts
  src/client/skeleton/ConversationRoot.tsx/.module.css (545)   # header, width axis, sticky composer seat
  src/client/skeleton/ConversationContent.tsx, ConversationHeader.tsx, ConversationMainPanel.tsx
  src/client/skeleton/InputBar.tsx (507) + .module.css (418)   # the composer card
  src/client/skeleton/EmptyHero.tsx + HeroShell.module.css, ContextMeter, TodoPanel, DefaultConversationViews
  src/client/input/editor/*             # Lexical 0.49 draft editor, ReferenceChip, projection, keymap
  src/client/queue/QueueDock.tsx/.module.css

packages/client/ui-chat/                # the concrete "chat" target
  src/client/chat/ChatView.tsx (337) + ChatView.module.css (278)      # the transcript
  src/client/chat/ChatNodeSeat.tsx (174)                              # KEYED RENDERER SEAT
  src/client/chat/register-node-renderers.ts                          # key → component table
  src/client/chat/ChatGroupSeat.tsx, render-entry.ts, TurnNavigator.tsx (295)
  src/client/chat/AssistantMarkdown.tsx, ReasoningRow.tsx, MessageItem.tsx (392) + .module.css
  src/client/chat/TurnProcessNodeView.tsx, TurnTailNodeView.tsx, TurnTriggerNodeView.tsx, StatsPills.tsx
  src/client/chat/ContextBody.tsx (592), SystemPromptRow.tsx, CommandNodeView.tsx, CompactionItem.tsx
  src/client/chat/use-chat-scroll.ts, use-chat-viewport.ts, use-scroll-follow.ts, use-turn-data.ts …
  src/client/conversation-nodes/*        # 20+ ConversationNodeDefinition modules (assistant, message, tool, …)
  src/client/conversation-nodes/chat-snapshot-builder.ts (1215)       # the 'chat' ConversationViewDefinition
  src/client/conversation-nodes/README.md                             # grouping/folding rules (read this for behavior)

packages/client/ui-tool/                # tool-call cards
  src/client/tool/ToolCallTree.tsx + .module.css
  src/client/tool/components/ToolRow.tsx + ToolRow.module.css, ToolDetails, AskQuestionCard, PreparingToolRow
  src/client/tool/toolviews/{read-row,read-family-row,read-image-row,file-mutation-row,search-row,
                             web-row,bash-sample,details-row,todo-row,ask-question-row,GenericToolCard}
  src/client/tool/models/*               # pure per-card models (terminal/diff/read/search/web/image/todo/…)
```

### 2.4 Shell, panes, settings

```
packages/client/ui-layout/              # AppFrame: 3-column grid + drag handles + ctx.layout
  src/client/AppFrame.tsx (304), AppFrame.module.css (286), columns.ts (geometry constants), theme-presenter.ts
packages/client/ui-dockkit/             # split-tree docking engine + React components (zero Cordis, embedder-agnostic)
  src/engine/{tree,operations,planner,controller,geometry,constraints,sequence,initial}.ts
  src/components/{DockSurface,PaneTree,TabLayout,TabPanel,TabTitle,TabMenu,FloatLayer}.tsx + dockkit.module.css
packages/client/ui-sidebar/             # left sidebar: session tree, search, groups, state dots, rail collapse
packages/client/ui-sidebar-right/       # right rail: tab registry, panel, headers, focus/persistence
packages/client/ui-sidebar-{files,browser,terminal,documentpreview}/   # right-rail tab types
packages/client/ui-settings-general/    # Settings modal shell (SettingsRoot.tsx 800×800) + General section + chrome
packages/client/ui-settings/            # settings domain base: config-form, schema, slots contract
packages/client/ui-settings-{account,models,plugins,plugin-inventory,agent-loop,subagent,shell,web-search,session-log}
packages/client/ui-{commands,input-trigger,shortcuts,workspace,plan,goal,jobs,deliverables,trajectory,workflow-run,user-questions,approval,attachment,skill,subagent,…}
packages/client/ui-renderer/            # React slot bindings: buildRenderApp → ctx.slots.renderSlot('root', {})
packages/client/ui-slots/               # slot registry core (single | list | keyed | chain)
apps/web/                               # Vite 6 entry: index.html + src/main.ts → @deepseek-ai/dsh-client-web
```

---

## 3. Design-token inventory

**Naming law**: `--dsw-*` = theme-owned global tokens (static palette, semantic alias, typography, elevation, radius). `--dsh-*` = *component/contract-local* variables (scrollbar, chat width axis, composer height, frame clearance, font-size axis). Feature CSS may not define new `--dsw-*` (`docs/web-styling.md:9-11`).

**There is no spacing scale and no z-index scale.** Spacing is literal px per component; z-index is literal (documented hotspots: CodeBlock banner `z-index: 6`, composer seat `7`, back-to-bottom `8`, `/`-menu seat `9`, right panel `10`, frame handles `11`, leading seat `15`, overlay layer `20`, settings overlay `1000`).

### 3.1 Static palette — `design-platform.css:4-83` (light) / `:85-164` (dark, identical values)

60 variables, all `rgb()`/alpha. Representative entries:

```css
--dsw-static-neutral-bluish-00: rgb(255, 255, 255);
--dsw-static-neutral-bluish-50: rgb(249, 250, 251);
--dsw-static-neutral-bluish-100: rgb(235, 238, 242);
--dsw-static-neutral-bluish-300: rgb(207, 211, 214);
--dsw-static-neutral-bluish-500: rgb(151, 157, 166);
--dsw-static-neutral-bluish-700: rgb(97, 102, 107);
--dsw-static-neutral-bluish-750: rgb(67, 69, 74);
--dsw-static-neutral-bluish-800: rgb(53, 54, 56);
--dsw-static-neutral-bluish-850: rgb(44, 44, 46);
--dsw-static-neutral-bluish-875: rgb(35, 35, 36);
--dsw-static-neutral-bluish-900: rgb(27, 27, 28);
--dsw-static-neutral-bluish-950: rgb(21, 21, 23);      /* dark base bg */
--dsw-static-neutral-bluish-1000: rgb(15, 17, 21);     /* light "ink" */
--dsw-static-neutral-50: rgb(250, 250, 250);
--dsw-static-neutral-200: rgb(229, 229, 229);
--dsw-static-neutral-300: rgb(212, 212, 212);
--dsw-static-deepseek-50: rgb(237, 243, 254);
--dsw-static-deepseek-100: rgb(228, 237, 253);
--dsw-static-deepseek-200: rgb(211, 226, 255);
--dsw-static-deepseek-400: rgb(122, 170, 255);
--dsw-static-deepseek-450: rgb(86, 134, 254);
--dsw-static-deepseek-500: rgb(65, 118, 230);          /* brand blue */
--dsw-static-deepseek-800: rgb(52, 65, 91);
--dsw-static-green-500: rgb(34, 197, 94);   --dsw-static-green-500-a08: rgb(34 197 94 / 8%);
--dsw-static-green-400: rgb(78, 209, 126);  --dsw-static-green-100: rgb(230, 250, 237);
--dsw-static-amber-500: rgb(245, 158, 11);  --dsw-static-amber-400: rgb(247, 173, 49);
--dsw-static-amber-600: rgb(221, 134, 41);  --dsw-static-amber-100: rgb(254, 245, 231);
--dsw-static-red-600: rgb(236, 19, 19);     --dsw-static-red-400: rgb(242, 90, 90);
--dsw-static-red-600-a08: rgb(236 19 19 / 8%);
--dsw-static-blue-500: rgb(59, 130, 246);   --dsw-static-blue-900: rgb(14, 48, 116);
```

### 3.2 Semantic aliases — light on `body` (`:166-282`), dark on `body[data-ds-dark-theme]` (`:284-398`)

Surfaces / backgrounds (light → dark):

```css
--dsw-alias-bg-base:            --dsw-static-neutral-bluish-00  →  --dsw-static-neutral-bluish-950   /* #fff → #151517 */
--dsw-alias-bg-layer-1:         bluish-00  →  bluish-875
--dsw-alias-bg-layer-2:         bluish-00  →  bluish-850
--dsw-alias-bg-layer-3:         bluish-00  →  bluish-800
--dsw-alias-bg-overlay:         bluish-150 →  bluish-700
--dsw-alias-bg-module-platform: bluish-60  →  bluish-800
--dsw-alias-bg-document-preview:bluish-100 →  bluish-950
--dsw-alias-bg-skeleton:        rgba(0,0,0,0.04)   →  rgba(255,255,255,0.08)
--dsw-alias-bg-mask-1:          rgba(0,0,0,0.24)   →  rgba(0,0,0,0.5)      /* modal/settings mask */
--dsw-alias-bg-mask-2:          rgba(0,0,0,0.12)   →  rgba(0,0,0,0.2)
--dsw-alias-bg-mask-3:          rgba(0,0,0,0.48)
--dsw-alias-bg-document-selection: color-mix(in srgb, var(--dsw-static-blue-500) 40%, transparent)
```

Borders (all drawn at 0.5px by consumers):

```css
--dsw-alias-border-l1: rgba(0,0,0,0.04)  →  rgba(255,255,255,0.06)
--dsw-alias-border-l2: rgba(0,0,0,0.10)  →  rgba(255,255,255,0.12)
--dsw-alias-border-l3: rgba(0,0,0,0.12)  →  rgba(255,255,255,0.16)
--dsw-alias-border-l4: rgba(0,0,0,0.16)  →  rgba(255,255,255,0.20)
--dsw-alias-border-l2-darkmode-thin: rgba(0,0,0,0.10) → rgba(255,255,255,0.06)
```

Text / labels:

```css
--dsw-alias-label-primary:   bluish-1000 → bluish-50
--dsw-alias-label-secondary: bluish-700  → bluish-300
--dsw-alias-label-tertiary:  bluish-600  → bluish-400
--dsw-alias-label-caption:   bluish-400  → bluish-600
--dsw-alias-label-dimmed:    bluish-200  → bluish-750
--dsw-alias-label-primary-foreground: #fff → bluish-1000      /* text on primary fill */
--dsw-alias-label-shimmer:   color-mix(in srgb, #000 30%, transparent) → color-mix(in srgb, #fff 45%, transparent)
--dsw-alias-label-deep-diving: color-mix(in srgb, deepseek-500 70%, blue-950)
                             → color-mix(in srgb, deepseek-450 55%, bluish-400)     /* the running-turn label */
--dsw-alias-label-deep-diving-shimmer: color-mix(deepseek-500 30%, blue-950) → color-mix(blue-300 65%, deepseek-400)
```

Brand + interactive + buttons:

```css
--dsw-alias-brand-primary: bluish-1000 → bluish-50     /* NOTE: near-black in light, near-white in dark */
--dsw-alias-brand-text:    bluish-1000 → bluish-50
--dsw-alias-brand-primary-new-colorprimary-new-color: rgb(65,118,230) → deepseek-450
--dsw-alias-button-primary-fill:  var(--dsw-alias-brand-primary)
--dsw-alias-button-primary-hover: bluish-750 → bluish-100
--dsw-alias-button-primary-dimmed: bluish-100 → bluish-750
--dsw-alias-button-ghost-active-fill: bluish-100 → bluish-750
--dsw-alias-button-ghost-active-hover: bluish-150 → bluish-700
--dsw-alias-button-ghost-active-border: bluish-500 → bluish-600
--dsw-alias-button-floating-fill: bluish-00 → bluish-850
--dsw-alias-button-floating-hover: bluish-75 → bluish-800
--dsw-alias-button-elevated-fill: bluish-00 → bluish-750
--dsw-alias-button-contrast-fill: bluish-700 → bluish-50
--dsw-alias-button-info-fill: deepseek-500 → deepseek-400
--dsw-alias-button-tool-bar-fill: rgba(84,85,87,0.5);  --dsw-alias-button-tool-bar-hover: rgba(84,85,87,0.6)
--dsw-alias-interactive-bg-hover:        rgba(38,49,72,0.06) → rgba(255,255,255,0.08)
--dsw-alias-interactive-bg-hover-solid:  bluish-75 → bluish-800
--dsw-alias-interactive-bg-hover-accent: rgba(38,49,72,0.14) → rgba(255,255,255,0.24)
--dsw-alias-interactive-bg-hover-danger: rgba(236,19,19,0.05) → rgba(242,90,90,0.15)
--dsw-alias-interactive-bg-active:       rgba(38,49,72,0.10) → rgba(255,255,255,0.14)
```

Status states (`--dsw-alias-state-*`), the *blue accent* used for focus rings, active tabs, carets:

```css
--dsw-alias-state-business-primary: deepseek-500 → deepseek-400     /* #4176E6 → #7AAAFF : focus ring, active tab, caret */
--dsw-alias-state-business-tertiary: deepseek-100 → deepseek-800
--dsw-alias-state-success-primary: green-500 ; --dsw-alias-state-success-secondary: green-400 ; --dsw-alias-state-success-tertiary: green-100 → green-900
--dsw-alias-state-warn-primary: amber-500 ; --dsw-alias-state-warn-secondary: amber-400 ; --dsw-alias-state-warn-tertiary: amber-100 → amber-900 ; --dsw-alias-state-warn-label: amber-600
--dsw-alias-state-error-primary: red-600 → red-400 ; --dsw-alias-state-error-secondary: red-400
--dsw-alias-state-idle-primary: neutral-300 → neutral-600
--dsw-alias-link: deepseek-500 → deepseek-400
```

Content-specific aliases (markdown, diffs, menus, scrollbars, tooltips, switches):

```css
--dsw-alias-markdown-code-block:        bluish-50 → bluish-900
--dsw-alias-markdown-code-block-banner: bluish-50 → bluish-850
--dsw-alias-markdown-inline-code:       neutral-50 → neutral-800
--dsw-alias-markdown-citation:          bluish-100 → bluish-800
--dsw-alias-markdown-tag:               bluish-75 → bluish-850
--dsw-alias-markdown-placeholder:       bluish-60 → bluish-850
--dsw-alias-markdown-code-segment-selected:   bluish-00 → bluish-800
--dsw-alias-markdown-code-segment-unselected: bluish-75 → bluish-900
--dsw-alias-code-diff-added:   green-500-a08 → green-500-a12
--dsw-alias-code-diff-deleted: red-600-a08   → red-400-a12
--dsw-alias-file-diff-added-bg:      rgb(230,244,231) → rgb(31,49,36)
--dsw-alias-file-diff-added-gutter:  rgb(237,247,237) → rgb(19,32,22)
--dsw-alias-file-diff-added-marker:  rgb(1,162,65)    → rgb(65,201,119)
--dsw-alias-file-diff-deleted-bg:     rgb(252,230,226) → rgb(60,31,27)
--dsw-alias-file-diff-deleted-gutter: rgb(253,236,233) → rgb(40,19,14)
--dsw-alias-file-diff-deleted-marker: rgb(186,39,35)   → rgb(250,66,62)
--dsw-alias-scrollbar-bg-l1:    neutral-200 → neutral-700
--dsw-alias-scrollbar-bg-l2:    neutral-200 → neutral-600
--dsw-alias-scrollbar-hover-l1: neutral-300 → neutral-600
--dsw-alias-scrollbar-hover-l2: neutral-300 → neutral-550
--dsw-alias-menu-icon:   bluish-800 → var(--dsw-alias-label-primary-dimmed)
--dsw-alias-menu-group-header-fill: rgba(248,249,250,0.94) → rgba(48,49,54,0.94)
--dsw-alias-toast-bg:    bluish-800 → bluish-750 ; --dsw-alias-toast-label: bluish-00
--dsw-alias-tooltip-bg:  bluish-850 → bluish-750
--dsw-alias-tooltip-key-bg: color-mix(in srgb, var(--dsw-alias-tooltip-bg), white 18%)
--dsw-alias-switch-thumb: bluish-00 → bluish-400
--dsw-alias-turn-trigger-bg:        var(--dsw-alias-markdown-code-block) → var(--dsw-alias-interactive-bg-hover)
--dsw-alias-turn-trigger-bg-hover:  var(--dsw-alias-interactive-bg-hover) → var(--dsw-alias-interactive-bg-active)
```

`--dsw-specific-*` (24 names) — surfaces that are not "alias-generic":

```css
--dsw-specific-bubble:          deepseek-50 → bluish-850        /* user message bubble */
--dsw-specific-bubble-highlight: deepseek-200 → bluish-750
--dsw-specific-input-major:     bluish-00 → bluish-850          /* composer card fill */
--dsw-specific-selector:        bluish-60 → bluish-800
--dsw-specific-sidebar-fill:    bluish-50 → bluish-900
--dsw-specific-sidebar-nav-item-hover:  bluish-75 → bluish-850
--dsw-specific-sidebar-nav-item-active: bluish-100 → bluish-750
--dsw-specific-sidebar-nav-item-active-accent: deepseek-100 → bluish-800
--dsw-specific-tip:             bluish-60 → bluish-800
--dsw-specific-login-input:     bluish-50 → bluish-900
--dsw-specific-menu:            var(--dsw-menu-surface-fill)     /* rebased to 94% opaque on darwin */
--dsw-menu-surface-fill:        rgba(248,249,250,0.58) → rgba(67,69,74,0.45)
--dsw-menu-backdrop-filter:     blur(40px) saturate(150%)
```

macOS override (`design-platform.css:400-408`):

```css
html[data-platform='darwin'] body { --dsw-specific-menu: rgba(248, 249, 250, 0.94); }
html[data-platform='darwin'] body[data-ds-dark-theme] { --dsw-specific-menu: rgba(48, 49, 54, 0.94); }
```

### 3.3 Radius scale + corner shape

`packages/client/ui-theme/src/styles/base.css:16-21`:

```css
:root {
  --dsw-radius-xs: 4px;    --dsw-radius-sm: 8px;    --dsw-radius-md: 12px;
  --dsw-radius-lg: 16px;   --dsw-radius-xl: 20px;   --dsw-radius-panel: 28px;
}
```

Settings-card material aliases (`base.css:24-27`):

```css
body {
  --dsw-alias-settings-card-fill: var(--dsw-alias-bg-layer-2);
  --dsw-alias-settings-card-stroke: var(--dsw-alias-border-l4);
}
```

Superellipse smoothing (`corner-shape.css`):

```css
@supports (corner-shape: superellipse(1.5)) {
  :root { --dsw-corner-shape: superellipse(1.5); }
  *, *::before, *::after { corner-shape: var(--dsw-corner-shape); }
}
```

Consequence enforced by tests: every full-round declaration (`50%`, `100%`, pill) must pair `corner-shape: round` in the same rule — e.g. `Switch.module.css`:

```css
.switch { height: 20px; padding: 2px; border-radius: 999px; background: var(--dsw-alias-border-l3); }
.thumb  { height: 16px; border-radius: 50%; corner-shape: round; background: var(--dsw-alias-label-primary-foreground); }
```

### 3.4 Typography — `gradient-shadow-text.css:47-271`

Font stacks (`base.css:6-15`):

```css
--dsw-font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB',
                   'Microsoft YaHei', 'Helvetica Neue', Helvetica, Arial, sans-serif;
--dsw-font-family-brand: 'Montserrat', var(--dsw-font-family);
--ds-font-family-code: 'SF Mono', 'JetBrains Mono', 'Fira Code', Consolas, 'Liberation Mono', Menlo, Courier,
                       'PingFang SC', 'Microsoft YaHei';
```

The **font-size axis** (drives the whole markdown ladder from the Settings stepper):

```css
--dsh-content-font-delta: calc(var(--dsh-content-font-size, 14px) - 14px);
--dsh-content-font-size-secondary: min(calc(var(--dsh-content-font-size, 14px) - 1px),
                                       max(13px, calc(var(--dsh-content-font-size, 14px) - 2px)));
--dsh-content-font-delta-secondary: calc(var(--dsh-content-font-size-secondary) - 13px);
```

Markdown ladder (each family also exports `-font-family/-font-weight/-line-height/-font-size/-font-style`):

```css
--dsw-font-markdown-h1: 700 calc(21px + var(--dsh-content-font-delta)) / calc(30px + var(--dsh-content-font-delta)) var(--dsw-font-family);
--dsw-font-markdown-h2: 700 calc(19px + …) / calc(28px + …)
--dsw-font-markdown-h3: 700 calc(18px + …) / calc(26px + …)
--dsw-font-markdown-h4: 600 var(--dsh-content-font-size, 14px) / calc(24px + …)
--dsw-font-markdown-base: var(--dsh-content-font-size, 14px) / calc(24px + …)
--dsw-font-markdown-base-strong: 600 <base>
--dsw-font-markdown-table: var(--dsh-content-font-size-secondary, 13px)/calc(22px + var(--dsh-content-font-delta-secondary, 0px))
--dsw-font-markdown-table-head: 500 <same size>
--dsw-font-markdown-small: 12px/20px
--dsw-font-markdown-code: 12px/19px var(--ds-font-family-code)
--dsw-font-markdown-code-block: 11px/19px var(--ds-font-family-code)
--dsw-font-markdown-code-block-small: 11px/16px var(--ds-font-family-code)
```

UI ladder (16 families, all `var(--dsw-font-family)`):

```css
--dsw-font-xl-24: 600 24px/32px      --dsw-font-l-20: 500 20px/28px
--dsw-font-m-18: 500 16px/28px       --dsw-font-base-16: 16px/24px
--dsw-font-base-strong-16: 500 16px/24px
--dsw-font-s-14: 14px/22px           --dsw-font-s-strong-14: 500 14px/22px
--dsw-font-xs-13: 13px/20px          --dsw-font-xs-strong-13: 500 13px/20px
--dsw-font-xxs-12: 12px/18px         --dsw-font-xxs-strong-12: 500 12px/18px
--dsw-font-xxxs-11: 11px/14px        --dsw-font-xxxs-strong-11: 500 11px/14px
```

Weights used are 400/500/600/700 only (the sheet comments that Figma's 510 always renders as 500). Brand face is Montserrat 300/400/500 from `brand-font.css` + 3 woff2 files, used only for `--dsw-font-family-brand` slots (FishLogo wordmark, onboarding).

### 3.5 Elevation, shadows, motion, focus, scrollbars

Shadows (`gradient-shadow-text.css:4-9`):

```css
--dsw-shadow-lv1: 0 2px 4px 0 rgba(0,0,0,0.05);
--dsw-shadow-lv1-blur: 0 4px 12px 0 rgba(0,0,0,0.02);
--dsw-shadow-lv2: 0 4px 12px 0 rgba(0,0,0,0.02), 0 2px 8px 0 rgba(0,0,0,0.04);
--dsw-shadow-lv3: 0 0 1px 0 rgba(0,0,0,0.2), 0 0 4px 0 rgba(0,0,0,0.02), 0 12px 32px 0 rgba(0,0,0,0.08);
```

Elevation trio (re-declared on `body, body *` so a surface can rebind the stroke colour):

```css
body { --dsw-elevation-stroke-color: var(--dsw-alias-border-l4); --dsw-mask-blur: none;
       --dsw-menu-backdrop-filter: blur(40px) saturate(150%); }
body[data-ds-dark-theme] [data-menu-material] { --dsw-elevation-stroke-color: var(--dsw-alias-border-l3); }

body, body * {
  --dsw-elevation-stroke: 0 0 0 0.5px var(--dsw-elevation-stroke-color);
  --dsw-elevation-panel:     var(--dsw-elevation-stroke), 0 3px 8px 0 rgba(0,0,0,0.03), 0 0 16px 0 rgba(0,0,0,0.02);
  --dsw-elevation-prominent: var(--dsw-elevation-stroke), 0 3px 8px 0 rgba(0,0,0,0.04), 0 0 20px 0 rgba(0,0,0,0.05);
  --dsw-elevation-soft:      var(--dsw-elevation-stroke), 0 4px 16px 0 rgba(0,0,0,0.03), 0 0 24px 0 rgba(0,0,0,0.03);
}
```

The 0.5px hairline lives *inside* the shadow, which is why elevated surfaces set `border: 0`.

Motion (`base.css:12-15`) — the only motion tokens, and they are `--ds-`, not `--dsw-`:

```css
--ds-ease-in-out: cubic-bezier(0.4, 0, 0.2, 1);
--ds-transition-duration: 0.2s;  --ds-transition-duration-fast: 0.1s;  --ds-transition-duration-slow: 0.3s;
```

Focus (`focus.css`, complete):

```css
:root { --dsw-focus-ring-width: 2px; }
:focus-visible { outline-color: var(--dsw-focus-ring-color, var(--dsw-alias-state-business-primary));
                 outline-width: var(--dsw-focus-ring-width); }
html[data-input-modality='pointer'] body :focus-visible:not(:read-write) {
  --dsw-focus-ring-color: transparent; outline-color: transparent; }
```

Scrollbars (`scrollbar.css`) — the four-token rebinding contract:

```css
body {
  --dsh-scrollbar-thumb: var(--dsw-alias-scrollbar-bg-l1);
  --dsh-scrollbar-thumb-hover: var(--dsw-alias-scrollbar-hover-l1);
  --dsh-scrollbar-thumb-border: 0px;  --dsh-scrollbar-track-margin: 0px;
  --dsh-scrollbar-width: 5px;
}
::-webkit-scrollbar { width: var(--dsh-scrollbar-width); height: var(--dsh-scrollbar-width); }
::-webkit-scrollbar-thumb { border-radius: 999px; corner-shape: round; background: var(--dsh-scrollbar-thumb); background-clip: content-box; }
@supports not selector(::-webkit-scrollbar) { body, body * { scrollbar-width: thin; scrollbar-color: var(--dsh-scrollbar-thumb) transparent; } }
```

### 3.6 Shiki syntax palette — `shiki.css` (complete)

```css
:root {
  --shiki-foreground: var(--dsw-alias-label-primary);
  --shiki-background: var(--dsw-alias-markdown-code-block);
  --shiki-token-constant: #1c7ed6;    --shiki-token-string: #2f9e44;
  --shiki-token-comment: #868e96;     --shiki-token-keyword: #d6336c;
  --shiki-token-parameter: #e8590c;   --shiki-token-function: #6741d9;
  --shiki-token-string-expression: #2b8a3e; --shiki-token-punctuation: #495057; --shiki-token-link: #1971c2;
}
body[data-ds-dark-theme] {
  --shiki-token-constant: #4dabf7; --shiki-token-string: #69db7c; --shiki-token-comment: #adb5bd;
  --shiki-token-keyword: #faa2c1;  --shiki-token-parameter: #ffa94d; --shiki-token-function: #b197fc;
  --shiki-token-string-expression: #8ce99a; --shiki-token-punctuation: #ced4da; --shiki-token-link: #74c0fc;
}
```

### 3.7 Layout contract variables (`--dsh-*`) declared by components

| Variable | Declared in | Value / meaning |
|---|---|---|
| `--dsh-content-font-size` | `ui-layout/theme-presenter.ts` (also boot script) | 10–22px, default 14px |
| `--dsh-chat-content-width` | `ui-conversation/…/ConversationRoot.module.css` `.body` | `var(--dsh-chat-user-width, clamp(680px, calc(var(--dsh-conversation-column-width, 0px) * 0.64), 920px))` |
| `--dsh-composer-card-max-width` | same | `calc(var(--dsh-chat-content-width) + 32px)` |
| `--dsh-composer-side-clearance` | same | `16px` (embedded: 8px) |
| `--dsh-composer-dock-inset` | same | `8px` |
| `--dsh-composer-stack-gap` | `.composerStack` | `6px` |
| `--dsh-composer-text-max-height` | `.composerSeat` | `336px` (= 14 lines × 24px) |
| `--dsh-composer-height` / `--dsh-conversation-viewport-height` | `ConversationContent.tsx` ResizeObserver writes on the scroller | live px |
| `--dsh-chat-flow-gap` | `.column` gap rule | `6px` default, `12px` around responses, `16px` around turn-process |
| `--dsh-scrollbar-*` | `scrollbar.css` | see §3.5 |
| `--dsh-frame-top-clearance` / `--dsh-frame-overlay-top` / `--dsh-frame-leading-clearance` | `AppFrame.module.css` | 48px darwin / titlebar height; overlay = clearance+20px; 160px collapsed darwin |
| `--dsh-windows-titlebar-height`, `--dsh-windows-content-radius: 16px` | `AppFrame.module.css` | Windows caption handling |
| `--dsh-sidebar-inline-padding: 12px` | `ui-sidebar/SidebarRoot.module.css` | rail padding `18px 10px 6px` |
| `--dsl-*` (few, feature-owned) | e.g. `--dsl-code-block-border-radius` (`ui-primitives/markdown/CodeBlock.module.css`), `--dsl-guide-entry-radius`, `--dsl-terminal-*` | component-local contracts |

---

## 4. Component inventory

### 4.1 `ui-primitives` controls — paths + purpose + key geometry

| Export | File | Notes / real values |
|---|---|---|
| `Button` | `src/Button.tsx`, `Button.module.css` | `md` H36/R12/pad 0 14/font 14-22; `sm` H28/R8/pad 0 10/font 12-18; variants `primary`(brand fill)/`ghost`/`outline`(0.5px l3)/`toolbar`; disabled `opacity:.4` |
| `Switch` | `src/Switch.module.css` | 36×20, thumb 16, `999px` + `corner-shape: round`, off-thumb `--dsw-alias-switch-thumb` |
| `Checkbox` | `Checkbox.module.css` | native input, `accent-color: var(--dsw-alias-brand-primary)`, 14/20 |
| `Input` | `Input.module.css` | H32/R12, wrap fill `--dsw-alias-bg-layer-1`, focus border `state-business-primary` |
| `Pill` | `Pill.module.css` | H24 capsule, pad 0 8, 12/18 |
| `Tag` | `Tag.module.css` | capsule 11/17, pad 1px 8px, 8 tones via `color-mix(… 10–12%, transparent)` |
| `SegmentedControl` | `SegmentedControl.module.css` | outer R12 pad 4, indicator/segment R8, segments H28 |
| `SegmentedTabs` | `SegmentedTabs.module.css` | outer R16 pad 4, segments R12 |
| `Menu` / `MenuItemButton` | `Menu.tsx`, `Menu.module.css` | data rows + component rows, submenus, keyboard walk |
| `MenuSurface` | `MenuSurface.module.css` | R16 (`compact` → R12); `.material` layer = `--dsw-menu-surface-fill` + `backdrop-filter: var(--dsw-menu-backdrop-filter)`; darwin opaque `.backing` via CSS anchors |
| `MenuGroup` + `observeStickyMenuGroups` | `MenuGroup.module.css` | sticky headers, `data-stuck` → `--dsw-alias-menu-group-header-fill` |
| `Modal` | `Modal.module.css` | R28/BG `bg-layer-2`, mask `bg-mask-1` with `backdrop-filter: var(--dsw-mask-blur)` (= `none`), padding `max(24px, var(--dsh-frame-overlay-top,24px)) 24px`, close 28×28/R8 |
| `Tooltip` / `HoverCard` | `Tooltip.module.css`, `HoverCard.module.css` | bubble pad 3px 7px, R8, `--dsw-alias-tooltip-bg`, keycaps 16px R4 `--dsw-alias-tooltip-key-bg` |
| `Toast`, `ConnectionIndicator`, `RiskConfirmation`, `ImageLightbox` | same-named files | |
| `DisclosureRow` | `DisclosureRow.module.css` | 24px row (+font delta), 16px leading glyph, title 13/24 tertiary→secondary |
| `TextShimmer` | `TextShimmer.tsx` | 300ms delay, 1s sweep, 500ms rest, 15° tilt |
| `StateDot` | `StateDot.module.css` | 10px slot; success/warn/error/idle + 14px spinner; `appearance="step"` = check/hollow |
| `PathLabel`, `ShortcutKeys`, `JsonTree`/`JsonBlock`, `FileTypeIcon` | same-named | |
| `SettingsForm`, `SettingsValueField`, `SettingsSecretField`, `SettingsFormModel` | `settings-form/` | complete settings page kit (staged drafts, save-on-button) |
| Output cards: `TerminalBlock` `ReadBlock` `DiffBlock` `SearchBlock` `WebBlock` | same-named `.tsx` | shared geometry: `white-space: pre`, head-tail fold at `maxLines` (default 16) |
| `MarkdownText`, `CodeBlock`, `MarkdownDelegateProvider` | `src/markdown/` | see §6 |
| Icons | `src/icons/index.tsx` (1658 lines), `shared-artwork.tsx` | hand-written SVG; every product glyph ships `…Regular` (stroke 1) + `…Medium` (stroke 1.3); `size` prop |
| Artwork | `plugin-artwork.tsx`, `guide-artwork.tsx`, `FishLogo.tsx`, `BrandWordmark.tsx` | fixed-palette 36×36 tiles; `useId()` per-instance def ids |

### 4.2 Chat surface (`ui-chat` + `ui-conversation`)

| Component | Path | Purpose |
|---|---|---|
| `ChatView` | `ui-chat/src/client/chat/ChatView.tsx` | Transcript: frame → scroll → centered column → keyed seat list; TurnNavigator rail, back-to-bottom button |
| `ChatNodeList` | same file (memo) | maps `RenderEntry[]` → `ChatNodeSeat`/`ChatGroupSeat`, keys via `chatRenderKey` |
| `ChatNodeSeat` | `…/ChatNodeSeat.tsx` | subscribes one node key, applies turn-process visibility, dispatches `renderSlot('conversation.chat.node', …, { entryKey: kind })`; emits `data-chat-flow-kind` / `data-chat-node-key` / `data-chat-anchor-key` |
| `ChatGroupSeat` | `…/ChatGroupSeat.tsx` | business group rows |
| `render-entry.ts` | 16 lines | `chatRenderKey()` = `JSON.stringify(['node', key, groupPart ?? null])` |
| `AssistantNodeView` / `AssistantMarkdown` | `…/AssistantMarkdown.tsx` | block loop: `text` → `MarkdownText`, `reasoning` → `ReasoningRow`, `image` → attachment slot, `tool-call` → grouped out, unknown → `JsonBlock` |
| `ReasoningRow` | `…/ReasoningRow.tsx` + `.module.css` | 24px `DisclosureRow` with think glyph, collapsed first-paragraph/`TextShimmer` preview, expanded → compact `MarkdownText` |
| `TurnProcessNodeView` | `…/TurnProcessNodeView.tsx` | whole-Turn process fold ("N steps") |
| `TurnTriggerNodeView`, `TurnTailNodeView`, `CommandNodeView`, `SystemPromptRow`, `CompactionItem`, `RetryNodeView`, `TurnErrorNodeView`, `StatsPills`, `ContextBody` | same dir | turn chrome, commands, stats/token panels |
| `MessageItem` | `MessageItem.tsx` + `.module.css` (364) | user bubble + file/attachment cards + compaction row |
| `TurnNavigator` | `TurnNavigator.tsx` (295) | virtualized (`@tanstack/react-virtual`) turn rail |
| `RunningStatus` / `RunningWhaleTail` | `RunningStatus.tsx` | blue `--dsw-alias-label-deep-diving` + shimmer + animated whale mask |
| `ConversationRoot` | `ui-conversation/…/ConversationRoot.tsx` + `.module.css` (545) | header (76px), body width axis, width drag handles, sticky composer seat |
| `ConversationContent` | `…/ConversationContent.tsx` | hero/docked toggle, composer chain + takeovers, `conversation.input.dock` |
| `InputBar` | `…/InputBar.tsx` + `.module.css` (418) | the composer card (R28, elevation-soft, model/attachment tool row, send/stop) |
| `QueueDock`, `TodoPanel`, `ContextMeter`, `EmptyHero`/`HeroShell` | same dir | queue, todo dock, context ring, hero |
| `ToolCallTree` + `ToolRow` | `ui-tool/src/client/tool/` | root call tree + 24px summary row, subcalls indented `margin: 4px 0 2px 22px; border-left: 0.5px solid var(--dsw-alias-border-l2)` |

### 4.3 Shell

| Component | Path | Notes |
|---|---|---|
| `AppFrame` | `ui-layout/src/client/AppFrame.tsx` + `.module.css` | `display:grid; grid-template-rows:100%`, cols `sidebarCol / centerCol / rightbarCol`, `data-animating`/`data-dragging`; 8px `col-resize` handles (`z-index: 11`), overlay layer z20 |
| column geometry | `ui-layout/src/client/columns.ts` | `SIDEBAR_MIN 264 / MAX 420 / DEFAULT 280 / COLLAPSED 56 / AUTO_COLLAPSE 1024`, `CENTER_MIN 400`, `RIGHTBAR_MIN 300 / MAX_RATIO 0.7 / DEFAULT_RATIO 0.45` |
| `ThemePresenter` | `ui-layout/src/client/theme-presenter.ts` | applies snapshots to `<html>`/`<body>` |
| Sidebar | `ui-sidebar/src/client/SidebarRoot.tsx` + `.module.css` (614) | rail collapse, `quietBars` scrollbar hiding, brand row, panel list, settings seat |
| Right rail | `ui-sidebar-right/src/client/shell/{RightbarRoot,SidebarRight,ExpandButton}.tsx` + `SidebarRight.module.css` | docking surface, tab registry, `data-sidebar-right-open`, fullscreen mode |
| Dock engine | `ui-dockkit/src/engine/*` + `components/*` | split tree, undoable ops, `DockSurface`/`PaneTree`/`TabLayout`; embedder supplies `DockLabels`, `TabRenderer`, `DockIntents` |
| Settings shell | `ui-settings-general/src/client/SettingsRoot.tsx` + `SettingsRoot.module.css` | body-portalled modal: `.overlay` fixed inset 0 z1000, `.mask` `--dsw-alias-bg-mask-1` + `backdrop-filter: var(--dsw-mask-blur)`, `.panel` **800×800**, R28, `bg-layer-2`, `box-shadow: var(--dsw-elevation-prominent)`, `.nav` 188px wide, `.navCell` H40/R12 |
| Settings card | `ui-settings-account/src/client/AccountSection.module.css` | `padding: 12px 16px; border: 0.5px solid var(--dsw-alias-settings-card-stroke); border-radius: var(--dsw-radius-xl); background: var(--dsw-alias-settings-card-fill);` |
| Command popup | `ui-commands/src/client/PopupSelectView.tsx` + `.module.css` | `popupSelect` registry, 3 command UI kinds |

---

## 5. `ConversationNodeDefinition` + keyed renderer (the core pattern)

### 5.1 The interface — `packages/client/ui-conversation/src/client/contract/conversation.ts:196-256`

```ts
export interface ConversationNodeDefinition<State = unknown> {
  readonly kind: string
  /** Sole view target owned by this Definition; omitted for state-only Contexts. */
  readonly target?: string
  match(event: SessionEventLike): ConversationMatchResult | null      // { id, role: 'start' | 'update' }
  start(context: ConversationNodeContext<State>, match: ConversationStartMatch,
        reader: ConversationContextReader): State
  update(context: ConversationNodeContext<State> & { readonly state: State },
         match: ConversationMatch): State
  publication?(match: ConversationMatch): ConversationPublication   // 'none' | 'animation-frame' | 'immediate'
  buildLocationData?(context, scope: 'step' | 'turn', previous): ConversationLocationData | null
  buildViewNode?(context: ConversationNodeContext<State>): ConversationViewNode | null
}
```

`ConversationViewNode` (`:130-136`) is `{ key, kind, id, target, data }`; context keys are built by

```ts
export function conversationContextKey(kind: string, id: string): string { return `${kind.length}:${kind}${id}` }
```

A real (trimmed) Definition, `packages/client/ui-chat/src/client/conversation-nodes/message.ts:62-120`:

```ts
export const messageDefinition: ConversationNodeDefinition<MessageNode> = {
  kind: 'input-message',
  target: 'chat',
  match: (event) => event.type === 'user/message'
    ? (isAppendSurfaceEvent(event) && !isCompactionCheckpoint(event)
        ? { id: String(event.data.id), role: 'start' } : null)
    : null,
  start: (_context, match, reader) => { /* … returns UserMessageNode | SteeringMessageNode | ContextMessageNode */ },
  update: context => context.state,
  buildViewNode: (context) => context.state === undefined ? null
    : chatNode(context, waking ? 'turn-trigger' : context.state.kind, context.state.seq, context.state),
}
```

Node payload types are declaration-merged per package (`message.ts:26-37`):

```ts
declare module '../contract/chat-nodes.ts' {
  interface ChatNodeDataMap {
    user: ReferencedUserMessageNode
    steering: ReferencedSteeringMessageNode
    context: ContextMessageNode
    'turn-trigger': ContextMessageNode
  }
}
```

and the node builder (`conversation-nodes/common.ts:40-58`) stamps `key/id/target/anchorSeq/location/visibility`.

### 5.2 Registration (Cordis)

`packages/client/ui-chat/src/client/conversation-nodes/message.ts:140-143`:

```ts
export function registerMessageConversationNode(ctx: Context): void {
  ctx.uiConversation.events.register(messageDefinition)
  ctx.uiConversation.events.register(developerMessageDefinition)
}
```

`…/chat-snapshot-builder.ts:1206-1214` registers the *view* target:

```ts
export const chatViewDefinition: ConversationViewDefinition<ChatConversationViewNode, ChatSnapshot> = {
  target: 'chat',
  create: () => new ChatSnapshotBuilder(),
  isActive: snapshot => snapshot.order.some(key => snapshot.nodes.get(key)?.kind !== 'command'),
}
export function registerChatConversationView(ctx: Context): void { ctx.uiConversation.views.register(chatViewDefinition) }
```

`ConversationViewDefinition` (`contract/conversation.ts:294-310`) = `{ target, create(): ConversationViewBuilder, isActive?(snapshot), toolCallFocus?(callId) }`; the builder implements `empty`, `replace({nodes,timeline,changedTurns})`, `apply({upserts,timeline})`, optional `groupInput()`, `publish()`.

Registry lifecycle is generic (`conversation/definition-registry.ts`): duplicate keys throw, registration is a `ctx.effect` so unloading removes it, `entries()` is reference-stable, `subscribe()` invalidates synchronously.

### 5.3 The keyed renderer

`register-node-renderers.ts` is a plain key → component table on the `conversation.chat.node` **keyed slot**:

```ts
ctx.slots.inject('conversation.chat.node', () => ctx.slots.register(
  { name: 'conversation.chat.node', key: 'user', locale: NS }, UserMessageNodeView))
ctx.slots.inject('conversation.chat.node', () => ctx.slots.register({
  name: 'conversation.chat.node', key: 'assistant-step', locale: NS,
  inject: () => ({ hooks: { presentation } }),
}, AssistantNodeView))
ctx.slots.inject('conversation.chat.node', () => ctx.slots.register({
  name: 'conversation.chat.node', key: 'turn-tail', locale: NS,
  inject: () => ({ hooks: { performanceUsage } }),
  children: { 'conversation.chat.turnTail': { kind: 'list', scope: 'session' },
              'conversation.chat.assistant-actions': { kind: 'list', scope: 'session' } },
}, TurnTailNodeView))
```

Dispatch (`ChatNodeSeat.tsx:161-171`):

```tsx
{renderSlot('conversation.chat.node', routedOwner, {
  entryKey: routedNode.kind,
  hookContext,
  fallback: <JsonBlock label={t('message.unknownSurface', { type: routedNode.kind })}
                       payload={routedNode.data} truncatedLabel={total => t('json.truncated', { total })} />,
})}
```

Key mechanics: rows subscribe to **one stable node key** via `useChatNode(nodeKey)`; React keys come from `chatRenderKey()`; the slot kinds are the four declared by `ui-slots` — `single | list | keyed | chain` (`ui-slots/README.md:28`), with `renderSlot(key, owner, { entryKey })` doing dispatch (`ui-slots/src/index.ts:260-262`). Tool calls reuse the same idea one level down: `tool.call.toolview` is keyed by the wire tool name (`ui-tool/README.md:39-44`).

---

## 6. Markdown / math / highlighting stack

All in `packages/client/ui-primitives/src/markdown/` (+ `src/code-highlighting.ts`).

| File | Role |
|---|---|
| `parse.ts` (45) | two mdast grammars: `parseGfm` = `gfm() + cjkFriendlyStrong()`, `parseGfmWithMath` = `+ mathCompatibility() + math()`; both run `recoverLocalImages(...)` |
| `incremental.ts` (360) | streaming block-freeze parser; `const UNSTABLE_TAIL_BLOCKS = 2`; returns `{ frozen, tail, generation }` keyed by stable source offsets |
| `render.tsx` (760) | mdast → React, footnote section, links/images; **raw HTML is never parsed**: `case 'html': return node.value` (`:333-335`) |
| `MarkdownText.tsx` (199) | public component; `StreamingRenderer` class freezes blocks; imports `katex/dist/katex.min.css` directly |
| `katex.tsx` (90) | `katex.renderToString` → `DOMParser` → React elements; 3-arm error chain (strict → `strict:'ignore'` → `.katex-error` span) |
| `mathCompatibility.ts` (349) | extra TeX delimiters |
| `highlight.ts` (600) | the ONE shiki highlighter |
| `CodeBlock.tsx` / `CodeBlock.module.css` (145) | fenced block: R16 (`--dsl-code-block-border-radius: var(--dsw-radius-lg)`), sticky banner (`position: sticky; top: 0; z-index: 6`), banner `11px/18px`, copy + wrap controls |
| `useViewportHighlighting.ts`, `code-highlighting.ts` | visible-range highlighting; `languageForPath`, `CODE_HIGHLIGHT_EXTENSIONS` re-exported from `@deepseek-ai/dsh-util-code-language` |
| `MarkdownDelegate.tsx` (62) | `MarkdownDelegateProvider` — `openExternalLink`, `openFile`, `fileImages` |
| `MarkdownText.module.css` (467) | the whole markdown look |

Public API (`MarkdownText.tsx:174-183`):

```tsx
export const MarkdownText = memo(function MarkdownText({
  text, streaming = false, labels, fileMentions, pathImages, variant = 'body',
}: {
  text: string; streaming?: boolean; labels: MarkdownLabels
  fileMentions?: MarkdownFileMentions | undefined; pathImages?: MarkdownPathImages | undefined
  variant?: 'body' | 'compact'
})
```

Shiki setup (`highlight.ts:21-44`) — no WASM, JS regex engine, CSS-variables theme:

```ts
import { createHighlighterCoreSync, createCssVariablesTheme } from 'shiki/core'
import { createJavaScriptRegexEngine, defaultJavaScriptRegexConstructor } from 'shiki/engine/javascript'
import langTs from '@shikijs/langs/typescript'; import langBash from '@shikijs/langs/shellscript'; import langJson from '@shikijs/langs/json'
const LANGS = [langTs, langBash, langJson]        // boot set
const LAZY_GRAMMARS = new Map([['python', () => import('@shikijs/langs/python')], /* ~38 more */])
```

Markdown visuals worth copying (`MarkdownText.module.css`): base `font: var(--dsw-font-markdown-base)`; links `color: var(--dsw-alias-link); font-weight: 500;` with transparent 3/2px hit-area borders and `text-decoration: underline dotted var(--dsw-alias-link); text-underline-offset: 3px` on hover/focus only; `hr` = `height: 0.5px; background: var(--dsw-alias-border-l2)`; blockquote `border-left: 2px solid var(--dsw-alias-label-caption); padding-left: 14px`; inline code `background-color: var(--dsw-alias-markdown-inline-code); border: 0.5px solid var(--dsw-alias-border-l1); border-radius: var(--dsw-radius-sm); padding: 0 5px; font-size: .875em`; h1/h2/h3 `margin: 32px 0 16px`, p `margin: 16px 0`; tables `th/td padding: 10px 16px`, header border-bottom `0.5px var(--dsw-alias-border-l3)`, cells `max-width: min(30vw, 320px)`; wide tables toggle `overflow-x` on hover/focus (`:global(.md-table-wide)`).

Streaming visuals: `AssistantMarkdown` root carries `data-streaming`, `ReasoningRow` uses `TextShimmer`, `RunningStatus` renders `TalkingAbout` = `--dsw-alias-label-deep-diving` text at `calc(font-size - 2px)` plus a CSS-mask animated whale (`mask: url('./running-whale@2x.png')`).

---

## 7. App shell & layout skeleton

```
#root
└─ AppFrame.frame (display:grid; grid-template-rows:100%; height:100%; background: var(--dsw-alias-bg-base))
   ├─ .sidebarCol   (grid track 280px; bg var(--dsw-specific-sidebar-fill); border-right 0.5px var(--dsw-alias-border-l3))
   │   └─ SidebarRoot (.root: padding 6px 12px; --dsh-scrollbar-thumb l2; .collapsed → padding 18px 10px 6px)
   ├─ .handle ×2    (absolute; width 8px; margin-left -4px; cursor col-resize; z-index 11)
   ├─ .centerCol    (flex column; min-width 0)
   │   └─ ConversationRoot.root[data-phase=active|hero|settling]
   │       ├─ .header (grid auto/minmax(0,1fr); min-height 76px; padding 10px 28px 0 20px; border-bottom 0.5px l3)
   │       │   ├─ .headerLeading (grid-col 1) / .titleRow (grid-col 2, container-type: inline-size; min-height 30px)
   │       │   └─ .tabs (grid-col 1/-1; gap 36px; margin-top 10px; tab 13px/16px/500; active bar 2px, color state-business-primary)
   │       └─ .body (position:relative) — publishes the width axis
   │           ├─ --dsh-chat-content-width / --dsh-composer-card-max-width / --dsh-composer-side-clearance: 16px
   │           ├─ .widthHandle[data-side=left|right] (width min(10px, calc((100% - width)/2 - 48px)); ::after glow gradient)
   │           └─ .scrollBody (overflow-y auto; margin-right 2px; scrollbar-gutter: stable)
   │               ├─ [data-conversation-scroll] → ChatView .frame/.root/.scroll(container-type:inline-size)/.column
   │               │    └─ .column (max-width var(--dsh-chat-content-width); margin 0 auto; flow gap 6/12/16px)
   │               └─ .composerSeat (position: sticky; bottom: 0; z-index: 7;
   │                    background: linear-gradient(180deg, color-mix(bg-base 0%, transparent) 0px, var(--dsw-alias-bg-base) 36px))
   │                   └─ .composerStack (gap 6px) → [docks] → InputBar .card
   └─ .rightbarCol  (relative; overflow visible — panel can hang over the centre; occupant draws its own left border)
```

Composer card (`InputBar.module.css:45-115`) — the single most imitated surface:

```css
.card {
  gap: 12px; padding-top: 8px; width: 100%; max-width: var(--dsh-composer-card-max-width);
  border: 0; --dsw-elevation-stroke-color: var(--dsw-alias-border-l2);
  border-radius: var(--dsw-radius-panel);
  background: var(--dsw-specific-input-major);
  box-shadow: var(--dsw-elevation-soft);
  font-size: var(--dsh-content-font-size, 14px);
  line-height: calc(24px + var(--dsh-content-font-delta, 0px));
  --dsh-scrollbar-thumb: var(--dsw-alias-scrollbar-bg-l2);
}
.scroll { max-height: var(--dsh-composer-text-max-height); overflow-y: auto; margin-right: 4px; }
.input  { min-height: 36px; padding: 4px 8px 0 14px; caret-color: var(--dsw-alias-state-business-primary); }
```

Transcript geometry (`ChatView.module.css`):

```css
.scroll { padding: 16px calc(var(--dsh-composer-side-clearance) + 16px); container-type: inline-size; }
.column { max-width: var(--dsh-chat-content-width); width: 100%; margin: 0 auto; }
.column > … ~ … { margin-top: var(--dsh-chat-flow-gap, 6px); }     /* 12px around responses, 16px around turn-process */
.toBottom { width: 34px; height: 34px; border-radius: 100px; corner-shape: round;
            background: var(--dsw-alias-button-floating-fill); box-shadow: var(--dsw-elevation-panel); }
```

User bubble (`MessageItem.module.css:22-40`):

```css
.bubble { max-width: 100%; background: var(--dsw-specific-bubble); border-radius: var(--dsw-radius-xl);
          padding: 10px 16px; font-size: var(--dsh-content-font-size, 14px);
          line-height: calc(22px + var(--dsh-content-font-delta, 0px)); color: var(--dsw-alias-label-primary);
          white-space: pre-wrap; word-break: break-word; }
.userStack { align-items: flex-end; gap: 8px; max-width: min(calc(var(--dsh-chat-content-width, 748px) * 0.702), 82%); }
```

---

## 8. CSS methodology & build (concrete evidence)

* **CSS Modules + `clsx`. No Tailwind, no CSS-in-JS, no vanilla-extract, no component library.**
  - Every component has a sibling `*.module.css`: **186** files under `packages/client` + `apps/web` (`find packages/client apps/web -name '*.module.css'`).
  - `docs/web-styling.md:17`: "Use CSS Modules and `clsx`; do not add a component library or Tailwind."
  - Zero matches for `tailwind`, `styled-components`, `@emotion`, `vanilla-extract` across `packages/client` + `apps/web` (json/ts/tsx/css).
  - `clsx` is the only styling helper dep (`ui-primitives`, `ui-dockkit`, `ui-tool`, `ui-conversation`, `ui-sidebar`, `ui-theme`, …).
* **Class-name hashing is done by the client plugin bundler**, `packages/client/tsdown.client.ts:563`:
  ```ts
  const { code, exports: cssExports } = transform({ filename: fileId, code: source,
    cssModules: { pattern: '[hash]_[local]' }, minify: true })
  ```
  and the generated module injects a `<style>` tag plus a `classMap` object (so `import css from './X.module.css'` returns the map). `apps/web/vite.config.ts` sets **no** `css.modules` options → Vite defaults for the shell build.
* **Global sheets are plain `.css`, imported with `?inline` and injected as `<style data-plugin>` per Cordis effect** (`ui-theme/src/client/styles.ts`):
  ```ts
  import base from '../styles/base.css?inline'
  const STYLES = [['base.css', base], ['corner-shape.css', cornerShape], ['design-platform.css', designPlatform],
    ['focus.css', focus], ['onboarding.css', onboarding], ['scrollbar.css', scrollbar],
    ['gradient-shadow-text.css', gradientShadowText], ['shiki.css', shiki]] as const
  tag.dataset.plugin = PLUGIN_ID; tag.dataset.pluginCss = `${PLUGIN_ID}/${name}`
  ```
  The `?inline` / `.css` virtual-module handling lives in `tsdown.client.ts` (`dsh-css-modules-inline`, `dsh-css-text-inline`, `dsh-css-global-inline` plugins).
* **Theme selectors never appear in feature CSS** — the one dark selector is `body[data-ds-dark-theme]`, plus `html[data-platform='darwin']`, `html[data-windows-titlebar]`, `html[data-input-modality='pointer']`, `html[data-fullscreen]`.
* **No `<style>` in components, no inline theme styles**: `docs/web-styling.md:22` — inline React styles may only pass component-local custom-property *values*.
* **Style-guard tests** live beside the sheets: `ui-theme/tests/{radius,elevation,corner-shape,focus,scrollbar,menu-surfaces}-styles.client.spec.ts`, with `tests/expected/radius-exceptions.expected.json` pinning every permitted off-scale declaration.

---

## 9. Frameworks & build

| Concern | Reality | Evidence |
|---|---|---|
| React | `react`/`react-dom` `^18.2.0`, `@types/react` `~18.3.1` | `packages/client/ui-primitives/package.json`, `apps/web/package.json` |
| TypeScript | `typescript ^6.0.3` (root/apps) | `apps/web/package.json` |
| Shell build | Vite `^6.0.0` + `@vitejs/plugin-react ^4`, `target: 'es2022'`, `resolve.dedupe: ['react','react-dom']`, `base: './'` | `apps/web/vite.config.ts:168-249` |
| Client plugins | tsdown/rolldown per package with a CJS-ish `window.__ModuleLoader__.load({ id, factory })` banner | `packages/client/tsdown.client.ts` outputOptions.banner |
| State | `zustand ~4.4.7` + `immer ^10.1.1` behind a React-free contract in `@deepseek-ai/dsh-client-store` (`createSnapshotStore`, `notifySubscribers`); React binding is `use-sync-external-store 1.2.0` | `packages/client/store/package.json`, `ui-renderer/package.json` |
| Virtualized lists | `@tanstack/react-virtual ^3.14.9` — used in `ui-chat/.../TurnNavigator.tsx` and `ui-trajectory/.../TrajectoryTable.tsx` (the transcript itself is **not** virtualized) | grep result |
| Rich text editor | `lexical ^0.49.0` (+ `@lexical/{headless,history,plain-text,text,utils}`) for the composer draft | `ui-conversation/package.json`, `src/client/input/editor/` |
| Icons | **Hand-authored SVG components**, no icon library: `ui-primitives/src/icons/index.tsx` (1658 lines) + `shared-artwork.tsx`; Regular stroke 1 / Medium stroke 1.3; `size` prop; `simple-icons` only supplies brand marks for known web hosts | files above |
| Math | `katex ^0.16.47`, CSS imported via `import 'katex/dist/katex.min.css'`; Vite routes `.woff2/.woff/.ttf` to `assets/fonts/` | `MarkdownText.tsx:24`, `vite.config.ts:152-153,211-215` |
| Highlight | `shiki ^4.3.1` + `@shikijs/langs` (JS regex engine, `createCssVariablesTheme`), lazy grammar chunks under `assets/langs/` | `highlight.ts`, `vite.config.ts:117-150,201-210` |
| Markdown | `micromark-*` 2.x + `mdast-util-{from-markdown,gfm,math}` | `ui-primitives/package.json` |
| Terminal | `anser ^2.3.5` | `ui-primitives/src/ansi.ts` |
| Diff | `diff ^9.0.0` | `DiffBlock.tsx` |
| L10n | every string is a required prop from a typed `t` seat; primitives own **no** locale (`ui-primitives/README.md:107-109`) | |

---

## 10. How a non-dsh React app reuses this

### 10.1 Port list, in dependency order

1. **Tokens — 100 % portable, copy verbatim.** `ui-theme/src/styles/{base,design-platform,gradient-shadow-text,corner-shape,focus,scrollbar,shiki,onboarding}.css`. Concatenate in the order listed in `styles.ts` (order matters: `scrollbar.css` needs `design-platform.css` first). Keep the `body` / `body[data-ds-dark-theme]` scoping. Add the pre-paint bootstrap from `ui-theme/src/boot-theme.ts` if you support dark mode.
2. **The theme presenter — ~40 lines.** Reimplement `ui-layout/src/client/theme-presenter.ts:53-70` (set `color-scheme`, `data-ds-dark-theme`, `--dsh-content-font-size`, tokens) in a React effect or a module function. That is the entire runtime theming story.
3. **`ui-primitives` itself** — the highest-value port. It is genuinely framework-agnostic apart from React: **zero Cordis imports** (only `cordis` as an unused peerDependency for typing), React 18, `clsx`, and three tiny workspace helpers you can inline:
   - `@deepseek-ai/dsh-client-store` → only `settings-form/form-model.ts` needs it (zustand/immer snapshot store; swap for `useState`).
   - `@deepseek-ai/dsh-util-code-language` → `code-highlighting.ts` (233 lines: extension → shiki grammar table).
   - `@deepseek-ai/dsh-util-workspace-path` → `PathLabel.tsx` (236 lines: file URL resolution).
   If you vendor `ui-primitives`, also take `Button.module.css`, `MenuSurface.module.css`, `Modal.module.css`, `Tag/Pill/Switch/Input/SegmentedControl.module.css`, the icons module, and `markdown/`.
4. **The markdown stack** — `ui-primitives/src/markdown/*` + `shiki.css` + `CodeBlock.module.css`. Requirements: a bundler that can dynamic-`import()` per-language chunks (`@shikijs/langs/*`), plain `.css` import support for `katex/dist/katex.min.css`, and a CSS-Modules loader. Strip the `fileMentions`/`pathImages`/`openFile` vocabularies (dsh-specific) by simply not passing them; `MarkdownDelegateProvider` is optional.
5. **The chat layout skeleton** — copy the CSS-module files and the DOM shape, not the components: `ChatView.module.css` (`.frame/.root/.scroll/.column`), `ConversationRoot.module.css` (`.header/.body/.scrollBody/.composerSeat` + the `--dsh-*` width axis), `InputBar.module.css` (`.card/.scroll/.input`), `MessageItem.module.css` (`.userStack/.bubble`), `ReasoningRow.module.css`, `ToolRow.module.css`, `ToolCallTree.module.css`, `CodeBlock.module.css`, `MarkdownText.module.css`.
6. **Shell geometry** — `ui-layout/src/client/AppFrame.module.css` + `columns.ts` are plain CSS numbers with no Cordis in them; reimplement `AppFrame.tsx`'s pointer-drag in ~100 lines of React.
7. **Settings look** — `ui-settings-general/src/client/SettingsRoot.module.css` (800×800 panel, 188px nav rail, H40/R12 cells) + the settings-card recipe from `AccountSection.module.css`; `ui-primitives/src/settings-form/` gives you the whole form kit.
8. **Dock kit (optional)** — `ui-dockkit` is explicitly embedder-agnostic ("The Harness Web client is its first embedder; nothing in here knows that") and zero-Cordis: `src/engine/*` is pure logic, `src/components/*` take `DockLabels`/`TabRenderer`/`DockIntents`. Portable as-is.

### 10.2 What is entangled with Cordis (do **not** try to port directly)

| Thing | Where | Why it's entangled | Reuse strategy |
|---|---|---|---|
| Slot system | `packages/client/ui-slots/src/index.ts` (1669 lines), `ui-renderer/src/client/{app,bindings,bind.ts,registry.ts}` | `ctx.slots.register/inject/renderSlot`, standard-source bindings, `ctx.effect` lifetimes, `renderSlot('root', {})` as the app root | Replace with a plain React registry: `Map<key, Component>` + `useSyncExternalStore`, or a `Record<kind, Component>` switch. The *pattern* (keyed dispatch + per-kind payload map via TS declaration merging) is the valuable part |
| Conversation assembly | `ui-conversation/src/client/conversation/assembler.ts` (1042) + registries | Driven by `SessionEventLikeEntry` feeds from the Session Controller over RPC; `ConversationDefinitionRegistry` extends a Cordis `Service` | Reimplement the *shape*: append-only event log → `match/start/update` state machines → `{key, kind, data}` view nodes → keyed renderer. ~300 lines for a toy |
| Plugin lifetimes | `ctx.effect(...)` everywhere (e.g. `definition-registry.ts:51`, `ui-theme/src/client/styles.ts:31`) | unload/HMR semantics | `useEffect` cleanup or a simple dispose stack |
| Client module system / boot | `packages/client/modules`, `packages/client/web`, `window.__DSH_BOOT__`, `window.__ModuleLoader__.load({ id, factory })` banner in `tsdown.client.ts` | each plugin is a separately built bundle loaded at runtime | Ignore; build your app as one Vite bundle |
| Locale | `ctx.locale` + per-package `locales.ts` | primitives deliberately take labels as props | Pass your own strings — no change needed |
| Headless host/workspace/RPC | `ui-*` feature packages (`ui-jobs`, `ui-deliverables`, `ui-workspace`, …) | depend on dsh Remotes and host services | Skip; only the visual language is transferable |

### 10.3 The minimal "look-alike" recipe (what actually makes it look like dsh)

1. White `#fff` / `#151517` canvas, 0.5px neutral hairlines (`rgba(0,0,0,.04→.16)` light, `rgba(255,255,255,.06→.20)` dark).
2. Radius discipline: 8 / 12 / 16 / 20 / 28 by role; full round only for avatars/dots/pills, always with `corner-shape: round`.
3. `corner-shape: superellipse(1.5)` under `@supports`.
4. Elevated surfaces = `border: 0` + the three-layer elevation shadow whose first layer is the 0.5px stroke.
5. Menus = translucent fill + `blur(40px) saturate(150%)` on an isolated background layer.
6. Near-black primary buttons in light mode, near-white in dark; **blue `#4176E6` / `#7AAAFF` reserved for focus rings, active tabs, carets, links, running status**.
7. 14px body / 24px line-height base with a `--dsh-content-font-size` axis, 13px secondary tier, 12px small, 11px code-block; 400/500/600/700 weights only.
8. 6px flow gap between transcript rows, 12px around an assistant answer, 16px around a turn header; centered column `clamp(680px, 64%, 920px)`; composer card = column + 32px.
9. User bubble R20 `#EDF3FE`-ish (`--dsw-specific-bubble`), 10px/16px padding, ≤82% width.
10. Reasoning = a 24px tertiary disclosure row with a shimmering one-line preview; tools = 24px rows with 0.5px-left-indented subcall trees; code = R16 card with a sticky 11px banner.

---

## 11. The ~10 most valuable files to read next

1. `packages/client/ui-theme/src/styles/design-platform.css` — the entire palette, both themes (408 lines).
2. `packages/client/ui-theme/src/styles/gradient-shadow-text.css` — elevation + the full type ladder (271 lines).
3. `packages/client/ui-primitives/README.md` — the component catalog and every behavioral contract; then `src/index.ts` for the export list.
4. `packages/client/ui-conversation/src/client/contract/conversation.ts` — `ConversationNodeDefinition` / `ConversationViewDefinition` (320 lines).
5. `packages/client/ui-chat/src/client/chat/ChatNodeSeat.tsx` + `register-node-renderers.ts` — the keyed-renderer pattern end to end.
6. `packages/client/ui-conversation/src/client/skeleton/ConversationRoot.module.css` — the width axis, sticky composer seat, header geometry (545 lines).
7. `packages/client/ui-conversation/src/client/skeleton/InputBar.module.css` — the composer card recipe (418 lines).
8. `packages/client/ui-primitives/src/markdown/MarkdownText.module.css` (+ `MarkdownText.tsx` / `incremental.ts`) — markdown visuals and streaming algorithm.
9. `packages/client/ui-layout/src/client/{AppFrame.module.css,columns.ts,theme-presenter.ts}` — shell geometry + how theming is applied.
10. `docs/ui-radius.md` + `docs/web-styling.md` — the enforceable rules; keep them next to any port so the scale does not drift.
