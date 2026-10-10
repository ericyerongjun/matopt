# MatOpt frontend design

Updated 10 October 2026. This redesign is frontend only. The current React/Vite, Radix/ZCode primitives, Markdown/KaTeX and intelligent-result renderer remain in place. Existing conversations, request payloads, streaming/replay, file operations and backend behavior are preserved.

## Direction

MatOpt is an analytical workspace: people bring a problem or source file, direct real agent work, and inspect a durable result. Its identity should come from excellent treatment of mathematical answers, evidence, files and ongoing work. A quiet system-sans interface supports that content.

The selected layout is a compact workspace navigator beside an inset working canvas. A clear task header anchors the canvas. The conversation reads like a well-edited document; the composer is a generous two-row control; the optional results/files pane is a first-class working surface. Activity is available without displacing the selected document. On small screens the same hierarchy becomes one clear view at a time.

```text
MatOpt / navigation       Task title                    Workspace  Activity
New chat                  ┌──────────────────────────────────────────────┐
Search / files            │ Conversation             Results | Files     │
                          │ Request                  Selected output    │
Running (when present)    │ MatOpt / public work      or document        │
Recent chats              │ Answer, math, table                          │
                          │                                             │
                          │ [Ask, attach, or steer…                  ]   │
Workspace identity        │ [+  Model / effort                     ↑]   │
                          └──────────────────────────────────────────────┘
```

Alignment: left-aligned navigation, headings and prose; centered conversation column with 72–78 character reading measure when space permits. Display equations retain mathematical alignment. Data stays in real tables with an independently scrollable viewport. The content area should feel spacious without making controls sparse or oversized.

Plan review against the brief: an initial idea of adding cards and a decorative agent dashboard would repeat generic software styling and obscure the actual work. The selected design uses one deliberate structural change—the inset working canvas—and spends visual detail on readable results and a useful composer. No invented teams, workspaces, activity or percentages are introduced.

## Reference patterns

Current official pages are researched directly; source notes and dates are recorded in [the reference audit](docs/design-reference-research.md). Published marketing screenshots are design references, not evidence of access to authenticated product behavior.

Links below into `frontend/test/artifacts/` refer to local verification evidence. These generated reports and browser captures are retained in the development workspace and excluded from Git; they are not available in a fresh clone. Reusable fixtures and automated tests remain versioned.

- [ChatGPT Intelligent UI, 7 October 2026](https://openai.com/index/gpt-6-for-everyone/): responses mix prose and useful interactive content, appearing progressively. MatOpt already has that protocol; this task improves its presentation and consistency.
- [Codex app](https://openai.com/index/introducing-the-codex-app/): stable task identity and explicit review of agent work inform the workspace hierarchy.
- [OpenAI UI guidelines](https://developers.openai.com/plugins/concepts/ui-guidelines): component layouts should fit the user task and their host context. MatOpt adopts a shared visual vocabulary across answer controls and output panels.
- [Claude unified conversation](https://claude.com/resources/articles/cowork-is-now-claude), 16 September 2026, and [artifacts](https://support.claude.com/en/articles/17153992-what-are-artifacts-and-how-do-i-use-them): one entry point with durable outputs alongside the conversation.
- [Cursor compact chats and work panels](https://cursor.com/changelog/3-4), 13 May 2026: progressive disclosure, deliberate work views and a composer that remains available.
- [Grok Agent Dashboard](https://x.ai/news/agent-dashboard), 15 June 2026: attention-based status hierarchy and parent/child grouping. This is a CLI/TUI reference, not evidence of Grok web styling.

The target is comparable craft and clarity, not a claim of identical commercial model capabilities or a pixel clone.

## Design tokens

Use semantic tokens consumed by existing `--color-*` aliases. One source owns both schemes. Component files must not redeclare independent light/dark palettes.

| Role | Light | Dark |
| --- | --- | --- |
| App canvas | `#f3f4f6` | `#111214` |
| Navigation | `#f3f4f6` | `#151619` |
| Working surface | `#ffffff` | `#1b1d20` |
| Secondary surface | `#f5f6f8` | `#24272c` |
| Primary text | `#202329` | `#eeeef1` |
| Secondary text | `#5f6671` | `#afb4bf` |
| Subtle border | `#e3e6eb` | `#343840` |
| Interactive focus | `#4e60b9` | `#aab8ff` |

The cool accent identifies selected/focused controls and genuine active work. Success, warning and error colors remain semantic and paired with text/icons. No decorative gradients or heavy shadows.

- Typography: native system sans stack. Default answer/composer 16px; controls 13–14px; secondary metadata no smaller than 12px. Existing text-size preference continues to scale semantic roles. Welcome heading 30–34px; document hierarchy 24/20/17px. Answer line height about 1.7; controls about 1.4. Only code uses monospace; KaTeX retains its own metrics.
- Spacing: 4, 8, 12, 16, 20, 24, 32, 40, 48px. Desktop navigation about 248px; header 56–60px; answer measure about 760px. Dense context and generous reading space serve different jobs.
- Radii: 6px small embedded controls, 9px standard controls, 12px content blocks, 18px composer, 20px canvas. Pills reserved for compact state/chip controls.
- Elevation: one subtle canvas/composer edge; floating menus may use a soft shadow. Avoid shadow on every block.
- Motion: 120–180ms disclosure/hover transitions; animate only real running states. Respect reduced motion and never animate an already-buffered answer to imitate generation.

## Component guidelines

| Component | Intended treatment | Behavior preserved |
| --- | --- | --- |
| Navigation | Product identity, clear New chat, compact search, meaningful recent/running grouping, visible active selection | Select, rename, delete, search and mobile drawer keyboard handling |
| Task header | Task title first; restrained Workspace/Activity controls and clear active state | Existing panel toggles, settings, conversation identity |
| Composer | Two rows with generous writing area and calm toolbar; attachments, model/effort and send/stop remain discoverable | Drafts, IME, multiline input, files, slash commands, steering, failure recovery |
| User request | Compact distinct surface, readable attachments | Literal user text and command selections |
| Assistant answer | Modest author/phase identity, document hierarchy and spacious prose | Actual stream updates, copy, follow-ups and canonical reconciliation |
| Tables/code/math | Clear header/body hierarchy, tabular numerals, subtle separators, accessible copy/overflow controls | Semantic table, syntax highlighting, literal source distinction, KaTeX and resize behavior |
| Work/activity | One concise public work summary; clear running/paused/failed/completed state; expandable outcomes | Public-only allowlist, measured progress, manual expansion and no private traces |
| Results/files | Consistent panel header/tabs, readable document surface, explicit empty state and file selection | Mounted state, tab/scroll/focus retention, file previews, native controls, sandbox boundaries |

## Iteration and evidence protocol

The baseline and **at least four major implementation/refinement rounds** will be recorded below. Each round must include a rendered inspection, explicit critique, changes and evidence; a green build alone is not a visual review. Scores are our subjective design assessment, not a user study or independent commercial ranking. Do not invent a 9/10 rating to close the task.

1. Structure and foundation: coherent tokens, working canvas, navigation and composer hierarchy.
2. Answer and workspace craft: messages, tables/code/math, agent work, result/document treatment.
3. Interaction and adaptive behavior: menus, focus, side panels, small/short screens, light/dark balance.
4. Final visual critique and cleanup: spacing consistency, hierarchy, difficult states and any remaining defects discovered in the preceding rounds.

Rounds may be expanded or redirected by actual findings. The required critique dimensions are visual polish, typography, spacing/density, message/table/code quality, agent-state clarity and overall contemporary product quality. The target is at least 9/10 by the final self-review, supported by specific observations and disclosed remaining limits.

## Preservation and completion gates

- No backend or data-model changes. Record frontend source changes separately from pre-existing dirty work.
- Keep chat selection/rename/delete, attachments/upload, slash tools/skills, model/effort/check settings, theme/text size, exports and analysis restart reachable.
- Keep partial results, actual streaming, public tool activity, controls, reconnect, canonical reload and private-field exclusion intact.
- Keep result/file selections, native interactive state, mounted panes, composer draft and scroll/focus across opening/closing and final reconciliation.
- Verify empty, user/assistant, active, paused, error, attachment, table, code, math, interactive-result, Activity and Files states. Fixture data must be labeled as such; no test fixture is presented as live execution.
- Inspect light/dark desktop and mobile, 320px width, short viewports, text-size preference and 200% zoom/reflow. State explicitly if zoom is emulated.
- Verify keyboard navigation, visible focus, contrast, labels, reduced motion, scroll containment and no browser errors. Existing functionality tests and the production build must pass.
- Retain baseline and each round's screenshots, logs and source/fixture identity. Check actual production files rather than only an isolated mockup.

## Baseline and iteration log

### Baseline

[Baseline critique and 24 screenshots](frontend/test/artifacts/redesign/baseline-critique.md) cover 1440×900 and 390×844, light and dark, saved synthetic conversation, table/code, Activity, Results, Files and an unsaved empty route. No API writes or browser errors occurred. Subjective overall score **6.3/10**: visual polish 6.5, typography 7, density 6, message/table/code 7, agent clarity 6, light/dark coherence 6.5 and overall product feel 5.5. Main gaps: a flat shell, input shaped like search, repetitive activity, crowded file chrome and weak role hierarchy.

### Round 1 — structure and foundation

Integrated semantic tokens, inset canvas, two-row composer with actual model/effort, date/running navigation groups, answer hierarchy and docked Activity. Removed superseded global component rules. [Capture report](frontend/test/artifacts/redesign/round1-results.json): all four viewport/theme combinations completed six views each, with no page/console errors or attempted API writes. Root inspected 12 representative images covering every view and both themes; feature owners are inspecting their additional cases. Saved synthetic conversation is complete, so these images do not prove active animation.

Subjective scores: visual polish 8.0, typography 8.3, density 7.6, message/table/code 8.2, agent clarity 7.4, light/dark 8.3, overall product feel 7.8; overall **7.9/10**. Substantial improvement over the 6.3 baseline, still short of the target. The stronger document frame and restrained surfaces work; the composer reads as a task entry control and no longer gets covered by Activity.

Remaining gaps driving Round 2: (1) Files consumes about 400 px before preview on mobile and narrow docks; (2) Activity parents do not actually collapse descendants; (3) three conflicting product marks weaken identity; (4) the welcome view lacks concrete task entry; (5) compact composer/menu/sidebar behavior needs dedicated checks. Table overflow stays contained, but mobile users need obvious access to offscreen columns. Root shell/model tests 10 passed; component lanes reported 20 composer/navigation, 20 conversation/activity and 28 workspace preservation checks. Full integrated tests remain pending.

### Round 2 — task entry and focused work

Implemented a narrow-pane Files/document switch while retaining the mounted preview, true activity subtree disclosure with filtered ancestor context, one mathematical product mark, and editable task starters. The welcome view prioritizes input in short viewports. Larger text scales menu/composer labels, and keyboard-focused long chat titles expand to remain readable. Starter actions do not submit and are cleared when switching chats.

[Integrated capture](frontend/test/artifacts/redesign/round2-results.json) has 24 screenshots with identical source hashes at start/end, no browser errors and no API mutations. Root inspected the mobile dark document, desktop light welcome, desktop dark Activity and short large-text welcome alongside the labeled running/error fixtures. The document preview gains about 182px in a 390px phone and a 377px desktop dock; selected preview DOM, scroll and focus survive browser/document switching. Details: [workspace captures](frontend/test/artifacts/redesign/workspace-round2-after.json), [composer critique](frontend/test/artifacts/redesign/composer-navigation-round2-critique.md), [activity fixture scope](frontend/test/artifacts/redesign/activity-fixture-review.md). The fixture captures represent simulated component states, not new live agent runs.

Subjective scores: visual polish 8.7, typography 8.8, density 8.7, message/table/code 8.8, agent clarity 8.7, light/dark 8.8, overall product feel 8.7; overall **8.7/10**. The new welcome entry and compact activity hierarchy feel cohesive. Files now gives most of the pane to the actual document. The short viewport sacrifices its welcome headline so the task input remains usable.

Remaining gaps driving Round 3: a few token foreground/background combinations sit just below 4.5:1; selected paused work still appears in the sidebar's Running group; mobile header typography does not scale with the text preference. Wider interaction checks must verify panel focus, settings, preserved drafts/attachments, and 200% equivalent reflow. Focused component results: composer/navigation 21, activity/message 23, workspace 29 passed. The subsequent terminal-copy regression raises activity/message coverage to 24.

### Round 3 — interaction and adaptive behavior

Fixed mobile drawer focus when the opener remounts, scaled mobile headings and multiline menu/disclaimer line height, added truthful Paused navigation, renamed the queued/running/paused filter to Active, and hid nonmatching historical activity sections while preserving their mounted disclosure state. Published code now scales font and line height together: actual shadow-DOM rows measure 14px/21.7px and 20px/31px, including wrapped lines. The narrow native-result picker was compacted to 62–64px while retaining accessible labels and selection state.

[Integrated screenshots](frontend/test/artifacts/redesign/round3-results.json) again have stable start/end source hashes and no browser errors or writes. [Six interaction configurations](frontend/test/artifacts/redesign/round3-after-interaction-results.json) pass 74 assertions for real settings, menus, keyboard panel resizing, focus return, drafts, attachments, exports/restart discoverability and scroll containment. [Navigation-specific checks](frontend/test/artifacts/redesign/composer-navigation-round3-critique.md) pass 26 assertions, including the repaired drawer focus. [Message review](frontend/test/artifacts/redesign/round3-message-readability.md) includes 12 simulated activity cases, eight code-layout cases and direct inspection. Root inspected the default mobile welcome, paused desktop state, enlarged code, settings, file reflow and short conversation views.

[Token contrast](frontend/test/artifacts/redesign/token-contrast.json): all 70 opaque semantic text/background combinations meet 4.5:1 (minimum 4.52). Actual computed-style sampling found 334 UI and 144 syntax-token samples at or above 4.5:1. This is a bounded contrast check, not complete accessibility certification. The 640×450 CSS viewport at device scale 2 is explicitly an emulated 200% reflow check, not physical browser zoom or a mobile software-keyboard test.

Root subjective scores: visual polish 8.9, typography 9.1, density 8.6, message/table/code 9.1, agent clarity 9.1, light/dark 9.1, overall product feel 8.9; overall **8.9/10**. The navigation reviewer rates its scope 9.0; the [workspace reviewer rates its scope 8.2](frontend/test/artifacts/redesign/workspace-round3-critique.md). The latter is a useful disagreement: repetitive workspace/result chrome and an unclear wide-table affordance still consume attention on narrow screens. These are addressed in Round 4 rather than raising a rating based on passing tests.

### Round 4 — final visual critique and cleanup

Combined the generic Workspace title and view tabs into one Results/Files/close row, retaining the Workspace heading for assistive technology and initial focus on the selected visible tab. Native results now show a conditional table scroll hint, directional controls and a named keyboard region; fitting tables show no cue. The adapter preserves table nodes, contents, scroll and live renderer-owned accessibility labels, and uses the existing Markdown virtual-scroll limit. Independent code review found and helped resolve a stale-label issue before completion. The composer now responds to its own width, showing the full model name in a narrow large-text desktop dock while retaining effort metadata in its accessible description and menu.

The [independent final critique](frontend/test/artifacts/redesign/final-independent-critique.md) rates reviewed visual/interaction quality **9.0/10**, with density 8.8 and responsive/keyboard basics 8.9 explicitly retained as qualifications. Root's final judgment is also **9.0/10**: visual polish 9.0, typography 9.2, spacing/density 8.8, message/table/code quality 9.1, agent clarity 9.1, light/dark coherence 9.1 and overall product quality 9.0. The gain comes from coherent hierarchy, compact contextual controls and legible output, not from increasing a score because tests passed. This is an informed self-review, not an external usability study or proof of equivalence to commercial products.

Final validation used the actual production bundle through a temporary loopback preview: [24 screenshots and stable presentation-source hashes](frontend/test/artifacts/redesign/round4-production-results.json), plus [78 assertions across six interaction configurations](frontend/test/artifacts/redesign/round4-production-interaction-results.json). Both reports contain zero browser errors and zero attempted API writes. Root directly inspected production desktop light welcome, desktop dark Activity/table/math, mobile dark Files, mobile light table/math, and the final native-result and narrow-composer captures. Feature reviewers inspected additional states; generated screenshot count is not presented as manual review count.

[Native-result verification](frontend/test/artifacts/redesign/workspace-round4-table-verification.md) covers seven fixture configurations: normal/20px text, both themes, Markdown and structured tables, resize, keyboard boundaries and retained edits. All 298 frontend tests and the production build pass. The first restricted test run could not open the local socket needed by one runtime test; the complete rerun with local socket access passed with no skips. The UI contract check, seven provenance-gate regressions, and offline verification of 1,373 frontend provenance entries also pass. Only five reviewed host-adapter hashes/descriptions were refreshed; pinned copies and source hashes were not modified.

See the [completion audit](frontend/test/artifacts/redesign/final-audit.md) for requirement-by-requirement evidence and limits. The [119 backend source hashes](frontend/test/artifacts/redesign/backend-preservation.json) match the start of this frontend redesign. No backend changes, new libraries, commits or pushes were made for this design pass.

Remaining practical limits: narrow panes still scroll for wide tables and long filenames; the result picker repeats the selected canvas title to preserve explicit output choice. Physical mobile keyboards, a screen reader, physical browser zoom and new live model/OCR runs were not part of this frontend verification. The build retains large-chunk warnings from the existing rendering stack. These are disclosed follow-up considerations, not claims of completed testing. The separate [agent streaming/harness research plan](docs/AGENT-STREAMING-REFINEMENT-PLAN.md) remains a proposal for broader backend work.
