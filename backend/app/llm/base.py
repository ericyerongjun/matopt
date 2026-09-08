"""Provider-agnostic streaming interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional


@dataclass
class ToolCallDelta:
    index: int
    id: Optional[str] = None
    name: Optional[str] = None
    arguments: str = ""


@dataclass
class StreamDelta:
    kind: str                                   # text | reasoning | tool_call | usage | finish
    text: str = ""
    tool_call: Optional[ToolCallDelta] = None
    usage: Optional[dict] = None
    finish_reason: Optional[str] = None


@dataclass
class ChatOptions:
    model: str
    reasoning: Optional[str] = None             # slider level id: off | low | medium | high | max
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    parallel_tool_calls: Optional[bool] = None
    extra: dict = field(default_factory=dict)


@dataclass
class ToolCallSpec:
    id: str
    name: str
    arguments: str                              # JSON string as sent by the model


class LLMProvider(ABC):
    id: str = ""
    replay_reasoning: bool = False              # include reasoning_content in assistant replay messages

    @abstractmethod
    def stream(self, messages: list[dict], tools: list[dict] | None, options: ChatOptions) -> AsyncIterator[StreamDelta]:
        """Yield normalised deltas for one model call."""

    def assistant_message(self, text: str, reasoning: str, tool_calls: list[ToolCallSpec]) -> dict:
        """Shape of an assistant turn (with tool calls) when replayed to the model."""
        msg: dict[str, Any] = {"role": "assistant", "content": text or ("" if tool_calls else "")}
        if tool_calls:
            msg["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
                for tc in tool_calls
            ]
        if self.replay_reasoning and tool_calls:
            msg["reasoning_content"] = reasoning or ""
        return msg

    @staticmethod
    def tool_message(call_id: str, name: str, content: str) -> dict:
        return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}
