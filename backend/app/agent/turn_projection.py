"""Folds a turn's internal events into its durable record and its public view.

Tools, the model loop and the verifier all emit through one function. Each
event may update the transcript, the saved message parts, the session log and
the public activity, and only an allowlisted few are passed on to the client.
"""

from __future__ import annotations

from typing import Any

from app.agent.events import Event, ev
from app.agent.presentation import public_work, tool_label
from app.agent.turn_state import EmitFn, TurnState


class TurnProjector:
    def __init__(self, harness: Any, state: TurnState, *, cid: str, message_id: str, strict_final: bool,
                 internal_events: bool, publish: EmitFn):
        self.harness = harness
        self.state = state
        self.message_id = message_id
        self.strict_final = strict_final
        self.internal_events = internal_events
        self.publish = publish
        # The harness updates these two as the turn takes hold of its conversation.
        self.cid = cid
        self.claimed = False

    async def update_work(self, label: str | None = None, item_status: str = "running") -> None:
        if label is not None:
            if label != "Working":
                for step in self.state.work["steps"]:
                    if step["label"] == "Working" and step["status"] == "running":
                        step["status"] = "complete"
            step = next((s for s in self.state.work["steps"] if s["label"] == label), None)
            if step is None:
                self.state.work["steps"].append({"label": label, "status": item_status})
            else:
                step["status"] = item_status
        if not self.internal_events:
            await self.publish(ev("work", work=public_work(self.state.work)))

    async def emit(self, event: Event) -> None:
        if self.state.phase != "verify" and event.type in {"transcript_note", "tool_call_ready", "tool_result"}:
            if event.type == "transcript_note":
                entry = self.state.transcript.note(event.data["step"], event.data["text"])
            elif event.type == "tool_call_ready":
                entry = self.state.transcript.action(self.state.step, event.data["id"],
                    event.data.get("resolved_name") or event.data.get("name", ""), event.data.get("arguments"))
            else:
                entry = self.state.transcript.result(event.data["id"], event.data.get("ok"),
                                                event.data.get("output"), event.data.get("error"))
            if entry is not None and not self.internal_events and not self.strict_final:
                await self.publish(ev("transcript", entry=dict(entry)))
            if event.type == "transcript_note":
                return
        kinds = {"plan": "plan/write", "skills": "skill/activated", "phase": "phase/change",
                 "verification": "verification"}
        if self.state.session is not None and event.type in kinds:
            self.state.session.append(kinds[event.type], {"turn": self.state.turn, "step": self.state.step, **event.data})
        if event.type == "monitor":
            self.state.parts.append({"type": "monitor", **event.data})
        if event.type == "ui_error":
            sid = str(event.data.get("surface_id", ""))
            fallback = event.data.get("fallback")
            if isinstance(fallback, str) and fallback.strip() and self.harness.ui.store.get(self.cid, sid) is None:
                part = self.state.ui_fallbacks.get(sid)
                if part is None:
                    part = {"type": "text", "text": fallback}
                    self.state.ui_fallbacks[sid] = part
                    self.state.parts.append(part)
                else:
                    part["text"] = fallback
                if self.state.session is not None:
                    self.state.session.append("ui/fallback", {"turn": self.state.turn, "step": self.state.step,
                        "message_id": self.message_id, "surface_id": sid, "fallback": fallback}, ignorable=True)
                    await self.state.session.flush()
        if event.type == "ui_commit":
            surface = event.data["surface"]
            repaired = self.state.ui_fallbacks.pop(surface["id"], None)
            if repaired is not None:
                self.state.parts[:] = [p for p in self.state.parts if p is not repaired]
            existing = next((p for p in self.state.parts if p.get("type") == "ui" and p["surface"]["id"] == surface["id"]), None)
            if existing is None:
                self.state.parts.append({"type": "ui", "surface": surface, "phase": self.state.phase,
                                    "publication": self.state.output_generation})
            else:
                existing["surface"] = surface
                existing["phase"] = self.state.phase
                existing["publication"] = self.state.output_generation
            if self.state.session is not None:
                checkpoint = self.state.session.append("ui/surface", {"turn": self.state.turn, "step": self.state.step,
                    "message_id": self.message_id, "surface_id": surface["id"], "revision": surface["revision"],
                    "fallback": surface["fallback"]}, ignorable=True)
                await self.state.session.flush()
                event.seq = checkpoint["seq"]
        if event.type == "message_start":
            await self.publish(event)
            await self.state.activity.set_phase("work")
            await self.state.activity.progress("I’m working on your request.")
            await self.update_work("Working")
            return
        if event.type == "tool_call_ready":
            if (event.data.get("resolved_name") or event.data.get("name")) in {"report_progress", "publish_result"}:
                if self.internal_events:
                    await self.publish(event)
                return
            key = f"{self.state.step}:{event.data['id']}"
            self.state.work_tools[key] = tool_label(event.data.get("name", ""))
            self.state.work["tool_count"] = len(self.state.work_tools)
            await self.state.activity.queued(self.state.step, event.data["id"], event.data.get("resolved_name") or event.data.get("name", ""), event.data.get("arguments"))
            await self.update_work(self.state.work_tools[key])
        elif event.type == "tool_running":
            await self.state.activity.running(self.state.step, event.data["id"])
        elif event.type == "tool_activity_progress":
            await self.state.activity.measured(self.state.step, event.data["id"], event.data.get("progress", {}))
        elif event.type == "tool_result":
            key = f"{self.state.step}:{event.data['id']}"
            await self.state.activity.result(self.state.step, event.data["id"], event.data.get("name", ""),
                event.data.get("ok"), event.data.get("meta") or {}, event.data.get("artifacts") or [])
            if key in self.state.work_tools:
                await self.update_work(self.state.work_tools[key], "complete" if event.data.get("ok") else "error")
        elif event.type == "phase":
            if event.data.get("phase") == "revise" and self.state.result_publisher:
                await self.state.result_publisher.supersede()
            if event.data.get("phase") == "verify" and self.state.activity.phase != "check":
                await self.state.activity.set_phase("check")
            await self.state.activity.progress("Checking the results." if event.data.get("phase") == "verify" else "Correcting an issue found during the checks.")
            await self.update_work("Checking answer" if event.data.get("phase") == "verify" else "Correcting answer")
        elif event.type == "verification":
            verdict = event.data.get("verdict")
            await self.state.activity.item(f"{self.message_id}:phase:check", summary={
                "correct": "The selected checks passed.", "incorrect": "A check found an issue.",
                "uncertain": "The checks were inconclusive."}.get(verdict, "The checks could not finish."))
            await self.update_work("Checking answer", "complete")
        elif event.type == "public_progress" and self.state.phase != "verify":
            await self.state.activity.progress(event.data.get("text", ""))
        elif event.type == "plan" and self.state.phase != "verify":
            await self.state.activity.plan(event.data.get("steps") or [])
        elif event.type == "run_state":
            await self.state.activity.progress("Paused at a safe boundary." if event.data.get("status") == "paused" else "Resuming work.")
        elif event.type == "steer_applied":
            if self.state.result_publisher:
                await self.state.result_publisher.supersede()
            await self.state.activity.progress("Your instruction has been applied to the ongoing task.")
        if event.type == "text_delta" and self.state.final_streaming:
            # Keep the already-public prefix when Stop, a deadline or a
            # provider error interrupts final writing. Private worker and
            # strict-mode drafts never enter this publication boundary.
            self.state.final_text += event.data.get("delta", "")
        if self.internal_events:
            await self.publish(event)
        elif event.type in {"conversation", "usage", "steer_applied", "run_state"}:
            await self.publish(event)
        elif event.type in {"subagent", "approval_request", "approval_resolved", "guardian_review", "mode"}:
            # Public lifecycle facts; runs.project_public_event allowlists their fields.
            await self.publish(event)
        elif event.type == "text_delta" and self.state.final_streaming:
            await self.publish(event)
        elif event.type in {"ui_start", "ui_patch", "ui_commit", "ui_error"} and self.state.phase != "verify" and not self.strict_final:
            # The compiler emits validated native prefixes; mini-apps are
            # published by the service only once complete and committed.
            await self.publish(event)
        elif event.type == "error" and not self.claimed:
            await self.publish(ev("error", message="The answer could not be completed. Please try again."))
