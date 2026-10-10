"""Compact timed streams adapted from dsh llm/src/assistant-stream.ts.

MatOpt timestamps and gaps are epoch seconds; the upstream uses integer ms.
Adjacent homogeneous deltas are packed without losing member timing or bytes.
"""

from __future__ import annotations

from .events import snapshot_json


class AssistantStreamAccumulator:
    def __init__(self):
        self._records: list[dict] = []
        self._last_time: float | None = None

    def push(self, timestamp: float, chunk: dict) -> None:
        chunk = snapshot_json(chunk)
        previous = self._records[-1] if self._records else None
        kind = chunk["type"]
        if kind in {"text-delta", "reasoning-delta", "tool-call-delta"}:
            record_type = {"text-delta": "text-chunks", "reasoning-delta": "reasoning-chunks",
                           "tool-call-delta": "tool-call-chunks"}[kind]
            identity = {"type": record_type, "index": chunk.get("index", 0)}
            if kind == "tool-call-delta":
                identity["id"] = chunk.get("id", "")
                if chunk.get("name"):
                    identity["name"] = chunk["name"]
            key = "args" if kind == "tool-call-delta" else "texts"
            value = chunk.get("argumentsDelta", "") if kind == "tool-call-delta" else chunk["text"]
            if previous is not None and all(previous.get(k) == v for k, v in identity.items()) and (
                    ("name" in previous) == ("name" in identity)):
                previous["dt"].append(timestamp - self._last_time)
                previous[key].append(value)
            else:
                self._records.append({**identity, "time0": timestamp, "dt": [], key: [value]})
        else:
            self._records.append({"type": "chunk", "time": timestamp, "chunk": chunk})
        self._last_time = timestamp

    def finish(self) -> list[dict]:
        return snapshot_json(self._records)


def expand_stream(records: list[dict]) -> list[dict]:
    out = []
    for record in records:
        if record["type"] == "chunk":
            out.append({"time": record["time"], "chunk": snapshot_json(record["chunk"])})
            continue
        timestamp = record["time0"]
        kind = record["type"]
        key = "args" if kind == "tool-call-chunks" else "texts"
        if len(record["dt"]) != len(record[key]) - 1:
            raise ValueError("Compact stream timing count mismatch")
        for index, value in enumerate(record[key]):
            if index:
                timestamp += record["dt"][index - 1]
            chunk = {"type": {"text-chunks": "text-delta", "reasoning-chunks": "reasoning-delta",
                              "tool-call-chunks": "tool-call-delta"}[kind], "index": record["index"]}
            if kind == "tool-call-chunks":
                chunk.update(id=record["id"], argumentsDelta=value)
                if "name" in record:
                    chunk["name"] = record["name"]
            else:
                chunk["text"] = value
            out.append({"time": timestamp, "chunk": chunk})
    return out
