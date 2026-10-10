"""Process-wide singletons: database, kernel pool, tool registry, harness."""

from __future__ import annotations

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
from app.agent.tools.file_tools import EditFileTool, GrepTool, ListFilesTool, ReadFileTool, WriteFileTool
from app.agent.tools.math_tools import CheckEquivalenceTool, WolframAlphaTool
from app.agent.tools.plan_tool import UpdatePlanTool
from app.agent.tools.progress_tool import ReportProgressTool
from app.agent.tools.result_tool import PublishResultTool
from app.agent.tools.python_tool import PythonTool
from app.agent.tools.router import ToolRouter
from app.agent.tools.shell_tool import ShellTool
from app.agent.tools.lambda_tool import LambdaDataAgentTool
from app.agent.tools.skill_tools import LoadSkillTool, SearchSkillsTool
from app.agent.tools.web_search import WebFetchTool, WebSearchTool
from app.agent.tools.ui_tools import CreateUIAppTool, RegisterUIActionTool, RenderUITool
from app.agent.workspace import Workspace
from app.config import settings
from app.llm.registry import providers
from app.storage.db import Database


class Runtime:
    def __init__(self):
        self.db = Database(settings.db_path)
        self.kernels = KernelPool(lambda cid: Workspace(cid, settings.workspace_dir), idle_seconds=settings.kernel_idle_seconds)
        self.skills = SkillRegistry.discover(settings.skill_directories())
        self.tools = build_tools(self.skills)
        self.mcp = McpHub(
            settings.resolved_mcp_config_path() if settings.mcp_enabled else None,
            max_output_chars=settings.tool_max_output_chars,
        )
        self.tool_router = ToolRouter()
        self.providers = providers
        self.executions = ToolExecutionService(settings)
        self.harness = AgentHarness(
            self.db, self.kernels, settings, providers, self.tools,
            skills=self.skills, tool_router=self.tool_router, executions=self.executions,
        )
        self.runs = RunManager(self.db, self.harness)

    async def start(self) -> None:
        await self.runs.recover()
        self.harness.ui.store.recover_pending()
        self.kernels.start_reaper()
        if settings.mcp_enabled:
            await self.mcp.start()
            for t in self.mcp.tools():
                self.tools.register(t)

    async def stop(self) -> None:
        await self.runs.shutdown()
        await self.harness.shutdown()
        await self.mcp.stop()
        await self.executions.shutdown()
        await self.kernels.shutdown_all()
        self.db.close()

    def workspace(self, conversation_id: str) -> Workspace:
        return Workspace(conversation_id, settings.workspace_dir)


def build_tools(skills: SkillRegistry | None = None) -> ToolRegistry:
    skills = skills or SkillRegistry()
    tools: list = [
        PythonTool(settings.tool_timeout, settings.tool_max_timeout, settings.tool_max_output_chars),
        ShellTool(settings.tool_timeout, settings.tool_max_timeout, settings.tool_max_output_chars),
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        ListFilesTool(),
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
    if settings.lambda_enabled:
        tools.append(LambdaDataAgentTool())
    if settings.wolfram_app_id:
        tools.append(WolframAlphaTool(settings.wolfram_app_id))
    registry = ToolRegistry(tools)
    discovery = ToolRouter()
    registry.register(SearchToolsTool(registry, discovery))
    registry.register(LoadToolSchemasTool(registry, discovery))
    return registry


@lru_cache(maxsize=1)
def get_runtime() -> Runtime:
    return Runtime()
