# Verification and final-writing contract experiment

Date: 10 October 2026. **Deterministic fault injection through the actual harness; no paid calls.**

Normal mode checks a candidate and then makes a new final-writing request.
Strict mode releases the checked wording without a subsequent rewrite. Four
deliberately injected final-writer changes reached normal output and did not
reach strict output. Both modes still accepted an incorrect candidate when the
injected checker missed its error. These results establish publication and
control boundaries, **not model accuracy or a winning model**.

## Reproduction and scope

From the repository root:

```sh
backend/.venv/bin/python scripts/benchmark_verification_contract.py \
  --output docs/reference/verification-contract-results.json
cd backend
.venv/bin/python -m pytest tests/test_verification_contract.py -q
```

- Runner and fixtures: [`benchmark_verification_contract.py`](../scripts/benchmark_verification_contract.py).
- Assertions: [`test_verification_contract.py`](../backend/tests/test_verification_contract.py).
- Raw inputs, measured fields, check metadata, request sequences and source
  fingerprints: [`verification-contract-results.json`](reference/verification-contract-results.json).
- Product contract: [agent streaming](AGENT-STREAMING.md#execution-verification-and-final-streaming)
  and [protocol](PROTOCOL.md).

Validation: **12 tests passed in 1.25 seconds**, exercising 24 actual harness
runs. The standalone runner independently recorded the same 12 scenarios in
normal and strict modes. The run time is test execution time, not a provider
latency measurement. The backend test environment is required; no credentials
or external services are used.

The real `AgentHarness`, verification loop, final-writing path, `RunBudget`,
session journal, canonical database storage, public projection and event stream
execute. A scripted provider supplies every worker answer, checker verdict and
final-writer mutation. No tool body performs independent recomputation in these
fixtures. An inert registered tool keeps the requests on the agent path. The
oracle facts are supplied directly in the request; the fixture URL is an
identifier and is never fetched.

Existing tests already cover strict publication gates, private consolidation
of provisional findings, malformed verdicts, unchanged corrections and source
forwarding. This experiment adds paired field oracles, injected checker errors,
and check identity/accounting across steering. It does not replace those tests.
No production files were changed for this experiment.

## Fixtures and explicit oracle

The supplied facts are distance **60 m** and elapsed time **5 s**. The expected
answer has four fields: average speed **12 m/s**, the fixture measurement link,
and the limitation “Instantaneous speed is not established.” A steering case
changes the time to **3 s**, making the expected average speed **20 m/s**.

The oracle recognizes a deliberately narrow answer grammar, the exact named
link, and the explicit limitation sentence. It is independent of the scripted
checker verdict. It is not a semantic evaluator for arbitrary prose: an
equivalent paraphrase outside this grammar could fail the fixture oracle.
All supplied drafts use its stated grammar.

Each normal/strict pair shares the worker drafts and checker verdicts. Normal
mode additionally reaches the final writer. A drift injection is configured in
both modes, but strict mode has no post-check writer to invoke it. The difference
therefore measures removal of that unchecked stage, **not greater resistance
by the same model to an adversarial prompt**.

| Final-writing fixture | Injected writer behavior | Normal final field errors | Strict final field errors |
| --- | --- | --- | --- |
| Clean control | Preserve the candidate | None | None |
| Value | Change 12 to 15 | Value | None |
| Unit | Change m/s to km/h without conversion | Unit | None |
| Citation | Replace the measurement URL with an unsupported URL | Citation | None |
| Limitation | Remove the limitation sentence | Limitation | None |

The four normal drift cases still have verification status `correct`: the
checker saw the original candidate. Normal final text arrived as actual fake
provider deltas after `final_start(mode=streaming)`. Strict text was buffered
and exactly matched the candidate passed to the checker. Canonical reload and
the concatenated final deltas matched the final answer in all 24 runs; no
injected private reasoning or checker-report canary reached public events.

## Checker fallibility and bounded correction

| Fixture | Injected behavior | Observed behavior in both modes |
| --- | --- | --- |
| Missed error | Draft says 15 m/s; checker says correct | Wrong value published; one check, no correction |
| False failure | Correct draft; checker incorrectly rejects it; correct paraphrase then passes | One unnecessary correction and two checks |
| Inconclusive | Correct draft; checker returns uncertain | One check, no correction, explicit unverified notice |
| Successful correction | Wrong draft; checker rejects it; corrected value passes | Two checks, one correction, correct final fields |
| Correction limit | Wrong draft; changed but still-wrong value rejected again | Two checks, one correction, unresolved/provisional notice; no third correction |

The fixture config deliberately requests `verify_rounds=50`. The harness still
caps each verification episode at two checks. A correction changes the result
generation and candidate hash; the second check has a distinct check ID. These
constraints bound work and preserve provenance. They do not establish that
the checker selected sufficient tests or reached a true verdict.

The inconclusive and unresolved fixtures finish with run status `complete`
while retaining verification status `uncertain` or `limit_reached` and a
limitation notice. Here, `complete` denotes a finished run, not certified
correctness. Consumers must retain the separate verification status and notice.
Strict mode likewise means **exact wording was presented to the checker**,
not that every released answer passed the checker.

## Steering, check identity and shared allowance

A gate holds the first verifier request. A user update arrives during that
request, then the provider is released. The stale verdict is discarded and
the worker computes the updated task. This ordering uses an event gate rather
than a guessed sleep duration.

With five model attempts available, normal mode performs worker → check →
updated worker → check → final writer. Strict mode performs the first four.
The completed new check has round **1**, ID **`1:check:2`**, total check counter
**2**, and result generation **1**. Its input includes the user update, and its
SHA-256 digest matches the exact problem/candidate/source bundle sent to the
checker. The stale first check has no completed-verification record. Hashes
identify the checked material; they are not correctness certificates.

With only three model attempts available, the same sequence stops after the
updated worker. The next check cannot dispatch, no final writer runs, and the
public outcome is `error` with the model-limit notice. Steering does not renew
the shared allowance.

**Counter limitation:** `stats.verification_checks` currently increments on
entry into each check round, before provider admission. It is a count of
entered checks, not completed checks, paid requests, or whole verification
episodes. A correction increments it within an episode; steering starts a new
episode whose displayed round begins at 1. In the cap fixture the counter is
**2**, but only **1** checker request was admitted and no verdict record
completed. The run budget is the authority for actual model admissions:
**3** total requests in that fixture. The tests preserve this distinction.

## Aggregate observations and limits

| Metric over 12 runs per mode | Normal | Strict |
| --- | ---: | ---: |
| Injected writer-drift cases reaching final output | 4 of 4 | 0 of 4 |
| Injected checker errors missed | 1 | 1 |
| Injected checker false failures | 1 | 1 |
| Admitted fake model requests | 44 | 33 |
| Canonical reload mismatches | 0 | 0 |
| Private-text canary leaks | 0 | 0 |

The 11-request difference is exactly the extra normal final-writing request in
each run that reaches final publication. The shared-cap run has none. These
counts are fixture dispatches, not token-cost or production-efficiency savings.
Synthetic usage receipts have fixed values and are not measured provider usage.
There are no independent stochastic trials or accuracy confidence intervals.

The observed contract supports a conservative fallback when exact checked
wording matters: use `verify=true, strict_final=true`. The current **Check
results** UI enables normal result checking; strict wording remains an API
option. This experiment changes neither the default nor the settings UI. With
published partial findings, strict mode can privately consolidate before
checking; that separate existing path is not included in this numeric matrix.

The remaining empirical question is how often real worker/checker/final-writer
models introduce, catch or worsen errors on representative tasks. A later
empirical evaluation should use held-out tasks with source-backed field
oracles and human adjudication, fixed model versions/settings, paired inputs,
and repeated trials. Record errors by value/unit/citation/limitation, checker
misses and false failures, coverage losses during correction, calibration of
uncertain outcomes, end-to-end latency and actual usage. Include long documents,
multilingual responses, code results, current-source questions, and published
partial-result consolidation. The present results neither select a model nor
show that general-purpose verification is deterministic.
