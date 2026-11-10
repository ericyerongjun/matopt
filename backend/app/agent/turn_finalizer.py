"""Seals a claimed turn: one saved assistant message, a closed session turn, the terminal events.

Runs from the turn's ``finally``, so it handles every ending (complete,
stopped, failed, out of budget) and a turn that failed during setup, when the
tool context or the model options may not exist yet.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

from app.agent import otel
from app.agent.events import ev
from app.agent.presentation import public_content, public_work
from app.agent.turn_state import EmitFn, RunRequest, TurnState

logger = logging.getLogger(__name__)


@dataclass
class TurnFinalizer:
    harness: Any
    state: TurnState
    req: RunRequest
    cid: str
    message_id: str
    internal_events: bool
    emit: EmitFn                 # the turn's public channel, not the projector
    conv: dict
    ctx: Any = None              # ToolContext, absent if setup failed before it was built
    options: Optional[Any] = None

    async def finish(self, status: str) -> None:
        harness, state, req, cid, message_id = self.harness, self.state, self.req, self.cid, self.message_id
        internal_events, emit, conv, ctx, options = self.internal_events, self.emit, self.conv, self.ctx, self.options
        state.work["status"] = status
        state.work["tool_count"] = len(state.activity.calls)
        await state.activity.finish(status)
        state.transcript.stop_running()
        for step in state.work["steps"]:
            if step["status"] == "running":
                step["status"] = "complete" if status == "complete" else "error"
        if state.subagents is not None:
            await state.subagents.close_all()
        tracked = ctx.state.get("turn_diff") if ctx else None
        changes = await asyncio.to_thread(tracked.render) if tracked is not None else {"files": []}
        content = {
            "parts": state.parts, "model": options.model if options else conv.get("model"),
            "provider": state.provider_id, "reasoning": options.reasoning if options else conv.get("reasoning"),
            "usage": state.usage, "status": status, "plan": ctx.state.get("plan", []) if ctx else [],
            "stats": {"llm_calls": state.llm_calls, "tool_calls": state.tool_calls,
                      "verification_checks": state.verification_checks},
            "presentation": "final", "final_text": state.final_text, "final_ready": state.final_ready,
            "transcript": state.transcript.snapshot(),
            "work": public_work(state.work),
            "output_generation": state.output_generation, "publish_outputs": state.publish_outputs,
            "strict_final": req.strict_final,
            "budget": state.run_budget.snapshot(),
        }
        if state.notice:
            content["notice"] = state.notice
        if state.turn:
            content["turn"] = state.turn
        if state.mode is not None:
            content["mode"] = state.mode.public()
        if changes["files"]:
            # The file list only; the diff text is served by the turn diff endpoint.
            content["changes"] = {"turn": state.turn, "files": changes["files"]}
        if state.session is not None and state.turn:
            state.session.close_open_step("aborted" if status == "stopped" else "error")
            state.session.append_ui_message("assistant", content, message_id)
            harness._persist_steers(cid, state)
            reason = state.end_reason or ("completed" if status == "complete" else "aborted" if status == "stopped" else "error")
            if changes["files"]:
                state.session.append("workspace/changes", {"turn": state.turn, **changes})
            state.session.append("turn/end", {"turn": state.turn, "reason": {"kind": reason}})
            await state.session.flush()
            try:
                otel.export_turn(cid, state.session.events_view, state.turn)
            except Exception:  # noqa: BLE001 - telemetry must never fail a turn
                logger.warning("OpenTelemetry export failed", exc_info=True)
        elif state.session is None:
            harness.db.add_message(cid, "assistant", content, message_id=message_id)
            harness._persist_steers(cid, state)
        checkpoint_seq = state.session.last_seq if state.session else None
        presentation = public_content(content)
        if not internal_events:
            await emit(ev("work", work=presentation["work"]))
            if changes["files"] and state.turn:
                await emit(ev("workspace_changes", turn=state.turn, files=changes["files"]))
            if not state.final_started:
                await emit(ev("final_start", message_id=message_id, mode="buffered"))
            # These are chunks of the checked, buffered answer. No
            # additional generation or simulated token timing occurs.
            for part in presentation["parts"]:
                if part["type"] == "text" and not state.final_started:
                    for offset in range(0, len(part["text"]), 4096):
                        await emit(ev("text_delta", delta=part["text"][offset:offset + 4096]))
                elif part["type"] == "ui":
                    await emit(ev("ui_commit", surface=part["surface"]))
                elif part["type"] == "artifact":
                    await emit(ev("artifact", artifact=part["artifact"]))
                elif part["type"] == "options":
                    await emit(ev("options", items=part["items"]))
        await emit(ev("message_end", message_id=message_id, status=status,
                               content=content if internal_events else presentation, seq=checkpoint_seq))
        if status == "stopped":
            await emit(ev("stopped", conversation_id=cid, message_id=message_id, seq=checkpoint_seq))
        elif status == "complete":
            await emit(ev("done", conversation_id=cid, message_id=message_id, seq=checkpoint_seq))
        elif not internal_events:
            await emit(ev("error", message=state.notice or "The answer could not be completed. Please try again.",
                                   public_message=state.notice, conversation_id=cid, message_id=message_id,
                                   seq=checkpoint_seq))
