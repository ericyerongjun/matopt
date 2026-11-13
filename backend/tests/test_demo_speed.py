"""Exercise the real harness: speed comes from fewer calls, never replayed output."""
import dataclasses
import hashlib
import json

import pytest

from app.agent.harness import AgentHarness, RunRequest
from app.agent.prompts import FINAL_RESPONSE_PROMPT
from app.agent.runs import RunManager
from app.agent.tools.base import Tool, ToolRegistry
from app.agent.events import ToolResult
from app.config import settings
from app.llm.base import CompletionResult, StreamDelta
from app.storage.db import Database
from tests.fake_provider import FakeProvider, FakeRegistry


class Compute(Tool):
    name = "python"
    description = "Compute a result."
    parameters = {"type": "object", "properties": {}}

    async def run(self, ctx):
        return ToolResult(ok=True, output="42; private diagnostic details")


class NoKernels:
    def peek(self, _cid):
        return None


class CountingProvider(FakeProvider):
    def __init__(self, script):
        super().__init__(script)
        self.worker_options = []
        self.finals = self.titles = 0

    async def stream(self, messages, tools, options):
        if messages[0]["content"] == FINAL_RESPONSE_PROMPT:
            self.finals += 1
            yield StreamDelta(kind="text", text=json.loads(messages[-1]["content"])["answer"])
            yield StreamDelta(kind="finish", finish_reason="stop")
            return
        self.worker_options.append(options)
        async for delta in super().stream(messages, tools, options):
            yield delta

    async def complete(self, messages, options):
        self.titles += 1
        return CompletionResult("Measurements", None, "stop")


@pytest.mark.asyncio
@pytest.mark.parametrize("ultrafast,verify,finals,titles", [(False, False, 1, 1), (True, False, 0, 0), (True, True, 1, 0)])
async def test_demo_removes_only_unnecessary_calls(tmp_path, ultrafast, verify, finals, titles):
    script = [{"tool": "python", "args": {}}, {"text": "The computed result is **42**."}]
    if verify:
        script.append({"text": "<report>The computation agrees.</report><verdict>correct</verdict>"})
    provider = CountingProvider(script)
    config = settings.model_copy(update={"data_dir": tmp_path, "session_log_enabled": False,
                                         "default_reasoning": "low"})
    db = Database(tmp_path / "demo.sqlite3")
    cid = db.create_conversation(title="New chat")["id"]
    tools = ToolRegistry()
    tools.register(Compute())
    harness = AgentHarness(db, NoKernels(), config, FakeRegistry(provider), tools)
    events = [event.to_dict() async for event in harness.run(RunRequest(
        conversation_id=cid, text="Analyze the measurements and compute the result.",
        reasoning=None, ultrafast=ultrafast, verify=verify))]
    await harness.wait_side_tasks()
    end = next(event for event in events if event["type"] == "message_end")
    assert end["status"] == "complete"
    assert end["content"]["parts"][0]["text"] == "The computed result is **42**."
    assert "private diagnostic details" not in end["content"]["parts"][0]["text"]
    assert (provider.finals, provider.titles) == (finals, titles)
    assert db.get_conversation(cid)["title"] != "New chat"
    assert provider.worker_options[0].reasoning == ("off" if ultrafast else "low")
    if verify:
        assert end["content"]["work"]["verification"]["status"] == "correct"
    if ultrafast and not verify:
        assert len(provider.worker_options) == 2  # compute dispatch and result; no third writing request
        assert next(event for event in events if event["type"] == "final_start")["mode"] == "buffered"


def test_old_request_idempotency_hashes_remain_valid():
    request = RunRequest(conversation_id="chat-1", text="Analyze measurements")
    legacy = dataclasses.asdict(request)
    legacy.pop("ultrafast")
    legacy.pop("run_id")
    expected = hashlib.sha256(json.dumps(legacy, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    assert RunManager._fingerprint(request, "chat-1") == expected
    assert RunManager._fingerprint(dataclasses.replace(request, ultrafast=True), "chat-1") != expected
