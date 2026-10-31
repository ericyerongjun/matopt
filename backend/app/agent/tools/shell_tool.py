"""``bash`` tool: run a shell command inside the workspace."""

from __future__ import annotations

import asyncio
import codecs
import os
import signal
import time
from typing import Any

from app.agent.child_env import build_child_env
from app.agent.events import ToolResult
from app.agent.exec_output import HeadTailBuffer, exec_header
from app.agent.exec_rules import ExecPolicy
from app.agent.sandbox import SandboxUnavailable, build_policy, wrap_argv
from app.agent.tools.base import Tool, ToolContext, output_bound_bytes, truncate_text
from app.agent.tools.read_state import invalidate_execution_changes
from app.agent.workspace import is_internal_output
from app.config import settings

# Fork bombs and raw writes to block devices have no argv prefix to match.
BLOCKED_PATTERNS = (":(){", "> /dev/sd", ">/dev/sd", "> /dev/disk", ">/dev/disk")


class ShellTool(Tool):
    name = "bash"
    destructive = False
    concurrent_safe = False
    side_effect_scope = "system"
    risk_level = "high"
    needs_approval = True
    ui_kind = "terminal"
    description = (
        "Run a shell command in the conversation workspace (bash, macOS/Linux). Use it for file management, "
        "installing Python packages into the agent's environment (`pip install <pkg>`), running scripts, "
        "converting files, or inspecting large files with head/tail/wc. `python` and `pip` refer to the same "
        "environment as the python tool. Commands are killed after `timeout` seconds. Prefer the python tool for computation. "
        "For a long job (a training run, a server, a slow download) pass `background: true`: the command keeps running, "
        "this call returns its first output and a `session_id`, and `bash_poll` reads more output, sends input or stops it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command line to run with bash -c."},
            "timeout": {"type": "integer", "description": "Seconds before the command is killed (default 120, max 900). "
                        "With background, the job's total lifetime (default and max 3600)."},
            "background": {"type": "boolean", "description": "Keep the command running and return a session_id (default false)."},
            "yield_seconds": {"type": "number", "description": "With background: how long to wait for first output (default 10, max 30)."},
        },
        "required": ["command"],
    }

    def __init__(self, default_timeout: int = 120, max_timeout: int = 900, max_output_chars: int = 12000,
                 exec_policy: ExecPolicy | None = None, sessions: Any = None):
        from app.agent.tools.exec_sessions import ExecSessionStore
        self.exec_policy = exec_policy or ExecPolicy()
        self.sessions = sessions or ExecSessionStore(max_output_chars=max_output_chars)
        self.default_timeout = default_timeout
        self.max_timeout = max_timeout
        self.max_output_chars = max_output_chars
        self.timeout_ms = int(default_timeout) * 1000
        self.max_output_bytes = output_bound_bytes(max_output_chars) + 1000

    def prepare(self, ctx: ToolContext, cmd: str) -> tuple[list[str], dict[str, str]] | ToolResult:
        """Policy checks, then the (sandboxed) argv and environment for ``cmd``."""
        lowered = cmd.lower()
        for pat in BLOCKED_PATTERNS:
            if pat in lowered:
                return ToolResult(ok=False, output="", error=f"Command refused: contains blocked pattern {pat!r}.")
        verdict = self.exec_policy.evaluate(cmd)
        approved = ctx.call_id in (ctx.state.get("approved_calls") or ())
        if not verdict.allowed and not (verdict.decision == "prompt" and approved):
            return ToolResult(ok=False, output="", error=verdict.message(),
                              meta={"code": "EXEC_POLICY_" + verdict.decision.upper()})
        policy = build_policy(getattr(ctx, "settings", None) or settings, ctx.workspace.root)
        env = build_child_env(workspace_root=ctx.workspace.root, extra=policy.child_env())
        # Not a login shell: ``-l`` would source ~/.bash_profile and ~/.profile,
        # and HOME is the workspace, so the agent could plant a profile that
        # runs before every later command. Codex spawns non-login shells too.
        try:
            return wrap_argv(["/bin/bash", "--noprofile", "--norc", "-c", cmd], policy), env
        except SandboxUnavailable as exc:
            return ToolResult(ok=False, output="", error=f"Sandbox unavailable: {exc}", meta={"code": "SANDBOX_UNAVAILABLE"})

    async def run(self, ctx: ToolContext, command: str, timeout: int | None = None, background: bool = False,
                  yield_seconds: float | None = None, **_: Any) -> ToolResult:
        cmd = (command or "").strip()
        if not cmd:
            return ToolResult(ok=False, output="", error="No command provided.")
        prepared = self.prepare(ctx, cmd)
        if isinstance(prepared, ToolResult):
            return prepared
        argv, env = prepared
        if background:
            return await self.sessions.start(ctx, argv, env, cmd, lifetime=timeout, yield_seconds=yield_seconds)
        limit = min(int(timeout or self.default_timeout), self.max_timeout)
        before = ctx.workspace.snapshot()
        started = time.monotonic()

        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(ctx.workspace.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        captured = HeadTailBuffer()
        timed_out = False
        aborted = False

        async def pump() -> None:
            assert proc.stdout is not None
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            while True:
                # Fixed-size reads, not readline: a single huge line would
                # overrun StreamReader's 64 KiB line limit and kill the pump.
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                captured.write(chunk)
                await ctx.progress(decoder.decode(chunk))

        pump_task = asyncio.create_task(pump())
        try:
            while True:
                try:
                    await asyncio.wait_for(asyncio.shield(pump_task), timeout=0.5)
                    break
                except asyncio.TimeoutError:
                    if ctx.cancel.is_set():
                        aborted = True
                        break
                    if time.monotonic() - started > limit:
                        timed_out = True
                        break
            if timed_out or aborted:
                _kill(proc)
                try:
                    await asyncio.wait_for(pump_task, timeout=3)
                except Exception:  # noqa: BLE001
                    pump_task.cancel()
            await proc.wait()
        finally:
            # A dispatcher deadline or transport disconnect can cancel this
            # coroutine between polling iterations. Always reap the process
            # group before releasing the workspace execution lease.
            if proc.returncode is None:
                _kill(proc)
                await proc.wait()
            if not pump_task.done():
                pump_task.cancel()
            await asyncio.gather(pump_task, return_exceptions=True)
            changed = ctx.workspace.changed_since(before)
            read_state_meta = invalidate_execution_changes(ctx.state, ctx.workspace, before, changed)

        output = captured.text()
        artifacts = []
        for rel in changed:
            if is_internal_output(rel):
                continue
            try:
                artifacts.append(ctx.workspace.artifact_for(rel))
            except Exception:  # noqa: BLE001
                continue
        for a in artifacts:
            output += f"\n[file written: {a.path}]"
        code = proc.returncode
        ok = code == 0 and not timed_out and not aborted
        error = None
        if timed_out:
            error = f"Command timed out after {limit} seconds and was killed."
        elif aborted:
            error = "Command was stopped by the user."
        elif code != 0:
            error = f"Command exited with code {code}."
        body = output.strip() or "(no output)"
        shown = truncate_text(body, self.max_output_chars)
        header = exec_header(
            exit_code=code, wall_ms=int((time.monotonic() - started) * 1000), status=error,
            original_chars=captured.total, shown_chars=len(shown),
        )
        text = f"{header}\n{shown}"
        return ToolResult(
            ok=ok,
            output=text,
            artifacts=artifacts,
            error=error,
            meta={"exit_code": code, "duration_ms": int((time.monotonic() - started) * 1000), **read_state_meta},
        )


def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
