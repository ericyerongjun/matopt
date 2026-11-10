"""Process-wide singletons: database, kernel pool, tool registry, harness."""

from __future__ import annotations

import asyncio
import collections
import logging
import time
from pathlib import Path
from functools import lru_cache

from app.agent.harness import AgentHarness
from app.agent.execution import ToolExecutionService
from app.agent.runs import RunManager
from app.agent.kernel import KernelPool
from app.agent.mcp.client import McpHub
from app.agent.skills.loader import SkillRegistry
from app.agent.tools.base import ToolRegistry
from app.agent.tools.document_tool import ParseDocumentTool
from app.agent.tools.discovery_tool import SearchToolsTool, LoadToolSchemasTool
from app.agent.tools.file_tools import (
    EditFileTool, FindFilesTool, GrepTool, ListFilesTool, ReadFileTool, WriteFileTool,
)
from app.agent.tools.lean_tool import LeanCheckTool
from app.agent.tools.math_tools import CheckEquivalenceTool, WolframAlphaTool
from app.agent.tools.plan_tool import UpdatePlanTool
from app.agent.tools.progress_tool import ReportProgressTool
from app.agent.tools.result_tool import PublishResultTool
from app.agent.tools.python_tool import PythonTool
from app.agent.tools.router import ToolRouter
from app.agent.exec_rules import ExecPolicy
from app.agent.tools.exec_sessions import BashPollTool
from app.agent.tools.shell_tool import ShellTool
from app.agent.subagents import subagent_tools
from app.agent.automations import automation_tools
from app.agent.intent import SwitchToOptTool
from app.agent.tools.data_agent_tool import DataAgentTool
from app.agent.tools.skill_tools import LoadSkillTool, SearchSkillsTool
from app.agent.tools.web_search import WebFetchTool, WebSearchTool
from app.agent.tools.ui_tools import CreateUIAppTool, RegisterUIActionTool, RenderUITool
from app.agent.workspace import Workspace
from app.config import settings
from app.llm.registry import providers
from app.log_context import install_log_context
from app.storage.db import Database

logger = logging.getLogger(__name__)

LAG_SAMPLE_SECONDS = 0.5
LAG_WARNING_SECONDS = 1.0
MAINTENANCE_SECONDS = 30.0
PRUNE_SECONDS = 6 * 3600.0


class Runtime:
    def __init__(self):
        from app.agent.hooks import HookEngine
        from app.agent.plugins import PluginManager
        install_log_context()
        self.db = Database(settings.db_path)
        self.kernels = KernelPool(lambda cid: Workspace(cid, settings.workspace_dir), idle_seconds=settings.kernel_idle_seconds)
        self.plugins = PluginManager(settings)
        self.skills = SkillRegistry.discover([*settings.skill_directories(), *self.plugins.skill_dirs()])
        self.tools = build_tools(self.skills)
        from app.agent.memory import MemoryService, set_memory_service
        from app.agent.memory.session import SessionMemory, set_session_memory
        # Built here, not through get_memory_service(), which resolves the runtime.
        self.memories = MemoryService(settings, self.db, providers)
        set_memory_service(self.memories)
        if settings.memories_enabled:
            for tool in self.memories.tools():
                self.tools.register(tool)
        # Session memory shares the memory service's recall index.
        self.session_memory = SessionMemory(settings, self.db, self.memories.index, self.memories)
        set_session_memory(self.session_memory)
        if settings.session_memory_enabled:
            for tool in self.session_memory.tools():
                self.tools.register(tool)
        self.mcp = McpHub(
            settings.resolved_mcp_config_path() if settings.mcp_enabled else None,
            max_output_chars=settings.tool_max_output_chars,
            env_path=settings.resolved_mcp_env_path(),
        )
        self._mcp_lock = asyncio.Lock()
        self.tool_router = ToolRouter()
        self.providers = providers
        self.executions = ToolExecutionService(
            settings, hooks=HookEngine.from_settings(settings, extra=self.plugins.hook_config()))
        self._memory_task: asyncio.Task | None = None
        self._background: list[asyncio.Task] = []
        self._lag_samples: collections.deque[float] = collections.deque(maxlen=120)  # the last minute
        self._stalled_runs = 0
        self._started = False
        self.harness = AgentHarness(
            self.db, self.kernels, settings, providers, self.tools,
            skills=self.skills, tool_router=self.tool_router, executions=self.executions,
        )
        self.runs = RunManager(self.db, self.harness)
        self.automations = None
        if settings.automations_enabled:
            from app.agent.automations import AutomationService, AutomationStore, set_automation_service
            self.automations = AutomationService(
                AutomationStore(settings.automations_path or Path(settings.data_dir) / "automations.json"),
                settings.workspace_dir, self._start_automation_run, is_busy=self.runs.is_running)
            set_automation_service(self.automations)

    async def start(self) -> None:
        self.db.interrupt_running_subagents()
        await self.runs.recover()
        self.harness.ui.store.recover_pending()
        self.kernels.start_reaper()
        if self.automations is not None:
            self.automations.start()
        self._background = [asyncio.create_task(self._lag_loop(), name="event-loop-lag"),
                            asyncio.create_task(self._maintenance_loop(), name="maintenance")]
        self._started = True
        if settings.memories_enabled:
            self._memory_task = asyncio.create_task(self._memory_loop(), name="memories")
        if settings.mcp_enabled:
            from app.agent.mcp.resources import resource_tools
            for t in resource_tools(self.mcp):
                self.tools.register(t)
            await self._start_mcp()

    async def _start_mcp(self) -> None:
        mcp_names = {t.name for t in self.mcp.tools()}
        reserved = {n for n in self.tools.names() if n not in mcp_names}
        await self.mcp.start(reserved=reserved | {"list_mcp_resources", "read_mcp_resource"},
                             extra_servers=self.plugins.mcp_servers())
        for t in self.mcp.tools():
            self.tools.register(t)

    async def reload_mcp(self) -> list[dict]:
        """Reconnect every MCP server after ``mcp.json`` changed, without a restart.

        Calls already running on a server that is torn down fail with a
        transport error; new turns see the new tool set.
        """
        if not settings.mcp_enabled:
            return []
        async with self._mcp_lock:
            old = [t.name for t in self.mcp.tools()]
            await self.mcp.stop()
            for name in old:
                self.tools.unregister(name)
            await self._start_mcp()
            return self.mcp.status()

    async def _memory_loop(self) -> None:
        """Codex runs memory passes in the background at startup; MatOpt also repeats hourly."""
        while True:
            try:
                await self.memories.run_pass()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logging.getLogger(__name__).warning("Memory pass failed", exc_info=True)
            await asyncio.sleep(3600)

    async def _lag_loop(self) -> None:
        """How late a timer fires is how long something held the event loop."""
        while True:
            started = time.monotonic()
            await asyncio.sleep(LAG_SAMPLE_SECONDS)
            lag = max(0.0, time.monotonic() - started - LAG_SAMPLE_SECONDS)
            self._lag_samples.append(lag)
            if lag >= LAG_WARNING_SECONDS:
                logger.warning("The event loop was blocked for %.2fs", lag)

    async def maintain(self, prune: bool) -> None:
        self._stalled_runs = self.runs.check_stalled(settings.run_stall_seconds)
        if prune and settings.run_event_retention_days:
            cutoff = time.time() - settings.run_event_retention_days * 86400
            removed = await asyncio.to_thread(self.db.prune_run_events, cutoff)
            if removed:
                logger.info("Pruned %d run journal events past retention", removed)

    async def _maintenance_loop(self) -> None:
        next_prune = 0.0
        while True:
            try:
                due = time.monotonic() >= next_prune
                await self.maintain(due)
                if due:
                    next_prune = time.monotonic() + PRUNE_SECONDS
            except Exception:  # noqa: BLE001 - housekeeping must keep running
                logger.warning("Maintenance pass failed", exc_info=True)
            await asyncio.sleep(MAINTENANCE_SECONDS)

    def health(self) -> dict:
        """Liveness of the background work a static "ok" cannot show."""
        reaper = self.kernels.reaper_status()
        samples = self._lag_samples
        return {
            "status": "degraded" if self._started and not reaper["running"] else "ok",
            "kernel_reaper": reaper,
            "event_loop_lag_ms": {"last": round(samples[-1] * 1000, 1) if samples else None,
                                  "max_last_minute": round(max(samples) * 1000, 1) if samples else None},
            "runs": {"active": self.runs.active_count, "stalled": self._stalled_runs},
        }

    async def _start_automation_run(self, cid: str, text: str, row: dict) -> str | None:
        from app.agent.harness import RunRequest
        conv = self.db.get_conversation(cid)
        if conv is None:
            raise LookupError("conversation was deleted")
        run = await self.runs.start(RunRequest(conversation_id=cid, text=text, provider_id=conv.get("provider"),
                                               model_id=conv.get("model"), reasoning=conv.get("reasoning")))
        return run.get("id")

    async def stop(self) -> None:
        if self.automations is not None:
            await self.automations.stop()
        for task in self._background:
            task.cancel()
        await asyncio.gather(*self._background, return_exceptions=True)
        self._background = []
        if self._memory_task is not None:
            self._memory_task.cancel()
            await asyncio.gather(self._memory_task, return_exceptions=True)
        await self.runs.shutdown()
        await self.harness.shutdown()
        await self.mcp.stop()
        await self.executions.shutdown()
        shell = self.tools.get("bash")
        if shell is not None and hasattr(shell, "sessions"):
            await shell.sessions.shutdown()
        await self.kernels.shutdown_all()
        self.memories.index.close()
        self.db.close()
        # Its database is closed now, so the next caller must get a new runtime.
        _cached_runtime.cache_clear()

    def workspace(self, conversation_id: str) -> Workspace:
        return Workspace(conversation_id, settings.workspace_dir)


def build_tools(skills: SkillRegistry | None = None) -> ToolRegistry:
    skills = skills or SkillRegistry()
    shell = ShellTool(settings.tool_timeout, settings.tool_max_timeout, settings.tool_max_output_chars,
                      exec_policy=ExecPolicy.from_settings(settings))
    tools: list = [
        PythonTool(settings.tool_timeout, settings.tool_max_timeout, settings.tool_max_output_chars),
        shell,
        BashPollTool(shell.sessions),
        *subagent_tools(),
        SwitchToOptTool(),
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        ListFilesTool(),
        FindFilesTool(),
        GrepTool(),
        ParseDocumentTool(),
        CheckEquivalenceTool(),
        UpdatePlanTool(),
        ReportProgressTool(),
        PublishResultTool(),
        SearchSkillsTool(skills),
        LoadSkillTool(skills),
        RenderUITool(),
        CreateUIAppTool(),
        RegisterUIActionTool(),
    ]
    if settings.web_search_enabled:
        tools.extend([WebSearchTool(), WebFetchTool()])
    if settings.data_agent_enabled:
        tools.append(DataAgentTool())
    if settings.wolfram_app_id:
        tools.append(WolframAlphaTool(settings.wolfram_app_id))
    if lean := LeanCheckTool.detect():
        tools.append(lean)
    if settings.automations_enabled:
        tools.extend(automation_tools())
    registry = ToolRegistry(tools)
    discovery = ToolRouter()
    registry.register(SearchToolsTool(registry, discovery))
    registry.register(LoadToolSchemasTool(registry, discovery))
    return registry


@lru_cache(maxsize=1)
def _cached_runtime() -> Runtime:
    return Runtime()


def get_runtime() -> Runtime:
    return _cached_runtime()
