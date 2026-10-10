"""Validated, lossless JSON envelopes for MatOpt's durable session format v1.

Surface eligibility and explicit source references follow dsh session/surface.ts.
Payloads use MatOpt's existing provider wire dictionaries, rather than dsh blocks.
"""

from __future__ import annotations

import json
import math
from typing import Any

SURFACE_TYPES = frozenset({
    "system/message", "developer/message", "user/message", "assistant/message", "tool/result",
})
KNOWN_TYPES = SURFACE_TYPES | frozenset({
    "turn/start", "turn/end", "step/start", "step/end", "assistant/attempt", "tool/call",
    "request/header", "request/context", "session/end-seed", "plan/write", "skill/activated",
    "phase/change", "verification", "compaction", "workspace/changes", "ui/message",
    "llm/retry", "llm/retry-started", "llm/side-task",
})
END_REASONS = frozenset({"completed", "aborted", "blocked", "error", "max-tokens", "interrupted", "forked"})
MAX_SAFE_INTEGER = 2 ** 53 - 1


def snapshot_json(value: Any) -> Any:
    """Reject coercions that json.dumps(default=str) would silently introduce."""
    active: set[int] = set()

    def check(obj: Any) -> None:
        if obj is None or type(obj) in (str, bool):
            return
        if type(obj) is int:
            if abs(obj) > MAX_SAFE_INTEGER:
                raise ValueError("JSON integers must be safe for browser readers")
            return
        if type(obj) is float:
            if not math.isfinite(obj):
                raise ValueError("JSON numbers must be finite")
            return
        if type(obj) not in (dict, list):
            raise TypeError(f"Not lossless JSON: {type(obj).__name__}")
        identity = id(obj)
        if identity in active:
            raise ValueError("JSON cannot contain cycles")
        active.add(identity)
        if isinstance(obj, dict):
            if any(type(key) is not str for key in obj):
                raise TypeError("JSON object keys must be strings")
            for item in obj.values():
                check(item)
        else:
            for item in obj:
                check(item)
        active.remove(identity)

    check(value)
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def is_seq(value: Any) -> bool:
    return type(value) is int and 0 <= value <= MAX_SAFE_INTEGER


def encode_source_seqs(seqs: list[int]) -> list[int | list[int]]:
    """dsh's range encoding: runs of at least three become [first, last]."""
    encoded: list[int | list[int]] = []
    i = 0
    while i < len(seqs):
        j = i + 1
        while j < len(seqs) and seqs[j] == seqs[j - 1] + 1:
            j += 1
        if j - i >= 3:
            encoded.append([seqs[i], seqs[j - 1]])
        else:
            encoded.extend(seqs[i:j])
        i = j
    return encoded


def decode_source_seqs(encoded: Any, before: int) -> list[int]:
    if not isinstance(encoded, list) or not encoded:
        raise ValueError("sourceEventSeqs must be a nonempty array")
    out: list[int] = []
    for item in encoded:
        if is_seq(item):
            if item >= before:
                raise ValueError("sourceEventSeqs must reference earlier events")
            out.append(item)
        elif (isinstance(item, list) and len(item) == 2 and all(is_seq(v) for v in item)
              and item[0] <= item[1] < before):
            out.extend(range(item[0], item[1] + 1))
        else:
            raise ValueError("Invalid sourceEventSeqs range")
    if len(set(out)) != len(out):
        raise ValueError("sourceEventSeqs must not contain duplicates")
    return out


def validate_envelope(event: dict, expected_seq: int) -> None:
    allowed = {"seq", "time", "type", "data", "surfaceOp", "sourceEventSeqs", "ignorable"}
    if set(event) - allowed:
        raise ValueError("Unknown session envelope field")
    if not is_seq(event.get("seq")) or event["seq"] != expected_seq:
        raise ValueError(f"Session sequence must be contiguous: expected {expected_seq}")
    if type(event.get("time")) not in (int, float) or not math.isfinite(event["time"]) or event["time"] < 0:
        raise ValueError("Session time must be finite epoch seconds")
    kind = event.get("type")
    if not isinstance(kind, str) or not isinstance(event.get("data"), dict):
        raise ValueError("Session type must be a string and data must be an object")
    if "ignorable" in event and event["ignorable"] is not True:
        raise ValueError("ignorable must be true when present")
    if kind not in KNOWN_TYPES:
        if event.get("ignorable") is not True:
            raise ValueError(f"Unknown non-ignorable session event: {kind}")
        return
    if kind not in SURFACE_TYPES:
        if "surfaceOp" in event or "sourceEventSeqs" in event:
            raise ValueError("Log-only events cannot carry surface metadata")
        return
    op = event.get("surfaceOp")
    if op != "append":
        if (not isinstance(op, dict) or set(op) != {"op", "startSeq", "endSeq"}
                or op["op"] != "replace" or not is_seq(op["startSeq"]) or not is_seq(op["endSeq"])
                or op["startSeq"] >= event["seq"] or op["endSeq"] >= event["seq"]):
            raise ValueError("Surface events require append or a valid replace operation")
    if "sourceEventSeqs" in event:
        decode_source_seqs(event["sourceEventSeqs"], event["seq"])
    message = event["data"].get("message")
    roles = {"system/message": "system", "developer/message": "developer", "user/message": "user",
             "assistant/message": "assistant", "tool/result": "tool"}
    if not isinstance(message, dict) or message.get("role") != roles[kind]:
        raise ValueError(f"{kind} must contain a {roles[kind]} message")
