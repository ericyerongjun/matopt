"""Public delivery is a final answer, never the internal agent transcript."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

import app.api.conversations as conversations_api
from app.agent.events import Artifact, ToolResult
from app.agent.harness import AgentHarness, RunRequest, _extract_verdict
from app.agent.presentation import public_content
from app.agent.runs import RunManager
from app.agent.session.repair import recovered_content
from app.agent.tools.base import Tool, ToolRegistry
from app.agent.tools.ui_tools import RenderUITool
from app.agent.workspace import Workspace
from app.config import settings
from app.llm.base import StreamDelta
from app.main import app
from app.storage.db import Database
from tests.fake_provider import FakeProvider, FakeRegistry


class OutputTool(Tool):
    name = "output"
    description = "Produce an output file."
    parameters = {"type": "object", "properties": {"value": {"type": "integer"}}}

    async def run(self, ctx, value=1, filename="result.json"):
        return ToolResult(ok=True, output="PRIVATE TOOL OUTPUT", artifacts=[
            Artifact(kind="json", name=filename, path="outputs/" + filename, data={"value": value}),
        ])


class ReadSource(Tool):
    name = "read_file"
    description = "Read source."

    async def run(self, ctx, **kwargs):
        return ToolResult(ok=True, output="SOURCE DOCUMENT: The measured length is 17 cm.")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    db = Database(tmp_path / "public.sqlite")
    config = settings.model_copy(update={"data_dir": tmp_path, "skill_auto_activate": False,
        "lambda_enabled": False, "diagnostics_enabled": False, "verify_rounds": 2})
    provider = FakeProvider([])
    harness = AgentHarness(db, SimpleNamespace(peek=lambda cid: None), config, FakeRegistry(provider),
                           ToolRegistry([OutputTool(), ReadSource(), RenderUITool()]))
    cid = db.create_conversation()["id"]
    rt = SimpleNamespace(db=db, harness=harness, workspace=lambda cid: Workspace(cid, config.workspace_dir))
    rt.runs = RunManager(db, harness)
    monkeypatch.setattr(conversations_api, "get_runtime", lambda: rt)
    yield SimpleNamespace(db=db, config=config, provider=provider, harness=harness, cid=cid, rt=rt)
    db.close()


async def run(rig, script, **kwargs):
    # These regression cases verify the exact-wording/buffered delivery path.
    # The default live phase is exercised by test_agent_activity.py.
    kwargs.setdefault("strict_final", True)
    rig.provider.script = list(script)
    events = [event.to_dict() async for event in rig.harness.run(RunRequest(
        conversation_id=rig.cid, text=kwargs.pop("text", "Solve Questions 1 and 2."), **kwargs))]
    await rig.harness.wait_side_tasks()
    return events


def final(events):
    return next(e["content"] for e in events if e["type"] == "message_end")


@pytest.mark.asyncio
async def test_one_final_delivery_full_revision_and_safe_reload_exports(rig):
    answer = "# Question 1\nThe result is 2.\n\n# Question 2\nThe corrected result is 4."
    events = await run(rig, [
        {"text": "INTERNAL TOOL NARRATION", "reasoning": "PRIVATE THOUGHT", "tool": "output", "args": {"value": 1, "filename": "incorrect-draft.json"}},
        {"text": "# Question 1\nThe result is 2.\n\n# Question 2\nThe result is 3."},
        {"text": "PRIVATE VERIFIER PREAMBLE<report>PRIVATE REPORT</report><verdict>incorrect</verdict>"},
        {"text": "INTERNAL CORRECTION", "tool": "output", "args": {"value": 4}},
        {"text": answer, "reasoning": "PRIVATE REVISION THOUGHT"},
        {"text": "<report>PRIVATE CHECK</report><verdict>correct</verdict>"},
    ], verify=True)
    types = [e["type"] for e in events]
    assert types.count("final_start") == 1
    assert all(types.index("final_start") < i for i, kind in enumerate(types) if kind in {"text_delta", "artifact", "ui_commit"})
    assert "".join(e["delta"] for e in events if e["type"] == "text_delta") == answer
    assert not {"reasoning_delta", "tool_call_start", "tool_call_delta", "tool_call_ready", "tool_result", "verification", "phase", "plan"}.intersection(types)
    assert "PRIVATE" not in json.dumps(events) and "INTERNAL" not in json.dumps(events)
    content = final(events)
    assert content["presentation"] == "final" and "earlier_drafts" not in content
    assert content["work"]["verification"] == {"status": "correct", "rounds": 2}
    assert content["work"]["tool_count"] == 2
    assert next(p["artifact"]["data"] for p in content["parts"] if p["type"] == "artifact") == {"value": 4}
    # Activity can reference files actually created during work, while only the
    # accepted generation is published in the final answer's artifact slots.
    assert "incorrect-draft.json" not in json.dumps(content["parts"])
    assert "COMPLETE corrected" in rig.provider.calls[3][-1]["content"]
    private = rig.db.list_session_events(rig.cid)
    assert "PRIVATE THOUGHT" in json.dumps(private) and "PRIVATE REPORT" in json.dumps(private)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        saved = (await client.get(f"/api/conversations/{rig.cid}")).json()["messages"][-1]["content"]
        assert saved == content
        for fmt in ("markdown", "ipynb"):
            exported = await client.get(f"/api/conversations/{rig.cid}/export", params={"format": fmt})
            assert exported.status_code == 200
            assert "PRIVATE" not in exported.text and "INTERNAL" not in exported.text
            assert "corrected result is 4" in exported.text


@pytest.mark.asyncio
async def test_ui_stays_private_until_final_answer_is_ready(rig):
    surface = {"id": "result", "title": "Result", "fallback": "The value is 4.",
               "nodes": [{"id": "text", "type": "markdown", "props": {"text": "The value is 4."}}]}
    events = await run(rig, [{"tool": "render_ui", "args": surface}, {"text": "The value is 4."}], ui_version=1)
    kinds = [e["type"] for e in events]
    assert "ui_start" not in kinds and "ui_patch" not in kinds
    assert kinds.index("final_start") < kinds.index("ui_commit") < kinds.index("message_end")
    assert sum(p["type"] == "ui" for p in final(events)["parts"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("greeting", ["hi", "Thanks!", "你好"])
async def test_verification_skips_trivial_messages(rig, greeting):
    events = await run(rig, [{"text": "Hello."}, {"text": "Must not be called."}], text=greeting, verify=True)
    assert len(rig.provider.calls) == 1
    assert final(events)["work"]["verification"] == {"status": "skipped", "rounds": 0}


@pytest.mark.asyncio
async def test_verifier_has_source_without_requiring_a_second_tool_call(rig):
    events = await run(rig, [
        {"tool": "read_file", "args": {}}, {"text": "The measured length is 17 cm."},
        {"text": "<report>Checked the source.</report><verdict>correct</verdict>"},
    ], verify=True)
    verifier_prompt = rig.provider.calls[-1][-1]["content"]
    assert "SOURCE DOCUMENT: The measured length is 17 cm." in verifier_prompt
    assert final(events)["work"]["tool_count"] == 1
    assert final(events)["work"]["verification"]["status"] == "correct"


@pytest.mark.asyncio
async def test_uncertain_stops_and_incorrect_at_cap_never_revises_unchecked(rig):
    events = await run(rig, [{"text": "Candidate answer."}, {"text": "<verdict>uncertain</verdict>"},
                             {"text": "Unchecked correction."}], verify=True)
    assert len(rig.provider.calls) == 2
    assert final(events)["work"]["verification"] == {"status": "uncertain", "rounds": 1}
    assert "unverified" in final(events)["notice"]
    rig.config.verify_rounds = 50  # even a legacy large config remains bounded
    before = len(rig.provider.calls)
    events = await run(rig, [{"text": "Candidate answer."}, {"text": "<verdict>incorrect</verdict>"},
                             {"text": "Complete corrected answer."}, {"text": "<verdict>incorrect</verdict>"},
                             {"text": "Unchecked correction."}], verify=True)
    assert len(rig.provider.calls) - before == 4
    assert final(events)["work"]["verification"] == {"status": "limit_reached", "rounds": 2}
    assert "provisional" in final(events)["notice"] and "Unchecked correction" not in json.dumps(events)


@pytest.mark.asyncio
async def test_partial_revision_is_withheld_instead_of_published_as_complete(rig):
    events = await run(rig, [{"tool": "output", "args": {}},
        {"text": "# Q1\nFull solution one.\n# Q2\nFull solution two."},
        {"text": "<report>Q2 has an error.</report><verdict>incorrect</verdict>"},
        {"tool": "render_ui", "args": {"id": "wrong", "title": "Draft view", "fallback": "Draft value.",
         "nodes": [{"id": "text", "type": "markdown", "props": {"text": "Draft value."}}]}},
        {"text": "# Q2\nA correction only."}], verify=True, ui_version=1)
    assert "Full solution" not in json.dumps(events) and "A correction only" not in json.dumps(events)
    assert "could not finish a complete corrected answer" in final(events)["parts"][0]["text"]
    assert final(events)["work"]["verification"]["status"] == "limit_reached"
    assert not any(e["type"] in {"ui_commit", "artifact"} for e in events)
    assert [p["type"] for p in final(events)["parts"]] == ["text"]


@pytest.mark.asyncio
async def test_failure_and_cancel_do_not_publish_buffered_internal_text(rig):
    class FailingProvider(FakeProvider):
        async def stream(self, messages, tools, options):
            yield StreamDelta(kind="reasoning", text="PRIVATE THOUGHT")
            yield StreamDelta(kind="text", text="PRIVATE UNFINISHED DRAFT")
            raise RuntimeError("PRIVATE FAILURE DETAIL")
    rig.harness.providers = FakeRegistry(FailingProvider([]))
    events = [e.to_dict() async for e in rig.harness.run(RunRequest(rig.cid, "work"))]
    assert final(events)["status"] == "error" and final(events)["parts"] == []
    assert "PRIVATE" not in json.dumps(events)
    assert [e["type"] for e in events][-2:] == ["message_end", "error"]
    assert sum(e["type"] == "error" for e in events) == 1

    class WaitingProvider(FakeProvider):
        started = asyncio.Event()
        release = asyncio.Event()
        async def stream(self, messages, tools, options):
            yield StreamDelta(kind="reasoning", text="PRIVATE THOUGHT")
            yield StreamDelta(kind="text", text="PRIVATE UNFINISHED DRAFT")
            self.started.set()
            await self.release.wait()
            yield StreamDelta(kind="finish", finish_reason="stop")
    provider = WaitingProvider([])
    rig.harness.providers = FakeRegistry(provider)
    async def consume():
        return [e.to_dict() async for e in rig.harness.run(RunRequest(rig.cid, "work"))]
    task = asyncio.create_task(consume())
    await provider.started.wait()
    rig.harness.stop(rig.cid)
    provider.release.set()
    events = await asyncio.wait_for(task, 2)
    assert events[-1]["type"] == "stopped" and final(events)["parts"] == []
    assert "PRIVATE" not in json.dumps(events)


@pytest.mark.asyncio
async def test_verification_failure_preserves_candidate_with_honest_notice(rig):
    class FailingCheck(FakeProvider):
        async def stream(self, messages, tools, options):
            if self.calls:
                raise RuntimeError("PRIVATE VERIFIER FAILURE")
            async for delta in super().stream(messages, tools, options):
                yield delta
    rig.provider = FailingCheck([{"text": "Candidate answer."}])
    rig.harness.providers = FakeRegistry(rig.provider)
    events = await run(rig, [{"text": "Candidate answer."}], verify=True)
    content = final(events)
    assert content["parts"][0]["text"] == "Candidate answer."
    assert content["work"]["verification"]["status"] == "error" and "unverified" in content["notice"]
    assert "PRIVATE" not in json.dumps(events)


@pytest.mark.asyncio
async def test_legacy_partial_revision_and_diagnostics_default_gate(rig):
    original = {"status": "complete", "parts": [
        {"type": "reasoning", "text": "PRIVATE THOUGHT"},
        {"type": "text", "text": "PRIVATE TOOL NARRATION", "phase": "answer"},
        {"type": "tool_call", "name": "python", "arguments": {"code": "PRIVATE CODE"}, "result": {"output": "PRIVATE OUTPUT"}},
        {"type": "text", "text": "# Q1\nOriginal full solution.\n# Q2\nOriginal conclusion.", "phase": "answer"},
        {"type": "phase", "phase": "verify"},
        {"type": "text", "text": "PRIVATE VERIFIER PREAMBLE", "phase": "verify"},
        {"type": "verification", "verdict": "incorrect", "report": "PRIVATE REPORT"},
        {"type": "phase", "phase": "revise"},
        {"type": "text", "text": "Confirmed — the verifier is correct.\n\n# Q2\nCorrected conclusion.", "phase": "revise"},
    ]}
    rig.db.add_message(rig.cid, "assistant", original)
    projected = public_content(original)
    assert projected["parts"][0]["text"].startswith("# Q2")
    assert projected["earlier_drafts"] == ["# Q1\nOriginal full solution.\n# Q2\nOriginal conclusion."]
    assert "PRIVATE" not in json.dumps(projected)
    assert rig.db.list_messages(rig.cid)[0]["content"] == original
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for suffix in ("events", "requests/0", "trajectory"):
            assert (await client.get(f"/api/conversations/{rig.cid}/{suffix}")).status_code == 404
        for suffix in ("usage", "stats", "context"):
            assert (await client.get(f"/api/conversations/{rig.cid}/{suffix}")).status_code == 200
        saved = (await client.get(f"/api/conversations/{rig.cid}")).json()["messages"][0]["content"]
        assert saved == projected
        exported = await client.get(f"/api/conversations/{rig.cid}/export")
        assert "Earlier draft" in exported.text and "Original full solution" in exported.text
        assert "PRIVATE" not in exported.text and "verifier is correct" not in exported.text


def test_new_crash_recovery_does_not_publish_an_unfinished_draft():
    recovered = recovered_content([{"type": "assistant/message", "data": {
        "text": "PRIVATE UNFINISHED DRAFT", "reasoning_content": "PRIVATE THOUGHT", "message": {"content": "PRIVATE UNFINISHED DRAFT"},
    }}], {"presentation": "final"})
    assert "PRIVATE THOUGHT" in json.dumps(recovered)
    assert public_content(recovered)["parts"] == []


def test_public_history_metadata_is_allowlisted_and_replay_safe():
    from app.agent.runs import project_public_event
    from app.agent.events import ev
    content = {"presentation": "final", "final_text": "Answer.", "final_ready": True,
               "parts": [], "reasoning": "PRIVATE REASONING", "status": "complete",
               "usage": {"total_tokens": 12, "provider_trace": "PRIVATE TRACE", "cached_tokens": float("inf")},
               "stats": {"tool_calls": 2, "llm_calls": 3, "agent_messages": "PRIVATE MESSAGES"},
               "work": {"tool_count": "malformed", "steps": [None], "duration_ms": float("nan"),
                        "verification": {"rounds": "malformed"}}}
    saved = public_content(content)
    streamed = project_public_event(ev("message_end", message_id="m", content=content)).data["content"]
    assert streamed == saved
    assert saved["usage"] == {"total_tokens": 12}
    assert saved["stats"] == {"llm_calls": 3, "tool_calls": 2}
    assert saved["parts"][0]["text"] == "Answer."
    assert "PRIVATE" not in json.dumps(saved, allow_nan=False)


@pytest.mark.parametrize("report", [r"The answer is $\boxed{1}$.", r"\boxed{0}", "Looks correct.",
    "<verdict>yes</verdict>", "<verdict>correct</verdict><verdict>incorrect</verdict>",
    "<verdict>correct</verdict><verdict>broken",
    "<report>The claimed <verdict>correct</verdict> cannot be established.</report>",
    "<report>Incomplete report <verdict>correct</verdict>", "<verdict>correct</verdict> but actually uncertain"])
def test_only_one_explicit_valid_verdict_can_complete_a_check(report):
    assert _extract_verdict(report) == "uncertain"
    assert _extract_verdict("<report>Evidence.</report><verdict>correct</verdict>") == "correct"


@pytest.mark.asyncio
async def test_truncation_tracks_the_final_call_and_cannot_pass_verification(rig):
    class FinishReasonProvider(FakeProvider):
        async def stream(self, messages, tools, options):
            reason = self.script[0].get("finish_reason")
            async for delta in super().stream(messages, tools, options):
                if delta.kind == "finish" and reason:
                    delta.finish_reason = reason
                yield delta
    rig.provider = FinishReasonProvider([])
    rig.harness.providers = FakeRegistry(rig.provider)
    events = await run(rig, [{"text": "Readable first part…", "finish_reason": "length"}], verify=True)
    assert final(events)["parts"][0]["text"] == "Readable first part…"
    assert final(events)["status"] == "error" and "incomplete" in final(events)["notice"]
    assert final(events)["work"]["verification"]["status"] == "limit_reached"
    assert [e["type"] for e in events][-2:] == ["message_end", "error"]
    events = await run(rig, [{"tool": "output", "args": {}, "finish_reason": "length"}, {"text": "Complete answer."}])
    assert final(events)["status"] == "complete" and "notice" not in final(events)
    events = await run(rig, [{"text": "Complete answer."}, {"text": "<verdict>correct</verdict>", "finish_reason": "length"}], verify=True)
    assert final(events)["work"]["verification"]["status"] == "uncertain"
    assert "unverified" in final(events)["notice"]


def test_output_artifact_dedup_uses_latest_data():
    content = {"presentation": "final", "final_text": "Answer", "final_ready": True, "parts": [
        {"type": "artifact", "artifact": {"kind": "json", "name": "result.json", "data": {"value": 1}}},
        {"type": "artifact", "artifact": {"kind": "json", "name": "result.json", "data": {"value": 4}}},
    ]}
    assert public_content(content)["parts"][-1]["artifact"]["data"] == {"value": 4}


@pytest.mark.asyncio
@pytest.mark.parametrize("user_request,answer", [
    (
        "Explain the decision tree using a diagram, table and equation.",
        "# Decision tree\n\n![Decision tree](figures/tree.png)\n\n"
        "```\nRoot\n|-- A: 17\n`-- B: 23\n```\n\n"
        "| Branch | Value |\n| --- | ---: |\n| A | 17 |\n| B | 23 |\n\n"
        "The total is $17 + 23 = 40$.\n\n$$P(A) = \\frac{17}{40}$$\n",
    ),
    (
        "Give me the literal Markdown source for a copyable decision-tree template.",
        "````markdown\n# Decision tree\n\n![Decision tree](figures/tree.png)\n\n"
        "```text\nRoot\n|-- A: 17\n`-- B: 23\n```\n\n"
        "| Branch | Value |\n| --- | ---: |\n| A | 17 |\n| B | 23 |\n\n"
        "The total is $17 + 23 = 40$.\n\n$$P(A) = \\frac{17}{40}$$\n````\n",
    ),
], ids=["rendered-answer-boundaries", "literal-markdown-source"])
async def test_public_delivery_preserves_markdown_and_source_exactly(rig, user_request, answer):
    events = await run(rig, [{"text": answer}], text=user_request)
    assert "".join(e["delta"] for e in events if e["type"] == "text_delta") == answer
    text_parts = [p for p in final(events)["parts"] if p["type"] == "text"]
    assert len(text_parts) == 1 and text_parts[0]["text"] == answer
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        saved = (await client.get(f"/api/conversations/{rig.cid}")).json()["messages"][-1]["content"]
        assert next(p["text"] for p in saved["parts"] if p["type"] == "text") == answer
        exported = await client.get(f"/api/conversations/{rig.cid}/export", params={"format": "markdown"})
        assert exported.status_code == 200 and answer in exported.text
