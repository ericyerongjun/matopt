"""
The agent harness: streams one assistant turn.

    user message ─► [LLM ⇄ tools]* ─► final answer ─► (verify ⇄ revise)? ─► persist

Design notes
- Everything the client needs is emitted as :class:`Event`s through an
  asyncio queue, so tools can push progress while the model is idle.
- The turn is bounded: ``max_steps`` LLM calls, ``max_consecutive_failures``
  failed tool batches; the model is nudged to conclude when limits approach.
- Cancellation is cooperative: ``cancel`` is checked between deltas and tool
  calls, running kernels are interrupted, and partial work is persisted.
- Verification (optional) runs an independent verifier with tools; if it
  reports "incorrect" the solver revises, up to ``verify_rounds`` times.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from app.agent.datasets import datasets_block
from app.agent.events import Event, ToolResult, ev, keepalive
from app.agent.execution import ToolExecutionService, restore_execution_state
from app.agent import compaction, otel
from app.agent.budget import BudgetExhausted, RunBudget
from app.agent.activity import PublicActivity
from app.agent.results import ResultPublisher
from app.agent.session import SessionLog, derive_messages, derive_request
from app.agent.session.derive import fold_surface
from app.agent.session.events import snapshot_json
from app.agent.session.stream import AssistantStreamAccumulator
from app.agent.prompts import (
    FAILURE_NUDGE,
    FINAL_STEP_NUDGE,
    REPEAT_NUDGE,
    REVISION_TEMPLATE,
    TITLE_PROMPT,
    VERIFIER_PROMPT,
    VERIFIER_USER_TEMPLATE,
    build_system_prompt,
    build_turn_context,
)
from app.agent.monitor import next_step_options
from app.agent.loop_policy import FullReadEvidence, Operation, OperationGuard, Outcome
from app.agent.presentation import public_content, public_work, tool_label
from app.agent.scheduler import schedule_calls
from app.agent.skills.loader import SkillRegistry
from app.agent.skills.slash import default_slash_prompt, split_slash_commands
from app.agent.skills.materialize import materialize_skill
from app.agent.skills.router import RoutePlan, SkillRouter
from app.agent.tools.base import Tool, ToolContext, ToolRegistry, truncate_text
from app.agent.tools.router import ToolRouter
from app.agent.tools.read_state import latest_read, parse_read_state_metadata
from app.agent.workspace import Workspace
from app.llm.base import ChatOptions, LLMProvider, ToolCallSpec
from app.llm.catalog import canonical_reasoning
from app.storage.db import Database, new_id

logger = logging.getLogger(__name__)

EmitFn = Callable[[Event], Awaitable[None]]


class Stopped(Exception):
    """Raised inside the loop when the user cancelled the run."""


class BudgetExceeded(BudgetExhausted):
    """Observed spend cannot admit another configured provider dispatch."""

    def __init__(self, message: str):
        super().__init__("observed_spend", limit=None, used=None, detail=message)


class SteeringPending(Exception):
    """Return from an isolated verifier to the worker owning user instructions."""


@dataclass
class RunRequest:
    conversation_id: Optional[str]
    text: str
    attachments: list[dict] = field(default_factory=list)   # [{name, path, url, size}]
    provider_id: Optional[str] = None
    model_id: Optional[str] = None
    reasoning: Optional[str] = None
    verify: bool = False
    temperature: Optional[float] = None
    skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    web_search: bool = True
    ui_version: Optional[int] = None
    strict_final: bool = False  # verify the exact answer text before releasing it
    run_id: Optional[str] = None


@dataclass
class SteerNote:
    text: str
    skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)


REPEAT_LIMIT = 3
SOFT_SUCCESS_TOOLS = frozenset({
    "list_files", "read_file", "grep", "bash", "web_search", "web_fetch",
    "update_plan", "search_skills", "load_skill", "report_progress", "publish_result",
})


@dataclass
class TurnState:
    parts: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    llm_calls: int = 0
    tool_calls: int = 0
    phase: str = "answer"
    plan: list = field(default_factory=list)
    failure_counts: dict[str, int] = field(default_factory=dict)
    blocked_tools: set[str] = field(default_factory=set)
    operation_guard: OperationGuard = field(default_factory=OperationGuard)
    repeat_nudge_sent: bool = False
    # Steers applied *during* this turn, in arrival order. They are persisted
    # only after the assistant message so stored order reflects when they
    # actually happened; see `_drain_steer`.
    applied_steers: list[dict] = field(default_factory=list)
    unapplied_steers: list[dict] = field(default_factory=list)
    work_steps: int = 0
    work_step_limit: int = 0
    session: Optional[SessionLog] = None
    turn: int = 0
    step: int = 0
    provider_id: str = ""
    end_reason: Optional[str] = None
    ui_compiler: Any = None
    ui_fallbacks: dict[str, dict] = field(default_factory=dict)
    final_text: str = ""
    final_ready: bool = False
    notice: Optional[str] = None
    output_generation: int = 0
    publish_outputs: bool = True
    last_finish_reason: Optional[str] = None
    answer_truncated: bool = False
    work: dict = field(default_factory=lambda: {"status": "running", "tool_count": 0,
        "verification": {"status": "not_requested", "rounds": 0}, "steps": []})
    work_tools: dict[str, str] = field(default_factory=dict)
    activity: Any = None
    final_started: bool = False
    final_streaming: bool = False
    context: Any = None
    result_publisher: Any = None
    run_budget: RunBudget | None = None
    verification_checks: int = 0


@dataclass(frozen=True)
class NormalizedCall:
    """A tool call whose arguments were normalized exactly once (plan §3.8c).

    ``arguments`` is the single dict that `_execute_calls` hands to the
    misalignment monitor, the ``tool_call_ready`` event, the persisted
    ``tool_call`` part and the tool handler; ``payload`` is the form the event
    and the log carry. Nothing downstream re-parses ``call.arguments``, so the
    bytes that were inspected are the bytes that ran — no "approve A, run B".
    """

    call: ToolCallSpec
    tool: Optional[Tool] = None
    resolve_error: Optional[str] = None
    arguments: Optional[dict] = None
    error: Optional[str] = None

    @property
    def raw(self) -> str:
        """The model's original argument bytes — kept only for the failure path."""
        return self.call.arguments

    @property
    def payload(self) -> dict:
        """What the event and the persisted part carry: the arguments, or `_raw`."""
        return self.arguments if self.arguments is not None else {"_raw": self.raw}


class AgentHarness:
    def __init__(self, db: Database, kernels, settings, providers, tools: ToolRegistry,
                 skills: Optional[SkillRegistry] = None, tool_router: Optional[ToolRouter] = None,
                 executions: Optional[ToolExecutionService] = None):
        self.db = db
        self.kernels = kernels
        self.settings = settings
        self.providers = providers
        self.tools = tools
        self._owns_executions = executions is None
        self.executions = executions or ToolExecutionService(settings)
        self.skills = skills or SkillRegistry()
        self.skill_router = SkillRouter(
            self.skills,
            auto_activate_max=getattr(settings, "skill_auto_activate_max", 2),
        )
        self.tool_router = tool_router or ToolRouter()
        self._active: dict[str, asyncio.Event] = {}     # conversation_id -> cancel flag
        self._steer: dict[str, asyncio.Queue[SteerNote]] = {}
        self._paused: dict[str, asyncio.Event] = {}
        self._finalizing: set[str] = set()
        self._session_logs: OrderedDict[str, SessionLog] = OrderedDict()
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._session_users: dict[str, int] = {}
        self._side_tasks: set[asyncio.Task] = set()
        from app.ui.service import UIService
        self.ui = UIService(self)

    # ── public API ──────────────────────────────────────────────────────

    def is_running(self, conversation_id: str) -> bool:
        return conversation_id in self._active

    def claim_external_run(self, conversation_id: str) -> asyncio.Event:
        """Synchronously reserve the same lease used by chat turns."""
        from app.ui.store import UIConflict
        if conversation_id in self._active:
            raise UIConflict("This conversation is busy. Wait for the current run to finish.")
        flag = asyncio.Event()
        self._active[conversation_id] = flag
        return flag

    def release_external_run(self, conversation_id: str, flag: asyncio.Event) -> None:
        if self._active.get(conversation_id) is flag:
            self._active.pop(conversation_id, None)
            self._trim_session_cache()

    def stop(self, conversation_id: str) -> bool:
        flag = self._active.get(conversation_id)
        if flag is None:
            return False
        flag.set()
        return True

    def pause(self, conversation_id: str) -> bool:
        flag = self._paused.get(conversation_id)
        if flag is None:
            return False
        flag.set()
        return True

    def resume(self, conversation_id: str) -> bool:
        flag = self._paused.get(conversation_id)
        if flag is None:
            return False
        flag.clear()
        return True

    async def _control_boundary(self, ctx: ToolContext, emit: EmitFn) -> None:
        """Pause between operations; never pretend an in-flight effect is paused."""
        flag = self._paused.get(ctx.conversation_id)
        if ctx.cancel.is_set():
            raise Stopped()
        budget = ctx.state.get("run_budget")
        if budget is not None:
            budget.remaining_timeout()
        if flag is None or not flag.is_set():
            return
        await emit(ev("run_state", status="paused"))
        while flag.is_set() and not ctx.cancel.is_set():
            if budget is not None:
                budget.remaining_timeout()
            try:
                await asyncio.wait_for(ctx.cancel.wait(), .1)
            except asyncio.TimeoutError:
                pass
        if ctx.cancel.is_set():
            raise Stopped()
        await emit(ev("run_state", status="running"))

    async def _open_session(self, conversation_id: str) -> SessionLog:
        lock = self._session_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            log = self._session_logs.get(conversation_id)
            if log is None:
                log = await SessionLog.open(self.db, conversation_id)
                self._session_logs[conversation_id] = log
            self._session_logs.move_to_end(conversation_id)
            self._trim_session_cache()
            return log

    def _trim_session_cache(self) -> None:
        """Keep at most eight idle logs while protecting all live/checkpoint users."""
        for cid, log in list(self._session_logs.items()):
            if len(self._session_logs) <= 8:
                break
            if cid not in self._active and not self._session_users.get(cid) and log.can_evict:
                self._session_logs.pop(cid)
                lock = self._session_locks.get(cid)
                if lock is not None and not lock.locked():
                    self._session_locks.pop(cid)

    def _start_title(self, cid: str, provider, options: ChatOptions, text: str, session: SessionLog | None) -> None:
        # Reserve the lease synchronously, before the background coroutine can
        # yield. A new turn and this side task then share the same live log.
        self._session_users[cid] = self._session_users.get(cid, 0) + 1

        async def work() -> None:
            await self._auto_title(cid, provider, options, text, session)

        task = asyncio.create_task(work())
        self._side_tasks.add(task)

        def completed(done: asyncio.Task) -> None:
            self._side_tasks.discard(done)
            # Callbacks also run if cancellation happened before work() ever
            # entered; coroutine-finally alone would leak that reserved lease.
            self._session_users[cid] -= 1
            if not self._session_users[cid]:
                self._session_users.pop(cid)
            self._trim_session_cache()
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                logger.exception("Title task failed")

        task.add_done_callback(completed)

    async def wait_side_tasks(self) -> None:
        """Wait for currently owned auxiliary calls (also useful for test teardown)."""
        if self._side_tasks:
            await asyncio.gather(*tuple(self._side_tasks), return_exceptions=True)

    async def shutdown(self) -> None:
        """Cancel and reap owned side tasks before the runtime closes SQLite."""
        tasks = tuple(self._side_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._owns_executions:
            await self.executions.shutdown()

    async def record_side_task(self, conversation_id: str, data: dict) -> None:
        """Record a completed/failed auxiliary provider call with its request/usage."""
        if not getattr(self.settings, "session_log_enabled", True):
            return
        self._session_users[conversation_id] = self._session_users.get(conversation_id, 0) + 1
        try:
            log = await self._open_session(conversation_id)
            log.append("llm/side-task", data)
            await log.flush()
        finally:
            self._session_users[conversation_id] -= 1
            if not self._session_users[conversation_id]:
                self._session_users.pop(conversation_id)
            self._trim_session_cache()

    async def session_events(self, conversation_id: str) -> list[dict]:
        """Open/repair an inactive persisted prefix, then expose durable rows."""
        await self.prepare_session(conversation_id)
        return await asyncio.to_thread(self.db.list_session_events, conversation_id)

    async def prepare_session(self, conversation_id: str) -> None:
        if getattr(self.settings, "session_log_enabled", True):
            self._session_users[conversation_id] = self._session_users.get(conversation_id, 0) + 1
            try:
                log = await self._open_session(conversation_id)
                await log.flush()
            finally:
                self._session_users[conversation_id] -= 1
                if not self._session_users[conversation_id]:
                    self._session_users.pop(conversation_id)
                self._trim_session_cache()

    async def persist_recovered_run(self, conversation_id: str, message_id: str, content: dict) -> None:
        """Persist a terminal public recovery view without executing any work."""
        if self.is_running(conversation_id):
            raise ValueError("Cannot replace a live run with a recovered snapshot.")
        if getattr(self.settings, "session_log_enabled", True):
            log = await self._open_session(conversation_id)
            log.append_ui_message("assistant", content, message_id)
            await log.flush()
        elif self.db.get_message(message_id):
            self.db.update_message(message_id, content)
        else:
            self.db.add_message(conversation_id, "assistant", content, message_id=message_id)

    def enqueue_steer(
        self,
        conversation_id: str,
        text: str,
        skills: Optional[list[str]] = None,
        tools: Optional[list[str]] = None,
    ) -> bool:
        """Queue a mid-turn correction (Claude/Astra-style fold at the next tool boundary)."""
        q = self._steer.get(conversation_id)
        if q is None or conversation_id in self._finalizing or not (text or "").strip():
            return False
        q.put_nowait(SteerNote(text=text.strip(), skills=list(skills or []), tools=list(tools or [])))
        return True

    def _parse_slash(self, text: str, extra_skills: Optional[list[str]] = None,
                     extra_tools: Optional[list[str]] = None) -> tuple[str, list[str], list[str]]:
        rest, skills, tools = split_slash_commands(text, self.skills.names(), self.tools.names())
        forced_skills = list(dict.fromkeys([*(extra_skills or []), *skills]))
        forced_tools = list(dict.fromkeys([*(extra_tools or []), *tools]))
        return rest, forced_skills, forced_tools

    def _resolve_tools(self, names: list[str]) -> list[str]:
        resolved: list[str] = []
        for name in names:
            tool, _ = self.tool_router.resolve(self.tools, name)
            if tool is not None and tool.name not in resolved:
                resolved.append(tool.name)
        return resolved

    def _enable_tool_flags(self, ctx: ToolContext, names: list[str]) -> None:
        """Switch on the per-turn flags the chosen tools declare (plan §3.8a).

        A tool states its own gate in ``Tool.requires_flags``; enabling a tool
        by name — a slash command, a steer,
        ``RunRequest.tools`` — therefore enables whatever that tool needs,
        without any call site knowing that ``web_search`` is special.
        """
        for name in names:
            tool = self.tools.get(name)
            if tool is None:
                continue
            for flag in tool.requires_flags:
                if not flag.startswith("ui_"):  # UI capabilities are client/server negotiation only.
                    ctx.state[flag] = True

    async def _drain_steer(
        self,
        conversation_id: str,
        history: list,
        emit: EmitFn,
        ctx: ToolContext,
        state: TurnState,
        route: Optional[RoutePlan] = None,
    ) -> bool:
        """Fold queued mid-turn corrections into the live turn.

        The durable surface records the correction at its actual boundary,
        between the completed tool result and the next assistant response.
        The existing UI still receives one aggregate assistant row per turn;
        that display view is never used for durable model replay.
        """
        # Verifier histories are temporary and cannot own a user instruction.
        if state.phase == "verify":
            if self._has_steer(conversation_id):
                raise SteeringPending()
            return False
        q = self._steer.get(conversation_id)
        if self._has_steer(conversation_id) and state.work_step_limit and state.work_steps >= state.work_step_limit:
            state.notice = "The run reached its work limit before applying your latest instruction. Your instruction was saved; start a new turn to continue."
            raise BudgetExceeded(state.notice)
        notes: list[SteerNote] = []
        while q is not None and not q.empty():
            notes.append(q.get_nowait())
        if not notes:
            return False
        pending = [{"text": note.text.strip()} for note in notes]
        state.unapplied_steers.extend(pending)
        for note, pending_note in zip(notes, pending):
            recovery_guard = ctx.state.get("recovery_guard")
            if recovery_guard is not None:
                recovery_guard.authorize_user_text(note.text)
            rest, skill_names, tool_names = self._parse_slash(note.text, note.skills, note.tools)
            tool_names = self._resolve_tools(tool_names)
            llm_text = rest.strip() or default_slash_prompt(skill_names, tool_names) or note.text
            if skill_names:
                await self._force_skills(skill_names, ctx, emit, state, route, history)
            if tool_names:
                preferred = list(ctx.state.get("tools") or [])
                for name in tool_names:
                    if name not in preferred:
                        preferred.append(name)
                ctx.state["tools"] = preferred
                self._enable_tool_flags(ctx, tool_names)
            prefix = default_slash_prompt(skill_names, tool_names)
            block = f"{prefix}\n\n{llm_text}".strip() if prefix and prefix not in llm_text else llm_text
            history.append({"role": "user", "content": f"[Steer] {block}"})
            if state.session is not None:
                state.session.sync_history(history, source="steer", turn=state.turn, step=state.step)
                await state.session.flush()
            stored = note.text.strip()
            state.applied_steers.append({
                "text": stored,
                "skills": skill_names,
                "tools": tool_names,
            })
            state.unapplied_steers.remove(pending_note)
            await emit(ev("steer_applied", text=stored, skills=skill_names, tools=tool_names,
                          seq=state.session.events[-1]["seq"] if state.session else None))
        return True

    def _has_steer(self, conversation_id: str) -> bool:
        queue = self._steer.get(conversation_id)
        return queue is not None and not queue.empty()

    def _persist_steers(self, conversation_id: str, state: TurnState) -> None:
        """Write steers applied this turn *after* the assistant message.

        Called from `_run_inner`'s `finally`, immediately after the assistant
        row, so seq order is `user, assistant, user(steer)` — matching the
        order in which they occurred.
        """
        for steer in state.applied_steers:
            content = {"text": steer["text"], "attachments": [], "steer": True}
            if state.session is not None:
                state.session.append_ui_message("user", content)
            else:
                self.db.add_message(conversation_id, "user", content)
        for steer in state.unapplied_steers:
            content = {"text": steer["text"], "attachments": [], "steer": True, "applied": False}
            if state.session is not None:
                state.session.append_ui_message("user", content)
            else:
                self.db.add_message(conversation_id, "user", content)


    async def run(self, req: RunRequest, *, internal_events: bool = False,
                  on_claim: Callable[[str], Awaitable[None]] | None = None) -> AsyncIterator[Event]:
        """Stream the public presentation; internal_events is for trusted diagnostics/tests."""
        queue: asyncio.Queue[Optional[Event]] = asyncio.Queue(maxsize=256)
        closing = asyncio.Event()

        async def emit(event: Event) -> None:
            if not closing.is_set():
                await queue.put(event)

        task = asyncio.create_task(self._run_inner(req, emit, queue, closing,
                                  internal_events=internal_events, on_claim=on_claim))
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=self.settings.keepalive_seconds)
                except asyncio.TimeoutError:
                    if task.done() and queue.empty():
                        break
                    yield keepalive()
                    continue
                if item is None:
                    break
                yield item
        finally:
            # A producer may be suspended on a full queue when its consumer
            # disconnects. Release that backpressure before cancelling, and
            # suppress cleanup emissions so persistence cannot deadlock.
            closing.set()
            while not queue.empty():
                queue.get_nowait()
            if not task.done():
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    # ── the turn ────────────────────────────────────────────────────────

    async def _run_inner(self, req: RunRequest, emit: EmitFn, queue: asyncio.Queue,
                         closing: asyncio.Event | None = None, *, internal_events: bool = False,
                         on_claim: Callable[[str], Awaitable[None]] | None = None) -> None:
        cid = req.conversation_id or ""
        original_user_text = req.text
        cancel = asyncio.Event()
        state = TurnState()
        state.run_budget = RunBudget(
            model_attempt_limit=getattr(self.settings, "run_max_model_attempts", 80),
            tool_start_limit=getattr(self.settings, "run_max_tool_starts", 160),
            timeout_seconds=getattr(self.settings, "run_timeout_seconds", 1800))
        # Steering may restart work, but never refreshes its step allowance.
        # Keep the existing initial-work plus one-correction allocation.
        state.work_step_limit = max(1, self.settings.agent_max_steps)
        if req.verify:
            state.work_step_limit += max(1, min(8, self.settings.agent_max_steps // 2))
        claimed = False
        message_id = new_id("m_")
        status = "complete"
        ctx = None
        options = provider = None
        prior_messages: list[dict] = []
        original_emit = emit
        state.activity = PublicActivity(message_id, state.work, original_emit, enabled=not internal_events)

        async def update_work(label: str | None = None, item_status: str = "running") -> None:
            if label is not None:
                if label != "Working":
                    for step in state.work["steps"]:
                        if step["label"] == "Working" and step["status"] == "running":
                            step["status"] = "complete"
                step = next((s for s in state.work["steps"] if s["label"] == label), None)
                if step is None:
                    state.work["steps"].append({"label": label, "status": item_status})
                else:
                    step["status"] = item_status
            if not internal_events:
                await original_emit(ev("work", work=public_work(state.work)))

        async def durable_emit(event: Event) -> None:
            kinds = {"plan": "plan/write", "skills": "skill/activated", "phase": "phase/change",
                     "verification": "verification"}
            if state.session is not None and event.type in kinds:
                state.session.append(kinds[event.type], {"turn": state.turn, "step": state.step, **event.data})
            if event.type == "monitor":
                state.parts.append({"type": "monitor", **event.data})
            if event.type == "ui_error":
                sid = str(event.data.get("surface_id", ""))
                fallback = event.data.get("fallback")
                if isinstance(fallback, str) and fallback.strip() and self.ui.store.get(cid, sid) is None:
                    part = state.ui_fallbacks.get(sid)
                    if part is None:
                        part = {"type": "text", "text": fallback}
                        state.ui_fallbacks[sid] = part
                        state.parts.append(part)
                    else:
                        part["text"] = fallback
                    if state.session is not None:
                        state.session.append("ui/fallback", {"turn": state.turn, "step": state.step,
                            "message_id": message_id, "surface_id": sid, "fallback": fallback}, ignorable=True)
                        await state.session.flush()
            if event.type == "ui_commit":
                surface = event.data["surface"]
                repaired = state.ui_fallbacks.pop(surface["id"], None)
                if repaired is not None:
                    state.parts[:] = [p for p in state.parts if p is not repaired]
                existing = next((p for p in state.parts if p.get("type") == "ui" and p["surface"]["id"] == surface["id"]), None)
                if existing is None:
                    state.parts.append({"type": "ui", "surface": surface, "phase": state.phase,
                                        "publication": state.output_generation})
                else:
                    existing["surface"] = surface
                    existing["phase"] = state.phase
                    existing["publication"] = state.output_generation
                if state.session is not None:
                    checkpoint = state.session.append("ui/surface", {"turn": state.turn, "step": state.step,
                        "message_id": message_id, "surface_id": surface["id"], "revision": surface["revision"],
                        "fallback": surface["fallback"]}, ignorable=True)
                    await state.session.flush()
                    event.seq = checkpoint["seq"]
            if event.type == "message_start":
                await original_emit(event)
                await state.activity.set_phase("work")
                await state.activity.progress("I’m working on your request.")
                await update_work("Working")
                return
            if event.type == "tool_call_ready":
                if (event.data.get("resolved_name") or event.data.get("name")) in {"report_progress", "publish_result"}:
                    if internal_events:
                        await original_emit(event)
                    return
                key = f"{state.step}:{event.data['id']}"
                state.work_tools[key] = tool_label(event.data.get("name", ""))
                state.work["tool_count"] = len(state.work_tools)
                await state.activity.queued(state.step, event.data["id"], event.data.get("resolved_name") or event.data.get("name", ""), event.data.get("arguments"))
                await update_work(state.work_tools[key])
            elif event.type == "tool_running":
                await state.activity.running(state.step, event.data["id"])
            elif event.type == "tool_activity_progress":
                await state.activity.measured(state.step, event.data["id"], event.data.get("progress", {}))
            elif event.type == "tool_result":
                key = f"{state.step}:{event.data['id']}"
                await state.activity.result(state.step, event.data["id"], event.data.get("name", ""),
                    event.data.get("ok"), event.data.get("meta") or {}, event.data.get("artifacts") or [])
                if key in state.work_tools:
                    await update_work(state.work_tools[key], "complete" if event.data.get("ok") else "error")
            elif event.type == "phase":
                if event.data.get("phase") == "revise" and state.result_publisher:
                    await state.result_publisher.supersede()
                if event.data.get("phase") == "verify" and state.activity.phase != "check":
                    await state.activity.set_phase("check")
                await state.activity.progress("Checking the results." if event.data.get("phase") == "verify" else "Correcting an issue found during the checks.")
                await update_work("Checking answer" if event.data.get("phase") == "verify" else "Correcting answer")
            elif event.type == "verification":
                verdict = event.data.get("verdict")
                await state.activity.item(f"{message_id}:phase:check", summary={
                    "correct": "The selected checks passed.", "incorrect": "A check found an issue.",
                    "uncertain": "The checks were inconclusive."}.get(verdict, "The checks could not finish."))
                await update_work("Checking answer", "complete")
            elif event.type == "public_progress" and state.phase != "verify":
                await state.activity.progress(event.data.get("text", ""))
            elif event.type == "plan" and state.phase != "verify":
                await state.activity.plan(event.data.get("steps") or [])
            elif event.type == "run_state":
                await state.activity.progress("Paused at a safe boundary." if event.data.get("status") == "paused" else "Resuming work.")
            elif event.type == "steer_applied":
                if state.result_publisher:
                    await state.result_publisher.supersede()
                await state.activity.progress("Your instruction has been applied to the ongoing task.")
            if event.type == "text_delta" and state.final_streaming:
                # Keep the already-public prefix when Stop, a deadline or a
                # provider error interrupts final writing. Private worker and
                # strict-mode drafts never enter this publication boundary.
                state.final_text += event.data.get("delta", "")
            if internal_events:
                await original_emit(event)
            elif event.type in {"conversation", "usage", "steer_applied", "run_state"}:
                await original_emit(event)
            elif event.type == "text_delta" and state.final_streaming:
                await original_emit(event)
            elif event.type in {"ui_start", "ui_patch", "ui_commit", "ui_error"} and state.phase != "verify" and not req.strict_final:
                # The compiler emits validated native prefixes; mini-apps are
                # published by the service only once complete and committed.
                await original_emit(event)
            elif event.type == "error" and not claimed:
                await original_emit(ev("error", message="The answer could not be completed. Please try again."))

        emit = durable_emit
        try:
            spec, model = self.providers.resolve(req.provider_id, req.model_id)
            provider = self.providers.get(spec.id)
            state.provider_id = spec.id
            requested_level = canonical_reasoning(req.reasoning)
            level = requested_level if requested_level in model.reasoning_levels else model.default_reasoning
            conv = self.db.get_conversation(cid) if cid else None
            if conv is None:
                conv = self.db.create_conversation(title=_heuristic_title(req.text), provider=spec.id,
                                                   model=model.id, reasoning=level, conversation_id=cid or None)
                cid = conv["id"]
            if cid in self._active:
                await emit(ev("error", message="A run is already in progress for this conversation."))
                return
            self._active[cid] = cancel
            self._steer[cid] = asyncio.Queue()
            self._paused[cid] = asyncio.Event()
            claimed = True
            # Deliver manager controls queued before this producer acquired the
            # conversation, before even a fast response can close admission.
            if on_claim is not None:
                await on_claim(cid)
            self.db.update_conversation(cid, provider=spec.id, model=model.id, reasoning=level)
            prior_messages = self.db.list_messages(cid)
            if getattr(self.settings, "session_log_enabled", True):
                state.session = await self._open_session(cid)
                if not fold_surface(state.session.events):
                    state.session.set_system({"role": "system", "content": ""})
                    # Old UI rows never had an exact provider request or timed
                    # usage. Record the imported replay honestly as a seed.
                    for message in self._replay(prior_messages, provider):
                        state.session.append_message(message, source="legacy", legacy=True)
                    for message in prior_messages:
                        state.session.append_ui_message(message["role"], message["content"], message["id"])
                    if prior_messages:
                        state.session.append("session/end-seed", {"legacy": True})
                state.turn = state.session.next_turn
                state.session.append("turn/start", {"turn": state.turn, "message_id": message_id,
                                                     "provider": spec.id, "model": model.id, "reasoning": level,
                                                     "presentation": "final"})
                state.session.append_ui_message("user", {"text": req.text, "attachments": req.attachments})
                await state.session.flush()
            else:
                self.db.add_message(cid, "user", {"text": req.text, "attachments": req.attachments})
            checkpoint_seq = state.session.events[-1]["seq"] if state.session else None
            await emit(ev("conversation", conversation_id=cid, title=conv["title"], seq=checkpoint_seq))
            await emit(ev("message_start", message_id=message_id, provider=spec.id, model=model.id,
                          reasoning=level, seq=checkpoint_seq))

            workspace = Workspace(cid, self.settings.workspace_dir)
            slash_rest, slash_skills, slash_tools = self._parse_slash(req.text, req.skills, req.tools)
            slash_tools = self._resolve_tools(slash_tools)
            hint = default_slash_prompt(slash_skills, slash_tools)
            rest = slash_rest.strip()
            llm_text = f"{hint}\n\n{rest}" if rest and hint else (rest or hint or req.text)
            req = dataclasses.replace(req, text=llm_text, skills=slash_skills, tools=slash_tools)
            ctx = ToolContext(conversation_id=cid, workspace=workspace, emit=emit, cancel=cancel,
                              state={"plan": state.plan, "skills": [], "ui_client_version": req.ui_version,
                                     "ui_service": self.ui, "ui_message_id": message_id,
                                     "ui_native": req.ui_version == 1 and self.ui.enabled("native"),
                                     "ui_mini_apps": req.ui_version == 1 and self.ui.enabled("mini_app"),
                                     "ui_enabled": req.ui_version == 1 and (self.ui.enabled("native") or self.ui.enabled("mini_app"))},
                              kernels=self.kernels, settings=self.settings)
            state.context = ctx
            ctx.state["run_budget"] = state.run_budget

            async def control_boundary():
                try:
                    await self._control_boundary(ctx, emit)
                except Stopped as exc:
                    # Child services share cancellation without importing the
                    # harness (which would introduce a dependency cycle).
                    raise asyncio.CancelledError() from exc
                if state.session is not None:
                    from app.agent.session.telemetry import budget_error
                    reason = budget_error(state.session.events, state.turn, self.settings)
                    if reason:
                        raise BudgetExceeded(reason)

            ctx.state["control_boundary"] = control_boundary

            async def publish_result(block):
                if state.session is not None:
                    state.session.append("ui/result", {"turn": state.turn, "step": state.step,
                        "message_id": message_id, "block": block, "strict_final": req.strict_final}, ignorable=True)
                    await state.session.flush()
                if not req.strict_final:
                    await original_emit(ev("result_block", message_id=message_id, block=block))

            def observed_result_refs():
                refs = set()
                for item in state.work.get("items", []):
                    if item.get("status") != "completed":
                        continue
                    refs.add(item["id"])
                    for output in item.get("outputs", []):
                        refs.update(value for key in ("url", "path", "surface_id")
                                    if isinstance((value := output.get(key)), str))
                for part in state.parts:
                    if part.get("type") == "tool_call" and part.get("name") in {"read_file", "parse_document"} and (part.get("result") or {}).get("ok"):
                        path = (part.get("arguments") or {}).get("path")
                        if isinstance(path, str) and path and not path.startswith("/") and ".." not in path.split("/"):
                            refs.add(path)
                return refs

            state.result_publisher = ResultPublisher(message_id, state.parts, publish=publish_result,
                observed_refs=observed_result_refs, allowed=lambda: state.phase in {"answer", "revise"} and not cancel.is_set())
            ctx.state["result_publisher"] = state.result_publisher
            if ctx.state["ui_native"]:
                from app.ui.compiler import UIStreamCompiler
                state.ui_compiler = UIStreamCompiler(self.ui.store, cid)
            restore_execution_state(ctx, state.session.events if state.session is not None else [], original_user_text)
            route = self._route_turn(req, workspace)
            await self._activate_skills(route, ctx, emit, forced=bool(req.skills), state=state)
            if req.tools:
                ctx.state["tools"] = list(req.tools)
                self._enable_tool_flags(ctx, req.tools)
            options = ChatOptions(model=model.id, reasoning=level, temperature=req.temperature,
                                  max_tokens=self.settings.max_output_tokens or None)
            # Document OCR uses the exact provider already resolved for this
            # conversation, including configured credentials/base URL. Its
            # page requests are separate, cancellable and accounted side tasks.
            ctx.state["document_provider"] = provider

            async def record_auxiliary_call(data: dict, *, document: bool = False) -> None:
                task_id = data.get("task_id")
                status = data.get("status")
                if status == "started" and document:
                    if state.session is not None:
                        from app.agent.session.telemetry import budget_error
                        reason = budget_error(state.session.events, state.turn, self.settings)
                        if reason:
                            raise BudgetExceeded(reason)
                    admission = state.run_budget.admit_model(task_id, source="document_ocr",
                        provider=data.get("provider", ""), model=data.get("model", ""))
                    if not admission.is_new:
                        raise RuntimeError("A document model attempt cannot be dispatched twice.")
                if state.session is not None:
                    state.session.append("llm/side-task", {**data, "turn": state.turn, "step": state.step})
                    await state.session.flush()
                if status == "started":
                    state.llm_calls += 1
                elif status in {"completed", "error", "aborted"}:
                    if document:
                        state.run_budget.settle_model(task_id, usage=data.get("usage"), outcome=status)
                    for key, value in (data.get("usage") or {}).items():
                        if type(value) in (int, float):
                            state.usage[key] = state.usage.get(key, 0) + value

            async def record_document_call(data: dict) -> None:
                await record_auxiliary_call(data, document=True)

            ctx.state["document_record_call"] = record_document_call
            # Delegated services own their shared-budget admission/settlement;
            # the parent callback records their actual usage, without charging twice.
            ctx.state["lambda_record_call"] = record_auxiliary_call
            system = {"role": "system", "content": self._system_prompt(workspace, route, ui=ctx.state["ui_enabled"])}
            turn_context = self._turn_context(workspace, route, cid)
            if not prior_messages:
                # Titles depend only on the first request; starting once setup
                # succeeds lets the header update while the agent works.
                self._start_title(cid, provider, options, req.text, state.session)
            if state.session is not None:
                state.session.set_system(system, turn=state.turn, step=state.step)
                state.session.append_message(turn_context, source="turn-context", turn=state.turn)
                state.session.append_message({"role": "user", "content": _user_content(req.text, req.attachments)},
                                             source="human", turn=state.turn)
                events = state.session.events
                history = _provider_replay(derive_messages(events), provider, events)
            else:
                history = [system, *self._replay(prior_messages, provider), turn_context,
                           {"role": "user", "content": _user_content(req.text, req.attachments)}]
            _compact_ui_history(history)
            await self._prepare_attachments(req, provider, history, ctx, state, emit)
            skip_check = req.verify and _trivial_verification_request(req)
            if skip_check:
                state.work["verification"] = {"status": "skipped", "rounds": 0}
            direct = not self._has_steer(cid) and not req.strict_final and (not req.verify or skip_check) and not req.attachments and (
                _trivial_verification_request(req) or not len(self.tools))
            if direct:
                self._finalizing.add(cid)
                await self._begin_final(state, original_emit, message_id)
            final_text = await self._agent_loop(
                provider, options, history, ctx, state, emit, tools=self.tools,
                max_steps=self.settings.agent_max_steps, query=req.text, route=route,
                web_search=bool(req.web_search), direct_response=direct,
            )
            while not direct:
                if req.strict_final and _current_published_results(state.parts) and not state.answer_truncated:
                    # Consolidate privately before strict checking; opening the
                    # final-token channel here would release an unchecked draft.
                    await self._control_boundary(ctx, emit)
                    final_text = await self._final_response(provider, options, req, final_text, history, ctx, state, emit)
                if req.verify and final_text.strip() and not cancel.is_set() and not state.answer_truncated:
                    if _trivial_verification_request(req) and not state.applied_steers:
                        state.work["verification"] = {"status": "skipped", "rounds": 0}
                    else:
                        problem = _user_content(req.text, req.attachments)
                        for note in state.applied_steers:
                            problem += "\n\n[User update] " + note["text"]
                        try:
                            final_text = await self._verify_and_revise(provider, options, history, ctx, state, emit,
                                problem, final_text, request=req)
                        except SteeringPending:
                            # A check cannot apply a task change to its isolated
                            # context. Recompute in the owning worker instead.
                            pass
                await self._control_boundary(ctx, emit)
                if await self._drain_steer(cid, history, emit, ctx, state, route):
                    state.phase = "answer"
                    state.notice = None
                    state.answer_truncated = False
                    state.publish_outputs = True
                    state.output_generation += 1
                    state.work["verification"] = {"status": "not_requested", "rounds": 0}
                    await state.activity.set_phase("work")
                    await state.activity.progress("Updating the work to include your instruction.")
                    final_text = await self._agent_loop(
                        provider, options, history, ctx, state, emit, tools=self.tools,
                        max_steps=self.settings.agent_max_steps, query=req.text, route=route,
                        web_search=bool(req.web_search),
                    )
                    continue
                # No await between the empty-queue check and closing admission:
                # every accepted instruction was applied, or is still queued.
                self._finalizing.add(cid)
                break
            if cancel.is_set():
                raise Stopped()
            if not direct and not req.strict_final and not state.answer_truncated and final_text.strip():
                await self._begin_final(state, original_emit, message_id)
                final_text = await self._final_response(provider, options, req, final_text, history, ctx, state, emit)
            if state.answer_truncated:
                status = "error"
                state.end_reason = "max-tokens"
                state.publish_outputs = False
                state.work["verification"]["status"] = "limit_reached" if req.verify else "not_requested"
                state.notice = "The response reached its length limit before finishing. This answer is incomplete."
            state.final_text = final_text
            if status == "complete":
                await state.result_publisher.finalize()
            state.final_ready = True
            if not state.final_started:
                await state.activity.set_phase("final")
            await update_work("Preparing final answer")
            choices = next_step_options(ctx.state.get("plan") or state.plan, final_text)
            if choices:
                state.parts.append({"type": "options", "items": choices})
                await emit(ev("options", items=choices))
        except Stopped:
            status = "stopped"
        except asyncio.CancelledError:
            status = "stopped"
            raise
        except BudgetExhausted as exc:
            status = "error"
            state.end_reason = "blocked"
            state.notice = str(exc) + " Completed work and available partial results were saved."
            state.work["verification"]["status"] = "limit_reached" if req.verify else "not_requested"
            await emit(ev("error", message=state.notice))
        except Exception as exc:  # noqa: BLE001
            status = "error"
            logger.exception("Agent run failed")
            await emit(ev("error", message=_friendly_error(exc)))
        finally:
            # Stop/error paths must retain accepted notes too, without falsely
            # publishing steer_applied for instructions that never ran.
            if claimed:
                self._finalizing.add(cid)
                pending = self._steer.get(cid)
                while pending is not None and not pending.empty():
                    state.unapplied_steers.append({"text": pending.get_nowait().text.strip()})
            if state.unapplied_steers and not state.notice:
                state.notice = "The run ended before applying your latest instruction. Your instruction was saved; start a new turn to continue."
            try:
                if claimed:
                    state.work["status"] = status
                    state.work["tool_count"] = len(state.activity.calls)
                    await state.activity.finish(status)
                    for step in state.work["steps"]:
                        if step["status"] == "running":
                            step["status"] = "complete" if status == "complete" else "error"
                    content = {
                        "parts": state.parts, "model": options.model if options else conv.get("model"),
                        "provider": state.provider_id, "reasoning": options.reasoning if options else conv.get("reasoning"),
                        "usage": state.usage, "status": status, "plan": ctx.state.get("plan", []) if ctx else [],
                        "stats": {"llm_calls": state.llm_calls, "tool_calls": state.tool_calls,
                                  "verification_checks": state.verification_checks},
                        "presentation": "final", "final_text": state.final_text, "final_ready": state.final_ready,
                        "work": public_work(state.work),
                        "output_generation": state.output_generation, "publish_outputs": state.publish_outputs,
                        "strict_final": req.strict_final,
                        "budget": state.run_budget.snapshot(),
                    }
                    if state.notice:
                        content["notice"] = state.notice
                    if state.session is not None and state.turn:
                        state.session.close_open_step("aborted" if status == "stopped" else "error")
                        state.session.append_ui_message("assistant", content, message_id)
                        self._persist_steers(cid, state)
                        reason = state.end_reason or ("completed" if status == "complete" else "aborted" if status == "stopped" else "error")
                        state.session.append("turn/end", {"turn": state.turn, "reason": {"kind": reason}})
                        await state.session.flush()
                        try:
                            otel.export_turn(cid, state.session.events, state.turn)
                        except Exception:  # noqa: BLE001 - telemetry must never fail a turn
                            logger.warning("OpenTelemetry export failed", exc_info=True)
                    elif state.session is None:
                        self.db.add_message(cid, "assistant", content, message_id=message_id)
                        self._persist_steers(cid, state)
                    checkpoint_seq = state.session.events[-1]["seq"] if state.session else None
                    presentation = public_content(content)
                    if not internal_events:
                        await original_emit(ev("work", work=presentation["work"]))
                        if not state.final_started:
                            await original_emit(ev("final_start", message_id=message_id, mode="buffered"))
                        # These are chunks of the checked, buffered answer. No
                        # additional generation or simulated token timing occurs.
                        for part in presentation["parts"]:
                            if part["type"] == "text" and not state.final_started:
                                for offset in range(0, len(part["text"]), 4096):
                                    await original_emit(ev("text_delta", delta=part["text"][offset:offset + 4096]))
                            elif part["type"] == "ui":
                                await original_emit(ev("ui_commit", surface=part["surface"]))
                            elif part["type"] == "artifact":
                                await original_emit(ev("artifact", artifact=part["artifact"]))
                            elif part["type"] == "options":
                                await original_emit(ev("options", items=part["items"]))
                    await original_emit(ev("message_end", message_id=message_id, status=status,
                                           content=content if internal_events else presentation, seq=checkpoint_seq))
                    if status == "stopped":
                        await original_emit(ev("stopped", conversation_id=cid, message_id=message_id, seq=checkpoint_seq))
                    elif status == "complete":
                        await original_emit(ev("done", conversation_id=cid, message_id=message_id, seq=checkpoint_seq))
                    elif not internal_events:
                        await original_emit(ev("error", message=state.notice or "The answer could not be completed. Please try again.",
                                               conversation_id=cid, message_id=message_id, seq=checkpoint_seq))
            finally:
                if claimed:
                    self._active.pop(cid, None)
                    self._steer.pop(cid, None)
                    self._paused.pop(cid, None)
                    self._finalizing.discard(cid)
                    self._trim_session_cache()
                if closing is None or not closing.is_set():
                    await queue.put(None)

    async def _begin_final(self, state, emit, message_id):
        if state.result_publisher:
            state.result_publisher.closed = True
        state.phase = "final"
        state.final_started = True
        state.final_streaming = True
        await state.activity.set_phase("final")
        await state.activity.progress("Preparing the final response from the results.")
        await emit(ev("final_start", message_id=message_id, mode="streaming"))

    async def _final_response(self, provider, options, req, answer, history, ctx, state, emit):
        """A tool-free rendering pass over the result bundle, streamed live.

        Verifier reports, private provider reasoning and agent conversations are
        deliberately absent from this request. Strict wording verification uses
        the buffered path instead and never claims to be live generation.
        """
        from app.agent.prompts import FINAL_RESPONSE_PROMPT
        outputs = public_content({"parts": state.parts, "final_text": answer, "final_ready": True,
            "output_generation": state.output_generation, "publish_outputs": state.publish_outputs})["parts"]
        payload = {"request": _user_content(req.text, req.attachments), "answer": answer,
                   "published_results": _current_published_results(state.parts),
                   "user_updates": [note["text"] for note in state.applied_steers],
                   "verification": state.work["verification"], "notice": state.notice,
                   "outputs": [{"type": part["type"], "artifact": part.get("artifact"),
                                "surface": part.get("surface")} for part in outputs if part["type"] in {"artifact", "ui"}]}
        messages = [{"role": "system", "content": FINAL_RESPONSE_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        final_options = dataclasses.replace(options, reasoning="off", temperature=0)
        state.step += 1
        if state.session is not None:
            state.session.append("step/start", {"turn": state.turn, "step": state.step, "phase": "final"})
        try:
            text, reasoning, calls = await self._stream_llm(provider, final_options, messages, None, emit, state, ctx.cancel)
            if ctx.cancel.is_set():
                raise Stopped()
            if calls:
                raise RuntimeError("The final response unexpectedly requested a tool.")
            if not text.strip():
                raise RuntimeError("The final response was empty.")
            state.answer_truncated = state.last_finish_reason in {"length", "max_tokens", "max-tokens"}
            state.parts.append({"type": "text", "text": text, "phase": "final"})
            # Restore the conversation's provider context after the isolated
            # final-writing request. It must not erase earlier turns/tools.
            if history and history[-1].get("role") == "assistant" and not history[-1].get("tool_calls"):
                history[-1] = provider.assistant_message(text, reasoning, [])
            else:
                history.append(provider.assistant_message(text, reasoning, []))
            return text
        finally:
            state.final_streaming = False
            if state.session is not None:
                state.session.close_open_step("aborted" if ctx.cancel.is_set() else "error")
                state.session.sync_history(history, turn=state.turn, step=state.step, phase=state.phase)
                await state.session.flush()

    async def _prepare_attachments(self, req, provider, history, ctx, state, emit) -> None:
        """Read visual uploads before reasoning, without requiring a slash tool.

        The automatic tool envelope is explicitly labelled as application work,
        not a model response. It uses the normal durable tool lifecycle so stop,
        history replay and interrupted-turn recovery retain the reading.
        """
        if not getattr(self.settings, "document_auto_parse", True) or not req.attachments or not self.tools.get("parse_document"):
            return
        from pathlib import Path
        from app.agent.document_ocr import DOCUMENT_EXTENSIONS, is_visual_document

        paths = []
        for attachment in req.attachments:
            path = attachment.get("path")
            if not isinstance(path, str) or not path or path in paths:
                continue
            candidate = Path(path).suffix.lower() in DOCUMENT_EXTENSIONS
            if not candidate:
                try:
                    candidate = is_visual_document(ctx.workspace.resolve(path, must_exist=True))
                except (ValueError, OSError):
                    candidate = False
            if candidate:
                paths.append(path)
        limit = self.settings.document_ocr_max_files
        omitted = paths[limit:]
        paths = paths[:limit]
        if not paths:
            return
        state.step += 1
        calls = [ToolCallSpec(new_id("ocr_"), "parse_document", json.dumps({"path": path, "backend": "auto"})) for path in paths]
        message = provider.assistant_message("", "", calls)
        history.append(message)
        if state.session is not None:
            state.session.append("step/start", {"turn": state.turn, "step": state.step,
                                                "phase": state.phase, "automatic": True})
            state.session.append_message(message, source="attachment-preprocessing", turn=state.turn,
                                         step=state.step, phase=state.phase, automatic=True, stream=[])
            await state.session.flush()
        try:
            for call in calls:
                await emit(ev("tool_call_start", id=call.id, name=call.name))
            results = await self._execute_calls(calls, self.tools, ctx, emit, state)
            for call, result in zip(calls, results):
                history.append(provider.tool_message(call.id, call.name, _tool_message_text(result)))
            if omitted:
                note = (f"Only {limit} attached documents were read automatically. These attachments were not read: "
                        + ", ".join(omitted) + ". Use parse_document for those files if relevant; do not claim their contents are known.")
                history.append({"role": "user", "content": f"[Attachment processing note: {note}]"})
                if state.session is not None:
                    state.session.append_message(history[-1], source="attachment-preprocessing", turn=state.turn, step=state.step)
                await emit(ev("status", message=note))
        finally:
            if state.session is not None:
                state.session.close_open_step("aborted" if ctx.cancel.is_set() else "error")
                await state.session.flush()

    # ── system prompt & history ─────────────────────────────────────────

    def _system_prompt(self, workspace: Workspace, route: Optional[RoutePlan] = None, ui: bool = False) -> str:
        """Conversation-stable system head; per-turn facts belong in ``_turn_context``."""
        extra = []
        if self.tools.get("wolfram_alpha"):
            extra.append("`wolfram_alpha` — short factual/computational answers from Wolfram|Alpha for cross-checking.")
        if self.tools.get("lean_check"):
            extra.append("`lean_check` — compile a Lean 4 proof; it passes only without sorry, admit or new axioms. "
                         "Use it when the user asks for a formal or machine-checked proof, or a key lemma warrants one.")
        extra.append("`search_skills` / `load_skill` — find and activate a specialised playbook from the skill space.")
        if self.tools.get("lambda_data_agent"):
            extra.append(
                "Use native `python` and file tools by default for short, bounded calculations, "
                "single statistics, table inspections, and file edits. "
                "`lambda_data_agent` supports substantial specialized EDA, chart/report, notebook, "
                "and PowerPoint workflows, or an explicit request to use LAMBDA. "
                "When delegating, specify the inputs, question, expected deliverable and output paths, "
                "and what evidence establishes completion. Delegation shares this run's model/tool "
                "limits and deadline; it does not reset them. Inspect the returned results and "
                "incomplete status before presenting a final answer."
            )
        if self.tools.get("web_search"):
            extra.append(
                "`web_search` / `web_fetch` — live web search (open metasearch: ddgs, optional SearXNG/Tavily/Brave) "
                "and page fetch. Available by default: use automatically for time-sensitive information. "
                "The user does not need to select a slash command."
            )
        return build_system_prompt(extra, interactive_ui=ui)

    def _turn_context(self, workspace: Workspace, route: Optional[RoutePlan], cid: str) -> dict:
        try:
            ds = datasets_block(workspace)
        except Exception:  # noqa: BLE001
            ds = ""
        catalog, active = "", ""
        if route is not None:
            catalog, active = route.as_prompt(getattr(self.settings, "skill_catalog_limit", 16))
        elif len(self.skills):
            catalog = self.skills.catalog_lines()
        content = build_turn_context(workspace.describe_for_prompt(), ds, skills_catalog=catalog,
                                     skills_active=active, ui_state=self.ui.store.context_summary(cid))
        return {"role": "user", "content": content}

    def _route_turn(self, req: RunRequest, workspace: Workspace) -> RoutePlan:
        if not len(self.skills):
            return RoutePlan()
        files = []
        try:
            files = workspace.list_files(limit=80)
        except Exception:  # noqa: BLE001
            files = []
        route = self.skill_router.plan(req.text, attachments=req.attachments, files=files)
        forced: list = []
        for name in req.skills or []:
            skill = self.skills.get(name)
            if skill is not None:
                forced.append(skill)
        if not forced:
            return route
        seen = {s.name for s in forced}
        route.activate = forced + [s for s in route.activate if s.name not in seen]
        return route

    async def _activate_skills(
        self,
        route: RoutePlan,
        ctx: ToolContext,
        emit: EmitFn,
        forced: bool = False,
        state: Optional[TurnState] = None,
    ) -> None:
        if not route.activate:
            return
        if not forced and not getattr(self.settings, "skill_auto_activate", True):
            return
        names = []
        for skill in route.activate:
            try:
                materialize_skill(skill, ctx.workspace)
                names.append(skill.name)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to materialise skill %s", skill.name)
        if names:
            ctx.state["skills"] = names
            part = {"type": "skills", "names": names, "auto": not forced}
            if state is not None:
                state.parts.append(part)
            await emit(ev("status", message="Loaded skill" + ("s" if len(names) > 1 else "") + ": " + ", ".join(names)))
            await emit(ev("skills", names=names, auto=not forced))

    async def _force_skills(
        self,
        names: list[str],
        ctx: ToolContext,
        emit: EmitFn,
        state: TurnState,
        route: Optional[RoutePlan],
        history: list,
    ) -> None:
        """Activate slash-invoked skills mid-turn: materialise, inject bodies, refresh system prompt."""
        if not names:
            return
        plan = route or RoutePlan()
        seen = {s.name for s in plan.activate}
        added = []
        for name in names:
            skill = self.skills.get(name)
            if skill is None:
                continue
            if skill.name not in seen:
                plan.activate.append(skill)
                seen.add(skill.name)
            added.append(skill)
        if not added:
            return
        await self._activate_skills(RoutePlan(activate=added), ctx, emit, forced=True, state=state)
        loaded = list(ctx.state.get("skills") or [])
        for skill in added:
            if skill.name not in loaded:
                loaded.append(skill.name)
        ctx.state["skills"] = loaded
        if route is not None:
            route.activate = plan.activate
        # The skill bodies below reach the model as a message; rewriting the
        # system head here would invalidate the provider's cached prefix.
        bodies = []
        for skill in added:
            body = skill.body
            if len(body) > 12_000:
                body = body[:12_000] + "\n\n… [truncated; read skills/" + skill.name + "/SKILL.md]"
            bodies.append(f"### Skill `{skill.name}`\n\n{body}")
        if bodies:
            history.append({
                "role": "user",
                "content": (
                    "The user invoked "
                    + ", ".join(f"`/{s.name}`" for s in added)
                    + ". Follow these skill instructions for the rest of this turn.\n\n"
                    + "\n\n".join(bodies)
                ),
            })

    def _replay(self, messages: list[dict], provider: LLMProvider) -> list[dict]:
        """Rebuild LLM history from stored messages; recent turns keep full tool detail."""
        out: list[dict] = []
        assistant_idx = [i for i, m in enumerate(messages) if m["role"] == "assistant"]
        keep = self.settings.recent_turns_full_replay
        full_from = assistant_idx[-keep] if len(assistant_idx) >= keep else 0
        for i, m in enumerate(messages):
            c = m["content"] or {}
            if m["role"] == "user":
                out.append({"role": "user", "content": _user_content(c.get("text", ""), c.get("attachments") or [])})
            elif m["role"] == "assistant":
                parts = c.get("parts") or []
                if i >= full_from:
                    out.extend(_expand_parts(parts, provider, self.settings.tool_max_output_chars // 2))
                else:
                    reasoning = "".join(part.get("text", "") for part in parts if part.get("type") == "reasoning")
                    out.append(provider.assistant_message(_collapse_parts(parts), reasoning, []))
        # Trim very long histories from the front (keep the last turns).
        budget = self.settings.history_char_budget
        while len(out) > 4 and sum(len(json.dumps(m, ensure_ascii=False)) for m in out) > budget:
            out.pop(0)
            while out and out[0]["role"] != "user":
                out.pop(0)
        return out

    # ── core loop ───────────────────────────────────────────────────────

    async def _agent_loop(self, provider, options, history, ctx: ToolContext, state: TurnState, emit: EmitFn,
                          tools: ToolRegistry, max_steps: int, query: str = "", route: Optional[RoutePlan] = None,
                          web_search: bool = False, direct_response: bool = False) -> str:
        consecutive_failures = 0
        last_text = ""
        if web_search:
            # New requests enable web access by default; callers can opt out.
            # Keep the declared capability gate for explicit opt-outs and
            # later slash commands/steers that intentionally enable access.
            ctx.state["web_search"] = True

        def current_schemas() -> list[dict]:
            preferred = list(ctx.state.get("tools") or [])
            out = self.tool_router.schemas(tools, query, route, preferred_tools=preferred,
                                           loaded_tools=ctx.state.get("loaded_tools"), ctx=ctx)
            visible = {t.name for t in tools if t.is_provider_visible(ctx)}
            return [s for s in out if (s.get("function") or {}).get("name") in visible]

        schemas = current_schemas()
        for step in range(max_steps):
            await self._control_boundary(ctx, emit)
            if state.phase == "verify" and self._has_steer(ctx.conversation_id):
                raise SteeringPending()
            worker = state.phase != "verify"
            if worker and state.work_step_limit and state.work_steps >= state.work_step_limit:
                state.notice = "The run reached its work limit. Completed work and your instructions are saved; start a new turn to continue."
                raise BudgetExceeded(state.notice)
            state.step += 1
            if state.session is not None:
                state.session.append("step/start", {"turn": state.turn, "step": state.step, "phase": state.phase})
            try:
                if ctx.cancel.is_set():
                    raise Stopped()
                if await self._drain_steer(ctx.conversation_id, history, emit, ctx, state, route):
                    schemas = current_schemas()
                if worker:
                    state.work_steps += 1
                last_step = step == max_steps - 1 or (worker and state.work_step_limit and state.work_steps >= state.work_step_limit)
                if last_step:
                    history.append({"role": "user", "content": FINAL_STEP_NUDGE})
                if await self._compact_history(provider, options, history, state):
                    from app.agent.tools.read_state import clear_read_state
                    clear_read_state(ctx.state)
                text, reasoning, calls = await self._stream_llm(provider, options, history, None if last_step or direct_response else schemas, emit, state, ctx.cancel)
                if direct_response and calls:
                    raise RuntimeError("The final response unexpectedly requested a tool.")
                if reasoning.strip():
                    state.parts.append({"type": "reasoning", "text": reasoning, "phase": state.phase})
                if text.strip():
                    state.parts.append({"type": "text", "text": text, "phase": state.phase})
                    last_text = text
                if ctx.cancel.is_set():
                    raise Stopped()
                history.append(provider.assistant_message(text, reasoning, calls))
                if state.phase == "verify" and self._has_steer(ctx.conversation_id):
                    raise SteeringPending()
                if not calls:
                    if not direct_response and not last_step and await self._drain_steer(ctx.conversation_id, history, emit, ctx, state, route):
                        schemas = current_schemas()
                        continue
                    if state.phase in {"answer", "revise", "final"}:
                        state.answer_truncated = state.last_finish_reason in {"length", "max_tokens", "max-tokens"}
                        if not state.answer_truncated and state.end_reason == "max-tokens":
                            state.end_reason = None
                    return text
                results = await self._execute_calls(calls, tools, ctx, emit, state)
                for call, res in zip(calls, results):
                    history.append(provider.tool_message(call.id, call.name, _tool_message_text(res)))
                if any(result.ok and result.meta.get("refresh_tools") for result in results):
                    schemas = current_schemas()
                newly_blocked = (_record_failures(calls, results, state, tools, self.tool_router)
                                 if getattr(self.settings, "loop_policy", "operation") == "legacy" else [])
                if newly_blocked and not state.repeat_nudge_sent:
                    history.append({"role": "user", "content": REPEAT_NUDGE})
                    state.repeat_nudge_sent = True
                if not last_step and await self._drain_steer(ctx.conversation_id, history, emit, ctx, state, route):
                    schemas = current_schemas()
                stopping = _turn_stopping_result(calls, results, tools, self.tool_router)
                if stopping is not None:
                    # `stop_turn_on_success` (§3.8a): the tool declared that a
                    # successful result concludes the turn, so the loop reads the
                    # declaration instead of matching a tool name. The result is
                    # already committed above (persisted as its `tool_call` part and
                    # emitted as `tool_result`) and carries the terminal payload;
                    # the text the model produced in this same step stays the
                    # answer. A *failed* result does not stop anything — the model
                    # gets the error and may retry in-session, like ZCode.
                    stopping.meta.setdefault("stop_turn", True)
                    return last_text
                consecutive_failures = _next_consecutive_failures(
                    consecutive_failures, calls, results, tools, self.tool_router,
                )
                if consecutive_failures >= self.settings.agent_max_consecutive_failures:
                    history.append({"role": "user", "content": FAILURE_NUDGE})
                    consecutive_failures = 0
            finally:
                if state.session is not None:
                    # Missing tool results get conservative synthetic closers
                    # before the step boundary, also for cooperative stops.
                    state.session.close_open_step("aborted" if ctx.cancel.is_set() else "error")
                    await state.session.flush()
        return last_text

    async def _stream_llm(self, provider, options, history, schemas, emit: EmitFn, state: TurnState, cancel: asyncio.Event):
        """One step, with each actual dispatch durably recorded as an attempt."""
        # Freeze every component before dispatch; adapters receive detached data.
        frozen_history = snapshot_json(history)
        frozen_schemas = snapshot_json(schemas) if schemas else None
        frozen_options = snapshot_json(dataclasses.asdict(options))
        config = {"provider": state.provider_id or provider.id, **frozen_options}
        attempts = 0
        while True:
            if state.session is not None:
                from app.agent.session.telemetry import budget_error
                budget_message = budget_error(state.session.events, state.turn, self.settings)
                if budget_message:
                    raise BudgetExceeded(budget_message)
            attempts += 1
            budget = state.run_budget
            admission_id = f"native:{state.turn}:{state.step}:{attempts}"
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
                request = derive_request(state.session.events)
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

            try:
                source = provider.stream(snapshot_json(frozen_history), snapshot_json(frozen_schemas),
                                         ChatOptions(**snapshot_json(frozen_options)))
                delta_stream = _controlled_deltas(source, cancel, budget)
                async for delta in delta_stream:
                    if cancel.is_set():
                        break
                    if state.final_streaming and state.context is not None:
                        await self._control_boundary(state.context, emit)
                    timestamp = time.time()
                    if delta.kind in ("text", "reasoning", "tool_call") and first_token is None:
                        first_token = timestamp
                    if delta.kind == "text":
                        stream.push(timestamp, {"type": "text-delta", "index": 0, "text": delta.text})
                        text_parts.append(delta.text)
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
                if usage is not None:
                    for key, value in usage.items():
                        if type(value) in (int, float):
                            state.usage[key] = state.usage.get(key, 0) + value
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
                if usage is not None:
                    for key, value in usage.items():
                        if type(value) in (int, float):
                            state.usage[key] = state.usage.get(key, 0) + value
                if (isinstance(exc, Exception) and not isinstance(exc, (BudgetExhausted, Stopped)) and attempts < 2 and _is_transient(exc)
                        and not text_parts and not reasoning_parts and not pending and not cancel.is_set()):
                    logger.warning("Transient LLM error, retrying: %s", exc)
                    if state.session is not None:
                        state.session.append("llm/retry", {"turn": state.turn, "step": state.step,
                            "retry": attempts, "delay_ms": 1500, "failure": data["error"]})
                        await state.session.flush()
                    await emit(ev("status", message="Model connection hiccup, retrying…"))
                    if state.activity is not None:
                        await state.activity.item(f"{state.activity.id}:retry:{state.step}", kind="control",
                            parent_id=f"{state.activity.id}:phase:{state.activity.phase}",
                            label="Retrying model connection", purpose="Recover a connection that failed before producing output",
                            status="running", attempt=attempts + 1)
                        await state.activity.progress("The model connection was interrupted before producing output. Retrying.")
                    await asyncio.sleep(1.5)
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

    async def _execute_calls(self, calls: list[ToolCallSpec], tools: ToolRegistry, ctx: ToolContext, emit: EmitFn, state: TurnState) -> list[ToolResult]:
        operation_policy = getattr(self.settings, "loop_policy", "operation") == "operation"
        admitted_operations: dict[str, Operation] = {}
        parsed: list[NormalizedCall] = []
        for call in calls:
            tool, resolve_err = self.tool_router.resolve(tools, call.name)
            normalized = _normalize_call(call, tool, resolve_err)
            parsed.append(normalized)
            await emit(ev("tool_call_ready", id=call.id, name=call.name,
                          resolved_name=tool.name if tool else "tool", arguments=normalized.payload))

        async def run_one(nc: NormalizedCall) -> ToolResult:
            started = time.monotonic()
            call, tool = nc.call, nc.tool
            resolved_name = tool.name if tool is not None else call.name
            if state.session is not None:
                state.session.append("tool/call", {"turn": state.turn, "step": state.step,
                    "callId": call.id, "name": call.name, "arguments": call.arguments,
                    "resolved_name": resolved_name, "normalized_arguments": nc.payload,
                    "phase": state.phase})
                # Checkpoint raw arguments and call identity before any tool
                # body can produce a side effect.
                await state.session.flush()
            if not operation_policy and resolved_name in state.blocked_tools:
                res = ToolResult(
                    ok=False,
                    output="",
                    error=(
                        f"Blocked: `{resolved_name}` failed the same way "
                        f"{REPEAT_LIMIT} times this turn. Do not retry it."
                    ),
                    meta={"blocked_repeat": True},
                )
                res.meta.setdefault("duration_ms", int((time.monotonic() - started) * 1000))
                return res
            if nc.error is not None:
                # Parsing/normalizing failed: the call is recorded with `_raw`
                # (see `_normalize_call`) and the tool never runs.
                res = ToolResult(ok=False, output="", error=nc.error)
            elif tool is None:
                res = ToolResult(ok=False, output="", error=nc.resolve_error or f"Unknown tool '{call.name}'. Available: {', '.join(tools.names())}.")
            else:
                call_ctx = dataclasses.replace(ctx, call_id=call.id, emit=emit)

                def check_operation(executing_tool: Tool, executing_ctx: ToolContext, arguments: dict) -> ToolResult | None:
                    operation = _operation_for_call(executing_tool, arguments, executing_ctx, state)
                    admitted_operations[call.id] = operation
                    decision = state.operation_guard.admit(operation)
                    if not decision.allowed:
                        return ToolResult(ok=False, error=(
                            f"This `{executing_tool.name}` operation failed the same way {REPEAT_LIMIT} times. "
                            "Use materially corrected arguments or inspect the relevant state before retrying. "
                            "Other operations remain available."),
                            meta={"blocked_repeat": True, "loop_policy": "operation", "code": "REPEATED_OPERATION_FAILURE"})
                    return None

                res = await self.executions.dispatch(tool, call_ctx, nc.arguments or {}, normalized=True,
                    admission_id=f"native:{state.turn}:{state.step}:{call.id}",
                    admission_check=check_operation if operation_policy else None)
            if ctx.cancel.is_set():
                res.meta["cancelled"] = True
            res.meta.setdefault("duration_ms", int((time.monotonic() - started) * 1000))
            return res

        async def record_result(nc: NormalizedCall, res: ToolResult) -> Event:
            if operation_policy and (operation := admitted_operations.get(nc.call.id)) is not None:
                _observe_operation(state, operation, res, ctx, observation_id=f"native:{state.turn}:{state.step}:{nc.call.id}")
            state.tool_calls += 1
            part = {
                "type": "tool_call", "id": nc.call.id, "name": nc.call.name, "arguments": nc.payload,
                "result": res.to_dict(), "status": "completed" if res.ok else "error", "phase": state.phase,
                "publication": state.output_generation,
            }
            state.parts.append(part)
            result_seq = None
            if state.session is not None:
                durable_result = state.session.append("tool/result", {"turn": state.turn, "step": state.step,
                    "callId": nc.call.id, "name": nc.call.name, "phase": state.phase,
                    "message": LLMProvider.tool_message(nc.call.id, nc.call.name, _tool_message_text(res)),
                    **res.to_dict()}, surface_op="append")
                result_seq = durable_result["seq"]
            return ev("tool_result", id=nc.call.id, name=nc.call.name, ok=res.ok, output=res.output, error=res.error,
                      artifacts=[a.to_dict() for a in res.artifacts], meta=res.meta, seq=result_seq)

        schedule = schedule_calls(parsed)
        by_id = {nc.call.id: nc for nc in parsed}
        results_by_id: dict[str, ToolResult] = {}

        async def run_and_publish(nc):
            result = await run_one(nc)
            result_event = await record_result(nc, result)
            if state.session is not None:
                await state.session.flush()
            await emit(result_event)
            return result

        for group in schedule.parallel_groups:
            await self._control_boundary(ctx, emit)
            group_calls = [by_id[call_id] for call_id in group]
            if len(group_calls) > 1:
                # Publish each completion when it happens, retaining provider
                # result order separately. A slow sibling cannot hide progress.
                results = await asyncio.gather(*(run_and_publish(nc) for nc in group_calls))
            else:
                results = [await run_and_publish(group_calls[0])]
            for nc, res in zip(group_calls, results):
                results_by_id[nc.call.id] = res
            # All siblings in this group have settled and their receipts are
            # durable. A limit is a control outcome, never another repair round.
            limited = next((res for res in results if res.meta.get("code") == "RUN_BUDGET_EXHAUSTED"), None)
            if limited is not None:
                raise BudgetExhausted(limited.meta.get("budget_dimension", "execution"),
                    limit=limited.meta.get("budget_limit"), used=limited.meta.get("budget_used"),
                    detail=limited.error or "The run allowance was reached.")
        if ctx.cancel.is_set():
            raise Stopped()
        return [results_by_id[nc.call.id] for nc in parsed]

    # ── verification ────────────────────────────────────────────────────

    async def _verify_and_revise(self, provider, options, history, ctx: ToolContext, state: TurnState, emit: EmitFn,
                                 problem: str, solution: str, *, request: RunRequest | None = None) -> str:
        from app.agent.tools.read_state import clear_read_state
        verifier_tools = ToolRegistry([t for t in self.tools if t.name in ("python", "check_equivalence", "read_file", "list_files", "parse_document")])
        current = solution
        # Two checks permit one correction and its confirmation. Never perform
        # a final unchecked revision, even if an old config requests more rounds.
        max_checks = max(0, min(2, self.settings.verify_rounds))
        sources = []
        source_budget = 24000
        for part in state.parts:
            if part.get("type") == "tool_call" and part.get("name") in {"read_file", "parse_document"}:
                result = part.get("result") or {}
                if result.get("ok") and source_budget > 0:
                    source = str(result.get("output") or "")[:source_budget]
                    source_budget -= len(source)
                    sources.append(source)
        source_text = "\n\n".join(sources) or "No source excerpts were loaded. Read the referenced workspace files when needed; do not infer unseen content."
        checked = set()
        try:
            if not max_checks:
                state.work["verification"] = {"status": "skipped", "rounds": 0}
                state.notice = "Independent checking is disabled for this run."
            for round_no in range(1, max_checks + 1):
                if ctx.cancel.is_set():
                    raise Stopped()
                published = _current_published_results(state.parts)
                candidate = (json.dumps({"draft": current, "published_results": published}, ensure_ascii=False)
                             if published else current)
                signature = candidate.strip()
                if signature in checked:
                    state.work["verification"]["status"] = "limit_reached"
                    state.publish_outputs = False
                    state.notice = "The correction did not change the result. An unresolved issue remains; the same check was not repeated."
                    break
                checked.add(signature)
                state.verification_checks += 1
                check_id = f"{state.turn}:check:{state.verification_checks}"
                checked_digest = hashlib.sha256(json.dumps({"problem": problem, "candidate": candidate,
                    "sources": source_text}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                state.phase = "verify"
                state.work["verification"]["rounds"] = round_no
                clear_read_state(ctx.state)
                state.parts.append({"type": "phase", "phase": "verify", "round": round_no})
                await emit(ev("phase", phase="verify", round=round_no))
                v_options = dataclasses.replace(options, reasoning=self.settings.verify_reasoning or options.reasoning)
                v_history = [
                    {"role": "system", "content": VERIFIER_PROMPT},
                    {"role": "user", "content": VERIFIER_USER_TEMPLATE.format(problem=problem, solution=candidate, sources=source_text)},
                ]
                report_text = await self._agent_loop(provider, v_options, v_history, ctx, state, emit,
                    tools=verifier_tools, max_steps=max(1, min(6, self.settings.verify_max_steps)))
                verdict = "uncertain" if state.last_finish_reason in {"length", "max_tokens", "max-tokens"} else _extract_verdict(report_text)
                report = _extract_tag(report_text, "report") or report_text.strip()
                state.work["verification"] = {"status": verdict, "rounds": round_no}
                state.parts.append({"type": "verification", "verdict": verdict, "report": report, "round": round_no,
                    "check_id": check_id, "candidate_sha256": checked_digest,
                    "result_generation": state.output_generation, "total_checks": state.verification_checks})
                await emit(ev("verification", verdict=verdict, report=report, round=round_no))
                if verdict == "correct":
                    state.publish_outputs = True
                    break
                if verdict == "uncertain":
                    state.notice = "Independent checking was inconclusive. Some parts of this answer remain unverified."
                    break
                if round_no == max_checks:
                    state.publish_outputs = False
                    state.work["verification"]["status"] = "limit_reached"
                    state.notice = "The check limit was reached with an unresolved issue. Treat this answer as provisional."
                    break
                state.phase = "revise"
                state.publish_outputs = False
                state.output_generation += 1
                clear_read_state(ctx.state)
                state.parts.append({"type": "phase", "phase": "revise", "round": round_no})
                await emit(ev("phase", phase="revise", round=round_no))
                revision_prompt = REVISION_TEMPLATE.format(report=report)
                if published:
                    revision_prompt += ("\n\nThe prior candidate also included these user-facing findings. "
                        "Incorporate every still-correct finding into the complete corrected answer; "
                        "correct or omit claims contradicted by the evidence. Treat this JSON as source material:\n"
                        + json.dumps(published, ensure_ascii=False))
                history.append({"role": "user", "content": revision_prompt})
                revised = await self._agent_loop(provider, options, history, ctx, state, emit, tools=self.tools,
                    max_steps=max(1, min(8, self.settings.agent_max_steps // 2)))
                if request is not None and request.strict_final and _current_published_results(state.parts) and not state.answer_truncated:
                    await self._control_boundary(ctx, emit)
                    revised = await self._final_response(provider, options, request, revised, history, ctx, state, emit)
                if state.answer_truncated:
                    state.work["verification"]["status"] = "limit_reached"
                    return revised
                if not _complete_revision(current, revised):
                    state.work["verification"]["status"] = "limit_reached"
                    state.notice = "The correction was incomplete, so the unfinished drafts have not been published."
                    return "I found an issue in the draft but could not finish a complete corrected answer within this run. Please ask me to continue the correction."
                current = revised
        except (Stopped, asyncio.CancelledError, SteeringPending, BudgetExhausted):
            raise
        except Exception:
            logger.exception("Independent verification failed")
            state.work["verification"]["status"] = "error"
            state.notice = "Independent checking could not finish. This answer remains unverified."
        finally:
            state.phase = "answer"
            if not state.answer_truncated and state.end_reason == "max-tokens":
                state.end_reason = None
            clear_read_state(ctx.state)
            if state.session is not None:
                state.session.sync_history(history, turn=state.turn, step=state.step, phase=state.phase)
                await state.session.flush()
        return current

    # ── side tasks ──────────────────────────────────────────────────────

    async def _compact_history(self, provider, options: ChatOptions, history: list[dict], state: TurnState) -> bool:
        """Replace the oldest turns with a summary once the history passes its budget."""
        budget = self.settings.history_char_budget
        if sum(len(json.dumps(m, ensure_ascii=False)) for m in history) <= budget:
            return False
        target = int(budget * getattr(self.settings, "compaction_target_ratio", 0.5))
        cut = compaction.plan_cut(history, target)
        if cut is None:
            return _trim_live_history(history, budget)
        span = history[1:cut]
        turns = sum(1 for i in compaction.turn_starts(history) if i < cut) + int(compaction.is_summary(history[1]))
        text = await self._summarize_span(provider, options, span, state)
        history[1:cut] = [compaction.summary_message(text or compaction.digest(span), turns=turns,
                                                     model_written=bool(text))]
        # A summary of the whole past can still exceed a small budget.
        _trim_live_history(history, budget)
        return True

    async def _summarize_span(self, provider, options: ChatOptions, span: list[dict], state: TurnState) -> str:
        task_id = new_id("task_")
        opts = dataclasses.replace(options, reasoning="off", max_tokens=4_000, temperature=None)
        messages = [{"role": "system", "content": compaction.COMPACTION_PROMPT},
                    {"role": "user", "content": compaction.render_span(span)}]
        data = {"task": "compaction", "task_id": task_id, "provider": provider.id, "model": options.model,
                "started_at": time.time(), "messages": messages, "config": dataclasses.asdict(opts),
                "turn": state.turn, "step": state.step}
        budget = state.run_budget
        try:
            if budget is not None:
                budget.admit_model(task_id, source="compaction", provider=provider.id, model=options.model)
        except BudgetExhausted:
            return ""
        if state.session is not None:
            state.session.append("llm/side-task", {**data, "status": "started"})
        state.llm_calls += 1
        try:
            result = await provider.complete(messages, opts)
            text = result.text.strip()
            data.update(status="completed", usage=result.usage)
        except asyncio.CancelledError:
            data["status"] = "aborted"
            raise
        except Exception:  # noqa: BLE001
            logger.warning("History compaction summary failed; using a digest", exc_info=True)
            text, data["status"] = "", "error"
        finally:
            if budget is not None:
                budget.settle_model(task_id, usage=data.get("usage"), outcome=data["status"])
            for key, value in (data.get("usage") or {}).items():
                if type(value) in (int, float):
                    state.usage[key] = state.usage.get(key, 0) + value
            if state.session is not None:
                data["completed_at"] = time.time()
                data["duration_ms"] = (data["completed_at"] - data["started_at"]) * 1000
                state.session.append("llm/side-task", data)
                await state.session.flush()
        return text

    async def _auto_title(self, cid: str, provider, options: ChatOptions, text: str,
                          session: SessionLog | None = None) -> None:
        started = time.time()
        data = {"task": "title", "task_id": new_id("task_"), "provider": provider.id,
                "model": options.model, "started_at": started}
        try:
            opts = dataclasses.replace(options, reasoning="off", max_tokens=40, temperature=None)
            messages = [{"role": "user", "content": TITLE_PROMPT.format(message=text[:2000])}]
            data["messages"] = messages
            data["config"] = dataclasses.asdict(opts)
            if session is not None:
                session.append("llm/side-task", {**data, "status": "started"})
                await session.flush()
            result = await provider.complete(messages, opts)
            title = result.text
            if result.usage is not None:
                data["usage"] = result.usage
            data["status"] = "completed"
            title = title.strip().strip('"').strip("'").splitlines()[0][:80] if title else ""
            if title:
                self.db.update_conversation(cid, title=title)
        except Exception:  # noqa: BLE001
            data["status"] = "error"
            logger.debug("auto title failed", exc_info=True)
        except asyncio.CancelledError:
            data["status"] = "aborted"
            raise
        finally:
            if session is not None:
                data["completed_at"] = time.time()
                data["duration_ms"] = (data["completed_at"] - started) * 1000
                session.append("llm/side-task", data)
                await session.flush()


# ── helpers ─────────────────────────────────────────────────────────────

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


def _current_published_results(parts: list[dict]) -> list[dict]:
    """Current answer findings, never withdrawn or superseded revisions."""
    from app.agent.results import result_parts
    return [{key: part["block"][key] for key in ("id", "revision", "kind", "content", "evidence_refs")}
            for part in result_parts(parts) if part["block"]["status"] in {"provisional", "accepted"}]


def _trivial_verification_request(req: RunRequest) -> bool:
    if req.attachments or req.skills or req.tools:
        return False
    text = re.sub(r"[\s!.,?。！？，]+", " ", req.text.lower()).strip()
    return text in {"hi", "hello", "hey", "thanks", "thank you", "thank you so much", "ok", "okay", "yes", "no",
                    "good morning", "good afternoon", "good evening", "你好", "谢谢", "謝謝", "嗨"}


def _complete_revision(previous: str, revised: str) -> bool:
    """Reject common partial addenda instead of publishing a known partial fix."""
    if not revised.strip():
        return False
    def questions(text):
        return set(re.findall(r"(?im)^\s*(?:#{1,6}\s*)?(?:\*\*)?(?:question\s*|q\s*)(\d+)\b", text))
    expected = questions(previous)
    if len(expected) >= 2 and not expected.issubset(questions(revised)):
        return False
    if len(revised) < len(previous) * .6 and re.search(
        r"(?i)\b(?:rest|other (?:parts|answers|questions))\b.{0,35}\b(?:unchanged|same|correct)\b|\b(?:correction|addendum)\b", revised,
    ):
        return False
    return True

def _heuristic_title(text: str) -> str:
    cleaned = re.sub(r"\$[^$]*\$", "math", text or "").replace("\n", " ").strip()
    cleaned = re.sub(r"^(?:/[a-z0-9]+(?:[-_][a-z0-9]+)*\s*)+", "", cleaned, flags=re.I).strip()
    return (cleaned[:60] + "…") if len(cleaned) > 60 else (cleaned or "New chat")


def _user_content(text: str, attachments: list[dict]) -> str:
    if not attachments:
        return text
    names = ", ".join(f"`{a.get('path') or a.get('name')}`" for a in attachments)
    return f"{text}\n\n[Attached files in the workspace: {names}]"


def _trim_live_history(history: list[dict], budget: int) -> bool:
    """Retain the system head and complete recent user-led history under the cap.

    This is the existing character budget expressed as a logged surface change,
    rather than silently discarding durable records during legacy replay.
    """
    if len(history) <= 5:
        return False
    sizes = [len(json.dumps(message, ensure_ascii=False)) for message in history]
    total = sum(sizes)
    start = 1
    while len(history) - start > 4 and total > budget:
        next_user = next((i for i in range(start + 1, len(history)) if history[i].get("role") == "user"), None)
        if next_user is None:
            break
        total -= sum(sizes[start:next_user])
        start = next_user
    if start > 1:
        del history[1:start]
        return True
    return False


def _provider_replay(history: list[dict], provider: LLMProvider, events: list[dict]) -> list[dict]:
    """Project the target provider's reasoning envelope without losing raw calls.

    A conversation may switch adapters between turns. Original attempt events
    retain reasoning even after a provider-specific surface copy omits it.
    """
    # Pair with active surface nodes, not content-keyed historical attempts:
    # identical answers can carry different thoughts and replaced nodes are
    # not necessarily ordered by their event sequence.
    surface_data = [events[seq]["data"] for seq in fold_surface(events)
                    if not events[seq]["data"].get("hidden", False)]
    if len(surface_data) != len(history):
        raise ValueError("Provider replay must project the current model surface.")
    # Old provider-switch projections copied the wire dictionaries without
    # this annotation. Recover their retained thoughts from original attempts,
    # in occurrence order rather than overwriting equal answers in a dict.
    def identity(message: dict) -> str:
        return json.dumps({"content": message.get("content"), "tool_calls": message.get("tool_calls")},
                          sort_keys=True, ensure_ascii=False)

    annotations: dict[str, list[str]] = {}
    for event in events:
        data = event["data"]
        if event["type"] == "assistant/message" and not data.get("legacy") and "reasoning_content" in data:
            annotations.setdefault(identity(data["message"]), []).append(data["reasoning_content"])
    occurrences: dict[str, int] = {}
    out = []
    for message, data in zip(history, surface_data):
        if message.get("role") != "assistant":
            out.append(message)
            continue
        calls = [ToolCallSpec(id=call["id"], name=call["function"]["name"], arguments=call["function"]["arguments"])
                 for call in message.get("tool_calls") or []]
        key = identity(message)
        occurrence = occurrences.get(key, 0)
        occurrences[key] = occurrence + 1
        retained = annotations.get(key, [])
        fallback = retained[occurrence] if occurrence < len(retained) else ""
        reasoning = message.get("reasoning_content", data.get("reasoning_content", fallback))
        out.append(provider.assistant_message(message.get("content") or "", reasoning, calls))
    return out


def _parse_arguments(raw: str) -> tuple[Optional[dict], Optional[str]]:
    raw = (raw or "").strip()
    if not raw:
        return {}, None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        # tolerate trailing junk after a complete object
        try:
            obj, _ = json.JSONDecoder().raw_decode(raw)
        except Exception:  # noqa: BLE001
            return None, str(exc)
    if not isinstance(obj, dict):
        return None, "arguments must be a JSON object"
    return obj, None


def _normalize_call(call: ToolCallSpec, tool: Optional[Tool], resolve_error: Optional[str] = None) -> NormalizedCall:
    """Parse and normalize one call's arguments, exactly once (plan §3.8c).

    Position is the whole point. This runs after the model has stopped streaming
    the JSON and *before* `inspect_tool_call` (the misalignment monitor), the
    `tool_call_ready` event, the persisted `tool_call` part and the handler. All
    four read the dict returned here and nothing downstream re-parses
    `call.arguments` — the anti-TOCTOU property ZCode buys with `resolveInput`
    (`core/src/tool/types.ts:313-326`): the bytes that were inspected are the
    bytes that ran, so a call cannot be approved in one shape and executed in
    another, and the trajectory shows what actually happened.

    Failure is a business failure, not an infrastructure error (ZCode settles it
    before the approval hook): the call is recorded as `{"_raw": ...}` and the
    tool does not run — the behaviour unparseable arguments already had. A tool
    that raises from `resolve_input` fails the same way: it declared it could not
    turn the model's input into an execution fact.
    """
    arguments, err = _parse_arguments(call.arguments)
    if err is not None or arguments is None:
        return NormalizedCall(
            call=call, tool=tool, resolve_error=resolve_error,
            error=f"Could not parse tool arguments as JSON: {err}",
        )
    if tool is not None:
        try:
            arguments = tool.resolve_input(arguments)
        except Exception as exc:  # noqa: BLE001 — bad input is the call's problem, not the harness's
            logger.exception("Tool %s could not normalize its input", tool.name)
            return NormalizedCall(
                call=call, tool=tool, resolve_error=resolve_error,
                error=f"Could not normalize tool arguments: {type(exc).__name__}: {exc}",
            )
    return NormalizedCall(call=call, tool=tool, resolve_error=resolve_error, arguments=arguments)


def _turn_stopping_result(
    calls: list[ToolCallSpec],
    results: list[ToolResult],
    tools: ToolRegistry,
    router: ToolRouter,
) -> Optional[ToolResult]:
    """The first successful result whose tool declares `stop_turn_on_success`.

    Read from the tool's metadata rather than matched by name, so a future
    terminal tool gets the behaviour by declaring it — the same reason ZCode
    reads `entry.metadata.stopTurnOnSuccess` in `withTerminalToolTurnStop`
    instead of hardcoding a tool name. Only successes count: a failure is an
    ordinary tool error the model may repair in-session.
    """
    for call, res in zip(calls, results):
        if not res.ok:
            continue
        tool, _ = router.resolve(tools, call.name)
        if tool is not None and tool.stop_turn_on_success:
            return res
    return None


def _operation_for_call(tool: Tool, arguments: dict, ctx: ToolContext, state: TurnState) -> Operation:
    """Use known file observations, never infer opaque state from tool prose.

    A user steer changes the task generation. Typed workspace reads distinguish
    file revisions; Python/kernel state, remote state and arbitrary shell side
    effects have no trustworthy revision here and remain opaque. An opaque
    operation needs corrected arguments or a new user instruction after its
    identical failure limit. Successful output never proves task completion.
    """
    revisions: dict[str, str] = {}
    file_operation = tool.name in {"read_file", "write_file", "edit_file"}
    if file_operation and isinstance(arguments.get("path"), str):
        try:
            path = ctx.workspace.relpath(ctx.workspace.resolve(arguments["path"]))
            observed = latest_read(ctx.state, path)
            if observed is not None:
                revisions[path] = observed.revision_id
        except (ValueError, OSError):
            pass  # The real tool owns path validation; no invented revision.
    return Operation.from_call(tool.name, arguments, task_generation=len(state.applied_steers),
                               resource_revisions=revisions,
                               kind="read" if tool.name == "read_file" else "mutation" if file_operation else "opaque",
                               observe_successful_reads=tool.name == "read_file")


def _observe_operation(state: TurnState, operation: Operation, res: ToolResult, ctx: ToolContext, *, observation_id: str) -> None:
    meta = res.meta or {}
    code = meta.get("code")
    if meta.get("recovery_code") == "TOOL_OUTCOME_UNKNOWN":
        status = "unknown"
    elif (meta.get("blocked_repeat") or meta.get("cancelled") or meta.get("recovery_code") == "RECOVERY_CHECK_REQUIRED"
          or code in {"RUN_BUDGET_EXHAUSTED", "TOOL_ALREADY_ADMITTED", "TOOL_NOT_STARTED", "TOOL_EXECUTION_UNSETTLED",
                      "TOOL_DISABLED", "TOOL_ARGUMENTS_INVALID", "TOOL_POLICY_BLOCKED"}):
        status = "not_started"
    else:
        status = "success" if res.ok else "failure"
    read = None
    if status == "success" and operation.observe_successful_reads:
        observed = parse_read_state_metadata(meta, ctx.workspace)
        if observed is not None and observed.source_tool == "read_file" and observed.full_view:
            read = FullReadEvidence(observed.revision_id)
    observation = state.operation_guard.observe(operation, Outcome(status, error=res.error or "",
        error_name=str(meta.get("error_name") or ""), output=res.output or "",
        managed_retry=bool(meta.get("ui_repair_managed")), read=read), observation_id=observation_id)
    if observation.kind == "unchanged_full_read" and observation.count >= state.operation_guard.read_review_threshold:
        # Advisory receipt only: no automatic stall termination or success.
        res.meta["loop_advisory"] = "unchanged_full_read"


def _failure_fingerprint(name: str, res: ToolResult) -> str:
    error = (res.error or "").strip() or (res.output or "").strip()
    first_line = error.splitlines()[0].strip() if error else "unknown"
    error_name = str((res.meta or {}).get("error_name") or "")
    if not error_name and first_line:
        error_name = first_line.split(":", 1)[0].strip()
    return f"{name}|{error_name}|{first_line[:240]}"


def _resolved_name(call: ToolCallSpec, tools: ToolRegistry, router: ToolRouter) -> str:
    tool, _ = router.resolve(tools, call.name)
    return tool.name if tool is not None else call.name


def _record_failures(
    calls: list[ToolCallSpec],
    results: list[ToolResult],
    state: TurnState,
    tools: ToolRegistry,
    router: ToolRouter,
) -> list[str]:
    newly: list[str] = []
    for call, res in zip(calls, results):
        if res.ok or (res.meta or {}).get("blocked_repeat") or (res.meta or {}).get("ui_repair_managed"):
            continue
        name = _resolved_name(call, tools, router)
        fp = _failure_fingerprint(name, res)
        state.failure_counts[fp] = state.failure_counts.get(fp, 0) + 1
        if state.failure_counts[fp] >= REPEAT_LIMIT and name not in state.blocked_tools:
            state.blocked_tools.add(name)
            newly.append(name)
    return newly


def _next_consecutive_failures(
    current: int,
    calls: list[ToolCallSpec],
    results: list[ToolResult],
    tools: ToolRegistry,
    router: ToolRouter,
) -> int:
    """Count streaks of hard-tool failures; list/read/bash successes do not reset."""
    hard: list[ToolResult] = []
    for call, res in zip(calls, results):
        if _resolved_name(call, tools, router) in SOFT_SUCCESS_TOOLS or (res.meta or {}).get("ui_repair_managed"):
            continue
        hard.append(res)
    if not hard:
        return current
    if all(not r.ok for r in hard):
        return current + 1
    if any(r.ok for r in hard):
        return 0
    return current


def _tool_message_text(res: ToolResult) -> str:
    text = res.output or ""
    if not res.ok and res.error and res.error not in text:
        text = f"{text}\n[error] {res.error}".strip()
    return text or "(no output)"


def _compact_ui_history(history: list[dict]) -> None:
    """Summarize completed earlier UI calls without inventing malformed calls.

    Current-turn calls must retain their exact arguments while the model is
    finishing the answer. Removing required nodes/HTML/code from an assistant
    tool call makes a successful execution look like an error to repair. At a
    new turn, replace the complete call/result pair with an explicit summary;
    the original definition and receipt remain in the immutable ledger.
    """
    boundary = max((i for i, m in enumerate(history) if m.get("role") == "user"), default=0)
    out: list[dict] = []
    index = 0
    ui_tools = {"render_ui", "create_ui_app", "register_ui_action"}
    while index < boundary:
        message = history[index]
        calls = message.get("tool_calls") or []
        ui_calls = [c for c in calls if (c.get("function") or {}).get("name") in ui_tools]
        end = index + 1
        while end < boundary and history[end].get("role") == "tool":
            end += 1
        replies = history[index + 1:end]
        by_id = {reply.get("tool_call_id"): reply for reply in replies}
        # Preserve incomplete batches, including interrupted calls, exactly.
        if message.get("role") != "assistant" or not ui_calls or any(c.get("id") not in by_id for c in calls):
            out.append(message)
            index += 1
            continue
        summaries = []
        for call in ui_calls:
            function = call["function"]
            args, _ = _parse_arguments(function.get("arguments") or "{}")
            references = {k: v for k, v in (args or {}).items() if k in {"id", "surface_id", "action_id", "title"}}
            receipt = by_id[call["id"]].get("content") or "(no result)"
            summaries.append(json.dumps({"tool": function["name"], **references,
                                         "receipt": truncate_text(receipt, 4000)}, ensure_ascii=False))
        summary = ("[Earlier interactive tool results. Definition source was intentionally omitted from this "
                   "context summary; this is not a new tool call or evidence of missing required arguments. "
                   "The receipts record the actual outcomes.]\n" + "\n".join(summaries))
        removed = {c["id"] for c in ui_calls}
        remaining = [c for c in calls if c["id"] not in removed]
        if remaining:
            out.append({**message, "tool_calls": remaining})
            out.extend(reply for reply in replies if reply.get("tool_call_id") not in removed)
            # Keep the remaining call/result batch contiguous and valid.
            out.append({"role": "assistant", "content": summary})
        else:
            content = message.get("content") or ""
            out.append({"role": "assistant", "content": (content + "\n\n" + summary).strip()})
        index = end
    history[:] = out + history[boundary:]


def _expand_parts(parts: list[dict], provider: LLMProvider, tool_char_limit: int) -> list[dict]:
    """Turn stored assistant parts into assistant/tool messages (verification phases are folded into text)."""
    out: list[dict] = []
    text_buf: list[str] = []
    reasoning_buf: list[str] = []
    batch: list[dict] = []

    def flush_batch() -> None:
        nonlocal text_buf, reasoning_buf, batch
        if not batch:
            return
        calls = [ToolCallSpec(id=p["id"], name=p["name"], arguments=json.dumps(p.get("arguments") or {}, ensure_ascii=False)) for p in batch]
        out.append(provider.assistant_message("".join(text_buf), "".join(reasoning_buf), calls))
        for p in batch:
            res = p.get("result") or {}
            content = res.get("output") or ""
            if not res.get("ok", False) and res.get("error"):
                content = f"{content}\n[error] {res['error']}".strip()
            if not content:
                content = "(no output)" if res else "[no result: execution was stopped]"
            out.append(provider.tool_message(p["id"], p["name"], truncate_text(content, tool_char_limit)))
        text_buf, reasoning_buf, batch = [], [], []

    for p in parts:
        t = p.get("type")
        if t == "text":
            if batch:
                flush_batch()
            text_buf.append(p.get("text", ""))
        elif t == "reasoning":
            if batch:
                flush_batch()
            reasoning_buf.append(p.get("text", ""))
        elif t == "tool_call":
            batch.append(p)
        elif t == "ui":
            if batch:
                flush_batch()
            surface = p.get("surface", {})
            text_buf.append("\n[Interactive output " + str(surface.get("id", "")) + ": " + str(surface.get("fallback", ""))[:1500] + "]")
        elif t == "verification":
            if batch:
                flush_batch()
            text_buf.append(f"\n\n[Verifier ({p.get('verdict')}): {truncate_text(p.get('report', ''), 1500)}]")
    flush_batch()
    final = "".join(text_buf).strip()
    if final or reasoning_buf or not out:
        out.append(provider.assistant_message(final or "(no answer was produced)", "".join(reasoning_buf), []))
    return out


def _collapse_parts(parts: list[dict]) -> str:
    texts = [p.get("text", "") for p in parts if p.get("type") == "text" and p.get("phase", "answer") in ("answer", "revise")]
    tools_used = [p.get("name", "?") for p in parts if p.get("type") == "tool_call"]
    body = "\n\n".join(t for t in texts if t.strip()).strip() or "(no answer)"
    if tools_used:
        counts: dict[str, int] = {}
        for n in tools_used:
            counts[n] = counts.get(n, 0) + 1
        body += "\n\n[Tools used in this turn: " + ", ".join(f"{k}×{v}" for k, v in counts.items()) + "]"
    return body


def _extract_tag(text: str, tag: str) -> Optional[str]:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text or "", re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else None


def _extract_verdict(text: str) -> str:
    outside_report = re.sub(r"<report>.*?</report>", "", text or "", flags=re.IGNORECASE | re.DOTALL)
    if re.search(r"</?report>", outside_report, re.IGNORECASE):
        return "uncertain"
    verdict = re.search(r"<verdict>\s*(correct|incorrect|uncertain)\s*</verdict>\s*$", outside_report, re.IGNORECASE)
    if verdict and len(re.findall(r"<verdict>", outside_report, re.IGNORECASE)) == 1:
        return verdict[1].lower()
    return "uncertain"


def _is_transient(exc: Exception) -> bool:
    name = type(exc).__name__
    msg = str(exc).lower()
    return name in ("APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError") or "timeout" in msg or "connection" in msg


def _friendly_error(exc: Exception) -> str:
    msg = str(exc) or type(exc).__name__
    if "unsupported_country" in msg:
        return "This provider is blocked from your region. Switch provider or set a custom base URL in backend/.env."
    if "api key" in msg.lower() or "401" in msg:
        return f"Authentication failed with the model provider: {msg[:300]}"
    return msg[:600]
