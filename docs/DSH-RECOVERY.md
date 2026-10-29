# DeepSeek Harness recovery checkpoint

Recovered on 2026-09-30 from **Improve matopt with DeepSeek harness**,
session `session-0bbb89d3-44b0-406f-91e3-9120effae9ba`.
The original compressed session remains under the user's `.dsh/sessions/`
project directory; it was read without modifying it.

This document records the initial recovery checkpoint. The frontend was later
replaced with native ZCode source at the user's request. Current UI source
mapping and verification are in [ZCODE-ADE-MIGRATION.md](ZCODE-ADE-MIGRATION.md);
the earlier frontend test counts below remain historical evidence.

## Recovered instructions and stopping point

The five human messages requested: inspect MatOpt and DeepSeek Harness and
plan the enhancements; also inspect ZCode; include the agent execution
environment; prefer direct source migration when compatible; and implement
using subagents after planning. The saved plan is `DSH-REFINEMENT-PLAN.md`.

The parent session ended at sequence 2318 with a user-aborted turn, followed
by a paused goal and end-seed marker. It was waiting on verification that
reported **47/48 frontend tests**. The checkout already contained the vendor
scaffold, design tokens and UI primitives, child-process environment filtering,
steering-order fix and tool metadata. These changes were preserved.

The resumed run reproduced a hang in the asynchronous React `act` invocation
in `Menu.test.tsx`. The selection test also expected a controlled menu to close
without closing it in its owner. Synchronous `act`, one microtask flush,
owner-controlled selection and jsdom cleanup make the suite exit normally.
Tests now run sequentially with a 512 MB Node heap cap and per-test timeout.
The production menu's vendored source did not need changes.

## Implemented continuation

- **Durable session format v1:** additive SQLite events, contiguous sequence
  validation, lossless browser-safe JSON, explicit surface replacement,
  source references, atomic worker-thread checkpoints before provider/tool
  dispatch, exact request reconstruction, and deterministic append-only crash
  repair. Compact timed streams and provider usage remain on each attempt.
- **Compatibility and transport:** existing chat messages remain a materialized
  UI view. Legacy replay is marked as lacking historical usage evidence. The
  session-log feature can be disabled; existing SSE events remain compatible.
  Committed stream checkpoints carry durable sequence IDs; token deltas do
  not. Queue buffering is bounded at 256 frames, and the disconnected-client
  check also runs during the keepalive wait. `/events` replays the ledger;
  it does not replay old SSE deltas automatically via `Last-Event-ID`.
  Title generation runs as an owned background task and does not keep a
  finished turn busy. Plain chat reload and export repair interrupted turns.
- **Trajectory:** Chat/Trajectory switching, turn and step records, timing
  overview with range filtering, search, a 50-row tail window, event paging,
  live refresh, and Input/Output/Timing/Usage inspection. Exact model request
  history is loaded only when its Input inspector is selected and discarded
  when selection changes.
- **Usage:** strict per-attempt accounting including retry attempts and
  auxiliary title/follow-up calls, replacement of cumulative usage samples,
  cache/reasoning subsets without double counting, optional buckets only when
  all attempts report them, and explicit unknown totals for missing evidence.
  Context estimates are labelled; no current monetary prices are assumed.
- **Configured pricing:** `MODEL_PRICING` accepts operator-provided rates per
  million tokens keyed by `provider/model`, with `input`, `output`, optional
  `cache_read`/`cache_write`, `currency`, and `as_of`. Optional turn/conversation
  spend checks run before the next model request. They are observed-spend
  checks: a single request can exceed a limit. Missing usage/prices and unknown
  imported spend prevent an exact cost disclosure or enforcement claim.
- **Tool scheduling:** pinned ZCode dependency ordering and bounded parallel
  groups, with MatOpt read/write barriers and conservative unknown-tool
  handling. Results retain model call order.
- **File freshness:** read-before-edit/overwrite, SHA256 plus mtime/size,
  latest complete view, truncation awareness, persisted read-state metadata,
  Python/bash invalidation, and staged publication. Nanosecond timestamps are
  exact decimal strings in JSON. The guard preserves workspace containment;
  arbitrary host execution is still not container isolation.
- **UI completion:** persistent theme and font-size controls, shared dropdown
  primitives, and verification settings disabled while a turn streams.

## Inspection endpoints

All endpoints use `/api/conversations/{id}` as the prefix:

| Endpoint | Result |
| --- | --- |
| `/events?from_seq=-1&limit=200` | Ordered page, exclusive cursor, `next_seq`, `has_more` |
| `/requests/{seq}` | Exact frozen messages, tools and config at a request header |
| `/trajectory` | Grouped ledger and whole-session timing statistics |
| `/usage` | Exact-or-unknown usage, coverage, configured cost, labelled context estimate |
| `/stats` | Timing/count projection |
| `/context` | Latest matching route's prompt pressure and reference estimate |

`stats.turns` follows DeepSeek's definition: distinct turns with at least one
closed step. Counts and timing use the whole log, independent of visible paging.
Global welcome suggestions have no conversation to attribute to; their response
includes provider usage instead of silently presenting it as conversation spend.

## Roadmap still outside this checkpoint

The original plan also describes a standalone report generator, deliverable and
workspace-diff capture, token-aware compaction, broader shell/primitive cleanup,
container execution, background computations and permission/workflow products.
Those phases are not claimed as implemented by this recovery. This checkpoint
completes the resumed core log, trajectory, accounting, scheduler and freshness
work; it does not mark the entire nine-phase design document complete.

## Validation

Verified on 2026-09-30:

| Check | Result |
| --- | --- |
| Backend: `.venv/bin/python -m pytest -q` from `backend/` | **164 passed**, 75.66 s; includes local Jupyter kernels and loopback integration |
| Frontend: `npm test` from `frontend/` | **63 passed**, 1.84 s; sequential tests, 512 MB JavaScript heap cap |
| Frontend: `NODE_OPTIONS=--max-old-space-size=2048 npm run build` | TypeScript and Vite passed, 16.48 s; existing large Plotly/Mermaid chunk warnings |
| Generated upstream copies: `bash scripts/vendor_check.sh` | 72 primitive files and 12 theme/assets match pins; 2 checks passed, 1 legacy data-agent script skipped because it has no `--check` mode |
| Whitespace: `git diff --check` | Passed |
| Rendered preview | Chat, trajectory, exact request inspector, timing and usage checked in the in-app browser using a scripted provider and isolated temporary data |

Regression coverage includes exact request checkpoints, raw tool arguments,
provider switching, concurrent auxiliary calls, append-only crash repair,
plain-chat reload/export recovery, stalled full queues, silent-provider
disconnects, checkpoint-only SSE IDs, and accepting a new turn while title
generation is in flight. Usage tests cover missing/invalid reports, cumulative
samples, retries, imported history, cache/reasoning subsets, route-aware
context pressure and configured price/budget behavior. File tests cover stale
reads, latest truncated views, restart restoration and atomic publication.

No production LLM calls or deployment were needed for verification. Changes
are left in the working tree, without committing or pushing them.
