"""Background shell sessions: ``bash`` with ``background: true`` plus ``bash_poll``.

Codex's ``unified_exec`` (``core/src/unified_exec/``) lets a command outlive
the tool call: ``exec_command`` waits up to a yield time and returns the output
so far with a session id, and ``write_stdin`` feeds input or, with no input,
polls for more. A long training run or a local server then no longer has to
fit inside one call's timeout, and the agent can keep working while it runs.

MatOpt keeps the same lifecycle with pipes instead of a PTY:

* ``ExecSessionStore.start`` spawns the already policy-checked, sandboxed
  argv from ``ShellTool.prepare`` with stdin open, and returns after
  ``yield_seconds`` (or sooner, when the command exits).
* ``bash_poll`` returns only the output produced since the previous read,
  optionally after writing ``input``, and can stop the job.
* Output is held in bounded buffers (``HeadTailBuffer``); a job that prints
  forever cannot grow the backend's memory.
* At most ``MAX_SESSIONS`` jobs per conversation. A new one evicts the
  least-recently-used *finished* job first, then the oldest running one
  except the newest ``PROTECTED`` (Codex's pruning rule). A job is killed when
  its lifetime ends, and every job when the backend shuts down.

Sessions live in the backend process. After a restart a session id is
unknown, and ``bash_poll`` says so instead of guessing what happened.
"""

from __future__ import annotations

import asyncio
import codecs
import itertools
import os
import signal
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from app.agent.events import ToolResult
from app.agent.exec_output import HeadTailBuffer, exec_header
from app.agent.tools.base import Tool, ToolContext, truncate_text
from app.agent.tools.read_state import invalidate_execution_changes
from app.agent.workspace import is_internal_output

MAX_SESSIONS = 8
PROTECTED = 2
DEFAULT_LIFETIME = 3600
DEFAULT_YIELD = 10.0
MAX_YIELD = 30.0
MAX_POLL_WAIT = 300.0
PENDING_BYTES = 256 * 1024


@dataclass
class ExecSession:
    id: str
    conversation_id: str
    command: str
    proc: asyncio.subprocess.Process
    started: float
    deadline: float
    before: dict
    total: HeadTailBuffer = field(default_factory=HeadTailBuffer)
    pending: HeadTailBuffer = field(default_factory=lambda: HeadTailBuffer(PENDING_BYTES))
    last_used: float = field(default_factory=time.monotonic)
    reader: Optional[asyncio.Task] = None
    reaper: Optional[asyncio.Task] = None
    output_event: asyncio.Event = field(default_factory=asyncio.Event)
    killed_reason: str = ""

    @property
    def running(self) -> bool:
        return self.proc.returncode is None

    def take_pending(self) -> HeadTailBuffer:
        taken, self.pending = self.pending, HeadTailBuffer(PENDING_BYTES)
        self.output_event.clear()
        return taken


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


class ExecSessionStore:
    def __init__(self, max_output_chars: int = 12_000):
        self.max_output_chars = max_output_chars
        self._sessions: dict[str, ExecSession] = {}
        self._ids = itertools.count(1)

    def get(self, conversation_id: str, session_id: str) -> Optional[ExecSession]:
        session = self._sessions.get(str(session_id))
        return session if session is not None and session.conversation_id == conversation_id else None

    def _prune(self, conversation_id: str) -> None:
        mine = sorted((s for s in self._sessions.values() if s.conversation_id == conversation_id),
                      key=lambda s: s.last_used)
        while len(mine) >= MAX_SESSIONS:
            finished = [s for s in mine if not s.running]
            victim = finished[0] if finished else mine[0] if len(mine) > PROTECTED else None
            if victim is None:
                return
            if victim.running:
                victim.killed_reason = "evicted to make room for a newer background job"
                _kill_group(victim.proc)
            self._sessions.pop(victim.id, None)
            mine.remove(victim)

    async def start(self, ctx: ToolContext, argv: list[str], env: dict, command: str, *,
                    lifetime: Optional[float] = None, yield_seconds: Optional[float] = None) -> ToolResult:
        self._prune(ctx.conversation_id)
        lifetime = min(float(lifetime or DEFAULT_LIFETIME), DEFAULT_LIFETIME)
        before = ctx.workspace.snapshot()
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(ctx.workspace.root), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env, start_new_session=True)
        now = time.monotonic()
        session = ExecSession(id=str(next(self._ids)), conversation_id=ctx.conversation_id, command=command,
                              proc=proc, started=now, deadline=now + lifetime, before=before)
        session.reader = asyncio.create_task(self._read(session))
        session.reaper = asyncio.create_task(self._reap(session))
        self._sessions[session.id] = session
        wait = min(max(float(yield_seconds if yield_seconds is not None else DEFAULT_YIELD), 0.25), MAX_YIELD)
        return await self._collect(ctx, session, wait, wait_for_output=False)

    async def poll(self, ctx: ToolContext, session_id: str, *, input_text: Optional[str] = None,
                   wait_seconds: float = 5.0, kill: bool = False) -> ToolResult:
        session = self.get(ctx.conversation_id, session_id)
        if session is None:
            return ToolResult(ok=False, error=f"No background job {session_id!r} in this conversation. It may have "
                              "been evicted, or the backend restarted; check the workspace for its output files.",
                              meta={"code": "EXEC_SESSION_UNKNOWN"})
        session.last_used = time.monotonic()
        if kill and session.running:
            session.killed_reason = "stopped on request"
            _kill_group(session.proc)
            await session.proc.wait()
        if input_text and session.running and session.proc.stdin is not None:
            try:
                session.proc.stdin.write(input_text.encode("utf-8"))
                await session.proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
        wait = min(max(float(wait_seconds), 0.0), MAX_POLL_WAIT)
        return await self._collect(ctx, session, wait, wait_for_output=not input_text)

    async def _read(self, session: ExecSession) -> None:
        assert session.proc.stdout is not None
        while True:
            chunk = await session.proc.stdout.read(65536)
            if not chunk:
                break
            session.total.write(chunk)
            session.pending.write(chunk)
            session.output_event.set()
        session.output_event.set()

    async def _reap(self, session: ExecSession) -> None:
        try:
            await asyncio.wait_for(session.proc.wait(), timeout=max(0.0, session.deadline - time.monotonic()))
        except asyncio.TimeoutError:
            session.killed_reason = "its lifetime ended"
            _kill_group(session.proc)
            await session.proc.wait()
        session.output_event.set()

    async def _collect(self, ctx: ToolContext, session: ExecSession, wait: float, *,
                       wait_for_output: bool) -> ToolResult:
        """Wait until exit, or ``wait`` seconds (an empty poll returns at the first new output)."""
        deadline = time.monotonic() + wait
        while session.running and not ctx.cancel.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if wait_for_output and session.pending.total:
                # Let a burst finish before returning it.
                await asyncio.sleep(min(0.2, remaining))
                break
            try:
                await asyncio.wait_for(session.output_event.wait(), timeout=min(remaining, 0.5))
            except asyncio.TimeoutError:
                pass
            if not wait_for_output:
                session.output_event.clear()
        if not session.running and session.reader is not None:
            await asyncio.wait({session.reader}, timeout=2)  # drain what the process left in the pipe
        taken = session.take_pending()
        changed = ctx.workspace.changed_since(session.before)
        read_state_meta = invalidate_execution_changes(ctx.state, ctx.workspace, session.before, changed)
        session.before = ctx.workspace.snapshot()
        artifacts = []
        for rel in changed:
            if not is_internal_output(rel):
                try:
                    artifacts.append(ctx.workspace.artifact_for(rel))
                except Exception:  # noqa: BLE001
                    continue
        body = taken.text().strip() or "(no new output)"
        shown = truncate_text(body, self.max_output_chars)
        code = session.proc.returncode
        wall = int((time.monotonic() - session.started) * 1000)
        if session.running:
            status = f"Process running with session ID {session.id}"
        elif session.killed_reason:
            status = f"Process was killed: {session.killed_reason}"
        else:
            status = None
        header = exec_header(exit_code=code, wall_ms=wall, status=status,
                             original_chars=taken.total, shown_chars=len(shown))
        text = f"{header}\n{shown}" + "".join(f"\n[file written: {a.path}]" for a in artifacts)
        ok = session.running or (code == 0 and not session.killed_reason)
        return ToolResult(ok=ok, output=text, artifacts=artifacts,
                          error=None if ok else (f"Command exited with code {code}." if not session.killed_reason
                                                 else f"Process was killed: {session.killed_reason}."),
                          meta={"session_id": session.id, "running": session.running, "exit_code": code,
                                "duration_ms": wall, **read_state_meta})

    async def shutdown(self) -> None:
        sessions, self._sessions = list(self._sessions.values()), {}
        for session in sessions:
            if session.running:
                _kill_group(session.proc)
        for session in sessions:
            tasks = [t for t in (session.reader, session.reaper) if t is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


class BashPollTool(Tool):
    name = "bash_poll"
    destructive = False
    concurrent_safe = False
    side_effect_scope = "system"
    risk_level = "high"
    needs_approval = True
    ui_kind = "terminal"
    description = (
        "Read new output from a background `bash` job, send it input, or stop it. Without `input`, waits up to "
        "`wait_seconds` for new output and returns it; the result says whether the job is still running."
    )
    parameters = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "The session_id a background bash call returned."},
            "input": {"type": "string", "description": "Text to write to the job's stdin (include \\n to end a line)."},
            "wait_seconds": {"type": "number", "description": "How long to wait for output (default 5, max 300)."},
            "kill": {"type": "boolean", "description": "Stop the job (default false)."},
        },
        "required": ["session_id"],
    }

    def __init__(self, store: ExecSessionStore):
        self.store = store

    async def run(self, ctx: ToolContext, session_id: str, input: Optional[str] = None,
                  wait_seconds: float = 5.0, kill: bool = False, **_: Any) -> ToolResult:
        return await self.store.poll(ctx, str(session_id), input_text=input, wait_seconds=wait_seconds, kill=kill)
