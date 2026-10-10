"""One in-memory admission ledger shared by native, OCR and delegated work.

This module performs no I/O, schedules no work, and does not cancel operations.
Callers must dispatch only when ``admission.is_new`` is true and must use
``remaining_timeout`` to bound awaited operations. Counts are admission limits,
not a monetary estimate; admitted failed/cancelled attempts are never refunded.

The deadline is absolute and monotonic. Waiting for a user, including an
acknowledged pause, does not extend it. Existing per-tool/per-agent limits can
remain stricter than this shared run allowance.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable, Literal

from app.agent.session.telemetry import aggregate_attempts, is_count, normalize_usage


class BudgetExhausted(Exception):
    """A control outcome: stop admission rather than ask an agent to repair it."""

    def __init__(self, dimension: str, *, limit: int | float | None, used: int | float | None, detail: str = ""):
        self.dimension, self.limit, self.used = dimension, limit, used
        self.code = "RUN_BUDGET_EXHAUSTED"
        super().__init__(detail or f"The run's {dimension.replace('_', ' ')} limit was reached.")


@dataclass(frozen=True)
class BudgetAdmission:
    id: str
    kind: Literal["model", "tool"]
    source: str
    admitted_at: float
    provider: str = ""
    model: str = ""
    final: bool = False
    is_new: bool = True


@dataclass
class _ModelRecord:
    admission: BudgetAdmission
    outcome: str | None = None
    usage: dict | None = None
    usage_status: str = "pending"


def _seconds(value: float | None, field: str) -> float | None:
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{field} must be a finite positive number or None")
    return float(value)


def _identity(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonempty text")
    return value


class RunBudget:
    def __init__(self, *, model_attempt_limit: int | None = None, tool_start_limit: int | None = None,
                 timeout_seconds: float | None = None, reserve_final_slot: bool = False,
                 clock: Callable[[], float] = time.monotonic):
        for name, limit in (("model_attempt_limit", model_attempt_limit), ("tool_start_limit", tool_start_limit)):
            if limit is not None and not is_count(limit):
                raise ValueError(f"{name} must be a nonnegative safe integer or None")
        if type(reserve_final_slot) is not bool:
            raise ValueError("reserve_final_slot must be a boolean")
        if reserve_final_slot and model_attempt_limit == 0:
            raise ValueError("A final slot cannot be reserved from a zero model allowance")
        self._model_limit, self._tool_limit = model_attempt_limit, tool_start_limit
        self._timeout = _seconds(timeout_seconds, "timeout_seconds")
        self._reserve_final = reserve_final_slot and model_attempt_limit is not None
        self._clock = clock
        self._lock = threading.Lock()
        self._models: dict[str, _ModelRecord] = {}
        self._tools: dict[str, BudgetAdmission] = {}
        self._final_admitted = False
        self._last_now = -math.inf
        self._started_at = self._now()
        self._deadline = None if self._timeout is None else self._started_at + self._timeout
        if self._deadline is not None and not math.isfinite(self._deadline):
            raise ValueError("deadline must be finite")

    def _now(self) -> float:
        value = self._clock()
        if type(value) not in (int, float) or not math.isfinite(value) or value < self._last_now:
            raise ValueError("clock must return finite monotonic seconds")
        self._last_now = float(value)
        return float(value)

    def _check_deadline(self, now: float) -> None:
        if self._deadline is not None and now >= self._deadline:
            raise BudgetExhausted("deadline", limit=self._timeout, used=now - self._started_at,
                                  detail="The run's absolute deadline was reached.")

    def admit_model(self, attempt_id: str, *, source: str, provider: str = "", model: str = "",
                    final: bool = False) -> BudgetAdmission:
        """Charge once before an actual request, including each retry.

        Reusing the same ID returns ``is_new=False``: it is a lookup of a prior
        admission, not permission to repeat an external request. A retry needs
        a new attempt ID. Reusing an ID with different metadata is an error.
        """
        _identity(attempt_id, "attempt_id")
        _identity(source, "source")
        if not isinstance(provider, str) or not isinstance(model, str) or type(final) is not bool:
            raise ValueError("provider/model must be text and final must be boolean")
        with self._lock:
            previous = self._models.get(attempt_id)
            if previous:
                admission = previous.admission
                if (admission.source, admission.provider, admission.model, admission.final) != (source, provider, model, final):
                    raise ValueError("Attempt ID already belongs to a different model request")
                return replace(admission, is_new=False)
            now = self._now()
            self._check_deadline(now)
            held = int(self._reserve_final and not self._final_admitted and not final)
            if self._model_limit is not None and len(self._models) >= self._model_limit - held:
                raise BudgetExhausted("model_attempts", limit=self._model_limit, used=len(self._models),
                                      detail="The work model allowance was reached; one attempt remains reserved for the final response."
                                      if held else "The run's model attempt limit was reached.")
            admission = BudgetAdmission(attempt_id, "model", source, now, provider, model, final)
            self._models[attempt_id] = _ModelRecord(admission)
            self._final_admitted |= final
            return admission

    def admit_tool(self, call_id: str, *, source: str) -> BudgetAdmission:
        """Charge one actual handler start after validation and policy checks."""
        _identity(call_id, "call_id")
        _identity(source, "source")
        with self._lock:
            previous = self._tools.get(call_id)
            if previous:
                if previous.source != source:
                    raise ValueError("Call ID already belongs to a different tool request")
                return replace(previous, is_new=False)
            now = self._now()
            self._check_deadline(now)
            if self._tool_limit is not None and len(self._tools) >= self._tool_limit:
                raise BudgetExhausted("tool_starts", limit=self._tool_limit, used=len(self._tools))
            admission = BudgetAdmission(call_id, "tool", source, now)
            self._tools[call_id] = admission
            return admission

    def settle_model(self, attempt_id: str, *, usage: dict | None = None, outcome: str = "completed") -> bool:
        """Store a terminal receipt; return whether accounting information changed.

        Absent/invalid/explicitly estimated usage stays unknown, never zero.
        A late valid receipt may refine unknown usage for the same outcome.
        Identical receipts are idempotent; conflicting exact receipts or terminal
        outcomes are rejected. Settlement still works after the deadline.
        """
        if outcome not in {"completed", "error", "aborted"}:
            raise ValueError("outcome must be completed, error or aborted")
        estimated = isinstance(usage, dict) and usage.get("estimated") is True
        normalized = None if estimated else normalize_usage(usage)
        status = "reported" if normalized is not None else "estimated" if estimated else "missing" if usage is None else "invalid"
        with self._lock:
            record = self._models.get(attempt_id)
            if record is None:
                raise KeyError(f"No admitted model attempt {attempt_id!r}")
            if record.outcome is not None:
                if record.outcome != outcome:
                    raise ValueError("Conflicting terminal outcome for model attempt")
                if record.usage is not None:
                    if normalized is not None and normalized != record.usage:
                        raise ValueError("Conflicting exact usage for model attempt")
                    return False
                if normalized is None:
                    return False
            record.outcome, record.usage, record.usage_status = outcome, normalized, status
            return True

    def remaining_timeout(self, local_timeout: float | None = None) -> float | None:
        """Bound an await by the stricter local timeout and absolute run deadline."""
        local = _seconds(local_timeout, "local_timeout")
        with self._lock:
            now = self._now()
            self._check_deadline(now)
            remaining = None if self._deadline is None else self._deadline - now
            return remaining if local is None else local if remaining is None else min(local, remaining)

    def snapshot(self) -> dict:
        """Detached accounting snapshot; known subtotals are not exact run totals."""
        with self._lock:
            now = self._now()
            records = list(self._models.values())
            reported = [record.usage for record in records if record.usage is not None]
            subtotal = aggregate_attempts(reported)
            complete = len(reported) == len(records) and (subtotal is not None or not records)
            remaining = None if self._model_limit is None else max(0, self._model_limit - len(records))
            held = int(self._reserve_final and not self._final_admitted)
            return {
                "model_attempts": len(records), "model_attempt_limit": self._model_limit,
                "tool_starts": len(self._tools), "tool_start_limit": self._tool_limit,
                "remaining_model_attempts": remaining,
                "remaining_work_model_attempts": None if remaining is None else max(0, remaining - held),
                "remaining_tool_starts": None if self._tool_limit is None else max(0, self._tool_limit - len(self._tools)),
                "final_slot_reserved": bool(held),
                "started_at": self._started_at, "deadline": self._deadline,
                "remaining_seconds": None if self._deadline is None else max(0.0, self._deadline - now),
                "deadline_reached": self._deadline is not None and now >= self._deadline,
                "usage": {"exact": subtotal if complete else None,
                          "reported_subtotal": subtotal, "complete": complete,
                          "admitted_attempts": len(records), "reported_attempts": len(reported),
                          "unsettled_attempts": sum(record.outcome is None for record in records),
                          "unknown_settled_attempts": sum(record.outcome is not None and record.usage is None for record in records)},
                "model_receipts": {record.admission.id: {"source": record.admission.source,
                    "provider": record.admission.provider, "model": record.admission.model,
                    "final": record.admission.final, "outcome": record.outcome,
                    "usage_status": record.usage_status,
                    "usage": dict(record.usage) if record.usage is not None else None} for record in records},
            }
