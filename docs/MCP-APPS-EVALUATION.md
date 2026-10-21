# MCP Apps evaluation

Should MatOpt's interactive results (see [INTELLIGENT-UI.md](INTELLIGENT-UI.md))
also be exposed as **MCP Apps**? MCP Apps is the stable MCP extension
`io.modelcontextprotocol/ui`, spec SEP-1865 / ext-apps `2026-01-26`. Under it, a
host such as Claude, ChatGPT, VS Code or Goose renders a tool's `ui://` resource
in a sandboxed iframe and talks to it over JSON-RPC `postMessage`.

**Recommendation: go, staged.** Read-only mini-apps map almost directly and are
prototyped here. State persistence and Python recompute can follow behind
app-only tools. Native surfaces stay text-only for now. Remote exposure of `/mcp`
is a **no-go** until the server has authentication. This is a property of the
kernel server itself, not of MCP Apps.

## Prototype

- `backend/app/ui/mcp_app_viewer.html` is a static view served as
  `ui://matopt/mini-app-viewer.html` with MIME `text/html;profile=mcp-app`.
  FastMCP 4.1.3 (already pinned) sets the MIME for `ui://` URIs.
- `open_ui_app(conversation_id, surface_id)` on the kernel MCP server (`/mcp`,
  stdio `python -m app.agent.mcp`) declares `_meta.ui.resourceUri` for that view.
  It is read-only: it reads the saved surface from `UIStore` and never runs a
  handler.
  - `content` holds the readable fallback, which is what the model and
    text-only clients see.
  - `structuredContent` holds a small summary: id, revision, kind, title and
    state.
  - `_meta["matopt/surface"]` holds the HTML and state for the view only, so up
    to 512 KiB of HTML stays out of model context.
- The view runs `ui/initialize` (protocol `2026-01-26`), then sends
  `ui/notifications/initialized`. It then waits for
  `ui/notifications/tool-result` and mounts the mini-app with the same
  `window.matopt` API and HTML handling as `frontend/sandbox/runtime.js`.
  - Theme comes from `hostContext.theme` and `host-context-changed`.
  - `resize()` sends `ui/notifications/size-changed`.
  - `ui/resource-teardown` gets a reply.
  - Errors, native surfaces and missing meta show the text fallback.
- Every tool on the server, including `open_ui_app`, is stamped
  `_meta.ui.visibility: ["model"]` (see Security).
- Tests:
  - `backend/tests/test_mcp_apps.py` covers the protocol: tool meta,
    resource MIME, a self-contained view, meta kept out of model text, and
    degradation for native, unknown, invalid and disabled surfaces.
  - `frontend/sandbox/mcpAppViewer.test.mjs` simulates a host with jsdom and
    covers the handshake, theme, rendering, declared-key filtering, fallback,
    foreign-source rejection, teardown and unknown methods.

The in-app rendering path (`frontend/sandbox`, `UISurfaceView`, REST/SSE) is
unchanged.

## Mapping

| MatOpt | MCP Apps | Fit |
| --- | --- | --- |
| `mini_app` surface: self-contained HTML/CSS/JS | `ui://` resource plus tool result | **Direct.** Inline-only HTML already fits the host's default CSP. |
| Text `fallback` | `content` text (graceful degradation, SEP-2133) | Direct |
| `revision`, `state` | `structuredContent` | Direct |
| `matopt.getTheme` / `onThemeChange` | `hostContext.theme` / `host-context-changed` | Direct. `hostContext.styles.variables` could later feed `--matopt-*`. |
| `matopt.resize` | `ui/notifications/size-changed` | Direct |
| `matopt.setState` (host saves via `PATCH …/state` with `revision`) | View-local only in the prototype | **Adapt.** Add an app-only `save_ui_state(cid, sid, revision, state)` tool that calls `UIStore.patch_state`. The revision check gives the same optimistic concurrency. |
| Host-owned action buttons, then `POST …/actions` (SSE), then Python `compute(values)` in the conversation kernel | View calls `tools/call` on an app-only `run_ui_action` | **Adapt.** Reuse `UIService.prepare_action` / `actions()` unchanged. Map SSE `status` to progress notifications and the final `ui_commit` surface to the call result `_meta`. Stop maps to request cancellation. |
| Replay protection: idempotency key, request hash, revision, durable receipt | Same, server-side | **Carries over.** The key must be generated in the view (`crypto.randomUUID()`), so it is chosen by model-authored code and is not trusted. Its effect is bounded by the receipt hash check and the kernel lease (`WORKSPACE_BUSY`). |
| Explain (follow-up with parameters) | `ui/message` / `ui/update-model-context` | Adapt in the host bridge, not in the generated HTML |
| Export JSON, downloads | none (`ui/open-link` only) | Keep MatOpt-only |
| Streaming previews (`ui_start`/`ui_patch`) | `tool-input-partial` streams tool *arguments* | No useful mapping; previews stay in-app |
| `native` surfaces (catalog nodes, Plotly, KaTeX, Mermaid) | Needs a renderer bundled into one `ui://` HTML | **Defer.** It needs a single-file build of the React renderer plus scientific bundles (several MB, and no CDN under default CSP). Text fallback until there is demand. |
| Blob Web Workers, `blob:` images | Default host CSP has no `blob:` and no `worker-src` | **Gap.** Heavy simulations must degrade to the main thread. CSP domain lists cannot grant `blob:`. Document this in the `create_ui_app` guidance if exposure grows. |

## Security against the current boundary

What MatOpt enforces today:

- an **opaque origin** (`sandbox="allow-scripts"`, no `allow-same-origin`) on a
  dedicated static origin;
- an HTTP CSP with `connect-src`/`frame-src`/`form-action 'none'`;
- the embedding app's `frame-src` limits;
- channel and source binding;
- host-side filtering of declared state keys;
- a bridge with **no** method for computation, navigation, downloads or model
  calls.

1. **MatOpt stops enforcing the boundary itself.** Under MCP Apps the host
   applies the CSP and iframe sandbox. Web hosts use a double-iframe proxy with
   `allow-scripts allow-same-origin` on the host's own sandbox origin, so the
   view is not opaque. It may also have storage. MatOpt declares no
   `_meta.ui.csp`, which requests the restrictive default (`connect-src 'none'`).
   The viewer still strips `<base>`, `<link>`, `script[src]` and
   `meta[http-equiv]` as defense in depth. Accept this only for hosts the user
   already trusts with the conversation.
2. **The view gains host verbs.** Model-authored (and possibly prompt-injected)
   HTML can post `ui/message` (inject a prompt), `ui/open-link` (exfiltrate via
   URL, usually behind user confirmation) and `tools/call` to the server
   directly. The prototype's shim exposes none of these, but generated scripts
   can call `parent.postMessage` themselves.
3. **Tool reachability from the view is the critical risk.** Default visibility
   is `["model","app"]`, so a view could call `python`/`bash`. Hosts must reject
   app calls to tools without `"app"`, so the prototype stamps every tool
   `["model"]`, and a test asserts it. Any future app-only tool
   (`save_ui_state`, `run_ui_action`) must be narrowly scoped:
   - validate against the surface's declared keys and action input schema;
   - check the revision;
   - share the kernel lease;
   - never take code or paths from the view.

   Visibility is not access control. The model can call those tools too, which
   is acceptable because they are the same validated operations the REST API
   exposes.
4. **No authentication on `/mcp`.** The server already runs arbitrary Python and
   shell per `conversation_id`. That is fine for local stdio hosts (Claude
   Desktop/Code, VS Code, Goose) but unacceptable on a public URL, which is what
   Claude.ai and ChatGPT require. `open_ui_app` adds nothing beyond what `bash`
   already reads. Remote use needs OAuth plus per-conversation authorization
   first.
5. **Size and replay.** HTML is bounded at 512 KiB and the whole surface at
   1 MiB by `UISurface`. A view renders only the first tool result, so later
   messages cannot swap the app.

## Next steps (if pursued)

1. Add an app-only `save_ui_state`. The viewer calls it, debounced, from
   `setState` when the host advertises `serverTools`.
2. Add an app-only `run_ui_action`, wired to `UIService`. The viewer renders
   action buttons from `surface.actions`. Generated code in the same frame
   could also post the call, so server-side validation (point 3 under
   Security) remains the control, not the button.
3. Check against a real host (`@modelcontextprotocol/ext-apps` basic host, or
   VS Code) over stdio. That was not done here; the tests simulate the host.
