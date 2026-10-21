"""
Start, observe, reconnect to, and explicitly control durable agent runs.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.harness import RunRequest
from app.agent.runs import RunConflict
from app.runtime import get_runtime

router = APIRouter(prefix="/api", tags=["chat"])


class Attachment(BaseModel):
    name: str
    path: str
    url: Optional[str] = None
    size: Optional[int] = None


class ChatStreamRequest(BaseModel):
    conversation_id: Optional[str] = None
    message: str = Field(..., min_length=1)
    attachments: list[Attachment] = Field(default_factory=list)
    provider: Optional[str] = None
    model: Optional[str] = None
    reasoning: Optional[str] = None
    verify: bool = False
    temperature: Optional[float] = None
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    # Available automatically; an API caller may explicitly disable it.
    web_search: bool = True
    ui_version: Optional[int] = Field(default=None, ge=1)
    client_run_id: Optional[str] = Field(default=None, min_length=1, max_length=128,
                                         pattern=r"^[A-Za-z0-9_.:-]+$")
    strict_final: bool = False
    language: Optional[str] = Field(default=None, max_length=8)


@router.post("/chat/stream")
async def chat_stream(body: ChatStreamRequest, request: Request):
    rt = get_runtime()
    # Reattaching a durable run does not need a presently configured provider.
    # RunManager still validates that the idempotency key matches the request.
    if not (body.client_run_id and rt.db.get_run_by_client_id(body.client_run_id)):
        try:
            rt.providers.resolve(body.provider, body.model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    req = RunRequest(
        conversation_id=body.conversation_id,
        text=body.message,
        attachments=[a.model_dump() for a in body.attachments],
        provider_id=body.provider,
        model_id=body.model,
        reasoning=body.reasoning,
        verify=body.verify,
        temperature=body.temperature,
        skills=body.skills,
        tools=body.tools,
        web_search=body.web_search,
        ui_version=body.ui_version,
        strict_final=body.strict_final,
        language=body.language,
    )
    try:
        run = await rt.runs.start(req, body.client_run_id)
    except RunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _stream_response(rt.runs, run["id"], request)


def _stream_response(manager, run_id: str, request: Request, after: int = 0) -> StreamingResponse:
    async def gen():
        iterator = manager.subscribe(run_id, after)
        pending = None
        try:
            while True:
                if pending is None:
                    pending = asyncio.create_task(anext(iterator))
                ready, _ = await asyncio.wait({pending}, timeout=0.25)
                if await request.is_disconnected():
                    break
                if not ready:
                    continue
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    pending = None
                    break
                pending = None
                yield event.to_sse() if event is not None else ": keepalive\n\n"
        finally:
            # Only this subscription is closed. The RunManager owns execution.
            if pending is not None:
                pending.cancel()
                try:
                    await pending
                except (asyncio.CancelledError, StopAsyncIteration):
                    pass
            await iterator.aclose()
    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive",
                 "X-Run-Id": run_id},
    )


@router.get("/chat/{conversation_id}/run")
async def chat_run(conversation_id: str):
    rt = get_runtime()
    if rt.db.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"run": rt.runs.latest(conversation_id)}


@router.get("/runs/{run_id}")
async def run_snapshot(run_id: str):
    run = get_runtime().runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run": run}


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, after: int = Query(0, ge=0)):
    rt = get_runtime()
    if rt.runs.get(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    header = request.headers.get("last-event-id")
    if header:
        try:
            after = max(after, int(header))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Last-Event-ID") from exc
    return _stream_response(rt.runs, run_id, request, after)


@router.post("/chat/{conversation_id}/stop")
async def chat_stop(conversation_id: str):
    rt = get_runtime()
    stopped, run_id = await rt.runs.stop(conversation_id)
    session = rt.kernels.peek(conversation_id)
    if session is not None:
        await session.interrupt()
    return {"stopped": stopped, "run_id": run_id}


@router.post("/chat/{conversation_id}/pause")
async def chat_pause(conversation_id: str):
    rt = get_runtime()
    accepted, run_id = await rt.runs.pause(conversation_id)
    if not accepted:
        raise HTTPException(status_code=409, detail="No run is in progress for this conversation.")
    return {"accepted": True, "run_id": run_id}


@router.post("/chat/{conversation_id}/resume")
async def chat_resume(conversation_id: str):
    rt = get_runtime()
    accepted, run_id = await rt.runs.resume(conversation_id)
    if not accepted:
        raise HTTPException(status_code=409, detail="No paused run is in progress for this conversation.")
    return {"accepted": True, "run_id": run_id}


class SteerRequest(BaseModel):
    message: str = Field(..., min_length=1)
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)


@router.post("/chat/{conversation_id}/steer")
async def chat_steer(conversation_id: str, body: SteerRequest):
    rt = get_runtime()
    accepted, run_id = await rt.runs.steer(conversation_id, body.message, skills=body.skills, tools=body.tools)
    if not accepted:
        raise HTTPException(status_code=409, detail="No run is in progress for this conversation.")
    return {"accepted": True, "run_id": run_id}
