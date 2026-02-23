"""Pydantic request / response schemas for the chat endpoint."""

from typing import Optional
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    stream: bool = False
    use_sympy: bool = True          # auto-augment with symbolic verification
    use_wolfram: bool = False       # allow Wolfram Alpha lookups


class ToolCall(BaseModel):
    """Represents one tool invocation made by the LLM."""
    name: str
    arguments: dict
    result: Optional[str] = None


class ChatResponse(BaseModel):
    id: str
    content: str
    tool_calls: list[ToolCall] = []
    usage: Optional[dict] = None    # token counts
