from types import SimpleNamespace as NS

import pytest

from app.llm.base import ChatOptions, LLMProvider, StreamDelta
from app.llm.catalog import MODEL_ID, PROVIDERS
from app.llm.openai_compat import OpenAICompatProvider, _usage_dict


def test_usage_dict_preserves_openai_and_deepseek_cache_evidence():
    usage = NS(prompt_tokens=100, completion_tokens=20, total_tokens=120,
               completion_tokens_details=NS(reasoning_tokens=5), prompt_tokens_details=NS(cached_tokens=60))
    assert _usage_dict(usage)["cached_tokens"] == 60
    usage.model_extra = {"prompt_cache_hit_tokens": 60, "prompt_cache_miss_tokens": 40}
    folded = _usage_dict(usage)
    assert folded["cache_read_tokens"] == 60
    assert folded["uncached_input_tokens"] == 40
    assert folded["total_tokens"] == 120
    assert "cache_write_tokens" not in folded
    assert _usage_dict(NS()) == {}


class FakeStream:
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


@pytest.mark.asyncio
async def test_stream_wire_reasoning_usage_and_tool_calls():
    provider = OpenAICompatProvider(PROVIDERS["deepseek"], "test-key", "http://unused.invalid")
    assert provider.client.max_retries == 0
    await provider.client.close()
    requests = []
    delta = NS(reasoning_content=None, model_extra={"reasoning": "thinking"}, content="answer",
               tool_calls=[NS(index=0, id="call", function=NS(name="read_file", arguments='{"path":"x"}'))])
    chunks = [NS(usage=None, choices=[NS(delta=delta, finish_reason="tool_calls")]),
              NS(usage=NS(prompt_tokens=10, completion_tokens=5, total_tokens=15), choices=[])]

    async def create(**kwargs):
        requests.append(kwargs)
        return FakeStream(chunks)

    provider.client = NS(chat=NS(completions=NS(create=create)))
    deltas = [d async for d in provider.stream([{"role": "user", "content": "hi"}], [], ChatOptions(model=MODEL_ID))]
    assert len(requests) == 1
    assert requests[0]["stream_options"] == {"include_usage": True}
    assert requests[0]["model"] == MODEL_ID
    assert requests[0]["reasoning_effort"] == "high"
    assert requests[0]["extra_body"] == {"thinking": {"type": "enabled"}}
    assert [d.kind for d in deltas] == ["reasoning", "text", "tool_call", "finish", "usage"]
    assert deltas[0].text == "thinking"
    assert deltas[-1].usage["total_tokens"] == 15


@pytest.mark.asyncio
async def test_complete_reports_side_task_usage():
    provider = OpenAICompatProvider(PROVIDERS["deepseek"], "test-key", "http://unused.invalid")
    await provider.client.close()

    async def create(**kwargs):
        assert kwargs["model"] == MODEL_ID
        assert kwargs["reasoning_effort"] == "none"
        assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
        return NS(choices=[NS(message=NS(content=" Title "))], usage=NS(prompt_tokens=3, completion_tokens=2, total_tokens=5))

    provider.client = NS(chat=NS(completions=NS(create=create)))
    result = await provider.complete([], ChatOptions(model=MODEL_ID, reasoning="off"))
    assert result.text == "Title" and result.usage["total_tokens"] == 5
    assert await provider.complete_text([], ChatOptions(model=MODEL_ID, reasoning="off")) == "Title"


@pytest.mark.asyncio
async def test_base_completion_replaces_cumulative_usage_samples():
    class StreamingOnly(LLMProvider):
        async def stream(self, messages, tools, options):
            yield StreamDelta(kind="text", text="hi")
            yield StreamDelta(kind="usage", usage={"total_tokens": 3})
            yield StreamDelta(kind="usage", usage={"total_tokens": 7})
    result = await StreamingOnly().complete([], ChatOptions(model="fake"))
    assert result.text == "hi" and result.usage["total_tokens"] == 7
