import asyncio
import base64
import json
from types import SimpleNamespace

import pytest

pytest.importorskip('fastmcp')
from fastmcp import Client, FastMCP
from mcp.types import ListResourcesResult, Resource, TextResourceContents

from app.agent.mcp.client import McpHub
from app.agent.mcp.resources import ListMcpResourcesTool, ReadMcpResourceTool, resource_tools
from app.agent.tools.base import ToolContext
from app.agent.workspace import Workspace


def make_client(name, *, resources=True, fail_enter=False, pages=None, contents=None, read_error=None):
    class FakeClient:
        def __init__(self, config):
            self.name = next(iter(config['mcpServers']))
            self.server_capabilities = SimpleNamespace(resources={} if resources else None)

        async def __aenter__(self):
            if fail_enter:
                raise RuntimeError('https://secret-token@host/mcp unreachable')
            return self

        async def __aexit__(self, *_):
            pass

        async def list_tools(self):
            return [{'name': 'lookup', 'inputSchema': {'type': 'object'}, 'annotations': {'readOnlyHint': True}}]

        async def list_resources_mcp(self, cursor=None):
            page = (pages or {}).get(cursor, ([], None))
            return ListResourcesResult(resources=[Resource(uri=u, name=u.rsplit('/', 1)[-1]) for u in page[0]],
                                       nextCursor=page[1])

        async def read_resource(self, uri):
            if read_error:
                raise read_error
            return (contents or {}).get(uri, [])
    return FakeClient


@pytest.fixture
def ctx(tmp_path):
    async def emit(event):
        pass
    return ToolContext('w', Workspace('w', tmp_path), emit, asyncio.Event(), call_id='c')


async def start_hub(monkeypatch, servers, clients):
    import fastmcp
    import app.agent.mcp.client as client_module

    def factory(config):
        name = next(iter(config['mcpServers']))
        return clients[name](config)
    monkeypatch.setattr(fastmcp, 'Client', factory)
    monkeypatch.setattr(client_module, 'load_mcp_config', lambda _: {'mcpServers': servers})
    hub = McpHub(max_output_chars=2_000)
    await hub.start()
    return hub


@pytest.mark.asyncio
async def test_status_reports_each_server_without_secrets(monkeypatch):
    hub = await start_hub(monkeypatch, {'docs': {}, 'plain': {}, 'down': {'url': 'https://secret-token@host/mcp'},
                                        'off': {'enabled': False}},
                          {'docs': make_client('docs'), 'plain': make_client('plain', resources=False),
                           'down': make_client('down', fail_enter=True)})
    rows = {row['name']: row for row in hub.status()}
    assert rows['docs'] == {'name': 'docs', 'state': 'connected', 'tools': 1, 'resources': True}
    assert rows['plain']['resources'] is False and rows['plain']['state'] == 'connected'
    assert rows['down']['state'] == 'failed' and rows['down']['tools'] == 0
    assert 'secret' not in rows['down']['error'] and 'host' not in rows['down']['error']
    assert rows['off'] == {'name': 'off', 'state': 'disabled', 'tools': 0, 'resources': False}
    assert hub.resource_servers() == ['docs'] and hub.client('docs') is not None
    await hub.stop()
    assert hub.status() == [] and not hub.supports_resources()


@pytest.mark.asyncio
async def test_tools_hidden_without_resource_servers(monkeypatch, ctx):
    hub = await start_hub(monkeypatch, {'plain': {}}, {'plain': make_client('plain', resources=False)})
    assert not any(tool.is_provider_visible(ctx) for tool in resource_tools(hub))
    await hub.stop()


@pytest.mark.asyncio
async def test_list_all_servers_and_paginate_one(monkeypatch, ctx):
    pages = {None: (['file:///a.txt', 'file:///b.txt'], 'next'), 'next': (['file:///c.txt'], None)}
    hub = await start_hub(monkeypatch, {'docs': {}, 'more': {}},
                          {'docs': make_client('docs', pages=pages),
                           'more': make_client('more', pages={None: (['db://schema'], None)})})
    tool = ListMcpResourcesTool(hub)
    assert tool.is_provider_visible(ctx) and tool.read_only and tool.side_effect_scope == 'none'
    every = json.loads((await tool(ctx, {})).output)
    assert [(r['server'], r['uri']) for r in every['resources']] == [
        ('docs', 'file:///a.txt'), ('docs', 'file:///b.txt'), ('more', 'db://schema')]
    assert 'nextCursor' not in every
    first = json.loads((await tool(ctx, {'server': 'docs'})).output)
    assert first['nextCursor'] == 'next'
    second = json.loads((await tool(ctx, {'server': 'docs', 'cursor': 'next'})).output)
    assert [r['uri'] for r in second['resources']] == ['file:///c.txt']
    bad = await tool(ctx, {'cursor': 'next'})
    assert not bad.ok and 'cursor can only be used' in bad.error
    unknown = await tool(ctx, {'server': 'nope'})
    assert not unknown.ok and unknown.meta['code'] == 'MCP_UNKNOWN_SERVER'
    await hub.stop()


@pytest.mark.asyncio
async def test_read_text_blob_and_errors(monkeypatch, ctx):
    from mcp.shared import exceptions
    from mcp.types import BlobResourceContents
    McpError = getattr(exceptions, 'MCPError', None) or exceptions.McpError
    contents = {
        'file:///a.txt': [TextResourceContents(uri='file:///a.txt', mimeType='text/plain', text='hello ' * 2000)],
        'file:///p.png': [BlobResourceContents(uri='file:///p.png', mimeType='image/png',
                                               blob=base64.b64encode(b'png').decode())],
    }
    hub = await start_hub(monkeypatch, {'docs': {}, 'plain': {}, 'err': {}},
                          {'docs': make_client('docs', contents=contents),
                           'plain': make_client('plain', resources=False),
                           'err': make_client('err', read_error=McpError(-32002, 'Resource not found'))})
    tool = ReadMcpResourceTool(hub)
    text = await tool(ctx, {'server': 'docs', 'uri': 'file:///a.txt'})
    assert text.ok and text.output.startswith('hello') and len(text.output) <= 2_100  # bounded
    assert text.meta['mcp_server'] == 'docs'
    blob = await tool(ctx, {'server': 'docs', 'uri': 'file:///p.png'})
    assert blob.ok and blob.artifacts and blob.artifacts[0].mime == 'image/png'
    assert ctx.workspace.resolve(blob.artifacts[0].path).read_bytes() == b'png'
    unsupported = await tool(ctx, {'server': 'plain', 'uri': 'x'})
    assert not unsupported.ok and unsupported.meta['code'] == 'MCP_RESOURCES_UNSUPPORTED'
    failed = await tool(ctx, {'server': 'err', 'uri': 'x'})
    assert not failed.ok and 'Resource not found' in failed.error
    await hub.stop()


@pytest.mark.asyncio
async def test_real_fastmcp_server_resources(ctx):
    server = FastMCP('docs')

    @server.resource('resource://notes/readme', mime_type='text/plain')
    def readme() -> str:
        return 'MatOpt notes'

    async with Client(server) as client:
        hub = McpHub()
        hub._by_server['docs'] = client
        hub._set_status('docs', 'connected', resources=True)
        from app.agent.mcp.client import _advertises_resources
        assert _advertises_resources(client)
        listed = json.loads((await ListMcpResourcesTool(hub)(ctx, {'server': 'docs'})).output)
        assert [r['uri'] for r in listed['resources']] == ['resource://notes/readme']
        read = await ReadMcpResourceTool(hub)(ctx, {'server': 'docs', 'uri': 'resource://notes/readme'})
        assert read.ok and read.output == 'MatOpt notes'
