"""What one turn carries: its request, its mutable state and its control exceptions.

These live apart from the harness so the pieces of a turn (the event projector,
the model step, the finalizer, sub-agents) can share them without importing it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from app.agent.budget import BudgetExhausted, RunBudget
from app.agent.events import Event
from app.agent.loop_policy import OperationGuard
from app.agent.session import SessionLog
from app.agent.transcript import Transcript

EmitFn = Callable[[Event], Awaitable[None]]


class Stopped(Exception):
    """Raised inside the loop when the user cancelled the run."""


class BudgetExceeded(BudgetExhausted):
    """Observed spend cannot admit another configured provider dispatch."""

    def __init__(self, message: str):
        super().__init__("observed_spend", limit=None, used=None, detail=message)


class ContextOverflow(Exception):
    """The provider rejected the request as longer than its context window.

    Raised only when nothing was streamed, so the step can compact and be
    dispatched again (Codex's ``ContextWindowExceeded`` recovery).
    """


class HookBlocked(RuntimeError):
    """A prompt hook refused the request; its reason is written for the user."""


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
    language: Optional[str] = None  # reply language chosen in the UI (app.speech.LANGUAGES code)
    mode: Optional[str] = None      # "auto" | "mat" (chat and explain) | "opt" (work on files); app/agent/intent.py


@dataclass
class SteerNote:
    text: str
    skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)


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
    context_window: Optional[int] = None    # of the turn's resolved ModelSpec
    last_prompt_tokens: Optional[int] = None
    # Length of the history the last usage report covered. Tokens appended
    # since are estimated on top of it (Codex's get_total_token_usage).
    last_usage_len: Optional[int] = None
    overflow_compactions: int = 0
    stop_hook_blocks: int = 0
    mode: Any = None              # IntentDecision (app/agent/intent.py)
    # Sub-agents (app/agent/subagents.py): a child's state is marked so it
    # never takes the user's steers or runs Stop hooks; ``mailbox`` delivers
    # sub-agent reports to a parent, or the parent's messages to a child.
    is_subagent: bool = False
    # Budget admission ids are unique per run; a child agent shares the run's
    # budget and its turn number, so it needs its own prefix.
    admission_prefix: str = "native"
    model_dispatches: int = 0
    mailbox: Optional[Callable[[list], bool]] = None
    # A sub-agent has no session of its own; its spend is booked to the parent's.
    spend_log: Optional[SessionLog] = None
    subagents: Any = None
    transcript: Transcript = field(default_factory=Transcript)
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
