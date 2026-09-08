"""
Streaming adapter for OpenAI-compatible chat-completions endpoints
(OpenAI, DeepSeek, DashScope/Qwen).

Provider differences are confined to :func:`build_params` (reasoning
controls) and :attr:`LLMProvider.replay_reasoning`.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator, Optional

from openai import AsyncOpenAI, NOT_GIVEN

from app.llm.base import ChatOptions, LLMProvider, StreamDelta, ToolCallDelta
from app.llm.catalog import ModelSpec, ProviderSpec, reasoning_params

logger = logging.getLogger(__name__)


class OpenAICompatProvider(LLMProvider):
    def __init__(self, spec: ProviderSpec, api_key: str, base_url: Optional[str], timeout: float = 600.0):
        self.spec = spec
        self.id = spec.id
        self.replay_reasoning = spec.replay_reasoning
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url or None, timeout=timeout, max_retries=2)

    def model_spec(self, model_id: str) -> Optional[ModelSpec]:
        return self.spec.model(model_id)

    async def stream(self, messages: list[dict], tools: list[dict] | None, options: ChatOptions) -> AsyncIterator[StreamDelta]:
        spec = self.model_spec(options.model)
        kwargs, extra_body = reasoning_params(self.spec, spec, options.reasoning)
        if options.temperature is not None and self.spec.allow_temperature:
            kwargs["temperature"] = options.temperature
        if options.max_tokens:
            kwargs["max_tokens"] = options.max_tokens
        if options.parallel_tool_calls is not None and tools and self.spec.supports_parallel_tool_calls:
            kwargs["parallel_tool_calls"] = options.parallel_tool_calls
        extra_body.update(options.extra or {})

        request = dict(
            model=options.model,
            messages=messages,
            tools=tools or NOT_GIVEN,
            stream=True,
            **kwargs,
        )
        if extra_body:
            request["extra_body"] = extra_body

        try:
            stream = await self.client.chat.completions.create(stream_options={"include_usage": True}, **request)
        except Exception as exc:  # noqa: BLE001 — some gateways reject stream_options
            if "stream_options" in str(exc):
                stream = await self.client.chat.completions.create(**request)
            else:
                raise

        async with stream:
            async for chunk in stream:
                usage = getattr(chunk, "usage", None)
                if usage:
                    yield StreamDelta(kind="usage", usage=_usage_dict(usage))
                if not getattr(chunk, "choices", None):
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta is not None:
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning is None and getattr(delta, "model_extra", None):
                        reasoning = delta.model_extra.get("reasoning_content") or delta.model_extra.get("reasoning")
                    if reasoning:
                        yield StreamDelta(kind="reasoning", text=reasoning)
                    if delta.content:
                        yield StreamDelta(kind="text", text=delta.content)
                    for tc in delta.tool_calls or []:
                        fn = tc.function
                        yield StreamDelta(
                            kind="tool_call",
                            tool_call=ToolCallDelta(
                                index=tc.index if tc.index is not None else 0,
                                id=tc.id,
                                name=fn.name if fn else None,
                                arguments=(fn.arguments or "") if fn else "",
                            ),
                        )
                if choice.finish_reason:
                    yield StreamDelta(kind="finish", finish_reason=choice.finish_reason)

    async def complete_text(self, messages: list[dict], options: ChatOptions) -> str:
        """Non-streaming helper for side tasks (titles, suggestions)."""
        spec = self.model_spec(options.model)
        kwargs, extra_body = reasoning_params(self.spec, spec, options.reasoning or "off")
        if options.max_tokens:
            kwargs["max_tokens"] = options.max_tokens
        request = dict(model=options.model, messages=messages, **kwargs)
        if extra_body:
            request["extra_body"] = extra_body
        resp = await self.client.chat.completions.create(**request)
        return (resp.choices[0].message.content or "").strip()


def _usage_dict(usage) -> dict:
    d = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        v = getattr(usage, key, None)
        if v is not None:
            d[key] = v
    details = getattr(usage, "completion_tokens_details", None)
    if details is not None:
        rt = getattr(details, "reasoning_tokens", None)
        if rt is not None:
            d["reasoning_tokens"] = rt
    pdetails = getattr(usage, "prompt_tokens_details", None)
    if pdetails is not None:
        ct = getattr(pdetails, "cached_tokens", None)
        if ct is not None:
            d["cached_tokens"] = ct
    return d
