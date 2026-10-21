"""Summarising compaction keeps whole recent turns and replaces older ones once."""
import pytest

from app.agent import compaction
from app.agent.session import derive_messages
from app.storage.db import Database

from tests.fake_provider import FakeProvider
from tests.test_session_harness import collect, make_harness


def turn(question: str, answer: str, size: int = 0) -> list[dict]:
    return [{"role": "user", "content": "<turn-context>\nfiles\n</turn-context>"},
            {"role": "user", "content": question},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
                                                                 "function": {"name": "python", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "x" * size},
            {"role": "assistant", "content": answer}]


def test_cut_points_keep_turn_context_with_its_request_and_the_latest_turn():
    history = [{"role": "system", "content": "s"}, *turn("q1", "a1", 50_000), *turn("q2", "a2", 50_000), *turn("q3", "a3")]
    assert compaction.turn_starts(history) == [1, 6, 11]
    assert compaction.plan_cut(history, 40_000) == 11
    assert compaction.plan_cut(history, 200_000) == 6
    assert compaction.plan_cut(history[:6], 10) is None


def test_rolling_summary_is_folded_and_digest_keeps_visible_text():
    summary = compaction.summary_message("Earlier: mean was 4.2", turns=2, model_written=True)
    history = [{"role": "system", "content": "s"}, summary, *turn("q2", "a2"), *turn("q3", "a3")]
    assert compaction.turn_starts(history) == [2, 7]
    span = history[1:7]
    assert "[Earlier summary]" in compaction.render_span(span)
    text = compaction.digest(span)
    assert not text.startswith("Harness-provided context")
    assert "Earlier: mean was 4.2" in text and "User: q2" in text and "Assistant: a2" in text
    assert "<turn-context>" not in text


def test_render_span_bounds_long_tool_output():
    rendered = compaction.render_span(turn("q", "a", 50_000))
    assert len(rendered) < 10_000 and "characters omitted" in rendered


class SummarisingProvider(FakeProvider):
    def __init__(self, script):
        super().__init__(script)
        self.summary_requests = []

    async def complete(self, messages, options):
        from app.llm.base import CompletionResult
        if messages and messages[0]["content"] == compaction.COMPACTION_PROMPT:
            self.summary_requests.append(messages)
            return CompletionResult("## User goals\nFirst question answered: 42.",
                                    {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120})
        return await super().complete(messages, options)


@pytest.mark.asyncio
async def test_harness_replaces_old_turns_with_a_logged_summary_that_survives_later_turns(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    long_answer = "detail " * 3_000
    provider = SummarisingProvider([{"text": long_answer}] * 3 + [{"text": "fourth answer"}])
    harness = make_harness(db, provider, tmp_path, history_char_budget=70_000)
    events = await collect(harness, conversation_id=None, text="first question")
    cid = events[0]["conversation_id"]
    for text in ("second question", "third question", "fourth question"):
        await collect(harness, conversation_id=cid, text=text)

    assert len(provider.summary_requests) == 1
    request = provider.calls[-1]
    summaries = [m for m in request if compaction.is_summary(m)]
    assert len(summaries) == 1 and request[1] == summaries[0]
    assert "First question answered: 42." in summaries[0]["content"]
    assert "first question" not in [m.get("content") for m in request]
    assert request[-1]["content"] == "fourth question"
    rows = db.list_session_events(cid)
    tasks = [e["data"] for e in rows if e["type"] == "llm/side-task" and e["data"].get("task") == "compaction"]
    assert [t["status"] for t in tasks] == ["started", "completed"]
    assert any(compaction.is_summary(m) for m in derive_messages(rows))
    db.close()


@pytest.mark.asyncio
async def test_failed_summariser_falls_back_to_digest(tmp_path):
    class FailingProvider(FakeProvider):
        async def complete(self, messages, options):
            if messages and messages[0]["content"] == compaction.COMPACTION_PROMPT:
                raise RuntimeError("summariser down")
            return await super().complete(messages, options)

    db = Database(tmp_path / "test.sqlite")
    long_answer = "detail " * 3_000
    provider = FailingProvider([{"text": long_answer}, {"text": long_answer}, {"text": "third answer"}])
    harness = make_harness(db, provider, tmp_path, history_char_budget=50_000)
    events = await collect(harness, conversation_id=None, text="first question")
    cid = events[0]["conversation_id"]
    for text in ("second question", "third question"):
        await collect(harness, conversation_id=cid, text=text)
    summary = next(m for m in provider.calls[-1] if compaction.is_summary(m))
    assert "automatic digest" in summary["content"] and "User: first question" in summary["content"]
    db.close()
