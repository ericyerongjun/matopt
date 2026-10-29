# Operation-specific loop policy: controller and harness experiments

Date: 10 October 2026. **Integrated into the native harness behind `LOOP_POLICY=operation|legacy`; default `operation`.**

The candidate avoids whole-tool bans after a particular operation fails. The
deterministic comparison found fewer false blocks in deliberately constructed
fixtures. It does **not** establish improved model quality, production task
success, cost or latency. The isolated controller comparison used no provider
requests or real tools. A separate native-harness comparison below uses a fake
provider, the actual Python tool with a controlled persistent kernel, and gated
concurrent fixture tools. Neither experiment makes paid provider calls or uses
external services. Final-writer factual drift is outside both comparisons.

## Scope and implementation

- Candidate: [`loop_policy.py`](../backend/app/agent/loop_policy.py).
- Unit and parity tests: [`test_loop_policy.py`](../backend/tests/test_loop_policy.py).
- Offline runner: [`benchmark_loop_policy.py`](../scripts/benchmark_loop_policy.py).
- Complete deterministic traces and source fingerprints:
  [`loop-policy-controller-results.json`](reference/loop-policy-controller-results.json).

The original isolated comparison did not modify the production execution path.
The subsequent harness integration is described separately below. The controller
comparison freezes `REPEAT_LIMIT=3`, `_failure_fingerprint`, `_record_failures` and canonical
tool resolution behavior. It does not simulate the rest of the harness, its
model, failure nudges, final writer or verification loop. Unit parity checks
execute the native failure helper against equivalent results without dispatching
a tool. The runner checks a source hash of the three helper functions so later
changes cannot silently alter the claimed baseline.

The candidate has three deliberate differences:

1. Failure identity includes the resolved tool, exact canonical JSON arguments,
   caller-owned task generation and explicit resource revisions. Three known
   failures with the same error fingerprint block that operation, leaving corrected arguments, other
   operations and changed generations eligible. Error fingerprints retain the
   baseline error name and first error line, capped at 240 characters; they are
   not a comparison of entire stack traces.
2. The failure streak is consecutive **for that operation**. A known success or
   a different error ends its matching-error streak; unrelated activity does
   not reset it. The baseline instead accumulates matching errors across
   successes and then bans the whole tool. The intermittent-success fixture
   isolates this additional difference from operation scoping.
3. Successful-read observation is opt-in. Three consecutive, complete, identical
   content SHA-256 observations make `admit()` return an advisory review reason,
   while `allowed` remains true. A changed fingerprint, partial observation or
   missing complete evidence clears the read streak. Display text, timestamps
   and revision numbers alone do not qualify.

Arguments are snapshotted before storage, preserving JSON types. Object key
ordering is normalized, but code strings, paths and semantically different JSON
numbers are not guessed equivalent. Observation IDs make replay idempotent;
reusing an ID with different facts is an error. This object is scoped to a run;
it neither executes nor automatically retries work.

## Progress, budgets and uncertain effects

Polling and optimization accept typed runner-observed counters, job/search
identities and state tokens. The first sample is only an observation; it is not
called advancement. Counter regressions are recorded without moving the high
water mark backwards. A stable best objective does not imply no progress while
candidate evaluations advance. Stable pending status does not imply failure or
completion. None of these observations alone blocks an operation or declares a
task correct.

`Deadline` takes caller-owned monotonic time. Observing another result never
renews it. In the comparison, the same deadline and operation-cap gates precede
both policies. The candidate adds no replacement global budget and reserves no
extra model calls. Opaque successful Python, shell or MCP output is left
unclassified; stdout equality cannot establish what happened to hidden state.

`Outcome("unknown")` is recorded as `recovery_required`, never an ordinary
repeatable failure. **The candidate is not a replacement for `RecoveryGuard`.**
Admission must receive the existing recovery decision; `recovery_blocked=True`
takes precedence even after a task/resource change. The candidate cannot infer
authorization from a read, a model statement or a new generation. In the
simulation, unknown-effect protection and an optional one-attempt grant are
explicit shared fixture inputs. Those fixtures validate the policy composition
assumption, not the real recovery implementation.

## Reproduction and observed results

From the repository root:

```sh
backend/.venv/bin/python scripts/benchmark_loop_policy.py \
  --output docs/reference/loop-policy-controller-results.json --repeats 3
cd backend
.venv/bin/python -m pytest tests/test_loop_policy.py -q
```

The runner uses only the Python standard library and the isolated candidate.
The focused tests additionally use the existing backend test environment for
the native helper parity check. No credentials are needed.

There are **18 hand-authored fixture variants**, run with three deterministic
completion-order seeds per policy: 54 controller traces per policy. Only the
parallel fixture varies completion order. These repetitions are not independent
model trials and do not justify confidence intervals or claims about general
agent accuracy.

| Metric over 54 traces per policy | Frozen baseline | Candidate |
| --- | ---: | ---: |
| Admission/effect oracle satisfied | 36 | 54 |
| Requested fixture operations | 294 | 294 |
| Simulated tool-body executions | 237 | 258 |
| Denied operations | 57 | 36 |
| False blocks against explicit fixture oracle | 21 | 0 |
| Required blocks missed | 0 | 0 |
| Advisory read-review admissions | 0 | 9 |

The candidate executes **21 more legitimate operations** because it avoids
false blocks. This is not a tool-call saving. Both policies execute the
deterministically failing operation three times before blocking it. Both also
execute all six successful repeated reads: advisory review does not reduce
their call count. Any future bounded recovery/termination decision based on
that advisory needs a separate experiment.

Per-fixture executions below are for one seed; all three seeds have the same
admission totals. A false block means the fixture explicitly required the next
operation to remain admissible, not that a model judge disliked the answer.

| Fixture | Baseline executions | Candidate executions | Baseline false blocks | Candidate false blocks |
| --- | ---: | ---: | ---: | ---: |
| Exact deterministic failure | 3 | 3 | 0 | 0 |
| Corrected arguments after failures | 3 | 4 | 1 | 0 |
| Different operations with the same error | 3 | 4 | 1 | 0 |
| Changed resource revision | 3 | 4 | 1 | 0 |
| Changed task generation | 3 | 4 | 1 | 0 |
| Intermittent successful execution | 4 | 6 | 2 | 0 |
| Repeated full read, opted in | 6 | 6 | 0 | 0 |
| Full read without opt-in | 6 | 6 | 0 | 0 |
| Partial read | 6 | 6 | 0 | 0 |
| Identical opaque stdout | 6 | 6 | 0 | 0 |
| Changed full-content fingerprint | 6 | 6 | 0 | 0 |
| Poll remains pending, then completes | 5 | 5 | 0 | 0 |
| Poll reaches its existing deadline | 4 | 4 | 0 | 0 |
| Optimization plateau while evaluations advance | 6 | 6 | 0 | 0 |
| Optimization with no progress reaches shared cap | 4 | 4 | 0 | 0 |
| Unknown effect already committed; inspect only | 2 | 2 | 0 | 0 |
| Unknown effect absent; inspect, then granted attempt | 3 | 3 | 0 | 0 |
| Parallel failing and progressing siblings | 6 | 7 | 1 | 0 |

The unknown-effect fixtures finish with exactly one simulated side effect under
both policies. Batch admission is evaluated before shuffled completions, so a
result cannot retroactively cancel an already admitted sibling. No inference
about the production scheduler's races follows from this simulation.

Focused validation: **23 tests passed**. They cover identity snapshots, generation
isolation, successful/different-error resets, malformed declarations, replay
idempotence, read opt-in/completeness, typed progress and deadlines, recovery
precedence, baseline parity and deterministic comparison output. No full harness
or live-provider integration was run for this experiment.

## Actual native-harness paired fixtures

These results are distinct from the 54 simulated controller traces per policy.
The actual `AgentHarness`, scheduler, shared execution service, run budget and
session journal run under each policy with identical settings and an identical
receipt-based answer oracle. The fixture provider is deterministic and runs
locally. `strict_final=True` isolates loop-policy behavior from a separate final
writer; verification and web search are off. These are two constructed regression
cases, not a representative task sample or an estimate of live-model success.

1. **Stateful Python correction:** the actual `PythonTool` executes three fixed
   programs that increment a persistent counter and then raise the same
   `NameError`. The provider uses explicit aliases and one Markdown fence; all
   normalize to the same operation. It then supplies corrected source defining
   the missing value. The kernel fixture executes these fixed test programs in
   one persistent Python dictionary instead of launching Jupyter. The final
   answer passes only when the actual corrected-call receipt contains `42`.
2. **Parallel aliases and an unrelated tool:** three same-operation failures
   must enter an async gate together. The tool is explicitly declared concurrent
   in this synthetic fixture; the actual Python tool remains serial. The next
   batch requests the unchanged operation, corrected arguments and an unrelated
   read-only inspection. The oracle requires the corrected receipt and completed
   inspection. No already-admitted sibling is retroactively cancelled.

| Actual-harness fixture / metric | Legacy | Operation |
| --- | ---: | ---: |
| Stateful correction: provider requests | 5 | 5 |
| Stateful correction: actual tool executions | 3 | 4 |
| Stateful correction: observed identical failures | 3 | 3 |
| Stateful correction: blocked calls | 1 | 0 |
| Stateful correction: durable call/result pairs | 4 | 4 |
| Stateful correction: answer oracle | Fail | Pass |
| Parallel control: provider requests | 3 | 3 |
| Parallel control: actual tool executions | 4 | 5 |
| Parallel control: blocked calls | 2 | 1 |
| Parallel control: answer oracle | Fail | Pass |

The operation policy spends one more legitimate tool execution per fixture; it
does not reduce model requests in these measurements. Both policies block the
unchanged fourth operation in the parallel case and allow the unrelated
inspection. The legacy final response honestly reports the corrected computation
was blocked, so a completed message is not treated as a passed task oracle.

Raw per-policy records are written by the tests, not reconstructed from this
table: [`loop-policy-harness-results.json`](reference/loop-policy-harness-results.json).
They include source fingerprints and exact fixture boundaries. Regenerate them
from the repository root:

```sh
MATOPT_LOOP_POLICY_RESULTS_PATH=docs/reference/loop-policy-harness-results.json \
  backend/.venv/bin/python -m pytest backend/tests/test_loop_policy_harness.py -q -s
```

Without that environment variable, the tests do not write an experiment artifact.
All **8 harness-policy tests passed**. They also cover trusted user-steer
generations, complete file-read revisions, recovery precedence and one-shot
retry permission, budget controls excluded from repair counts, and advisory
unchanged reads that neither block nor finish work.

The combined focused policy/controller/budget-execution/shared-execution/MCP
suite passed **62 tests** (two existing FastMCP deprecation warnings). Separately,
the existing `test_harness.py` suite passed **24 tests**, including real Jupyter
kernel tests, when run with localhost socket access. An initial sandboxed run
could not bind Jupyter ports; that environment failure was resolved by rerunning
the same tests with socket permission. Those broader regressions are not additional
paired loop-policy measurements.

## Integration boundary and remaining experiments

The native harness resolves aliases and normalizes arguments once, then checks
the operation policy through an optional admission callback in
`ToolExecutionService.dispatch`. Existing validation, recovery, unsettled-work
and capability/policy guards run first. The callback precedes shared run-budget
admission, so denied operations spend no tool-start allowance and emit no
`tool_running` event. Each requested call still gets a durable result. The frozen
legacy helpers remain unchanged and are used only under `LOOP_POLICY=legacy`.
The operation policy is currently attached by the native harness; MCP and the delegated
data agent do not acquire a new operation guard through this change.

Task generation is the count of actually applied user steers. For native file
operations, existing typed file-read records supply known content revisions.
Successful complete reads contribute advisory fingerprints, never inferred
completion. Python/kernel state, shell effects and remote state remain opaque:
there is no invented resource version derived from stdout. After three identical
failures, an opaque operation needs materially corrected arguments or a new user
instruction; unrelated activity alone does not erase its failure history.
An external file change is not known until a trusted read observation exists.
Broader state-version tracking needs a separate design and measurement.

Public Activity does not expose argument identities or raw failure fingerprints.
The blocked tool result invites corrected arguments or relevant inspection and
does not claim that the entire tool is unavailable. `RecoveryGuard` remains
stronger, and its one-shot user authorization is checked exactly once.

This policy does not change verification or final writing. Standard final
writing remains a separate preservation risk. Test numeric/unit/
citation drift and omitted limitations independently, alongside the strict
buffered mode, rather than attributing those outcomes to this operation policy.

The next separate experiment is a paired live pilot of real task quality and
redundant work; none was run here. The broader research plan and its limits
remain in [AGENT-EXPERIENCE-PLAN.md](AGENT-EXPERIENCE-PLAN.md).
