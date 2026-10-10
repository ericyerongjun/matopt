"""Conservative append-only recovery translated from dsh core/session/repair.ts."""

from __future__ import annotations

import json

from .validate import lifecycle

# These two messages are translated verbatim from the pinned MIT upstream.
INTERRUPTED_STARTED = (
    "The tool call was interrupted after it was recorded, but no result was durably recorded. "
    "Its outcome is unknown. Decide whether to retry from the tool semantics: retry only if "
    "the operation is read-only or idempotent; if it may have side effects, first verify "
    "external state or ask the user. Do not retry blindly."
)
INTERRUPTED_NOT_STARTED = (
    "The tool call was interrupted before the Harness recorded it as started. Retry it if it is still needed."
)


def open_turn_closers(events: list[dict], cause: str = "interrupted", *, close_turn: bool = True) -> list[dict]:
    """Append missing tool results, then step/end and turn/end; never discard rows.

    Reuse the last committed timestamp and deterministic result identities so
    repeatedly opening the same crashed prefix produces the same repair.
    """
    if cause not in {"interrupted", "forked", "aborted", "error"}:
        raise ValueError("Invalid recovery cause")
    turn, step, pending = lifecycle(events)
    if turn is None:
        return []
    seq = len(events)
    timestamp = events[-1]["time"]
    out: list[dict] = []
    for call_id, call in pending.items():
        code = "TOOL_OUTCOME_UNKNOWN" if call["started"] else "TOOL_NOT_STARTED"
        text = INTERRUPTED_STARTED if call["started"] else INTERRUPTED_NOT_STARTED
        if cause == "forked":
            text = (
                "The parent session may have completed this tool call after the fork point. "
                "Its outcome is unknown. Retry only if the operation is read-only or idempotent; "
                "otherwise verify external state first."
            )
        out.append({"seq": seq, "time": timestamp, "type": "tool/result", "surfaceOp": "append", "data": {
            "turn": turn, "step": step, "callId": call_id, "name": call["name"], "ok": False,
            "output": "", "error": text, "artifacts": [],
            "message": {"role": "tool", "tool_call_id": call_id, "name": call["name"], "content": text},
            "id": f"{cause}-tool-result-{call_id}-{seq}",
            "meta": {"recovery_code": code, "synthetic": True},
        }})
        seq += 1
    if step is not None:
        out.append({"seq": seq, "time": timestamp, "type": "step/end", "data": {"turn": turn, "step": step}})
        seq += 1
    if close_turn:
        # The pre-trajectory UI consumes aggregate rows. Recover its view from
        # the same durable events instead of dropping partial work on restart.
        start = next(e for e in reversed(events) if e["type"] == "turn/start")
        message_id = start["data"].get("message_id")
        if message_id is not None and not any(e["type"] == "ui/message" and
                e["data"].get("message_id") == message_id for e in events):
            content = recovered_content(events[start["seq"]:] + out, start["data"])
            out.append({"seq": seq, "time": timestamp, "type": "ui/message", "data": {
                "message_id": message_id, "role": "assistant", "content": content,
            }})
            seq += 1
        out.append({"seq": seq, "time": timestamp, "type": "turn/end", "data": {"turn": turn, "reason": {"kind": cause}}})
    return out


def recovered_content(events: list[dict], turn_data: dict) -> dict:
    from app.agent.results import public_result, result_parts

    parts: list[dict] = []
    calls: dict[tuple, dict] = {}
    ui_fallbacks: dict[str, dict] = {}
    usage: dict = {}
    llm_calls = 0
    document_calls: dict[str, dict] = {}
    result_receipts: list[dict] = []
    message_id = turn_data.get("message_id")
    strict_final = turn_data.get("strict_final") is True
    saw_result_receipt = False
    for event in events:
        data = event["data"]
        if event["type"] == "llm/side-task" and data.get("task") == "document_ocr":
            document_calls[data["task_id"]] = data
        if event["type"] in {"assistant/message", "assistant/attempt"} and not data.get("legacy") and not data.get("automatic"):
            llm_calls += 1
            for key, value in (data.get("usage") or {}).items():
                if type(value) in (int, float):
                    usage[key] = usage.get(key, 0) + value
        if event["type"] == "assistant/message" and not data.get("legacy"):
            phase = data.get("phase", "answer")
            reasoning = data.get("reasoning_content")
            if reasoning:
                parts.append({"type": "reasoning", "text": reasoning, "phase": phase})
            text = data.get("text") or data["message"].get("content")
            if isinstance(text, str) and text:
                parts.append({"type": "text", "text": text, "phase": phase})
            for call in data["message"].get("tool_calls") or []:
                function = call["function"]
                raw = function["arguments"]
                try:
                    arguments = json.loads(raw)
                    if not isinstance(arguments, dict):
                        arguments = {"_raw": raw}
                except json.JSONDecodeError:
                    arguments = {"_raw": raw}
                part = {"type": "tool_call", "id": call["id"], "name": function["name"],
                        "arguments": arguments, "status": "error", "phase": phase}
                calls[(data.get("step"), call["id"])] = part
                parts.append(part)
        elif event["type"] == "ui/result" and data.get("message_id") == message_id:
            saw_result_receipt = True
            strict_final = strict_final or data.get("strict_final") is True
            block = public_result(data.get("block"))
            if (block is not None and isinstance(message_id, str)
                    and block["id"].startswith(message_id + ":result:")):
                result_receipts.append({"type": "result_block", "block": block})
        elif event["type"] == "ui/fallback":
            sid = data["surface_id"]
            part = ui_fallbacks.get(sid)
            if part is None:
                part = {"type": "text", "text": data["fallback"]}
                ui_fallbacks[sid] = part
                parts.append(part)
            else:
                part["text"] = data["fallback"]
        elif event["type"] == "ui/surface":
            repaired = ui_fallbacks.pop(data["surface_id"], None)
            if repaired is not None:
                parts[:] = [p for p in parts if p is not repaired]
            part = {"type": "ui", "surface": {"id": data["surface_id"], "revision": data["revision"], "fallback": data.get("fallback", "")}}
            existing = next((p for p in parts if p.get("type") == "ui" and p["surface"]["id"] == data["surface_id"]), None)
            if existing is None:
                parts.append(part)
            else:
                existing["surface"] = part["surface"]
        elif event["type"] == "tool/call" and (data.get("step"), data.get("callId")) in calls:
            part = calls[(data.get("step"), data["callId"])]
            if isinstance(data.get("normalized_arguments"), dict):
                part["arguments"] = data["normalized_arguments"]
        elif event["type"] == "tool/result" and (data.get("step"), data.get("callId")) in calls:
            part = calls[(data.get("step"), data["callId"])]
            part["result"] = {key: data.get(key) for key in ("ok", "output", "artifacts", "error", "meta")}
            part["status"] = "completed" if data.get("ok") else "error"
    for data in document_calls.values():
        llm_calls += 1
        for key, value in (data.get("usage") or {}).items():
            if type(value) in (int, float):
                usage[key] = usage.get(key, 0) + value
    if not strict_final:
        # Withdrawal tombstones must override older public-journal revisions.
        parts.extend(result_parts(result_receipts))
    content = {"parts": parts, "provider": turn_data.get("provider"), "model": turn_data.get("model"),
            "reasoning": turn_data.get("reasoning"), "usage": usage, "status": "stopped", "recovered": True,
            "plan": [], "stats": {"llm_calls": llm_calls, "tool_calls": len(calls)}}
    if turn_data.get("presentation") == "final" or saw_result_receipt:
        content.update(presentation="final", final_text="", final_ready=False, strict_final=strict_final)
    return content
