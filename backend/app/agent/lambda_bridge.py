"""Shared execution context when LAMBDA runs inside a MatOpt conversation.

LAMBDA's original loop still plans EDA / reports, but Python cells and the
working directory come from MatOpt's per-conversation Jupyter kernel and
workspace — not a second OpenSandbox or in-process ``exec``.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

SANDBOX_WORK_DIR = "/tmp/workspace"

_CTX: ContextVar[Optional["LambdaExecContext"]] = ContextVar("lambda_exec_ctx", default=None)


@dataclass
class LambdaExecContext:
    conversation_id: str
    workspace_root: Path
    kernel: Any = None
    sandbox_session_id: str = ""


def get_lambda_exec_context() -> Optional[LambdaExecContext]:
    return _CTX.get()


def set_lambda_exec_context(ctx: Optional[LambdaExecContext]) -> Token:
    return _CTX.set(ctx)


def reset_lambda_exec_context(token: Token) -> None:
    _CTX.reset(token)


@contextmanager
def bind_lambda_exec_context(ctx: Optional[LambdaExecContext]) -> Iterator[Optional[LambdaExecContext]]:
    token = set_lambda_exec_context(ctx)
    try:
        yield ctx
    finally:
        reset_lambda_exec_context(token)


def resolve_local_workspace(session_id: str, fallback: Path) -> Path:
    """Prefer the bound MatOpt workspace when LAMBDA is running as a tool."""
    ctx = get_lambda_exec_context()
    if ctx is not None:
        ctx.workspace_root.mkdir(parents=True, exist_ok=True)
        return ctx.workspace_root
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


async def run_python_on_kernel(code: str, timeout: int | None = None) -> dict[str, Any]:
    """Execute ``code`` on the bound MatOpt kernel and map the result to LAMBDA's dict."""
    ctx = get_lambda_exec_context()
    if ctx is None or ctx.kernel is None:
        raise RuntimeError("MatOpt kernel is not bound for LAMBDA execution")
    workspace = ctx.workspace_root
    rewritten = (code or "").replace(SANDBOX_WORK_DIR, str(workspace))
    res = await ctx.kernel.execute(rewritten, timeout=float(timeout or 120))
    parts: list[str] = []
    if res.stdout.strip():
        parts.append(res.stdout.rstrip())
    for item in res.results:
        if item.strip():
            parts.append(item.rstrip())
    text = "\n".join(parts).strip()
    error = None if res.ok else (f"{res.error_name}: {res.error_value}" if res.error_name else res.status)
    images: list[str] = []
    generated: list[dict[str, Any]] = []
    for art in res.artifacts:
        if art.kind == "image":
            loc = art.url or art.path or ""
            if loc:
                images.append(loc)
        if art.path:
            generated.append({
                "filename": art.name,
                "sandbox_path": str(workspace / art.path),
                "oss_url": art.url or "",
                "url": art.url or "",
                "type": art.kind,
            })
    return {
        "success": res.ok,
        "stdout": text,
        "stderr": res.stderr,
        "error": error,
        "exit_code": 0 if res.ok else 1,
        "execution_time": (res.duration_ms or 0) / 1000.0,
        "images": images,
        "generated_files": generated,
        "kernel_restarted": res.status == "restarted",
    }
