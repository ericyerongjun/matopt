# Cache-stable prompt layout and summarising compaction

Date: 21 October 2026. **Integrated into the native harness; no setting is needed.**

## Problem

DeepSeek reuses cached prompt tokens only for a prefix that is identical from
the first token. The system message embedded the workspace file listing,
datasets, the skill catalog re-ranked for each request, active skill bodies,
the date and interactive UI state. Any of these changing between turns
invalidated the cached prefix for the whole conversation history. Native tools
were also reordered to put preferred tools first, and a mid-turn `/skill`
rewrote the system head.

## Change

- [`build_system_prompt`](../backend/app/agent/prompts.py) holds only facts that are
  stable for a deployment. Per-turn facts move into a `<turn-context>` message
  that the harness logs (source `turn-context`) immediately before the user's
  text. Earlier turns keep their own context message, so history stays a fixed
  prefix. The public conversation never shows these messages.
- Native tools keep a fixed order; mid-turn skill loads only append messages.
- Past the history budget, [`compaction.py`](../backend/app/agent/compaction.py)
  replaces the oldest whole turns with one rolling `<conversation-summary>`
  written by a budgeted side call (deterministic digest on failure), shrinking
  history to half the budget so the one-off cache invalidation is rare.

## Measurement

[`measure_prompt_cache.py`](../scripts/measure_prompt_cache.py) ran the same live
three-turn task (create a CSV, regress on it, a short follow-up) on DeepSeek V4.1
Flash with low reasoning, once per version. Per-request records:
[before](reference/prompt-cache-before.json), [after](reference/prompt-cache-after.json).

| Cache-read share of prompt tokens | Before | After |
|---|---|---|
| First request of turns 2 and 3 | 19.4% | 79.7% |
| All requests | 65.1% | 84.5% |
| System message or tool list changed between turns | yes | no |

What still misses on a new turn's first request is genuinely new content: the
previous answer, the new turn context and the question. The model took a
different number of steps in each run, so the totals are indicative rather than
a controlled comparison; the per-turn system/tool equality is exact.

Trade-off: each turn's context (about 2–3k tokens) stays in history. Cached
tokens are billed at a small fraction of uncached input, and compaction removes
old contexts along with the turns they describe.

Regression tests: `test_later_turns_extend_the_earlier_request_as_an_exact_prefix`
and [`test_compaction.py`](../backend/tests/test_compaction.py).
