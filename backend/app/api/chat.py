# For chating with the model
"""
POST /api/chat — send messages to the LLM and receive a response.
Supports streaming via Server-Sent Events when ``stream=true``.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.core.chat_service import chat_service
from app.schemas.chat import ChatRequest, ChatResponse

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Send a conversation to the LLM and get a response.
    If ``request.stream`` is True, returns an SSE stream instead.
    """
    if request.stream:
        return StreamingResponse(
            chat_service.chat_stream(request),
            media_type="text/event-stream",
        )
    return await chat_service.chat(request)