"""Durable-log projections for MatOpt's trajectory and token disclosure.

The attempt state machine, safe-count checks, optional-bucket aggregation and
fixed-density estimator are transliterated from deepseek-harness at
639ed015397290b3745d163aafe02ffee4aa3f84 (MIT):
packages/llm/token-meter/src/{turn-usage,estimate}.ts.
MatOpt adapters report inclusive prompt_tokens, unlike dsh's disjoint input
bucket. normalize_usage validates that wire contract before the same fold.
The trajectory shape and configured monetary pricing are MatOpt additions.
"""

from __future__ import annotations

import json
import math
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from typing import Any

CHARS_PER_TOKEN = 4
BLOCK_OVERHEAD = 4
ROLE_OVERHEAD = 4
MAX_SAFE_INTEGER = 2**53 - 1


def is_count(value: Any) -> bool:
    return type(value) is int and 0 <= value <= MAX_SAFE_INTEGER


def safe_sum(values: list[int]) -> int | None:
    total = sum(values)
    return total if all(is_count(v) for v in values) and is_count(total) else None


def normalize_usage(usage: Any) -> dict | None:
    """Validate inclusive prompt counts; reasoning is an output subset."""
    if not isinstance(usage, dict):
        return None
    output = usage.get("completion_tokens", usage.get("output_tokens"))
    prompt = usage.get("prompt_tokens")
    total = usage.get("total_tokens")
    uncached = usage.get("uncached_input_tokens", usage.get("input_tokens"))
    cache_read = usage.get("cache_read_tokens", usage.get("cached_tokens"))
    cache_write = usage.get("cache_write_tokens")
    reasoning = usage.get("reasoning_tokens")
    if not is_count(output):
        return None
    for value in (prompt, total, uncached, cache_read, cache_write, reasoning):
        if value is not None and not is_count(value):
            return None
    if "cached_tokens" in usage and "cache_read_tokens" in usage:
        if usage["cached_tokens"] != usage["cache_read_tokens"]:
            return None
    if prompt is None:
        if total is not None:
            prompt = total - output
        elif uncached is not None and cache_read is not None and cache_write is not None:
            prompt = safe_sum([uncached, cache_read, cache_write])
    if not is_count(prompt):
        return None
    if total is None:
        total = safe_sum([prompt, output])
    if not is_count(total) or total != prompt + output:
        return None
    known_cache = (cache_read or 0) + (cache_write or 0)
    if known_cache > prompt or (reasoning is not None and reasoning > output):
        return None
    if uncached is not None:
        known_prompt = uncached + known_cache
        if known_prompt > prompt:
            return None
        if cache_read is not None and cache_write is not None and known_prompt != prompt:
            return None
    result = {"prompt_tokens": prompt, "completion_tokens": output, "total_tokens": total}
    if cache_read is not None:
        result.update(cache_read_tokens=cache_read, cached_tokens=cache_read)
    if cache_write is not None:
        result["cache_write_tokens"] = cache_write
    if uncached is not None:
        result["uncached_input_tokens"] = uncached
    if reasoning is not None:
        result["reasoning_tokens"] = reasoning
    return result


def aggregate_attempts(attempts: list[dict]) -> dict | None:
    if not attempts:
        return None
    result = {}
    required = ("prompt_tokens", "completion_tokens", "total_tokens")
    optional = ("cache_read_tokens", "cached_tokens", "cache_write_tokens", "reasoning_tokens", "uncached_input_tokens")
    for key in (*required, *optional):
        values = [a.get(key) for a in attempts]
        if not all(is_count(v) for v in values):
            if key in required:
                return None
            continue
        count = safe_sum(values)
        if count is None:
            return None
        result[key] = count
    return result


def stream_usage(stream: list[dict]) -> dict | None:
    result = None
    for row in stream:
        chunk = row.get("chunk", {})
        if chunk.get("type") == "usage":
            result = chunk.get("usage")
    return result


def derive_turn_usage(events: list[dict]) -> dict | None:
    """dsh's strict lifecycle fold; incomplete evidence never becomes a total."""
    state = "idle"
    turn = step = None
    by = None
    sample = None
    saw_end = False
    attempts: list[dict] = []
    automatic_steps = {(e["data"].get("turn"), e["data"].get("step")) for e in events
                       if e["type"] == "step/start" and e["data"].get("automatic")}

    def close_open() -> bool:
        normalized = normalize_usage(sample)
        if state != "open" or normalized is None:
            return False
        attempts.append(normalized)
        return True

    for event in events:
        kind, data = event["type"], event["data"]
        if data.get("legacy") or kind == "llm/side-task":
            continue
        if (data.get("turn"), data.get("step")) in automatic_steps:
            continue
        if kind == "turn/start":
            if turn is not None or state != "idle":
                return None
            turn = data["turn"]
            continue
        if turn is None:
            return None
        if kind == "turn/end":
            if data.get("turn") != turn or state != "idle" or saw_end:
                return None
            saw_end = True
            continue
        if saw_end:
            return None
        if kind == "step/start":
            if data.get("turn") != turn or state != "idle":
                return None
            state, step, sample = "open", data["step"], None
            continue
        if kind == "llm/retry-started":
            if data.get("turn") != turn or state != "settled" or by != "retry" or data.get("step") != step:
                return None
            state, sample = "open", None
            continue
        if kind in ("assistant/message", "assistant/attempt"):
            if data.get("turn") != turn or state != "open" or data.get("step") != step:
                return None
            sample = data.get("usage") or stream_usage(data.get("stream", [])) or sample
            if not close_open():
                return None
            if kind == "assistant/attempt":
                state = "finishClosed"
            else:
                state, by = "settled", "message"
            continue
        if kind == "llm/retry":
            if data.get("turn") != turn or state == "idle" or data.get("step") != step:
                return None
            if state == "settled" or (state == "open" and not close_open()):
                return None
            state, by = "settled", "retry"
            continue
        if kind == "step/end":
            if data.get("turn") != turn or state == "idle" or data.get("step") != step:
                return None
            if state == "open" and not close_open():
                return None
            state, sample = "idle", None
    return aggregate_attempts(attempts) if saw_end and state == "idle" else None


def estimate_message(message: dict) -> int:
    """Deterministic 4-char reference estimate, not provider tokenization."""
    content = message.get("content") or ""
    if message.get("role") == "system" and not content:
        return 0
    if isinstance(content, str):
        tokens = math.ceil(len(content) / CHARS_PER_TOKEN)
        if message.get("role") != "system":
            tokens += BLOCK_OVERHEAD
    else:
        tokens = 0
        for block in content:
            text = block.get("text") if isinstance(block, dict) else None
            raw = text if isinstance(text, str) else json.dumps(block, ensure_ascii=False, separators=(",", ":"))
            tokens += math.ceil(len(raw) / CHARS_PER_TOKEN) + BLOCK_OVERHEAD
    for call in message.get("tool_calls", []):
        function = call.get("function", {})
        tokens += math.ceil(len(function.get("name", "")) / CHARS_PER_TOKEN)
        tokens += math.ceil(len(function.get("arguments", "")) / CHARS_PER_TOKEN) + BLOCK_OVERHEAD
    reasoning = message.get("reasoning_content")
    if reasoning:
        tokens += math.ceil(len(reasoning) / CHARS_PER_TOKEN) + BLOCK_OVERHEAD
    return tokens + ROLE_OVERHEAD


def estimate_tools(tools: list[dict] | None) -> int:
    if not tools:
        return 0
    return math.ceil(len(json.dumps(tools, ensure_ascii=False, separators=(",", ":"))) / CHARS_PER_TOKEN) + BLOCK_OVERHEAD


def price_usage(usage: dict, rates: dict) -> Decimal | None:
    """Configured rates per million tokens; never guesses missing cache rates."""
    normalized = normalize_usage(usage)
    if normalized is None:
        return None
    try:
        input_rate, output_rate = Decimal(str(rates["input"])), Decimal(str(rates["output"]))
        read = normalized.get("cache_read_tokens")
        write = normalized.get("cache_write_tokens")
        # If prices distinguish cache buckets, those buckets must be exact.
        if "cache_read" in rates and read is None or "cache_write" in rates and write is None:
            return None
        read, write = read or 0, write or 0
        read_rate = Decimal(str(rates.get("cache_read", input_rate)))
        write_rate = Decimal(str(rates.get("cache_write", input_rate)))
        if any(not r.is_finite() or r < 0 for r in (input_rate, output_rate, read_rate, write_rate)):
            return None
        return ((normalized["prompt_tokens"] - read - write) * input_rate + read * read_rate
                + write * write_rate + normalized["completion_tokens"] * output_rate) / Decimal(1_000_000)
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return None


def _group_turns(events: list[dict]) -> OrderedDict:
    grouped: OrderedDict[Any, list[dict]] = OrderedDict()
    for event in events:
        if event["type"] == "llm/side-task":
            continue
        turn = event["data"].get("turn")
        if turn is not None:
            grouped.setdefault(turn, []).append(event)
    return grouped


def project_usage(events: list[dict]) -> dict:
    turn_rows = []
    normalized = []
    incomplete = 0
    attempt_slots: dict[tuple, dict | None] = {}
    side_tasks: dict[Any, dict | None] = {}
    for turn, rows in _group_turns(events).items():
        if not any(e["type"] == "turn/start" for e in rows):
            continue
        exact = derive_turn_usage(rows)
        turn_rows.append({"turn": turn, "exact": exact})
        incomplete += exact is None
        if exact is not None:
            normalized.append(exact)
    for event in events:
        if event["data"].get("legacy") or event["data"].get("automatic"):
            continue
        if event["type"] in ("request/header", "assistant/message", "assistant/attempt", "llm/side-task"):
            data = event["data"]
            usage = normalize_usage(data.get("usage") or stream_usage(data.get("stream", [])))
            if event["type"] == "llm/side-task":
                task_id = data.get("taskId", data.get("task_id", event["seq"]))
                side_tasks[task_id] = usage
            else:
                slot = (data.get("turn"), data.get("step"), data.get("attempt", 1))
                if event["type"] == "request/header":
                    attempt_slots.setdefault(slot, None)
                else:
                    attempt_slots[slot] = usage
    incomplete_side_tasks = sum(usage is None for usage in side_tasks.values())
    normalized.extend(usage for usage in side_tasks.values() if usage is not None)
    samples = list(attempt_slots.values()) + list(side_tasks.values())
    attempts = len(samples)
    reported = sum(sample is not None for sample in samples)
    legacy_history = any(e["type"] == "session/end-seed" and e["data"].get("legacy") for e in events)
    exact = aggregate_attempts(normalized) if incomplete == 0 and reported == attempts and not legacy_history else None
    estimate = _estimate_context(events)
    return {
        "exact": exact, "turns": turn_rows,
        "estimated_context": estimate,
        "cost": configured_cost(events), "context": _context_pressure(events, estimate),
        "coverage": {"attempts": attempts, "reported_attempts": reported, "incomplete_turns": incomplete,
                     **({"incomplete_side_tasks": incomplete_side_tasks} if incomplete_side_tasks else {}),
                     **({"legacy_history": True} if legacy_history else {})},
    }


def configured_cost(events: list[dict], prices: dict | None = None, turn: int | None = None) -> dict | None:
    if turn is None and any(e["type"] == "session/end-seed" and e["data"].get("legacy") for e in events):
        return None
    if prices is None:
        from app.config import settings
        prices = settings.model_pricing
    headers: dict[tuple, dict] = {}
    side_tasks: dict[Any, tuple[dict, dict | None]] = {}
    samples: list[tuple[dict, dict | None]] = []
    for event in events:
        kind, data = event["type"], event["data"]
        if data.get("legacy") or data.get("automatic") or (turn is not None and data.get("turn") != turn):
            continue
        key = (data.get("turn"), data.get("step"), data.get("attempt", 1))
        if kind == "request/header":
            headers[key] = data.get("header", {}).get("config", {})
            continue
        if kind not in ("assistant/message", "assistant/attempt", "llm/side-task"):
            continue
        usage = data.get("usage") or stream_usage(data.get("stream", []))
        if kind == "llm/side-task":
            task_id = data.get("taskId", data.get("task_id", event["seq"]))
            side_tasks[task_id] = (data, usage)
        else:
            samples.append((headers.pop(key, {}), usage))
    samples.extend(side_tasks.values())
    # Open requests may already have billed; never treat them as zero.
    if headers or not samples:
        return None
    amounts, currencies, dates = [], set(), set()
    for config, usage in samples:
        rate = prices.get(f"{config.get('provider')}/{config.get('model')}")
        if not isinstance(rate, dict) or not rate.get("as_of") or not rate.get("currency"):
            return None
        amount = price_usage(usage, rate)
        if amount is None:
            return None
        amounts.append(amount)
        currencies.add(rate["currency"])
        dates.add(rate["as_of"])
    if len(currencies) != 1:
        return None
    return {"amount": str(sum(amounts, Decimal(0))), "currency": next(iter(currencies)),
            "as_of": sorted(dates), "source": "operator-configured rates"}


def budget_error(events: list[dict], turn: int, settings) -> str | None:
    """Check observed spend before the next dispatch; a call can exceed a cap."""
    for scope, limit, selected_turn in (
        ("turn", settings.max_turn_cost, turn),
        ("conversation", settings.max_conversation_cost, None),
    ):
        if limit is None:
            continue
        if not settings.model_pricing:
            return f"Cannot enforce the {scope} cost limit: configure model prices first."
        if scope == "conversation" and any(e["type"] == "session/end-seed" and e["data"].get("legacy") for e in events):
            return "Cannot enforce the conversation cost limit: imported legacy spend is unknown."
        relevant = [e for e in events if e["type"] in ("assistant/message", "assistant/attempt", "llm/side-task")
                    and not e["data"].get("legacy") and not e["data"].get("automatic") and (selected_turn is None or e["data"].get("turn") == selected_turn)]
        if not relevant:
            continue
        cost = configured_cost(events, settings.model_pricing, selected_turn)
        if cost is None:
            return f"Cannot enforce the {scope} cost limit: usage or configured prices are incomplete."
        if Decimal(cost["amount"]) >= Decimal(str(limit)):
            return f"The {scope} cost limit was reached ({cost['amount']} {cost['currency']})."
    return None


def _current_messages(events: list[dict]) -> list[dict]:
    # Import lazily so the pure estimator remains usable without the store.
    from app.agent.session.derive import derive_messages
    return derive_messages(events)


def _estimate_context(events: list[dict]) -> dict | None:
    if not events:
        return None
    messages = _current_messages(events)
    header = next((e["data"].get("header", {}) for e in reversed(events) if e["type"] == "request/header"), {})
    return {"total_tokens": sum(estimate_message(m) for m in messages) + estimate_tools(header.get("tools")),
            "method": "4 characters per token plus message/tool framing"}


def _context_pressure(events: list[dict], estimate: dict | None = None) -> dict | None:
    header = next((e["data"].get("header", {}) for e in reversed(events) if e["type"] == "request/header"), None)
    if header is None:
        return None
    from app.llm.catalog import PROVIDERS
    config = header.get("config", {})
    provider = PROVIDERS.get(config.get("provider"))
    model = provider.model(config.get("model")) if provider else None
    window = model.context_window if model else None
    sample = None
    for e in reversed(events):
        data = e["data"]
        if e["type"] != "assistant/message" or not data.get("usage") or data.get("legacy"):
            continue
        if data.get("provider") == config.get("provider") and data.get("model") == config.get("model"):
            sample = normalize_usage(data["usage"])
        break
    # Until a canonical envelope match and sampled-surface anchor are proved,
    # projected_tokens is an explicitly heuristic estimate, never exact usage.
    estimate = estimate if estimate is not None else _estimate_context(events)
    return {"window": window, "pressure_tokens": sample["prompt_tokens"] if sample else None,
            "projected_tokens": estimate["total_tokens"] if estimate else None}


def _duration(start: Any, end: Any) -> int | None:
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return None
    return max(0, round((end - start) * 1000))


def project_trajectory(events: list[dict]) -> dict:
    """Group the complete ledger without assuming successful lifecycle ends."""
    turns: OrderedDict[Any, dict] = OrderedDict()
    steps: dict[tuple, dict] = {}
    calls: dict[tuple, dict] = {}
    stats = dict(turns=0, steps=0, llm_ms=0, tool_ms=0, ttft_ms=0,
                 ttft_steps=0, decode_ms=0, decode_tokens=0)
    closed_turns = set()
    request_inputs: dict[tuple, dict] = {}
    for event in events:
        kind, data, stamp = event["type"], event["data"], event["time"]
        if data.get("hidden"):
            continue
        turn_id = data.get("turn")
        if turn_id is None:
            continue
        turn = turns.setdefault(turn_id, {"turn": turn_id, "status": "running", "started_at": None,
                                          "ended_at": None, "steps": []})
        if kind == "turn/start":
            turn["started_at"] = stamp
        elif kind == "turn/end":
            reason = data.get("reason", "completed")
            turn.update(ended_at=stamp, status=reason.get("kind", "completed") if isinstance(reason, dict) else reason)
        step_id = data.get("step")
        if step_id is None:
            if kind not in ("user/message", "phase/change", "verification"):
                continue
            step_id = 0
        key = (turn_id, step_id)
        if key not in steps:
            prelude = step_id == 0
            steps[key] = {"step": step_id, "status": "completed" if prelude else "running",
                          "started_at": stamp if prelude else None, "ended_at": stamp if prelude else None, "records": []}
            turn["steps"].append(steps[key])
        step = steps[key]
        if kind == "request/header":
            # Exact messages are fetched only when an inspector selects this
            # request. Embedding every growing history in the trajectory would
            # turn a small ledger into quadratic response data.
            request_inputs[(turn_id, step_id, data.get("attempt", 1))] = {
                "request_seq": event["seq"],
                **data.get("header", {}),
            }
            continue
        if kind == "step/start":
            step["started_at"] = stamp
            continue
        if kind == "step/end":
            step.update(ended_at=stamp, status="completed")
            stats["steps"] += 1
            closed_turns.add(turn_id)
            if data.get("interrupted") or turn["status"] == "interrupted":
                step["status"] = "interrupted"
            continue
        if kind not in ("assistant/message", "assistant/attempt", "tool/call", "tool/result", "user/message", "verification", "phase/change", "llm/retry", "llm/retry-started"):
            continue
        if data.get("legacy"):
            continue
        if kind == "tool/result":
            record = calls.get((turn_id, data.get("callId")))
            if record is not None:
                duration = data.get("meta", {}).get("duration_ms")
                if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
                    duration = _duration(record["started_at"], stamp)
                record.update(status="completed" if data.get("ok") else "error", ended_at=stamp,
                              duration_ms=duration,
                              output={k: data.get(k) for k in ("ok", "output", "error", "artifacts", "meta")})
                if duration is not None:
                    record["ended_at"] = record["started_at"] + duration / 1000
                stats["tool_ms"] += record["duration_ms"] or 0
                record["source_event_seqs"].append(event["seq"])
                if data.get("meta", {}).get("interrupted") or data.get("meta", {}).get("recovery_code"):
                    record["status"] = "interrupted"
                continue
        duration = data.get("duration_ms") if kind.startswith("assistant/") else None
        ttft = data.get("ttft_ms") if kind.startswith("assistant/") else None
        started = stamp - duration / 1000 if isinstance(duration, (int, float)) else stamp
        message = data.get("message", {})
        status = "running" if kind == "tool/call" else "completed"
        if kind == "assistant/attempt":
            status = "interrupted" if data.get("interrupted") else "error"
        if kind == "assistant/message" and data.get("interrupted"):
            status = "interrupted"
        if kind == "tool/result":
            status = "interrupted" if data.get("meta", {}).get("recovery_code") else ("completed" if data.get("ok") else "error")
        sources = []
        for source in event.get("sourceEventSeqs", []):
            sources.extend(range(source[0], source[1] + 1) if isinstance(source, list) else [source])
        record = {"id": str(event["seq"]), "seq": event["seq"], "type": kind,
                  "label": data.get("name") or ("Assistant attempt" if kind == "assistant/attempt" else kind.replace("/", " · ")),
                  "status": status, "started_at": started,
                  "ended_at": None if kind == "tool/call" else stamp, "duration_ms": duration,
                  "first_token_ms": ttft, "input": data.get("arguments") if kind == "tool/call" else request_inputs.get((turn_id, step_id, data.get("attempt", 1))),
                  "output": message.get("content", data.get("content", data.get("report"))),
                  "usage": normalize_usage(data.get("usage") or stream_usage(data.get("stream", []))),
                  "source_event_seqs": list(dict.fromkeys([event["seq"], *sources]))}
        step["records"].append(record)
        if kind == "tool/call":
            calls[(turn_id, data.get("callId"))] = record
        if kind.startswith("assistant/"):
            stats["llm_ms"] += duration or 0
            if ttft is not None:
                stats["ttft_ms"] += ttft
                stats["ttft_steps"] += 1
                if record["usage"] is not None:
                    stats["decode_ms"] += max(0, (duration or 0) - ttft)
                    stats["decode_tokens"] += record["usage"]["completion_tokens"]
    for turn in turns.values():
        if turn["status"] in ("interrupted", "aborted", "error"):
            for step in turn["steps"]:
                if step["status"] == "running":
                    step["status"] = turn["status"]
                for record in step["records"]:
                    if record["status"] == "running":
                        record["status"] = "interrupted"
    stats["turns"] = len(closed_turns)
    return {"turns": list(turns.values()), "stats": stats, "legacy": not events}
