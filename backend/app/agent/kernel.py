"""
Persistent Python kernels (one per conversation) built on ``jupyter_client``.

Why a Jupyter kernel instead of ``exec()``:
- variables persist between tool calls (load data once, analyse in later steps)
- rich outputs: matplotlib figures, plotly figures, pandas HTML tables arrive
  as ``display_data`` messages instead of being scraped from stdout
- a runaway cell can be interrupted (SIGINT) and, failing that, the whole
  process restarted, without touching the API server
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from app.agent.child_env import KERNEL_MPLBACKEND, build_child_env
from app.agent.events import Artifact
from app.agent.workspace import Workspace

logger = logging.getLogger(__name__)

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

PLOTLY_MIME = "application/vnd.plotly.v1+json"
MAX_INLINE_HTML = 200_000       # chars of text/html kept inline for the UI
MAX_INLINE_PLOTLY = 3_000_000   # chars of plotly JSON kept inline

STARTUP_CODE = r'''
%matplotlib inline
import os as _os, sys as _sys, warnings as _warnings
_os.chdir({cwd!r})
_warnings.filterwarnings("ignore", category=DeprecationWarning)
_warnings.filterwarnings("ignore", category=FutureWarning)
try:
    import matplotlib_inline.backend_inline as _mi
    _mi.set_matplotlib_formats("png")
    import matplotlib.pyplot as plt
    plt.rcParams["figure.dpi"] = 110
    plt.rcParams["figure.figsize"] = (8, 5)
    plt.rcParams["axes.grid"] = True
    plt.rcParams["grid.alpha"] = 0.3
    # plt.show() already emits the figure; close so the inline backend
    # does not flush the same open figure again at post_execute.
    if not getattr(plt, "_matopt_show_wrapped", False):
        _orig_show = plt.show
        def _matopt_show(*args, **kwargs):
            _orig_show(*args, **kwargs)
            plt.close("all")
        plt.show = _matopt_show
        plt._matopt_show_wrapped = True
    # A figure saved into the workspace already becomes an artifact as a file;
    # don't capture it inline as well (that showed one plot twice).
    import matplotlib.figure as _mfig
    if not getattr(_mfig.Figure, "_matopt_savefig_wrapped", False):
        _orig_savefig = _mfig.Figure.savefig
        def _matopt_savefig(self, fname, *args, **kwargs):
            import os
            out = _orig_savefig(self, fname, *args, **kwargs)
            if isinstance(fname, (str, os.PathLike)):
                target = os.path.abspath(os.fspath(fname))
                if target.startswith(os.getcwd() + os.sep):
                    self._matopt_saved = True
            return out
        _mfig.Figure.savefig = _matopt_savefig
        _mfig.Figure._matopt_savefig_wrapped = True
        _orig_display = _mi.display
        def _matopt_display(*objs, **kwargs):
            objs = [o for o in objs if not getattr(o, "_matopt_saved", False)]
            if objs:
                _orig_display(*objs, **kwargs)
        _mi.display = _matopt_display
except Exception as _e:
    print("matplotlib setup skipped:", _e)
try:
    import plotly.io as _pio
    _pio.renderers.default = "plotly_mimetype"
    _pio.templates.default = "plotly_white"
except Exception:
    pass
try:
    import pandas as _pd
    _pd.set_option("display.max_rows", 60)
    _pd.set_option("display.max_columns", 40)
    _pd.set_option("display.width", 160)
except Exception:
    pass
import numpy as np, math
try:
    import sympy as sp
except Exception:
    pass
try:
    from IPython import get_ipython as _gi
    _ip = _gi()
    def _matopt_close_figures(_evt=None):
        try:
            import matplotlib.pyplot as _plt
            _plt.close("all")
        except Exception:
            pass
    if _ip is not None and not getattr(_ip, "_matopt_close_registered", False):
        # Close leftovers before the next cell so %matplotlib inline cannot
        # recapture them, and again after so they do not linger.
        _ip.events.register("pre_run_cell", _matopt_close_figures)
        _ip.events.register("post_run_cell", _matopt_close_figures)
        _ip._matopt_close_registered = True
except Exception:
    pass
del _os, _sys, _warnings
'''


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text or "")


@dataclass
class ExecResult:
    status: str = "ok"                  # ok | error | timeout | aborted | restarted
    stdout: str = ""
    stderr: str = ""
    results: list[str] = field(default_factory=list)   # text/plain of execute_result / display_data
    artifacts: list[Artifact] = field(default_factory=list)
    error_name: Optional[str] = None
    error_value: Optional[str] = None
    traceback: str = ""
    duration_ms: int = 0
    execution_count: Optional[int] = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class KernelSession:
    """A single kernel bound to one conversation workspace."""

    def __init__(self, conversation_id: str, workspace: Workspace, startup_timeout: float = 90.0):
        self.conversation_id = conversation_id
        self.workspace = workspace
        self.startup_timeout = startup_timeout
        self._km = None
        self._kc = None
        self._lock = asyncio.Lock()
        self.last_used = time.time()
        self.started = False
        self.execution_count = 0
        self._seen_figure_digests: set[str] = set()
        self._ipc_dir: str | None = None

    # ── lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        from jupyter_client import AsyncKernelManager

        env = _child_env(self.workspace.root)
        km = AsyncKernelManager(kernel_name="python3")
        if os.name == "posix":
            # Unix sockets instead of unencrypted loopback TCP: other local users
            # and processes cannot connect to the kernel. A short private
            # directory keeps socket paths under the ~104-character limit.
            self._ipc_dir = tempfile.mkdtemp(prefix="mo-k")
            km.transport, km.ip = "ipc", os.path.join(self._ipc_dir, "k")
        await km.start_kernel(cwd=str(self.workspace.root), env=env)
        kc = km.client()
        kc.start_channels()
        try:
            await kc.wait_for_ready(timeout=self.startup_timeout)
        except Exception:
            kc.stop_channels()
            await km.shutdown_kernel(now=True)
            self._remove_ipc_dir()
            raise
        self._km, self._kc = km, kc
        self.started = True
        boot = await self._execute_raw(STARTUP_CODE.format(cwd=str(self.workspace.root)), timeout=60)
        if boot.status != "ok":
            logger.warning("Kernel startup cell failed for %s: %s %s", self.conversation_id, boot.error_name, boot.error_value)

    async def ensure_started(self) -> None:
        if not self.started or self._km is None or not await self._km.is_alive():
            await self.shutdown()
            await self.start()

    async def restart(self) -> None:
        await self.shutdown()
        await self.start()

    async def interrupt(self) -> None:
        if self._km is not None:
            try:
                await self._km.interrupt_kernel()
            except Exception:  # noqa: BLE001
                logger.debug("interrupt failed", exc_info=True)

    async def shutdown(self) -> None:
        self.started = False
        kc, km = self._kc, self._km
        self._kc, self._km = None, None
        if kc is not None:
            try:
                kc.stop_channels()
            except Exception:  # noqa: BLE001
                pass
        if km is not None:
            try:
                await km.shutdown_kernel(now=True)
            except Exception:  # noqa: BLE001
                logger.debug("shutdown failed", exc_info=True)
        self._remove_ipc_dir()

    def _remove_ipc_dir(self) -> None:
        if self._ipc_dir:
            shutil.rmtree(self._ipc_dir, ignore_errors=True)
            self._ipc_dir = None

    # ── execution ───────────────────────────────────────────────────────

    async def execute(
        self,
        code: str,
        timeout: float = 120.0,
        on_stream: Optional[Callable[[str, str], Awaitable[None]]] = None,
        cancel: Optional[asyncio.Event] = None,
    ) -> ExecResult:
        async with self._lock:
            await self.ensure_started()
            self.last_used = time.time()
            self.execution_count += 1
            result = await self._execute_raw(code, timeout=timeout, on_stream=on_stream, cancel=cancel)
            self.last_used = time.time()
            try:
                from app.agent.notebook import append_analysis_cell

                append_analysis_cell(self.workspace, code, result)
            except Exception:  # noqa: BLE001
                logger.debug("analysis.ipynb append failed", exc_info=True)
            return result

    async def _execute_raw(
        self,
        code: str,
        timeout: float,
        on_stream: Optional[Callable[[str, str], Awaitable[None]]] = None,
        cancel: Optional[asyncio.Event] = None,
    ) -> ExecResult:
        kc = self._kc
        assert kc is not None
        res = ExecResult()
        started = time.monotonic()
        deadline = started + timeout
        msg_id = kc.execute(code, store_history=True, allow_stdin=False, stop_on_error=True)
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        interrupted = False
        interrupt_sent_at: Optional[float] = None

        while True:
            now = time.monotonic()
            if cancel is not None and cancel.is_set() and not interrupted:
                await self.interrupt()
                interrupted = True
                interrupt_sent_at = now
                res.status = "aborted"
            if now > deadline and not interrupted:
                await self.interrupt()
                interrupted = True
                interrupt_sent_at = now
                res.status = "timeout"
            if interrupted and interrupt_sent_at is not None and now - interrupt_sent_at > 10:
                # kernel ignored SIGINT → hard restart
                logger.warning("Kernel %s did not respond to interrupt; restarting", self.conversation_id)
                await self.restart()
                res.status = "restarted"
                break
            try:
                msg = await asyncio.wait_for(kc.get_iopub_msg(timeout=1.0), timeout=1.5)
            except (asyncio.TimeoutError, Exception):  # queue.Empty surfaces as generic Exception
                continue
            if msg.get("parent_header", {}).get("msg_id") != msg_id:
                continue
            mtype = msg["header"]["msg_type"]
            content = msg.get("content", {})
            if mtype == "stream":
                text = content.get("text", "")
                if content.get("name") == "stderr":
                    stderr_parts.append(text)
                else:
                    stdout_parts.append(text)
                if on_stream is not None:
                    try:
                        await on_stream(content.get("name", "stdout"), text)
                    except Exception:  # noqa: BLE001
                        pass
            elif mtype in ("display_data", "execute_result"):
                self._collect_display(content.get("data", {}) or {}, res)
                if mtype == "execute_result":
                    res.execution_count = content.get("execution_count")
            elif mtype == "error":
                res.error_name = content.get("ename")
                res.error_value = content.get("evalue")
                res.traceback = strip_ansi("\n".join(content.get("traceback", [])))
                if res.status == "ok":
                    res.status = "error"
            elif mtype == "status" and content.get("execution_state") == "idle":
                break

        # Drain the execute_reply so the shell channel stays in sync.
        try:
            await asyncio.wait_for(kc.get_shell_msg(timeout=2.0), timeout=3.0)
        except Exception:  # noqa: BLE001
            pass

        res.stdout = strip_ansi("".join(stdout_parts))
        res.stderr = strip_ansi("".join(stderr_parts))
        res.duration_ms = int((time.monotonic() - started) * 1000)
        if res.status == "timeout":
            res.error_name = res.error_name or "TimeoutError"
            res.error_value = f"Execution exceeded {int(timeout)} seconds and was interrupted."
        elif res.status == "aborted":
            res.error_name = res.error_name or "Aborted"
            res.error_value = "Execution was stopped by the user."
        elif res.status == "restarted":
            res.error_name = "KernelRestarted"
            res.error_value = (
                f"Execution exceeded {int(timeout)} seconds, ignored the interrupt, and the kernel was restarted. "
                "All in-memory variables are gone; reload data before continuing."
            )
        return res

    # ── rich output handling ────────────────────────────────────────────

    def _skip_duplicate_figure(self, digest: str) -> bool:
        """Remember *digest*; return True if this exact figure was already saved."""
        if digest in self._seen_figure_digests:
            return True
        self._seen_figure_digests.add(digest)
        return False

    def _save_image_bytes(self, raw: bytes, ext: str, res: ExecResult) -> bool:
        digest = hashlib.sha256(raw).hexdigest()
        if self._skip_duplicate_figure(digest):
            return True
        path = self.workspace.next_figure_path(ext)
        path.write_bytes(raw)
        art = self.workspace.artifact_for(path)
        art.digest = digest
        res.artifacts.append(art)
        return True

    def _collect_display(self, data: dict, res: ExecResult) -> None:
        ws = self.workspace
        handled = False
        if "image/png" in data:
            handled = self._save_image_bytes(base64.b64decode(data["image/png"]), ".png", res)
        elif "image/svg+xml" in data:
            svg = data["image/svg+xml"]
            text = svg if isinstance(svg, str) else "".join(svg)
            handled = self._save_image_bytes(text.encode("utf-8"), ".svg", res)
        elif "image/jpeg" in data:
            handled = self._save_image_bytes(base64.b64decode(data["image/jpeg"]), ".jpg", res)
        if PLOTLY_MIME in data:
            fig = data[PLOTLY_MIME]
            payload = json.dumps(fig, default=str)
            digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            if not self._skip_duplicate_figure("plotly:" + digest):
                path = ws.next_figure_path(".json", prefix="plot")
                path.write_text(payload, encoding="utf-8")
                art = ws.artifact_for(path)
                art.kind = "plotly"
                art.digest = digest
                if len(payload) <= MAX_INLINE_PLOTLY:
                    art.data = fig
                res.artifacts.append(art)
            handled = True
        if "text/html" in data and not handled:
            html = data["text/html"]
            html = html if isinstance(html, str) else "".join(html)
            if len(html) <= MAX_INLINE_HTML:
                res.artifacts.append(Artifact(kind="html", name="table", data=html, mime="text/html"))
            handled = True
        if "text/markdown" in data and not handled:
            res.results.append(str(data["text/markdown"]))
            handled = True
        if "text/latex" in data and not handled:
            res.results.append(str(data["text/latex"]))
            handled = True
        if "text/plain" in data:
            text = data["text/plain"]
            text = text if isinstance(text, str) else "".join(text)
            # For figures the text/plain repr is just "<Figure ...>" – skip it.
            if not (handled and text.startswith("<")):
                res.results.append(text)


class KernelPool:
    """Lazily creates kernels per conversation and reaps idle ones."""

    def __init__(self, workspace_factory: Callable[[str], Workspace], idle_seconds: int = 1800):
        self._sessions: dict[str, KernelSession] = {}
        self._factory = workspace_factory
        self.idle_seconds = idle_seconds
        self._reaper: Optional[asyncio.Task] = None

    def get(self, conversation_id: str) -> KernelSession:
        session = self._sessions.get(conversation_id)
        if session is None:
            session = KernelSession(conversation_id, self._factory(conversation_id))
            self._sessions[conversation_id] = session
        return session

    def peek(self, conversation_id: str) -> Optional[KernelSession]:
        return self._sessions.get(conversation_id)

    async def restart(self, conversation_id: str) -> None:
        await self.get(conversation_id).restart()

    async def shutdown(self, conversation_id: str) -> None:
        session = self._sessions.pop(conversation_id, None)
        if session is not None:
            await session.shutdown()

    async def shutdown_all(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            self._reaper = None
        for cid in list(self._sessions):
            await self.shutdown(cid)

    def start_reaper(self) -> None:
        if self._reaper is None:
            self._reaper = asyncio.create_task(self._reap_loop())

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time.time()
            for cid, session in list(self._sessions.items()):
                if session.started and now - session.last_used > self.idle_seconds and not session._lock.locked():
                    logger.info("Shutting down idle kernel for conversation %s", cid)
                    await self.shutdown(cid)


def _child_env(workspace_root: Path | None = None) -> dict:
    """Environment for the kernel child process (see ``app.agent.child_env``).

    The kernel plots through matplotlib's *inline* backend, not the shell's
    ``Agg``, so it overrides the shared default; ``workspace_root`` becomes the
    kernel's ``HOME`` so ``~`` is the workspace rather than the user's home.
    """
    return build_child_env(workspace_root=workspace_root, extra={"MPLBACKEND": KERNEL_MPLBACKEND})
