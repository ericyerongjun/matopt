import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

pytest.importorskip('fastmcp')
from fastmcp import Client
from mcp.types import Tool as McpTool, ToolAnnotations

from app.agent.events import ToolResult
from app.agent.execution import SessionExecutionReceipt, ToolExecutionService
from app.agent.harness import AgentHarness
from app.agent.mcp.client import McpHub, McpProxyTool
from app.agent.mcp.sandbox import McpSandbox, build_mcp
from app.agent.tools.base import Tool, ToolContext, ToolRegistry
from app.agent.tools.file_tools import EditFileTool, ReadFileTool, WriteFileTool
from app.agent.workspace import Workspace
from app.config import settings
from app.storage.db import Database
from tests.fake_provider import FakeProvider, FakeRegistry


@pytest.fixture
def runtime(tmp_path):
    cfg = settings.model_copy(update={'data_dir': tmp_path, 'tool_timeout': 1, 'align_monitor': True})
    db = Database(tmp_path / 'db.sqlite')
    tools = ToolRegistry([ReadFileTool(), WriteFileTool(), EditFileTool()])
    execution = ToolExecutionService(cfg, cleanup_timeout=.02)
    harness = AgentHarness(db, None, cfg, FakeRegistry(FakeProvider([])), tools, executions=execution)
    rt = SimpleNamespace(db=db, tools=tools, executions=execution, harness=harness, kernels=None,
                         workspace=lambda cid: Workspace(cid, tmp_path / 'workspaces'))
    yield rt
    db.close()


async def rpc(app, method, arguments):
    # Deliberately new HTTP client/request context on each call, with no cookie
    # or session identity. The workspace comes only from explicit arguments.
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as http:
        response = await http.post('/', headers={
            'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-11-25'},
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': method, 'arguments': arguments}})
    assert response.status_code == 200, response.text
    if response.headers.get('content-type', '').startswith('text/event-stream'):
        packets = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        packet = next(packet for packet in packets if packet.get('id') == 1)
    else:
        packet = response.json()
    assert 'error' not in packet, packet
    return packet['result']


@pytest.mark.asyncio
async def test_explicit_workspace_read_edit_across_stateless_http(runtime):
    mcp = build_mcp(runtime)
    app = mcp.http_app(path='/', stateless_http=True)
    async with app.router.lifespan_context(app):
        opened = await rpc(app, 'open_workspace', {})
        cid = opened['structuredContent']['conversation_id']
        (runtime.workspace(cid).root / 'file.txt').write_text('before')
        read = await rpc(app, 'read_file', {'conversation_id': cid, 'path': 'file.txt'})
        assert not read.get('isError') and read['structuredContent']['meta']['read_state']['path'] == 'file.txt'
        edited = await rpc(app, 'edit_file', {'conversation_id': cid, 'path': 'file.txt',
                                           'old_string': 'before', 'new_string': 'after'})
        assert not edited.get('isError'), edited
        assert (runtime.workspace(cid).root / 'file.txt').read_text() == 'after'
        other = await rpc(app, 'open_workspace', {})
        cid2 = other['structuredContent']['conversation_id']
        assert cid2 != cid and not (runtime.workspace(cid2).root / 'file.txt').exists()
    rows = runtime.db.list_session_events(cid)
    assert [row['type'] for row in rows] == ['execution/call', 'execution/result'] * 2


@pytest.mark.asyncio
async def test_observations_survive_new_server_instance(runtime):
    sandbox = McpSandbox(runtime)
    cid = sandbox.open_workspace()['conversation_id']
    (runtime.workspace(cid).root / 'file.txt').write_text('before')
    assert not (await sandbox.execute('read_file', cid, {'path': 'file.txt'})).is_error
    # App state is reconstructed from the same private SessionLog receipts.
    runtime.harness._session_logs.clear()
    sandbox = McpSandbox(runtime)
    result = await sandbox.execute('edit_file', cid, {'path': 'file.txt', 'old_string': 'before', 'new_string': 'after'})
    assert not result.is_error


@pytest.mark.asyncio
async def test_crash_then_inspection_then_retry_across_separate_mcp_requests(runtime):
    sandbox = McpSandbox(runtime)
    cid = sandbox.open_workspace()['conversation_id']
    (runtime.workspace(cid).root / 'file.txt').write_text('before')
    log = await runtime.harness._open_session(cid)
    async def emit(_):
        pass
    ctx = ToolContext(cid, runtime.workspace(cid), emit, asyncio.Event(), call_id='abandoned')
    args = {'path': 'file.txt', 'old_string': 'before', 'new_string': 'after'}
    await SessionExecutionReceipt(log).started(EditFileTool(), ctx, args)
    runtime.harness._session_logs.clear()  # only a durable start survived
    blocked = await McpSandbox(runtime).execute('edit_file', cid, args)
    assert blocked.structured_content['meta']['recovery_code'] == 'RECOVERY_CHECK_REQUIRED'
    assert not (await McpSandbox(runtime).execute('read_file', cid, {'path': 'file.txt'})).is_error
    result = await McpSandbox(runtime).execute('edit_file', cid, args)
    assert not result.is_error, result
    assert (runtime.workspace(cid).root / 'file.txt').read_text() == 'after'


@pytest.mark.asyncio
async def test_mcp_uses_same_native_busy_lease_and_explicit_identity(runtime):
    sandbox = McpSandbox(runtime)
    cid = sandbox.open_workspace()['conversation_id']
    cancel = runtime.harness.claim_external_run(cid)
    result = await sandbox.execute('write_file', cid, {'path': 'x.txt', 'content': 'no'})
    assert result.is_error and result.structured_content['meta']['code'] == 'WORKSPACE_BUSY'
    assert not (runtime.workspace(cid).root / 'x.txt').exists()
    runtime.harness.release_external_run(cid, cancel)
    for invalid in ('', '../outside', 'missing'):
        result = await sandbox.execute('read_file', invalid, {'path': 'x.txt'})
        assert result.is_error and result.structured_content['meta']['code'] == 'MCP_WORKSPACE_UNKNOWN'
    with pytest.raises(ValueError):
        sandbox.open_workspace('../outside')


@pytest.mark.parametrize('spec', [
    McpTool(name='lookup', description='Find an item', inputSchema={'type': 'object'}, annotations=ToolAnnotations(readOnlyHint=True)),
    {'name': 'lookup', 'description': 'Find an item', 'inputSchema': {'type': 'object'}, 'annotations': {'readOnlyHint': True}},
])
def test_typed_and_dictionary_metadata(spec):
    tool = McpProxyTool(None, 'server_with_underscores', spec)
    assert tool.read_only and tool.concurrent_safe and tool.side_effect_scope == 'none'
    assert tool.description == 'Find an item'
    assert tool.mcp_server == 'server_with_underscores'


@pytest.mark.asyncio
async def test_one_bad_server_or_tool_does_not_disable_healthy_tools(monkeypatch):
    import fastmcp
    import app.agent.mcp.client as client_module
    closed = []
    class FakeClient:
        def __init__(self, config):
            self.name = next(iter(config['mcpServers']))
        async def __aenter__(self):
            if self.name == 'broken':
                raise RuntimeError('unavailable')
        async def __aexit__(self, *_):
            closed.append(self.name)
        async def list_tools(self):
            return [ {'name': 'bad', 'inputSchema': {'type': 'not-a-json-schema-type'}},
                    {'name': 'valid', 'inputSchema': {'type': 'object'}, 'annotations': {'readOnlyHint': True}}]
    monkeypatch.setattr(fastmcp, 'Client', FakeClient)
    monkeypatch.setattr(client_module, 'load_mcp_config', lambda _: {'mcpServers': {'broken': {}, 'with_under': {}}})
    hub = McpHub()
    await hub.start()
    assert len(hub.tools()) == 1
    tool = hub.tools()[0]
    assert tool.name == 'with_under_valid' and tool.remote_name == 'valid' and tool.mcp_server == 'with_under'
    await hub.stop()
    assert sorted(closed) == ['broken', 'with_under']


@pytest.fixture
def tool_ctx(tmp_path):
    events = []
    async def emit(event):
        events.append(event)
    ctx = ToolContext('w', Workspace('w', tmp_path), emit, asyncio.Event(), call_id='remote',
                      settings=SimpleNamespace(tool_timeout=.04, tool_max_timeout=1, align_monitor=True))
    return ctx, events


@pytest.mark.asyncio
async def test_proxy_preserves_structured_media_and_actual_progress(tool_ctx):
    ctx, events = tool_ctx
    raw_image, raw_audio = b'image-data', b'audio-data'
    class Remote:
        async def call_tool(self, name, args, **options):
            assert options['timeout'] == .04 and options['raise_on_error'] is False
            await options['progress_handler'](2, 5, 'private remote status')
            return {'structuredContent': {'answer': 42, 'sources': ['one']}, 'content': [
                {'type': 'text', 'text': 'computed'},
                {'type': 'image', 'mimeType': 'image/png', 'data': base64.b64encode(raw_image).decode()},
                {'type': 'audio', 'mimeType': 'audio/wav', 'data': base64.b64encode(raw_audio).decode()},
                {'type': 'resource_link', 'name': 'details', 'uri': 'https://example.org/details'},
            ]}
    tool = McpProxyTool(Remote(), 'remote', {'name': 'lookup', 'annotations': {'readOnlyHint': True}})
    result = await ToolExecutionService(ctx.settings).dispatch(tool, ctx, {})
    assert result.ok and result.meta['structured_content']['answer'] == 42
    assert len(result.artifacts) == 2
    assert [ctx.workspace.resolve(a.path).read_bytes() for a in result.artifacts] == [raw_image, raw_audio]
    progress = next(event for event in events if event.type == 'tool_activity_progress')
    assert progress.data['progress'] == {'completed': 2, 'total': 5, 'unit': 'steps'}
    assert 'private' not in json.dumps(progress.data)
    assert any(event.type == 'tool_progress' for event in events)
    assert result.meta['mcp_content'][-1]['uri'] == 'https://example.org/details'


@pytest.mark.asyncio
async def test_long_structured_result_is_saved_in_full(tool_ctx):
    ctx, _ = tool_ctx
    payload = {'rows': [{'value': 'kept' * 1000}]}
    class Remote:
        async def call_tool(self, *_args, **_options):
            return {'structuredContent': payload, 'content': []}
    tool = McpProxyTool(Remote(), 'remote', {'name': 'lookup'}, max_output_chars=300)
    result = await tool(ctx, {})
    saved = json.loads(ctx.workspace.resolve(result.meta['mcp_result_path']).read_text())
    assert saved['structured_content'] == payload and len(result.output) < 500


@pytest.mark.asyncio
async def test_remote_transport_unknown_and_cancellation(tool_ctx):
    ctx, _ = tool_ctx
    class Broken:
        async def call_tool(self, *_args, **_options):
            raise TimeoutError('lost response')
    mutator = McpProxyTool(Broken(), 'remote', {'name': 'send'})
    result = await ToolExecutionService(ctx.settings).dispatch(mutator, ctx, {})
    assert result.meta['recovery_code'] == 'TOOL_OUTCOME_UNKNOWN'
    readonly = McpProxyTool(Broken(), 'remote', {'name': 'read', 'annotations': {'readOnlyHint': True}})
    assert 'recovery_code' not in (await ToolExecutionService(ctx.settings).dispatch(readonly, ctx, {})).meta
    entered, cancelled = asyncio.Event(), asyncio.Event()
    class Blocking:
        async def call_tool(self, *_args, **_options):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
    tool = McpProxyTool(Blocking(), 'remote', {'name': 'send_another'})
    service = ToolExecutionService(ctx.settings, cleanup_timeout=.01)
    task = asyncio.create_task(service.dispatch(tool, ctx, {}))
    await entered.wait()
    ctx.cancel.set()
    result = await task
    assert cancelled.is_set() and result.meta['recovery_code'] == 'TOOL_OUTCOME_UNKNOWN'
    assert not service._unsettled


@pytest.mark.asyncio
async def test_mcp_protocol_measured_progress_and_media_output(runtime):
    class ProgressImage(Tool):
        name = 'read_file'
        read_only = True
        async def run(self, ctx, path):
            await ctx.activity_progress(1, 3, unit='pages')
            image = ctx.workspace.resolve('sample.png')
            image.write_bytes(b'png-bytes')
            return ToolResult(ok=True, output='done', artifacts=[ctx.workspace.artifact_for(image)])
    runtime.tools.register(ProgressImage())
    mcp = build_mcp(runtime)
    progress = []
    async def on_progress(completed, total, message):
        progress.append((completed, total))
    async with Client(mcp) as client:
        opened = await client.call_tool('open_workspace', {})
        cid = opened.data['conversation_id']
        result = await client.call_tool('read_file', {'conversation_id': cid, 'path': 'sample.png'}, progress_handler=on_progress)
    assert progress == [(1, 3)]
    assert any(block.type == 'image' and base64.b64decode(block.data) == b'png-bytes' for block in result.content)
    assert result.structured_content['artifacts'][0]['path'] == 'sample.png'
