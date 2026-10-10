"""
OpenAI SDK adapter for the single DeepSeek V4.1 Flash route.
The SDK is a wire client; it does not make OpenAI a selectable provider.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator, Optional

from openai import AsyncOpenAI, NOT_GIVEN

from app.llm.base import ChatOptions, CompletionResult, LLMProvider, StreamDelta, ToolCallDelta
from app.llm.catalog import MODEL_ID, PROVIDER_ID, MAX_OUTPUT_TOKENS, ModelSpec, ProviderSpec, reasoning_params

logger = logging.getLogger(__name__)


class OpenAICompatProvider(LLMProvider):
    def __init__(self, spec: ProviderSpec, api_key: str, base_url: Optional[str], timeout: float = 600.0):
        if spec.id != PROVIDER_ID or tuple(m.id for m in spec.models) != (MODEL_ID,):
            raise ValueError("Only DeepSeek V4.1 Flash (deepseek-flash) can be dispatched.")
        self.spec = spec
        self.id = spec.id
        self.replay_reasoning = spec.replay_reasoning
        # The harness owns retries so failed attempts are durable and billable
        # usage is never silently hidden inside the SDK.
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url or spec.default_base_url, timeout=timeout, max_retries=0)

    def model_spec(self, model_id: str) -> Optional[ModelSpec]:
        return self.spec.model(model_id)

    def _request_params(self, options: ChatOptions) -> tuple[dict, dict]:
        # Check at the final dispatch boundary as well as in the registry, so
        # auxiliary calls and direct adapter users cannot send another model.
        if options.model != MODEL_ID:
            raise ValueError("Only deepseek-flash can be dispatched; resolve old saved selections through the registry first.")
        kwargs, extra = reasoning_params(self.spec, self.model_spec(options.model), options.reasoning)
        if options.max_tokens is not None:
            if type(options.max_tokens) is not int or not 1 <= options.max_tokens <= MAX_OUTPUT_TOKENS:
                raise ValueError(f"max_tokens must be between 1 and {MAX_OUTPUT_TOKENS}.")
            kwargs["max_tokens"] = options.max_tokens
        if options.temperature is not None and kwargs["reasoning_effort"] == "none":
            kwargs["temperature"] = options.temperature
        supplied = options.extra or {}
        if not isinstance(supplied, dict):
            raise ValueError("extra must be an object.")
        if "model" in supplied or "messages" in supplied or "tools" in supplied or "stream" in supplied:
            raise ValueError("extra cannot override model, messages, tools or stream.")
        if any(key in supplied for key in ("reasoning_effort", "thinking", "max_tokens")):
            raise ValueError("Set reasoning and max_tokens through ChatOptions, not extra.")
        extra.update(supplied)
        return kwargs, extra

    async def stream(self, messages: list[dict], tools: list[dict] | None, options: ChatOptions) -> AsyncIterator[StreamDelta]:
        kwargs, extra_body = self._request_params(options)

        request = dict(
            model=options.model,
            messages=messages,
            tools=tools or NOT_GIVEN,
            stream=True,
            **kwargs,
        )
        if extra_body:
            request["extra_body"] = extra_body

        # Official DeepSeek supports this parameter. Retry dispatches belong
        # to the durable harness, never to a hidden compatibility fallback.
        stream = await self.client.chat.completions.create(stream_options={"include_usage": True}, **request)

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
        return (await self.complete(messages, options)).text

    async def complete(self, messages: list[dict], options: ChatOptions) -> CompletionResult:
        kwargs, extra_body = self._request_params(options)
        request = dict(model=options.model, messages=messages, **kwargs)
        if extra_body:
            request["extra_body"] = extra_body
        resp = await self.client.chat.completions.create(**request)
        return CompletionResult(
            (resp.choices[0].message.content or "").strip(),
            _usage_dict(resp.usage) if getattr(resp, "usage", None) is not None else None,
            getattr(resp.choices[0], "finish_reason", None),
        )


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
    # DeepSeek supplies its cache counters as extension fields rather than
    # OpenAI's prompt_tokens_details. Prompt includes hits; miss is uncached.
    extra = getattr(usage, "model_extra", None) or {}
    for wire_key, key in (
        ("prompt_cache_hit_tokens", "cache_read_tokens"),
        ("prompt_cache_miss_tokens", "uncached_input_tokens"),
        ("cache_write_tokens", "cache_write_tokens"),
    ):
        value = getattr(usage, wire_key, None)
        if value is None:
            value = extra.get(wire_key)
        if value is not None:
            d[key] = value
    if "cache_read_tokens" in d:
        d["cached_tokens"] = d["cache_read_tokens"]
    return d
