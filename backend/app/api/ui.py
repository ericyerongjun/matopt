"""Negotiated UI capabilities, persisted state, and explicit action streams."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.agent.events import Event
from app.runtime import get_runtime
from app.ui.contracts import NODE_TYPES, UIActionRequest, UIStateRequest, UI_VERSION
from app.ui.store import UIConflict, UINotFound

router = APIRouter(prefix="/api", tags=["ui"])


def _require_conversation(rt, cid):
    if rt.db.get_conversation(cid) is None:
        raise HTTPException(404, "Conversation not found")


def _error(exc):
    if isinstance(exc, UINotFound):
        return HTTPException(404, str(exc))
    if isinstance(exc, UIConflict):
        return HTTPException(409, str(exc))
    return HTTPException(422, str(exc))


@router.get("/ui/capabilities")
async def capabilities():
    ui = get_runtime().harness.ui
    return {"version": UI_VERSION, "native": ui.enabled("native"), "mini_apps": ui.enabled("mini_app"), "node_types": list(NODE_TYPES)}


@router.get("/conversations/{conversation_id}/ui/{surface_id}")
async def surface(conversation_id: str, surface_id: str):
    rt = get_runtime()
    _require_conversation(rt, conversation_id)
    result = rt.harness.ui.store.get(conversation_id, surface_id)
    if result is None:
        raise HTTPException(404, "UI surface not found")
    return {"surface": result}


@router.patch("/conversations/{conversation_id}/ui/{surface_id}/state")
async def state(conversation_id: str, surface_id: str, body: UIStateRequest):
    rt = get_runtime()
    _require_conversation(rt, conversation_id)
    try:
        result = await rt.harness.ui.save_state(conversation_id, surface_id, body.revision, body.state)
    except ValueError as exc:
        raise _error(exc) from exc
    return {"surface": result}


@router.post("/conversations/{conversation_id}/ui/{surface_id}/actions")
async def action(conversation_id: str, surface_id: str, body: UIActionRequest, request: Request):
    rt = get_runtime()
    _require_conversation(rt, conversation_id)
    service = rt.harness.ui
    try:
        prepared = service.prepare_action(conversation_id, surface_id, body.model_dump(mode="json"))
    except ValueError as exc:
        raise _error(exc) from exc

    async def stream():
        if isinstance(prepared, dict):
            for event in prepared["events"]:
                yield Event(event["type"], {k: v for k, v in event.items() if k != "type"}).to_sse()
            return
        iterator = service.actions(prepared)
        pending = None
        try:
            while True:
                if pending is None:
                    pending = asyncio.create_task(anext(iterator))
                ready, _ = await asyncio.wait({pending}, timeout=0.25)
                if await request.is_disconnected():
                    prepared.cancel.set()
                    break
                if not ready:
                    continue
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    pending = None
                    break
                pending = None
                yield event.to_sse()
        finally:
            if pending is not None:
                if not pending.done():
                    pending.cancel()
                try:
                    await pending
                except (asyncio.CancelledError, StopAsyncIteration):
                    pass
            await iterator.aclose()
            rt.harness.release_external_run(conversation_id, prepared.cancel)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})
