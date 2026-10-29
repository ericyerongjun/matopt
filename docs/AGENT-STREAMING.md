# Live agent execution and final answers

MatOpt presents an agent run as observable work followed by one authoritative
answer. Progress and tool activity remain visible while execution continues;
private reasoning and agent conversations stay outside the public protocol.
The implementation keeps FastAPI, the existing provider adapters, and React.

## Public execution contract

There are three separate public channels:

| Channel | Events | Meaning |
| --- | --- | --- |
| Progress | `progress` | A brief public update about the task or an observed milestone. |
| Activity | `activity`, `work`, `run_state`, `control_ack` | Identified work items, measured progress, lifecycle changes, and control acknowledgments. |
| Answers and outputs | `result_block`, `final_start`, `text_delta`, `artifact`, `ui_start`, `ui_patch`, `ui_commit`, `message_end` | Explicit provisional answer sections, the final answer stream, and validated output versions. |

An activity item has `id`, `parent_id`, `seq`, `kind`, `phase`, `label`,
`purpose`, `status`, `started_at`, `updated_at`, `finished_at`, `attempt`, and
`outputs`. Optional fields are `summary`, `tool`, and measured `progress`.
Timestamps are Unix seconds. Kinds are `phase`, `tool`, `agent`, and `control`;
phases are `work`, `check`, and `final`. Statuses are `queued`, `running`,
`completed`, `failed`, `cancelled`, and `paused`.

Tool item identity includes the turn, step, and tool-call ID. Two concurrent
Python calls remain two items even when their labels match. Item `seq` is its
public revision; the durable SSE event cursor is a separate run-level sequence.
Clients deduplicate events and update items by identity. A later status for one
call cannot complete another call with the same label.

`work` carries a complete snapshot: `items`, `current_progress`, up to twelve `milestones`, aggregate
verification status, tool count, and run timing. The older `steps` projection
remains available for saved conversations and compatibility. It is not used to
infer identities for the new activity timeline.

Sources:

- [`activity.py`](../backend/app/agent/activity.py): public activity construction
  and nested allowlists.
- [`runs.py`](../backend/app/agent/runs.py): public event projection, journal,
  execution ownership, and subscriptions.
- [`protocol.ts`](../frontend/src/types/protocol.ts) and
  [`stream.ts`](../frontend/src/services/stream.ts): client contract and reducer.

## Progress, privacy, and evidence

The harness emits an immediate acknowledgment and factual tool lifecycle events.
`report_progress` provides an explicit mechanism for brief model-authored public
commentary. Ordinary intermediate model content is never automatically promoted
to public commentary. In quiet periods the interface shows the working state
and elapsed time without inventing activity or an overall percentage.

Only measured progress is displayed: for example pages read or retrieved source
counts. A progress bar requires a valid, known total. Failure and retry states
come from actual tool outcomes; a repeated tool name alone is not a retry.

The backend public projector admits only named fields. It does not forward raw
tool arguments, stdout, provider reasoning, verifier reports, private prompts,
or agent-to-agent communication. The frontend also selects public fields rather
than spreading arbitrary metadata into components. The private session journal
continues to support provider replay and diagnostics; the normal chat and
Activity panel do not fetch that journal.

Explicit output references connect activity to files, sources, and interactive
surfaces. A document reading can link to extracted text as evidence of what was
read. That link does not by itself publish the extraction as a final deliverable.
External links use HTTP(S); file links remain scoped to the conversation
workspace. Executable URLs and parent-directory paths are rejected by the UI.

### Useful results during work

`publish_result` deliberately publishes a completed section while remaining work
continues. It is an ordered presentation operation with no filesystem effects.
The event is `result_block` with `message_id` and a nested `block`: `id`, positive
integer `revision`, `kind` (`markdown` or `source`), `status`, `content`,
`evidence_refs`, and `created_at`. IDs belong to the message; revisions are assigned
by the backend. Content is limited to 50,000 characters per block, at most 32
blocks and 200,000 current content characters per run. Structured evidence
references must identify sources, files or completed activity observed in that run.
Reference validation does not independently verify every claim in a block.

Workers publish `provisional` results or `withdrawn` tombstones. Applied steering
and correction phases conservatively mark earlier provisional results
`superseded`. The consolidated final answer supersedes remaining provisional
sections on successful completion. `accepted` is supported by the wire format,
but the current worker tool cannot declare a result accepted or verified.

The frontend renders Markdown and math, preserves explicitly requested source,
deduplicates revisions and folds superseded results into an earlier-results
disclosure. `final_start` does not erase partial results. `message_end` replaces
them with the canonical manifest; stopped runs retain clearly provisional work.
Public replay recovery folds result revisions without rerunning tools. Strict
mode omits partial blocks from both live events and public saved history.

The active assistant is identified by its running status rather than its index
in the conversation, so an appended user steering message cannot prematurely
finish its streaming view.

## Execution, verification, and final streaming

The normal tool-heavy path is:

1. Execute the requested work, emitting progress and activity.
2. When verification is enabled, check relevant results and correct issues
   within the configured budget.
3. Enter an explicit final-response phase and stream actual provider text deltas.
4. Save the authoritative answer and output versions, emit `message_end`, then
   complete the run.

Simple eligible replies can stream directly. Other runs use a tool-free final
writing pass over the result bundle. That pass receives the request, checked
answer, public verification status, and outputs. It does not receive verifier
reports or private agent conversations. Its instructions prohibit new research,
computations, or unsupported claims, and it has no tools available. These are
behavioral constraints, not a mathematical guarantee that a model cannot alter
a fact while writing.

Verification permits at most two checks: one initial check and, if needed, one
confirmation after correction. An unchanged result is not checked repeatedly.
Inconclusive checks, exhausted budgets, and unfinished corrections have explicit
statuses and notices. The public timeline uses one checking phase while its
tools and outcome remain inspectable.

### Strict final wording

The product's **Check results** setting enables checks before final writing; it
does not claim that the exact final wording was independently verified. Strict
wording verification is currently an API option, not an additional UI setting.

API callers that require checking the exact answer wording can send
`verify: true` together with `strict_final: true`. The accepted answer is then
buffered until the checking path finishes; no extra rewriting pass follows it.
`final_start.mode` is `buffered` for that path and `streaming` for live provider
generation. Strict mode alone does not enable verification, override a disabled
check budget, or turn an inconclusive verdict into a passed check.

Progress remains live during strict-mode work. Buffered delivery is not
represented as new model generation: the frontend renders received text directly
and has no cosmetic, timed reveal after execution. This is the explicit tradeoff
between streaming a newly written answer and checking its exact wording before
release.

Implementation: [`harness.py`](../backend/app/agent/harness.py),
[`prompts.py`](../backend/app/agent/prompts.py), and
[`MarkdownRenderer.tsx`](../frontend/src/components/MarkdownRenderer.tsx).

## Conversation and Activity views

The conversation shows the latest meaningful update and active tools above the
answer. Completed actions stay in an expandable work record. On completion the
default view is a compact duration/action summary followed by the final answer.
Manually expanded details remain expanded across completion and canonical
history reconciliation.

The Activity panel groups entries by phase and parent agent, with expandable
purposes, outcomes, durations, retries, and output links. Filters select running
work, failures, or outputs. Motion is limited to real running indicators and
disclosure transitions; reduced-motion settings disable those animations.
Incoming deltas follow the bottom only while the reader is already near it.

Validated native UI surfaces may update during execution. Executable HTML
surfaces require a complete committed version. Versions replace earlier copies
by stable surface identity. Markdown tables and math render as answer content;
explicit source fences remain code, and HTML previews wait for completion.

Implementation: [`WorkActivity.tsx`](../frontend/src/components/WorkActivity.tsx),
[`MessageList.tsx`](../frontend/src/components/MessageList.tsx), and
[`presentation.ts`](../frontend/src/services/presentation.ts).

## Run ownership, reconnection, and controls

An HTTP stream observes a run owned by `RunManager`. Closing that stream,
navigating away, or losing the network closes only the subscription. Reconnection
reads the durable event prefix and tails the same run from its cursor. The
subscribe implementation coordinates the replay boundary with its live waiter
to avoid losing an event between the two.

`client_run_id` makes an accepted start request idempotent. Reusing it with a
different request is rejected. Reconnection does not reissue model requests or
tool calls. Heartbeats are SSE comments rather than fabricated work events.

| API | Purpose |
| --- | --- |
| `POST /api/chat/stream` | Start or reattach to an idempotent accepted request. |
| `GET /api/chat/{conversation_id}/run` | Find the active or most recent run. |
| `GET /api/runs/{run_id}` | Read lifecycle metadata and the last cursor. |
| `GET /api/runs/{run_id}/events?after=N` | Replay and subscribe; also accepts `Last-Event-ID`. |
| `POST /api/chat/{conversation_id}/pause` | Request a pause at a safe execution boundary. |
| `POST /api/chat/{conversation_id}/resume` | Release a requested or reached pause. |
| `POST /api/chat/{conversation_id}/steer` | Queue a user instruction for the running task. |
| `POST /api/chat/{conversation_id}/stop` | Explicitly stop execution. |

A queued control acknowledgment is distinct from the applied state. Pause waits
for an in-flight operation to reach a safe boundary; the UI does not claim that
the operation has already stopped. Final writing also observes pause boundaries.
Steering is acknowledged when queued and when applied; instructions arriving
after the final-writing boundary are not silently accepted as applied work.

On backend restart, orphaned active runs are repaired from their saved checkpoint
and marked `interrupted`, retaining completed work and settling unfinished item
statuses. If the complete final answer was already committed before the terminal
SSE event, recovery marks the run completed without regenerating it. Recovery
does not automatically restart the model or blindly repeat side effects.
An ordinary new turn continues from the repaired conversation checkpoint.
The recovery guard blocks an exact repeat of a side-effecting call whose outcome
is unknown. Built-in file edits need a complete read receipt for the affected
file; opaque operations need an explicit user retry instruction. Unrelated
reads and a generic “continue” do not authorize repeating an uncertain action.
Resuming an in-process paused run and restarting an interrupted task are
different operations; automatic cross-process execution continuation is not
implemented. Execution ownership is process-local with a SQLite journal and a
single Uvicorn worker, not a distributed or multiworker scheduler.

Completion uses `message_end` and a canonical conversation fetch to reconcile the
matching answer in place. It preserves the composer draft and active-chat guards;
there is no full-page reload. Stream closure alone is not proof of success.
The complete presented surface set across all turns also reconciles the Canvas
cache. A withdrawn preview closes only its own selection and pending work;
accepted surfaces keep their local view state and edited parameters, and earlier
results or open file selections remain available.

## Research rationale

These papers motivate the interaction choices; they do not establish the private
implementations of ChatGPT, Claude, or Grok.

| Primary source | Design applied here |
| --- | --- |
| [AgentGUI](https://arxiv.org/abs/2607.26300), 2026 preprint | Live observation, concurrent work identities, and steering of long-running agents. |
| [ResearStudio](https://aclanthology.org/2025.emnlp-demos.69/), EMNLP 2025 demonstrations | Connect actions and files to the ongoing task instead of leaving all useful output until the end. |
| [AGDebugger](https://www.microsoft.com/en-us/research/publication/interactive-debugging-and-steering-of-multi-agent-ai-systems/), CHI 2025 | Inspectable execution history, failures, and intervention points. |
| [Magentic-UI](https://arxiv.org/abs/2507.22358), 2025 preprint | User control and shared progress during agent execution. |
| [AgentLens](https://arxiv.org/abs/2402.08995), 2024 preprint | Hierarchical activity summaries and temporal structure. |
| [DuetUI](https://arxiv.org/abs/2509.13444), revised 2026 | Evolving task interfaces and direct user interaction with intermediate results. |

The [Codex App Server contract](https://learn.chatgpt.com/docs/app-server) informs
the distinction between commentary and final answers, stable work-item identity,
and authoritative completion. [Claude's streaming SDK](https://code.claude.com/docs/en/agent-sdk/streaming-output)
and [xAI's tool streaming documentation](https://docs.x.ai/developers/tools/streaming-and-sync)
provide public examples of incremental output and tool feedback. MatOpt adapts
these patterns to its own provider-independent harness and public privacy boundary.

## Verification and acceptance

Focused frontend coverage lives in `presentation.test.ts`,
`MessageList.test.tsx`, and `MarkdownRenderer.test.tsx`. It exercises distinct
parallel calls, stale-sequence replacement, private-field exclusion, measured
progress, interrupted statuses, phase/agent nesting, retry displays, output
links, manual expansion, scroll anchoring, direct final text, native previews,
HTML gating, tables, equations, and literal code. Transport coverage is in
`stream.test.ts` and `useChat.test.tsx`; run-manager coverage is in
`backend/tests/test_runs.py`.

Before declaring the complete feature validated, run the relevant suites and
the production build, then exercise an integrated PDF/OCR → search → parallel
computation → correction → verification → final-answer scenario. Include a
mid-tool disconnect, reload, pause/resume, steering, explicit Stop, and server
restart. Check that replay does not duplicate side effects, terminal history
matches the final rendered answer, and private canary strings do not appear in
public events, history, exports, or the DOM. Test the live and settled interface
in light/dark themes, at narrow widths, and with reduced motion.

This document describes the implementation and acceptance scope. A test name or
an acceptance item is not a claim that an unexecuted integration check passed.

## Earlier acceptance record — 2026-10-10

Executed against the earlier progress/final-stream implementation, before the
explicit partial-result and persistent workspace additions:

- `backend/.venv/bin/python -m pytest backend/tests -q`: **315 passed**.
- `cd frontend && npm test`: **261 passed**, no skipped tests.
- `cd frontend && npm run build`: succeeded; existing bundle-size warnings remain.
- Backend Docker image built, started with the existing data volume, and passed `/health`.
- `git diff --check`: passed. Local HEAD and remote `main` both resolved to
  `b73ca6e2a091b3e8f5aa045160c6f5df6d3cb108`; implementation changes remain uncommitted.

The deterministic integrated harness scenario in `test_agent_activity.py` uses
real scanned-PDF rasterization/OCR dispatch and native UI compilation, with a
controlled provider, search and computation. It covers concurrent tool completion,
failure/retry, correction and bounded verification, gated live final deltas,
exact-wording buffering, private canaries, late steering, and cancellation.
`test_recovery_guard.py` and `test_runs.py` exercise fresh harness/manager recovery,
uncertain side effects, fast-producer control admission, disconnection and replay.
`test_web_search.py` proves synchronous search cannot block event delivery or
cancellation. Frontend tests include partial Markdown/math/code, HTML completion,
withdrawn native previews, view-state preservation, scroll anchoring, and reload.

A separate live run used the configured provider and a synthetic scanned PDF
containing the values 2, 3, 5. It performed OCR, live search, Python computation,
and native table creation. The first public acknowledgment arrived in 0.032s;
the final answer streamed 284 actual deltas from 30.251s, before completion at
31.543s. Its 398 public events had unique cursors. Disconnecting during OCR and
reattaching did not repeat the action; terminal content exactly matched saved
history. Browser inspection confirmed rendered tables, equations, Python code,
the native table, a collapsed work record, and the Activity timeline. That run's
independent check was **inconclusive**, and both the answer and interface showed
the limitation. It is not evidence that all generated facts were independently
verified. The controlled correction/check tests cover those verdict transitions.

After a backend restart, the completed live run retained its completed status,
identical answer and cursor 398; replay ended with `message_end`, `run_state`,
and `done`. A second live control check reloaded during a 20-second Python call,
paused at its safe boundary, accepted steering, resumed, and returned the requested
wording. The journal contained exactly one tool execution and the state sequence
queued → running → paused → running → completed. Browser inspection confirmed
the unsent draft and explicitly expanded work record survived final reconciliation.

Interrupted work continues through a new user turn from a repaired checkpoint;
automatic cross-process execution continuation and multiworker scheduling are
outside this implementation. Exact final wording checks use the documented
`strict_final` API option; the product's **Check results** control selects result
verification before ordinary live final writing.

## Partial-result acceptance — 2026-10-10

The subsequent integrated backend suite passed **368 tests**. Publication review
then added fixes for findings omitted from final-writer input and private result
checkpoints missing from recovery; **89 focused tests** passed afterward. MCP
execution/recovery also passed **44 focused tests**. Frontend: **278 full-suite
tests**, then **19 focused tests** after consolidating the Stop control, followed
by a successful production build. Existing large-bundle warnings remain.

The rebuilt backend retained the existing data volume and passed `/health`.
`scripts/verify_live_agent.py` then created one synthetic raster-only PDF with
values 2, 3, 5 and explicitly requested partial publication between questions.
All ten acceptance assertions passed. The [recorded report](reference/agent-streaming-live-20261010.json)
includes the image identity, relevant source hashes, event counts and scope.

| Observation | Measured result |
| --- | --- |
| First useful public result | 5.969 seconds |
| More tool work after the partial result | Yes |
| First final-answer token | 17.665 seconds |
| Completion | 18.980 seconds |
| Actual final text deltas | 399 |
| Public event cursors | 480, all unique |
| Independent check | Correct, one round |
| Deliberate disconnect and replay | Passed |
| Terminal content versus canonical reload | Exact match |
| Raster PDF through multimodal reading | Passed |

The final solution contained sum 10, sum of squares 38, and mean 10/3. Both
partial blocks advanced from provisional revision 1 to superseded revision 2.
No private event types were exposed. Deterministic canary tests, rather than this
synthetic live task alone, establish the tested field-exclusion behavior.

An isolated Chromium browser inspected this actual saved result at 1440×960 and
390×844. Tables and mathematics rendered, earlier results were collapsed, the
mobile header/composer stayed within the viewport, and opening/closing Activity
retained an unsent draft. No page errors occurred. Screenshots and the browser
report are in `frontend/test/artifacts/publication-*`.

This is one explicitly guided integration run. It does not establish autonomous
publication frequency, broad model accuracy, a preferred loop policy, or general
latency improvements. The response still repeated its verification summary;
that presentation issue is separate from successful protocol delivery. Shared
budget, delegated cancellation and loop-policy experiments are a subsequent
change and are not covered by this image's acceptance record.

## Shared limits and interruption acceptance — 2026-10-10

After shared-budget, operation-policy and cancellation integration, the full
backend suite passed **528 tests in 70.63 seconds**. The two warnings are from
FastMCP's deprecated logging capability in local protocol tests. The run includes
actual local Jupyter and stateless HTTP MCP integration; model responses are
controlled unless a separate live record explicitly says otherwise.

One run allowance now covers native work, final writing, checks, OCR and
the delegated data agent. Defaults are 80 model admissions, 160 tool admissions and an
absolute 1,800-second deadline, including pauses. Each retry consumes an
admission. These are engineering defaults, not measured optimal limits.
Tests cover logging enabled/disabled, parallel sibling settlement, shared
OCR/worker caps, quiet-stream Stop/deadline cleanup, pause accounting and
exhaustion before final writing. Available partials survive an incomplete exit.

Independent review reproduced and fixed three additional edge cases:

- Stop, deadline or provider failure during final writing previously removed
  the already-public prefix during canonical reconciliation. Logged and
  unlogged runs now preserve exactly that prefix, without publishing private
  worker drafts or unreceived answer suffixes.
- A delegated response ending at its output limit previously looked complete.
  It now preserves late usage receipts and earlier completed tools/artifacts,
  reports an explicit incomplete result and suppresses unfinished tool calls.
- Cancelling local extraction's async wrapper previously detached its thread.
  The tool now retains ownership until the thread really exits. If the cleanup
  grace period expires first, the execution service rejects further tool calls
  in that workspace until settlement. This does not claim that Python threads
  can be forcibly terminated.

Bound data-agent output registration also applies the existing native visibility
policy, excluding internal runtime files and traces from deliverables. The
standalone data-agent path retains its existing visibility behavior.

Comparisons remain separately scoped:

- [Operation policy](LOOP-POLICY-EXPERIMENT.md): isolated controller traces and
  two paired actual-harness fake-provider fixtures. The selected default allows
  corrected calls that the legacy whole-tool block rejected.
- [Verification contract](VERIFICATION-CONTRACT-EXPERIMENT.md): 24 paired runs
  with injected errors. Strict mode avoids a subsequent rewriting stage;
  neither mode fixes an intentionally fallible checker.
- [Delegation design and evidence](DELEGATION-EXPERIMENT.md): independent file
  and answer oracles, equal run limits and separate genuine usage receipts.

Final UI checks found and fixed a zero-height document preview in short
viewports. **16 focused tests and the production build passed** after that fix.
Keyboard navigation, 320/390px layouts, reduced motion and two explicitly
labeled 200% viewport/device-scale emulations passed with no browser errors.
The [acceptance report](../frontend/test/artifacts/ACCEPTANCE-BOUNDARIES.md)
retains before/after geometry, screenshots and the native-zoom limitation.

## Selected build deployment and live acceptance — 2026-10-10

After the native-first prompt/skill-routing adjustment, **63 focused routing and
data-agent tests passed**. The backend image was rebuilt and applied locally only
after confirming no active runs. The existing data volume remained attached;
all 15 pre-existing conversations remained after restart. `/health` returned
healthy with the configured provider and 22 tools. No credentials or existing
user conversations were edited. All 119 backend application source hashes in
the running container matched the tested local sources.

The [new live record](reference/agent-streaming-selected-20261010.json) identifies
the deployed image and source hashes. A fresh synthetic raster-only PDF run
passed all ten acceptance assertions with this build:

| Observation | Result |
| --- | --- |
| First useful partial result | 7.008 seconds |
| Additional work after that result | Yes |
| First final-answer delta | 18.446 seconds |
| Completion | 20.469 seconds |
| Actual final deltas | 633 |
| Public event cursors | 722, all unique |
| Check outcome | Correct, one round |
| Disconnect/replay and canonical reload | Passed, exact terminal content |
| Raster-only PDF through multimodal reading | Passed |

The saved answer has sum 10, sum of squares 38 and mean 10/3. It still includes
more derivation detail than the concise test request needed; protocol delivery
does not guarantee ideal model wording. This is one explicitly guided
integration run, not a general accuracy or autonomous-publication benchmark.

Read-only isolated Chromium inspection of this new saved chat passed at
1440×900 and 390×844. Its semantic table and 20 KaTeX nodes rendered without
math errors; Work, Earlier results and all Activity details started collapsed.
Keyboard opening/closing of Activity retained the unsent draft and canonical
answer. The mobile table scrolls within its own container, including access to
the rightmost result column. There were no page/console errors or attempted API
writes. Screenshots and raw checks are retained in
`frontend/test/artifacts/selected-*`. These saved-chat checks supplement the
live API test and dedicated reconciliation component tests; they are not a
browser observation of every token as it originally arrived.

The [delegation experiment](DELEGATION-EXPERIMENT.md) separately records the
original pilot's tool-menu defect and a corrected eight-run comparison. Under
the same six-request allowance, native execution completed 4/4 bounded tasks
and the delegated data agent completed 1/4; both produced correct files in 4/4. This
supports native-first routing for small arithmetic/file work while retaining
the data agent for substantial specialized workflows or explicit requests. No extra
request allowance was granted to hide failed handoffs.
