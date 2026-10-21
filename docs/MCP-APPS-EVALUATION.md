# MCP Apps evaluation

Should MatOpt's interactive results (see [INTELLIGENT-UI.md](INTELLIGENT-UI.md))
also be exposed as **MCP Apps**? MCP Apps is the stable MCP extension
`io.modelcontextprotocol/ui`, spec SEP-1865 / ext-apps `2026-01-26`. Under it, a
host such as Claude, ChatGPT, VS Code or Goose renders a tool's `ui://` resource
in a sandboxed iframe and talks to it over JSON-RPC `postMessage`.

**Decision: go. Implemented.**
- Mini-apps can be viewed, keep their saved controls, and recompute in Python
  through two app-only tools that are locked to one surface.
- Native surfaces stay text-only.
- HTTP `/mcp` remains unauthenticated by default, the same as the rest of the
  local API. It now supports a bearer token or OAuth, which are required before
  exposing it beyond this machine.

The in-app rendering path (`frontend/sandbox`, `UISurfaceView`, REST/SSE) is
unchanged. The REST state endpoint now calls the shared `UIService.save_state`.

## What a host sees

| Item | Audience | Purpose |
| --- | --- | --- |
| `ui://matopt/mini-app-viewer.html` (`text/html;profile=mcp-app`) | host | Static view: `backend/app/ui/mcp_app_viewer.html` |
| `open_ui_app(conversation_id, surface_id)` with `_meta.ui.resourceUri` | model | Read-only. Mounts a saved mini-app. |
| `save_ui_state(conversation_id, surface_id, token, revision, state)` | app | Persists declared controls (`UIService.save_state`) |
| `run_ui_action(conversation_id, surface_id, token, action_id, revision, values, idempotency_key)` | app | Runs a registered `compute(values)` (`UIService.prepare_action`/`actions`) |
| Every other tool (`python`, `bash`, files, …) | model | `_meta.ui.visibility: ["model"]`, so views cannot call them |

What `open_ui_app` returns:
- `content`: the readable fallback, which is what the model and text-only
  clients see.
- `structuredContent`: id, revision, kind, title and state.
- `_meta["matopt/surface"]`: the HTML, state, `computed_state`, action manifests
  and a surface token. This goes to the view only.

Action results return the new state and fallback, but never the HTML or the
token.

## Mapping

| MatOpt | MCP Apps | Status |
| --- | --- | --- |
| `mini_app` HTML/CSS/JS | `ui://` view mounts it from the tool result | Done. Same `window.matopt` API and HTML handling as `runtime.js`. |
| Text `fallback` | `content` text (graceful degradation, SEP-2133) | Done |
| `getTheme` / `onThemeChange`, `--matopt-*` tokens | `hostContext.theme`, `styles.variables` (`--color-background-primary`, `--color-text-primary/secondary`, `--color-border-primary`, `--color-background-secondary`, `--font-sans`), `host-context-changed` | Done |
| `resize()` | `ui/notifications/size-changed` | Done |
| `setState` (saved via `PATCH …/state`, revision) | Debounced `save_ui_state` when the host has `serverTools` | Done. A conflict adopts the server's revision and retries. A busy conversation retries after 3 s, up to 5 times. Edits during a computation save afterwards. |
| Host action buttons, then Python in the conversation kernel | Viewer buttons, then `run_ui_action` | Done. Same revision check, kernel lease (`WORKSPACE_BUSY`), cancellation, receipt replay by idempotency key, and failure preservation. Pending edits save first. A notice shows when the controls differ from the computed inputs. |
| Explain | `ui/message` when the host has `message` | Done. Same text as the in-app Explain. |
| Model sees current parameters | `ui/update-model-context` when supported | Done. Sent on mount, save and recompute. |
| Export JSON, downloads | none | MatOpt-only |
| Streaming previews | `tool-input-partial` streams arguments only | Not mapped |
| `native` surfaces | Would need the React, Plotly, KaTeX and Mermaid renderer bundled into one HTML file | Deferred. Text fallback. |
| Blob Web Workers | The default host CSP has no `blob:`. Chromium constructs the worker, then fires `error` (`worker-src`). It does not throw. | `create_ui_app` guidance now says to fall back to the main thread on the worker's `error` event |

## Security

MatOpt's own runtime gives each mini-app an opaque origin and no host verbs.
Under MCP Apps the host enforces the sandbox. MatOpt declares no
`_meta.ui.csp`, so the host applies the restrictive default (`connect-src
'none'`). Web hosts use a double-iframe proxy with `allow-same-origin` on the
host's sandbox origin, so the view is not opaque there. Use MCP Apps only with
hosts the user already trusts with the conversation.

Generated mini-app HTML is model-authored, so it is treated as hostile. The
controls:

1. **Tool reachability.** Only `save_ui_state` and `run_ui_action` are
   app-visible. Visibility is not access control, so both also require an
   HMAC-SHA256 token over `(conversation_id, surface_id)`. Its key lives in
   `DATA_DIR/mcp_apps.key` (mode 0600) and survives restarts. The token cannot
   address any other surface, and a missing or wrong token changes nothing.
2. **The token stays out of generated code.**
   - Only the mounting result carries it, and that message is dispatched before
     any generated script runs.
   - The viewer keeps the token in a closure.
   - It captures `parent.postMessage` before the app runs, so replacing
     `window.parent` cannot intercept outgoing calls.
   - Later results omit the token and the HTML.
3. **No computation without a user.** Viewer buttons live in a closed shadow
   root and act only on `isTrusted` clicks. Generated code cannot find them,
   and its scripted `click()` is ignored.
4. **Server-side validation is unchanged.**
   - State edits are limited to declared keys, control domains and 64 KiB.
   - Action inputs must match the registered JSON Schema.
   - Handler code and files are server-owned.
   - Stale revisions are refused.
   - An idempotency key cannot be reused with different values.
5. **Remaining host verbs.** Generated code can still post `ui/message` or
   `ui/open-link` itself, the same way a malicious page could. Hosts normally
   gate these behind user confirmation. Forged messages from inside the frame
   can only change view-local data, which `setState` already allows.
6. **Authentication on `/mcp`.**
   - `MCP_AUTH_TOKEN` enables a constant-time static bearer check, intended for
     VS Code, Goose and Claude Code over HTTP.
   - `MCP_OAUTH_ISSUER`, `MCP_OAUTH_JWKS_URI` and `MCP_PUBLIC_URL` (optionally
     `MCP_OAUTH_AUDIENCE` and `MCP_OAUTH_SCOPES`) make `/mcp` an OAuth 2.1
     resource server, which hosts such as Claude.ai and ChatGPT require.
     - JWTs from that authorization server are verified locally.
     - The 401 challenge and RFC 9728 metadata point to
       `/.well-known/oauth-protected-resource/mcp/`, with resource `<public>/mcp/`.
   - An incomplete OAuth configuration, or a token under 16 characters,
     disables the HTTP transport rather than serving it open.
   - MatOpt is single-user. Any authenticated principal may use every
     conversation, as with the REST API.

## Verification

- `backend/tests/test_mcp_apps.py` checks the protocol over an in-memory MCP
  client on a real `AgentHarness`:
  - audiences and MIME;
  - meta kept out of model text;
  - token scoping;
  - revision conflicts and the busy lease;
  - idempotent replay, schema validation and failure preservation.
- `backend/tests/test_mcp_auth.py` checks auth on the real `create_app()` mount:
  - the bearer token is rejected when missing or wrong, and accepted when
    right;
  - the OAuth challenge and metadata;
  - fail-closed configuration.
- `backend/tests/test_mcp_apps_stdio.py` launches `python -m app.agent.mcp`
  over stdio, as local hosts do. It reads the view, opens and saves, and shows
  HTTP auth settings do not affect stdio.
- `frontend/sandbox/mcpAppViewer.test.mjs` is a jsdom host covering:
  - handshake, theme and style variables;
  - the debounced save and conflict rebase;
  - trusted-click-only actions with save-before-compute;
  - result updates, Explain, failure text, fallbacks and teardown.
- A manual check in Chromium used a scratch host page that applied
  `sandbox="allow-scripts"` and the spec's default CSP. Slider, save, Recompute
  and the result display all worked. A hostile mini-app could not find the
  controls or intercept outgoing messages, and never saw the token. `fetch` and
  `blob:` workers were blocked by CSP.
- **Not yet tried:** an official host (`@modelcontextprotocol/ext-apps` basic
  host, Claude Desktop, VS Code) and a real OAuth authorization server.
