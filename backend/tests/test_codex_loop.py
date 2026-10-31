"""Loop mechanics ported from Codex: retry policy, token estimate, mid-turn compaction."""

import random

import pytest
from types import SimpleNamespace

from app.agent import compaction
from app.agent.harness import TurnState, _estimated_prompt_tokens, _previous_turn_stopped
from app.agent.prompts import build_turn_context
from app.llm.retry import backoff_delay, classify


class StatusError(Exception):
    def __init__(self, status, message="", headers=None):
        super().__init__(message)
        self.status_code = status
        self.response = SimpleNamespace(status_code=status, headers=headers or {})


def test_classify_by_status_and_type():
    assert classify(StatusError(429)).retryable
    assert classify(StatusError(503)).retryable
    assert not classify(StatusError(401, "bad key")).retryable
    assert not classify(StatusError(400, "invalid request")).retryable
    assert classify(RuntimeError("connection unavailable before any response")).retryable
    assert not classify(ValueError("schema mismatch")).retryable


def test_context_overflow_is_reported_not_retried():
    decision = classify(StatusError(400, "This model's maximum context length is 128000 tokens"))
    assert decision.context_overflow and not decision.retryable


def test_retry_after_header_wins_and_backoff_grows():
    decision = classify(StatusError(429, headers={"retry-after": "3"}))
    assert decision.retry_after == 3.0
    assert backoff_delay(1, retry_after=decision.retry_after) == 3.0
    rng = random.Random(0)
    delays = [backoff_delay(n, 0.2, rng=rng) for n in (1, 2, 3, 4)]
    assert all(0.9 * 0.2 * 2 ** (i) <= d <= 1.1 * 0.2 * 2 ** (i) for i, d in enumerate(delays))
    assert backoff_delay(20, 0.2) <= 30.0


def _turn(steps: int, output: str = "x" * 5000) -> list[dict]:
    history = [{"role": "system", "content": "sys"},
               {"role": "user", "content": "<turn-context>"},
               {"role": "user", "content": "Analyse the dataset."}]
    for i in range(steps):
        history.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "python", "arguments": "{}"}}]})
        history.append({"role": "tool", "tool_call_id": f"c{i}", "content": output})
    return history


def test_intra_turn_cut_keeps_request_and_pairs():
    history = _turn(8)
    start, end = compaction.plan_intra_turn_cut(history, target=12_000)
    assert start == 3, "the turn's own request run is never summarised"
    assert history[end]["role"] == "assistant", "the cut never splits a call from its result"
    assert end <= len(history) - 2, "the newest step is kept"


def test_steer_starts_its_own_run_and_is_never_summarised():
    history = _turn(4)
    history.insert(7, {"role": "user", "content": "[Steer] use the log scale"})
    start, _ = compaction.plan_intra_turn_cut(history, target=1)
    assert start == 8


def test_intra_turn_cut_needs_two_steps():
    assert compaction.plan_intra_turn_cut(_turn(1), target=10) is None


def test_step_summary_is_not_a_turn_start():
    history = _turn(4)
    start, end = compaction.plan_intra_turn_cut(history, target=8_000)
    history[start:end] = [compaction.summary_message("did things", turns=0, model_written=True, steps=2)]
    assert "2 earlier step(s) of the current turn" in history[start]["content"]
    assert compaction.turn_starts(history) == [1]
    # A second mid-turn compaction folds the earlier summary into the new span.
    history += _turn(2)[3:]
    again = compaction.plan_intra_turn_cut(history, target=1)
    assert again is not None and compaction.is_summary(history[again[0]])


def test_estimated_prompt_tokens_adds_items_since_last_usage():
    history = _turn(2)
    state = TurnState(last_prompt_tokens=1000, last_usage_len=3)
    assert _estimated_prompt_tokens(history, state) > 1000 + 5000 // 4
    assert _estimated_prompt_tokens(history, TurnState()) == 0


def test_previous_stopped_turn_is_visible_to_the_model():
    events = [{"type": "turn/end", "data": {"turn": 1, "reason": {"kind": "completed"}}},
              {"type": "turn/end", "data": {"turn": 2, "reason": {"kind": "aborted"}}}]
    assert _previous_turn_stopped(events)
    assert not _previous_turn_stopped(events[:1])
    assert "stopped the previous turn" in build_turn_context("files", "", previous_stopped=True)
    assert "stopped the previous turn" not in build_turn_context("files", "")


@pytest.mark.asyncio
async def test_overflow_redispatch_gets_a_fresh_admission(tmp_path):
    """A step re-sent after context-overflow compaction must not reuse the spent admission id."""
    from types import SimpleNamespace
    from app.agent.budget import RunBudget
    from app.agent.harness import AgentHarness, RunRequest
    from app.agent.tools.base import ToolRegistry
    from app.config import settings
    from app.storage.db import Database
    from tests.fake_provider import FakeProvider, FakeRegistry

    class OverflowOnce(FakeProvider):
        def __init__(self):
            super().__init__([{"text": "Recovered answer."}, {"text": "Recovered answer."}])
            self.overflowed = False

        async def stream(self, messages, tools, options):
            if not self.overflowed:
                self.overflowed = True
                raise RuntimeError("This model's maximum context length is 128000 tokens")
            async for delta in super().stream(messages, tools, options):
                yield delta

    db = Database(tmp_path / "o.sqlite")
    config = settings.model_copy(update={"data_dir": tmp_path, "skill_auto_activate": False,
                                         "data_agent_enabled": False, "verify_rounds": 0})
    provider = OverflowOnce()
    harness = AgentHarness(db, SimpleNamespace(peek=lambda cid: None), config, FakeRegistry(provider), ToolRegistry([]))
    cid = db.create_conversation()["id"]
    events = [e.to_dict() async for e in harness.run(RunRequest(cid, "Explain the identity at length."))]
    assert provider.overflowed
    assert not [e for e in events if e["type"] == "error"], [e for e in events if e["type"] == "error"]
    end = next(e for e in events if e["type"] == "message_end")
    assert end["status"] == "complete"
    db.close()
