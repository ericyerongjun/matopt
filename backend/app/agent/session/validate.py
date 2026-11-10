"""Prefix validation before a candidate can become durable or model-visible."""

from __future__ import annotations

from dataclasses import dataclass, field

from .derive import apply_surface_event, fold_surface
from .events import END_REASONS, KNOWN_TYPES, is_seq, snapshot_json, validate_envelope


@dataclass
class LifecycleState:
    """Where a validated prefix stands: its open turn, step and unanswered calls."""

    turn: int | None = None
    step: int | None = None
    last_turn: int = 0
    last_step: int = 0
    pending: dict[str, dict] = field(default_factory=dict)

    def copy(self) -> "LifecycleState":
        return LifecycleState(self.turn, self.step, self.last_turn, self.last_step,
                              {call_id: dict(call) for call_id, call in self.pending.items()})


def advance_lifecycle(state: LifecycleState, event: dict) -> None:
    """Apply one event whose envelope is already valid; ``state`` is mutated."""
    kind, data = event["type"], event["data"]
    if kind not in KNOWN_TYPES:
        return
    if kind == "ui/message":
        if (not isinstance(data.get("message_id"), str) or not data["message_id"]
                or data.get("role") not in {"user", "assistant"} or not isinstance(data.get("content"), dict)):
            raise ValueError("UI view events require message identity, role and JSON content")
    if kind in {"assistant/message", "assistant/attempt"} and not data.get("legacy"):
        from .stream import expand_stream
        if not isinstance(data.get("stream"), list):
            raise ValueError("Assistant attempts must contain a timed stream")
        expand_stream(data["stream"])
    pending = state.pending
    if kind == "turn/start":
        if state.turn is not None or not is_seq(data.get("turn")) or data["turn"] <= state.last_turn:
            raise ValueError("Turn starts must be increasing and cannot overlap")
        state.turn = state.last_turn = data["turn"]
        state.last_step = 0
    elif kind == "turn/end":
        reason = data.get("reason")
        if isinstance(reason, dict):
            reason = reason.get("kind")
        if state.turn is None or data.get("turn") != state.turn or state.step is not None or reason not in END_REASONS:
            raise ValueError("Turn end must close its open turn after its step")
        state.turn = None
    elif kind == "step/start":
        if (state.turn is None or data.get("turn") != state.turn or state.step is not None
                or not is_seq(data.get("step")) or data["step"] <= state.last_step):
            raise ValueError("Step starts must be increasing inside their open turn")
        state.step = state.last_step = data["step"]
        pending.clear()
    elif kind == "step/end":
        if state.turn != data.get("turn") or state.step is None or state.step != data.get("step") or pending:
            raise ValueError("Step end must close its open step with no unanswered calls")
        state.step = None
    elif kind in {"assistant/message", "assistant/attempt", "tool/call", "tool/result", "request/header"}:
        # Imported legacy surfaces are explicitly marked. They never claim
        # provider usage or lifecycle precision absent from the old store.
        if data.get("legacy") is True:
            return
        turn, step = state.turn, state.step
        if turn is None or step is None or data.get("turn") != turn or data.get("step") != step:
            raise ValueError(f"{kind} must belong to its current turn and step")
        if kind == "assistant/message":
            for call in data["message"].get("tool_calls") or []:
                call_id = call.get("id")
                if not isinstance(call_id, str) or not call_id or call_id in pending:
                    raise ValueError("Assistant call IDs must be unique in their step")
                function = call.get("function") or {}
                pending[call_id] = {"turn": turn, "step": step, "name": function.get("name"), "started": False}
        elif kind == "tool/call":
            call = pending.get(data.get("callId"))
            if call is None or call["started"] or call["name"] != data.get("name") or not isinstance(data.get("arguments"), str):
                raise ValueError("Tool call must uniquely start a pending assistant call with raw JSON arguments")
            call["started"] = True
        elif kind == "tool/result":
            call_id = data.get("callId")
            call = pending.get(call_id)
            recovered = (data.get("meta") or {}).get("recovery_code") == "TOOL_NOT_STARTED"
            if call is None or (not call["started"] and not recovered):
                raise ValueError("Tool result must settle a pending started call")
            if data["message"].get("tool_call_id") != call_id:
                raise ValueError("Tool result message must identify the same call")
            del pending[call_id]
        elif kind == "request/header":
            header = data.get("header")
            if not isinstance(header, dict) or not isinstance(header.get("config"), dict) or "system" in header:
                raise ValueError("Request header requires config; system belongs to a surface message")
            if "tools" in header and (not isinstance(header["tools"], list) or not header["tools"]):
                raise ValueError("Request header must omit empty tools")


def lifecycle(events: list[dict]) -> tuple[int | None, int | None, dict[str, dict]]:
    state = LifecycleState()
    for index, event in enumerate(events):
        validate_envelope(event, index)
        advance_lifecycle(state, event)
    return state.turn, state.step, state.pending


def validate_events(events: list[dict]) -> None:
    snapshot_json(events)
    fold_surface(events)
    lifecycle(events)


class IncrementalValidator:
    """The whole-prefix checks, done once per event.

    Accepts exactly the prefixes ``validate_events`` accepts, but a new batch
    costs only that batch. A rejected batch leaves the state untouched.
    """

    def __init__(self) -> None:
        self.count = 0
        self.nodes: list[int] = []
        self.state = LifecycleState()

    def extend(self, batch: list[dict]) -> None:
        nodes, state, index = list(self.nodes), self.state.copy(), self.count
        for event in batch:
            apply_surface_event(nodes, event, index)
            advance_lifecycle(state, event)
            index += 1
        self.nodes, self.state, self.count = nodes, state, index
