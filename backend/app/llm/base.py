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
    reasoning: Optional[str] = None             # slider level id: off | low | high | max
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    parallel_tool_calls: Optional[bool] = None
    extra: dict = field(default_factory=dict)


@dataclass
class ToolCallSpec:
    id: str
    name: str
    arguments: str                              # JSON string as sent by the model


@dataclass
class CompletionResult:
    """A side-task answer and its provider usage; missing usage stays unknown."""

    text: str
    usage: Optional[dict] = None
    finish_reason: Optional[str] = None


class LLMProvider(ABC):
    id: str = ""
    replay_reasoning: bool = False              # include reasoning_content in assistant replay messages

    @abstractmethod
    def stream(self, messages: list[dict], tools: list[dict] | None, options: ChatOptions) -> AsyncIterator[StreamDelta]:
        """Yield normalised deltas for one model call."""

    async def complete_text(self, messages: list[dict], options: ChatOptions) -> str:
        """Compatibility helper for providers without a non-streaming endpoint."""
        result = await self.complete(messages, options)
        return result.text

    async def complete(self, messages: list[dict], options: ChatOptions) -> CompletionResult:
        # Preserve existing custom providers that implement complete_text only.
        if type(self).complete_text is not LLMProvider.complete_text:
            return CompletionResult(await self.complete_text(messages, options))
        text: list[str] = []
        usage = None
        finish_reason = None
        async for delta in self.stream(messages, None, options):
            if delta.kind == "text":
                text.append(delta.text)
            elif delta.kind == "usage" and delta.usage is not None:
                usage = dict(delta.usage)  # cumulative samples replace, never add
            elif delta.kind == "finish":
                finish_reason = delta.finish_reason
        return CompletionResult("".join(text).strip(), usage, finish_reason)

    def assistant_message(self, text: str, reasoning: str, tool_calls: list[ToolCallSpec]) -> dict:
        """Shape of an assistant turn (with tool calls) when replayed to the model."""
        msg: dict[str, Any] = {"role": "assistant", "content": text or ("" if tool_calls else "")}
        if tool_calls:
            msg["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
                for tc in tool_calls
            ]
        if self.replay_reasoning:
            msg["reasoning_content"] = reasoning or ""
        return msg

    @staticmethod
    def tool_message(call_id: str, name: str, content: str) -> dict:
        return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}
