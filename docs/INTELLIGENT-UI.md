# Interactive conversations

MatOpt uses system sans-serif typography, a 256px collapsible sidebar, a centered
conversation and an optional canvas. The canvas opens a selected output; it
does not repeat a separate latest-answer transcript. Files and Activity open on
demand. Mobile presents one pane at a time and preserves the chat when returning
from canvas. Appearance and text-size preferences continue to apply.

Type `/` at a command boundary to search skills and tools. Arrow keys navigate;
Enter or Tab selects a removable command chip. Selection and dismissal preserve
the surrounding draft and attachments. `/web-search` and `/web-fetch` enable web
tools for the submitted turn, including steering. There is no persistent web
search toggle. Attachments, model effort and Send/Stop remain visible.

## Final answer and activity

Execution is private. The conversation displays one collapsed Working/Worked
row with fixed activity labels and a bounded verification result. Reasoning,
agent exchanges, tool code/stdout and verifier reports are absent from normal
chat, Activity and exports. Numeric usage remains available on demand.

Requested verification skips greetings, permits at most two checks and one
complete correction, and ends on a correct or inconclusive result. If the last
check still finds an issue, the answer is explicitly provisional; it does not
start another unchecked rewrite. Older partial corrections retain their prior
full draft behind a labelled disclosure instead of silently losing it.

The server completes internal work before publishing `final_start`, chunks of
the selected final answer, and its committed outputs. `message_end` replaces
the live snapshot. The client refreshes the saved conversation in place after
completion, retaining the user's draft, scroll position and any newer run.
The host reveals newly published text on a stable Markdown surface for at most
2.4 seconds. This uses the exact saved text; it does not simulate additional
model activity. Reduced-motion users and background tabs see the result
immediately. Follow-up suggestions do not prolong the running state.

Ordinary answer Markdown renders as headings, tables and mathematical notation.
Explicitly requested Markdown source stays in a labelled code fence; actual
code and ASCII diagrams keep their own fences. Image grouping preserves the
line boundaries around those blocks, including while the answer streams.

## Surface contract and rendering

`backend/app/ui/contracts.py` is the source of the version 1 contract. Generate
its public JSON Schema and TypeScript with:

```sh
cd backend && .venv/bin/python -m app.ui.generate
cd .. && python3 scripts/generate_ui_types.py
python3 scripts/generate_ui_types.py --check
```

A message contains ordered `{type: "ui", surface: UISurface}` parts. A surface
has a stable ID, increasing revision, title, readable fallback, `native` or
`mini_app` kind, current `state`, last `computed_state`, and declared actions.
Native nodes have stable IDs, a catalog type, JSON props and optional children.
The catalog includes Markdown, equations, comparisons, tables, Plotly figures,
strict Mermaid diagrams, tabs/disclosures, sliders, numbers, selects, toggles,
buttons, stacks and grids. `{$state: "key"}` binds a permitted prop to local
state without evaluating code. Numeric results should identify their units,
assumptions, data and sampling/seed choices.

The frontend advertises `ui_version: 1` on chat requests. The server advertises
`render_ui`, `create_ui_app` and `register_ui_action` only to a compatible client
when enabled. These internal tools are absent from the slash catalog. DeepSeek
remains the sole model provider. This is a MatOpt renderer inspired by the
interaction patterns in the design brief; it does not depend on a proprietary
OpenAI UI API.

Internally, streaming `render_ui` arguments compile only complete, validated
sections into `ui_start`/`ui_patch` events with full `{surface}` snapshots.
The chat stages these until final publication and exposes committed views only. Preview controls cannot execute actions. `ui_commit` enables a saved
surface; `ui_error` contains `{surface_id, revision, error, fallback}`. A failed
surface receives one repair attempt per revision, then readable text. Stopping
or losing the connection keeps committed results and settles incomplete views.
Final `message_end` snapshots and recovered history use the same message shape.

## State and computations

| Method | Path | Body / response |
| --- | --- | --- |
| GET | `/api/ui/capabilities` | `{version:1,native,mini_apps,node_types}` |
| GET | `/api/conversations/{cid}/ui/{sid}` | `{surface}` |
| PATCH | `/api/conversations/{cid}/ui/{sid}/state` | `{revision,state}` → `{surface}` |
| POST | `/api/conversations/{cid}/ui/{sid}/actions` | `{action_id,revision,values,idempotency_key}` → SSE |

Control edits are shared between inline and canvas views and saved after a short
debounce. They do not call the model. Edits during a chat or another computation
save when that work finishes. A changed computation input displays a notice
until the user recomputes; persisted `computed_state` keeps that distinction
after reload. Reset restores the view's initial controls. Explain explicitly
submits current parameters and the last result as a follow-up. Export downloads
the surface, parameters and data as JSON; conversation exports include readable
fallbacks and parameter information.

An action handler is registered by the agent as a self-contained Python module
defining `compute(values)`. Its versioned code, JSON input schema, required
workspace files, seed and timeout are persisted server-side. It returns a JSON
object containing `nodes`, `state`, `fallback` and/or `title`. The browser sends
only the registered action ID and validated values. Execution uses the existing
conversation kernel and notebook, shares the chat's execution lease, supports
Stop, and refuses stale revisions. Handlers reload their definitions and files
after a kernel restart. Missing inputs and failures preserve the prior result.
An idempotency key replays the original receipt; it cannot be reused for a
different request. Result revision and success receipt commit atomically.

SQLite stores immutable revisions, current state, versioned action definitions
and action receipts in additive tables. UI references/events also enter the
session ledger when enabled. Conversation reads hydrate current revisions;
later model context receives compact state/result summaries. Active-turn tool
calls keep their complete arguments until the answer finishes. Older completed
UI call/result pairs become labelled summaries, so omitted source cannot look
like a failed call requiring repair. Original requests remain auditable in the
ledger without replaying whole mini-app source into every subsequent turn.

## Isolated mini-app runtime

`frontend/sandbox` is a static server with only `/` and `/runtime.js`. It has no
API proxy, workspace mounts or credentials. The host embeds it with
`sandbox="allow-scripts"` and an opaque origin. HTTP CSP blocks network calls,
child frames, forms and external assets. The host's `frame-src` policy also
prevents scripted navigation to the main app or an external site. PDF previews
render one bounded page image at a time through the backend, with page navigation
and the original download available. They do not require a browser PDF plugin.
Existing generated HTML previews use the same isolated runtime as new mini-apps.

Only complete self-contained HTML runs. Use inline CSS/JS and data/blob assets;
external libraries and CDNs are unavailable. Inert JSON script blocks are
preserved. Heavy local simulations should use blob Web Workers, falling back
to the main thread on the worker's `error` event: external MCP Apps hosts may
block `blob:` workers, and the constructor does not throw (see
[MCP-APPS-EVALUATION.md](MCP-APPS-EVALUATION.md)). The runtime
exposes:

```js
window.matopt.getState();
window.matopt.setState({ frequency: 3 });
const unsubscribe = window.matopt.onStateChange(state => draw(state));
window.matopt.getTheme(); // "light" or "dark"
window.matopt.onThemeChange(theme => redraw(theme));
window.matopt.resize();
```

Theme variables are `--matopt-bg`, `--matopt-text`, `--matopt-muted`,
`--matopt-border` and `--matopt-control`. A frame-specific channel and source
window bind all bridge messages to their surface. The host validates declared
state keys, bounded JSON and resize values. Frames have no bridge method for
server computation, downloads, navigation or model calls; the host supplies
those controls.

`npm run dev` starts ports 5173/5174 together. Compose serves frontend port 3000
and sandbox port 3001. For a remote HTTPS installation set the separate HTTPS
`UI_SANDBOX_ORIGIN` for Compose; it is supplied to both the frontend build and
the Nginx frame policy. A manually built frontend uses
`VITE_UI_SANDBOX_ORIGIN`; its web server must set the matching restrictive
`frame-src <sandbox-origin> blob:` header. A single-container backend deployment
needs the standalone sandbox service as a companion; it must never proxy the
backend. An unavailable runtime shows the text fallback.

`UI_NATIVE_ENABLED` and `UI_MINI_APPS_ENABLED` independently disable generated
UI kinds. The client checks capabilities before rendering stored active views;
disabled or unavailable views remain readable as text.

## Reproducible validation

```sh
cd frontend && npm test && npm run build
cd ../backend && .venv/bin/python -m pytest -q
cd .. && backend/.venv/bin/python scripts/check_ui_math.py
python3 scripts/generate_ui_types.py --check
bash scripts/vendor_check.sh
```

The mathematical smoke test uses real disposable kernels and checks Taylor
points, CLT moments/seeded restart, ridge coefficients, CSV inputs, idempotency
and failure preservation. To inspect the same examples in a browser without
using a model API or existing conversations:

```sh
backend/.venv/bin/python scripts/preview_intelligent_ui.py --port 8001
# In a second terminal:
cd frontend && MATOPT_API_ORIGIN=http://127.0.0.1:8001 npm run dev
```

The preview backend always creates temporary data and a scripted provider.
Stop it and restart the frontend without `MATOPT_API_ORIGIN` to reconnect to
the usual backend. Test runners need local socket permission for Jupyter and
the isolated runtime integration check. The build retains large lazily loaded
scientific-renderer bundles; a successful build is not a bundle-size benchmark.
