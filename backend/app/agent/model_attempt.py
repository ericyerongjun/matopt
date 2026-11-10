"""One model step: dispatch, stream, record, and retry while nothing public was sent.

Every dispatch is admitted against the run budget and durably recorded as its
own attempt before and after it runs, so a retry or a crash never hides a
billed request. ``run`` returns the first attempt that completes.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from typing import Any

from app.agent.budget import BudgetExhausted, RunBudget
from app.agent.events import ev
from app.agent.session.events import snapshot_json
from app.agent.session.stream import AssistantStreamAccumulator
from app.agent.turn_state import BudgetExceeded, ContextOverflow, EmitFn, Stopped, TurnState
from app.llm.base import ChatOptions, ToolCallSpec
from app.llm.retry import IncompleteStream, backoff_delay, classify

logger = logging.getLogger(__name__)


class ModelAttempt:
    def __init__(self, harness: Any, provider: Any, options: ChatOptions, history: list[dict], schemas: list[dict] | None,
                 emit: EmitFn, state: TurnState, cancel: asyncio.Event):
        self.harness = harness
        self.provider = provider
        self.options = options
        self.history = history
        self.schemas = schemas
        self.emit = emit
        self.state = state
        self.cancel = cancel

    async def run(self) -> tuple[str, str, list[ToolCallSpec]]:
        harness, provider, options, history = self.harness, self.provider, self.options, self.history
        schemas, emit, state, cancel = self.schemas, self.emit, self.state, self.cancel
        # Freeze every component before dispatch; adapters receive detached data.
        frozen_history = snapshot_json(history)
        frozen_schemas = snapshot_json(schemas) if schemas else None
        frozen_options = snapshot_json(dataclasses.asdict(options))
        config = {"provider": state.provider_id or provider.id, **frozen_options}
        attempts = 0
        while True:
            ledger = state.session if state.session is not None else state.spend_log
            if ledger is not None:
                from app.agent.session.telemetry import budget_error
                budget_message = budget_error(ledger.events_view, state.turn, harness.settings)
                if budget_message:
                    raise BudgetExceeded(budget_message)
            attempts += 1
            # The admission id counts every dispatch in this state, not just this
            # call's attempts: a step re-dispatched after context-overflow
            # compaction must not reuse the id the rejected request spent.
            state.model_dispatches += 1
            budget = state.run_budget
            admission_id = f"{state.admission_prefix}:{state.turn}:{state.step}:{state.model_dispatches}"
            if budget is not None:
                admission = budget.admit_model(admission_id, source="native:" + state.phase,
                    provider=state.provider_id or provider.id, model=options.model,
                    final=state.final_streaming)
                if not admission.is_new:
                    raise RuntimeError("A model attempt cannot be dispatched twice.")
            state.llm_calls += 1
            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            pending: dict[int, dict] = {}
            stream = AssistantStreamAccumulator()
            started = time.time()
            first_token = None
            usage = None
            finish_reason = None
            attempt_recorded = False
            budget_outcome = "error"
            public_output = False  # once visible, a second attempt would duplicate it
            delta_stream = None
            if state.session is not None:
                state.session.sync_history(frozen_history, turn=state.turn, step=state.step, phase=state.phase)
                header = {"config": config}
                if frozen_schemas:
                    header["tools"] = frozen_schemas
                state.session.append("request/header", {"turn": state.turn, "step": state.step,
                    "attempt": attempts, "header": header, "reason": "dispatch"})
                state.session.append("request/context", {"turn": state.turn, "step": state.step,
                    "provider": state.provider_id or provider.id, "model": options.model, "phase": state.phase})
                request = state.session.current_request()
                if request != {"messages": frozen_history, "tools": frozen_schemas, "config": config}:
                    raise AssertionError("Model-visible request differs from the durable session log")
                await state.session.flush()

            def attempt_data() -> dict:
                now = time.time()
                data = {"turn": state.turn, "step": state.step, "attempt": attempts, "phase": state.phase,
                        "provider": state.provider_id or provider.id, "model": options.model,
                        "reasoning": options.reasoning, "stream": stream.finish(), "started_at": started,
                        "completed_at": now, "duration_ms": (now - started) * 1000}
                if first_token is not None:
                    data["first_token_at"] = first_token
                    data["ttft_ms"] = (first_token - started) * 1000
                if usage is not None:
                    data["usage"] = usage
                return data

            async def book_child_attempt(data: dict, outcome: str, error: dict | None = None) -> None:
                """A sub-agent's attempt, in the parent's log so cost caps and usage see it."""
                if state.spend_log is None:
                    return
                state.spend_log.append("llm/side-task", {
                    "task": "subagent", "task_id": admission_id, "status": outcome, "turn": state.turn, "step": state.step,
                    "provider": data["provider"], "model": data["model"], "started_at": data["started_at"],
                    "completed_at": data["completed_at"], "duration_ms": data["duration_ms"], "usage": data.get("usage"),
                    **({"error": error} if error else {})})
                await state.spend_log.flush()

            try:
                source = provider.stream(snapshot_json(frozen_history), snapshot_json(frozen_schemas),
                                         ChatOptions(**snapshot_json(frozen_options)))
                delta_stream = _controlled_deltas(source, cancel, budget)
                async for delta in delta_stream:
                    if cancel.is_set():
                        break
                    if state.final_streaming and state.context is not None:
                        await harness._control_boundary(state.context, emit)
                    timestamp = time.time()
                    if delta.kind in ("text", "reasoning", "tool_call") and first_token is None:
                        first_token = timestamp
                    if delta.kind == "text":
                        stream.push(timestamp, {"type": "text-delta", "index": 0, "text": delta.text})
                        text_parts.append(delta.text)
                        public_output = public_output or state.final_streaming
                        await emit(ev("text_delta", delta=delta.text))
                    elif delta.kind == "reasoning":
                        stream.push(timestamp, {"type": "reasoning-delta", "index": 0, "text": delta.text})
                        reasoning_parts.append(delta.text)
                        await emit(ev("reasoning_delta", delta=delta.text))
                    elif delta.kind == "tool_call" and delta.tool_call is not None:
                        tc = delta.tool_call
                        slot = pending.get(tc.index)
                        if slot is None:
                            slot = {"id": tc.id or f"call_{state.llm_calls}_{tc.index}", "name": tc.name or "", "arguments": ""}
                            pending[tc.index] = slot
                            await emit(ev("tool_call_start", id=slot["id"], name=slot["name"]))
                        else:
                            if tc.id and tc.id != slot["id"]:
                                slot["id"] = tc.id
                            if tc.name and not slot["name"]:
                                slot["name"] = tc.name
                                await emit(ev("tool_call_start", id=slot["id"], name=slot["name"]))
                        chunk = {"type": "tool-call-delta", "index": tc.index, "id": tc.id or "",
                                 "argumentsDelta": tc.arguments or ""}
                        if tc.name is not None:
                            chunk["name"] = tc.name
                        stream.push(timestamp, chunk)
                        if tc.arguments:
                            slot["arguments"] += tc.arguments
                            await emit(ev("tool_call_delta", id=slot["id"], delta=tc.arguments))
                            if state.ui_compiler is not None:
                                preview = state.ui_compiler.feed(slot["name"], slot["id"], slot["arguments"])
                                if preview is not None:
                                    public_output = True
                                    await emit(preview)
                    elif delta.kind == "usage" and delta.usage is not None:
                        # Samples are cumulative for one attempt: the last one
                        # replaces earlier samples rather than being summed.
                        usage = snapshot_json(delta.usage)
                        stream.push(timestamp, {"type": "usage", "usage": usage})
                        await emit(ev("usage", **usage))
                    elif delta.kind == "finish":
                        finish_reason = delta.finish_reason
                        stream.push(timestamp, {"type": "finish", "reason": finish_reason})
                if finish_reason in {"error", "aborted", "insufficient_system_resource"}:
                    raise RuntimeError(f"Provider ended the stream with {finish_reason}")
                if finish_reason is None and not cancel.is_set():
                    raise IncompleteStream("The model stream ended without a finish reason.")
                if finish_reason in {"length", "max_tokens", "max-tokens"}:
                    state.end_reason = "max-tokens"
                state.last_finish_reason = finish_reason
                calls = [ToolCallSpec(id=value["id"], name=value["name"], arguments=value["arguments"])
                         for _, value in sorted(pending.items())]
                text, reasoning = "".join(text_parts), "".join(reasoning_parts)
                budget_outcome = "aborted" if cancel.is_set() else "completed"
                data = attempt_data()
                if cancel.is_set():
                    data["interrupted"] = True
                if state.session is not None:
                    if text or reasoning or calls or not cancel.is_set():
                        state.session.append("assistant/message", {**data,
                            "message": provider.assistant_message(text, reasoning, calls),
                            "text": text, "reasoning_content": reasoning,
                            "finish_reason": finish_reason}, surface_op="append")
                    else:
                        state.session.append("assistant/attempt", {**data, "status": "aborted"})
                    attempt_recorded = True
                    await state.session.flush()
                elif state.spend_log is not None:
                    attempt_recorded = True
                    await book_child_attempt(data, budget_outcome)
                if usage is not None:
                    for key, value in usage.items():
                        if type(value) in (int, float):
                            state.usage[key] = state.usage.get(key, 0) + value
                    if type(usage.get("prompt_tokens")) is int:
                        state.last_prompt_tokens = usage["prompt_tokens"]
                        state.last_usage_len = len(frozen_history)
                if attempts > 1 and state.activity is not None:
                    await state.activity.item(f"{state.activity.id}:retry:{state.step}", status="completed",
                                              summary="The model connection recovered.")
                return text, reasoning, calls
            except BaseException as exc:
                if attempt_recorded:
                    # A storage/checkpoint failure after the completed attempt
                    # must not invent a second billed provider attempt.
                    raise
                budget_outcome = "aborted" if isinstance(exc, (asyncio.CancelledError, Stopped, BudgetExhausted)) else "error"
                data = attempt_data()
                data.update(status=budget_outcome,
                            error={"name": type(exc).__name__, "message": str(exc)})
                if state.session is not None:
                    if isinstance(exc, asyncio.CancelledError) and (text_parts or reasoning_parts or pending):
                        text, reasoning = "".join(text_parts), "".join(reasoning_parts)
                        calls = [ToolCallSpec(id=value["id"], name=value["name"], arguments=value["arguments"])
                                 for _, value in sorted(pending.items()) if value["name"]]
                        state.session.append("assistant/message", {**data, "interrupted": True,
                            "message": provider.assistant_message(text, reasoning, calls),
                            "text": text, "reasoning_content": reasoning}, surface_op="append")
                        if reasoning:
                            state.parts.append({"type": "reasoning", "text": reasoning, "phase": state.phase})
                        if text:
                            state.parts.append({"type": "text", "text": text, "phase": state.phase})
                    else:
                        state.session.append("assistant/attempt", data)
                    await state.session.flush()
                else:
                    await book_child_attempt(data, budget_outcome, data["error"])
                if usage is not None:
                    for key, value in usage.items():
                        if type(value) in (int, float):
                            state.usage[key] = state.usage.get(key, 0) + value
                decision = classify(exc) if isinstance(exc, Exception) else None
                untouched = not text_parts and not reasoning_parts and not pending and not cancel.is_set()
                if (decision is not None and decision.context_overflow and untouched
                        and not isinstance(exc, (BudgetExhausted, Stopped))):
                    raise ContextOverflow(str(exc)) from exc
                max_retries = int(getattr(harness.settings, "llm_max_retries", 4))
                # Partial worker output is private and was never added to the
                # history, so it can be dropped and the step sent again.
                if (decision is not None and not isinstance(exc, (BudgetExhausted, Stopped)) and attempts <= max_retries
                        and decision.retryable and not public_output and not cancel.is_set()):
                    delay = backoff_delay(attempts, float(getattr(harness.settings, "llm_retry_base_delay", 0.2)),
                                          decision.retry_after)
                    logger.warning("Transient LLM error, retrying in %.2fs: %s", delay, exc)
                    if state.session is not None:
                        state.session.append("llm/retry", {"turn": state.turn, "step": state.step,
                            "retry": attempts, "delay_ms": int(delay * 1000), "failure": data["error"]})
                        await state.session.flush()
                    await emit(ev("status", message="Model connection hiccup, retrying…"))
                    if state.activity is not None:
                        await state.activity.item(f"{state.activity.id}:retry:{state.step}", kind="control",
                            parent_id=f"{state.activity.id}:phase:{state.activity.phase}",
                            label="Retrying model connection", purpose="Recover a connection that failed before producing output",
                            status="running", attempt=attempts + 1)
                        await state.activity.progress("The model connection was interrupted before producing output. Retrying.")
                    # A stop during the backoff ends the wait, not just the next attempt.
                    try:
                        await asyncio.wait_for(cancel.wait(), timeout=delay)
                    except asyncio.TimeoutError:
                        pass
                    if cancel.is_set():
                        raise Stopped()
                    if state.session is not None:
                        state.session.append("llm/retry-started", {"turn": state.turn, "step": state.step,
                                                              "retry": attempts})
                    continue
                raise
            finally:
                try:
                    if delta_stream is not None:
                        await delta_stream.aclose()
                finally:
                    if budget is not None:
                        budget.settle_model(admission_id, usage=usage, outcome=budget_outcome)


async def _controlled_deltas(source, cancel: asyncio.Event, budget: RunBudget | None):
    """Wait for actual provider deltas while Stop/deadline can interrupt silence."""
    iterator = source.__aiter__()
    stopped = asyncio.create_task(cancel.wait())
    pending = None
    try:
        while True:
            if cancel.is_set():
                raise Stopped()
            timeout = budget.remaining_timeout() if budget is not None else None
            if pending is None:
                pending = asyncio.create_task(anext(iterator))
            done, _ = await asyncio.wait({pending, stopped}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if stopped in done or cancel.is_set():
                raise Stopped()
            if pending not in done:
                # Recheck through the shared clock so the reason and accounting
                # are identical to admission at every other work boundary.
                budget.remaining_timeout()
                continue
            try:
                yield pending.result()
            except StopAsyncIteration:
                return
            finally:
                pending = None
    finally:
        waits = [task for task in (pending, stopped) if task is not None]
        for task in waits:
            if not task.done():
                task.cancel()
        await asyncio.gather(*waits, return_exceptions=True)
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()
