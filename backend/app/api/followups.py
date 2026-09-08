"""POST /api/followups — follow-up question chips for the last answer."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.suggestions import _parse_list
from app.llm.base import ChatOptions
from app.llm.registry import providers

router = APIRouter(prefix="/api", tags=["followups"])

PROMPT = (
    "Given the assistant reply below, propose {count} short follow-up questions the user might ask next "
    "(under 90 characters each, same language as the reply, math wrapped in $...$). "
    "Return ONLY a JSON array of strings.\n\nAssistant reply:\n{content}"
)


class FollowUpRequest(BaseModel):
    content: str = Field(..., min_length=1)
    count: int = Field(3, ge=1, le=6)
    provider: Optional[str] = None
    model: Optional[str] = None


@router.post("/followups")
async def followups(body: FollowUpRequest):
    try:
        spec, m = providers.resolve(body.provider, body.model)
        p = providers.get(spec.id)
        raw = await p.complete_text(
            [{"role": "user", "content": PROMPT.format(count=body.count, content=body.content[-6000:])}],
            ChatOptions(model=m.id, reasoning="off", max_tokens=300),
        )
        return {"followups": _parse_list(raw)[: body.count]}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
