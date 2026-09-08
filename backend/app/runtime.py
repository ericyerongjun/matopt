"""Process-wide singletons: database, kernel pool, tool registry, harness."""

from __future__ import annotations

from functools import lru_cache

from app.agent.harness import AgentHarness
from app.agent.kernel import KernelPool
from app.agent.tools.base import ToolRegistry
from app.agent.tools.document_tool import ParseDocumentTool
from app.agent.tools.file_tools import EditFileTool, GrepTool, ListFilesTool, ReadFileTool, WriteFileTool
from app.agent.tools.math_tools import CheckEquivalenceTool, WolframAlphaTool
from app.agent.tools.plan_tool import UpdatePlanTool
from app.agent.tools.python_tool import PythonTool
from app.agent.tools.shell_tool import ShellTool
from app.agent.workspace import Workspace
from app.config import settings
from app.llm.registry import providers
from app.storage.db import Database


class Runtime:
    def __init__(self):
        self.db = Database(settings.db_path)
        self.kernels = KernelPool(lambda cid: Workspace(cid, settings.workspace_dir), idle_seconds=settings.kernel_idle_seconds)
        self.tools = build_tools()
        self.providers = providers
        self.harness = AgentHarness(self.db, self.kernels, settings, providers, self.tools)

    async def start(self) -> None:
        self.kernels.start_reaper()

    async def stop(self) -> None:
        await self.kernels.shutdown_all()
        self.db.close()

    def workspace(self, conversation_id: str) -> Workspace:
        return Workspace(conversation_id, settings.workspace_dir)


def build_tools() -> ToolRegistry:
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
    ]
    if settings.wolfram_app_id:
        tools.append(WolframAlphaTool(settings.wolfram_app_id))
    return ToolRegistry(tools)


@lru_cache(maxsize=1)
def get_runtime() -> Runtime:
    return Runtime()
