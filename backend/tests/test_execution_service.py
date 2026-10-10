import asyncio
import json
import os
from types import SimpleNamespace

import pytest

from app.agent.events import ToolResult
from app.agent.execution import SessionExecutionReceipt, ToolExecutionService, restore_execution_state
from app.agent.recovery_guard import RecoveryGuard
from app.agent.session import SessionLog
from app.agent.tools.base import Tool, ToolContext
from app.agent.tools.file_tools import EditFileTool, ReadFileTool
from app.agent.tools.shell_tool import ShellTool
from app.agent.workspace import Workspace
from app.storage.db import Database


@pytest.fixture
def context(tmp_path):
    events = []
    async def emit(event):
        events.append(event)
    ctx = ToolContext('workspace', Workspace('workspace', tmp_path), emit, asyncio.Event(), call_id='test')
    ctx.state['recovery_guard'] = RecoveryGuard()
    return ctx, events


def service(**kwargs):
    return ToolExecutionService(SimpleNamespace(align_monitor=True, tool_timeout=.04, tool_max_timeout=1),
                                cleanup_timeout=.02, **kwargs)


class RecordingTool(Tool):
    name = 'record'
    parameters = {'type': 'object', 'properties': {'value': {'type': 'integer'}}, 'required': ['value']}
    read_only = True
    def __init__(self):
        self.normalized = self.executed = 0
    def resolve_input(self, args):
        self.normalized += 1
        return {**args, 'value': args.get('value', 0) + 1}
    async def run(self, ctx, value):
        self.executed += 1
        return ToolResult(ok=True, output=str(value))


@pytest.mark.asyncio
async def test_shared_boundary_normalizes_once_and_validates_before_body(context):
    ctx, events = context
    tool, execute = RecordingTool(), service()
    result = await execute.dispatch(tool, ctx, {'value': 2})
    assert result.ok and result.output == '3'
    assert tool.normalized == tool.executed == 1
    result = await execute.dispatch(tool, ctx, {'value': 4}, normalized=True)
    assert result.ok and result.output == '4' and tool.normalized == 1
    invalid = await execute.dispatch(tool, ctx, {'value': 'bad'}, normalized=True)
    assert not invalid.ok and invalid.meta['code'] == 'TOOL_ARGUMENTS_INVALID' and tool.executed == 2
    assert [e.type for e in events] == ['tool_running', 'tool_running']


@pytest.mark.asyncio
async def test_capability_and_monitor_run_on_same_arguments(context):
    ctx, events = context
    tool = RecordingTool()
    tool.requires_flags = ('enabled',)
    assert (await service().dispatch(tool, ctx, {'value': 1})).meta['capability_disabled']
    ctx.state['enabled'] = True
    shell = ShellTool()
    blocked = await service().dispatch(shell, ctx, {'command': 'shutdown now'})
    assert blocked.meta['monitor'] == 'block'
    assert events[-1].type == 'monitor' and not any(e.type == 'tool_running' for e in events)


class HangingMutation(Tool):
    name = 'external_mutation'
    parameters = {'type': 'object', 'properties': {'target': {'type': 'string'}}, 'required': ['target']}
    def __init__(self):
        self.entered = asyncio.Event()
        self.exited = asyncio.Event()
        self.calls = 0
    async def run(self, ctx, target):
        self.calls += 1
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.exited.set()


@pytest.mark.asyncio
async def test_timeout_is_durable_unknown_and_blocks_same_turn_retry(context, tmp_path):
    ctx, _ = context
    db = Database(tmp_path / 'db.sqlite')
    db.create_conversation(conversation_id=ctx.conversation_id)
    log = await SessionLog.open(db, ctx.conversation_id)
    tool, execute = HangingMutation(), service()
    result = await execute.dispatch(tool, ctx, {'target': 'invoice'}, receipt=SessionExecutionReceipt(log))
    assert not result.ok and result.meta['code'] == 'TOOL_TIMEOUT'
    assert result.meta['recovery_code'] == 'TOOL_OUTCOME_UNKNOWN'
    assert tool.exited.is_set() and not execute._unsettled
    rows = db.list_session_events(ctx.conversation_id)
    assert [row['type'] for row in rows] == ['execution/call', 'execution/result']
    assert rows[-1]['data']['meta']['recovery_code'] == 'TOOL_OUTCOME_UNKNOWN'
    retry = await execute.dispatch(tool, ctx, {'target': 'invoice'})
    assert retry.meta['recovery_code'] == 'RECOVERY_CHECK_REQUIRED' and tool.calls == 1
    # The same outcome is protected after a new application/context load.
    ctx.state = {}
    restore_execution_state(ctx, rows)
    assert (await execute.dispatch(tool, ctx, {'target': 'invoice'})).meta['recovery_code'] == 'RECOVERY_CHECK_REQUIRED'
    db.close()


@pytest.mark.asyncio
async def test_disconnect_cancellation_settles_receipt_before_propagating(context):
    ctx, _ = context
    log, tool, execute = SessionLog(), HangingMutation(), service()
    task = asyncio.create_task(execute.dispatch(tool, ctx, {'target': 'invoice'}, receipt=SessionExecutionReceipt(log)))
    await tool.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert tool.exited.is_set()
    assert log.events[-1]['type'] == 'execution/result'
    assert log.events[-1]['data']['meta']['recovery_code'] == 'TOOL_OUTCOME_UNKNOWN'


@pytest.mark.asyncio
async def test_explicit_stop_reaches_tool_and_readonly_timeout_is_safe(context):
    ctx, _ = context
    tool, execute = HangingMutation(), service()
    tool.read_only = True
    task = asyncio.create_task(execute.dispatch(tool, ctx, {'target': 'query'}))
    await tool.entered.wait()
    ctx.cancel.set()
    result = await task
    assert result.meta['cancelled'] and 'recovery_code' not in result.meta
    assert tool.exited.is_set()


def native_read_event(log, result):
    log.set_system({'role': 'system', 'content': 'test'})
    log.append('turn/start', {'turn': 1})
    log.append('step/start', {'turn': 1, 'step': 1})
    log.append('assistant/message', {'turn': 1, 'step': 1, 'stream': [], 'message': {
        'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'read', 'type': 'function',
        'function': {'name': 'read_file', 'arguments': '{"path":"file.txt"}'}}]}}, surface_op='append')
    log.append('tool/call', {'turn': 1, 'step': 1, 'callId': 'read', 'name': 'read_file',
                           'arguments': '{"path":"file.txt"}'})
    log.append('tool/result', {'turn': 1, 'step': 1, 'callId': 'read', 'name': 'read_file', **result.to_dict(),
        'message': {'role': 'tool', 'tool_call_id': 'read', 'content': result.output}}, surface_op='append')
    log.append('step/end', {'turn': 1, 'step': 1})
    log.append('turn/end', {'turn': 1, 'reason': {'kind': 'completed'}})


@pytest.mark.asyncio
async def test_native_to_mcp_and_mcp_to_native_observation_restore(context):
    ctx, _ = context
    (ctx.workspace.root / 'file.txt').write_text('before')
    result = await ReadFileTool()(ctx, {'path': 'file.txt'})
    log = SessionLog()
    native_read_event(log, result)
    ctx.state = {}
    restore_execution_state(ctx, log.events)
    assert (await service().dispatch(EditFileTool(), ctx,
            {'path': 'file.txt', 'old_string': 'before', 'new_string': 'after'}, receipt=SessionExecutionReceipt(log))).ok
    ctx.state = {}
    restore_execution_state(ctx, log.events)
    assert (await service().dispatch(EditFileTool(), ctx,
            {'path': 'file.txt', 'old_string': 'after', 'new_string': 'native'})).ok


@pytest.mark.asyncio
async def test_compacted_native_observation_is_not_resurrected(context):
    ctx, _ = context
    (ctx.workspace.root / 'file.txt').write_text('before')
    log = SessionLog()
    native_read_event(log, await ReadFileTool()(ctx, {'path': 'file.txt'}))
    log.sync_history([{'role': 'system', 'content': 'test'}, {'role': 'user', 'content': 'compacted'}])
    for _ in range(2):
        restore_execution_state(ctx, log.events)
        result = await service().dispatch(EditFileTool(), ctx,
            {'path': 'file.txt', 'old_string': 'before', 'new_string': 'after'})
        assert result.meta['code'] == 'FS_NOT_OBSERVED'


@pytest.mark.asyncio
async def test_restart_unanswered_mcp_mutation_requires_relevant_inspection(context):
    ctx, _ = context
    (ctx.workspace.root / 'file.txt').write_text('before')
    log = SessionLog()
    await SessionExecutionReceipt(log).started(EditFileTool(), ctx,
        {'path': 'file.txt', 'old_string': 'before', 'new_string': 'after'})
    restore_execution_state(ctx, log.events)
    args = {'path': 'file.txt', 'old_string': 'before', 'new_string': 'after'}
    execute = service()
    assert (await execute.dispatch(EditFileTool(), ctx, args)).meta['recovery_code'] == 'RECOVERY_CHECK_REQUIRED'
    assert (await execute.dispatch(ReadFileTool(), ctx, {'path': 'file.txt'})).ok
    assert (await execute.dispatch(EditFileTool(), ctx, args)).ok


def test_tool_specific_deadline_and_command_ceiling():
    tool = RecordingTool()
    tool.timeout_ms = 600_000
    assert service().timeout_for(tool, {}) == 600
    assert service().timeout_for(ShellTool(), {'timeout': 900}) == 1


@pytest.mark.asyncio
async def test_shell_task_cancellation_reaps_process_group(context):
    ctx, _ = context
    child_started = asyncio.Event()
    pid = None
    async def emit(event):
        nonlocal pid
        if event.type == 'tool_progress' and event.data['delta'].strip().isdigit():
            pid = int(event.data['delta'].strip())
            child_started.set()
    ctx.emit = emit
    task = asyncio.create_task(ShellTool()(ctx, {'command': 'echo $$; exec sleep 30'}))
    await asyncio.wait_for(child_started.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
