# Live agent experience: research and refinement plan

10 October 2026. Status: proposed, following the user's request to research and plan first. This document specifies the next refinement; it is not an implementation or acceptance report. Existing work remains preserved. The earlier implementation record is [AGENT-STREAMING.md](AGENT-STREAMING.md), the broader architecture is [AGENT-EXPERIENCE-PLAN.md](AGENT-EXPERIENCE-PLAN.md), and visual tokens/iteration history are in [DESIGN.md](../DESIGN.md).

## 1. Product contract

MatOpt is a workspace for directing agent work. A task should produce visible, useful progress throughout execution, then settle into one authoritative final answer with durable outputs.

There are three distinct public streams:

1. **Updates:** brief task-facing descriptions of observed progress, supported findings, and the next meaningful action.
2. **Activity:** actual operation lifecycles, measured progress, outcomes, and public parent/child relationships.
3. **Results:** useful provisional answer sections and interactive outputs, followed by the final answer.

Private reasoning, worker conversations, verifier discussions, prompts, and raw diagnostic logs do not become public streams. Public updates are deliberately authored for the user or derived from allowed operation facts. A disclosure opens public action details, not a private transcript.

The final refresh is an in-place reconciliation with saved server state. Keep the conversation, draft, focus, reading position, selected file, and valid interactive state. Do not reload the browser window. Failed, stopped, or interrupted work retains its useful partial results with an accurate status.

## 2. What the research supports

The following are primary research sources. Adaptations below are design proposals for MatOpt, not claims that the papers establish gains on our platform or explain unpublished commercial implementations.

| Priority | Paper | Relevant evidence | Proposed adaptation and boundary |
| --- | --- | --- | --- |
| 1 | [WaitGPT: Monitoring and Steering Conversational LLM Agent in Data Analysis with On-the-Fly Code Visualization](https://arxiv.org/abs/2408.01703), UIST 2024 | Visualizes evolving analysis operations and intermediate data. Formative study n=8; evaluation n=12. | Show task-meaningful operations and their results. Its small, data-analysis-focused evaluation does not establish a universal benefit from animation. |
| 2 | [Magentic-UI: Towards Human-in-the-loop Agentic Systems](https://www.microsoft.com/en-us/research/publication/magentic-ui-report/), Microsoft technical report/arXiv, July 2025 | Combines planning, ongoing intervention, concurrent tasks, and result review. A qualitative study involved 12 participants. Long histories and frequent interruptions were burdensome. | Compact summaries, visible evidence, and usable pause/steer controls. Do not copy verbose internal histories or add confirmation to routine actions. Downstream productivity gains were not established. |
| 3 | [Cocoa: Co-Planning and Co-Execution with AI Agents](https://kevinpjk.github.io/assets/pdf/cocoa_chi_2026.pdf), CHI 2026 | Interleaves planning and execution through shared task representations; 16-person lab study and seven-person week-long field deployment. | Allow changes to the public task direction after partial results without restarting the conversation. Researcher-focused studies and a more limited evaluated agent constrain transfer to our code/OCR workflows. |
| 4 | [DuetUI: A Bidirectional Context Loop for Human-Agent Co-Generation of Task-Oriented Interfaces](https://arxiv.org/abs/2509.13444), CHI 2026 ([author confirmation](https://arklab-hkustgz.com/projects_collection/duet-ui/)) | A generated interface changes with task context; direct interaction informs subsequent generation. Evaluation n=24. | Stable tables, charts, source lists, and parameter controls with explicit feedback events. External services were simulated; this is not proof of production reliability. |
| 5 | [When Should Users Check? Modeling Confirmation Frequency in Multi-Step Agentic AI Tasks](https://doi.org/10.1145/3772318.3790655), CHI 2026 | Studies intermediate versus final confirmation in a controlled 48-person experiment. | Checkpoints should reduce consequential rework, such as resolving ambiguous extracted data. The study concerns human confirmation under simulated task conditions, not automated verifier accuracy or a reason to require approval after every tool. |
| 6 | [Interactive Debugging and Steering of Multi-Agent AI Systems (AGDebugger)](https://arxiv.org/abs/2503.02068), CHI 2025 | Hierarchical inspection and intervention; evaluation n=14. Highlights difficulty knowing whether a change affected execution. | Show instruction received/applied status and affected public work. Its developer-facing agent-message browser is unsuitable for our public interface. |
| 7 | [ResearStudio: A Human-intervenable Framework for Building Controllable Deep Research Agents](https://aclanthology.org/2025.emnlp-demos.69/), EMNLP 2025 System Demonstrations | Persistent workspace, evolving plans, streamed actions/files, pause and resume. | Link activity to persistent outputs and preserve context across intervention. The paper does not quantify benefits from its human-intervention features. |
| 8 | [SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering](https://papers.nips.cc/paper_files/paper/2024/hash/5a7c947568c1b1328ccc5230172e1e7c-Abstract-Conference.html), NeurIPS 2024 | Studies how the interfaces supplied to an agent affect its software-engineering behavior. | Refine tool schemas, useful observations, error recovery, and execution feedback alongside the human UI. It does not evaluate our public chat design. |

[Agentless](https://arxiv.org/abs/2407.01489) is a useful counterweight for harness design: its bounded localization/repair/validation approach motivates comparing simpler controllers before adding more agents. This is a software-engineering benchmark result, not a recommendation to remove MatOpt's agent capabilities.

## 3. Official product and protocol references

- [OpenAI Intelligent UI announcement, 7 October 2026](https://openai.com/index/gpt-6-for-everyone/): describes native streamable components, progressive compilation, and partial answers interleaved with continued work. It also describes model training. We can build an analogous publication and rendering experience with the configured provider; a frontend patch cannot reproduce proprietary training.
- [Codex App Server](https://learn.chatgpt.com/docs/app-server): supplies concrete item identities, start/completion events, text deltas, commentary/final phases, and mid-turn steering. Use these lifecycle principles with our existing protocol; project only MatOpt's permitted public fields.
- [Claude Agent SDK streaming](https://code.claude.com/docs/en/agent-sdk/streaming-output): distinguishes stream events and text deltas from completed messages. This is an implementation reference, not proof of Claude web's private implementation. Tool-input fragments are not suitable public answer text.
- [Grok Build Agent Dashboard](https://x.ai/news/agent-dashboard): prioritizes sessions needing attention and groups subagents under their parent. This is a CLI/TUI reference; do not describe it as a verified Grok web layout.
- [Claude frontend aesthetics cookbook](https://platform.claude.com/cookbook/coding-prompting-for-frontend-aesthetics): informs deliberate typography, spacing, hierarchy, and motion. Preserve the user's system-sans/ChatGPT-like font preference.

## 4. Current foundation and refinement boundary

Static inspection confirms that this repository already contains:

| Existing capability | Main sources | Refinement to assess |
| --- | --- | --- |
| Public updates and allowlisted activity | `backend/app/agent/activity.py`, `presentation.py`, `tools/progress_tool.py` | Meaningful, non-repetitive progress in ordinary unprompted tasks; compact rendering of parallel work. |
| Versioned provisional results | `backend/app/agent/results.py`, `tools/result_tool.py`, `frontend/src/services/stream.ts` | Publication frequency and usefulness. Current publication sends a complete validated block, not token deltas from an unfinished publication call. |
| Actual final-answer streaming | `backend/app/agent/harness.py`, `frontend/src/services/stream.ts` | Distinguish a live final writer from buffered exact-wording mode; preserve facts through final consolidation. |
| Durable events and run controls | `backend/app/agent/runs.py`, `frontend/src/services/runs.ts`, `hooks/useChat.ts` | Verify reconnect, terminal ordering, queued/applied control clarity, and view preservation with the refined presentation. |
| Bounded checking and shared execution limits | `backend/app/agent/harness.py`, `budget.py`, `loop_policy.py` | Clear check scope/outcome, evidence-sensitive termination, and an evaluated allowance for finalization. |
| Activity, partial results, native UI, Markdown/math | `WorkActivity.tsx`, `MessageList.tsx`, `MarkdownRenderer.tsx`, `features/intelligent-ui/` | Visual hierarchy, accessible animation, paint cost, and stable interaction during updates. |

In `useChat.ts`, each event currently publishes updated React state. Profile realistic bursts before introducing a bounded paint scheduler; this is a performance question, not a demonstrated defect. The reducer correctly excludes ordinary text deltas before the final-answer boundary. Do not weaken that privacy boundary to obtain earlier text.

Earlier live acceptance explicitly requested partial publication. It proves the path can work, but does not establish that normal prompts reliably produce useful early results. Existing test counts and latency records are historical evidence; this planning pass does not recertify them or the latest visual edits.

### Concrete findings from this read-only audit

- **Activity subtree disclosure:** `WorkActivity.tsx:61–99` places descendants outside the parent's disclosure and filters before constructing hierarchy. Collapsing a parent does not collapse its child rows; a failures filter can remove the parent context. Implement actual subtree disclosure and retain contextual ancestor rows.
- **Delegated work visibility:** `tools/lambda_tool.py:290–310` sends child text/status to a private progress path. Keep that text private, but publish allowed child phase, measured tool count, and output summaries from structured lifecycle events. Current LAMBDA execution is sequential delegation; do not draw an invented parallel agent tree.
- **Accepted-control durability:** `runs.py:327–338,436–479` holds pre-claim controls in memory while acknowledging them publicly. A process crash in that interval can lose the accepted instruction. Reproduce with a deterministic crash test, then persist queued content/status privately before acknowledgment and recover it as unapplied. This is a static finding, not a newly executed crash reproduction.
- **Task completion:** `harness.py:1385–1394` ends the worker loop on a response without tool calls. A lightweight deliverable-coverage gate is worth evaluating for substantial tasks; it must not force unnecessary work for simple answers.
- **Verification versus wording:** `harness.py:895–939` checks findings before normal final writing. The existing controlled fault-injection test in `test_verification_contract.py:40` demonstrates that a writer can change checked values. Preserve the strict/streaming distinction, and check final structured facts and references without implying that a checker is infallible. The checker uses the configured model in a separate context, not an independent model.
- **Reading-position acceptance:** existing tests retain `scrollTop`, which does not necessarily retain the same visible passage when prior blocks are collapsed. Add browser checks for the visible text anchor and selection across supersession; define whether a result belongs inline, in the workspace, or both through explicit publication metadata rather than text-similarity deletion.

The current shell opens public Activity and numeric usage. Legacy trajectory inspector source exists, but no current shell rendering path was found. Preserve that separation.

## 5. Intended experience

Illustrative assignment workflow; labels and counts below appear only when backed by events:

| Moment | Conversation | Activity/workspace |
| --- | --- | --- |
| Request accepted | Brief acknowledgment and available controls | Actual run starts. |
| Reading upload | “Reading the assignment” and measured page progress when available | PDF/image reading operation; source file remains accessible. |
| First useful result | A completed question's solution or extracted table appears while other work continues | Result is marked provisional and linked to observed evidence. |
| Parallel work | Latest meaningful update plus a few active operations | Public subtasks group related work; each operation retains its own identity. |
| Checking | One “Checking results” phase with concise scope | Relevant tests/calculations and their actual outcomes; no repeated verification chatter. |
| Final writing | Consolidated answer streams as it is generated | Earlier results remain readable while the final answer arrives. |
| Completed | One saved final answer and durable outputs | Completed work collapses to a compact summary; explicit user expansion stays open. |

```text
User request

Working · elapsed time                              Pause   Stop
Latest useful public update
  Active operation 1
  Active operation 2                          View activity

Available result
Rendered explanation / equation / table / interactive output

[Ask a follow-up or steer the current task…                  ]
```

After completion, the work region becomes a small expandable summary above the final answer. On wide screens, Activity and results have deliberate docked space. On mobile, a focused panel provides a clear return to the conversation. The composer remains available where the layout permits and retains its draft in all cases.

Use the existing semantic tokens, system font, readable answer measure, and light/dark palettes from DESIGN.md. Keep motion functional: 120–180 ms disclosure/status transitions, a restrained indicator only for real active work, and reduced-motion support. Unknown duration has no invented percentage. A late network event must not briefly resurrect a finished spinner.

## 6. Implementation sequence

### Phase 0 — establish the behavioral baseline

Capture normal prompts for a scanned assignment, an image, a multi-source research question, a computation with a chart, a long tool call, and a delegated task. Add deterministic fixtures for pause, failure, cancellation, reconnect, and finalization races. Record the first useful result separately from acknowledgment and final-token latency.

Classify each gap as publication, event delivery, rendering, execution, or consolidation. Agree the event fixtures before parallel backend/frontend changes. Retain existing conversations and provider configuration. Resolve pending visual CSS integration as part of the frontend lane; do not treat partial visual edits as a completed redesign.

### Phase 1 — make useful interim output dependable

Keep `report_progress` and `publish_result` as explicit public boundaries. Guide publication at meaningful milestones: a source-backed finding, a finished question, a computed table, or a usable artifact. Avoid filler updates and repeated narration of the same tool call.

Use real tool lifecycle/progress facts immediately. Evaluate ordinary prompts without asking the user to request progress or invoke a special tool. If the model is still privately reasoning with no publishable finding, show the truthful phase and elapsed time.

Retain complete validated result blocks as the reliable provider-independent path. Add incremental public answer blocks only behind a provider capability check and a typed protocol: stable block identity, revision, start/delta/commit boundaries, and recovery semantics. Do not expose partial tool JSON or infer public prose from private output. Do not use a cosmetic typewriter after buffered results arrive.

### Phase 2 — refine the live work presentation

Show one latest meaningful update, up to three active operations, and a count/disclosure for additional work. Completed actions collapse; failures and input-needed states remain discoverable. Group public operations by task and phase, with purpose, duration, outcome, and output references available on expansion.

Avoid duplicating every started/completed operation in both the commentary history and action list. Task names should describe outcomes (“Analyze the uploaded table”) rather than internal implementation details. Child agents expose public task status and deliverables; worker-to-worker text stays private.

Fix the audited subtree/filter behavior before expanding the hierarchy UI. Keep failed children visible with their parent context, and preserve deliberate user expansion when filters or run status change.

Profile streaming Markdown/math and large event bursts. If needed, coalesce rendering to bounded animation-frame batches while preserving every event in reducer state; flush control and terminal events promptly. Preserve scroll when the user reads earlier content and provide a new-content affordance. Announce meaningful status changes to assistive technology, not each token.

### Phase 3 — refine harness decisions and control

Preserve the existing shared dispatcher, scheduler, budgets, cancellation, and recovery. Add only gaps demonstrated in Phase 0. Maintain a compact internal record of requested deliverables, available evidence, pending work, and unresolved constraints so completion is based on task coverage.

Improve user-facing summaries at actual execution boundaries. Show instruction received and applied separately. Pause is requested until a safe boundary is reached; connection loss does not mean stopped execution. Continue independent work while a nonblocking question awaits input.

Reproduce and close the pre-claim control durability gap before relying on an acknowledgment as proof of saved intent. Extend the existing private journal/control identities rather than adding another independent store. Recovery must never silently execute an already completed external action.

Use delegation for independent work with a clear deliverable and shared allowance. Compare it against direct execution on representative tasks; more subagents are not a quality target. Repeated attempts must respond to a changed condition or new evidence, and uncertain external writes must not be blindly replayed.

The budget primitive already supports reserving a final model slot, while current harness construction does not enable it. Evaluate a small finalization reserve against the baseline. It must reduce abrupt endings without disguising unfinished tasks or violating time/cost limits; deterministic incomplete output remains the fallback.

This requires an explicit transition from work-budget exhaustion to truthful consolidation. Merely enabling the reservation flag is insufficient because current exhaustion takes the error path.

### Phase 4 — regulate checking and final consolidation

Retain bounded checking: one relevant check and, after a substantive correction, at most one confirmation. Attach outcomes to the checked result revision and distinguish passed, failed, inconclusive, skipped, and budget-limited. Present one coherent checking phase. A user checkpoint is distinct from an automated verification pass.

For numerical/table/file results, retain the computed data as authoritative structured outputs. A final writer should reference those values rather than silently recreate them. Test that consolidation preserves critical numbers, citations, outputs, and unresolved caveats. Avoid blanket “verified” claims when only source results were checked.

Finalization order:

1. Stop scheduling new work and settle/cancel remaining child operations.
2. Freeze the result bundle and record check outcomes for its revision.
3. Stream the final answer in normal mode; keep exact-wording mode explicitly buffered.
4. Durably commit the canonical message and output manifest.
5. Emit completion and reconcile the client by stable IDs/revisions.
6. Collapse default work/history views, preserve explicit expansion and valid UI state.

Do not clear partial results at final-writing start. A failed final write or failed commit must leave recoverable results and an accurate status. Reconnection or late events cannot duplicate the answer or replace a newer result with an older revision.

### Phase 5 — connect intelligent outputs to the task

Use the existing validated native-component system for comparisons, data tables, source lists, charts, and useful controls. Render ordinary explanatory Markdown, tables, and math as answer content. Literal source/code remains literal when that is the requested deliverable; do not infer intent from a `text` fence alone.

Preserve component identity and user edits during updates. An interaction should explicitly update local view state or send a typed task instruction; it must not silently trigger unrelated actions. Executable HTML waits for a complete committed version in its isolated runtime.

Retain multimodal file handling, automatic search for time-sensitive questions, slash-based tools/skills, and the current model. Generated interactivity is optional: plain text remains appropriate when it answers the task best.

### Phase 6 — integration, measured acceptance, and visual iteration

Use deterministic replay tests for protocol guarantees and separately labeled live-provider runs for publication quality. Inspect the assignment workflow end to end. Run relevant frontend/backend tests and the production build after the affected implementation stabilizes.

Complete at least three substantial rendered critique/fix rounds for the streaming experience: (1) live state and meaningful partial results, (2) completion/steering/recovery, (3) dense outputs, responsive layouts, light/dark, and accessibility. These feed the broader DESIGN.md iteration requirement; one static screenshot is not evidence of streaming quality.

## 7. Acceptance gates and measurement

| Area | Required result |
| --- | --- |
| Useful progress | On eligible long tasks with separable deliverables, useful provisional content appears before finalization without an explicit “publish progress” instruction. Short/simple tasks need no artificial intermediate stage. |
| Actual activity | Every animation/status transition corresponds to a real event; concurrent calls with the same name remain distinct. Measured totals are the only source of percentages. |
| Privacy | Private canaries remain absent from live events, replay, canonical history, exports, and normal UI. Public free text is reviewed separately; a field allowlist alone cannot guarantee semantic correctness. |
| Verification | One bounded phase, result-revision linkage, accurate outcome and scope; no repeated unchanged checks or unqualified correctness badges. |
| Final answer | Exactly one authoritative completed answer; critical values and deliverables agree with evidence and saved history. |
| Failure/recovery | Disconnect/reconnect does not rerun tools. Failed/stopped/interrupted runs keep honest partial results. Unknown side effects are handled by the existing recovery policy. |
| Interaction | Draft, focus, selected result, scroll anchor, manual disclosure, and valid native control state survive updates and completion. |
| Accessibility | Keyboard-operable disclosures and controls, visible focus, reduced motion, useful live-region announcements, and readable 320 px/200% reflow. |
| Performance | Report acknowledgment, first meaningful update, first useful result, first final token, completion, and event-to-paint latency separately. Profile large answers and event bursts. |

Proposed local transport/render target: p95 event-receipt-to-visible-update below 250 ms under the defined replay workload. This is an engineering target, not a measured result or a promise about model inference time. Report missed targets rather than masking them with animation.

For model behavior, use a small paired evaluation with identical task inputs, model settings, tool access, and limits; repeat runs and record variability. Compare completion, correctness, publication usefulness, corrections between partial/final output, latency, and cost. Include tasks where an early answer is inappropriate. A formative user review can assess comprehension and perceived control, but remains unrun until actual participants take part.

## 8. Parallel ownership once implementation resumes

- **Integrator:** event contract, migration compatibility, canonical completion, cross-lane fixtures, final acceptance.
- **Harness lane:** public publication behavior, measured operation progress, check/finalization contract, bounded execution experiments.
- **Frontend lane:** live work region, grouped Activity, streaming rendering, controls, scroll/focus/accessibility.
- **Results/evaluation lane:** native output identity and interaction, regression scenarios, live task measurements, rendered critique.

Recommended priority is dependable interim results and clear activity first, then final consolidation and control, followed by richer intelligent outputs. Build on the current architecture; replace a subsystem only when the audit or measurements demonstrate the need.
