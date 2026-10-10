# MatOpt interface reference research

Verified 2026-10-10. Scope: frontend design references and concrete adaptations; no backend changes.

## Evidence boundary

Official product announcements, changelogs, and help pages were read directly. Public image URLs and their publisher captions are linked below. The computer-use browser inventory returned no available browsers, so no authenticated products or rendered demos were inspected in this pass. The OpenAI and Grok public pages expose demo controls/content in their HTML, which was inspected. Exact typefaces, colors, pixel dimensions, motion timing, and responsive behavior are **not measured product facts** here. Proposed MatOpt values below are design choices. Marketing imagery is not proof that every account has the same UI or capability.

## Verified references

| Reference and date | Verified pattern | MatOpt adaptation |
| --- | --- | --- |
| [Claude: Cowork and chat are one Claude](https://claude.com/resources/articles/cowork-is-now-claude), 2026-09-16 | A single conversation accepts quick questions and substantial work. Docs, slides, and design outputs stay connected to that conversation. The rollout differs by plan. | Keep one composer and task context. Express the change from answering to working through status and outputs rather than a mandatory chat/agent mode switch. |
| [Claude artifacts help](https://support.claude.com/en/articles/17153992-what-are-artifacts-and-how-do-i-use-them), undated living documentation, accessed 2026-10-10 | A reusable output opens beside the conversation and remains discoverable from the sidebar’s Artifacts area. Users can revise an output through conversation or direct editing where supported. | Conversation is the explanation; an optional workspace panel holds the selected durable output. Ordinary Markdown remains in the answer. File/output links should open the corresponding preview and retain selection. |
| [Claude Dashboards and Motion](https://claude.com/resources/articles/dashboards-and-motion), 2026-10-08 | Dashboard numbers can reveal their underlying query; charts report refresh time. Official image captions show a dashboard and a `/motion` command menu. | Give structured results clear titles and provenance affordances when existing metadata supplies them. Keep skills/tools in the existing slash menu. Do not add fabricated freshness labels or imply a new data connector. |
| [Cursor 3 announcement](https://cursor.com/blog/cursor-3), 2026-04-02 | Agents from multiple repositories and execution environments are organized in a sidebar; files, browser, and review views remain available for deeper inspection. | A compact task navigator should show location and state, with the current conversation inside a distinct working area. Keep existing file/result panels available without turning every message into a card. |
| [Cursor full-screen tabs and compact chats](https://cursor.com/changelog/3-4), 2026-05-13 | A work panel can expand to the working area with a floating prompt bar. Compact, balanced, and detailed activity densities are documented. | Default to a concise collapsed activity summary. Expansion reveals the existing safe public actions. Give the result panel a deliberate expanded mode while retaining a usable composer and a clear return action. |
| [Cursor release notes](https://prod.cursor.com/docs/release-notes), Agents Window 3.23, 2026-10-01 | Chat find follows focus; slash menus announce item/position; collapsible section headings expose expansion state; transcript controls have keyboard focus indicators. | Make search scope, keyboard focus, selected slash command, and panel expansion state unambiguous. Preserve draft and panel scroll positions when changing context. Treat these as acceptance conditions, not optional polish. |
| [Grok Build Agent Dashboard](https://x.ai/news/agent-dashboard), 2026-06-15 | A CLI/TUI dashboard groups sessions by status, prioritizes requests for input, and rolls subagents under their parent session. Its public demo lists task title, current action, and age, with a bottom dispatch input. | Status should explain what the user needs to do. Use compact row metadata for running/paused/complete/failed states already available, and keep child activity under its task. Do not claim that this is a verified Grok web layout or add unsupported cross-chat run data. |
| [Gemini Canvas announcement](https://blog.google/products-and-platforms/products/gemini/gemini-collaboration-features/), 2025-03-18 | Canvas pairs conversation with an editable document/code surface and live preview. | The output workspace should support sustained reading and inspection rather than squeezing a document into a narrow attachment card. This is a layout reference, not authority for new editing/export capabilities. |
| [Gemini Enterprise Canvas help](https://support.google.com/g/answer/16914858?hl=en), updated 2026-07-24 | Documents/slides are worked on beside chat; `/` can open the tool shortcut list. This source describes Gemini Enterprise Business edition. | Keep the existing slash-first interaction and make its discovery hint legible inside the composer; avoid claiming this exact interaction is universal across Gemini products. |
| [OpenAI Intelligent UI](https://openai.com/index/gpt-6-for-everyone/), 2026-10-07 | The public page contains a bicycle explainer with system-selection buttons and a roast-planning example with a guest-count control, computed quantities, a copy action, and a checklist. The text documents progressive native components and partial answers. | Organize rich answers around one useful interaction, with text and controls in the same reading flow. Existing native results can use consistent headers, spacing, selected states, and safe progressive entry. A generic dashboard of unrelated cards would miss the pattern. |

## Public visual references

These are publisher-hosted images discovered through their source pages. Their pixels were not rendered by the available browser tools in this pass; use them for the next visual critique, rather than claiming exact visual measurements from them.

- [Claude bike-share dashboard image](https://assets.claude.com/7688d4d3b888c17cf4b3553f537f291882a92656.png), from the October 8 announcement.
- [Claude `/motion` composer menu image](https://assets.claude.com/46f88e8de631c732fa245ad453262e6ef35c0e73.png), from the same announcement.
- [Cursor agent tabs image](https://cursor.com/marketing-static/_next/image?q=70&url=https%3A%2F%2Fptht05hbb1ssoooe.public.blob.vercel-storage.com%2Fassets%2Fchangelog%2Fagent-tabs&w=1920), from the April 2 changelog. This is an editor multi-chat example, not necessarily the default Agents Window.
- OpenAI’s [announcement demo](https://openai.com/index/gpt-6-for-everyone/) exposes actual labeled controls in the fetched page, but their rendered styling and behavior still need browser verification.

## Recommended MatOpt direction

These are design recommendations, not copied product specifications.

1. **Compact navigator, spacious work.** Use a narrow, quiet sidebar for task history, search, and workspace context. Place the main conversation in an inset working canvas. Keep useful chrome compact while preserving an answer line length near 65–80 characters. Let tables and plots use wider local regions without widening every prose paragraph.
2. **One answer document.** Assistant prose should read as a coherent document. User prompts can use a muted surface. Reserve stronger containers for code, files, native UI, and contextual actions; do not box every paragraph or place redundant labels above every message.
3. **Three clear visual layers.** Navigation, work canvas, and elevated controls need distinct but restrained surfaces in both themes. Use borders and small tonal differences first. A single cool accent should identify focus, selection, or live state; it should not tint every card or heading.
4. **System-sans hierarchy.** Retain the user’s preference for ChatGPT-aligned typography with a native sans stack rather than assuming access to proprietary fonts. Proposed starting points: 13px navigation, 14px controls, 16px answer text, approximately 1.65 prose line height, moderate heading weights, monospace only for source/code. Test rather than assuming these values fit every display density.
5. **Composer as a dock.** Give the input a calm, generous writing area; keep attachment, model, slash affordance, and send/stop in a stable secondary row. Avoid an always-visible toolbar of tools. Focus, file drag-over, disabled send, upload progress, and active steering need different accessible states.
6. **Activity as orientation.** Present the current public milestone and elapsed work in one compact row. Expand to the existing public action list on demand. Completed activity should settle quietly; paused, reconnecting, cancelled, and failed states should not keep spinning. Do not expose reasoning or worker messages for visual density.
7. **Results as a workspace.** Dock the selected file, artifact, or UI result beside the conversation; allow expansion and a clear close/return action. Show a meaningful title and only supported actions. On narrow viewports, make it a deliberate alternate surface rather than a crushed third column.
8. **Motion follows state.** Use brief transitions for opening menus, changing panel width, and settling activity. Keep live motion localized to an actually active state; respect reduced motion. Never replace useful partial content with a large loading skeleton on final reconciliation.

## Visual critique and acceptance matrix

Evaluate the actual application in light and dark themes at a desktop width, a compact laptop width, and a mobile width. Keep fixtures identical between rounds.

| Fixture | What must be visually and behaviorally clear |
| --- | --- |
| New task | Primary writing action; hierarchy without a marketing hero; balanced canvas and sidebar density. |
| Long answer with table, KaTeX, source block | Readable prose measure; deliberate table alignment; math/code horizontal overflow confined locally; literal source stays literal. |
| Running task with safe partial results | Current action, truthful state, compact log disclosure, visible useful output, stable composer. |
| Paused/reconnecting/stopped/failed | Distinct label and correct controls; no false success icon or continuing spinner; retained useful content. |
| File/plot/native-result panel | Readable preview, clear selected output, strong title/action hierarchy, easy close and expand, scroll position retained. |
| Keyboard-only flow | Visible focus, operable slash list and disclosures, correct focus return after closing menus/panels, no hidden focused control. |
| Final reconciliation | No transcript flash, disappearing partial content, draft loss, or unexpected forced scroll while the user reads earlier content. |

Suggested major rounds: (1) structure and density, (2) answer/composer typography, (3) activity/result workspace hierarchy, (4) dark/responsive/accessibility, (5) consistency and removal of unnecessary decoration. Score each against the same fixtures and preserve before/after evidence. A self-assigned numeric score is a critique aid, not evidence that the product is objectively equivalent to a commercial platform.
