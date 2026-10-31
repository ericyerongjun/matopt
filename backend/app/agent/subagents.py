"""Sub-agents: helper agents a turn can spawn, ported from Codex's multi-agent v2.

Codex lets an agent hand self-contained work to child agents
(``core/src/agent/``, ``tools/handlers/multi_agents_spec.rs``): ``spawn_agent``
starts a child with a task and a role, the parent keeps working, and the
child's final answer arrives in the parent's mailbox; ``wait_agent``,
``send_message``, ``followup_task``, ``interrupt_agent`` and ``list_agents``
manage the children. Roles (``agent-roles``) configure a child's tools and
instructions, and the spawn edges are kept in a graph store
(``agent-graph-store``).

MatOpt's version runs each child through the same ``AgentHarness._agent_loop``
as the parent, with:

* the parent's workspace (children are told to write to their own paths) and
  a kernel of their own, so Python state never collides;
* the parent's ``RunBudget``, so children spend from the same model, tool and
  deadline allowance instead of multiplying it (Codex's ``RolloutBudget``);
* the parent's cancellation, pause, approvals and turn diff;
* a role's tool allowlist, which never includes the sub-agent tools
  themselves (Codex's default ``agent_max_depth`` of 1);
* at most ``subagent_max_concurrent`` children running at once and
  ``subagent_max_per_turn`` spawned per turn.

Children have no durable session log of their own; their task, status and
final summary are kept in the ``subagents`` table and in the parent's tool
results. Only those public facts reach the UI (``subagent`` events and
activity items nested under the agent); messages between agents do not.
When the parent's turn ends, children still running are stopped.

Not ported: the message board (Slack-like channels between agents), agent
identity registration with ChatGPT, unloading idle agents, resuming a child
from its rollout.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.agent.events import ToolResult, ev
from app.agent.tools.base import Tool, ToolContext, ToolRegistry

logger = logging.getLogger(__name__)

AGENT_TOOLS = frozenset({"spawn_agent", "wait_agent", "send_message", "followup_task", "interrupt_agent", "list_agents"})
# Never given to a child: they talk to the user, publish the turn's answer or
# change the conversation's interactive state.
PARENT_ONLY = AGENT_TOOLS | {"publish_result", "report_progress", "update_plan", "render_ui", "create_ui_app",
                             "register_ui_action", "memory_note", "data_agent"}
NICKNAMES = ("Ada", "Gauss", "Noether", "Euler", "Turing", "Hopper", "Riemann", "Fermat", "Hilbert",
             "Kovalevskaya", "Ramanujan", "Lovelace", "Germain", "Galois", "Cantor", "Poincaré")
FINAL_STATUSES = frozenset({"completed", "errored", "interrupted", "closed"})


@dataclass(frozen=True)
class Role:
    name: str
    description: str
    tools: Optional[tuple[str, ...]]      # None: every tool except PARENT_ONLY
    instructions: str
    max_steps: Optional[int] = None


BUILTIN_ROLES: dict[str, Role] = {
    "default": Role("default", "General helper with the parent's tools.", None,
                    "Complete the task you were given and report the result."),
    "analyst": Role("analyst", "Computes and checks: Python, files and math tools.",
                    ("python", "read_file", "write_file", "edit_file", "list_files", "find_files", "grep",
                     "check_equivalence", "wolfram_alpha", "lean_check", "parse_document"),
                    "You compute and verify. Save substantial outputs to files under your own folder and "
                    "report their paths with the key numbers."),
    "researcher": Role("researcher", "Finds and reads sources and documents; does not modify files.",
                       ("web_search", "web_fetch", "read_file", "list_files", "find_files", "grep", "parse_document"),
                       "You research. Cite every source you rely on with its URL or file path. Do not "
                       "modify the workspace."),
    "reviewer": Role("reviewer", "Checks work for errors; does not modify files.",
                     ("read_file", "list_files", "find_files", "grep", "python", "check_equivalence"),
                     "You review the work described in your task and report concrete errors with evidence. "
                     "Do not modify workspace files."),
}

CHILD_PROMPT = """# Your role
You are {nickname}, a sub-agent ({role}) working for a parent agent, not directly for the user.
{instructions}
Work only on your task. You share the parent's workspace: write new files under `agents/{slug}/`
and never overwrite files you did not create. When finished, reply with a concise final report:
what you did, the results, file paths, and anything uncertain. That report is all the parent sees."""


def load_roles(settings: Any) -> dict[str, Role]:
    """Built-in roles plus ``<data_dir>/agents/*.toml`` (Codex agent role files)."""
    roles = dict(BUILTIN_ROLES)
    directory = Path(settings.data_dir) / "agents"
    for path in sorted(directory.glob("*.toml")) if directory.is_dir() else ():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            name = str(data.get("name") or path.stem)
            if not re.fullmatch(r"[a-z0-9_-]{1,40}", name):
                raise ValueError("role name must be lowercase letters, digits, - or _")
            tools = data.get("tools")
            roles[name] = Role(name, str(data.get("description", "")), tuple(tools) if tools else None,
                               str(data.get("instructions", "")),
                               int(data["max_steps"]) if data.get("max_steps") else None)
        except (OSError, ValueError, TypeError, tomllib.TOMLDecodeError) as exc:
            logger.warning("Ignoring agent role %s: %s", path, exc)
    return roles


@dataclass
class SubAgent:
    id: str
    path: str
    nickname: str
    role: Role
    task: str
    slug: str
    status: str = "pending"
    summary: str = ""
    history: list = field(default_factory=list)
    inbox: list = field(default_factory=list)
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    task_handle: Optional[asyncio.Task] = None
    created_at: float = field(default_factory=time.time)
    item_id: str = ""
    reported: bool = False        # the parent has this run's report
    runs: int = 0

    def public(self) -> dict:
        return {"id": self.id, "path": self.path, "nickname": self.nickname, "role": self.role.name,
                "status": self.status, "task": self.task[:500], "summary": self.summary[:2000]}


class ChildKernels:
    """A kernel per child, held in the parent's pool so the idle reaper sees it."""

    def __init__(self, pool: Any, key: str, workspace: Any):
        self.pool, self.key, self.workspace = pool, key, workspace

    def get(self, _conversation_id: str):
        from app.agent.kernel import KernelSession
        sessions = self.pool._sessions
        if self.key not in sessions:
            sessions[self.key] = KernelSession(self.key, self.workspace)
        return sessions[self.key]

    def peek(self, _conversation_id: str):
        return self.pool._sessions.get(self.key)

    async def restart(self, _conversation_id: str) -> None:
        await self.get(self.key).restart()

    async def shutdown(self, _conversation_id: str = "") -> None:
        await self.pool.shutdown(self.key)


class SubAgentManager:
    """The sub-agents of one parent turn."""

    def __init__(self, harness: Any, *, provider: Any, options: Any, parent_ctx: ToolContext, parent_state: Any,
                 emit: Any, settings: Any):
        self.harness, self.provider, self.options = harness, provider, options
        self.parent_ctx, self.parent_state, self.emit, self.settings = parent_ctx, parent_state, emit, settings
        self.roles = load_roles(settings)
        self.agents: dict[str, SubAgent] = {}
        self.mailbox: list[SubAgent] = []
        self.changed = asyncio.Event()
        self.parent_history: Optional[list] = None
        self.semaphore = asyncio.Semaphore(int(getattr(settings, "subagent_max_concurrent", 3)))
        self.spawned = 0

    # ── lookup ──────────────────────────────────────────────────────────

    def find(self, target: str) -> Optional[SubAgent]:
        target = str(target or "").strip()
        for agent in self.agents.values():
            if target in (agent.id, agent.path, agent.slug, agent.nickname, agent.path.rsplit("/", 1)[-1]):
                return agent
        return None

    def _persist(self, agent: SubAgent) -> None:
        try:
            self.harness.db.upsert_subagent({**agent.public(), "conversation_id": self.parent_ctx.conversation_id,
                "run_turn": self.parent_state.turn, "role": agent.role.name, "created_at": agent.created_at,
                "parent_id": None})
        except Exception:  # noqa: BLE001 - bookkeeping must not fail the agent
            logger.warning("Could not persist sub-agent %s", agent.id, exc_info=True)

    async def _publish(self, agent: SubAgent, kind: str) -> None:
        self._persist(agent)
        await self.emit(ev("subagent", kind=kind, **agent.public()))
        activity = self.parent_state.activity
        if activity is None:
            return
        status = {"pending": "queued", "running": "running", "completed": "completed", "errored": "failed",
                  "interrupted": "cancelled", "closed": "cancelled"}[agent.status]
        changes: dict[str, Any] = {"status": status}
        if kind == "started" and not agent.item_id:
            agent.item_id = f"{activity.id}:agent:{agent.id}"
            changes.update(kind="agent", tool="spawn_agent", label=f"{agent.nickname} · {agent.role.name}",
                           purpose=agent.task[:200], parent_id=f"{activity.id}:phase:{activity.phase}")
        if agent.status in FINAL_STATUSES:
            changes["summary"] = {"completed": "Finished its task.", "errored": "Could not finish its task.",
                                  "interrupted": "Was stopped.", "closed": "Was closed."}[agent.status]
        if agent.item_id:
            await activity.item(agent.item_id, **changes)

    # ── spawning ────────────────────────────────────────────────────────

    async def spawn(self, task_name: str, message: str, role_name: str, fork: str) -> SubAgent:
        if self.spawned >= int(getattr(self.settings, "subagent_max_per_turn", 6)):
            raise ValueError("This turn already started the maximum number of sub-agents. Do the work yourself "
                             "or reuse an agent with followup_task.")
        role = self.roles.get(role_name or "default")
        if role is None:
            raise ValueError(f"Unknown role {role_name!r}. Roles: {', '.join(sorted(self.roles))}.")
        slug = re.sub(r"[^a-z0-9_]+", "_", (task_name or "task").lower()).strip("_")[:40] or "task"
        if any(agent.slug == slug for agent in self.agents.values()):
            slug = f"{slug}_{len(self.agents) + 1}"
        nickname = NICKNAMES[len(self.agents) % len(NICKNAMES)]
        agent = SubAgent(id="agent_" + uuid.uuid4().hex[:12], path=f"/root/{slug}", nickname=nickname, role=role,
                         task=message, slug=slug)
        system = self.parent_history[0] if self.parent_history else {"role": "system", "content": ""}
        child_system = {"role": "system", "content": (system.get("content") or "") + "\n\n" + CHILD_PROMPT.format(
            nickname=nickname, role=role.name, instructions=role.instructions, slug=slug)}
        agent.history.append(child_system)
        if fork == "all" and self.parent_history:
            # Codex's fork keeps the conversation, not the tool traffic: user
            # and assistant text only, so the child sees what was asked and
            # decided without the parent's raw tool outputs.
            for item in self.parent_history[1:]:
                if item.get("role") in ("user", "assistant") and isinstance(item.get("content"), str) \
                        and item["content"].strip() and not item.get("tool_calls"):
                    agent.history.append({"role": item["role"], "content": item["content"]})
        self.agents[agent.id] = agent
        self.spawned += 1
        await self._publish(agent, "started")
        agent.task_handle = asyncio.create_task(self._run(agent, message), name=f"subagent:{agent.id}")
        return agent

    def _tools(self, role: Role) -> ToolRegistry:
        tools = [t for t in self.harness.tools if t.name not in PARENT_ONLY
                 and (role.tools is None or t.name in role.tools)]
        return ToolRegistry(tools)

    async def _run(self, agent: SubAgent, message: str) -> None:
        from app.agent.budget import BudgetExhausted
        from app.agent.harness import Stopped, TurnState
        parent_ctx, parent_state = self.parent_ctx, self.parent_state
        watcher = asyncio.create_task(self._propagate_cancel(agent))
        child_state = TurnState()
        child_state.run_budget = parent_state.run_budget
        child_state.turn = parent_state.turn
        child_state.provider_id = parent_state.provider_id
        child_state.is_subagent = True
        # Followups rerun the same agent, so the prefix also counts runs.
        agent.runs += 1
        child_state.admission_prefix = f"{agent.id}.{agent.runs}"
        max_steps = agent.role.max_steps or int(getattr(self.settings, "subagent_max_steps", 20))
        child_state.work_step_limit = max_steps
        child_state.mailbox = lambda history: self._drain_inbox(agent, history)
        child_ctx = ToolContext(
            conversation_id=parent_ctx.conversation_id, workspace=parent_ctx.workspace, emit=None, cancel=agent.cancel,
            state={key: parent_ctx.state[key] for key in ("run_budget", "turn_diff", "approvals", "web_search",
                                                          "document_provider", "document_record_call",
                                                          "tool_dispatch") if key in parent_ctx.state}
                  | {"plan": [], "skills": [], "ui_enabled": False},
            kernels=ChildKernels(parent_ctx.kernels, f"{parent_ctx.conversation_id}#{agent.id}", parent_ctx.workspace)
                    if parent_ctx.kernels is not None else None,
            settings=parent_ctx.settings)
        emit = self._child_emit(agent, child_state)
        child_ctx.emit = emit
        agent.history.append({"role": "user", "content": message})
        try:
            async with self.semaphore:
                if agent.cancel.is_set():
                    raise Stopped()
                agent.status = "running"
                self._persist(agent)
                await self._publish(agent, "started")
                text = await self.harness._agent_loop(
                    self.provider, self.options, agent.history, child_ctx, child_state, emit,
                    tools=self._tools(agent.role), max_steps=max_steps, query=message)
            agent.summary = (text or "").strip() or "(The sub-agent finished without a report.)"
            agent.status = "completed"
        except (Stopped, asyncio.CancelledError):
            agent.status = "closed" if agent.status == "closed" else "interrupted"
            agent.summary = agent.summary or "Stopped before finishing."
        except BudgetExhausted as exc:
            agent.status, agent.summary = "errored", f"Stopped by the run's limits: {exc}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Sub-agent %s failed", agent.id, exc_info=True)
            agent.status, agent.summary = "errored", f"{type(exc).__name__}: {str(exc)[:300]}"
        finally:
            watcher.cancel()
            for key, value in child_state.usage.items():
                if type(value) in (int, float):
                    parent_state.usage[key] = parent_state.usage.get(key, 0) + value
            parent_state.llm_calls += child_state.llm_calls
            kind = {"completed": "completed", "errored": "errored", "closed": "closed"}.get(agent.status, "interrupted")
            await self._publish(agent, kind)
            agent.reported = False
            self.mailbox.append(agent)
            agent.done.set()
            self.changed.set()

    async def _propagate_cancel(self, agent: SubAgent) -> None:
        await self.parent_ctx.cancel.wait()
        agent.cancel.set()

    def _child_emit(self, agent: SubAgent, child_state: Any):
        """Map a child's tool events onto the parent's activity, nested under the agent."""
        forward = {"approval_request", "approval_resolved", "guardian_review"}

        async def emit(event) -> None:
            if event.type in forward:
                await self.emit(event)
                return
            activity = self.parent_state.activity
            if activity is None:
                return
            step = f"{agent.id}:{child_state.step}"
            data = event.data
            if event.type == "tool_call_ready":
                name = data.get("resolved_name") or data.get("name", "")
                await activity.queued(step, data["id"], name, data.get("arguments"))
                item_id = activity.calls.get((step, data["id"]))
                if item_id and agent.item_id:
                    await activity.item(item_id, parent_id=agent.item_id)
            elif event.type == "tool_running":
                await activity.running(step, data["id"])
            elif event.type == "tool_activity_progress":
                await activity.measured(step, data["id"], data.get("progress", {}))
            elif event.type == "tool_result":
                await activity.result(step, data["id"], data.get("name", ""), data.get("ok"),
                                      data.get("meta") or {}, data.get("artifacts") or [])
        return emit

    # ── mail ────────────────────────────────────────────────────────────

    @staticmethod
    def _report(agent: SubAgent) -> str:
        # Codex's InterAgentCompletionMessage, as a harness-tagged note.
        return (f"[Sub-agent report] {agent.nickname} ({agent.path}, role {agent.role.name}) is {agent.status}.\n"
                f"Task: {agent.task[:500]}\nReport:\n{agent.summary[:8000]}")

    def drain_mailbox(self, history: list) -> bool:
        """Give the parent the reports that arrived since its last step."""
        if not self.mailbox:
            return False
        agents, self.mailbox = self.mailbox, []
        for agent in agents:
            agent.reported = True
        history.append({"role": "user", "content": "\n\n".join(self._report(a) for a in agents)})
        return True

    async def settle(self) -> None:
        """Wait for every running child; its report then waits in the mailbox."""
        if any(not a.done.is_set() for a in self.agents.values()):
            await self.wait([], timeout=600.0, all_of=True, consume=False)

    @staticmethod
    def _drain_inbox(agent: SubAgent, history: list) -> bool:
        if not agent.inbox:
            return False
        notes, agent.inbox = agent.inbox, []
        history.append({"role": "user", "content": "\n\n".join(f"[Message from the parent agent] {n}" for n in notes)})
        return True

    # ── control ─────────────────────────────────────────────────────────

    async def wait(self, targets: list[str], timeout: float, *, all_of: bool = False, consume: bool = True) -> dict:
        chosen = [a for a in (self.find(t) for t in targets) if a is not None] if targets else list(self.agents.values())
        if not chosen:
            return {"agents": [], "timed_out": False}
        pending = [a for a in chosen if not a.done.is_set()]
        if pending:
            waiters = [asyncio.create_task(a.done.wait()) for a in pending]
            stop = asyncio.create_task(self.parent_ctx.cancel.wait())
            try:
                if all_of:
                    gathered = asyncio.ensure_future(asyncio.gather(*waiters))
                    await asyncio.wait({gathered, stop}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                    gathered.cancel()
                else:
                    await asyncio.wait({*waiters, stop}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (*waiters, stop):
                    task.cancel()
        finished = [a for a in chosen if a.done.is_set()]
        if consume:
            # Reports returned here are not delivered a second time by the mailbox.
            for agent in finished:
                agent.reported = True
            self.mailbox = [a for a in self.mailbox if a not in finished]
        return {"agents": [a.public() for a in chosen], "timed_out": not finished}

    async def send(self, target: str, message: str, *, followup: bool) -> SubAgent:
        agent = self.find(target)
        if agent is None:
            raise ValueError(f"No sub-agent {target!r}.")
        if agent.status == "closed":
            raise ValueError(f"{agent.nickname} was closed.")
        if not agent.done.is_set():
            agent.inbox.append(message)
            return agent
        if not followup:
            raise ValueError(f"{agent.nickname} has finished; use followup_task to give it more work.")
        agent.done.clear()
        agent.cancel = asyncio.Event()
        agent.status, agent.summary = "pending", ""
        agent.task_handle = asyncio.create_task(self._run(agent, message), name=f"subagent:{agent.id}")
        return agent

    async def interrupt(self, target: str, *, close: bool = False) -> SubAgent:
        agent = self.find(target)
        if agent is None:
            raise ValueError(f"No sub-agent {target!r}.")
        if close:
            agent.status = "closed"
        agent.cancel.set()
        if agent.task_handle is not None:
            await asyncio.wait({agent.task_handle}, timeout=10)
        return agent

    async def close_all(self) -> None:
        """Stop every child still running when the parent's turn ends."""
        running = [a for a in self.agents.values() if not a.done.is_set()]
        for agent in running:
            agent.status = "closed"
            agent.cancel.set()
        handles = [a.task_handle for a in running if a.task_handle is not None]
        if handles:
            await asyncio.wait(handles, timeout=15)
        kernels = self.parent_ctx.kernels
        for agent in self.agents.values():
            key = f"{self.parent_ctx.conversation_id}#{agent.id}"
            if key in getattr(kernels, "_sessions", {}):
                await kernels.shutdown(key)


# ── tools ───────────────────────────────────────────────────────────────

class _AgentTool(Tool):
    requires_flags = ("subagents",)
    side_effect_scope = "workspace"
    risk_level = "medium"

    @staticmethod
    def manager(ctx: ToolContext) -> SubAgentManager:
        manager = ctx.state.get("subagents")
        if not isinstance(manager, SubAgentManager):
            raise ValueError("Sub-agents are not available in this turn.")
        return manager


def _listing(agents: list[dict]) -> str:
    lines = []
    for a in agents:
        line = f"- {a['nickname']} ({a['path']}, {a['role']}): {a['status']}"
        if a["status"] in FINAL_STATUSES and a.get("summary"):
            line += f"\n  Report: {a['summary'][:4000]}"
        lines.append(line)
    return "\n".join(lines) or "No sub-agents."


class SpawnAgentTool(_AgentTool):
    name = "spawn_agent"
    description = (
        "Start a sub-agent on a self-contained task while you keep working; its final report arrives "
        "automatically before your next step, or collect it with wait_agent. Spawn only when the work splits "
        "into independent parts that are worth running in parallel (separate analyses, a literature search "
        "alongside a computation, an independent check) — never for a quick step you can do yourself, and "
        "keep the critical path in your own hands. Give each agent everything it needs in `message`: inputs, "
        "file paths, the expected deliverable. Agents share the workspace; assign them different output files. "
        "Wait for every agent you start before your final answer. Roles: default (your tools), analyst "
        "(Python, files, math), researcher (web and documents, read-only), reviewer (checks work, read-only)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "task_name": {"type": "string", "description": "Short snake_case name, e.g. 'fit_models'."},
            "message": {"type": "string", "description": "The complete task for the agent."},
            "role": {"type": "string", "description": "default | analyst | researcher | reviewer, or a configured role."},
            "fork": {"type": "string", "enum": ["none", "all"],
                     "description": "'all' gives the agent this conversation's messages (without tool output); default 'none'."},
        },
        "required": ["task_name", "message"],
    }

    async def run(self, ctx: ToolContext, task_name: str, message: str, role: str = "default", fork: str = "none",
                  **_: Any) -> ToolResult:
        try:
            agent = await self.manager(ctx).spawn(task_name, message, role, fork)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        return ToolResult(ok=True, output=f"Started {agent.nickname} at {agent.path} (role {agent.role.name}). "
                          "Its report will arrive when it finishes.",
                          meta={"agent_id": agent.id, "path": agent.path, "nickname": agent.nickname})


class WaitAgentTool(_AgentTool):
    name = "wait_agent"
    side_effect_scope = "none"
    risk_level = "low"
    description = ("Wait until a sub-agent finishes (or the timeout passes) and return the statuses and final "
                   "reports. With no targets, waits for any of this turn's agents.")
    parameters = {
        "type": "object",
        "properties": {
            "targets": {"type": "array", "items": {"type": "string"}, "description": "Agent names or paths."},
            "timeout_seconds": {"type": "number", "description": "Default 60, max 600."},
        },
    }

    async def run(self, ctx: ToolContext, targets: Optional[list] = None, timeout_seconds: float = 60,
                  **_: Any) -> ToolResult:
        try:
            manager = self.manager(ctx)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        result = await manager.wait([str(t) for t in targets or []], min(max(float(timeout_seconds), 1.0), 600.0))
        note = "Timed out; the agents are still running.\n" if result["timed_out"] else ""
        return ToolResult(ok=True, output=note + _listing(result["agents"]), meta={"timed_out": result["timed_out"]})


class SendMessageTool(_AgentTool):
    name = "send_message"
    description = "Give a running sub-agent more information; it reads it before its next step."
    parameters = {"type": "object", "properties": {"target": {"type": "string"}, "message": {"type": "string"}},
                  "required": ["target", "message"]}

    async def run(self, ctx: ToolContext, target: str, message: str, **_: Any) -> ToolResult:
        try:
            agent = await self.manager(ctx).send(target, message, followup=False)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        return ToolResult(ok=True, output=f"Sent to {agent.nickname}.")


class FollowupTaskTool(_AgentTool):
    name = "followup_task"
    description = ("Give a sub-agent its next task. A finished agent starts again with its earlier context; "
                   "a running one receives it as a message.")
    parameters = {"type": "object", "properties": {"target": {"type": "string"}, "message": {"type": "string"}},
                  "required": ["target", "message"]}

    async def run(self, ctx: ToolContext, target: str, message: str, **_: Any) -> ToolResult:
        try:
            agent = await self.manager(ctx).send(target, message, followup=True)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        return ToolResult(ok=True, output=f"{agent.nickname} has the follow-up task.")


class InterruptAgentTool(_AgentTool):
    name = "interrupt_agent"
    description = "Stop a sub-agent's current work. Its partial report, if any, is returned."
    parameters = {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}

    async def run(self, ctx: ToolContext, target: str, **_: Any) -> ToolResult:
        try:
            agent = await self.manager(ctx).interrupt(target)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        return ToolResult(ok=True, output=f"{agent.nickname} is {agent.status}. {agent.summary}".strip())


class ListAgentsTool(_AgentTool):
    name = "list_agents"
    side_effect_scope = "none"
    risk_level = "low"
    description = "List this turn's sub-agents with their status and, when finished, their reports."
    parameters = {"type": "object", "properties": {}}

    async def run(self, ctx: ToolContext, **_: Any) -> ToolResult:
        try:
            manager = self.manager(ctx)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        return ToolResult(ok=True, output=_listing([a.public() for a in manager.agents.values()]))


def subagent_tools() -> list[Tool]:
    return [SpawnAgentTool(), WaitAgentTool(), SendMessageTool(), FollowupTaskTool(), InterruptAgentTool(),
            ListAgentsTool()]
