"""Operation-specific failure policy used by the configurable native harness.

The caller supplies resolved tool names, task generations and trusted resource
revisions. Unknown outcomes still require RecoveryGuard. Read observations are
advisory, opt-in, and require a complete content fingerprint. Stable text,
publication counts, elapsed time and objective plateaus never prove completion.
The isolated controller benchmark and actual-harness fixtures are separate
experiments; neither establishes live-model task quality.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Literal, Mapping


def _json_object(value: dict) -> str:
    if not isinstance(value, dict):
        raise ValueError("Normalized arguments must be a JSON object")

    def validate(item):
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("JSON object keys must be strings")
            for child in item.values():
                validate(child)
        elif isinstance(item, list):
            for child in item:
                validate(child)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError("Arguments must contain JSON values only")
    validate(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class Operation:
    tool: str
    arguments_json: str
    task_generation: int = 0
    resource_revisions: tuple[tuple[str, str], ...] = ()
    kind: Literal["opaque", "read", "poll", "optimization", "mutation"] = "opaque"
    observe_successful_reads: bool = False

    @classmethod
    def from_call(cls, tool: str, arguments: dict, *, task_generation: int = 0,
                  resource_revisions: Mapping[str, str] | None = None,
                  kind="opaque", observe_successful_reads=False) -> "Operation":
        return cls(tool, _json_object(arguments), task_generation,
                   tuple(sorted((resource_revisions or {}).items())), kind, observe_successful_reads)

    def __post_init__(self):
        if not isinstance(self.tool, str) or not self.tool.strip():
            raise ValueError("A canonical tool name is required")
        if type(self.task_generation) is not int or self.task_generation < 0:
            raise ValueError("Task generation must be a nonnegative integer")
        if self.kind not in {"opaque", "read", "poll", "optimization", "mutation"}:
            raise ValueError("Unsupported operation kind")
        if type(self.observe_successful_reads) is not bool:
            raise ValueError("Read observation must be explicitly enabled")
        if self.observe_successful_reads and self.kind != "read":
            raise ValueError("Only declared reads support successful-read observation")
        if not isinstance(self.resource_revisions, tuple) or any(
            not isinstance(pair, tuple) or len(pair) != 2
            or any(not isinstance(value, str) or not value for value in pair)
            for pair in self.resource_revisions
        ) or len({name for name, _ in self.resource_revisions}) != len(self.resource_revisions):
            raise ValueError("Resource revisions must be unique string pairs")
        # Reject mutable/noncanonical direct construction as well as factory misuse.
        if _json_object(json.loads(self.arguments_json)) != self.arguments_json:
            raise ValueError("Arguments must use the canonical JSON encoding")

    @property
    def key(self) -> tuple:
        return self.tool, self.arguments_json, self.task_generation, tuple(sorted(self.resource_revisions))


@dataclass(frozen=True)
class FullReadEvidence:
    """Produced by a trusted reader, never parsed from assistant/tool prose."""
    content_sha256: str
    complete: bool = True

    def __post_init__(self):
        if not isinstance(self.content_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.content_sha256):
            raise ValueError("Read evidence requires a SHA-256 content fingerprint")
        if type(self.complete) is not bool:
            raise ValueError("Read completeness must be explicit")


@dataclass(frozen=True)
class TypedProgress:
    """A runner-observed counter/token, not a claim of task correctness."""
    scope: str
    completed: int
    state_token: str = ""

    def __post_init__(self):
        if not isinstance(self.scope, str) or not self.scope:
            raise ValueError("Progress requires a job/search identity")
        if type(self.completed) is not int or self.completed < 0:
            raise ValueError("Progress must be a nonnegative observed count")
        if not isinstance(self.state_token, str):
            raise ValueError("Progress tokens must be strings")


@dataclass(frozen=True)
class Deadline:
    """An existing caller-owned monotonic deadline, not renewed by observation."""
    at: float

    def __post_init__(self):
        if type(self.at) not in (int, float) or not math.isfinite(self.at):
            raise ValueError("Deadline must be finite")

    def expired(self, now: float) -> bool:
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError("A finite monotonic clock reading is required")
        return now >= self.at


@dataclass(frozen=True)
class Outcome:
    status: Literal["success", "failure", "unknown", "not_started"]
    error: str = ""
    error_name: str = ""
    output: str = ""
    managed_retry: bool = False
    read: FullReadEvidence | None = None
    progress: TypedProgress | None = None

    def __post_init__(self):
        if self.status not in {"success", "failure", "unknown", "not_started"}:
            raise ValueError("Unsupported execution outcome")
        if any(not isinstance(value, str) for value in (self.error, self.error_name, self.output)):
            raise ValueError("Outcome text fields must be strings")
        if type(self.managed_retry) is not bool:
            raise ValueError("Managed retry must be a boolean")
        if self.read is not None and not isinstance(self.read, FullReadEvidence):
            raise ValueError("Read evidence must be typed")
        if self.progress is not None and not isinstance(self.progress, TypedProgress):
            raise ValueError("Progress evidence must be typed")


def failure_fingerprint(outcome: Outcome) -> str:
    text = outcome.error.strip() or outcome.output.strip()
    first_line = text.splitlines()[0].strip() if text else "unknown"
    name = outcome.error_name or first_line.split(":", 1)[0].strip()
    return f"{name}|{first_line[:240]}"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = "allowed"
    review: bool = False


@dataclass(frozen=True)
class Observation:
    kind: str
    count: int = 0


class OperationGuard:
    """Per-run exact-failure admission plus advisory complete-read observation.

    A success or different error ends the operation's identical failure streak.
    Calls to unrelated operations do not erase it. Replayed observation IDs are
    idempotent. This object does not retry, run tools, finalize a task, infer
    correctness, impose a new global budget, or replace RecoveryGuard.
    """
    def __init__(self, failure_limit: int = 3, read_review_threshold: int = 3):
        if any(type(value) is not int or value < 1 for value in (failure_limit, read_review_threshold)):
            raise ValueError("Thresholds must be positive integers")
        self.failure_limit = failure_limit
        self.read_review_threshold = read_review_threshold
        self._failures: dict[tuple, tuple[str, int]] = {}
        self._reads: dict[tuple, tuple[str, int]] = {}
        self._progress: dict[tuple, TypedProgress] = {}
        self._seen: dict[str, tuple[Operation, Outcome, Observation]] = {}

    def admit(self, operation: Operation, *, recovery_blocked: bool = False,
              deadline: Deadline | None = None, now: float | None = None) -> Decision:
        # Stronger uncertainty and deadline gates take precedence over heuristics.
        if recovery_blocked:
            return Decision(False, "recovery_required")
        if deadline is not None:
            if now is None:
                raise ValueError("Deadline admission requires the current monotonic time")
            if deadline.expired(now):
                return Decision(False, "deadline_reached")
        if self._failures.get(operation.key, ("", 0))[1] >= self.failure_limit:
            return Decision(False, "repeated_operation_failure")
        review = operation.observe_successful_reads and self._reads.get(operation.key, ("", 0))[1] >= self.read_review_threshold
        return Decision(True, "unchanged_full_read" if review else "allowed", review)

    def observe(self, operation: Operation, outcome: Outcome, *, observation_id: str) -> Observation:
        if not isinstance(observation_id, str) or not observation_id:
            raise ValueError("A durable observation identity is required")
        if observation_id in self._seen:
            prior_operation, prior_outcome, observation = self._seen[observation_id]
            if prior_operation != operation or prior_outcome != outcome:
                raise ValueError("A replayed observation cannot change its operation or outcome")
            return observation
        key = operation.key
        if outcome.status == "not_started" or outcome.managed_retry:
            observation = Observation("ignored")
        elif outcome.status == "unknown":
            self._failures.pop(key, None)
            self._reads.pop(key, None)
            observation = Observation("recovery_required")
        elif outcome.status == "failure":
            fingerprint = failure_fingerprint(outcome)
            previous, count = self._failures.get(key, ("", 0))
            count = count + 1 if previous == fingerprint else 1
            self._failures[key] = fingerprint, count
            self._reads.pop(key, None)
            observation = Observation("failure", count)
        else:
            self._failures.pop(key, None)
            if operation.observe_successful_reads and outcome.read is not None and outcome.read.complete:
                previous, count = self._reads.get(key, ("", 0))
                count = count + 1 if previous == outcome.read.content_sha256 else 1
                self._reads[key] = outcome.read.content_sha256, count
                observation = Observation("unchanged_full_read" if count > 1 else "full_read", count)
            elif outcome.progress is not None and operation.kind in {"poll", "optimization"}:
                # Different job scopes cannot erase observations of another job.
                progress_key = (*key, outcome.progress.scope)
                previous = self._progress.get(progress_key)
                advanced = previous is not None and outcome.progress.completed > previous.completed
                changed = previous is not None and outcome.progress.state_token != previous.state_token
                regressed = previous is not None and outcome.progress.completed < previous.completed
                if not regressed:
                    self._progress[progress_key] = outcome.progress
                observation = Observation("progress_observed" if previous is None else "progress_regressed" if regressed else "progress_advanced" if advanced else "state_changed" if changed else "progress_unchanged", outcome.progress.completed)
                self._reads.pop(key, None)
            else:
                self._reads.pop(key, None)
                observation = Observation("unclassified_success")
        self._seen[observation_id] = operation, outcome, observation
        return observation
