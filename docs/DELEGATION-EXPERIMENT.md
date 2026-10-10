# Direct execution versus sequential LAMBDA

This experiment compares two existing MatOpt execution paths on small file and arithmetic tasks. It tests whether invoking LAMBDA earns its additional model calls for these tasks. It does **not** test a native parallel agent tree, independent expert voting, large analysis reports, or delegation in general.

The corrected live pilot supports keeping native execution as the default for these short tasks under a six-attempt run limit: both paths produced correct files, but native execution delivered all four final answers using fewer tokens and less time. The first pilot exposed a restricted-tool prompt mismatch and remains diagnostic evidence. Both full pilots, including every failure, are preserved below. The runner still defaults to fake providers; fake results validate execution and accounting plumbing, not model quality or real latency.

## Matched conditions

Run two fixtures twice through each path: **8 runs / 4 matched pairs**. Counterbalance which path runs first by fixture and repetition. Each run receives a fresh conversation, identical input bytes and user prompt, and a cold conversation kernel. The benchmark process uses new temporary databases and upload directories; it does not attach to the running app or write into existing chats.

| Condition | Direct path | Delegated path |
|---|---|---|
| Outer loop | `AgentHarness` | Same `AgentHarness` |
| Model | Configured `deepseek-flash` | Same for outer loop and child |
| Worker reasoning | High | High |
| Native final writer | Existing reasoning-off policy | Same policy |
| Tools advertised to outer model | Python, read/write/list files | `lambda_data_agent` |
| Child loop | None | Existing sequential LAMBDA loop and tools |
| Corrected outer prompt | Common benchmark instructions plus exact registered menu | Same template plus this path's exact registered menu |
| Corrected unknown-tool error | Lists only registered IDs | Same rule |
| Model verifier | Disabled | Disabled |
| Correctness check | Independent environment and answer oracles | Same oracles |
| Outer automatic titles / skills / web / MCP | Disabled | Disabled |
| Shared run allowance | 6 model attempts, 8 tool starts, 120 seconds | Same object across parent and child |
| Completion cap | 2,048 tokens per request | Same cap, including actual child requests |

The script records actual stage reasoning; it does not force the final writer to think. Model verification is disabled to avoid substituting a model verdict for an independent oracle; ordinary worker checks remain possible. The two paths intentionally retain their different full tool schemas and child prompt. LAMBDA's full description still prefers native tools for bounded tasks, but the corrected outer prompt explicitly makes absent tools unavailable. The child retains its specialized instructions and tool set. Results therefore compare these concrete platform paths, not the isolated causal effect of “having another agent.”

`--tool-menu inherited` retains the first pilot's application prompt/router behavior. `--tool-menu registry` changes only the script's outer prompt and unknown-tool hints: both paths use one common instruction template, followed by their actual tool names and the first sentence of each description. Actual handlers, full provider tool schemas, child instructions, model settings, budgets, final writer, and oracles are unchanged by this switch. No forced tool call, added discovery handler, relaxed allowance, or automatic retry was introduced. Each report records the mode, common prompt hash, and full source hashes. Production changes between pilot snapshots mean the two pilots are not an isolated before/after ablation of the menu change.

The process retains the configured provider route and credentials in live mode. Credentials are never written to the result. A script-only transport observer records request hashes, byte sizes, model settings, reported usage, provider completion reason, request outcomes, and request latency. It rejects oversized requests and caps output equally for both paths. It introduces no provider retries.

## Fixtures and independent oracles

1. **Invoice:** four inventory rows, including zero quantity. Required `summary.json`: `{"row_count":4,"unit_count":9,"total_cents":1703}`.
2. **Ledger:** five signed entries across three accounts. Required `summary.json`: `{"entry_count":5,"net_cents":2100,"by_account":{"A":1050,"B":150,"C":900}}`.

Each user prompt requires `summary.json` at the workspace root and asks for the same JSON object as the terminal answer. The runner checks these independently:

- Required output file exists at the correct path and parses to exactly the expected object.
- Input file still exists and its SHA-256 is unchanged.
- The final chat answer parses to the expected object; exact JSON formatting is a separate field.
- All `summary.json` locations and workspace files are recorded, so a correctly named file in the wrong directory does not pass.
- Run status, final-ready state, failed tool results, provider retry attempts, and finish reasons are retained. A plausible answer cannot substitute for a failed file oracle, and an existing correct file cannot conceal a failed terminal answer.

The expected values are fixed independently of model output. In fake mode, a fixed Python solver reads the fixture through the actual tools and writes the result; the fake provider’s final answer is scripted. Passing that smoke is not evidence that a real model solved the task.

## Commands

From the repository root, use the backend virtual environment. Local Jupyter kernels require permission to bind loopback sockets.

```sh
# No paid provider calls; one matched pair exercises both actual execution paths.
backend/.venv/bin/python scripts/benchmark_delegation.py \
  --output /tmp/matopt-delegation-smoke.json --repeats 1 --fixtures invoice

# No paid calls; check that absent child usage stays unknown in both ledgers.
backend/.venv/bin/python scripts/benchmark_delegation.py \
  --output /tmp/matopt-delegation-missing-usage.json \
  --repeats 1 --fixtures ledger --smoke-missing-usage

# Opt-in live pilot, after production files are stable and smoke checks pass.
backend/.venv/bin/python scripts/benchmark_delegation.py \
  --live --tool-menu registry \
  --output /tmp/matopt-delegation-registry-live.json \
  --repeats 2 --fixtures invoice ledger \
  --model-attempts 6 --tool-starts 8 --timeout 120 \
  --max-output-tokens 2048 --max-request-bytes 60000
```

Choose fresh output names: the runner refuses to overwrite earlier results or journals. Its `--live` flag is the only mode that dispatches real configured provider calls. It does not launch tasks concurrently.

The 8-run live envelope is **at most 48 provider requests and 98,304 completion tokens**, assuming the configured endpoint honors `max_tokens`. Each serialized request is limited to 60,000 bytes. The sum of run deadlines is 960 seconds, with kernel startup/cleanup and bookkeeping overhead outside that sum. These are workload bounds, not a fabricated dollar ceiling. Bytes are not token counts. Dollar costs are reported only when genuine usage and operator-configured pricing are both available; missing rates or usage produce `null`, not zero. No pricing is inferred from an old website or model name.

## Saved evidence

The final JSON validates against [`reference/delegation-result.schema.json`](reference/delegation-result.schema.json). It includes each trial, oracle, terminal text, public event counts, shared `RunBudget` snapshot, separate wire receipts, conditions, source hashes, and paired differences.

Two append-only, fsynced journals beside the final output preserve evidence while the experiment runs:

- `.receipts.jsonl`: provider starts, genuine usage observations, terminal request receipts, and child tool starts/outcomes.
- `.runs.jsonl`: experiment conditions, run starts, and completed trial records.

An interrupted experiment may have no final JSON. Its completed trials and started-but-unsettled requests remain in these journals and the isolated SQLite/session logs. Do not mark an incomplete request free or automatically repeat it. Source hashes before and after execution cover the runner, result schema, and Python sources in both backend application packages; changed-source results are not eligible for comparison.

For every path, report all attempted runs, file/answer pass counts, status counts, median latency, native versus child request counts, provider finish reasons, retries, and tool failures. Compare token totals only for pairs with complete genuine receipts. Keep reported subtotals and missing-receipt counts separate. The independent wire receipt count and required token counters must agree with the parent budget ledger.

## First live pilot: restricted-menu confound

The first pilot started on **2026-10-10 at 12:02:28 +08:00** and retained all eight trials. Its delegated registry contained only `lambda_data_agent`, but the inherited system prompt also advertised native, skill, and discovery tools. The unknown-tool response recommended `search_tools` even though it was absent. Every delegated trial initially requested the unavailable `python` tool. This setup mismatch materially consumed the shared allowance; the results cannot establish delegation's intrinsic cost or answer quality.

| Observed metric, all attempted trials | Direct | Delegated |
|---|---:|---:|
| Correct root file and unchanged input | 4/4 | 4/4 |
| Completed run with exact requested final JSON | 4/4 | 1/4 |
| Terminal run status | 4 complete | 1 complete, 3 error |
| Actual provider requests | 16 native | 15 native + 9 child |
| Reported prompt tokens | 42,625 | 91,822 |
| Reported completion tokens | 1,441 | 5,988 |
| Reported total tokens | 44,066 | 97,810 |
| Median elapsed time | 13.584 s | 19.241 s |
| Unknown-tool rejections | 0 | 8 |
| Budget-control tool results | 0 | 2 |
| Failed executed child tools | — | 0/6 |

Three delegated trials exhausted the **same six model attempts** before a final response could be dispatched. These were admission failures, not wrong arithmetic, timeouts, provider failures, or truncated responses:

- **Invoice, repetition 1:** four outer requests included repeated unavailable-tool attempts; two child requests successfully produced the file. The next outer worker admission was denied.
- **Ledger, repetition 1:** the first child produced the correct file. A second delegated read-back request used one child model request and two successful file reads; its following model admission was denied.
- **Invoice, repetition 2:** after producing the correct file, an outer request asked LAMBDA for a read-back. Its first child model admission was denied because all six attempts were already used.
- **Ledger, repetition 2:** the sixth attempt was the final writer and returned the requested JSON successfully.

All 40 observed provider starts have one matching usage receipt and terminal receipt. All eight trial journal records equal their final report entries. Required token counters match the parent shared budget exactly; there are **zero missing or unsettled usage receipts**. The 121 recorded source hashes match before and after the run. Provider outcomes were all completed, with 26 `tool_calls` and 14 `stop` finish reasons; there were no `length` finishes. Both SDK clients disable automatic retries, and no harness retry attempts occurred. No allowance was silently increased and no failed trial was rerun or omitted. The runner's exit 0 confirms a completed, source-stable and reconciled experiment; it does not mean every task succeeded.

No dollar cost is reported: the recorded pricing configuration did not produce a price, so cost remains `null`. Observed latency and token differences include the restricted-menu failures, different schemas, and sequential child context. They must not be presented as a clean estimate of delegation overhead.

Sanitized evidence preserves every successful and failed trial, request receipt, and counter:

- [Original-pilot report](reference/delegation-live-2026-10-10-inherited.json)
- [Provider and child tool receipts](reference/delegation-live-2026-10-10-inherited.receipts.jsonl)
- [Incremental trial journal](reference/delegation-live-2026-10-10-inherited.runs.jsonl)
- [Reconciliation audit and precise failure phases](reference/delegation-live-2026-10-10-inherited.audit.json)

The audit export replaces temporary absolute paths and generated conversation/child IDs, preserves original-file hashes, and excludes private reasoning, agent messages, tool arguments/output, credentials, and provider request bodies. Request hashes, actual usage, timings, statuses, oracles, and source hashes remain intact. Original local evidence remains in `/private/tmp/matopt-delegation-live.*` and its isolated state directory.

## Corrected registry-menu pilot

The corrected fake smoke exited 0 on 2026-10-10: all four file/input and exact final-answer oracles passed, all request ledgers reconciled, and two injected missing child usage receipts stayed unknown. Static checks also confirm that an unknown-tool response cannot suggest absent discovery tools. Evidence is at `/private/tmp/matopt-delegation-registry-smoke.json` and its journal siblings.

The corrected live pilot started on **2026-10-10 at 12:12:15 +08:00**, using a fresh result file and the same eight-run, six-attempt, eight-tool, 120-second, 2,048-output-token envelope. All eight actual outer system prompts and 18 worker request headers were checked against the registered menus. Every delegated trial immediately used `lambda_data_agent`; there were **no unknown-tool rejections**.

| Observed metric, all attempted corrected trials | Direct | Delegated |
|---|---:|---:|
| Correct root file and unchanged input | 4/4 | 4/4 |
| Completed run with exact requested final JSON | 4/4 | 1/4 |
| Terminal run status | 4 complete | 1 complete, 3 error |
| Actual provider requests | 16 native | 7 native + 17 child |
| Reported prompt tokens | 22,918 | 124,900 |
| Reported completion tokens | 1,568 | 9,501 |
| Reported total tokens | 24,486 | 134,401 |
| Median elapsed time | 13.678 s | 21.509 s |
| Executed tool starts, including delegation | 9 | 19 |
| Unknown-tool rejections | 0 | 0 |
| Budget-control tool results | 0 | 1 |
| Failed executed child tools | — | 0/14 |

All corrected trials are retained here; **every row passed the independent file/input oracle**. An error row means no requested final JSON was delivered, despite the correct saved file.

| Fixture | Repetition | Path | Terminal result | Requests | Total tokens | Elapsed seconds |
|---|---:|---|---|---:|---:|---:|
| Invoice | 1 | Direct | Complete, exact final JSON | 4 | 6,152 | 13.779 |
| Invoice | 1 | Delegated | Model allowance exhausted | 6 | 44,325 | 21.792 |
| Ledger | 1 | Direct | Complete, exact final JSON | 4 | 6,079 | 13.366 |
| Ledger | 1 | Delegated | Model allowance exhausted | 6 | 34,246 | 21.226 |
| Invoice | 2 | Direct | Complete, exact final JSON | 4 | 6,132 | 13.847 |
| Invoice | 2 | Delegated | Complete, exact final JSON | 6 | 23,710 | 20.624 |
| Ledger | 2 | Direct | Complete, exact final JSON | 4 | 6,123 | 13.576 |
| Ledger | 2 | Delegated | Model allowance exhausted | 6 | 32,120 | 23.512 |

The failure phases are different from the first pilot:

- **Invoice and ledger, repetition 1:** one outer request plus five child requests completed the LAMBDA call and produced the correct file. The next outer worker admission was denied because all six attempts were used.
- **Ledger, repetition 2:** one outer plus four child requests produced the correct file. The sixth request was an outer request for another delegated read-back; its first child model admission was denied.
- **Invoice, repetition 2:** three child requests and three outer requests, including the final writer, fit within the six-attempt allowance.

No failed corrected trial dispatched a final-writer request. No time limit, tool-start limit, output truncation, or provider error caused these failures. There was no reserved final model slot in this condition. A sequential child can therefore consume the remaining parent allowance before finalization; increasing the allowance or reserving a final slot could change completion rates, but neither was tested or silently applied. The child completed 14 tool handlers successfully: eight Python executions, five shell executions, and one file glob. Its specialized workflow and larger context remain part of the measured path.

Every matched pair used identical human-prompt and input hashes. All eight output files and input hashes were independently rechecked from the saved workspaces. All 40 provider starts, usage receipts, and terminal receipts reconcile with the parent budget; all eight completed journal entries match the final report. Unknown usage, unsettled requests, and native retry attempts are all zero, and SDK automatic retries remain disabled. All 121 source hashes were stable throughout this pilot. Provider finishes were 26 `tool_calls` and 14 `stop`, with no truncations. The maximum observed request size was 51,224 bytes and the maximum completion was 703 tokens, within the recorded caps. Cost remains `null` because no configured rates produced a price.

Delegation used more reported tokens and elapsed time in all four matched pairs; the median paired time difference was **7.936 seconds**. This supports a narrow routing decision for these tasks and this shared allowance. It does not isolate the effect of delegation from the different tool schemas and child instructions, estimate quality on complex analysis/report tasks, or show that larger budgets or parallel agents behave the same way. The three failed trials remain in every aggregate; no run was retried or excluded.

- [Corrected-pilot report](reference/delegation-live-2026-10-10-registry.json)
- [Corrected provider and child tool receipts](reference/delegation-live-2026-10-10-registry.receipts.jsonl)
- [Corrected incremental trial journal](reference/delegation-live-2026-10-10-registry.runs.jsonl)
- [Corrected reconciliation audit, menu checks, and failure phases](reference/delegation-live-2026-10-10-registry.audit.json)

These exports use the same sanitization as the first pilot. Original corrected evidence remains at `/private/tmp/matopt-delegation-registry-live.*` and its isolated state directory. The original pilot remains available and is not replaced by this run. Four source files changed between the two pilots—the runner, harness, skill router, and LAMBDA tool—so cross-pilot differences are not a controlled menu-only ablation.

## Decision rule and limits

**Keep native execution as the default for short, bounded arithmetic/file tasks; retain LAMBDA for explicit requests and substantial specialized workflows.** This decision uses the corrected pilot only: delegation supplied no file-correctness gain here, consumed more tokens/time, and failed to finish three final answers under the same six-attempt limit. Shared admission and usage accounting worked correctly, with no bypass or hidden retry. Keep those controls; the pilot does not justify automatically increasing a child's allowance. An accounting discrepancy, silent truncation, cancellation leak, or changed input would block promotion regardless of answer quality.

Any future change to this default would need a broader matched fixture set and independent task-specific oracles. Keep every attempted run and separate budget-policy changes from execution-path changes; do not select the best of several runs. No further candidate rerun was performed here. Two fixtures and four pairs are a diagnostic pilot, not a statistically reliable ranking or a general “best harness” claim.

Long analysis/report tasks may justify LAMBDA because it has specialized workflows; this pilot does not estimate that benefit. A later separate experiment would need report-specific artifact oracles and equal budgets. No result here establishes a benefit from parallel collaboration, because the delegated implementation tested here is sequential.

## Smoke evidence

On 2026-10-10, the corrected fake smoke ran both fixtures once per path, with one deliberately missing child usage receipt per delegated run:

```sh
backend/.venv/bin/python scripts/benchmark_delegation.py \
  --output /tmp/matopt-delegation-smoke-final.json \
  --repeats 1 --fixtures invoice ledger --smoke-missing-usage
```

It exited **0**: all four runs completed, all four file/input oracles passed, all four final answers matched exactly, and all four parent budget ledgers matched the independently observed requests and reported usage. Direct execution used three scripted native requests per run; delegation used three scripted native plus two scripted child requests. Both omitted child usage receipts remained unknown, so those runs' exact token totals and paired token differences were `null`. No paid provider requests were made. These request counts describe the scripted smoke, not expected live behavior.

Raw evidence is retained at `/tmp/matopt-delegation-smoke-final.json` and its `.receipts.jsonl` / `.runs.jsonl` siblings. Isolated state is at `/var/folders/s0/81mn2nlx1z77vcfrf005ttgr0000gn/T/matopt-delegation-bdumv4qn`. The smoke's recorded source hashes were unchanged during execution. The final runner subsequently broadened the source hash inventory only; static/schema validation covers that instrumentation change.

The initial development smoke caught a recorder assumption that rejected the native final writer’s intentional reasoning-off call. A second smoke passed its oracles but exited 4 when concurrent production edits changed its source hashes. Both development records are retained and excluded from performance conclusions. Fake-provider latency and synthetic token counts are never used to rank the paths.
