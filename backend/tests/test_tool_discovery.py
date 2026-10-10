"""Safety and retrieval contracts independent of any live model or MCP server."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.agent.events import ToolResult
from app.agent.skills.router import RoutePlan
from app.agent.tools.base import Tool, ToolContext, ToolRegistry
from app.agent.tools.discovery_tool import LoadToolSchemasTool, SearchToolsTool
from app.agent.tools.router import ToolRouter
from app.agent.workspace import Workspace


class CatalogTool(Tool):
    read_only = True
    concurrent_safe = True

    def __init__(self, name, description="", *, server="connector", visible=True, flags=()):
        self.name, self.description = name, description
        self.mcp_server, self.provider_visible, self.requires_flags = server, visible, flags
        self.schema_reads = 0
        self.executions = 0

    def schema(self):
        self.schema_reads += 1
        return super().schema()

    async def run(self, ctx, **kwargs):
        self.executions += 1
        return ToolResult(ok=True, output="executed")


@pytest.fixture
def ctx(tmp_path):
    async def emit(event):
        pass
    return ToolContext("discovery", Workspace("discovery", tmp_path), emit, asyncio.Event())


def names(schemas):
    return [schema["function"]["name"] for schema in schemas]


def test_dispatch_never_executes_fuzzy_or_punctuation_normalized_names():
    read = CatalogTool("read_file", server="")
    destructive = CatalogTool("delete_account")
    registry = ToolRegistry([read, destructive])
    router = ToolRouter()
    for typo in ("readfile", "read-file", "delete_acount", "DELETE_ACCOUNT"):
        tool, error = router.resolve(registry, typo)
        assert tool is None
        assert "No tool was executed" in error
    assert router.resolve(registry, "read")[0] is read
    assert router.resolve(registry, "read_file")[0] is read
    assert router.resolve(registry, "delete_account")[0] is destructive
    assert read.executions == destructive.executions == 0


def test_registered_exact_name_precedes_alias_and_unknown_hints_are_bounded():
    exact, alias = CatalogTool("read"), CatalogTool("read_file")
    registry = ToolRegistry([exact, alias] + [CatalogTool(f"service_{i:04d}") for i in range(3000)])
    router = ToolRouter()
    assert router.resolve(registry, "read")[0] is exact
    _, error = router.resolve(registry, "xyzzy")
    assert len(error) < 300


def test_small_catalog_fastpath_keeps_all_visible_tools_without_search(ctx):
    tools = [CatalogTool(f"remote_{i}") for i in range(29)] + [CatalogTool("python", server=""), CatalogTool("hidden", visible=False)]
    registry = ToolRegistry(tools)
    router = ToolRouter()
    router.search = lambda *args, **kwargs: pytest.fail("small catalogs do not require search")
    selected = router.schemas(registry, "unseen capability", ctx=ctx)
    assert len(selected) == 30
    assert "hidden" not in names(selected)
    assert tools[-1].schema_reads == 0


def test_pins_loaded_tools_and_skill_selections_survive_budget_and_query_changes(ctx):
    tools = [CatalogTool(f"remote_{i:02d}", "search mail inbox") for i in range(40)]
    native = CatalogTool("python", server="")
    registry = ToolRegistry([native] + tools)
    router = ToolRouter(mcp_budget=2)
    plan = RoutePlan(activate=[SimpleNamespace(allowed_tools=["remote_39"])])
    for query in ("mail inbox", "totally unrelated topic"):
        result = names(router.schemas(registry, query, plan, [f"remote_{i:02d}" for i in range(20, 30)],
                                      loaded_tools=["remote_38"], ctx=ctx))
        assert set(result) >= {"python", "remote_38", "remote_39", *[f"remote_{i:02d}" for i in range(20, 30)]}
    assert len(names(router.schemas(registry, "mail inbox", preferred_tools=["remote_39"], ctx=ctx))) == 4


def test_bm25_discovery_uses_metadata_and_handles_paraphrase_exact_ids_and_no_evidence(ctx):
    target = CatalogTool("gmail_search_messages", "Search email messages by sender and subject.", server="gmail")
    registry = ToolRegistry([target, CatalogTool("drive_delete_document", "Delete a drive document.", server="drive")])
    router = ToolRouter()
    assert router.search(registry, "locate correspondence in my inbox", ctx=ctx)[0].name == target.name
    assert router.search(registry, target.name, ctx=ctx)[0].name == target.name
    assert router.search(registry, "quantum bananas", ctx=ctx) == []
    assert all(tool.schema_reads == 0 and tool.executions == 0 for tool in registry)
    assert names(router.load_schemas(registry, [target.name], ctx=ctx)) == [target.name]
    assert target.schema_reads == 1
    assert registry.get("drive_delete_document").schema_reads == 0


def test_catalog_cache_refreshes_on_registration_replacement_metadata_and_flags(ctx):
    first = CatalogTool("first", "calculate invoice")
    registry = ToolRegistry([first])
    router = ToolRouter()
    assert router.search(registry, "invoice", ctx=ctx)[0].name == "first"
    first.description = "remove photo"
    assert not router.search(registry, "invoice", ctx=ctx)
    registry.register(CatalogTool("first", "calculate invoice"))
    assert router.search(registry, "invoice", ctx=ctx)[0].name == "first"
    gated = CatalogTool("web_search", "lookup public websites", flags=("web_search",))
    registry.register(gated)
    assert not router.search(registry, "websites", ctx=ctx)
    ctx.state["web_search"] = True
    assert router.search(registry, "websites", ctx=ctx)[0].name == "web_search"


@pytest.mark.asyncio
async def test_search_then_atomic_schema_load_refreshes_next_step_and_never_executes(ctx):
    target = CatalogTool("drive_download", "Download a file from cloud drive.")
    registry = ToolRegistry([target] + [CatalogTool(f"remote_{i}", "miscellaneous connector") for i in range(40)])
    router = ToolRouter()
    search, load = SearchToolsTool(registry, router), LoadToolSchemasTool(registry, router)
    found = await search.run(ctx, "download cloud file")
    assert found.ok and json.loads(found.output)["tools"][0]["name"] == target.name
    assert "parameters" not in found.output and target.schema_reads == 0
    invalid = await load.run(ctx, [target.name, "drive_downlod"])
    assert not invalid.ok and not ctx.state.get("loaded_tools") and target.schema_reads == 0
    loaded = await load.run(ctx, [target.name, target.name])
    assert loaded.ok and loaded.meta["refresh_tools"] is True
    assert ctx.state["loaded_tools"] == [target.name]
    assert names(router.schemas(registry, "different task now", loaded_tools=ctx.state["loaded_tools"], ctx=ctx)) == [target.name]
    assert all(tool.executions == 0 for tool in registry)


@pytest.mark.asyncio
async def test_discovery_cannot_bypass_disabled_or_hidden_tools(ctx):
    registry = ToolRegistry([CatalogTool("hidden", "invoice", visible=False),
                             CatalogTool("gated", "invoice", flags=("enabled",))])
    router = ToolRouter()
    assert await SearchToolsTool(registry, router).run(ctx, "invoice")
    assert json.loads((await SearchToolsTool(registry, router).run(ctx, "invoice")).output)["tools"] == []
    for name in registry.names():
        result = await LoadToolSchemasTool(registry, router).run(ctx, [name])
        assert not result.ok and "unavailable" in result.error
    assert router.schemas(registry, "invoice", preferred_tools=registry.names(), ctx=ctx) == []
    assert not ctx.state.get("loaded_tools")


@pytest.mark.asyncio
@pytest.mark.parametrize("query,limit", [("", 8), ("x" * 2001, 8), (42, 8), ("invoice", 0), ("invoice", True)])
async def test_search_validates_public_arguments(ctx, query, limit):
    assert not (await SearchToolsTool(ToolRegistry()).run(ctx, query, limit)).ok


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [[], ["x"] * 21, "read_file", [None]])
async def test_load_validates_public_arguments(ctx, value):
    assert not (await LoadToolSchemasTool(ToolRegistry()).run(ctx, value)).ok


@pytest.mark.asyncio
async def test_harness_load_refreshes_next_provider_request_after_midtask_discovery(tmp_path):
    from copy import deepcopy
    from app.agent.harness import AgentHarness, RunRequest
    from app.config import settings
    from app.storage.db import Database
    from tests.fake_provider import FakeProvider, FakeRegistry

    class SchemaProvider(FakeProvider):
        def __init__(self):
            super().__init__([
                {"tool": "search_tools", "args": {"query": "download cloud drive file"}},
                {"tool": "load_tool_schemas", "args": {"names": ["drive_download_file"]}},
                {"tool": "drive_download_file", "args": {}},
                {"text": "The file is ready."},
            ])
            self.offered = []

        async def stream(self, messages, tools, options):
            self.offered.append(deepcopy(tools))
            async for event in super().stream(messages, tools, options):
                yield event

    provider = SchemaProvider()
    target = CatalogTool("drive_download_file", "Download a cloud drive file.")
    registry = ToolRegistry([target] + [CatalogTool(f"unrelated_{i}", "schedule calendar appointment") for i in range(40)])
    router = ToolRouter()
    registry.register(SearchToolsTool(registry, router))
    registry.register(LoadToolSchemasTool(registry, router))
    config = settings.model_copy(update={"data_dir": tmp_path, "skill_auto_activate": False, "lambda_enabled": False,
        "align_monitor": False, "document_auto_parse": False, "agent_max_steps": 8})
    db = Database(tmp_path / "discovery.sqlite")
    kernels = SimpleNamespace(peek=lambda cid: None)
    harness = AgentHarness(db, kernels, config, FakeRegistry(provider), registry, tool_router=router)
    cid = db.create_conversation(title="Discovery contract")["id"]
    try:
        events = [event.to_dict() async for event in harness.run(RunRequest(
            conversation_id=cid, text="Schedule an appointment", verify=False, strict_final=True))]
        await harness.wait_side_tasks()
        assert "drive_download_file" not in names(provider.offered[0])
        assert "drive_download_file" not in names(provider.offered[1])
        assert "drive_download_file" in names(provider.offered[2])
        assert target.executions == 1
        assert events[-1]["type"] == "done"
    finally:
        await harness.shutdown()
        db.close()
