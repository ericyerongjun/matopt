"""Follow-up suggestion endpoint."""

from fastapi import APIRouter, HTTPException

from app.core.chat_service import chat_service
from app.schemas.followups import FollowUpRequest, FollowUpResponse

router = APIRouter(prefix="/api", tags=["followups"])


@router.post("/followups", response_model=FollowUpResponse)
async def followups(request: FollowUpRequest):
    try:
        items = await chat_service.generate_followups(
            content=request.content,
            count=request.count,
        )
        return FollowUpResponse(followups=items[: request.count])
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
