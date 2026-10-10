"""Model history is the verbatim fold of validated surface operations."""

from __future__ import annotations

from .events import KNOWN_TYPES, SURFACE_TYPES, decode_source_seqs, snapshot_json, validate_envelope


def fold_surface(events: list[dict]) -> list[int]:
    """Translate dsh surface.ts's positional range replacement over live nodes.

    The endpoints are event sequences present in the current surface; intervening
    live positions are replaced regardless of their numeric sequence ordering.
    """
    nodes: list[int] = []
    for index, event in enumerate(events):
        validate_envelope(event, index)
        if event["type"] not in SURFACE_TYPES:
            continue
        op = event["surfaceOp"]
        if op == "append":
            if event["type"] == "system/message" and nodes:
                raise ValueError("The system head must be replaced, not appended twice")
            if not nodes and event["type"] != "system/message":
                raise ValueError("The first surface node must be the system head")
            nodes.append(event["seq"])
            continue
        try:
            start = nodes.index(op["startSeq"])
            end = nodes.index(op["endSeq"])
        except ValueError as exc:
            raise ValueError("Replacement endpoints must be current surface nodes") from exc
        if start > end:
            raise ValueError("Replacement endpoints are reversed in surface order")
        if event["type"] == "system/message":
            if start != 0 or end != 0:
                raise ValueError("System replacement may only replace the system head")
        elif start == 0:
            raise ValueError("A replacement cannot shadow the protected system head")
        shadowed = nodes[start:end + 1]
        sources = decode_source_seqs(event.get("sourceEventSeqs"), event["seq"])
        if not set(shadowed).issubset(sources):
            raise ValueError("Replacement sources must include every shadowed surface node")
        nodes[start:end + 1] = [event["seq"]]
    return nodes


def derive_messages(events: list[dict]) -> list[dict]:
    return [snapshot_json(events[seq]["data"]["message"]) for seq in fold_surface(events)
            if not events[seq]["data"].get("hidden", False)]


def derive_request(events: list[dict]) -> dict:
    """Reconstruct the exact history, tool catalog and options at a header prefix."""
    header = next((e["data"]["header"] for e in reversed(events)
                   if e["type"] == "request/header"), None)
    if header is None:
        raise ValueError("Session has no request header")
    return {"messages": derive_messages(events), "tools": snapshot_json(header.get("tools")),
            "config": snapshot_json(header["config"])}
