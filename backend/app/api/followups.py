"""POST /api/followups — follow-up question chips for the last answer."""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.suggestions import _parse_list
from app.llm.base import ChatOptions
from app.llm.registry import providers
from app.runtime import get_runtime
from app.storage.db import new_id

router = APIRouter(prefix="/api", tags=["followups"])

PROMPT = (
    "Given the assistant reply below, propose {count} short follow-up questions the user might ask next "
    "(under 90 characters each, same language as the reply, math wrapped in $...$). "
    "Each must go beyond the reply: never ask something the reply already answers. "
    "Return ONLY a JSON array of strings.\n\nAssistant reply:\n{content}"
)


class FollowUpRequest(BaseModel):
    content: str = Field(..., min_length=1)
    count: int = Field(3, ge=1, le=6)
    provider: Optional[str] = None
    model: Optional[str] = None
    conversation_id: Optional[str] = None


@router.post("/followups")
async def followups(body: FollowUpRequest):
    rt = get_runtime() if body.conversation_id else None
    if rt is not None and rt.db.get_conversation(body.conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    task = None
    started = time.time()
    try:
        spec, m = providers.resolve(body.provider, body.model)
        p = providers.get(spec.id)
        messages = [{"role": "user", "content": PROMPT.format(count=body.count, content=body.content[-6000:])}]
        if rt is not None:
            task = {"task_id": new_id("side_"), "task": "followups", "provider": spec.id, "model": m.id}
            await rt.harness.record_side_task(body.conversation_id, {**task, "status": "started", "messages": messages})
        result = await p.complete(
            messages,
            ChatOptions(model=m.id, reasoning="off", max_tokens=300),
        )
        if task is not None:
            await rt.harness.record_side_task(body.conversation_id, {**task, "status": "completed", "usage": result.usage,
                                              "duration_ms": int((time.time() - started) * 1000)})
        return {"followups": _parse_list(result.text)[: body.count], "usage": result.usage}
    except ValueError as exc:
        if task is not None:
            await rt.harness.record_side_task(body.conversation_id, {**task, "status": "error", "usage": None,
                                              "error": str(exc), "duration_ms": int((time.time() - started) * 1000)})
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        if task is not None:
            await rt.harness.record_side_task(body.conversation_id, {**task, "status": "error", "usage": None,
                                              "error": str(exc), "duration_ms": int((time.time() - started) * 1000)})
        raise HTTPException(status_code=502, detail=str(exc)) from exc
