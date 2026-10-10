# MatOpt native ZCode frontend migration

## Current design (2026-10-09)

The subsequent [interactive conversation design](INTELLIGENT-UI.md) replaces the
permanent three-pane shell with a MatOpt-owned chat/sidebar/optional-canvas
layout and system typography. ZCode primitives, source attribution and licenses
remain. The native stylesheet now also restores keyboard focus outlines;
PromptInput is adapted to preserve newer drafts and attachments when an earlier
submission is accepted. The provenance manifest identifies these as reviewed
adaptations, not exact upstream copies. The sections below record the original
migration and its historical verification checkpoint.

## Original migration specification

This specification superseded the earlier portable-component theme adaptation.
The user requested replacement of the existing frontend using ZCode source,
including its workspace layout, sidebar, task rows, header, composer, Markdown,
LaTeX, code frames, controls, typography and full native stylesheet.

Upstream is pinned at `29628c9acdb81b703bbd4080c207a0e7ce5e276e` (Apache-2.0).
Native portable components are copied whole. Desktop RPC orchestration is
replaced at explicit service boundaries by MatOpt HTTP/SSE adapters; source
render markup and utility classes are preserved. The brand remains MatOpt.
Unsupported terminal, Git, account and remote features are omitted.

Native layout places task navigation at the left, the task/composer column
next, and a resizable file/result/trajectory pane at the right. Small screens
select a single pane. Files and mathematical outputs use native controls and
styles. Legacy frontend styling is removed from the active dependency graph.

Saved task state, guarded SSE overlays, task-scoped file requests and durable
trajectory/usage retain their existing backend owners. Task switching must
prevent stale output and file previews. New task detaches the old overlay
immediately. The execution model remains exclusively `deepseek-flash` with
reasoning off/low/high/max; historical ledger requests stay unchanged.

Acceptance: TypeScript production build; meaningful frontend regression
checks; browser verification of tasks, streaming/stop, files/source/formulas,
math/code/tables, appearance settings, resize and narrow-screen layout.
Preview fixtures use disposable data and scripted provider responses.

## Migrated source and bindings

- `frontend/src/zcode/styles.css` is the complete upstream stylesheet.
  Only the Streamdown dependency scan path and MatOpt source scan are adapted.
- The native PromptInput, attachment, task/loading components and 24 UI
  primitives are byte copies. The full native CodeViewer uses its supported
  main-thread WASM route after the worker route produced blank rendered code.
- The native desktop frame, sidebar/header/task rendering subtrees, Markdown
  response, tables, code frames, Mermaid, lightbox, reasoning and locale sources
  are preserved. MatOpt replaces desktop service calls at documented boundaries.
- MatOpt workbench files, data tables, formulas, Plotly/Chart.js figures, actions,
  trajectory and usage use native controls and theme variables. Plotly follows
  pane resizing and appearance changes while preserving supplied figure options.
- Narrow screens show one pane. The sidebar has native caption controls,
  backdrop/Escape dismissal and an inert background; selecting or creating a
  task returns to the agent pane.

The old frontend components, pages, stylesheet/token layers and generated DSH
UI copies were deleted. The native frontend and public assets are self-contained;
frontend Docker builds use the checked-in npm lockfile. Node.js 22+ is required.

The [consolidated source manifest](reference/zcode-frontend-manifest.json) and
[provenance guide](reference/zcode-frontend-provenance.md) distinguish exact
copies from reviewed adaptations. Licenses and source attribution are retained.

## Verification commands

```sh
cd frontend
npm test
NODE_OPTIONS=--max-old-space-size=2048 npm run build
cd ..
bash scripts/vendor_check.sh
python3 scripts/vendor_zcode_frontend.py --check --source-repo /path/to/ZCode
python3 -m unittest discover -s scripts/tests -p test_vendor_zcode_frontend.py
git diff --check
```

The npm tests run sequentially with a 512 MB heap cap and bounded test timeouts.
They inspect actual rendered native code as well as task-scoped file races,
composer/IME/Stop/steering, math/currency/code preservation, menus, mobile panes,
theme changes and chart resize cleanup.

Plotly was updated to 4.1.1, whose [official release](https://github.com/plotly/plotly.js/releases/tag/v4.1.1)
updates its map dependency. Its [v4 migration](https://github.com/plotly/plotly.js/releases/tag/v4.0.0)
removes deprecated Mapbox traces; old imported `*mapbox` figures require
equivalent `*map` traces. MatOpt uses KaTeX for document mathematics.

## Verified migration checkpoint (2026-10-01)

| Check | Result |
| --- | --- |
| Frontend tests | 131 passed, 0 failed; 12.93 seconds |
| TypeScript and production build | Passed; Vite build completed in 14.58 seconds |
| Offline and pinned-source provenance | All 1,373 manifest targets verified |
| Provenance checker regression tests | 7 passed |
| Patch whitespace | `git diff --check` passed |

The built frontend was exercised against an isolated local FastAPI server with
a scripted provider and disposable task data. Browser checks covered HTTP/SSE
Send and Stop, task selection, the Flash-only effort menu, files and highlighted
source, unchanged display-math annotations, results and trajectory, both native
themes, keyboard pane resizing, chart resizing and a 390-by-844 mobile viewport.
The mobile viewport had no horizontal overflow or KaTeX errors. No console
errors or warnings were observed in the completed browser checks.
The final scientific-fence regression verifies that Plotly, Chart.js and chart
headers reference shipped SVG assets, avoiding missing-icon requests.

These checks did not make a live DeepSeek API call or build a Docker image.
The production build still reports large JavaScript chunks, including Plotly
and the native rendering dependencies; bundle-size optimization remains open.
