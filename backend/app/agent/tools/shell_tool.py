"""``bash`` tool: run a shell command inside the workspace."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from typing import Any

from app.agent.child_env import build_child_env
from app.agent.events import ToolResult
from app.agent.tools.base import Tool, ToolContext, output_bound_bytes, truncate_text
from app.agent.tools.read_state import invalidate_execution_changes
from app.agent.workspace import is_internal_output

BLOCKED_PATTERNS = (
    "rm -rf /", "rm -rf ~", "rm -rf *", "mkfs", ":(){", "shutdown", "reboot", "> /dev/sd",
    "sudo ", "dd if=", "chmod -R 777 /", "curl | sh", "wget | sh",
)


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
        "environment as the python tool. Commands are killed after `timeout` seconds. Prefer the python tool for computation."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command line to run with bash -c."},
            "timeout": {"type": "integer", "description": "Seconds before the command is killed (default 120, max 900)."},
        },
        "required": ["command"],
    }

    def __init__(self, default_timeout: int = 120, max_timeout: int = 900, max_output_chars: int = 12000):
        self.default_timeout = default_timeout
        self.max_timeout = max_timeout
        self.max_output_chars = max_output_chars
        self.timeout_ms = int(default_timeout) * 1000
        self.max_output_bytes = output_bound_bytes(max_output_chars) + 1000

    async def run(self, ctx: ToolContext, command: str, timeout: int | None = None, **_: Any) -> ToolResult:
        cmd = (command or "").strip()
        if not cmd:
            return ToolResult(ok=False, output="", error="No command provided.")
        lowered = cmd.lower()
        for pat in BLOCKED_PATTERNS:
            if pat in lowered:
                return ToolResult(ok=False, output="", error=f"Command refused: contains blocked pattern {pat!r}.")
        limit = min(int(timeout or self.default_timeout), self.max_timeout)
        before = ctx.workspace.snapshot()
        started = time.monotonic()

        env = build_child_env(workspace_root=ctx.workspace.root)

        proc = await asyncio.create_subprocess_exec(
            "/bin/bash", "-lc", cmd,
            cwd=str(ctx.workspace.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        chunks: list[str] = []
        timed_out = False
        aborted = False

        async def pump() -> None:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace")
                chunks.append(text)
                await ctx.progress(text)

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

        output = "".join(chunks)
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
        text = truncate_text(output.strip() or "(no output)", self.max_output_chars)
        if error:
            text += f"\n{error}"
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
