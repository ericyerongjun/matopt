"""Shared run allowances through real harness dispatch and canonical storage.

Provider responses are deterministic and never contact a paid service. Gates
establish ordering; an injected monotonic clock advances deadlines explicitly.
The controller, tools, document rendering, journals and publication are real.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.agent import harness as harness_module
from app.agent.budget import RunBudget
from app.agent.harness import RunRequest
from app.agent.presentation import public_content
from app.agent.tools.result_tool import PublishResultTool
from app.llm.base import StreamDelta
from tests.test_agent_activity import (
    BoundaryProvider,
    Capture,
    INTERNAL_TEXT,
    LiveProvider,
    REASONING,
    answer_event,
    rig,
    scanned_pdf,
    use_provider,
)


PARTIAL = "The extracted calculation is **6 × 7 = 42**."


def publish_partial():
    return {"tool": "publish_result", "args": {"id": "calculation", "content": PARTIAL},
            "text": INTERNAL_TEXT, "reasoning": REASONING}


def saved_answer(rig, capture, *, status="error"):
    """Check the public terminal event against a fresh canonical database read."""
    end = answer_event(capture.events)
    saved = rig.db.get_message(end["message_id"])["content"]
    assert end["status"] == saved["status"] == status
    assert public_content(saved) == end["content"]
    assert saved["budget"]["usage"]["unsettled_attempts"] == 0
    if status != "complete":
        assert not any(e["type"] == "done" for e in capture.events)
    assert not any(value in json.dumps(capture.events) for value in (REASONING, INTERNAL_TEXT))
    return saved


def assert_partial_retained(saved):
    public = public_content(saved)
    blocks = [part["block"] for part in public["parts"] if part["type"] == "result_block"]
    assert len(blocks) == 1
    assert blocks[0]["content"] == PARTIAL
    assert blocks[0]["status"] == "provisional"
    assert not any(part["type"] == "text" for part in public["parts"])
    assert not saved["final_ready"]
    assert "partial results were saved" in public["notice"]


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def inject_clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(harness_module, "RunBudget", lambda **kwargs: RunBudget(**kwargs, clock=clock))
    return clock


class QuietProvider(LiveProvider):
    """Wait without a token until cancelled; expose cleanup independently."""
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.closed = asyncio.Event()
        self.release = asyncio.Event()
        self.body_task = None
        self.attempts = 0

    async def stream(self, messages, tools, options):
        self.attempts += 1
        self.body_task = asyncio.current_task()
        self.started.set()
        try:
            await self.release.wait()
            yield StreamDelta(kind="finish", finish_reason="stop")
        finally:
            self.closed.set()


class RetryProvider(LiveProvider):
    def __init__(self, script):
        super().__init__(script)
        self.attempts = 0

    async def stream(self, messages, tools, options):
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("connection unavailable before any response")
        async for delta in super().stream(messages, tools, options):
            yield delta


@pytest.mark.asyncio
@pytest.mark.parametrize("session_log", [True, False])
async def test_model_cap_retains_published_partial_with_and_without_session_log(rig, session_log):
    rig.config.session_log_enabled = session_log
    rig.config.run_max_model_attempts = 1
    rig.harness.tools.register(PublishResultTool())
    rig.provider.script = [publish_partial(), {"text": "This request must never be dispatched."}]
    capture = Capture(rig.harness, RunRequest(rig.cid, "Calculate and publish the useful intermediate result.")).start()
    try:
        await capture.finish()
        saved = saved_answer(rig, capture)
        assert_partial_retained(saved)
        assert len(rig.provider.requests) == 1
        assert not rig.provider.final_requests
        assert len(rig.provider.script) == 1
        assert saved["budget"]["model_attempts"] == 1
        assert saved["budget"]["tool_starts"] == 1
        assert saved["budget"]["usage"]["complete"]
        assert saved["budget"]["usage"]["exact"]["total_tokens"] == 15
        assert bool(rig.db.list_session_events(rig.cid)) == session_log
    finally:
        await capture.close()


@pytest.mark.asyncio
async def test_final_writer_cannot_bypass_allowance_or_publish_the_private_draft(rig):
    rig.config.run_max_model_attempts = 2
    rig.harness.tools.register(PublishResultTool())
    private_draft = "UNPUBLISHED_FINAL_DRAFT_CANARY"
    rig.provider.script = [publish_partial(), {"text": private_draft}]
    capture = Capture(rig.harness, RunRequest(rig.cid, "Calculate, publish the partial result, and explain the answer.")).start()
    try:
        await capture.finish()
        saved = saved_answer(rig, capture)
        assert_partial_retained(saved)
        assert len(rig.provider.requests) == 2 and not rig.provider.final_requests
        assert saved["budget"]["model_attempts"] == 2
        assert saved["budget"]["usage"]["exact"]["total_tokens"] == 30
        assert private_draft not in json.dumps(capture.events)
        assert not any(e["type"] == "text_delta" for e in capture.events)
    finally:
        await capture.close()


@pytest.mark.asyncio
async def test_document_ocr_pages_are_accounted_but_do_not_consume_the_model_allowance(rig):
    """A long PDF must not starve the turn: pages count toward usage, not the attempt cap."""
    rig.config.run_max_model_attempts = 2
    rig.harness.tools.register(PublishResultTool())
    upload = rig.workspace.save_upload("scanned-calculation.pdf", scanned_pdf())
    rig.provider.script = [publish_partial(), {"text": "The worker kept both of its attempts."}]
    capture = Capture(rig.harness, RunRequest(rig.cid, "Read the attached calculation and publish the result.",
        attachments=[{"name": upload.name, "path": rig.workspace.relpath(upload)}])).start()
    try:
        await capture.finish()
        saved = saved_answer(rig, capture)
        assert len(rig.provider.vision_requests) == 1 and len(rig.provider.requests) == 2
        budget = saved["budget"]
        assert budget["model_attempts"] == 3
        assert budget["capped_model_attempts"] == 2 and budget["uncapped_model_attempts"] == 1
        assert budget["remaining_model_attempts"] == 0
        assert sorted(receipt["source"] for receipt in budget["model_receipts"].values()) == [
            "document_ocr", "native:answer", "native:answer"]
        assert budget["usage"]["complete"]
    finally:
        await capture.close()


@pytest.mark.asyncio
async def test_real_provider_retry_spends_a_separate_attempt_and_preserves_unknown_usage(rig):
    rig.config.run_max_model_attempts = 2
    rig.harness.tools.register(PublishResultTool())
    provider = use_provider(rig, RetryProvider([publish_partial(), {"text": "No third dispatch."}]))
    capture = Capture(rig.harness, RunRequest(rig.cid, "Calculate and publish this result.")).start()
    try:
        await capture.finish()
        saved = saved_answer(rig, capture)
        assert_partial_retained(saved)
        assert provider.attempts == 2
        assert not provider.final_requests
        budget = saved["budget"]
        assert budget["model_attempts"] == 2
        receipts = list(budget["model_receipts"].values())
        assert [receipt["outcome"] for receipt in receipts] == ["error", "completed"]
        assert budget["usage"]["unknown_settled_attempts"] == 1
        assert budget["usage"]["reported_attempts"] == 1
        assert not budget["usage"]["complete"] and budget["usage"]["exact"] is None
        assert budget["usage"]["reported_subtotal"]["total_tokens"] == 15
        assert len([e for e in rig.db.list_session_events(rig.cid) if e["type"] == "llm/retry"]) == 1
    finally:
        await capture.close()


@pytest.mark.asyncio
async def test_stop_interrupts_quiet_provider_closes_stream_and_reaps_pending_read(rig):
    provider = use_provider(rig, QuietProvider())
    capture = Capture(rig.harness, RunRequest(rig.cid, "Calculate the requested result.")).start()
    try:
        await asyncio.wait_for(provider.started.wait(), 5)
        assert rig.harness.stop(rig.cid)
        await capture.finish()
        saved = saved_answer(rig, capture, status="stopped")
        assert provider.attempts == 1 and provider.closed.is_set()
        assert provider.body_task.done()
        assert provider.body_task not in asyncio.all_tasks()
        assert saved["budget"]["model_attempts"] == 1
        receipt = next(iter(saved["budget"]["model_receipts"].values()))
        assert receipt["outcome"] == "aborted"
        assert saved["budget"]["usage"]["exact"] is None
        assert saved["budget"]["usage"]["unknown_settled_attempts"] == 1
        assert not any(e["type"] == "text_delta" for e in capture.events)
    finally:
        provider.release.set()
        await capture.close()


@pytest.mark.asyncio
async def test_absolute_deadline_interrupts_quiet_provider_and_marks_usage_unknown(rig, monkeypatch):
    clock = inject_clock(monkeypatch)
    rig.config.run_timeout_seconds = 0.02
    provider = use_provider(rig, QuietProvider())
    capture = Capture(rig.harness, RunRequest(rig.cid, "Calculate the requested result.")).start()
    try:
        await asyncio.wait_for(provider.started.wait(), 5)
        clock.now = 1.0
        await capture.finish()
        saved = saved_answer(rig, capture)
        assert provider.attempts == 1 and provider.closed.is_set()
        assert provider.body_task.done()
        assert provider.body_task not in asyncio.all_tasks()
        assert saved["budget"]["deadline_reached"]
        assert saved["budget"]["remaining_seconds"] == 0
        assert saved["budget"]["usage"]["unknown_settled_attempts"] == 1
        assert saved["budget"]["usage"]["exact"] is None
        assert next(iter(saved["budget"]["model_receipts"].values()))["outcome"] == "aborted"
        assert "deadline" in saved["notice"].lower()
        assert not provider.final_requests
    finally:
        provider.release.set()
        await capture.close()


@pytest.mark.asyncio
async def test_parallel_budget_denial_settles_started_sibling_and_receipts_before_termination(rig):
    rig.config.run_max_tool_starts = 1
    rig.provider.script = [{"tools": [
        {"tool": "python", "args": {"job": "slow"}},
        {"tool": "python", "args": {"job": "fast"}},
    ]}, {"text": "No repair or final writer may run after the budget denial."}]
    capture = Capture(rig.harness, RunRequest(rig.cid, "Compute these two independent values.")).start()
    try:
        await asyncio.wait_for(rig.compute.started_slow.wait(), 5)
        await capture.until(lambda events: any(e["type"] == "activity" and e["item"].get("tool") == "python"
            and e["item"]["status"] == "failed" for e in events))
        assert rig.compute.calls == ["slow"]
        assert not capture.task.done()
        assert not any(e["type"] == "message_end" for e in capture.events)
        before = [e for e in rig.db.list_session_events(rig.cid) if e["type"] == "tool/result"]
        assert len(before) == 1 and before[0]["data"]["meta"]["code"] == "RUN_BUDGET_EXHAUSTED"
        rig.compute.release_slow.set()
        await capture.finish()
        saved = saved_answer(rig, capture)
        assert len(rig.provider.requests) == 1 and not rig.provider.final_requests
        assert saved["budget"]["tool_starts"] == 1
        log = rig.db.list_session_events(rig.cid)
        results = [e for e in log if e["type"] == "tool/result"]
        assert len(results) == 2
        assert {e["data"]["ok"] for e in results} == {False, True}
        assert len({e["data"]["callId"] for e in results}) == 2
        assert max(e["seq"] for e in results) < next(e["seq"] for e in log if e["type"] == "turn/end")
        denied = next(e["data"] for e in results if not e["data"]["ok"])
        assert denied["meta"]["code"] == "RUN_BUDGET_EXHAUSTED"
        assert denied["meta"]["dispatched"] is False
        succeeded = next(e["data"] for e in results if e["data"]["ok"])
        assert succeeded["output"].startswith("42")
    finally:
        rig.compute.release_slow.set()
        await capture.close()


@pytest.mark.asyncio
async def test_pause_time_consumes_absolute_deadline_without_resume_or_final_request(rig, monkeypatch):
    clock = inject_clock(monkeypatch)
    rig.config.run_timeout_seconds = 10
    provider = use_provider(rig, BoundaryProvider([{"text": "The internal draft is 42."}]))
    capture = Capture(rig.harness, RunRequest(rig.cid, "Calculate and explain the result.")).start()
    try:
        await asyncio.wait_for(provider.boundary_started.wait(), 5)
        assert rig.harness.pause(rig.cid)
        provider.release_boundary.set()
        await capture.until(lambda events: any(e["type"] == "run_state" and e["status"] == "paused" for e in events))
        clock.now = 11.0
        await capture.finish()
        saved = saved_answer(rig, capture)
        assert len(provider.requests) == 1 and not provider.final_requests
        assert saved["budget"]["model_attempts"] == 1
        assert saved["budget"]["deadline_reached"]
        assert "deadline" in saved["notice"].lower()
        assert not any(e["type"] == "run_state" and e["status"] == "running" for e in capture.events)
    finally:
        provider.release_boundary.set()
        await capture.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("session_log", [True, False])
@pytest.mark.parametrize("termination", ["stop", "deadline", "provider_error"])
async def test_interrupted_final_stream_keeps_only_its_public_prefix(rig, monkeypatch, session_log, termination):
    clock = inject_clock(monkeypatch)
    rig.config.session_log_enabled = session_log
    rig.config.run_timeout_seconds = 0.03
    answer = "The computed result is 42. The remaining explanation has not been published yet."
    class InterruptibleFinal(LiveProvider):
        async def stream(self, messages, tools, options):
            async for delta in super().stream(messages, tools, options):
                if termination == "provider_error" and self.final_requests and self.final_started.is_set():
                    raise RuntimeError("Provider disconnected during final writing")
                yield delta

    provider = use_provider(rig, InterruptibleFinal([{"text": answer, "reasoning": REASONING}], gate_final=True))
    capture = Capture(rig.harness, RunRequest(rig.cid, "Calculate and explain this result.")).start()
    try:
        await asyncio.wait_for(provider.final_started.wait(), 5)
        public_prefix = "".join(e["delta"] for e in capture.events if e["type"] == "text_delta")
        assert public_prefix == answer[:17]
        if termination == "stop":
            assert rig.harness.stop(rig.cid)
        elif termination == "deadline":
            clock.now = 1.0
        else:
            provider.release_final.set()
        await capture.finish()
        saved = saved_answer(rig, capture, status="stopped" if termination == "stop" else "error")
        final_parts = [p for p in public_content(saved)["parts"] if p["type"] == "text"]
        assert final_parts == [{"type": "text", "text": public_prefix, "phase": "answer"}]
        assert saved["final_text"] == public_prefix and not saved["final_ready"]
        assert answer[17:] not in json.dumps(public_content(saved))
        assert len(provider.requests) == 2 and len(provider.final_requests) == 1
        receipts = list(saved["budget"]["model_receipts"].values())
        assert [receipt["outcome"] for receipt in receipts] == ["completed", "error" if termination == "provider_error" else "aborted"]
        assert saved["budget"]["usage"]["unknown_settled_attempts"] == 1
    finally:
        provider.release_final.set()
        await capture.close()
