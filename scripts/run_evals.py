#!/usr/bin/env python3
"""Graded model-quality evals against a running MatOpt backend.

Opt-in and paid: each problem in the JSONL suite runs as a fresh conversation
through the real harness and provider. The final ``\\boxed{}`` answer is graded
with math-verify, or numerically when the item declares ``abs_tol``. Results
record pass/fail, wall time, model and tool calls, tokens and prompt-cache
reuse, so a prompt or harness change can be compared with ``--baseline``.

    backend/.venv/bin/python scripts/run_evals.py --output evals/results/run.json
    backend/.venv/bin/python scripts/run_evals.py --tag statistics --baseline evals/results/run.json ...
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import time
from pathlib import Path

import httpx
from math_verify import parse, verify

ROOT = Path(__file__).resolve().parents[1]
INSTRUCTION = "\n\nEnd your answer with the final result alone in \\boxed{}."


def last_boxed(text: str) -> str | None:
    start = text.rfind("\\boxed{")
    if start < 0:
        return None
    depth, i = 0, start + len("\\boxed")
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[i + 1:j]
    return None


def grade(item: dict, boxed: str | None) -> bool:
    if boxed is None:
        return False
    if "abs_tol" in item:
        candidates = parse(f"${boxed}$") or []
        for value in candidates:
            try:
                if abs(float(value) - float(item["answer"])) <= item["abs_tol"]:
                    return True
            except (TypeError, ValueError):
                continue
        match = re.search(r"-?\d+(?:\.\d+)?(?:[eE]-?\d+)?", boxed.replace(",", ""))
        return bool(match) and abs(float(match.group()) - float(item["answer"])) <= item["abs_tol"]
    return bool(verify(parse(f"${item['answer']}$"), parse(f"$\\boxed{{{boxed}}}$")))


async def run_item(client: httpx.AsyncClient, item: dict, reasoning: str, keep: bool) -> dict:
    created = await client.post("/api/conversations", json={"title": f"[eval] {item['id']}"})
    created.raise_for_status()
    cid = created.json()["id"]
    started = time.monotonic()
    body = {"conversation_id": cid, "message": item["question"] + INSTRUCTION, "reasoning": reasoning}
    error = None
    try:
        async with client.stream("POST", "/api/chat/stream", json=body, timeout=1_800) as response:
            response.raise_for_status()
            async for _ in response.aiter_lines():
                pass
    except httpx.HTTPError as exc:
        error = repr(exc)
    elapsed = time.monotonic() - started
    for _ in range(60):  # The stream ends just before the turn's usage is persisted.
        conversation = (await client.get(f"/api/conversations/{cid}")).json()
        usage = (await client.get(f"/api/conversations/{cid}/usage")).json().get("exact") or {}
        if not conversation.get("running") and usage.get("prompt_tokens"):
            break
        await asyncio.sleep(1)
    assistant = next((m["content"] for m in reversed(conversation.get("messages", [])) if m["role"] == "assistant"), {})
    text = "\n".join(p.get("text", "") for p in assistant.get("parts", []) if p.get("type") == "text")
    boxed = last_boxed(text)
    prompt = usage.get("prompt_tokens") or 0
    result = {
        "id": item["id"], "tags": item["tags"], "passed": grade(item, boxed), "expected": item["answer"],
        "boxed": boxed, "status": assistant.get("status"), "error": error, "seconds": round(elapsed, 1),
        "llm_calls": (assistant.get("stats") or {}).get("llm_calls"),
        "tool_calls": (assistant.get("stats") or {}).get("tool_calls"),
        "prompt_tokens": prompt, "completion_tokens": usage.get("completion_tokens"),
        "cache_rate": round((usage.get("cache_read_tokens") or 0) / prompt, 3) if prompt else None,
    }
    if not keep:
        await client.delete(f"/api/conversations/{cid}")
    return result


def summarise(results: list[dict]) -> dict:
    def mean(key):
        values = [r[key] for r in results if isinstance(r.get(key), (int, float))]
        return round(statistics.fmean(values), 3) if values else None
    by_tag: dict[str, list[bool]] = {}
    for r in results:
        for tag in r["tags"]:
            by_tag.setdefault(tag, []).append(r["passed"])
    return {
        "n": len(results), "pass_rate": round(sum(r["passed"] for r in results) / max(1, len(results)), 3),
        "by_tag": {tag: f"{sum(v)}/{len(v)}" for tag, v in sorted(by_tag.items())},
        "mean_seconds": mean("seconds"), "mean_llm_calls": mean("llm_calls"), "mean_tool_calls": mean("tool_calls"),
        "mean_prompt_tokens": mean("prompt_tokens"), "mean_completion_tokens": mean("completion_tokens"),
        "mean_cache_rate": mean("cache_rate"), "errors": sum(1 for r in results if r["error"]),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--suite", type=Path, default=ROOT / "evals" / "math_core.jsonl")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tag", action="append", help="only run items with this tag (repeatable)")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--reasoning", default="high", choices=["off", "low", "high", "max"])
    parser.add_argument("--keep", action="store_true", help="keep eval conversations instead of deleting them")
    parser.add_argument("--baseline", type=Path, help="earlier results file to compare against")
    args = parser.parse_args()

    items = [json.loads(line) for line in args.suite.read_text().splitlines() if line.strip()]
    if args.tag:
        items = [item for item in items if set(item["tags"]) & set(args.tag)]
    items = items[:args.limit] if args.limit else items
    gate = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=60) as client:
        async def bounded(item):
            async with gate:
                result = await run_item(client, item, args.reasoning, args.keep)
                print(f"{'PASS' if result['passed'] else 'FAIL'} {result['id']:<16} {result['seconds']:>6}s "
                      f"boxed={result['boxed']!r} expected={result['expected']!r}", flush=True)
                return result
        results = await asyncio.gather(*(bounded(item) for item in items))

    report = {"suite": str(args.suite.relative_to(ROOT)), "reasoning": args.reasoning,
              "summary": summarise(results), "results": sorted(results, key=lambda r: r["id"])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2))
    if args.baseline:
        base = {r["id"]: r for r in json.loads(args.baseline.read_text())["results"]}
        for r in results:
            if r["id"] in base and base[r["id"]]["passed"] != r["passed"]:
                print(f"CHANGED {r['id']}: {base[r['id']]['passed']} -> {r['passed']}")
        print("baseline", json.dumps(json.loads(args.baseline.read_text())["summary"]))


if __name__ == "__main__":
    asyncio.run(main())
