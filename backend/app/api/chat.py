"""
POST /api/chat/stream — run one agent turn and stream events (SSE).
POST /api/chat/{conversation_id}/stop — cancel the running turn.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.harness import RunRequest
from app.runtime import get_runtime

logger = logging.getLogger(__name__)
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
    web_search: bool = False


@router.post("/chat/stream")
async def chat_stream(body: ChatStreamRequest, request: Request):
    rt = get_runtime()
    if body.conversation_id and rt.harness.is_running(body.conversation_id):
        raise HTTPException(status_code=409, detail="A run is already in progress for this conversation.")
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
        web_search=body.web_search,
    )

    async def gen():
        cid = body.conversation_id
        async for event in rt.harness.run(req):
            if event.type == "conversation":
                cid = event.data.get("conversation_id", cid)
            yield event.to_sse()
            if await request.is_disconnected():
                if cid:
                    rt.harness.stop(cid)
                break

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.post("/chat/{conversation_id}/stop")
async def chat_stop(conversation_id: str):
    rt = get_runtime()
    stopped = rt.harness.stop(conversation_id)
    session = rt.kernels.peek(conversation_id)
    if session is not None:
        await session.interrupt()
    return {"stopped": stopped}


class SteerRequest(BaseModel):
    message: str = Field(..., min_length=1)


@router.post("/chat/{conversation_id}/steer")
async def chat_steer(conversation_id: str, body: SteerRequest):
    rt = get_runtime()
    accepted = rt.harness.enqueue_steer(conversation_id, body.message)
    if not accepted:
        raise HTTPException(status_code=409, detail="No run is in progress for this conversation.")
    return {"accepted": True}
