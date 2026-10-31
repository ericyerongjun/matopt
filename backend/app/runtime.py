"""Process-wide singletons: database, kernel pool, tool registry, harness."""

from __future__ import annotations

import asyncio
import logging
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
from app.agent.intent import SwitchToOptTool
from app.agent.tools.data_agent_tool import DataAgentTool
from app.agent.tools.skill_tools import LoadSkillTool, SearchSkillsTool
from app.agent.tools.web_search import WebFetchTool, WebSearchTool
from app.agent.tools.ui_tools import CreateUIAppTool, RegisterUIActionTool, RenderUITool
from app.agent.workspace import Workspace
from app.config import settings
from app.llm.registry import providers
from app.storage.db import Database


class Runtime:
    def __init__(self):
        from app.agent.hooks import HookEngine
        from app.agent.plugins import PluginManager
        self.db = Database(settings.db_path)
        self.kernels = KernelPool(lambda cid: Workspace(cid, settings.workspace_dir), idle_seconds=settings.kernel_idle_seconds)
        self.plugins = PluginManager(settings)
        self.skills = SkillRegistry.discover([*settings.skill_directories(), *self.plugins.skill_dirs()])
        self.tools = build_tools(self.skills)
        from app.agent.memory import MemoryService, set_memory_service
        # Built here, not through get_memory_service(), which resolves the runtime.
        self.memories = MemoryService(settings, self.db, providers)
        set_memory_service(self.memories)
        if settings.memories_enabled:
            for tool in self.memories.tools():
                self.tools.register(tool)
        self.mcp = McpHub(
            settings.resolved_mcp_config_path() if settings.mcp_enabled else None,
            max_output_chars=settings.tool_max_output_chars,
        )
        self.tool_router = ToolRouter()
        self.providers = providers
        self.executions = ToolExecutionService(
            settings, hooks=HookEngine.from_settings(settings, extra=self.plugins.hook_config()))
        self._memory_task: asyncio.Task | None = None
        self.harness = AgentHarness(
            self.db, self.kernels, settings, providers, self.tools,
            skills=self.skills, tool_router=self.tool_router, executions=self.executions,
        )
        self.runs = RunManager(self.db, self.harness)

    async def start(self) -> None:
        self.db.interrupt_running_subagents()
        await self.runs.recover()
        self.harness.ui.store.recover_pending()
        self.kernels.start_reaper()
        if settings.memories_enabled:
            self._memory_task = asyncio.create_task(self._memory_loop(), name="memories")
        if settings.mcp_enabled:
            from app.agent.mcp.resources import resource_tools
            await self.mcp.start(reserved=set(self.tools.names()) | {"list_mcp_resources", "read_mcp_resource"},
                                 extra_servers=self.plugins.mcp_servers())
            for t in resource_tools(self.mcp):
                self.tools.register(t)
            for t in self.mcp.tools():
                self.tools.register(t)

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

    async def stop(self) -> None:
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
        self.db.close()

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
    registry = ToolRegistry(tools)
    discovery = ToolRouter()
    registry.register(SearchToolsTool(registry, discovery))
    registry.register(LoadToolSchemasTool(registry, discovery))
    return registry


@lru_cache(maxsize=1)
def get_runtime() -> Runtime:
    return Runtime()
