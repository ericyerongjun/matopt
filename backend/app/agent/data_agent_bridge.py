"""Shared execution context when the data agent runs inside a MatOpt conversation.

The data agent's original loop still plans EDA / reports, but Python cells and the
working directory come from MatOpt's per-conversation Jupyter kernel and
workspace — not a second OpenSandbox or in-process ``exec``.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator, Optional, TypeVar

from app.agent.budget import BudgetExhausted, RunBudget
from app.agent.session.telemetry import normalize_usage

T = TypeVar("T")

SANDBOX_WORK_DIR = "/tmp/workspace"

_CTX: ContextVar[Optional["DataAgentExecContext"]] = ContextVar("data_agent_exec_ctx", default=None)


async def reap_tasks(*tasks, cancel: bool = True) -> None:
    """Keep cleanup owned even if multiple parent layers request cancellation."""
    pending = [task for task in tasks if task is not None]
    if cancel:
        for task in pending:
            if not task.done() and not (isinstance(task, asyncio.Task) and task.cancelling()):
                task.cancel()
    settled = asyncio.gather(*pending, return_exceptions=True)
    interrupted = False
    while not settled.done():
        try:
            await asyncio.shield(settled)
        except asyncio.CancelledError:
            interrupted = True
    if interrupted:
        raise asyncio.CancelledError()


@dataclass
class DataAgentExecContext:
    conversation_id: str
    workspace_root: Path
    kernel: Any = None
    sandbox_session_id: str = ""
    run_budget: RunBudget | None = None
    cancel: asyncio.Event | None = None
    control_boundary: Callable[[], Awaitable[None]] | None = None
    record_model_call: Callable[[dict], Awaitable[None]] | None = None
    tool_context: Any = None

    def remaining_timeout(self, timeout: float | None = None) -> float | None:
        if self.cancel is not None and self.cancel.is_set():
            raise asyncio.CancelledError("Delegated analysis was stopped.")
        return self.run_budget.remaining_timeout(timeout) if self.run_budget is not None else timeout

    async def checkpoint(self) -> None:
        self.remaining_timeout()
        if self.control_boundary is not None:
            await self.wait(self.control_boundary)
        self.remaining_timeout()

    async def wait(self, operation: Callable[[], Awaitable[T]], *, timeout: float | None = None) -> T:
        """Own one await, including cancellation while no stream frame arrives.

        The factory is evaluated only after admission/deadline checks, so a
        rejected boundary cannot leak an unawaited coroutine or start work.
        Child task cleanup finishes before the delegation returns its lease.
        """
        limit = self.remaining_timeout(timeout)
        task = asyncio.ensure_future(operation())
        stopped = asyncio.create_task(self.cancel.wait()) if self.cancel is not None else None
        try:
            done, _ = await asyncio.wait(
                {task, *([stopped] if stopped is not None else [])},
                timeout=limit, return_when=asyncio.FIRST_COMPLETED,
            )
            if stopped is not None and stopped in done:
                raise asyncio.CancelledError("Delegated analysis was stopped.")
            if task in done:
                return task.result()
            # Distinguish the shared absolute deadline from a local timeout.
            self.remaining_timeout()
            raise asyncio.TimeoutError()
        finally:
            await reap_tasks(task, stopped)


async def controlled_items(source, ctx: DataAgentExecContext | None = None):
    """Close nested generators before leaving the bound parent context."""
    iterator = aiter(source)
    try:
        while True:
            try:
                item = await ctx.wait(lambda: anext(iterator)) if ctx is not None else await anext(iterator)
            except StopAsyncIteration:
                break
            yield item
    finally:
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()


def reported_model_usage(usage: Any) -> dict | None:
    """Preserve absent provider counts as unknown; never use data-agent estimates."""
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump(exclude_none=True)
    elif not isinstance(usage, dict):
        usage = {key: getattr(usage, key) for key in (
            "prompt_tokens", "completion_tokens", "total_tokens", "completion_tokens_details",
        ) if getattr(usage, key, None) is not None}
    usage = dict(usage)
    if usage.get("estimated") is True:
        return None
    details = usage.get("completion_tokens_details")
    reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else getattr(details, "reasoning_tokens", None)
    if reasoning is not None:
        usage["reasoning_tokens"] = reasoning
    return normalize_usage(usage)


@dataclass
class DataAgentModelAttempt:
    context: DataAgentExecContext | None
    usage: dict | None = None
    outcome: str = "completed"
    provider_finished: bool = False

    async def wait(self, operation: Callable[[], Awaitable[T]]) -> T:
        return await self.context.wait(operation) if self.context is not None else await operation()

    def observe_usage(self, usage: Any) -> None:
        if usage is not None:
            self.usage = reported_model_usage(usage)


@asynccontextmanager
async def model_attempt(*, provider: str, model: str):
    """Account at the actual child provider boundary, once for each request."""
    ctx = get_data_agent_exec_context()
    attempt = DataAgentModelAttempt(ctx)
    if ctx is not None:
        await ctx.checkpoint()
    metadata = {"task": "data", "task_id": f"data_agent_{uuid.uuid4().hex}",
                "provider": provider, "model": model, "started_at": time.time()}
    if ctx is not None:
        if ctx.run_budget is not None:
            admission = ctx.run_budget.admit_model(metadata["task_id"], source="data", provider=provider, model=model)
            if not admission.is_new:
                raise RuntimeError("Delegated model attempt was already admitted.")
    try:
        if ctx is not None and ctx.record_model_call is not None:
            await ctx.record_model_call({**metadata, "status": "started"})
        yield attempt
    except (BudgetExhausted, asyncio.CancelledError, GeneratorExit):
        if not attempt.provider_finished:
            attempt.outcome = "aborted"
        raise
    except BaseException:
        attempt.outcome = "error"
        raise
    finally:
        if ctx is not None:
            if ctx.run_budget is not None:
                ctx.run_budget.settle_model(metadata["task_id"], usage=attempt.usage, outcome=attempt.outcome)
            if ctx.record_model_call is not None:
                await ctx.record_model_call({**metadata, "status": attempt.outcome,
                                             "completed_at": time.time(), "usage": attempt.usage})


def get_data_agent_exec_context() -> Optional[DataAgentExecContext]:
    return _CTX.get()


def set_data_agent_exec_context(ctx: Optional[DataAgentExecContext]) -> Token:
    return _CTX.set(ctx)


def reset_data_agent_exec_context(token: Token) -> None:
    _CTX.reset(token)


@contextmanager
def bind_data_agent_exec_context(ctx: Optional[DataAgentExecContext]) -> Iterator[Optional[DataAgentExecContext]]:
    token = set_data_agent_exec_context(ctx)
    try:
        yield ctx
    finally:
        reset_data_agent_exec_context(token)


def resolve_local_workspace(session_id: str, fallback: Path) -> Path:
    """Prefer the bound MatOpt workspace when the data agent is running as a tool."""
    ctx = get_data_agent_exec_context()
    if ctx is not None:
        ctx.workspace_root.mkdir(parents=True, exist_ok=True)
        return ctx.workspace_root
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


async def run_python_on_kernel(code: str, timeout: int | None = None) -> dict[str, Any]:
    """Execute ``code`` on the bound MatOpt kernel and map the result to the data agent's dict."""
    ctx = get_data_agent_exec_context()
    if ctx is None or ctx.kernel is None:
        raise RuntimeError("MatOpt kernel is not bound for data-agent execution")
    workspace = ctx.workspace_root
    rewritten = (code or "").replace(SANDBOX_WORK_DIR, str(workspace))
    limit = ctx.remaining_timeout(float(timeout or 120))
    task = asyncio.create_task(ctx.kernel.execute(rewritten, timeout=limit, cancel=ctx.cancel))
    try:
        res = await ctx.wait(lambda: asyncio.shield(task))
    finally:
        if not task.done():
            # Cancelling an await does not interrupt a Jupyter execution. Keep
            # the kernel drain owned until it is idle, or restart it if needed.
            async def stop_kernel():
                try:
                    await ctx.kernel.interrupt()
                    try:
                        await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
                    except asyncio.TimeoutError:
                        await ctx.kernel.restart()
                finally:
                    await reap_tasks(task)

            await reap_tasks(asyncio.create_task(stop_kernel()), cancel=False)
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
