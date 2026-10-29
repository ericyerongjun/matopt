# MatOpt agent experience and harness improvement plan

Research and repository review: 10 October 2026. Status: implementation and local experiments following the research plan. Streaming acceptance is recorded in [AGENT-STREAMING.md](AGENT-STREAMING.md). Public partial results, replay/final reconciliation, workspace refinement, shared MCP execution, progressive discovery, shared run limits and operation-specific failure handling are integrated. Deterministic comparisons and a small live delegation pilot support the scoped choices in section 10; they do not establish an end-to-end model-quality winner. The larger held-out evaluation and user study below remain proposed follow-up work.

## 1. Product decision

MatOpt should be a workspace for directing and reviewing agent work. The conversation carries instructions, useful updates, and answers; the workspace carries durable documents, files, and interactive results. Execution remains observable and interruptible throughout.

The central change is **useful partial results while work continues, followed by one authoritative final answer**. A running indicator alone does not meet this requirement. Neither does replaying a completed answer with a typewriter effect.

Preserve the user's choices: ChatGPT-like system typography, tools and skills through `/`, automatic search for time-sensitive questions, multimodal PDF/image understanding, rendered Markdown and math, explicit source-code fences, private reasoning and agent communication, collapsed completed activity, and bounded verification.

### Public interaction contract

| Stage | What the user sees | Source of truth |
| --- | --- | --- |
| Accepted | Immediate acknowledgment and available controls | Server accepts the run |
| Working | One concise update, a few active operations, elapsed time | Public progress and actual tool lifecycle |
| Partial result | Useful answer sections, extracted evidence, tables or plots as available | Explicitly published, validated result blocks |
| Checking | One checking phase with its scope and outcome | Recorded checks against a result revision |
| Final response | Actual provider text as the final answer is written | Final-response stream |
| Completed | One final answer, durable outputs, compact expandable work record | Committed canonical message and output manifest |

“Refresh” means reconciling the answer and result versions in place. Keep the page, draft, focus, reading position, and selected document intact. A dropped connection is not completion. A budget exit is not success.

### Clarified experience: one agent run, three public streams

Keep three independently typed channels in the same run: **updates** explaining completed or ongoing work, **activity** representing actual tool/child-task lifecycles, and **answer content** containing useful partial results followed by the final response. None is reconstructed from private reasoning. The existing SSE transport can carry all three; changing transport alone would not solve the product gap.

For an assignment upload, the intended sequence is: acknowledge the upload; show actual page-reading progress; publish the extracted question or a completed question's solution when useful; display calculations and source checks as compact activity; publish additional completed sections; perform bounded checks; stream the consolidated final solution; and reconcile it with the saved output. This is an illustrative scenario, not a claim about a completed acceptance run.

The finalization order is explicit:

1. Stop scheduling new work when requirements are satisfied or a terminal boundary is reached.
2. Settle or cancel outstanding child work and finalize check outcomes.
3. Stream the public final answer from actual provider deltas in normal mode.
4. Commit the final message and deliverable manifest durably.
5. Emit the authoritative completion event; reconcile the client in place and collapse completed activity, retaining manual expansion.

A failed commit is not successful completion. A stopped run keeps its useful provisional results and a clear stopped status. A late event from an older run or result revision must not overwrite the current answer.

## 2. Research and product references

These sources motivate candidates; their reported results do not establish performance on MatOpt or reveal unpublished commercial implementations.

### Product patterns verified from official sources

| Reference | Relevant published behavior | MatOpt adaptation |
| --- | --- | --- |
| [OpenAI Intelligent UI, 7 October 2026](https://openai.com/index/gpt-6-for-everyone/) | Native streamable components, progressive compilation, and partial answers interleaved with ongoing thinking | Separate partial answer blocks from activity; compile validated native components incrementally |
| [Codex App Server, current official OpenAI documentation](https://learn.chatgpt.com/docs/app-server) | Stable item lifecycles, commentary/final phases, deltas, steering, authoritative completed items | Preserve event identities and final reconciliation; select only our permitted public fields |
| [Cursor 3, 2 April 2026](https://cursor.com/changelog/3-0) | Agent-centered workspace, parallel sessions and deliberate side-by-side views | Persistent task identity, result navigation, optional workspace focus |
| [Claude artifacts, current documentation](https://support.claude.com/en/articles/17153992-what-are-artifacts-and-how-do-i-use-them) | Substantial reusable outputs alongside a conversation | Distinguish inline answers from reusable deliverables |
| [Grok Build Agent Dashboard](https://x.ai/news/agent-dashboard) | Sessions needing input receive priority; child agents roll up under their parent | Attention states and compact parallel-work summaries |
| [Gemini Canvas](https://gemini.google/overview/canvas/) | Create, inspect and refine documents and interactive outputs | Persistent results with direct controls where useful |
| [Claude frontend aesthetics cookbook, 21 October 2025](https://platform.claude.com/cookbook/coding-prompting-for-frontend-aesthetics) | Deliberate typography, color, motion and layout choices | Apply those dimensions to this workspace; the user's font preference takes precedence over generic font advice |

OpenAI explicitly describes model training for interleaved answers. MatOpt can implement the interaction and publication protocol with its configured provider; CSS or a harness prompt alone does not reproduce that training. Do not change the user's provider as part of this plan.

### Papers most relevant to this project

| Paper and publication status | Applicable idea | Evidence boundary |
| --- | --- | --- |
| [WaitGPT](https://arxiv.org/abs/2408.01703), UIST 2024 | Visualize operations during analysis and provide intervention points | Small, analysis-focused user studies; adapt its operation visibility, not raw private traces |
| [AGDebugger](https://dig.cmu.edu/publications/2025-agdebugger.html), CHI 2025 | Hierarchical execution inspection and steering | Developer debugging interface evaluated with 14 participants on two tasks; public MatOpt views should expose task outcomes, not agent messages |
| [ResearStudio](https://aclanthology.org/2025.emnlp-demos.69/), EMNLP 2025 System Demonstrations | Live plan, action/file updates, pause and resume | Architecture reference: its experiments exclude human intervention and do not quantify the interactive features' benefits |
| [When Should Users Check?](https://arxiv.org/abs/2510.05307), CHI 2026 | Balance interruption cost against error propagation | Controlled 48-person study; apply selective checkpoints, not confirmation for every routine tool or claims about automated verification accuracy |
| [DuetUI](https://arxiv.org/abs/2509.13444), revised February 2026 | User manipulation of task UI informs subsequent generation | Proof of concept with a 24-person study; generated controls need usefulness tests |
| [AgentGUI](https://arxiv.org/abs/2607.26300), July/August 2026 preprint | Observe and steer concurrent long-running sessions | Small eight-person study and preliminary local-model experiments; no guaranteed gain here |
| [SWE-agent](https://papers.nips.cc/paper_files/paper/2024/hash/5a7c947568c1b1328ccc5230172e1e7c-Abstract-Conference.html), NeurIPS 2024 | Treat tool interfaces as a first-class determinant of agent performance | Supports clearer tool contracts, outputs and errors; not a UI styling paper |
| [Large Language Models Cannot Self-Correct Reasoning Yet](https://arxiv.org/abs/2310.01798), ICLR 2024 | Evaluate corrections against external evidence instead of relying on repeated self-critique | Results concern the studied models/tasks; do not generalize them to every newer model |
| [τ-bench](https://arxiv.org/abs/2406.12045), June 2024 | Check final environment state and repeated-run reliability | Adapt its evaluation principles; MatOpt assignments are a different task distribution |
| [Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/abs/2503.13657), 2025 paper, v3 | Classify specification, coordination and verification failures | Use its taxonomy for failure analysis; adding agents is not itself a remedy |
| [AgentLoop](https://arxiv.org/abs/2609.33315), 27 September 2026 preprint | Track requirements, supporting evidence and diminishing progress before continuing or synthesizing | Lexical stability is imperfect; bundled ablation does not isolate each mechanism; closure is not correctness |
| [What Does a Harness Buy? Tokens, Mostly](https://arxiv.org/abs/2610.04433), 3 October 2026 preprint | Compare harnesses at fixed model settings and account for rerun variance and cost | Motivates repeated paired trials; a small local benchmark cannot prove broad superiority |

Additional engineering references: Anthropic's [advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use) (24 November 2025) motivates on-demand tool discovery and usage examples; [writing effective tools](https://www.anthropic.com/engineering/writing-tools-for-agents) (11 September 2025) motivates concise outputs and actionable errors; [Managed Agents architecture](https://www.anthropic.com/engineering/managed-agents) (8 April 2026) motivates separating session persistence, harness control and execution environments.

The [MCP Tools specification, 2025-11-25 revision](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) is the contract reference for structured content, pagination, catalog-change notifications and annotations. Negotiate the supported version rather than assuming every server supports every capability.

X posts were searched as discovery leads. A linked Cursor post could not be retrieved reliably; no X-only claim is used as implementation evidence. Official documentation and primary papers are the basis above.

## 3. Current implementation and concrete gaps

Keep the working foundations: durable server-owned runs, cursor replay and idempotent starts, safe public projection, real final deltas, bounded checks, cooperative controls, versioned native UI, committed HTML previews, and uncertain-side-effect recovery guards.

The implementation now includes explicit public result blocks, active-message streaming, persistent workspace views, shared MCP/native execution and exact tool discovery. The integrated backend suite passed **528 tests** after shared budgets, operation-specific failure handling and interruption fixes; **63 focused tests** then passed after the native-first routing adjustment. The frontend suite passed 278, followed by 19 focused tests after consolidating Stop and 16 after fixing the short-viewport file preview; production builds passed. Publication review also repaired two cases: final consolidation retains findings published only as partial blocks, and recovery folds a private result checkpoint even when the public journal has not received it. Final-writing interruption now retains its already-public prefix.

The selected deployed build passed all ten actual-provider scanned-PDF integration assertions: useful partial output at 7.008 seconds, additional work afterward, final deltas from 18.446 seconds, completion at 20.469 seconds, one correct check, deliberate disconnect/replay and exact canonical reload. The existing data volume and all 15 pre-existing conversations survived restart; all 119 backend application source hashes matched the tested local files. This synthetic task explicitly requested publication and is not a general model-quality benchmark or user study. See [the acceptance record](AGENT-STREAMING.md#selected-build-deployment-and-live-acceptance--2026-10-10).

The [tool discovery experiment](TOOL-DISCOVERY-EXPERIMENT.md) improved initial expected-tool recall from 90/83.3/83.3% to 100/96.7/96.7% on 30/300/3,000-tool synthetic catalogs. It increased schema bytes compared with the previous shortlist. This supports exact dispatch and the tested local retrieval choice, not an end-to-end quality or cost claim.

| Initial issue | Integrated response | Remaining evidence boundary |
| --- | --- | --- |
| Steering appended after the assistant disabled streaming | Track the active assistant/run identity | Covered by transport and presentation fixtures |
| Useful text appeared only at final publication | Explicit partial-result blocks and public milestones | One live scanned-PDF run explicitly requested publication; general spontaneous publication frequency is unmeasured |
| Typed MCP annotations failed; per-call state lost read receipts | Normalize typed/dict metadata and persist workspace-bound state | Real local stateless-HTTP read/edit, progress and media tests passed |
| MCP and native execution followed different paths | Shared validation, timeout, cancellation and receipt service | Remote service-specific behavior still depends on its implementation |
| Fuzzy tool names could silently select a handler | Exact dispatch, searchable metadata and explicit schema loading | Retrieval improved on synthetic catalogs; prompt bytes increased |
| Child work bypassed parent limits | One admission ledger and absolute deadline across native work, checks, OCR and the delegated data agent | Limits are engineering defaults; token usage can remain unknown |
| Repeated errors blocked the whole tool | Operation-specific failure identity with a legacy fallback | Two actual-harness fake-provider comparisons permit corrected work; no broad quality claim |
| Checking and final rewriting had different evidence | Check receipts and strict exact-wording mode; measure normal final-writer drift separately | A checker can still miss an error; normal rewriting remains a model operation |
| Results, Files and Activity displaced one another | Persistent results/files, activity inspector, resizable workspace | Desktop/mobile inspection preserves the draft and selected view; user study unrun |
| Mobile scrolling displaced the header/composer; short panels hid file contents | Corrected viewport containment and document-focused view at short heights | 320/390px and labeled 200% viewport/device-scale emulations passed; native browser zoom was not exercised |
| A live final repeated its checking notice | Final-writing prompt requests one brief notice, only where useful | Prompt guidance is not a deterministic duplicate-content guarantee |

There is currently no general native recursive agent tree to “fix.” The first budget integration target is existing delegated work, including the data agent. Any later general delegation API must inherit the same controls from inception.

The initial reviewer reproduced the typed-annotation exception, silent fuzzy-name dispatch and loss of read-before-write state. Subsequent focused regressions and the integrated suites above cover the fixes. Experimental reports distinguish controller simulation, actual-harness fake-provider tests, local Jupyter/MCP execution, and actual-provider acceptance.

## 4. Interface proposal

### Recommended layout: conversation with a persistent workspace

```text
Tasks             Task title / workspace                 Model · settings
Running           Conversation                  Results | Files
Needs input       Useful public update          Selected report or view
Recent            2 active actions + more       Persistent document tabs
                  Partial result sections       Direct result controls
                  Final answer
                  Composer / run controls       Activity inspector on demand
```

The task title becomes the primary header identity. The model/effort selector remains available. Keep answer prose near 65–75 characters per line; use a resizable workspace with keyboard support. At narrow widths, show one full-width view with a clear return control. Opening Activity must not discard an interactive result's state.

Alternative: a deliberate **Focus workspace** mode makes the artifact large and the conversation narrow. Compare it on document editing and data exploration; keep conversation-first as the proposed default. Avoid automatic layout switching during a run.

### Tokens and motion

| Role | Light | Dark |
| --- | --- | --- |
| Conversation | `#FCFCFC` | `#212121` |
| Navigation | `#F7F7F7` | `#171717` |
| Raised control | `#F0F0F0` | `#2F2F2F` |
| Primary text | `#171717` | `#ECECEC` |
| Secondary text | `#666666` | `#A6A6A6` |
| Divider | `#DEDEDE` | `#3D3D3D` |

Retain the existing system sans stack; 16px answer/composer, 14px controls, approximately 13px metadata. Keep KaTeX and source-code fonts appropriate to their content. Use semantic colors only for measured data, attention and errors. Proposed disclosure motion: 120–160ms; pane changes: 160–200ms; reduced motion disables nonessential animation.

Brief review: a decorative dashboard or dense agent graph would weaken this product. The distinctive element is the inspectable result workspace, with quiet surrounding chrome. The same calm typography serves conversation, mathematical output and task controls.

### Activity behavior

- Show a meaningful public update and at most three primary active rows; summarize additional parallel work as “4 more running.” Expansion retains each call's identity.
- Use task language: “Reading 4 of 12 pages,” “Comparing three sources,” “Running the validation calculation.” Counts come from actual tool progress.
- Indicate start, completion, failure and retry through small state transitions. Never animate successful completion before the actual outcome.
- During a quiet provider/tool interval, show its actual state and elapsed time. Do not invent new milestones or percentages to fill the wait.
- Keep completed activity collapsed by default, preserve manual expansion, and retain failures, evidence and outputs in the inspector.
- Put Pause/Resume, Stop and steering feedback together near the composer. Distinguish instruction queued, instruction applied, and next-turn input.

## 5. Partial results, final streaming and intelligent UI

Extend the existing protocol rather than replacing transport. Retain `run_state`, `progress`, `activity`, `work`, `final_start`, `text_delta`, `ui_*` and `message_end`.

Add a versioned public result block contract: `run_id`, `message_id`, `block_id`, `revision`, `kind`, `status`, `evidence_refs`, `content`, and optional `supersedes`. Proposed statuses: provisional, accepted, superseded, withdrawn. Kinds distinguish rendered answer Markdown, literal source, native surface references and deliverable references.

The worker deliberately publishes public results through a typed publication interface. Provider adapters may support incremental public parts; otherwise a publication tool releases a block only after complete arguments validate. Never expose arbitrary intermediate worker content, reasoning, partial tool arguments, raw stdout, verifier reports or agent messages. The fallback still provides useful results between tool rounds; it must not imitate token generation after a buffered block arrives.

Examples: extracted problem statement after OCR; a completed result table while other questions are still being solved; retrieved sources while comparison continues; a validated chart whose series are added as computations finish. Mark incomplete sections clearly. A steering change can supersede affected blocks without deleting unaffected evidence.

Native UI patches pass through the existing compiler and component allowlist. Preserve stable control identity and user changes. Only committed complete HTML executes in the isolated runtime. Interactive controls create explicit task/action events and do not silently gain tool authority. Ordinary explanation stays rendered Markdown; code/source requested by the user stays literal.

Stream rendering must handle incomplete Markdown, code fences, tables and math without repeatedly remounting the whole answer. Coalesce incoming deltas into short paint batches while preserving order and cancellation; do not deliberately slow down buffered text to imitate generation. Announce meaningful state changes to assistive technology, not every token. Auto-follow only while the reader is at the bottom; otherwise show a new-content control and preserve their reading position.

Publication frequency follows meaningful content boundaries, not a timer demanding filler. A provider that supports safe public content deltas can publish them directly through the allowlisted adapter. A tool-calling provider can publish completed blocks between work rounds. If a provider is silently reasoning and has no publishable result, show the truthful phase and elapsed time. A heartbeat reports connection health, not fabricated work.

At finalization, stop accepting further publication mutations, settle child work, record check outcomes, stream the final response and commit its output manifest. `message_end` identifies which blocks and surfaces remain authoritative. The client reconciles by identity/revision, folds superseded drafts into the work record and preserves accepted earlier deliverables. An interrupted run retains honest partial results and its interruption status.

Keep the existing strict mode: exact checked wording can remain buffered, with activity still live. Normal mode streams the final writing pass; checking source results does not guarantee every subsequently generated sentence. Do not use a blanket “verified” badge for that path.

## 6. Harness, tool and MCP plan

### Shared execution boundary

Extract a tool dispatcher used by both the chat harness and MCP service. It owns exact lookup, schema validation, permission/scope checks, scheduling constraints, timeout/cancellation, durable call receipts, output bounds and safe progress projection. Retain the existing recovery rule for unknown side effects; timeout is not proof that an external action failed.

Adapt that dispatcher to the existing SessionLog and RunManager durability mechanisms rather than introducing a second independent receipt store. Public replay stores only allowlisted events; private execution details remain behind their existing access boundary. Use the same public projection for live SSE, canonical history and exports so reconnecting cannot reveal data that was hidden live.

Bind MCP calls to an explicit workspace/session identity supported by the negotiated transport. Do not rely on a request-local ContextVar to identify subsequent independent HTTP requests. Preserve per-workspace observation state while preventing cross-client/workspace leakage. Advertise only implemented capabilities.

Normalize annotations and schemas, preserve structured content/resources instead of flattening every result to text, and correctly represent tool errors. Add progress and cancellation plumbing, server health, catalog refresh and isolation of server failures. Remote annotations inform discovery; configured trust and local policy decide execution permissions.

### Tool discovery and context

Keep a small reliable core tool set available. Compare current lexical routing with searchable metadata plus on-demand schemas. Explicit `/tool` selections stay pinned. Keep search available when fresh information is required and respect explicit user opt-outs. Unknown names return actionable candidates; execution always needs a resolved exact identity.

Re-evaluate the catalog after new evidence or steering, not only the opening query. Avoid serializing the entire MCP catalog into every prompt. Bound output size while retaining evidence references, useful error types and a path to inspect omitted material.

Use a small-catalog fast path. For larger catalogs compare metadata/BM25 discovery before adding semantic reranking. Discovery experiments should include 30, 300 and 3,000 tools, confusing names, paraphrases and tasks whose needed tools change mid-run.

### Bounded work and termination

The integrated run-scoped budget is inherited by the worker, final writer, checker, OCR calls and the delegated data agent: defaults are 80 model-attempt admissions, 160 tool admissions and an absolute 1,800-second deadline. Each retry is a new admission. Paused time counts. Local limits can be stricter. Title/follow-up UI side tasks are outside this execution allowance. Missing provider token receipts remain unknown; token estimates do not masquerade as exact usage.

On exhaustion, save available partial results and a deterministic incomplete-status notice. Do not exceed the limit to ask a model for another handoff. The budget primitive supports reserving a final slot, but the current harness does not enable it: a last model call is not necessary to preserve results honestly. Existing observed-spend checks remain separate, depend on configured prices/actual receipts and are not hard reservation accounting.

The controller considers requested deliverables, evidence, pending work and constraints. Completion requires the relevant results to exist, active child work to settle and outputs to commit. Missing evidence or an exhausted budget produces a partial outcome with a reason. Empty model tool calls alone are insufficient evidence of task completion.

The [operation-policy experiment](LOOP-POLICY-EXPERIMENT.md) supports the selected `operation` default over the old whole-tool failure block, with `legacy` available for comparison or rollback. Repeated successful reads remain advisory; they do not terminate the run. Requirement/evidence tracking inspired by AgentLoop remains a later candidate, not an implemented learned controller. Treat stable output as a prompt to inspect progress, never proof of correctness. Legitimate polling and iterative optimization need tool-specific progress signals.

For general delegation experiments, begin with a shallow tree (candidate depth two) and small fan-out (candidate three), explicit task contracts, child budgets, cancellation propagation and output ownership. These are calibration settings, not proven optima. Keep a single worker when a task is sequential or coordination would cost more than it saves.

### Verification

Prefer deterministic checks for calculations, schema validation, file existence and output integrity. Use model checks for appropriate semantic questions. Record what was checked, against which result revision, and with what outcome. The two-check/one-correction ceiling is per verification episode; steering can start another episode. A total-run counter records checking rounds entered, including a round stopped before provider admission; actual model attempts are counted separately by the shared budget. Never repeat an unchanged check or present an inconclusive result as passed. A user task change invalidates affected checks and still consumes the same run budget.

The normal final writer currently has a preservation prompt, not a deterministic factual-preservation guarantee. Measure changed values, units, citations and omitted limitations using explicit fixture oracles. Strict mode must consolidate published findings privately before checking, then deliver the exact checked candidate without an additional rewriting pass. Keep final-writer drift separate from the loop-policy comparison.

The [verification contract experiment](VERIFICATION-CONTRACT-EXPERIMENT.md) exercised 24 actual-harness runs with scripted providers. Four injected post-check writer changes reached normal output; strict mode avoided that writing stage. Both modes retained injected checker mistakes. Keep normal streaming for the requested live experience and retain strict mode when exact checked wording is required. This is a contract choice, not evidence that either mode has higher general model accuracy.

## 7. Experiments and selection

First freeze a reproducible baseline without resetting the dirty checkout: record source hashes, settings, dependency versions and fixtures. Use the same configured model, effort, tool environment and limits across variants. Do not expose `.env` values in reports.

| Experiment | Baseline / candidates | Main decision metrics |
| --- | --- | --- |
| Delivery | Current progress + final / meaningful milestones + partial blocks | First useful result, final correctness, unsupported partial claims, stream latency, duplicate content |
| Workspace | Current pane / persistent workspace / optional focus mode | Find-result and steer-task completion, reading continuity, visual preference, keyboard access |
| Tool catalog | Current routing / metadata discovery + lazy schemas | Tool recall, wrong-tool calls, prompt tokens, task success and latency |
| Loop policy | Existing caps / deterministic no-progress guard / evidence-aware controller | Premature stops, redundant calls, success, tokens, duration, recovery quality |
| Verification | Existing checker / task-specific deterministic checks + selective model checks | Missed errors, false failures, correction success and check cost |
| Delegation | Single worker / bounded independent subtasks | Matched-quality latency, total tokens, merge conflicts and child-budget compliance |

Use deterministic transport/chaos fixtures first. Then run a small pilot of 12 representative tasks with three repeats per candidate; retain promising variants and evaluate a separate 24-task holdout with three repeats. Report task-level paired outcomes and rerun disagreement. These numbers are a practical local selection set, not enough to prove small general accuracy gains.

Task families: simple answer; multi-question assignment; clean/scanned PDF and screenshot; live-fact research; multi-source comparison; file edit; chart/parameter exploration; multiple MCP servers; transient failure; repeated no-progress call; delegated analysis; disconnect/restart. Run variants sequentially against shared writable state or use independent workspaces.

Use expected numerical/file outcomes and deterministic assertions where possible. Label model-judged scores separately and inspect disagreements. For live web tasks record dates and retrieved evidence; use fixed replay fixtures for reproducible comparisons. Compare separately on read-only live tasks to check environmental drift.

Record repeated-run reliability as well as average success, with the number of tasks and repeats stated. Measure unsupported partial claims and any fact changed by final rewriting; do not reward a polished answer that disagrees with its computed evidence.

Selection order: all correctness/privacy/recovery gates; then task success and premature-stop failures; then latency/cost; then usability. Prefer the simplest non-dominated candidate. Reject a faster controller that silently drops requested work. Keep inconclusive candidates experimental and retain the baseline as a fallback.

A proposed 6–8-person formative UI comparison can identify usability problems, but must be reported as unrun until people actually participate. Automated browser checks and our own inspection are not a substitute for that evidence.

## 8. Delivery order and ownership

1. **Baseline and correctness fixes.** Active-message identity, typed MCP annotations, persistent MCP observation state; write focused regressions for the reproduced behaviors.
2. **Execution contract.** Shared dispatcher, budget propagation, workspace identity and safe progress. Keep native chat/MCP parity tests.
3. **Public result protocol.** Versioned blocks, publication boundary, strict mode, final manifest and replay migration. Backend and frontend agents share fixtures before integrating.
4. **Experience refinement.** Compact live activity, public milestones, coherent run controls, persistent results/files and narrow-screen behavior. Validate both layout alternatives before selecting the default.
5. **Controlled experiments.** Tool discovery, termination, verification and shallow delegation behind explicit configuration. Record baseline/variant results and select measured winners.
6. **Integration and acceptance.** Integrate the selected behavior, run relevant frontend/backend suites and build, inspect live runs, update protocol/operating docs and record limitations.

Parallel work lanes: frontend interaction/presentation; backend execution/MCP; experiment fixtures/research validation. One integrator owns event schemas, migrations and final acceptance. Avoid concurrent edits to the large harness without agreed file ownership.

## 9. Release gates

- Useful public progress appears while a long operation is still running. Partial answers appear before finalization in normal mode, and remain buffered in strict mode.
- Private canary strings never appear in public SSE, canonical history, exports or DOM. Uploaded document instructions remain source material, not authority.
- Replayed or out-of-order revisions cannot duplicate or roll back a newer result. Accepted steering leaves the active assistant streaming even when a later user message exists.
- A fast parallel tool completes visibly while a slow sibling continues. Tool animations and counts correspond to actual events.
- MCP read→edit works for an observed file; unrelated clients cannot reuse its receipt. Native and MCP entry points enforce the same tool contract.
- Shared budgets stop nested work; cancellation propagates; no external write is blindly retried after an unknown outcome. Polling/optimization tests do not trigger false success.
- Final committed output equals canonical reload output. Finalization during disconnect/restart produces one answer and one version of each deliverable.
- The composer draft, focus, scroll anchor, manual activity expansion and valid interactive control state survive final reconciliation.
- No whole-page overflow at 320px width or 200% zoom; keyboard access to `/`, run controls, tabs and splitters; reduced-motion support; meaningful live-region announcements without per-token noise.
- Report acknowledgment latency, first useful update/result, first final token, total completion, tool latency, tokens/cost and failure counts separately. A quick acknowledgment must not disguise a long wait for useful output.

The first end-to-end milestone is a single representative assignment run: PDF or screenshot input, actual OCR progress, a useful answer section while another operation remains active, one bounded checking phase, real final deltas, and an identical answer after reload. Repeat it with steering, stop, a slow parallel tool and a dropped connection. Expand layout and orchestration experiments only after this vertical slice passes.

Deliverables: refined UI; public result/event contract; shared MCP/native dispatcher; bounded harness policies; regression fixtures; a reproducible comparison report; and documented winning configurations with fallback behavior.

## 10. Selected integration and follow-up boundaries

The release selects conservative, inspectable behavior from the experiments actually run. It does not claim to reproduce another platform's private model training or to have evaluated every proposed alternative.

| Area | Selected behavior | Evidence and tradeoff |
| --- | --- | --- |
| Live output | Explicit useful partial blocks, actual final deltas, canonical reconciliation | Live scanned-PDF acceptance plus replay/interruption fixtures; publication was explicitly requested in the live fixture |
| Workspace | Persistent conversation, results/files, optional Activity inspector | Desktop/mobile and keyboard checks; short viewports focus the selected file; no human preference ranking |
| Tool selection | Exact dispatch, small-catalog availability, searchable metadata and explicit schema loading | Better expected-tool recall on synthetic catalogs, with larger prompts than the old shortlist |
| Repeated failures | `LOOP_POLICY=operation`, retaining `legacy` fallback | Corrected operations survive failures that previously banned their whole tool; opaque output is never treated as proof of progress |
| Checking | Bounded result checks, ordinary final streaming; strict exact-wording API option | Scripted drift cases expose the normal writer's remaining risk; strict mode avoids that writer but does not fix checker mistakes |
| Delegation | Native tools first for bounded arithmetic/files; the data agent for substantial specialized workflows or explicit requests | Corrected eight-run comparison: native completed 4/4, delegated 1/4 under the same six-request allowance; both created correct files in 4/4 |
| Limits | One shared admission ledger and absolute deadline across native work, checks, OCR and the data agent | Cancellation, unknown usage, nested limits and incomplete exits tested; defaults are not empirically optimal |

The [live delegation report](DELEGATION-EXPERIMENT.md) retains all attempted trials. In the initial pilot, direct execution used 16 requests and 44,066 reported tokens across four runs; delegated execution used 24 requests and 97,810 reported tokens. Median elapsed time was 13.584 versus 19.241 seconds. Parent accounting matched independently observed provider receipts in all eight runs, and source hashes remained unchanged during the experiment. The delegated registry omitted tools still advertised by the generic prompt, causing avoidable failed calls. This is evidence of a benchmark setup defect, not a clean estimate of delegation overhead. Prices were not configured, so no dollar-cost claim is made.

A separate corrected pilot used accurate registered-tool menus and retained the same fixtures, repetitions, six-attempt cap, tool/time limits, model settings and independent oracles. Direct execution completed 4/4 runs using 16 requests and 24,486 reported tokens; delegation completed 1/4 using 24 requests and 134,401 reported tokens. Both created correct files in all four runs. Median times were 13.678 and 21.509 seconds. All provider receipts reconciled and source hashes stayed unchanged; no unknown-tool failures remained. Three delegated runs exhausted the shared model allowance before final dispatch. These results support native-first execution for the tested bounded tasks. They do not estimate complex-report quality, and the two concrete paths still have different tool schemas and child prompts.

General native recursive delegation, a learned evidence/termination controller, semantic tool reranking, a separate focus-layout comparison, the 12-task pilot/24-task holdout, and the formative user study remain follow-up candidates. They are not prerequisites for shipping the tested interaction and execution contracts, and none is reported as implemented or experimentally superior. Future promotion requires its own matched task evidence; existing delegated work already inherits the shared execution limits.
