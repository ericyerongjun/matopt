"""Prefix validation before a candidate can become durable or model-visible."""

from __future__ import annotations

from .derive import fold_surface
from .events import END_REASONS, KNOWN_TYPES, is_seq, snapshot_json, validate_envelope


def lifecycle(events: list[dict]) -> tuple[int | None, int | None, dict[str, dict]]:
    turn = step = None
    last_turn = 0
    last_step = 0
    pending: dict[str, dict] = {}
    for index, event in enumerate(events):
        validate_envelope(event, index)
        kind, data = event["type"], event["data"]
        if kind not in KNOWN_TYPES:
            continue
        if kind == "ui/message":
            if (not isinstance(data.get("message_id"), str) or not data["message_id"]
                    or data.get("role") not in {"user", "assistant"} or not isinstance(data.get("content"), dict)):
                raise ValueError("UI view events require message identity, role and JSON content")
        if kind in {"assistant/message", "assistant/attempt"} and not data.get("legacy"):
            from .stream import expand_stream
            if not isinstance(data.get("stream"), list):
                raise ValueError("Assistant attempts must contain a timed stream")
            expand_stream(data["stream"])
        if kind == "turn/start":
            if turn is not None or not is_seq(data.get("turn")) or data["turn"] <= last_turn:
                raise ValueError("Turn starts must be increasing and cannot overlap")
            turn = last_turn = data["turn"]
            last_step = 0
        elif kind == "turn/end":
            reason = data.get("reason")
            if isinstance(reason, dict):
                reason = reason.get("kind")
            if turn is None or data.get("turn") != turn or step is not None or reason not in END_REASONS:
                raise ValueError("Turn end must close its open turn after its step")
            turn = None
        elif kind == "step/start":
            if (turn is None or data.get("turn") != turn or step is not None
                    or not is_seq(data.get("step")) or data["step"] <= last_step):
                raise ValueError("Step starts must be increasing inside their open turn")
            step = last_step = data["step"]
            pending.clear()
        elif kind == "step/end":
            if turn != data.get("turn") or step is None or step != data.get("step") or pending:
                raise ValueError("Step end must close its open step with no unanswered calls")
            step = None
        elif kind in {"assistant/message", "assistant/attempt", "tool/call", "tool/result", "request/header"}:
            # Imported legacy surfaces are explicitly marked. They never claim
            # provider usage or lifecycle precision absent from the old store.
            if data.get("legacy") is True:
                continue
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
    return turn, step, pending


def validate_events(events: list[dict]) -> None:
    snapshot_json(events)
    fold_surface(events)
    lifecycle(events)
