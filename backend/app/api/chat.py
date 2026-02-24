# For chating with the model
"""
POST /api/chat — send messages to the LLM and receive a response.
Supports streaming via Server-Sent Events when ``stream=true``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.core.chat_service import chat_service
from app.schemas.chat import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Send a conversation to the LLM and get a response.
    If ``request.stream`` is True, returns an SSE stream instead.
    """
    try:
        if request.stream:
            return StreamingResponse(
                chat_service.chat_stream(request),
                media_type="text/event-stream",
            )
        return await chat_service.chat(request)
    except Exception as exc:
        logger.exception("Chat endpoint error")
        # Surface a readable message instead of a raw 500
        detail = str(exc)
        if "unsupported_country" in detail:
            detail = (
                "OpenAI API access is blocked from your region. "
                "Set OPENAI_BASE_URL in backend/.env to use a proxy or local model."
            )
        raise HTTPException(status_code=502, detail=detail) from exc