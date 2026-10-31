"""Per-turn critical path: where a turn's wall-clock time actually went.

Summing every model call and every tool duration over-counts a turn whose
tools ran concurrently, and hides the time nothing was recorded. Codex
instead follows the *dependency path* through the turn and attributes each
instant exactly once (``utils/critical-path/src/lib.rs``): a fork/join over
parallel tool calls keeps only the branch that released the join (Codex's
``ToolBatchTiming`` in ``direct-tool-timing``), and any gap not covered by a
recorded segment is ``overhead``. The analytics projection
(``analytics/src/turn_critical_path.rs``) reports ``complete`` or
``incomplete``; missing capture never becomes a zero-duration measurement.

This module is a Python transliteration of that path, fed by the timings the
SessionLog already carries:

* inference: ``assistant/message`` / ``assistant/attempt`` ``started_at`` and
  ``completed_at`` (wall seconds), plus ``llm/side-task`` compaction calls,
  which Codex also counts as inference;
* auto review: ``llm/side-task`` with ``task == "guardian"`` (it runs inside a
  tool call's dispatch, before the body starts);
* tools: the ``tool/call`` event time to its ``tool/result`` event time;
  calls whose intervals overlap form one batch, of which only the
  latest-finishing branch stays on the path;
* user wait: a tool result's ``meta.approval_wait_ms`` (or ``user_wait_ms``),
  the time its dispatch waited for a person, placed after any review.

A turn is ``incomplete`` when its start or end is missing, an assistant record
lacks timings, a tool call never settled, or a guardian review never
finished. Then every category is ``None``; only a measurable ``duration_ms``
is kept.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Optional

INFERENCE = "inference"
AUTO_REVIEW = "auto_review"
USER_WAIT = "user_wait"
TOOLS = "tools"
OVERHEAD = "overhead"
UNKNOWN = "unknown"
KINDS = (INFERENCE, AUTO_REVIEW, USER_WAIT, TOOLS, OVERHEAD, UNKNOWN)
MAX_PATH_DEPTH = 16_384  # Codex's history cap; beyond it the prefix is unknown


@dataclass(frozen=True)
class _Node:
    previous: Optional["_Node"]
    kind: str
    start: float
    end: float
    origin: float
    depth: int


class CriticalPath:
    """Immutable dependency path (``CriticalPath`` in ``utils/critical-path``)."""

    __slots__ = ("node",)

    def __init__(self, node: _Node | None = None):
        self.node = node

    @property
    def end(self) -> float | None:
        return self.node.end if self.node is not None else None

    def latest(self, other: "CriticalPath") -> "CriticalPath":
        """The later-finishing path, preferring ``self`` on a tie."""
        if other.end is not None and (self.end is None or other.end > self.end):
            return other
        return self

    def work(self, start: float, end: float, kind: str) -> "CriticalPath":
        """Append work after the path's end, clipping overlap and empty spans."""
        if self.end is not None:
            start = max(start, self.end)
        if start >= end:
            return self
        origin = self.node.origin if self.node is not None else start
        depth = (self.node.depth if self.node is not None else 0) + 1
        if depth > MAX_PATH_DEPTH:
            return CriticalPath().work(origin, end, UNKNOWN)
        return CriticalPath(_Node(self.node, kind, start, end, origin, depth))

    def durations(self, start: float, end: float) -> dict[str, float]:
        """Exclusive seconds per category over [start, end]; gaps are overhead."""
        totals = dict.fromkeys(KINDS, 0.0)
        node = self.node
        while node is not None and end > start:
            segment_end, segment_start = min(end, node.end), max(start, node.start)
            if end > node.end:
                totals[OVERHEAD] += end - max(start, node.end)
            if segment_start < segment_end:
                totals[node.kind] += segment_end - segment_start
            end = min(end, node.start)
            node = node.previous
        if start < end:
            totals[OVERHEAD] += end - start
        return totals


def _number(value: Any) -> float | None:
    if type(value) in (int, float) and math.isfinite(value):
        return float(value)
    return None


def _wait_seconds(meta: dict) -> float | None:
    for key in ("approval_wait_ms", "user_wait_ms"):
        value = _number(meta.get(key))
        if value is not None and value >= 0:
            return value / 1000
    return None


@dataclass
class _Call:
    call_id: Any
    start: float
    end: float | None = None
    wait: float | None = None


def _tool_branch(path: CriticalPath, call: _Call, reviews: list[tuple[float, float]]) -> CriticalPath:
    """One call's branch: tool time, with review and user wait carved out."""
    assert call.end is not None
    segments = sorted((max(s, call.start), min(e, call.end), AUTO_REVIEW) for s, e in reviews
                      if s < call.end and e > call.start)
    cursor = call.start
    branch = path
    for start, end, kind in segments:
        branch = branch.work(cursor, start, TOOLS).work(start, end, kind)
        cursor = max(cursor, end)
    if call.wait:
        wait_end = min(call.end, cursor + call.wait)
        branch = branch.work(cursor, wait_end, USER_WAIT)
        cursor = wait_end
    return branch.work(cursor, call.end, TOOLS)


def _incomplete(duration_ms: int | None) -> dict:
    return {"status": "incomplete", "duration_ms": duration_ms,
            **{f"{kind}_ms": None for kind in KINDS}}


def turn_critical_path(events: Iterable[dict]) -> dict:
    """Critical-path breakdown of one turn's session events (one ``turn`` id)."""
    events = list(events)
    turn_start = turn_end = None
    inference: list[tuple[float, float]] = []
    reviews: dict[Any, tuple[float, float] | None] = {}
    review_calls: dict[Any, Any] = {}
    calls: dict[Any, _Call] = {}
    complete = True
    for event in events:
        kind, data, stamp = event.get("type"), event.get("data") or {}, _number(event.get("time"))
        if kind == "turn/start":
            turn_start = stamp
        elif kind == "turn/end":
            turn_end = stamp
        elif kind in ("assistant/message", "assistant/attempt"):
            if data.get("legacy"):
                continue
            start, end = _number(data.get("started_at")), _number(data.get("completed_at"))
            if start is None or end is None or end < start:
                complete = False
            else:
                inference.append((start, end))
        elif kind == "llm/side-task":
            task = data.get("task")
            task_id = data.get("task_id", data.get("taskId", event.get("seq")))
            start, end = _number(data.get("started_at")), _number(data.get("completed_at"))
            if task == "compaction":
                if start is not None and end is not None and end >= start:
                    inference.append((start, end))
            elif task == "guardian":
                review_calls[task_id] = data.get("call_id")
                reviews[task_id] = (start, end) if start is not None and end is not None and end >= start else None
        elif kind == "tool/call":
            if data.get("legacy") or stamp is None:
                continue
            calls[data.get("callId")] = _Call(data.get("callId"), stamp)
        elif kind == "tool/result":
            call = calls.get(data.get("callId"))
            if call is None or stamp is None:
                continue
            call.end = max(call.start, stamp)
            call.wait = _wait_seconds(data.get("meta") or {})
    duration_ms = None
    if turn_start is not None and turn_end is not None and turn_end >= turn_start:
        duration_ms = round((turn_end - turn_start) * 1000)
    if duration_ms is None or not complete or any(call.end is None for call in calls.values()) \
            or any(span is None for span in reviews.values()):
        return _incomplete(duration_ms)

    def reviews_for(call: _Call) -> list[tuple[float, float]]:
        # A review names its call when the dispatcher passed one; otherwise
        # it belongs to whichever call interval contains it.
        return [span for task_id, span in reviews.items() if span is not None
                and (review_calls.get(task_id) == call.call_id
                     or (review_calls.get(task_id) is None and span[0] < call.end and span[1] > call.start))]

    def inside_call(task_id: Any, span: tuple[float, float]) -> bool:
        owner = review_calls.get(task_id)
        if owner is not None and owner in calls:
            return True
        return any(span[0] < call.end and span[1] > call.start for call in calls.values())
    # Order work by start. Inference is strictly sequential with tool batches
    # in this harness; overlapping tool calls are one fork/join batch. A
    # review outside every call interval stands on the main path itself.
    items: list[tuple[float, int, Any]] = [(start, 0, (start, end, INFERENCE)) for start, end in inference]
    items += [(span[0], 0, (span[0], span[1], AUTO_REVIEW)) for task_id, span in reviews.items()
              if span is not None and not inside_call(task_id, span)]
    items += [(call.start, 1, call) for call in calls.values()]
    items.sort(key=lambda item: (item[0], item[1]))
    path = CriticalPath()
    batch: list[_Call] = []
    batch_end = -math.inf

    def close_batch(current: CriticalPath) -> CriticalPath:
        joined = current
        for call in batch:
            joined = joined.latest(_tool_branch(current, call, reviews_for(call)))
        batch.clear()
        return joined

    for _, order, item in items:
        if order == 1:
            if batch and item.start >= batch_end:
                path = close_batch(path)
            batch_end = max(batch_end, item.end) if batch else item.end
            batch.append(item)
            continue
        if batch:
            path = close_batch(path)
        path = path.work(*item)
    if batch:
        path = close_batch(path)
    totals = path.durations(turn_start, turn_end)
    return {"status": "complete", "duration_ms": duration_ms,
            **{f"{kind}_ms": round(totals[kind] * 1000) for kind in KINDS}}
