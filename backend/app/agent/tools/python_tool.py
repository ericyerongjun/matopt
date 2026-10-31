"""``python`` tool: run code in the conversation's persistent Jupyter kernel."""

from __future__ import annotations

import re
from typing import Any

from app.agent.events import ToolResult
from app.agent.tools.base import Tool, ToolContext, output_bound_bytes, truncate_text
from app.agent.tools.read_state import invalidate_execution_changes
from app.agent.workspace import is_internal_output

_FENCE = re.compile(r"^\s*```[a-zA-Z0-9_+-]*\s*\n(.*?)\n?```\s*$", re.DOTALL)

# Model-friendly hints keyed on the exception class (AlphaApollo-style "result checking").
ERROR_HINTS = {
    "NameError": "A name is undefined: define it in this cell, check for typos, or re-import. Variables from earlier cells persist unless the kernel was restarted.",
    "ModuleNotFoundError": "The package is not installed: install it with the bash tool (`pip install <package>`) and re-run.",
    "ImportError": "Check that the imported name exists in this library version (use `dir(module)` or `help`).",
    "AttributeError": "Inspect the object's API in this library version (`dir(obj)`, `help(obj.method)`) before retrying.",
    "TypeError": "Check argument types and shapes; print the intermediate values involved.",
    "ValueError": "Check input values, array shapes and domains; print the intermediate values involved.",
    "KeyError": "Print the available keys/columns (e.g. `df.columns.tolist()`) and use an exact match.",
    "IndexError": "Check lengths and 0-based indexing; print `len(...)` / `.shape` first.",
    "SyntaxError": "Fix the syntax; send plain Python (no Markdown fences, no `!pip` — use the bash tool for shell commands).",
    "KeyboardInterrupt": "The cell was interrupted. Reduce the problem size, vectorise, sample the data, or raise `timeout`.",
    "TimeoutError": "The cell hit the timeout. Reduce the problem size, vectorise, sample the data, or raise `timeout`.",
    "MemoryError": "Reduce memory use: process in chunks, use smaller dtypes, or subsample.",
    "ZeroDivisionError": "Guard against zero denominators or use limits/symbolic handling.",
    "RecursionError": "Rewrite recursively-defined logic iteratively or memoise; raise the limit only if the depth is bounded.",
}


def clean_code(code: str) -> str:
    """Strip a surrounding Markdown fence if the model wrapped the cell in one."""
    m = _FENCE.match(code or "")
    return m.group(1) if m else (code or "")


class PythonTool(Tool):
    name = "python"
    # Executes arbitrary code in a Jupyter kernel that is *not* sandboxed: the
    # cell can import os/subprocess, read `~/.ssh`, install packages and reach
    # the network — its true blast radius is the host, not the workspace. The
    # conversation workspace is only its cwd, so the honest scope is `system`.
    # Not destructive by class (a `print(1)` destroys nothing; ZCode marks Bash
    # the same way) and not concurrent-safe: every call shares one kernel whose
    # variables and imports persist between cells.
    destructive = False
    concurrent_safe = False
    side_effect_scope = "system"
    risk_level = "high"
    needs_approval = True
    ui_kind = "code"
    description = (
        "Execute Python code in a persistent Jupyter kernel whose working directory is the conversation workspace. "
        "Variables, imports and data frames persist between calls. Pre-installed: numpy, scipy, sympy, mpmath, pandas, "
        "matplotlib, plotly, seaborn, statsmodels, scikit-learn, networkx, math-verify. Use it for any computation, "
        "simulation, data analysis, symbolic algebra, numerical verification, or visualisation. "
        "Figures: matplotlib figures are captured automatically when the cell ends (or on plt.show()); "
        "identical figures are captured once. Plot each distinct claim once — do not re-run a plotting cell "
        "only to tweak titles. For interactive charts build a plotly figure and call fig.show(). "
        "The last expression of the cell is displayed like in a notebook. Print intermediate values you need to see. "
        "Files you write under the workspace become downloadable artifacts. Long-running cells are interrupted after `timeout` seconds. "
        "If code mode is enabled, a `tools` object calls read-only MatOpt tools from the cell, e.g. "
        "`tools.read_file(path=\"data.csv\")` or `tools.web_search(query=\"...\")` (keyword arguments; returns the text "
        "output, raises `ToolError` on failure); `tools.list()` and `tools.describe(name)` show what is callable."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python source to execute in the kernel."},
            "timeout": {
                "type": "integer",
                "description": "Seconds before the cell is interrupted (default 120, max 900).",
            },
            "reset": {
                "type": "boolean",
                "description": "Restart the kernel first, discarding all variables (default false).",
            },
        },
        "required": ["code"],
    }

    def __init__(self, default_timeout: int = 120, max_timeout: int = 900, max_output_chars: int = 12000):
        self.default_timeout = default_timeout
        self.max_timeout = max_timeout
        self.max_output_chars = max_output_chars
        self.timeout_ms = int(default_timeout) * 1000        # enforced per cell (`timeout`, capped below)
        self.max_output_bytes = output_bound_bytes(max_output_chars)

    def resolve_input(self, arguments: dict) -> dict:
        """Strip a Markdown fence *before* anything else reads the code (§3.8c).

        `clean_code` used to run inside `run`, so the monitor, the
        `tool_call_ready` event and the persisted `tool_call` part all saw the
        fenced text while the kernel executed the unfenced text. Normalizing
        here means approval and execution are byte-identical.
        """
        code = arguments.get("code")
        if isinstance(code, str):
            cleaned = clean_code(code)
            if cleaned != code:
                return {**arguments, "code": cleaned}
        return arguments

    @staticmethod
    def _tool_binding(ctx: ToolContext, limit: float) -> Any:
        """The code-mode binding for this cell, or None when code mode is off.

        The harness supplies ``ctx.state["tool_dispatch"]`` (and optionally
        ``ctx.state["tool_registry"]`` for the allow-list and ``tools.list()``);
        without a dispatcher the binding is still made so ``tools.*`` reports
        that code mode is unavailable instead of "no cell is running".
        """
        settings = ctx.settings
        if settings is None:
            from app.config import settings
        if not getattr(settings, "code_mode_enabled", False):
            return None
        import time

        from app.agent.kernel_tools import KernelToolBinding

        dispatch = ctx.state.get("tool_dispatch")
        return KernelToolBinding(
            ctx=ctx, call_id=ctx.call_id, dispatch=dispatch if callable(dispatch) else None,
            registry=ctx.state.get("tool_registry"),
            max_calls=int(getattr(settings, "code_mode_max_calls_per_cell", 50)),
            deadline=time.monotonic() + limit)

    async def run(self, ctx: ToolContext, code: str, timeout: int | None = None, reset: bool = False, **_: Any) -> ToolResult:
        code = clean_code(code)   # idempotent: still true when `run` is called directly
        if not code or not code.strip():
            return ToolResult(ok=False, output="", error="No code provided.")
        if ctx.kernels is None:
            return ToolResult(ok=False, output="", error="Python kernel pool is not available.")
        session = ctx.kernels.get(ctx.conversation_id)
        if reset:
            await session.restart()
        limit = min(int(timeout or self.default_timeout), self.max_timeout)

        before = ctx.workspace.snapshot()

        async def on_stream(name: str, text: str) -> None:
            await ctx.progress(text)

        # Only pass `tools` when code mode is on, so kernel stand-ins without
        # the parameter keep working.
        binding = self._tool_binding(ctx, limit)
        extra = {"tools": binding} if binding is not None else {}
        try:
            res = await session.execute(code, timeout=limit, on_stream=on_stream, cancel=ctx.cancel, **extra)
        finally:
            changed = ctx.workspace.changed_since(before)
            read_state_meta = invalidate_execution_changes(ctx.state, ctx.workspace, before, changed)

        # Files created/modified by the cell (figures already captured are deduplicated by path).
        artifacts = list(res.artifacts)
        seen = {a.path for a in artifacts if a.path}
        for rel in changed:
            if rel in seen or is_internal_output(rel):
                continue
            try:
                artifacts.append(ctx.workspace.artifact_for(rel))
                seen.add(rel)
            except Exception:  # noqa: BLE001
                continue

        parts: list[str] = []
        if res.stdout.strip():
            parts.append(res.stdout.rstrip())
        for r in res.results:
            if r.strip():
                parts.append(r.rstrip())
        if res.stderr.strip():
            parts.append("[stderr]\n" + res.stderr.rstrip())
        if res.traceback:
            parts.append(res.traceback)
        elif res.error_name:
            parts.append(f"{res.error_name}: {res.error_value}")
        if res.error_name and res.error_name in ERROR_HINTS:
            parts.append(f"Hint: {ERROR_HINTS[res.error_name]}")
        for a in artifacts:
            if a.kind == "image":
                parts.append(f"[figure saved: {a.path}]")
            elif a.kind == "plotly":
                parts.append(f"[interactive plotly figure captured: {a.path}]")
            elif a.kind == "html":
                continue
            elif a.path:
                parts.append(f"[file written: {a.path}]")
        text = "\n".join(parts).strip()
        if not text:
            text = "(no output)"
        text = truncate_text(text, self.max_output_chars)

        ok = res.status == "ok"
        error = None if ok else (f"{res.error_name}: {res.error_value}" if res.error_name else res.status)
        return ToolResult(
            ok=ok,
            output=text,
            artifacts=artifacts,
            error=error,
            meta={
                "status": res.status,
                "duration_ms": res.duration_ms,
                "execution_count": res.execution_count,
                "stdout": truncate_text(res.stdout, self.max_output_chars),
                "stderr": truncate_text(res.stderr, 4000),
                **read_state_meta,
            },
        )
