# Native ZCode rendering migration

The rendering source is taken from `zai-org/ZCode` commit
`29628c9acdb81b703bbd4080c207a0e7ce5e276e`. The complete native source files
live under `frontend/src/zcode`, with their original imports, classes and
third-party notices retained. This replaces the earlier selected-component
reimplementation in MatOpt's Markdown renderer.

`zcode-rendering-manifest.json` records the source path and SHA-256 for every
whole file copied or adapted by this migration task. Its 149 entries contain
142 exact copies after removing the provenance banner and seven adaptations:

- `components/ai-elements/message.tsx`: removes an unused desktop store import;
  adds a scientific fence content callback; configures the native math plugin
  with MatOpt's existing accessible KaTeX output and macros.
- `components/ai-elements/code-block.tsx`: adds a content slot inside the native
  code frame. Plotly and Chart.js retain native header/copy controls. Their
  content slot uses MatOpt's real chart renderers.
- `hooks/useServices.tsx` and `i18n/IntlProvider.tsx`: point type imports at the
  browser host's optional-service boundary. Native context/provider behavior
  and the complete English and Chinese dictionaries are retained.
- `lib/codePreviewPreferences.ts`: imports the unchanged settings type from
  its neutral native source module rather than the desktop store barrel.
- `components/ui/code-viewer.tsx`: selects the library's supported main-thread
  WASM highlighter with `disableWorkerPool`. The native frame, DOM, classes,
  highlighting and controls are retained; the host's worker path produced an
  empty code body during browser QA.
- `lib/fileDisplayHelpers.ts`: maps CSV and TSV extensions to the actual
  upstream `table.svg` asset, and JSON-based Plotly/Chart.js fence languages
  to `json.svg`, avoiding nonexistent extension-based icon URLs.

`MarkdownRenderer.tsx` is a thin web host wrapper around native
`MessageResponse`. It retains MatOpt's existing mathematical delimiter and
assistant-output preprocessing, passes the actual document theme, and routes
workspace artifact links/images, including `workspace://` references, to
same-origin files API URLs. HTTP links use
the native external-link component and a real browser open handler. Native
Markdown tables, lists, blockquotes, images/lightboxes, code viewers, Mermaid
diagrams and copy/download controls remain the upstream implementations.

The standalone `lib/markdownMath.ts` is an exact extraction of the native
message source's pure currency/math and code-fence guard. Only the public
export was added. This keeps MatOpt's existing mathematical preprocessing
independent from the rendering component dependency graph.

The narrow shared barrel exposes copied upstream DTOs and pure functions.
Their native relative dependency closure and the eight `model-option-map`
source files are copied whole. Browser adapters provide no desktop RPC
implementation. The application supplies the actual native Intl and Tooltip
providers; desktop platform/service providers are optional and absent.

Focused verification exercises the actual native response, including GFM
table/list/blockquote elements, native code framing and controls, scientific
fence slots, KaTeX output/macros/errors, currency and incomplete math, and
workspace URL/code-example handling. Native code highlighting, source updates,
line numbers, wrapping and real Workbench previews are mounted tests that read
the actual viewer shadow DOM. The test environment supplies only missing jsdom
DOM globals and layout APIs. Dialog interaction and visual layout are checked
in the application's browser QA.

`MessageList.tsx` binds the MatOpt message parts to the complete native Message,
Reasoning, Task, code detail and Attachment components. Model reasoning and
verification remain disclosed separately from final answer copy. `ModelPicker.tsx`
binds the fixed Flash configuration to native Radix menu radio items with keyboard
navigation, focus restoration, and closure when a turn starts. Both preserve their
existing MatOpt application prop contracts and have mounted behavior tests.
