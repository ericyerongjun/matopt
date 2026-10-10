"""A scripted provider for harness tests."""

from __future__ import annotations

import json
from typing import AsyncIterator

from app.llm.base import ChatOptions, LLMProvider, StreamDelta, ToolCallDelta


class FakeProvider(LLMProvider):
    id = "fake"
    replay_reasoning = True

    def __init__(self, script: list[dict]):
        # each item: {"text": str} or {"tool": name, "args": dict, "text": str?, "reasoning": str?}
        # `raw_args` streams an exact argument string instead of `json.dumps(args)`,
        # so tests can feed whitespace-heavy (or unparseable) model output.
        self.script = list(script)
        self.calls: list[list[dict]] = []

    async def stream(self, messages, tools, options: ChatOptions) -> AsyncIterator[StreamDelta]:
        self.calls.append(list(messages))
        step = self.script.pop(0) if self.script else {"text": "(script exhausted)"}
        if step.get("reasoning"):
            yield StreamDelta(kind="reasoning", text=step["reasoning"])
        text = step.get("text", "")
        for i in range(0, len(text), 7):
            yield StreamDelta(kind="text", text=text[i:i + 7])
        tool_steps = step.get("tools") or ([step] if step.get("tool") else [])
        for idx, t in enumerate(tool_steps):
            raw = t.get("raw_args")
            args = raw if raw is not None else json.dumps(t.get("args", {}))
            yield StreamDelta(kind="tool_call", tool_call=ToolCallDelta(index=idx, id=f"call_{len(self.calls)}_{idx}", name=t["tool"], arguments=""))
            for i in range(0, len(args), 11):
                yield StreamDelta(kind="tool_call", tool_call=ToolCallDelta(index=idx, arguments=args[i:i + 11]))
        yield StreamDelta(kind="usage", usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        yield StreamDelta(kind="finish", finish_reason="tool_calls" if tool_steps else "stop")

    async def complete_text(self, messages, options):
        return "Fake title"


class FakeRegistry:
    def __init__(self, provider: FakeProvider):
        self.provider = provider

    def resolve(self, provider_id, model_id):
        from app.llm.catalog import PROVIDERS
        spec = PROVIDERS["deepseek"]
        return spec, spec.default_model

    def get(self, provider_id):
        return self.provider

    def configured_ids(self):
        return ["deepseek"]
