"""Public live-agent contract, exercised through the actual default harness.

Providers and computation are deterministic; document rendering, OCR dispatch,
tool scheduling, verification, UI compilation, journals and final publication
are real. Async gates establish ordering without guessing at elapsed time.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import io
import json
from types import SimpleNamespace

import pymupdf
import pytest
from PIL import Image, ImageDraw

from app.agent.events import Artifact, ToolResult
from app.agent.harness import AgentHarness, RunRequest
from app.agent.presentation import public_messages
from app.agent.prompts import FINAL_RESPONSE_PROMPT, VERIFIER_PROMPT
from app.academic_guidance import ACADEMIC_GUIDANCE
from app.agent.session.derive import derive_messages
from app.agent.tools.base import Tool, ToolRegistry
from app.agent.tools.document_tool import ParseDocumentTool
from app.agent.tools.progress_tool import ReportProgressTool
from app.agent.tools.ui_tools import RenderUITool
from app.agent.workspace import Workspace
from app.config import settings
from app.llm.base import CompletionResult, StreamDelta
from app.storage.db import Database
from tests.fake_provider import FakeProvider, FakeRegistry

REASONING = "PRIVATE_REASONING_CANARY"
VERIFIER = "PRIVATE_VERIFIER_CANARY"
STDOUT = "PRIVATE_STDOUT_CANARY"
INTERNAL_TEXT = "PRIVATE_INTERMEDIATE_TEXT_CANARY"
AGENT_MESSAGE = "PRIVATE_AGENT_COMMUNICATION_CANARY"
STEER = "Keep the table and show the formula, then include the literal Markdown example."
FINAL = """The checked result is **42**.

| Quantity | Result |
| --- | ---: |
| Product | 42 |

The calculation is $6\\times7=42$.

[Source](https://example.test/current)

Requested literal Markdown:

```markdown
**literal source**
```

The attached result.csv contains the checked value. The interactive view shows the same result.
"""


class NoKernels:
    def peek(self, _cid):
        return None


class LiveProvider(FakeProvider):
    """Normal steps remain explicit; the independent final writer is recognized."""
    def __init__(self, script=(), *, gate_final=False):
        super().__init__(list(script))
        self.requests = []
        self.final_requests = []
        self.request_options = []
        self.vision_requests = []
        self.final_started = asyncio.Event()
        self.release_final = asyncio.Event()
        if not gate_final:
            self.release_final.set()

    async def stream(self, messages, tools, options):
        self.requests.append((deepcopy(messages), deepcopy(tools)))
        self.request_options.append(deepcopy(options))
        if messages[0]["content"] == FINAL_RESPONSE_PROMPT:
            payload = json.loads(messages[-1]["content"])
            self.final_requests.append((payload, deepcopy(tools)))
            text = payload["answer"]
            yield StreamDelta(kind="reasoning", text=REASONING)
            yield StreamDelta(kind="text", text=text[:17])
            self.final_started.set()
            await self.release_final.wait()
            for start in range(17, len(text), 19):
                yield StreamDelta(kind="text", text=text[start:start + 19])
            yield StreamDelta(kind="usage", usage={"prompt_tokens": 9, "completion_tokens": 8, "total_tokens": 17})
            yield StreamDelta(kind="finish", finish_reason="stop")
            return
        assert self.script, "The normal execution unexpectedly exhausted its script"
        async for event in super().stream(messages, tools, options):
            yield event

    async def complete(self, messages, options):
        if isinstance(messages[-1]["content"], str) and messages[-1]["content"].startswith("Write a short title"):
            return CompletionResult("Test title", None, "stop")  # Titles run alongside the first turn.
        # The actual document tool sends rasterized PDF pages to this provider.
        self.vision_requests.append(deepcopy(messages))
        assert isinstance(messages[-1]["content"], list)
        assert any(block["type"] == "image_url" for block in messages[-1]["content"])
        return CompletionResult("Question: compute 6 times 7 and check the latest source.\nTable: input 6, input 7.",
                                {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}, "stop")


class ComputeTool(Tool):
    name = "python"
    read_only = True
    concurrent_safe = True
    description = "Deterministic isolated computation used by the contract test."
    parameters = {"type": "object", "properties": {"job": {"type": "string"}}, "required": ["job"]}

    def __init__(self):
        self.started_slow = asyncio.Event()
        self.release_slow = asyncio.Event()
        self.calls = []
        self.retry_attempts = 0

    async def run(self, ctx, job):
        self.calls.append(job)
        await ctx.progress(STDOUT)
        if job == "slow":
            self.started_slow.set()
            await self.release_slow.wait()
        if job == "retry":
            self.retry_attempts += 1
            if self.retry_attempts == 1:
                return ToolResult(ok=False, error=STDOUT, meta={"exit_code": 1})
        await ctx.activity_progress(1, 1, unit="checks")
        artifacts = []
        if job == "correct":
            ctx.workspace.resolve("result.csv").write_text("quantity,value\nproduct,42\n")
            artifacts = [Artifact(kind="file", name="result.csv", path="result.csv")]
        return ToolResult(ok=True, output=f"42\n{STDOUT}", artifacts=artifacts, meta={"exit_code": 0})


class SearchTool(Tool):
    name = "web_search"
    read_only = True
    concurrent_safe = True
    requires_flags = ("web_search",)
    parameters = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}

    async def run(self, ctx, query):
        await ctx.progress(STDOUT)
        return ToolResult(ok=True, output="Current source: https://example.test/current\n" + STDOUT,
                          meta={"count": 1, "public_sources": [{"title": "Reference source", "url": "https://example.test/current"}]})


@pytest.fixture
def rig(tmp_path):
    db = Database(tmp_path / "activity.sqlite")
    provider = LiveProvider()
    compute = ComputeTool()
    config = settings.model_copy(update={"data_dir": tmp_path, "skill_auto_activate": False, "lambda_enabled": False,
        "align_monitor": False, "document_auto_parse": True, "document_ocr_enabled": True,
        "document_ocr_max_pages": 2, "verify_rounds": 8, "agent_max_steps": 18})
    tools = ToolRegistry([ParseDocumentTool(), SearchTool(), compute, RenderUITool(), ReportProgressTool()])
    harness = AgentHarness(db, NoKernels(), config, FakeRegistry(provider), tools)
    cid = db.create_conversation(title="Activity contract")["id"]
    value = SimpleNamespace(db=db, provider=provider, compute=compute, config=config, harness=harness, cid=cid,
                            workspace=Workspace(cid, config.workspace_dir))
    yield value
    provider.release_final.set()
    compute.release_slow.set()
    db.close()


class Capture:
    def __init__(self, harness, request):
        self.harness = harness
        self.request = request
        self.events = []
        self.changed = asyncio.Condition()
        self.task = None

    async def collect(self):
        async for event in self.harness.run(self.request):
            async with self.changed:
                self.events.append(deepcopy(event.to_dict()))
                self.changed.notify_all()
        await self.harness.wait_side_tasks()

    def start(self):
        self.task = asyncio.create_task(self.collect())
        return self

    async def until(self, predicate):
        async with self.changed:
            await asyncio.wait_for(self.changed.wait_for(lambda: predicate(self.events)), 10)

    async def finish(self):
        return await asyncio.wait_for(self.task, 10)

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
        if self.task:
            await asyncio.gather(self.task, return_exceptions=True)


def native():
    return {"id": "checked-result", "title": "Checked result", "fallback": "The product is 42.",
            "nodes": [{"id": "formula", "type": "equation", "props": {"latex": "6\\times7=42"}}], "state": {}}


def report(verdict):
    return {"reasoning": REASONING, "text": f"<report>{VERIFIER}: selected calculation checked.</report><verdict>{verdict}</verdict>"}


def answer_event(events):
    return next(event for event in events if event["type"] == "message_end")


def activity_events(events, tool=None, status=None):
    return [event["item"] for event in events if event["type"] == "activity"
            and (tool is None or event["item"].get("tool") == tool)
            and (status is None or event["item"]["status"] == status)]


def scanned_pdf():
    picture = Image.new("RGB", (500, 180), "white")
    ImageDraw.Draw(picture).text((20, 20), "Question: compute 6 times 7", fill="black")
    output = io.BytesIO()
    picture.save(output, format="PNG")
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_image(pymupdf.Rect(30, 30, 530, 210), stream=output.getvalue())
        return pdf.tobytes()


@pytest.mark.asyncio
async def test_default_agent_streams_live_activity_checked_answer_and_reconciles_public_history(rig):
    p = rig.provider
    p.release_final.clear()
    rig.db.add_message(rig.cid, "user", {"text": "Remember our earlier discussion of multiplication.", "attachments": []})
    rig.db.add_message(rig.cid, "assistant", {"parts": [{"type": "text", "text": "PRIOR_CONVERSATION_RESULT"}],
        "model": "test", "provider": "deepseek", "reasoning": "high", "status": "complete", "usage": {}, "plan": [],
        "stats": {"tool_calls": 0, "llm_calls": 1}})
    upload = rig.workspace.save_upload("assignment.pdf", scanned_pdf())
    p.script = [
        {"reasoning": REASONING, "text": INTERNAL_TEXT, "tools": [
            {"tool": "report_progress", "args": {"text": "I extracted the question. I’m checking the current source and calculating the result."}},
            {"tool": "web_search", "args": {"query": "current multiplication source"}}]},
        {"reasoning": REASONING, "tools": [
            {"tool": "python", "args": {"job": "slow"}},
            {"tool": "python", "args": {"job": "fast"}},
            {"tool": "python", "args": {"job": "retry"}}]},
        {"tool": "python", "args": {"job": "retry"}},
        {"text": FINAL.replace("42", "41")},
        report("incorrect"),
        {"tools": [{"tool": "python", "args": {"job": "correct"}}, {"tool": "render_ui", "args": native()}]},
        {"text": FINAL},
        report("correct"),
    ]
    capture = Capture(rig.harness, RunRequest(conversation_id=rig.cid,
        text="Read this PDF, check a current source and compute the product. Include the table, formula and literal Markdown example.",
        attachments=[{"name": "assignment.pdf", "path": rig.workspace.relpath(upload)}], verify=True, ui_version=1)).start()
    try:
        await capture.until(lambda events: bool(activity_events(events, "python", "completed")) and bool(activity_events(events, "python", "failed")))
        assert rig.compute.started_slow.is_set() and not rig.compute.release_slow.is_set()
        first_completed = activity_events(capture.events, "python", "completed")[0]
        pending = {item["id"]: item for item in activity_events(capture.events, "python")}
        assert len(pending) == 3
        assert len({item["id"] for item in pending.values() if item["status"] == "running"}) == 1
        assert first_completed["id"] != next(item["id"] for item in pending.values() if item["status"] == "running")
        assert not any(event["type"] == "text_delta" for event in capture.events), "working drafts must stay private"
        assert rig.harness.enqueue_steer(rig.cid, STEER)
        rig.compute.release_slow.set()
        await capture.until(lambda events: any(event["type"] == "text_delta" for event in events))
        assert p.final_started.is_set() and not p.release_final.is_set()
        assert not capture.task.done()
        assert not any(event["type"] in {"done", "message_end"} for event in capture.events)
        assert "".join(event["delta"] for event in capture.events if event["type"] == "text_delta") == FINAL[:17]
        assert p.final_requests[0][1] is None, "final writing must not offer tools"
        assert p.final_requests[0][0]["user_updates"] == [STEER]
        assert all(canary not in json.dumps(p.final_requests) for canary in (REASONING, VERIFIER, STDOUT, INTERNAL_TEXT))
        p.release_final.set()
        await capture.finish()
    finally:
        p.release_final.set()
        rig.compute.release_slow.set()
        await capture.close()
    assert capture.events[-1]["type"] == "done"
    assert len(p.vision_requests) == 1, "real PDF rasterization must reach the multimodal provider"
    parsed = activity_events(capture.events, "parse_document", "completed")[-1]
    assert parsed["progress"] == {"completed": 1, "unit": "pages", "total": 1}
    source = activity_events(capture.events, "web_search", "completed")[-1]
    assert source["progress"]["completed"] == 1
    assert source["outputs"][0]["url"] == "https://example.test/current"
    assert any(item["attempt"] == 2 for item in activity_events(capture.events, "python")), "retry must retain its lineage"
    final = answer_event(capture.events)["content"]
    assert final["work"]["verification"] == {"status": "correct", "rounds": 2}
    assert len({item["id"] for item in final["work"]["items"] if item["kind"] == "phase" and item["phase"] == "check"}) == 1
    assert final["parts"][0]["text"] == FINAL
    assert "".join(event["delta"] for event in capture.events if event["type"] == "text_delta") == FINAL
    assert any(part["type"] == "artifact" and part["artifact"]["name"] == "result.csv" for part in final["parts"])
    assert any(part["type"] == "ui" and part["surface"]["status"] == "committed" for part in final["parts"])
    ui_item = activity_events(capture.events, "render_ui", "completed")[-1]
    assert any(output["kind"] == "ui" and output["surface_id"] == "checked-result" for output in ui_item["outputs"])
    public = public_messages(rig.db.list_messages(rig.cid))
    stored = next(message for message in public if message["id"] == answer_event(capture.events)["message_id"])
    assert stored["content"] == final
    wire = json.dumps(capture.events) + json.dumps(public)
    for canary in (REASONING, VERIFIER, STDOUT, INTERNAL_TEXT):
        assert canary not in wire
    assert all(event["type"] not in {"reasoning_delta", "tool_progress", "tool_call_ready", "verification"} for event in capture.events)
    log = rig.db.list_session_events(rig.cid)
    assert any(STDOUT in json.dumps(event) for event in log), "canaries must actually exist privately"
    restored = derive_messages(log)
    assert any("PRIOR_CONVERSATION_RESULT" in str(message.get("content")) for message in restored)
    assert any(STEER in str(message.get("content")) for message in restored)
    assert restored[-1]["role"] == "assistant" and restored[-1]["content"] == FINAL

    # A real subsequent dispatch must still receive the restored conversation.
    before = len(p.requests)
    p.script = [{"text": "The follow-up uses the previously checked product 42."}]
    followup = Capture(rig.harness, RunRequest(conversation_id=rig.cid,
        text="Explain how the checked product relates to our earlier discussion")).start()
    try:
        await followup.finish()
    finally:
        await followup.close()
    resumed = p.requests[before][0]
    assert any("PRIOR_CONVERSATION_RESULT" in str(message.get("content")) for message in resumed)
    assert any(STEER in str(message.get("content")) for message in resumed)
    assert any(message.get("role") == "assistant" and message.get("content") == FINAL for message in resumed)
    assert followup.events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_academic_guidance_reaches_solver_verifier_and_final_writer(rig):
    derivation = r"""Assume $X$ has a finite second moment. Write $\mu = \mathbb{E}[X]$.

$$
\begin{aligned}
\operatorname{Var}(X)
&= \mathbb{E}\left[(X-\mu)^2\right] \\
&= \mathbb{E}[X^2] - \mu^2.
\end{aligned}
$$

The finite second moment justifies expanding the expectation term by term.
"""
    rig.provider.script = [{"text": derivation}, report("correct")]
    capture = Capture(rig.harness, RunRequest(conversation_id=rig.cid,
        text="Derive the variance identity and state its assumptions.", verify=True)).start()
    try:
        await capture.finish()
    finally:
        await capture.close()
    prompts = [messages[0]["content"] for messages, _ in rig.provider.requests]
    assert len(prompts) == 3  # solver, one bounded verification, final writer
    assert VERIFIER_PROMPT in prompts and FINAL_RESPONSE_PROMPT in prompts
    for prompt in prompts:
        assert prompt.count(ACADEMIC_GUIDANCE) == 1
        assert r"\mathbb{E}" in prompt and r"\begin{aligned}" in prompt
        assert "\x08" not in prompt and "\x0c" not in prompt  # no escaped backspace/form-feed
    assert "<verdict>uncertain</verdict>" in VERIFIER_PROMPT
    assert "Use only these supplied results. You have no tools." in FINAL_RESPONSE_PROMPT
    assert rig.provider.final_requests[0][0]["answer"] == derivation
    assert rig.provider.final_requests[0][1] is None
    assert answer_event(capture.events)["content"]["parts"][0]["text"] == derivation
    assert not rig.provider.script


@pytest.mark.asyncio
async def test_unchanged_revision_is_not_verified_twice(rig):
    rig.provider.script = [{"text": FINAL}, report("incorrect"), {"text": FINAL}]
    capture = Capture(rig.harness, RunRequest(conversation_id=rig.cid, text="Check all the calculations in this assignment", verify=True)).start()
    try:
        await capture.finish()
    finally:
        await capture.close()
    checks = [messages for messages, _ in rig.provider.requests if messages[0]["content"] == VERIFIER_PROMPT]
    assert len(checks) == 1
    content = answer_event(capture.events)["content"]
    assert content["work"]["verification"]["status"] == "limit_reached"
    assert content["work"]["verification"]["rounds"] == 1
    assert "same check was not repeated" in content["notice"]
    assert len(rig.provider.final_requests) == 1
    assert not rig.provider.script


@pytest.mark.asyncio
async def test_strict_wording_mode_buffers_answer_and_skips_final_writer(rig):
    rig.provider.script = [{"text": FINAL}, report("correct")]
    capture = Capture(rig.harness, RunRequest(conversation_id=rig.cid, text="Check this assignment", verify=True, strict_final=True)).start()
    try:
        await capture.finish()
    finally:
        await capture.close()
    assert not rig.provider.final_requests
    final_start = next(event for event in capture.events if event["type"] == "final_start")
    assert final_start["mode"] == "buffered"
    assert answer_event(capture.events)["content"]["parts"][0]["text"] == FINAL


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["resume", "stop"])
async def test_pause_waits_for_tool_boundary_and_resume_or_stop_is_truthful(rig, control):
    rig.provider.script = [{"tool": "python", "args": {"job": "slow"}}, {"text": FINAL}]
    capture = Capture(rig.harness, RunRequest(conversation_id=rig.cid, text="Compute the assignment result and explain it")).start()
    try:
        await capture.until(lambda events: bool(activity_events(events, "python", "running")))
        assert rig.compute.started_slow.is_set()
        assert rig.harness.pause(rig.cid)
        assert not any(event["type"] == "run_state" and event["status"] == "paused" for event in capture.events)
        rig.compute.release_slow.set()
        await capture.until(lambda events: any(event["type"] == "run_state" and event["status"] == "paused" for event in events))
        assert not capture.task.done()
        assert not rig.provider.final_requests
        assert activity_events(capture.events, "python", "completed"), "in-flight effect finishes before paused acknowledgement"
        if control == "resume":
            assert rig.harness.enqueue_steer(rig.cid, STEER)
            assert rig.harness.resume(rig.cid)
            await capture.finish()
            assert any(event["type"] == "run_state" and event["status"] == "running" for event in capture.events)
            assert capture.events[-1]["type"] == "done"
            assert rig.provider.final_requests[0][0]["user_updates"] == [STEER]
        else:
            assert rig.harness.stop(rig.cid)
            await capture.finish()
            assert capture.events[-1]["type"] == "stopped"
            assert answer_event(capture.events)["content"]["status"] == "stopped"
            assert not rig.provider.final_requests
    finally:
        rig.compute.release_slow.set()
        await capture.close()


@pytest.mark.asyncio
async def test_stop_during_final_stream_does_not_commit_an_unfinished_answer(rig):
    rig.provider.release_final.clear()
    rig.provider.script = [{"text": FINAL}]
    capture = Capture(rig.harness, RunRequest(conversation_id=rig.cid, text="Present the full assignment calculation")).start()
    try:
        await capture.until(lambda events: any(event["type"] == "text_delta" for event in events))
        assert rig.provider.final_started.is_set()
        assert rig.harness.stop(rig.cid)
        rig.provider.release_final.set()
        await capture.finish()
    finally:
        rig.provider.release_final.set()
        await capture.close()
    assert capture.events[-1]["type"] == "stopped"
    assert not any(event["type"] == "done" for event in capture.events)
    terminal = answer_event(capture.events)["content"]
    assert terminal["status"] == "stopped"
    assert not any(part["type"] == "text" and part["text"] == FINAL for part in terminal["parts"])
    saved = public_messages(rig.db.list_messages(rig.cid))[-1]["content"]
    assert saved == terminal


@pytest.mark.asyncio
async def test_delegated_agent_has_public_assignment_and_outcome_without_private_messages(rig):
    class DelegatedAgent(Tool):
        name = "lambda_data_agent"
        read_only = True
        parameters = {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]}

        async def run(self, ctx, task):
            await ctx.progress(AGENT_MESSAGE)
            return ToolResult(ok=True, output="Checked product: 42\n" + AGENT_MESSAGE,
                              meta={"agent_messages": [AGENT_MESSAGE], "internal_task": task})

    rig.harness.tools.register(DelegatedAgent())
    rig.provider.script = [{"tool": "lambda_data_agent", "args": {"task": "Check the product using the dataset"}}, {"text": FINAL}]
    capture = Capture(rig.harness, RunRequest(conversation_id=rig.cid, text="Delegate checking this calculation and report the result")).start()
    try:
        await capture.finish()
    finally:
        await capture.close()
    agents = activity_events(capture.events, "lambda_data_agent")
    assert [item["status"] for item in agents] == ["queued", "running", "completed"]
    assert all(item["kind"] == "agent" and item["purpose"] for item in agents)
    assert agents[-1]["summary"] == "Completed."
    assert AGENT_MESSAGE not in json.dumps(capture.events)
    assert AGENT_MESSAGE not in json.dumps(public_messages(rig.db.list_messages(rig.cid)))
    assert AGENT_MESSAGE in json.dumps(rig.db.list_session_events(rig.cid))


class BoundaryProvider(LiveProvider):
    def __init__(self, script, *, gate="worker"):
        super().__init__(script)
        self.boundary_started = asyncio.Event()
        self.release_boundary = asyncio.Event()
        self.gate = gate
        self.did_gate = False

    async def stream(self, messages, tools, options):
        kind = "verifier" if messages[0]["content"] == VERIFIER_PROMPT else "final" if messages[0]["content"] == FINAL_RESPONSE_PROMPT else "worker"
        if kind == self.gate and not self.did_gate:
            self.did_gate = True
            self.boundary_started.set()
            await self.release_boundary.wait()
        async for event in super().stream(messages, tools, options):
            yield event


def use_provider(rig, provider):
    rig.provider = provider
    rig.harness.providers = FakeRegistry(provider)
    return provider


def workers(provider):
    return [messages for messages, _ in provider.requests if messages[0]["content"] not in {VERIFIER_PROMPT, FINAL_RESPONSE_PROMPT}]


@pytest.mark.asyncio
async def test_steering_during_verification_returns_to_worker_then_checks_new_result(rig):
    changed = FINAL.replace("42", "72").replace("6\\times7", "8\\times9")
    instruction = "Use 8 times 9 instead of the original inputs."
    provider = use_provider(rig, BoundaryProvider([
        {"text": FINAL}, report("correct"), {"text": changed}, report("correct"),
    ], gate="verifier"))
    capture = Capture(rig.harness, RunRequest(conversation_id=rig.cid, text="Calculate the assignment product 6 times 7", verify=True)).start()
    try:
        await asyncio.wait_for(provider.boundary_started.wait(), 10)
        assert rig.harness.enqueue_steer(rig.cid, instruction)
        provider.release_boundary.set()
        await capture.finish()
    finally:
        provider.release_boundary.set()
        await capture.close()
    assert len(workers(provider)) == 2, "the task change must reach a new worker dispatch"
    assert any(message.get("content") == "[Steer] " + instruction for message in workers(provider)[1])
    checks = [messages for messages, _ in provider.requests if messages[0]["content"] == VERIFIER_PROMPT]
    assert len(checks) == 2
    assert all(not any(str(message.get("content", "")).startswith("[Steer]") for message in messages) for messages in checks)
    assert instruction in checks[-1][-1]["content"], "the new check must include the changed task scope"
    assert changed in checks[-1][-1]["content"]
    assert len([event for event in capture.events if event["type"] == "steer_applied"]) == 1
    content = answer_event(capture.events)["content"]
    assert content["parts"][0]["text"] == changed
    assert content["work"]["verification"] == {"status": "correct", "rounds": 1}
    assert provider.final_requests[0][0]["user_updates"] == [instruction]
    history = derive_messages(rig.db.list_session_events(rig.cid))
    assert any(message.get("content") == "[Steer] " + instruction for message in history)
    assert not provider.script


@pytest.mark.asyncio
@pytest.mark.parametrize("strict_final", [False, True])
async def test_instruction_accepted_while_paused_before_finalization_is_executed(rig, strict_final):
    changed = FINAL.replace("42", "72").replace("6\\times7", "8\\times9")
    instruction = "Use the changed inputs 8 and 9."
    provider = use_provider(rig, BoundaryProvider([{"text": FINAL}, {"text": changed}]))
    capture = Capture(rig.harness, RunRequest(conversation_id=rig.cid, text="Calculate 6 times 7 for the assignment", strict_final=strict_final)).start()
    try:
        await asyncio.wait_for(provider.boundary_started.wait(), 10)
        assert rig.harness.pause(rig.cid)
        provider.release_boundary.set()
        await capture.until(lambda events: any(event["type"] == "run_state" and event["status"] == "paused" for event in events))
        assert rig.harness.enqueue_steer(rig.cid, instruction)
        assert rig.harness.resume(rig.cid)
        await capture.finish()
    finally:
        provider.release_boundary.set()
        await capture.close()
    assert len(workers(provider)) == 2
    assert any(message.get("content") == "[Steer] " + instruction for message in workers(provider)[1])
    assert answer_event(capture.events)["content"]["parts"][0]["text"] == changed
    assert any(event["type"] == "steer_applied" and event["text"] == instruction for event in capture.events)
    assert any(message["role"] == "user" and message["content"].get("text") == instruction for message in rig.db.list_messages(rig.cid))
    if not strict_final:
        assert provider.final_requests[0][0]["user_updates"] == [instruction]


@pytest.mark.asyncio
async def test_final_writer_uses_nonreasoning_deterministic_options_without_changing_worker(rig):
    rig.provider.script = [{"text": FINAL}]
    rig.config.max_output_tokens = 2048
    capture = Capture(rig.harness, RunRequest(conversation_id=rig.cid, text="Explain the checked assignment computation", reasoning="max", temperature=0.8)).start()
    try:
        await capture.finish()
    finally:
        await capture.close()
    worker_options, final_options = rig.provider.request_options
    assert worker_options.reasoning == "max" and worker_options.temperature == 0.8
    assert final_options.reasoning == "off" and final_options.temperature == 0
    assert final_options.model == worker_options.model
    assert final_options.max_tokens == worker_options.max_tokens == 2048


@pytest.mark.asyncio
async def test_late_steering_does_not_refresh_work_budget_or_falsely_acknowledge_application(rig):
    instruction = "Use 8 times 9 instead."
    rig.config.agent_max_steps = 1
    provider = use_provider(rig, BoundaryProvider([{"text": FINAL}]))
    capture = Capture(rig.harness, RunRequest(conversation_id=rig.cid, text="Compute the assignment result")).start()
    try:
        await asyncio.wait_for(provider.boundary_started.wait(), 10)
        assert rig.harness.pause(rig.cid)
        provider.release_boundary.set()
        await capture.until(lambda events: any(event["type"] == "run_state" and event["status"] == "paused" for event in events))
        assert rig.harness.enqueue_steer(rig.cid, instruction)
        rig.harness.resume(rig.cid)
        await capture.finish()
    finally:
        provider.release_boundary.set()
        await capture.close()
    assert len(workers(provider)) == 1
    assert not provider.final_requests
    assert not any(event["type"] == "steer_applied" for event in capture.events)
    assert capture.events[-1]["type"] == "error"
    assert "work limit" in answer_event(capture.events)["content"]["notice"]
    note = next(message for message in rig.db.list_messages(rig.cid) if message["role"] == "user" and message["content"].get("text") == instruction)
    assert note["content"]["applied"] is False


@pytest.mark.asyncio
async def test_stop_preserves_accepted_unapplied_instruction_without_applied_ack(rig):
    instruction = "Use a new input after the current action."
    rig.provider.script = [{"tool": "python", "args": {"job": "slow"}}]
    capture = Capture(rig.harness, RunRequest(conversation_id=rig.cid, text="Compute the assignment result")).start()
    try:
        await capture.until(lambda events: bool(activity_events(events, "python", "running")))
        assert rig.harness.enqueue_steer(rig.cid, instruction)
        assert rig.harness.stop(rig.cid)
        rig.compute.release_slow.set()
        await capture.finish()
    finally:
        rig.compute.release_slow.set()
        await capture.close()
    assert capture.events[-1]["type"] == "stopped"
    assert not any(event["type"] == "steer_applied" for event in capture.events)
    note = next(message for message in rig.db.list_messages(rig.cid) if message["role"] == "user" and message["content"].get("text") == instruction)
    assert note["content"]["applied"] is False
    assert "before applying" in answer_event(capture.events)["content"]["notice"]
