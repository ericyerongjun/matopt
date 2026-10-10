from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.agent.session.telemetry import (
    aggregate_attempts, budget_error, configured_cost, derive_turn_usage, estimate_message, normalize_usage,
    price_usage, project_trajectory, project_usage,
)


def event(seq, kind, data, stamp=None):
    row = {"seq": seq, "time": stamp if stamp is not None else 1000 + seq / 10, "type": kind, "data": data}
    if kind in ("system/message", "assistant/message", "user/message", "tool/result"):
        row["surfaceOp"] = "append"
    return row


def transcript(usage=None):
    return [
        event(0, "turn/start", {"turn": 1}),
        event(1, "system/message", {"message": {"role": "system", "content": "Solve accurately"}}),
        event(2, "user/message", {"turn": 1, "message": {"role": "user", "content": "2+2"}}),
        event(3, "step/start", {"turn": 1, "step": 1}),
        event(4, "request/header", {"turn": 1, "step": 1, "header": {"config": {"provider": "fake", "model": "fake"}, "tools": []}}),
        event(5, "assistant/message", {"turn": 1, "step": 1, "attempt": 1,
              "message": {"role": "assistant", "content": "4"}, "stream": [],
              "duration_ms": 100, "ttft_ms": 25, "usage": usage}),
        event(6, "step/end", {"turn": 1, "step": 1}),
        event(7, "turn/end", {"turn": 1, "reason": {"kind": "completed"}}),
    ]


USAGE = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
         "cached_tokens": 60, "reasoning_tokens": 5}


def test_normalize_inclusive_cache_and_output_subset():
    usage = normalize_usage(USAGE)
    assert usage["total_tokens"] == 120  # neither cache nor reasoning added twice
    assert usage["cache_read_tokens"] == 60
    assert normalize_usage({"input_tokens": 40, "cache_read_tokens": 60,
                            "cache_write_tokens": 0, "output_tokens": 20})["total_tokens"] == 120
    assert normalize_usage({"completion_tokens": 20, "total_tokens": 120})["prompt_tokens"] == 100


@pytest.mark.parametrize("change", [
    {"prompt_tokens": True}, {"completion_tokens": -1}, {"total_tokens": 999},
    {"reasoning_tokens": 21}, {"cached_tokens": 101}, {"prompt_tokens": 1.0},
    {"total_tokens": 2**53}, {"cache_read_tokens": 59},
])
def test_reject_contradictory_or_unsafe_usage(change):
    assert normalize_usage({**USAGE, **change}) is None


def test_optional_buckets_require_all_attempts():
    first = normalize_usage(USAGE)
    second = normalize_usage({"prompt_tokens": 10, "completion_tokens": 5})
    aggregate = aggregate_attempts([first, second])
    assert aggregate == {"prompt_tokens": 110, "completion_tokens": 25, "total_tokens": 135}


def test_complete_turn_and_missing_boundaries():
    rows = transcript(USAGE)
    assert derive_turn_usage(rows)["total_tokens"] == 120
    assert derive_turn_usage(rows[:-1]) is None
    assert derive_turn_usage([e for e in rows if e["type"] != "step/end"]) is None
    assert derive_turn_usage(transcript()) is None


def test_retry_is_a_second_billed_attempt_and_samples_replace():
    rows = transcript(USAGE)
    retry = [
        event(5, "assistant/attempt", {"turn": 1, "step": 1, "attempt": 1,
              "stream": [{"chunk": {"type": "usage", "usage": {"prompt_tokens": 2, "completion_tokens": 1}}},
                         {"chunk": {"type": "usage", "usage": {"prompt_tokens": 10, "completion_tokens": 5}}}]}),
        event(6, "llm/retry", {"turn": 1, "step": 1}),
        event(7, "llm/retry-started", {"turn": 1, "step": 1}),
    ]
    tail = [dict(e, seq=e["seq"] + 3) for e in rows[5:]]
    folded = derive_turn_usage(rows[:5] + retry + tail)
    assert folded == {"prompt_tokens": 110, "completion_tokens": 25, "total_tokens": 135}
    retry[0]["data"]["stream"] = []
    assert derive_turn_usage(rows[:5] + retry + tail) is None


def test_projection_discloses_coverage_not_partial_exact_total():
    rows = transcript(USAGE)
    usage = project_usage(rows)
    assert usage["exact"]["total_tokens"] == 120
    assert usage["coverage"] == {"attempts": 1, "reported_attempts": 1, "incomplete_turns": 0}
    assert usage["cost"] is None
    assert usage["context"]["window"] is None
    assert usage["estimated_context"]["total_tokens"] > 0
    assert project_usage(rows[:-1])["exact"] is None
    rows.append(event(8, "llm/side-task", {"task": "title", "usage": None}))
    assert project_usage(rows)["exact"] is None
    assert project_usage(rows)["coverage"]["attempts"] == 2


def test_trajectory_pairs_calls_and_preserves_failures():
    rows = transcript(USAGE)
    rows[5]["data"]["message"]["tool_calls"] = [{"id": "call", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]
    rows[6:6] = [
        event(6, "tool/call", {"turn": 1, "step": 1, "callId": "call", "name": "read_file", "arguments": "{}"}, 1001),
        event(7, "tool/result", {"turn": 1, "step": 1, "callId": "call", "name": "read_file", "ok": False,
              "output": "", "error": "missing", "meta": {}, "message": {"role": "tool", "tool_call_id": "call", "content": "missing"}}, 1001.25),
    ]
    rows = [dict(e, seq=i) for i, e in enumerate(rows)]
    projection = project_trajectory(rows)
    records = next(s["records"] for s in projection["turns"][0]["steps"] if s["step"] == 1)
    tool = next(r for r in records if r["type"] == "tool/call")
    assert tool["status"] == "error" and tool["duration_ms"] == 250
    assert tool["output"]["error"] == "missing"
    assert projection["stats"]["tool_ms"] == 250
    assert projection["stats"]["decode_tokens"] == 20
    assert project_trajectory([])["legacy"] is True


def test_prices_require_declared_rates_and_known_cache_buckets():
    rates = {"input": "2", "cache_read": ".2", "output": "4"}
    assert price_usage(USAGE, rates) == Decimal("0.000172")
    assert price_usage(USAGE, {**rates, "cache_write": "1"}) is None
    assert price_usage(USAGE, {"input": "-1", "output": "4"}) is None
    assert price_usage(USAGE, {"input": "NaN", "output": "4"}) is None
    assert estimate_message({"role": "system", "content": "1234"}) == 5
    assert estimate_message({"role": "system", "content": ""}) == 0


def test_seeded_replay_and_title_pair_do_not_double_count():
    rows = transcript(USAGE)
    legacy = event(5, "assistant/message", {"turn": 1, "step": 1, "legacy": True,
                   "message": {"role": "assistant", "content": "previous"}})
    rows.insert(5, legacy)
    rows = [dict(e, seq=i) for i, e in enumerate(rows)]
    assert project_usage(rows)["exact"]["total_tokens"] == 120
    assert project_usage(rows)["coverage"]["attempts"] == 1
    rows.extend([
        event(9, "llm/side-task", {"task_id": "title", "task": "title", "status": "started"}),
        event(10, "llm/side-task", {"task_id": "title", "task": "title", "status": "completed", "usage": USAGE}),
    ])
    assert project_usage(rows)["exact"]["total_tokens"] == 240
    assert project_usage(rows)["coverage"]["attempts"] == 2
    rows.append(event(11, "session/end-seed", {"legacy": True}))
    assert project_usage(rows)["exact"] is None
    assert project_usage(rows)["coverage"]["legacy_history"]


def test_configured_cost_and_budget_checks_are_fail_closed():
    rates = {"fake/fake": {"input": "2", "output": "4", "currency": "USD", "as_of": "2026-09-30"}}
    rows = transcript(USAGE)
    assert configured_cost(rows, rates)["amount"] == "0.00028"
    settings = SimpleNamespace(model_pricing=rates, max_turn_cost=.0001, max_conversation_cost=None)
    assert "limit was reached" in budget_error(rows, 1, settings)
    settings.model_pricing = {}
    assert "configure model prices" in budget_error([], 1, settings)
    settings.model_pricing, settings.max_turn_cost, settings.max_conversation_cost = rates, None, 1
    rows.append(event(8, "session/end-seed", {"legacy": True}))
    assert configured_cost(rows, rates) is None
    assert "legacy spend" in budget_error(rows, 1, settings)


def test_parallel_tool_timing_uses_individual_duration():
    rows = transcript(USAGE)
    rows.insert(6, event(6, "tool/call", {"turn": 1, "step": 1, "callId": "fast", "name": "read_file", "arguments": "{}"}, 1001))
    rows.insert(7, event(7, "tool/result", {"turn": 1, "step": 1, "callId": "fast", "ok": True, "meta": {"duration_ms": 5}}, 1003))
    rows = [dict(e, seq=i) for i, e in enumerate(rows)]
    records = next(s["records"] for s in project_trajectory(rows)["turns"][0]["steps"] if s["step"] == 1)
    tool = next(r for r in records if r["type"] == "tool/call")
    assert tool["duration_ms"] == 5 and tool["ended_at"] == 1001.005
