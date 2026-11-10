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
import dataclasses
import hashlib
import json
import logging
import os
import queue
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from app.agent.child_env import KERNEL_MPLBACKEND, build_child_env
from app.agent.events import Artifact
from app.agent.exec_output import HeadTailBuffer
from app.agent.workspace import Workspace

logger = logging.getLogger(__name__)

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

PLOTLY_MIME = "application/vnd.plotly.v1+json"
MAX_INLINE_HTML = 200_000       # chars of text/html kept inline for the UI
MAX_INLINE_PLOTLY = 3_000_000   # chars of plotly JSON kept inline
# How long an abandoned cell (its tool call was cancelled mid-run) gets to go
# idle before the kernel is treated as hung and replaced.
ABANDONED_CELL_GRACE_SECONDS = 7.0

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
    # don't capture it inline as well (that showed one plot twice). The saved
    # state is a fingerprint of the rendered pixels, so a figure changed after
    # saving (another series, a new title) is still captured.
    import matplotlib.figure as _mfig
    def _matopt_figure_state(fig):
        try:
            import hashlib
            fig.canvas.draw()
            return hashlib.sha256(bytes(fig.canvas.buffer_rgba())).hexdigest()
        except Exception:
            return None
    if not getattr(_mfig.Figure, "_matopt_savefig_wrapped", False):
        _orig_savefig = _mfig.Figure.savefig
        def _matopt_savefig(self, fname, *args, **kwargs):
            import os
            out = _orig_savefig(self, fname, *args, **kwargs)
            if isinstance(fname, (str, os.PathLike)):
                target = os.path.abspath(os.fspath(fname))
                if target.startswith(os.getcwd() + os.sep):
                    self._matopt_saved_state = _matopt_figure_state(self)
            return out
        _mfig.Figure.savefig = _matopt_savefig
        _mfig.Figure._matopt_savefig_wrapped = True
        _orig_display = _mi.display
        def _matopt_display(*objs, **kwargs):
            objs = [o for o in objs if getattr(o, "_matopt_saved_state", None) is None
                    or _matopt_figure_state(o) != o._matopt_saved_state]
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
    status: str = "ok"                  # ok | error | timeout | aborted | restarted | died
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
        # Code mode (app/agent/kernel_tools.py): the host end of this kernel's
        # tools socket, present only when settings.code_mode_enabled.
        self._tool_server: Any = None
        # Serialises start/shutdown/restart, which callers outside ``_lock``
        # (and the cell holding it) can all request.
        self._lifecycle = asyncio.Lock()
        self._abandoned_cell: tuple[str, bool] | None = None   # (msg_id, already interrupted)
        self._settle_task: Optional[asyncio.Task] = None
        self._restart_notice = ""

    # ── lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        async with self._lifecycle:
            await self._start()

    async def _start(self) -> None:
        from jupyter_client import AsyncKernelManager
        from app.agent.sandbox import build_policy, wrap_argv
        from app.config import settings

        policy = build_policy(settings, self.workspace.root)
        # Inside the conversation's own temp directory when sandboxed: the shared
        # temp root is writable by every kernel, and these sockets control this one.
        self._ipc_dir = tempfile.mkdtemp(prefix="k", dir=policy.temp_dir) if policy.temp_dir else tempfile.mkdtemp(prefix="mo-k")
        tool_server = None
        if getattr(settings, "code_mode_enabled", False) and os.name == "posix":
            from app.agent.kernel_tools import SOCKET_NAME, KernelToolServer

            tool_server = KernelToolServer(os.path.join(self._ipc_dir, SOCKET_NAME))
            try:
                await tool_server.start()
            except BaseException:
                self._remove_ipc_dir()
                raise
            # Seatbelt matches the real path (/var -> /private/var on macOS).
            policy = dataclasses.replace(policy, unix_sockets=(
                *policy.unix_sockets, Path(os.path.realpath(tool_server.socket_path))))

        class SandboxedKernelManager(AsyncKernelManager):
            """Prepends the sandbox wrapper to the kernel's launch command."""

            def format_kernel_cmd(self, extra_arguments=None):
                return wrap_argv(super().format_kernel_cmd(extra_arguments), policy)

        km = (SandboxedKernelManager if policy.active else AsyncKernelManager)(kernel_name="python3")
        if os.name == "posix":
            # Unix sockets instead of unencrypted loopback TCP: other local users
            # and processes cannot connect to the kernel. A short private
            # directory keeps socket paths under the ~104-character limit.
            km.transport, km.ip = "ipc", os.path.join(self._ipc_dir, "k")
        # IPython runs every file in <IPYTHONDIR>/profile_default/startup/ at
        # boot. Under the default (~/.ipython, and HOME is the workspace) the
        # agent could plant one that runs in every later kernel. A private,
        # per-start directory keeps profiles out of the agent's reach.
        env = _child_env(self.workspace.root)
        env["IPYTHONDIR"] = os.path.join(self._ipc_dir, "ipython")
        env.update(policy.child_env())
        if tool_server is not None:
            env.update(tool_server.child_env())
        # BaseException: Stop during startup arrives as CancelledError, and the
        # process exists by then with nothing else holding a reference to it.
        kc = None
        try:
            # The IPC directory is deleted with the kernel, so on-disk history never outlived
            # it; and SQLite cannot open a file below the sandbox's masked temp parent.
            await km.start_kernel(cwd=str(self.workspace.root), env=env,
                                  extra_arguments=["--HistoryManager.hist_file=:memory:"])
            kc = km.client()
            kc.start_channels()
            await kc.wait_for_ready(timeout=self.startup_timeout)
        except BaseException:
            await _uninterrupted(self._teardown(kc, km, tool_server, self._ipc_dir))
            self._ipc_dir = None
            raise
        self._km, self._kc = km, kc
        self._tool_server = tool_server
        self.started = True
        startup = STARTUP_CODE.format(cwd=str(self.workspace.root))
        if tool_server is not None:
            from app.agent.kernel_tools import KERNEL_TOOLS_CODE

            startup += KERNEL_TOOLS_CODE
        # This runs inside ``_lifecycle``, so a hung boot cell must restart without re-taking it.
        boot = await self._execute_raw(startup, timeout=60, restart=self._restart_unlocked)
        if boot.status != "ok":
            logger.warning("Kernel startup cell failed for %s: %s %s", self.conversation_id, boot.error_name, boot.error_value)

    async def _is_running(self) -> bool:
        return self.started and self._km is not None and await self._km.is_alive()

    async def ensure_started(self) -> None:
        if await self._is_running():
            return
        async with self._lifecycle:
            if not await self._is_running():
                await self._restart_unlocked()

    async def restart(self) -> None:
        async with self._lifecycle:
            await self._restart_unlocked()

    async def _restart_unlocked(self) -> None:
        await self._shutdown()
        await self._start()

    async def interrupt(self) -> None:
        if self._km is not None:
            try:
                await self._km.interrupt_kernel()
            except Exception:  # noqa: BLE001
                logger.debug("interrupt failed", exc_info=True)

    async def shutdown(self) -> None:
        settle, self._settle_task = self._settle_task, None
        if settle is not None and settle is not asyncio.current_task():
            settle.cancel()
        async with self._lifecycle:
            await self._shutdown()

    async def _shutdown(self) -> None:
        self.started = False
        self._abandoned_cell = None
        kc, km = self._kc, self._km
        self._kc, self._km = None, None
        tool_server, self._tool_server = self._tool_server, None
        ipc_dir, self._ipc_dir = self._ipc_dir, None
        await _uninterrupted(self._teardown(kc, km, tool_server, ipc_dir))

    @staticmethod
    async def _teardown(kc: Any, km: Any, tool_server: Any, ipc_dir: str | None) -> None:
        """Release one kernel's resources; a failing step cannot skip the rest."""
        if tool_server is not None:
            try:
                await tool_server.close()
            except Exception:  # noqa: BLE001
                logger.warning("Could not close the kernel tool server", exc_info=True)
        if kc is not None:
            try:
                kc.stop_channels()
            except Exception:  # noqa: BLE001
                logger.debug("stop_channels failed", exc_info=True)
        if km is not None and (kc is not None or getattr(km, "has_kernel", False)):
            try:
                await km.shutdown_kernel(now=True)
            except Exception:  # noqa: BLE001
                logger.warning("Kernel shutdown failed", exc_info=True)
        if ipc_dir:
            shutil.rmtree(ipc_dir, ignore_errors=True)

    # ── a cell whose caller stopped waiting ─────────────────────────────

    def _abandon_cell(self, msg_id: str, kc: Any, interrupted: bool) -> None:
        """Remember a running cell that lost its caller, and go resolve it."""
        if kc is not self._kc:
            return
        self._abandoned_cell = (msg_id, interrupted)
        if self._settle_task is None or self._settle_task.done():
            self._settle_task = asyncio.create_task(self._settle_when_free(), name=f"kernel-settle:{self.conversation_id}")

    async def _settle_when_free(self) -> None:
        try:
            async with self._lock:
                await self._settle_abandoned_cell()
        except Exception:  # noqa: BLE001
            logger.warning("Could not settle an abandoned cell for %s", self.conversation_id, exc_info=True)

    async def _settle_abandoned_cell(self) -> None:
        """Keep the kernel if the cell stops soon; replace it if the cell is hung.

        Waiting for the caller is not an option: the tool boundary cancels a
        stuck cell after a few seconds, so ``_execute_raw`` never reaches its
        own restart and every later cell would queue behind the hung one.
        Call with ``_lock`` held.
        """
        abandoned, self._abandoned_cell = self._abandoned_cell, None
        kc = self._kc
        if abandoned is None or kc is None:
            return
        msg_id, interrupted = abandoned
        try:
            if not interrupted:
                await self.interrupt()
            deadline = time.monotonic() + ABANDONED_CELL_GRACE_SECONDS
            while time.monotonic() < deadline and kc is self._kc:
                msg = await self._next_iopub(kc)
                if msg is None:
                    if await self._died(kc):
                        return  # ensure_started replaces it for the next cell
                    continue
                if (msg.get("parent_header", {}).get("msg_id") == msg_id and msg["header"]["msg_type"] == "status"
                        and msg.get("content", {}).get("execution_state") == "idle"):
                    try:
                        await asyncio.wait_for(kc.get_shell_msg(timeout=0.2), timeout=0.5)
                    except Exception:  # noqa: BLE001
                        pass
                    return
        except asyncio.CancelledError:
            if kc is self._kc:
                self._abandoned_cell = abandoned  # still unresolved; the next cell settles it
            raise
        if kc is self._kc:
            logger.warning("Kernel %s did not stop an abandoned cell; restarting", self.conversation_id)
            await self.restart()
            self._restart_notice = ("[kernel restarted] An earlier cell ignored the interrupt, so the kernel was "
                                    "restarted. All in-memory variables are gone; reload data before continuing.\n")

    async def _next_iopub(self, kc: Any) -> Optional[dict]:
        """One iopub message, or None after a quiet second."""
        try:
            return await asyncio.wait_for(kc.get_iopub_msg(timeout=1.0), timeout=1.5)
        except (asyncio.TimeoutError, queue.Empty):
            return None
        except Exception:  # noqa: BLE001
            # A closed channel raises before ever suspending, and wait_for does
            # not yield by itself: retrying at once would freeze the event loop.
            await asyncio.sleep(0.05)
            return None

    async def _died(self, kc: Any) -> bool:
        km = self._km
        if km is None or kc is not self._kc:
            return False
        try:
            return not await km.is_alive()
        except Exception:  # noqa: BLE001
            return False

    def _remove_ipc_dir(self) -> None:
        if self._ipc_dir:
            shutil.rmtree(self._ipc_dir, ignore_errors=True)
            self._ipc_dir = None

    # ── execution ───────────────────────────────────────────────────────

    @property
    def code_mode(self) -> bool:
        """Whether this kernel was started with a ``tools`` object."""
        return self._tool_server is not None

    async def execute(
        self,
        code: str,
        timeout: float = 120.0,
        on_stream: Optional[Callable[[str, str], Awaitable[None]]] = None,
        cancel: Optional[asyncio.Event] = None,
        tools: Any = None,
    ) -> ExecResult:
        """Run one cell. ``tools`` is the cell's ``KernelToolBinding``, if any.

        The binding is activated inside the lock, for exactly the life of the
        cell (Codex's per-cell dispatch gate, ``code_mode/delegate.rs``), and
        closed afterwards whatever happened, which cancels a nested tool call
        still in flight when the cell was interrupted or timed out.
        """
        async with self._lock:
            await self._settle_abandoned_cell()
            await self.ensure_started()
            self.last_used = time.time()
            self.execution_count += 1
            server = self._tool_server
            if tools is not None:
                tools.deadline = time.monotonic() + timeout
                if server is not None:
                    server.activate(tools)
            try:
                result = await self._execute_raw(code, timeout=timeout, on_stream=on_stream, cancel=cancel)
            finally:
                if tools is not None:
                    tools.close()
                    if server is not None:
                        server.deactivate(tools)
            self.last_used = time.time()
            if self._restart_notice:
                result.stderr = self._restart_notice + result.stderr
                self._restart_notice = ""
            try:
                from app.agent.notebook import append_analysis_cell

                # The file holds every earlier cell and its images; keep that off the loop.
                await asyncio.shield(asyncio.to_thread(append_analysis_cell, self.workspace, code, result))
            except Exception:  # noqa: BLE001
                logger.debug("analysis.ipynb append failed", exc_info=True)
            return result

    async def _execute_raw(
        self,
        code: str,
        timeout: float,
        on_stream: Optional[Callable[[str, str], Awaitable[None]]] = None,
        cancel: Optional[asyncio.Event] = None,
        restart: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> ExecResult:
        kc = self._kc
        assert kc is not None
        res = ExecResult()
        started = time.monotonic()
        deadline = started + timeout
        msg_id = kc.execute(code, store_history=True, allow_stdin=False, stop_on_error=True)
        # Bounded like bash: a cell printing in a loop must not grow the
        # backend's memory without limit (Codex's head/tail capture).
        stdout_parts = HeadTailBuffer()
        stderr_parts = HeadTailBuffer()
        interrupted = False
        interrupt_sent_at: Optional[float] = None

        try:
            while True:
                if kc is not self._kc:
                    # Another caller already replaced this kernel; its channels are gone.
                    res.status = "restarted"
                    break
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
                    await (restart or self.restart)()
                    res.status = "restarted"
                    break
                msg = await self._next_iopub(kc)
                if msg is None:
                    if await self._died(kc):
                        res.status = "died"
                        self.started = False
                        break
                    continue
                if msg.get("parent_header", {}).get("msg_id") != msg_id:
                    continue
                mtype = msg["header"]["msg_type"]
                content = msg.get("content", {})
                if mtype == "stream":
                    text = content.get("text", "")
                    if content.get("name") == "stderr":
                        stderr_parts.write(text)
                    else:
                        stdout_parts.write(text)
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
        except asyncio.CancelledError:
            self._abandon_cell(msg_id, kc, interrupted)
            raise

        # Drain the execute_reply so the shell channel stays in sync.
        if res.status != "died":
            try:
                await asyncio.wait_for(kc.get_shell_msg(timeout=2.0), timeout=3.0)
            except Exception:  # noqa: BLE001
                pass

        res.stdout = strip_ansi(stdout_parts.text())
        res.stderr = strip_ansi(stderr_parts.text())
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
        elif res.status == "died":
            res.error_name = "KernelDied"
            res.error_value = ("The Python process exited while running this cell, often because it ran out of memory. "
                               "All in-memory variables are gone; reload data before continuing.")
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

    reap_interval = 60.0

    def __init__(self, workspace_factory: Callable[[str], Workspace], idle_seconds: int = 1800):
        self._sessions: dict[str, KernelSession] = {}
        self._factory = workspace_factory
        self.idle_seconds = idle_seconds
        self._reaper: Optional[asyncio.Task] = None
        self._reaper_tick: Optional[float] = None

    def reaper_status(self) -> dict:
        """Whether idle kernels are still being collected, for /health."""
        running = self._reaper is not None and not self._reaper.done()
        age = None if self._reaper_tick is None else round(time.monotonic() - self._reaper_tick, 1)
        return {"running": running, "last_tick_age_seconds": age}

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
            self._reaper_tick = time.monotonic()
            self._reaper = asyncio.create_task(self._reap_loop(), name="kernel-reaper")

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(self.reap_interval)
            self._reaper_tick = time.monotonic()
            now = time.time()
            for cid, session in list(self._sessions.items()):
                if session.started and now - session.last_used > self.idle_seconds and not session._lock.locked():
                    logger.info("Shutting down idle kernel for conversation %s", cid)
                    try:
                        await self.shutdown(cid)
                    except Exception:  # noqa: BLE001 - one bad kernel must not end collection for the rest
                        logger.exception("Could not shut down idle kernel for conversation %s", cid)


async def _uninterrupted(coro: Awaitable[None]) -> None:
    """Finish cleanup even if the caller is cancelled again, then pass that on.

    ``coro`` must not raise. Abandoning it halfway would orphan a kernel process.
    """
    task = asyncio.ensure_future(coro)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError


def _child_env(workspace_root: Path | None = None) -> dict:
    """Environment for the kernel child process (see ``app.agent.child_env``).

    The kernel plots through matplotlib's *inline* backend, not the shell's
    ``Agg``, so it overrides the shared default; ``workspace_root`` becomes the
    kernel's ``HOME`` so ``~`` is the workspace rather than the user's home.
    """
    return build_child_env(workspace_root=workspace_root, extra={"MPLBACKEND": KERNEL_MPLBACKEND})
