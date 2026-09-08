import pytest

pytest.importorskip("fastmcp")

from fastmcp import Client

from app.agent.mcp.sandbox import build_mcp


@pytest.mark.asyncio
async def test_sandbox_mcp_lists_tools():
    mcp = build_mcp()
    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}
        for expected in ("open_workspace", "python", "bash", "read_file", "write_file", "check_equivalence"):
            assert expected in names, expected
