"""Deterministic admission/accounting invariants; no provider requests or sleeps."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from threading import Barrier

import pytest

from app.agent.budget import BudgetExhausted, RunBudget
from app.agent.session.telemetry import MAX_SAFE_INTEGER, normalize_usage


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def usage(prompt=10, completion=4, **extra):
    return {"prompt_tokens": prompt, "completion_tokens": completion, **extra}


def test_native_ocr_and_delegated_attempts_share_one_allowance():
    budget = RunBudget(model_attempt_limit=3, tool_start_limit=2)
    for source in ("native", "ocr", "data"):
        assert budget.admit_model(source, source=source).is_new
    for source in ("native", "data"):
        assert budget.admit_tool(source, source=source).is_new
    with pytest.raises(BudgetExhausted) as model_error:
        budget.admit_model("data-retry", source="data")
    assert (model_error.value.dimension, model_error.value.limit, model_error.value.used) == ("model_attempts", 3, 3)
    assert model_error.value.code == "RUN_BUDGET_EXHAUSTED"
    with pytest.raises(BudgetExhausted, match="tool starts"):
        budget.admit_tool("native-next", source="native")
    snapshot = budget.snapshot()
    assert snapshot["model_attempts"] == 3
    assert snapshot["tool_starts"] == 2
    assert snapshot["remaining_model_attempts"] == snapshot["remaining_tool_starts"] == 0


def test_admissions_are_idempotent_and_retry_ids_charge_even_after_failure():
    budget = RunBudget(model_attempt_limit=2, tool_start_limit=1)
    first = budget.admit_model("step/attempt-1", source="native", provider="deepseek", model="configured")
    repeated = budget.admit_model("step/attempt-1", source="native", provider="deepseek", model="configured")
    assert first.is_new and not repeated.is_new
    assert repeated.admitted_at == first.admitted_at
    with pytest.raises(FrozenInstanceError):
        first.is_new = False
    assert budget.settle_model(first.id, outcome="error")
    assert budget.admit_model("step/attempt-2", source="native").is_new
    assert budget.settle_model("step/attempt-2", outcome="aborted")
    with pytest.raises(BudgetExhausted):
        budget.admit_model("step/attempt-3", source="native")
    assert budget.admit_tool(first.id, source="native").is_new  # Separate namespaces.
    assert not budget.admit_tool(first.id, source="native").is_new
    with pytest.raises(BudgetExhausted):
        budget.admit_tool("retry", source="native")


@pytest.mark.parametrize("changed", [{"source": "ocr"}, {"provider": "other"}, {"model": "other"}, {"final": True}])
def test_model_id_cannot_silently_identify_different_work(changed):
    budget = RunBudget()
    args = {"source": "native", "provider": "provider", "model": "model", "final": False}
    budget.admit_model("id", **args)
    with pytest.raises(ValueError, match="different model request"):
        budget.admit_model("id", **{**args, **changed})
    assert budget.snapshot()["model_attempts"] == 1


def test_tool_id_cannot_change_source():
    budget = RunBudget()
    budget.admit_tool("id", source="native")
    with pytest.raises(ValueError, match="different tool request"):
        budget.admit_tool("id", source="data")


@pytest.mark.parametrize("kind", ["model", "tool"])
def test_atomic_concurrent_admissions_cannot_overrun_shared_limit(kind):
    workers, limit = 24, 7
    gate = Barrier(workers)
    budget = RunBudget(model_attempt_limit=limit, tool_start_limit=limit)
    admit = budget.admit_model if kind == "model" else budget.admit_tool

    def start(index):
        gate.wait(timeout=10)
        try:
            return admit(f"attempt-{index}", source=("native", "ocr", "data")[index % 3]).is_new
        except BudgetExhausted:
            return False

    with ThreadPoolExecutor(max_workers=workers) as pool:
        admitted = list(pool.map(start, range(workers)))
    assert sum(admitted) == limit
    assert budget.snapshot()["model_attempts" if kind == "model" else "tool_starts"] == limit


@pytest.mark.parametrize("kind", ["model", "tool"])
def test_concurrent_duplicate_attempt_id_grants_exactly_one_dispatch(kind):
    workers = 16
    gate = Barrier(workers)
    budget = RunBudget(model_attempt_limit=1, tool_start_limit=1)
    admit = budget.admit_model if kind == "model" else budget.admit_tool

    def start(_):
        gate.wait(timeout=10)
        return admit("one-operation", source="data").is_new

    with ThreadPoolExecutor(max_workers=workers) as pool:
        assert sum(pool.map(start, range(workers))) == 1


def test_final_slot_is_reserved_without_increasing_absolute_model_limit():
    budget = RunBudget(model_attempt_limit=3, tool_start_limit=1, reserve_final_slot=True)
    budget.admit_model("work-1", source="native")
    budget.admit_model("work-2", source="data")
    with pytest.raises(BudgetExhausted, match="reserved for the final response"):
        budget.admit_model("work-3", source="ocr")
    snapshot = budget.snapshot()
    assert snapshot["remaining_model_attempts"] == 1
    assert snapshot["remaining_work_model_attempts"] == 0
    assert snapshot["final_slot_reserved"]
    assert budget.admit_tool("tool", source="native").is_new
    assert budget.admit_model("final", source="final", final=True).is_new
    budget.settle_model("final", outcome="error")
    assert not budget.snapshot()["final_slot_reserved"]
    assert not budget.admit_model("final", source="final", final=True).is_new
    with pytest.raises(BudgetExhausted):
        budget.admit_model("final-retry", source="final", final=True)


def test_early_final_releases_reservation_but_its_retries_still_consume_slots():
    budget = RunBudget(model_attempt_limit=3, reserve_final_slot=True)
    budget.admit_model("final-1", source="final", final=True)
    budget.settle_model("final-1", outcome="error")
    assert budget.snapshot()["remaining_work_model_attempts"] == 2
    budget.admit_model("work", source="native")
    budget.admit_model("final-2", source="final", final=True)
    with pytest.raises(BudgetExhausted):
        budget.admit_model("final-3", source="final", final=True)


def test_final_request_retains_capacity_when_racing_concurrent_workers():
    workers = 16
    gate = Barrier(workers)
    budget = RunBudget(model_attempt_limit=4, reserve_final_slot=True)

    def start(index):
        gate.wait(timeout=10)
        try:
            return budget.admit_model(str(index), source="native", final=index == 0)
        except BudgetExhausted:
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        admissions = list(pool.map(start, range(workers)))
    assert admissions[0] is not None and admissions[0].final
    assert sum(admission is not None for admission in admissions) == 4


def test_exact_usage_matches_telemetry_and_reasoning_is_not_double_counted():
    budget = RunBudget()
    sample = usage(10, 4, reasoning_tokens=3, cache_read_tokens=2, cache_write_tokens=1, uncached_input_tokens=7)
    for source in ("native", "ocr", "data"):
        budget.admit_model(source, source=source)
        assert budget.settle_model(source, usage=sample)
    snapshot = budget.snapshot()
    assert snapshot["usage"]["exact"] == {
        "prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42,
        "reasoning_tokens": 9, "cache_read_tokens": 6, "cached_tokens": 6,
        "cache_write_tokens": 3, "uncached_input_tokens": 21,
    }
    assert snapshot["usage"]["complete"]
    assert snapshot["model_receipts"]["native"]["usage"] == normalize_usage(sample)


def test_optional_token_buckets_are_not_invented_when_a_provider_omits_them():
    budget = RunBudget()
    budget.admit_model("a", source="native")
    budget.settle_model("a", usage=usage(reasoning_tokens=2, cache_read_tokens=1))
    budget.admit_model("b", source="ocr")
    budget.settle_model("b", usage=usage())
    assert budget.snapshot()["usage"]["exact"] == {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}


@pytest.mark.parametrize("sample,status", [
    (None, "missing"), ({}, "invalid"), ({"prompt_tokens": 5}, "invalid"),
    (usage(total_tokens=100), "invalid"), (usage(estimated=True), "estimated"),
    (usage(prompt=True), "invalid"), (usage(reasoning_tokens=5), "invalid"),
])
def test_unknown_usage_stays_unknown_instead_of_zero(sample, status):
    budget = RunBudget()
    budget.admit_model("known", source="native")
    budget.settle_model("known", usage=usage())
    budget.admit_model("unknown", source="data")
    budget.settle_model("unknown", usage=sample, outcome="error")
    snapshot = budget.snapshot()
    assert snapshot["usage"] == {
        "exact": None, "reported_subtotal": normalize_usage(usage()), "complete": False,
        "admitted_attempts": 2, "reported_attempts": 1, "unsettled_attempts": 0, "unknown_settled_attempts": 1,
    }
    assert snapshot["model_receipts"]["unknown"]["usage_status"] == status
    assert snapshot["model_receipts"]["unknown"]["usage"] is None


def test_pending_attempt_prevents_an_exact_run_total():
    budget = RunBudget()
    budget.admit_model("known", source="native")
    budget.settle_model("known", usage=usage())
    budget.admit_model("pending", source="ocr")
    snapshot = budget.snapshot()
    assert snapshot["usage"]["exact"] is None
    assert snapshot["usage"]["unsettled_attempts"] == 1
    assert snapshot["model_receipts"]["pending"]["usage_status"] == "pending"


def test_settlement_deduplicates_and_allows_late_exact_receipt_after_unknown():
    budget = RunBudget()
    budget.admit_model("id", source="native")
    assert budget.settle_model("id", outcome="aborted")
    assert not budget.settle_model("id", outcome="aborted")
    assert budget.settle_model("id", usage=usage(), outcome="aborted")
    assert not budget.settle_model("id", usage=usage(total_tokens=14), outcome="aborted")
    assert not budget.settle_model("id", outcome="aborted")
    assert not budget.settle_model("id", usage=usage(estimated=True), outcome="aborted")
    assert budget.snapshot()["usage"]["exact"] == normalize_usage(usage())
    with pytest.raises(ValueError, match="Conflicting exact usage"):
        budget.settle_model("id", usage=usage(20, 4), outcome="aborted")
    with pytest.raises(ValueError, match="Conflicting terminal outcome"):
        budget.settle_model("id", usage=usage(), outcome="completed")
    with pytest.raises(KeyError):
        budget.settle_model("never-admitted", usage=usage())


def test_concurrent_duplicate_receipts_are_applied_once():
    workers = 12
    gate = Barrier(workers)
    budget = RunBudget()
    budget.admit_model("id", source="data")

    def settle(_):
        gate.wait(timeout=10)
        return budget.settle_model("id", usage=usage())

    with ThreadPoolExecutor(max_workers=workers) as pool:
        assert sum(pool.map(settle, range(workers))) == 1
    assert budget.snapshot()["usage"]["exact"]["total_tokens"] == 14


def test_absolute_deadline_does_not_extend_during_pause_and_settlement_still_works():
    clock = Clock()
    budget = RunBudget(timeout_seconds=10, clock=clock)
    budget.admit_model("before-pause", source="native")
    clock.advance(3)
    assert budget.remaining_timeout() == 7
    assert budget.remaining_timeout(2) == 2
    assert budget.remaining_timeout(20) == 7
    clock.advance(7)  # No admission during an acknowledged pause; wall time still counts.
    for operation in (lambda: budget.admit_model("after-pause", source="native"),
                      lambda: budget.admit_tool("after-pause", source="native"), budget.remaining_timeout):
        with pytest.raises(BudgetExhausted) as error:
            operation()
        assert (error.value.dimension, error.value.limit, error.value.used) == ("deadline", 10, 10)
    # An idempotent lookup is allowed, but must never authorize dispatch after expiry.
    assert not budget.admit_model("before-pause", source="native").is_new
    assert budget.settle_model("before-pause", usage=usage(), outcome="aborted")
    snapshot = budget.snapshot()
    assert snapshot["deadline"] == 110
    assert snapshot["deadline_reached"] and snapshot["remaining_seconds"] == 0
    assert snapshot["usage"]["exact"] == normalize_usage(usage())


def test_zero_limits_and_unlimited_defaults_are_explicit():
    zero = RunBudget(model_attempt_limit=0, tool_start_limit=0)
    with pytest.raises(BudgetExhausted):
        zero.admit_model("id", source="native")
    with pytest.raises(BudgetExhausted):
        zero.admit_tool("id", source="native")
    unlimited = RunBudget(reserve_final_slot=True)
    assert unlimited.remaining_timeout() is None
    assert unlimited.remaining_timeout(15) == 15
    snapshot = unlimited.snapshot()
    assert snapshot["remaining_model_attempts"] is None
    assert snapshot["remaining_work_model_attempts"] is None
    assert snapshot["remaining_tool_starts"] is None
    assert not snapshot["final_slot_reserved"]
    assert snapshot["usage"]["admitted_attempts"] == snapshot["usage"]["reported_attempts"] == 0
    assert snapshot["usage"]["exact"] is None


def test_snapshot_and_caller_usage_are_detached_from_internal_receipts():
    budget = RunBudget()
    budget.admit_model("id", source="native")
    sample = usage()
    budget.settle_model("id", usage=sample)
    sample["prompt_tokens"] = 900
    snapshot = budget.snapshot()
    snapshot["model_receipts"]["id"]["usage"]["prompt_tokens"] = 800
    snapshot["usage"]["reported_subtotal"]["prompt_tokens"] = 700
    assert budget.snapshot()["model_receipts"]["id"]["usage"]["prompt_tokens"] == 10
    assert budget.snapshot()["usage"]["exact"]["prompt_tokens"] == 10


@pytest.mark.parametrize("name", ["model_attempt_limit", "tool_start_limit"])
@pytest.mark.parametrize("limit", [True, -1, 1.0, "2", MAX_SAFE_INTEGER + 1])
def test_invalid_limits_fail_at_construction(name, limit):
    with pytest.raises(ValueError):
        RunBudget(**{name: limit})


@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf"), "5"])
def test_invalid_timeouts_fail_before_admission(value):
    with pytest.raises(ValueError):
        RunBudget(timeout_seconds=value)
    with pytest.raises(ValueError):
        RunBudget().remaining_timeout(value)


def test_final_reservation_and_identity_validation():
    with pytest.raises(ValueError):
        RunBudget(model_attempt_limit=0, reserve_final_slot=True)
    with pytest.raises(ValueError):
        RunBudget(reserve_final_slot=1)
    budget = RunBudget()
    for admit in (budget.admit_model, budget.admit_tool):
        with pytest.raises(ValueError):
            admit("", source="native")
        with pytest.raises(ValueError):
            admit("id", source=" ")
    with pytest.raises(ValueError):
        budget.admit_model("id", source="native", final=1)
    with pytest.raises(ValueError):
        budget.settle_model("id", outcome="retry")


def test_clock_validation_and_negative_origin():
    for invalid in (float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            RunBudget(clock=lambda: invalid)
    clock = Clock(-100)
    budget = RunBudget(timeout_seconds=10, clock=clock)
    assert budget.remaining_timeout() == 10
    clock.now = -101
    with pytest.raises(ValueError, match="monotonic"):
        budget.admit_tool("id", source="native")
    clock.now = -99
    assert budget.snapshot()["tool_starts"] == 0
    assert budget.remaining_timeout() == 9


def test_usage_overflow_remains_unknown_rather_than_an_unsafe_integer():
    budget = RunBudget()
    for attempt_id in ("first", "second"):
        budget.admit_model(attempt_id, source="native")
        budget.settle_model(attempt_id, usage=usage(MAX_SAFE_INTEGER, 0))
    snapshot = budget.snapshot()
    assert snapshot["usage"]["exact"] is None
    assert snapshot["usage"]["reported_subtotal"] is None
    assert snapshot["usage"]["reported_attempts"] == 2
    assert not snapshot["usage"]["complete"]
