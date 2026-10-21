#!/usr/bin/env python3
"""Measure provider prompt-cache reuse across turns of one live conversation.

Opt-in and paid: it sends a short multi-turn task to a running backend that has
DIAGNOSTICS_ENABLED=true, then reads the private session ledger. For every
model request it prints prompt tokens and cache hits; for the first request of
each later turn it also reports whether the system message or tool list changed
since the previous turn's last request, since those changes invalidate the
cached prefix for everything after them.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

TURNS = (
    "Use python to compute the first 20 primes and save them to primes.csv. Reply in two sentences.",
    "Using primes.csv, fit a linear regression of p_n on n and report the slope with a 95% confidence interval. Be brief.",
    "What is the sum of those 20 primes? Answer in one line.",
)


def send(client: httpx.Client, cid: str | None, text: str) -> str:
    body = {"message": text, "reasoning": "low", "verify": False}
    if cid:
        body["conversation_id"] = cid
    with client.stream("POST", "/api/chat/stream", json=body, timeout=900) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:])
            except ValueError:
                continue
            cid = cid or event.get("conversation_id") or (event.get("data") or {}).get("conversation_id")
    if not cid:
        cid = client.get("/api/conversations?limit=1").json()["conversations"][0]["id"]
    return cid


def events(client: httpx.Client, cid: str) -> list[dict]:
    out, cursor = [], -1
    while True:
        page = client.get(f"/api/conversations/{cid}/events", params={"from_seq": cursor, "limit": 1000})
        page.raise_for_status()
        data = page.json()
        out.extend(data["events"])
        if not data["has_more"]:
            return out
        cursor = data["next_seq"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    client = httpx.Client(base_url=args.base_url, timeout=60)

    cid = None
    for text in TURNS:
        cid = send(client, cid, text)

    log = events(client, cid)
    requests = {}
    for event in log:
        if event["type"] == "request/header":
            detail = client.get(f"/api/conversations/{cid}/requests/{event['seq']}").json()
            requests[(event["data"]["turn"], event["data"]["step"])] = detail
    rows, previous = [], None
    for event in log:
        if event["type"] != "assistant/message":
            continue
        data = event["data"]
        usage = data.get("usage") or {}
        key = (data.get("turn"), data.get("step"))
        prompt, hit = usage.get("prompt_tokens") or 0, usage.get("cache_read_tokens") or 0
        if not prompt:
            continue
        request = requests.get(key)
        row = {"turn": key[0], "step": key[1], "prompt": prompt, "cached": hit,
               "rate": round(hit / prompt, 3) if prompt else None}
        if request and previous and key[0] != previous[0][0]:
            prior = previous[1]
            row["system_changed"] = request["messages"][0] != prior["messages"][0]
            names = [t["function"]["name"] for t in request.get("tools") or []]
            prior_names = [t["function"]["name"] for t in prior.get("tools") or []]
            row["tools_changed"] = request.get("tools") != prior.get("tools")
            row["tool_order_changed"] = names != prior_names and sorted(names) == sorted(prior_names)
        rows.append(row)
        if request and request.get("tools"):  # Skip the tool-free final writer.
            previous = (key, request)

    first = [r for r in rows if r["turn"] != rows[0]["turn"] and "system_changed" in r]
    summary = {
        "conversation_id": cid,
        "requests": rows,
        "overall_rate": round(sum(r["cached"] for r in rows) / max(1, sum(r["prompt"] for r in rows)), 3),
        "cross_turn_first_request_rate": round(sum(r["cached"] for r in first) / max(1, sum(r["prompt"] for r in first)), 3),
    }
    args.output.write_text(json.dumps(summary, indent=2))
    for r in rows:
        print(r)
    print("overall", summary["overall_rate"], "cross-turn first requests", summary["cross_turn_first_request_rate"])


if __name__ == "__main__":
    main()
