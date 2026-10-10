#!/usr/bin/env python3
"""Offline controller simulation. No providers, tool bodies, network or real tasks.

The frozen baseline is the current canonical-tool failure counter/block rule,
not a simulated LLM or a claim about the whole production harness. Explicit
admission oracles and outcomes are hand-authored. Poll deadlines, operation caps
and uncertainty gates apply identically to both policies.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import platform
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.agent.loop_policy import Deadline, Decision, FullReadEvidence, Operation, OperationGuard, Outcome, TypedProgress

BASELINE_SOURCE_SHA256 = "ed72868e25192508e66535109fda53f664aee846f19f8a67c8bfc46dc799953b"


def baseline_reference():
    source = ROOT / "backend/app/agent/harness.py"
    text = source.read_text()
    nodes = ast.parse(text).body
    functions = [node for node in nodes if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name in {"_failure_fingerprint", "_record_failures", "_resolved_name"}]
    # Hash source segments rather than AST dumps, whose fields vary by Python.
    digest = hashlib.sha256("\n".join(ast.get_source_segment(text, node) for node in functions).encode()).hexdigest()
    repeat_limit = next(ast.literal_eval(node.value) for node in nodes if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == "REPEAT_LIMIT" for target in node.targets))
    return {"file": "backend/app/agent/harness.py", "functions": [node.name for node in functions],
            "frozen_source_sha256": BASELINE_SOURCE_SHA256, "current_source_sha256": digest,
            "source_matches": digest == BASELINE_SOURCE_SHA256 and repeat_limit == 3, "repeat_limit": repeat_limit}


class FrozenWholeToolBaseline:
    """Frozen _failure_fingerprint + _record_failures semantics, resolved names.

    Success never resets cumulative counts; arguments/generation/revision are
    absent from identity. A matching error occurring three times blocks the tool.
    Blocked-repeat results are not observations in this simulator.
    """
    def __init__(self):
        self.counts = Counter()
        self.blocked = set()

    def admit(self, operation):
        return Decision(operation.tool not in self.blocked, "whole_tool_failure" if operation.tool in self.blocked else "allowed")

    def observe(self, operation, outcome, **_):
        if outcome.status == "success" or outcome.managed_retry:
            return
        error = outcome.error.strip() or outcome.output.strip()
        first_line = error.splitlines()[0].strip() if error else "unknown"
        error_name = outcome.error_name
        if not error_name and first_line:
            error_name = first_line.split(":", 1)[0].strip()
        fingerprint = f"{operation.tool}|{error_name}|{first_line[:240]}"
        self.counts[fingerprint] += 1
        if self.counts[fingerprint] >= 3:
            self.blocked.add(operation.tool)


@dataclass(frozen=True)
class Attempt:
    id: str
    operation: Operation
    outcome: Outcome
    allow: bool = True
    batch: int = 0
    at: float = 0
    deadline: Deadline | None = None
    recovery_blocked: bool = False
    effect_delta: int = 0


@dataclass(frozen=True)
class Fixture:
    id: str
    family: str
    description: str
    attempts: tuple[Attempt, ...]
    body_cap: int = 12
    expected_effects: int = 0


FAILURE = Outcome("failure", error="NameError: missing input")
SUCCESS = Outcome("success", output="42")


def fingerprint(text):
    return hashlib.sha256(text.encode()).hexdigest()


def operation(**kwargs):
    return Operation.from_call("python", {"code": "calculate(x)"}, **kwargs)


def fixtures():
    cases = []
    op = operation()

    def case(identifier, family, description, rows, **kwargs):
        attempts = tuple(Attempt(f"{identifier}:{index}", *row, batch=index) for index, row in enumerate(rows))
        cases.append(Fixture(identifier, family, description, attempts, **kwargs))

    case("exact_failure", "known_failures", "A deterministic error repeats for one unchanged operation.",
         [(op, FAILURE, index < 3) for index in range(8)])
    case("corrected_arguments", "known_failures", "After three failures, corrected code is a different operation.",
         [(op, FAILURE)] * 3 + [(Operation.from_call("python", {"code": "calculate(6*7)"}), SUCCESS)])
    case("different_operations", "known_failures", "Three different bad inputs must not block a fourth valid input.",
         [(Operation.from_call("python", {"code": f"calculate({name})"}), FAILURE) for name in ["a", "b", "c"]]
         + [(Operation.from_call("python", {"code": "calculate(42)"}), SUCCESS)])
    case("resource_revision", "generation", "An explicitly changed kernel revision makes the same code eligible again.",
         [(operation(resource_revisions={"kernel": "r1"}), FAILURE)] * 3
         + [(operation(resource_revisions={"kernel": "r2"}), SUCCESS)])
    case("task_generation", "generation", "A caller-owned task generation changes the operation identity.",
         [(operation(task_generation=0), FAILURE)] * 3 + [(operation(task_generation=1), SUCCESS)])
    case("intermittent_success", "known_failures", "A known success ends a consecutive identical-error streak.",
         [(op, FAILURE), (op, FAILURE), (op, SUCCESS)] * 2)
    read = Operation.from_call("read_file", {"path": "data.csv"}, kind="read", observe_successful_reads=True)
    full = Outcome("success", output="same summary", read=FullReadEvidence(fingerprint("complete data")))
    case("unchanged_full_read", "read_observation", "Three full fingerprints enable advisory review; reads remain allowed.", [(read, full)] * 6)
    no_opt = Operation.from_call("read_file", {"path": "data.csv"}, kind="read")
    case("read_not_opted_in", "read_observation", "Complete fingerprints without explicit opt-in do not trigger review.", [(no_opt, full)] * 6)
    partial = Outcome("success", output="same summary", read=FullReadEvidence(fingerprint("partial data"), complete=False))
    case("partial_read", "read_observation", "Repeated partial content is insufficient evidence for review.", [(read, partial)] * 6)
    case("opaque_stdout", "opaque", "Opaque code may advance hidden state despite identical stdout.", [(op, SUCCESS)] * 6)
    case("changed_full_read", "read_observation", "Equal display text with different complete fingerprints is new evidence.",
         [(read, Outcome("success", output="same summary", read=FullReadEvidence(fingerprint(str(i))))) for i in range(6)])
    poll = Operation.from_call("job_status", {"id": "job-1"}, kind="poll")
    polls = tuple(Attempt(f"poll:{i}", poll, Outcome("success", output="pending" if i < 4 else "done",
        progress=TypedProgress("job-1", 0 if i < 4 else 1, "pending" if i < 4 else "done")), batch=i, at=i, deadline=Deadline(5)) for i in range(5))
    cases.append(Fixture("poll_completes", "polling", "Four identical pending results followed by completion within deadline.", polls))
    polls = tuple(Attempt(f"deadline:{i}", poll, Outcome("success", output="pending", progress=TypedProgress("job-1", 0, "pending")),
        allow=i < 4, batch=i, at=i, deadline=Deadline(4)) for i in range(6))
    cases.append(Fixture("poll_deadline", "polling", "A caller-specified unchanged-job deadline stops both policies.", polls))
    optimize = Operation.from_call("search_step", {"id": "search-1"}, kind="optimization")
    case("optimization_plateau", "optimization", "The best score stays flat while measured evaluations advance, then improves.",
         [(optimize, Outcome("success", output="best=0.8" if i < 5 else "best=0.9",
             progress=TypedProgress("search-1", i + 1, f"candidate-{i}"))) for i in range(6)])
    case("optimization_cap", "optimization", "No measured progress alone proves neither failure nor convergence; shared cap applies.",
         [(optimize, Outcome("success", output="best=0.8", progress=TypedProgress("search-1", 0)), i < 4) for i in range(6)], body_cap=4)
    mutation = Operation.from_call("send_mock", {"id": "request-1"}, kind="mutation")
    inspect = Operation.from_call("inspect_mock_receipt", {"id": "request-1"}, kind="read")
    for committed in (True, False):
        name = "unknown_committed" if committed else "unknown_not_committed"
        rows = [Attempt(f"{name}:0", mutation, Outcome("unknown", error="TOOL_OUTCOME_UNKNOWN"), batch=0, effect_delta=int(committed)),
                Attempt(f"{name}:1", mutation, SUCCESS, allow=False, batch=1, recovery_blocked=True, effect_delta=1),
                Attempt(f"{name}:2", inspect, Outcome("success", output="committed" if committed else "not committed"), batch=2)]
        if not committed:
            # The fixture supplies a one-attempt authorization after inspection.
            # This is an external gate, not authority inferred by OperationGuard.
            rows.append(Attempt(f"{name}:3", mutation, SUCCESS, batch=3, effect_delta=1))
        cases.append(Fixture(name, "unknown_outcome", "Common recovery gate prevents blind replay; inspect first, with explicit one-attempt grant only if absent.", tuple(rows), expected_effects=1))
    healthy = Operation.from_call("python", {"code": "calculate(42)"})
    parallel = tuple(attempt for batch in range(4) for attempt in (
        Attempt(f"parallel:bad:{batch}", op, FAILURE, allow=batch < 3, batch=batch),
        Attempt(f"parallel:good:{batch}", healthy, SUCCESS, batch=batch)))
    cases.append(Fixture("parallel_sibling", "parallel_order", "Batch admission precedes shuffled completions; a progressing sibling remains eligible.", parallel))
    return cases


def simulate(fixture: Fixture, variant: str, schedule_seed: int):
    policy = OperationGuard() if variant == "candidate" else FrozenWholeToolBaseline()
    rng = random.Random(schedule_seed)
    executed = effects = false_blocks = missed_blocks = reviews = 0
    trace = []
    for batch in sorted({attempt.batch for attempt in fixture.attempts}):
        pending = []
        # A batch observes a single admission snapshot. Completion order does not
        # retroactively alter siblings already admitted by the controller.
        for attempt in [value for value in fixture.attempts if value.batch == batch]:
            if attempt.recovery_blocked:
                decision = Decision(False, "recovery_required")
            elif attempt.deadline is not None and attempt.deadline.expired(attempt.at):
                decision = Decision(False, "deadline_reached")
            elif executed + len(pending) >= fixture.body_cap:
                decision = Decision(False, "shared_operation_cap")
            else:
                decision = policy.admit(attempt.operation)
            false_blocks += int(attempt.allow and not decision.allowed)
            missed_blocks += int(not attempt.allow and decision.allowed)
            reviews += int(decision.review)
            entry = {"id": attempt.id, "tool": attempt.operation.tool, "arguments": json.loads(attempt.operation.arguments_json),
                     "task_generation": attempt.operation.task_generation, "resource_revisions": dict(attempt.operation.resource_revisions),
                     "batch": batch, "clock": attempt.at, "expected_allow": attempt.allow,
                     "allowed": decision.allowed, "reason": decision.reason, "review": decision.review,
                     "scripted_outcome": attempt.outcome.status}
            trace.append(entry)
            if decision.allowed:
                pending.append((attempt, entry))
        rng.shuffle(pending)
        for attempt, entry in pending:
            observed = policy.observe(attempt.operation, attempt.outcome, observation_id=attempt.id)
            executed += 1
            effects += attempt.effect_delta
            entry["completion_order"] = executed
            if observed is not None:
                entry["observation"] = {"kind": observed.kind, "count": observed.count}
    return {"fixture": fixture.id, "family": fixture.family, "variant": variant, "schedule_seed": schedule_seed,
            "requested": len(fixture.attempts), "executed": executed, "denied": len(fixture.attempts) - executed,
            "false_blocks": false_blocks, "missed_blocks": missed_blocks, "review_admissions": reviews,
            "effects": effects, "expected_effects": fixture.expected_effects,
            "oracle_met": not false_blocks and not missed_blocks and effects == fixture.expected_effects,
            "trace": trace}


def benchmark(repeats=3):
    reference = baseline_reference()
    if not reference["source_matches"]:
        raise ValueError("Current failure policy differs from the frozen baseline; review and explicitly refresh the reference before comparison")
    selected = fixtures()
    runs = [simulate(fixture, variant, seed) for fixture in selected for seed in range(repeats) for variant in ("baseline", "candidate")]
    summary = {}
    for variant in ("baseline", "candidate"):
        group = [run for run in runs if run["variant"] == variant]
        summary[variant] = {"runs": len(group), "oracle_met": sum(run["oracle_met"] for run in group),
            **{key: sum(run[key] for run in group) for key in ("requested", "executed", "denied", "false_blocks", "missed_blocks", "review_admissions")}}
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in (
        ROOT / "backend/app/agent/loop_policy.py", ROOT / "scripts/benchmark_loop_policy.py")}
    return {"schema_version": 1, "experiment": "operation-policy-controller-simulation",
            "limitations": ["Hand-authored deterministic admission oracles; not actual harness or provider integration.",
                "Shared uncertainty/deadline/cap gates are fixture inputs; this does not validate RecoveryGuard.",
                "Successful-read review is advisory and does not reduce executions without a future controller decision.",
                "No model quality, token cost, real task latency, final-writer fidelity or production success claims."],
            "python": platform.python_version(), "baseline_reference": reference, "source_sha256": hashes,
            "fixture_count": len(selected), "repeats": repeats, "summary": summary,
            "fixtures": [{"id": fixture.id, "family": fixture.family, "description": fixture.description,
                          "body_cap": fixture.body_cap, "expected_effects": fixture.expected_effects} for fixture in selected], "runs": runs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 100:
        parser.error("--repeats must be between 1 and 100")
    report = benchmark(args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"fixture_count": report["fixture_count"], "repeats": report["repeats"], "summary": report["summary"], "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
