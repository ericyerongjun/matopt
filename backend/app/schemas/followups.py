"""Schemas for follow-up suggestion generation."""

from pydantic import BaseModel, Field


class FollowUpRequest(BaseModel):
    content: str = Field(..., min_length=1)
    count: int = Field(3, ge=1, le=6)


class FollowUpResponse(BaseModel):
    followups: list[str] = Field(default_factory=list)
