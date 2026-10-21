"""Machine-checked proofs with Lean 4.

Registered only when a Lean toolchain is available: ``LEAN_PROJECT_DIR`` (a Lake
project, usually with Mathlib; see scripts/setup_lean.sh) or a ``lean`` binary
from ``LEAN_COMMAND``, PATH or ``~/.elan/bin``. A proof is accepted only when
Lean reports no errors and the source uses no ``sorry``, ``admit`` or new
``axiom`` — the shortcuts that would let an unproved statement compile.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from app.agent.child_env import build_child_env
from app.agent.events import ToolResult
from app.agent.tools.base import Tool, ToolContext
from app.config import settings

ELAN_HOME = Path(os.environ.get("ELAN_HOME") or Path.home() / ".elan")
SHORTCUTS = re.compile(r"\b(sorry|admit)\b|^\s*(?:private\s+|noncomputable\s+)*axiom\b", re.MULTILINE)
NAME = re.compile(r"[^A-Za-z0-9_-]+")


def _strip_comments(source: str) -> str:
    source = re.sub(r"/-.*?-/", "", source, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", "", source)


def find_lean() -> tuple[list[str], Optional[Path]] | None:
    """The command prefix that checks one file, and the directory to run it in."""
    project = settings.lean_project_dir
    if project:
        lake = shutil.which("lake") or str(ELAN_HOME / "bin" / "lake")
        if Path(project, "lakefile.lean").exists() or Path(project, "lakefile.toml").exists():
            return [lake, "env", "lean"], Path(project)
    lean = settings.lean_command or shutil.which("lean")
    if not lean and (ELAN_HOME / "bin" / "lean").exists():
        lean = str(ELAN_HOME / "bin" / "lean")
    return ([lean], None) if lean else None


class LeanCheckTool(Tool):
    name = "lean_check"
    read_only = False
    concurrent_safe = True
    side_effect_scope = "workspace"
    risk_level = "low"
    ui_kind = "math"
    description = (
        "Machine-check a Lean 4 proof. Pass a complete Lean source file (imports, definitions, theorem statements "
        "and proofs). It is saved under proofs/ and compiled; it passes only if Lean reports no errors and the source "
        "uses no sorry, admit or new axiom. Use it when a claim needs a formal proof, and state the Lean theorem you "
        "proved in the answer. Mathlib is importable only when the operator configured a Mathlib project."
    )
    parameters = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Complete Lean 4 file contents."},
            "name": {"type": "string", "description": "Short file name, e.g. 'sum_of_odds'."},
        },
        "required": ["source"],
    }

    def __init__(self, command: list[str], cwd: Optional[Path], timeout: int):
        self.command, self.cwd, self.timeout_s = command, cwd, timeout
        self.timeout_ms = timeout * 1000 + 5_000

    @classmethod
    def detect(cls) -> Optional["LeanCheckTool"]:
        found = find_lean()
        return cls(found[0], found[1], settings.lean_timeout) if found else None

    async def run(self, ctx: ToolContext, source: str, name: str = "", **_: Any) -> ToolResult:
        if not source.strip():
            return ToolResult(ok=False, output="", error="No Lean source provided.")
        stem = NAME.sub("_", name or "proof").strip("_")[:60] or "proof"
        path = ctx.workspace.resolve(f"proofs/{stem}.lean")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        shortcuts = sorted({m.group(1) or "axiom" for m in SHORTCUTS.finditer(_strip_comments(source))})

        env = build_child_env(workspace_root=ctx.workspace.root, extra={
            "ELAN_HOME": str(ELAN_HOME), "PATH": f"{ELAN_HOME / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"})
        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *self.command, str(path), cwd=str(self.cwd or ctx.workspace.root),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env, start_new_session=True)
        wait = asyncio.create_task(proc.communicate())
        cancelled = asyncio.create_task(ctx.cancel.wait())
        try:
            done, _ = await asyncio.wait({wait, cancelled}, timeout=self.timeout_s, return_when=asyncio.FIRST_COMPLETED)
            if wait not in done:
                proc.kill()
                await proc.wait()
                reason = "cancelled" if cancelled in done else f"timed out after {self.timeout_s}s"
                return ToolResult(ok=False, output="", error=f"Lean check {reason}.", meta={"path": f"proofs/{stem}.lean"})
            output = wait.result()[0].decode("utf-8", errors="replace").strip()
        finally:
            cancelled.cancel()
        rel = f"proofs/{stem}.lean"
        meta = {"path": rel, "exit_code": proc.returncode, "seconds": round(time.monotonic() - started, 2),
                "shortcuts": shortcuts}
        uses_sorry = "declaration uses 'sorry'" in output
        if proc.returncode == 0 and not shortcuts and not uses_sorry:
            meta["verified"] = True
            note = f"\n\nLean messages:\n{output}" if output else ""
            return ToolResult(ok=True, output=f"VERIFIED: Lean accepted {rel} with no sorry, admit or axiom.{note}",
                              meta=meta)
        meta["verified"] = False
        problems = []
        if shortcuts:
            problems.append("the source uses " + ", ".join(shortcuts) + ", so it is not a complete proof")
        if uses_sorry:
            problems.append("Lean reports a declaration that uses sorry")
        if proc.returncode != 0:
            problems.append(f"Lean exited with code {proc.returncode}")
        return ToolResult(ok=False, output=output[:8_000], error="NOT VERIFIED: " + "; ".join(problems) + ".", meta=meta)
