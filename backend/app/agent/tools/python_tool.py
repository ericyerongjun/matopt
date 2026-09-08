"""``python`` tool: run code in the conversation's persistent Jupyter kernel."""

from __future__ import annotations

import re
from typing import Any

from app.agent.events import ToolResult
from app.agent.tools.base import Tool, ToolContext, truncate_text

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
    ui_kind = "code"
    description = (
        "Execute Python code in a persistent Jupyter kernel whose working directory is the conversation workspace. "
        "Variables, imports and data frames persist between calls. Pre-installed: numpy, scipy, sympy, mpmath, pandas, "
        "matplotlib, plotly, seaborn, statsmodels, scikit-learn, networkx, math-verify. Use it for any computation, "
        "simulation, data analysis, symbolic algebra, numerical verification, or visualisation. "
        "Figures: matplotlib figures are captured automatically when the cell ends (or on plt.show()); "
        "for interactive charts build a plotly figure and call fig.show(). Both are returned as artifacts. "
        "The last expression of the cell is displayed like in a notebook. Print intermediate values you need to see. "
        "Files you write under the workspace become downloadable artifacts. Long-running cells are interrupted after `timeout` seconds."
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

    async def run(self, ctx: ToolContext, code: str, timeout: int | None = None, reset: bool = False, **_: Any) -> ToolResult:
        code = clean_code(code)
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

        res = await session.execute(code, timeout=limit, on_stream=on_stream, cancel=ctx.cancel)

        # Files created/modified by the cell (figures already captured are deduplicated by path).
        artifacts = list(res.artifacts)
        seen = {a.path for a in artifacts if a.path}
        for rel in ctx.workspace.changed_since(before):
            if rel in seen:
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
            },
        )
