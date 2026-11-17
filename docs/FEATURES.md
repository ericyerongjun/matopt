# MatOpt: implemented features and technical challenges

MatOpt is a domain-specific assistant for mathematics, statistics, machine
learning and computer science. It combines a pretrained DeepSeek model with
computation, documents and interactive explanations. It does not train or
fine-tune an LLM. The [README](../README.md) lists the group and Docker startup
steps; [DEMO.md](DEMO.md) supplies reproducible demonstration prompts.

## Basic features

The chat interface accepts domain questions, retains conversation context and
supports follow-up explanations. System prompts and analytical skills guide
the model toward the selected subject area. Markdown and mathematical notation
make answers readable. English is supported, with multilingual interaction
available through the language controls and the model. Model responses can be
wrong, so the interface also exposes computation and optional checking.

## Additional features and integration

### Mathematical rendering

**Capability.** Inline and display equations, aligned derivations, fractions,
sums and equation labels render within explanations, tables and interactive
surfaces. Ordinary currency text remains text.

**Implementation.** `frontend/src/utils/markdownNormalize.ts` normalizes model
output before Markdown parsing. `mathRenderOptions.ts` supplies shared KaTeX
options; `MarkdownRenderer.tsx`, `InlineMathText.tsx` and the ZCode Markdown
adapter use the mathematical rendering path. Focused rendering tests cover
delimiters, labels and edge cases.

**Challenge and response.** Models emit different LaTeX delimiters and streaming
chunks can end in incomplete expressions. Separate answer, inline-label and
workspace renderers also risk interpreting the same expression differently.
Normalization and common rendering options reduce these inconsistencies, while
tests distinguish mathematics from literal dollar amounts. The Docker rehearsal
rendered 20 KaTeX/MathML expressions without KaTeX error elements and preserved
`Prices are $5 and $10.` in a separate check.

KaTeX can also mutate its macro dictionary while processing commands such as
`\tag`. Sharing that dictionary between equations caused state leakage and
circular data that broke the streaming renderer's options comparison. The
shared options helper now returns a fresh macro dictionary for each read,
preserving common settings without sharing mutable equation state.

**Limits.** KaTeX supports a subset of LaTeX. Arbitrary packages and malformed
expressions are not guaranteed to render; rendering does not verify mathematics.

### Persistent Python with controlled execution

**Capability.** A conversation can load data, compute a result and reuse its
variables in later turns without loading or defining them again. Figures and
files become inspectable workspace artifacts.

**Implementation.** `backend/app/agent/kernel.py` manages a separate IPython
kernel for each conversation. Kernel startup uses a private IPython configuration
directory, preventing workspace startup files from becoming implicit executable
configuration. `sandbox.py`, tool dispatch and execution rules control launched
processes; child environments filter sensitive settings. Output capture is
bounded, and execution results record status and elapsed time.

**Challenge and response.** Launching a fresh interpreter for every call would
lose state, but retaining a process introduces lifecycle, cancellation and access
control concerns. The kernel manager keeps computation state tied to its
conversation and uses the same execution boundary for later calls. Docker
provides the deployment boundary; native development uses platform-specific
sandbox wrappers where supported.

**Evidence and limits.** After fitting the bundled data, a follow-up executed
only `print(len(x), A, A/50)` and returned the existing values, including
`A = 2.006449352686209`. Memory-only variables do not survive a backend restart.
Compose runs all conversations inside one backend container: it is not a
separate security boundary for each conversation and is intended here for a
local assessment demo. The default Compose configuration uses external container
isolation, not nested bubblewrap.

### Interactive visualizations in a restricted runtime

**Capability.** Native surfaces can combine equations, tables, plots, diagrams
and controls. Generated HTML/JavaScript mini-apps support richer simulations;
outputs can be viewed inline, expanded into the canvas or opened from Files.

**Implementation.** `backend/app/ui/` validates surface specifications and
declared actions. `frontend/src/features/intelligent-ui/` renders native surfaces
and mini-app frames. The `ui-sandbox` Compose service hosts the HTML runtime on
port 3001, separately from the main application. Generated documents run in an
iframe with `allow-scripts` but without `allow-same-origin`. Supported Plotly
references are mapped to the bundled local library. Registered actions provide
controlled recomputation instead of giving arbitrary preview scripts backend
access. [INTELLIGENT-UI.md](INTELLIGENT-UI.md) describes the contracts.

**Challenge and response.** Generated pages often assume unrestricted CDN
access or access to the parent application. Those assumptions fail inside the
preview boundary. A separate runtime, supported local libraries and explicit
state/action messages provide a compatible route. Development also required
repairing generated label updates, angle units and an animation clock, then
checking the controls in the actual restricted preview.

**Evidence and limits.** A Plotly preview rendered the 12-point dataset and
updated its fitted curve when the amplitude slider moved. A repaired Canvas
example advanced its animation and froze at zero speed. Arbitrary libraries and
network requests may be blocked. Generated code can still contain bugs; a saved
file is not automatically a published surface. Rehearsal found unsupported
publication claims, so the prepared HTML examples provide a checked Files-preview
fallback. This limitation remains open.

### Agent execution and convergence

**Capability.** The agent can inspect files, compute, research and revise results
while reporting observable progress. Stop, pause/resume and steering controls
let the user manage longer tasks. Optional subagents can divide work.

**Implementation and challenge.** Early tool use could keep expanding without
reaching a useful stopping point. MatOpt adapted mechanisms from open-source
agent systems, including OpenAI Codex, into its Python harness. It did not embed
the complete Codex application. Shared model/tool/time budgets, operation-specific
repeated-failure handling, bounded verification, context compaction, cancellation
and limited subagent depth constrain execution. `harness.py`, `execution.py`,
`compaction.py` and `subagents.py` implement these mechanisms. Upstream boundaries
and attribution are recorded in [CODEX-INTEGRATION.md](CODEX-INTEGRATION.md) and
[NOTICE](../NOTICE).

**Limits.** A budget bounds work; it does not prove convergence or correctness.
Repeated successful but unhelpful actions can still waste time. Optional
**Check results** permits at most two checks and one full correction per checking
episode, and an LLM checker can miss errors. Lean proof checking is optional and
requires a separately installed toolchain; the default Docker image does not
provide it.

### Speech and animated 3D tutor

Browser audio capture connects to local faster-whisper transcription and Kokoro
speech synthesis in `backend/app/speech.py`. Frontend voice hooks coordinate
read-aloud, hands-free conversation and interruption. Three.js/VRM code under
`frontend/src/features/avatar/` displays a tutor with speaking, listening and
working states, blinking and approximate mouth motion. Users may load a custom
VRM avatar, subject to its asset license.

Coordinating capture, playback and model work requires state transitions so
interrupted audio does not keep playing as a new reply starts. First-use model
downloads and CPU inference also affect responsiveness. Local speech preflight
passed and browser read-aloud entered playback during rehearsal; microphone
interaction and audio intelligibility must still be checked on the presentation
device. Mathematical speech and approximate lip synchronization have limitations.

### Document and image understanding

Page-oriented document processing retains original uploads, extracted Markdown
and page-coverage information. PDF rasterization and vision/OCR processing allow
the assistant to work with scanned content as well as text. Source references
and previews let users inspect what was read. Processing is implemented under
`backend/app/agent/tools/` and exposed through the workspace/document interface.
OCR errors, unreadable pages, API availability and per-page processing time remain
limitations; partial extraction must not be described as complete understanding.

### Durable conversations and execution controls

SQLite-backed runs and session events preserve messages, files and observable
execution state. Server-sent events stream progress and results; sequence numbers
and replay handling support reconnection without intentionally rerunning tools.
`backend/app/agent/runs.py`, session modules and the frontend stream reducer
separate safe public activity from raw diagnostics and private model reasoning.
Persistence of the conversation does not imply persistence of Python memory.

Optional execution rules, approvals and guardian review apply policy to tool
use. Container hardening, loopback-only service ports and filtered child
environments reduce exposure. These mechanisms are not a claim of complete
isolation or an exhaustive security assessment.

### Memory, skills, integrations and task modes

Session compaction and recall support long conversations. Optional personal
memory adds cross-chat retrieval and structured notes, with per-chat controls.
Memory can miss facts and should not be treated as an authoritative knowledge
base. Slash-command skills provide reusable analytical workflows; MCP connects
external tools through discovery and explicit schemas. Such connectors require
their own services and credentials. Bundling a skill does not mean every external
integration was demonstrated.

**Mat** emphasizes explanation and computation, **Opt** supports workspace
changes, and **Auto** selects a route per turn. Optional scheduling, background
jobs, workspace change views and usage diagnostics support longer workflows
while the backend remains running. **Ultrafast Demo** is the default response
profile and the only profile toggle; off uses the configured model default.
There is no manual reasoning-effort menu and no guaranteed response latency.

## Demonstration evidence and remaining work

[DEMO-VERIFICATION.md](DEMO-VERIFICATION.md) records the real Docker/model
rehearsal, including incorrect model prose, generated-code repairs and failed
publication claims. It distinguishes observed behavior from code inspection.
The bundled data are synthetic, and the HTML examples are prepared fallbacks.
No independent user study or general accuracy, safety or latency guarantee is
claimed. The project remains under development.
