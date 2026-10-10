"""Unpaid policy-unit and deterministic controller tests; no harness dispatch."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys

import pytest

from app.agent.loop_policy import Deadline, FullReadEvidence, Operation, OperationGuard, Outcome, TypedProgress

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/benchmark_loop_policy.py"
spec = importlib.util.spec_from_file_location("loop_policy_benchmark", SCRIPT)
benchmark = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = benchmark
spec.loader.exec_module(benchmark)

BAD = Outcome("failure", error="NameError: missing input")
GOOD = Outcome("success", output="42")


def operation(**kwargs):
    return Operation.from_call("python", {"code": "calculate(x)"}, **kwargs)


def observe_n(guard, op, result=BAD, n=3, prefix="observation"):
    for index in range(n):
        guard.observe(op, result, observation_id=f"{prefix}:{index}")


def test_exact_failure_blocks_only_that_call_and_preserves_corrected_arguments():
    guard = OperationGuard()
    op = operation()
    observe_n(guard, op)
    assert guard.admit(op).reason == "repeated_operation_failure"
    corrected = Operation.from_call("python", {"code": "calculate(6*7)"})
    assert guard.admit(corrected).allowed
    assert guard.admit(Operation.from_call("read_file", {"path": "x"})).allowed


def test_identity_snapshots_normalized_json_and_distinguishes_typed_values():
    args = {"b": [1, {"x": True}], "a": "text"}
    first = Operation.from_call("python", args)
    second = Operation.from_call("python", {"a": "text", "b": [1, {"x": True}]})
    assert first.key == second.key
    args["b"][1]["x"] = False
    assert first.key == second.key
    assert Operation.from_call("python", {"x": True}).key != Operation.from_call("python", {"x": 1}).key
    assert Operation.from_call("python", {"x": 1}).key != Operation.from_call("python", {"x": 1.0}).key


def test_generation_and_explicit_resource_revisions_isolate_prior_failures():
    guard = OperationGuard()
    first = operation(resource_revisions={"kernel": "r1", "data": "d1"})
    observe_n(guard, first)
    assert not guard.admit(operation(resource_revisions={"data": "d1", "kernel": "r1"})).allowed
    assert guard.admit(operation(resource_revisions={"kernel": "r2", "data": "d1"})).allowed
    assert guard.admit(operation(task_generation=1, resource_revisions={"kernel": "r1", "data": "d1"})).allowed
    assert not guard.admit(first).allowed


@pytest.mark.parametrize("args", [{"x": float("nan")}, {"x": float("inf")}, {1: "value"}, {"x": (1, 2)}, {"x": object()}])
def test_non_json_identity_is_rejected(args):
    with pytest.raises(ValueError):
        Operation.from_call("python", args)


@pytest.mark.parametrize("kwargs", [{"task_generation": True}, {"task_generation": -1}, {"kind": "guess"},
    {"observe_successful_reads": True}, {"resource_revisions": {"kernel": ""}}])
def test_untrusted_or_incomplete_operation_declarations_are_rejected(kwargs):
    with pytest.raises(ValueError):
        operation(**kwargs)


def test_success_or_changed_failure_resets_only_the_exact_failure_streak():
    guard = OperationGuard()
    op = operation()
    observe_n(guard, op, n=2)
    guard.observe(Operation.from_call("python", {"code": "other"}), GOOD, observation_id="other")
    assert guard.observe(op, BAD, observation_id="third").count == 3
    # An execution receipt, not another tool's success, can settle this operation.
    guard.observe(op, GOOD, observation_id="known-success")
    assert guard.admit(op).allowed
    assert guard.observe(op, BAD, observation_id="new-failure").count == 1
    changed = Outcome("failure", error="ValueError: different error")
    assert guard.observe(op, changed, observation_id="different").count == 1
    assert guard.admit(op).allowed


def test_ignored_results_do_not_count_and_unknown_outcomes_defer_to_recovery():
    guard = OperationGuard()
    op = operation()
    observe_n(guard, op, Outcome("not_started", error="disabled"), prefix="skipped")
    observe_n(guard, op, Outcome("failure", error="managed repair", managed_retry=True), prefix="managed")
    assert guard.admit(op).allowed
    observe_n(guard, op, n=2)
    assert guard.observe(op, Outcome("unknown"), observation_id="unknown").kind == "recovery_required"
    # RecoveryGuard supplies this gate. Changing generation must not override it.
    assert guard.admit(op, recovery_blocked=True).reason == "recovery_required"
    assert not guard.admit(operation(task_generation=9), recovery_blocked=True).allowed


def test_observation_replay_is_idempotent_but_conflicting_replay_is_rejected():
    guard = OperationGuard()
    op = operation()
    for _ in range(10):
        assert guard.observe(op, BAD, observation_id="durable-1").count == 1
    assert guard.admit(op).allowed
    for changed_op, changed_result in [(op, GOOD), (operation(task_generation=1), BAD)]:
        with pytest.raises(ValueError, match="replayed observation"):
            guard.observe(changed_op, changed_result, observation_id="durable-1")


def full_read(text="content", *, complete=True):
    return Outcome("success", output="summary", read=FullReadEvidence(hashlib.sha256(text.encode()).hexdigest(), complete))


def test_full_read_review_is_opt_in_advisory_and_clears_when_content_changes():
    guard = OperationGuard()
    op = Operation.from_call("read_file", {"path": "x"}, kind="read", observe_successful_reads=True)
    observe_n(guard, op, full_read())
    decision = guard.admit(op)
    assert decision.allowed and decision.review and decision.reason == "unchanged_full_read"
    disabled = Operation.from_call("read_file", {"path": "x"}, kind="read")
    assert not guard.admit(disabled).review
    guard.observe(op, full_read("new content"), observation_id="changed")
    assert not guard.admit(op).review
    observe_n(guard, op, full_read("new content"), prefix="again")
    assert guard.admit(op).review
    guard.observe(op, full_read("partial", complete=False), observation_id="partial")
    assert not guard.admit(op).review


@pytest.mark.parametrize("result", [GOOD, full_read(complete=False)])
def test_display_text_or_partial_read_never_counts_as_full_observation(result):
    guard = OperationGuard()
    op = Operation.from_call("read_file", {"path": "x"}, kind="read", observe_successful_reads=True)
    observe_n(guard, op, result, n=5)
    assert guard.admit(op).allowed and not guard.admit(op).review
    opaque = operation()
    observe_n(guard, opaque, full_read(), n=5, prefix="opaque")
    assert not guard.admit(opaque).review


def test_poll_and_optimization_use_observed_progress_without_plateau_or_correctness_inference():
    guard = OperationGuard()
    poll = Operation.from_call("status", {"job": "1"}, kind="poll")
    for index in range(5):
        observation = guard.observe(poll, Outcome("success", output="pending", progress=TypedProgress("job-1", 0, "pending")), observation_id=f"poll-{index}")
        assert observation.kind == ("progress_observed" if index == 0 else "progress_unchanged")
        assert guard.admit(poll, deadline=Deadline(10), now=index).allowed
    assert guard.admit(poll, deadline=Deadline(10), now=10).reason == "deadline_reached"
    optimize = Operation.from_call("optimize", {"job": "1"}, kind="optimization")
    for index in range(5):
        result = Outcome("success", output="best=0.8", progress=TypedProgress("search", index, f"candidate-{index}"))
        assert guard.observe(optimize, result, observation_id=f"opt-{index}").kind == ("progress_observed" if index == 0 else "progress_advanced")
        assert guard.admit(optimize).allowed
    regressed = Outcome("success", progress=TypedProgress("search", 2, "old"))
    assert guard.observe(optimize, regressed, observation_id="regressed").kind == "progress_regressed"
    unchanged = Outcome("success", progress=TypedProgress("search", 4, "candidate-4"))
    assert guard.observe(optimize, unchanged, observation_id="still-four").kind == "progress_unchanged"
    assert guard.admit(optimize).allowed


def test_deadline_and_recovery_precedence_require_real_typed_time():
    guard = OperationGuard()
    op = operation()
    observe_n(guard, op)
    assert guard.admit(op, deadline=Deadline(5), now=5).reason == "deadline_reached"
    assert guard.admit(op, recovery_blocked=True, deadline=Deadline(5), now=6).reason == "recovery_required"
    with pytest.raises(ValueError):
        guard.admit(op, deadline=Deadline(5))
    with pytest.raises(ValueError):
        Deadline(float("nan"))
    with pytest.raises(ValueError):
        Deadline(5).expired(True)
    with pytest.raises(ValueError):
        Outcome("success", progress={"completed": 5})
    with pytest.raises(ValueError):
        FullReadEvidence("not a complete hash")


def test_frozen_baseline_matches_current_source_and_native_failure_counter():
    from app.agent.events import ToolResult
    from app.agent.harness import TurnState, _record_failures
    from app.agent.tools.base import Tool, ToolRegistry
    from app.agent.tools.router import ToolRouter
    from app.llm.base import ToolCallSpec
    class StubTool(Tool):
        name = "python"
        async def run(self, ctx, **kwargs):
            raise AssertionError("This comparison never executes tools")
    assert benchmark.baseline_reference()["source_matches"]
    frozen = benchmark.FrozenWholeToolBaseline()
    actual = TurnState()
    registry, router = ToolRegistry([StubTool()]), ToolRouter()
    op = operation()
    calls = [ToolCallSpec(id="call", name="python", arguments="{}")]
    results = [BAD, GOOD, Outcome("failure", error="NameError: missing input\nnew details"),
               Outcome("failure", error="managed", managed_retry=True), BAD]
    for outcome in results:
        frozen.observe(op, outcome)
        _record_failures(calls, [ToolResult(ok=outcome.status == "success", error=outcome.error,
            output=outcome.output, meta={"error_name": outcome.error_name, "ui_repair_managed": outcome.managed_retry})], actual, registry, router)
        assert frozen.blocked == actual.blocked_tools
        assert dict(frozen.counts) == actual.failure_counts


def test_controller_comparison_covers_false_stops_without_claiming_read_savings():
    report = benchmark.benchmark(3)
    assert report["fixture_count"] == 18
    assert report["summary"]["candidate"]["oracle_met"] == 54
    assert report["summary"]["candidate"]["false_blocks"] == 0
    assert report["summary"]["candidate"]["missed_blocks"] == 0
    assert report["summary"]["baseline"]["false_blocks"] > 0
    for run in report["runs"]:
        if run["fixture"] == "exact_failure":
            assert run["executed"] == 3
        if run["fixture"] == "unchanged_full_read":
            assert run["executed"] == 6
            assert run["review_admissions"] == (3 if run["variant"] == "candidate" else 0)
        if run["family"] == "unknown_outcome":
            assert run["effects"] == 1
    for variant in ("baseline", "candidate"):
        runs = [run for run in report["runs"] if run["variant"] == variant and run["fixture"] == "parallel_sibling"]
        assert len({(run["executed"], run["false_blocks"]) for run in runs}) == 1
    # Keep the delivered result deterministic for the same fixtures and seed.
    assert benchmark.benchmark(3) == report
