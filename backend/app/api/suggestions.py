"""Suggestion generation endpoint."""

from fastapi import APIRouter, HTTPException, Query

from app.core.chat_service import chat_service
from app.schemas.suggestions import SuggestionResponse

router = APIRouter(prefix="/api", tags=["suggestions"])


@router.get("/suggestions", response_model=SuggestionResponse)
async def suggestions(count: int = Query(4, ge=1, le=8)):
    try:
        items = await chat_service.generate_suggestions(count=count)
        return SuggestionResponse(suggestions=items[:count])
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
