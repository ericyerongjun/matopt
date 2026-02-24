"""Schemas for suggestion generation."""

from pydantic import BaseModel, Field


class SuggestionResponse(BaseModel):
    suggestions: list[str] = Field(default_factory=list)
