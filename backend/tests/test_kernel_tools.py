"""Code mode: the kernel's ``tools`` object and its Unix-socket host."""

from __future__ import annotations

import asyncio
import json
import os
import struct
import sys
from types import SimpleNamespace

import pytest

from app.agent.events import Event, ToolResult
from app.agent.kernel import KernelPool
from app.agent.kernel_tools import KernelToolBinding, KernelToolServer, kernel_callable
from app.agent.sandbox import SEATBELT
from app.agent.tools.base import Tool, ToolContext, ToolRegistry
from app.agent.tools.python_tool import PythonTool
from app.agent.workspace import Workspace
from app.config import settings


class _FakeRead(Tool):
    name = "read_file"
    read_only = True
    description = "Read a file."
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}

    async def run(self, ctx, **kw):  # pragma: no cover - dispatch is faked
        return ToolResult(ok=True)


class _FakeBash(_FakeRead):
    name = "bash"
    read_only = False


class _FakeMcp(_FakeRead):
    def __init__(self, name, read_only):
        self.name, self.read_only, self.mcp_server = name, read_only, "srv"


def make_ctx(ws, kernels=None, **state):
    events: list[Event] = []

    async def emit(e):
        events.append(e)

    ctx = ToolContext(conversation_id="c1", workspace=ws, emit=emit, cancel=asyncio.Event(),
                      call_id="call_py", kernels=kernels, settings=settings)
    ctx.state.update(state)
    return ctx, events


@pytest.fixture
def code_mode(monkeypatch):
    monkeypatch.setattr(settings, "code_mode_enabled", True)
    monkeypatch.setattr(settings, "code_mode_max_calls_per_cell", 3)


def test_allow_list():
    assert kernel_callable("read_file") and kernel_callable("web_search")
    for name in ("python", "bash", "bash_poll", "data_agent", "update_plan", "render_ui", "spawn_agent"):
        assert not kernel_callable(name)
    assert kernel_callable(_FakeMcp("srv__lookup", True))
    assert not kernel_callable(_FakeMcp("srv__write", False))
    assert not kernel_callable(_FakeMcp("bash", True))
    assert not kernel_callable("srv__lookup")  # a bare MCP name carries no read-only signal


async def _raw_request(path, payload):
    reader, writer = await asyncio.open_unix_connection(path)
    data = json.dumps(payload).encode()
    writer.write(struct.pack(">I", len(data)) + data)
    await writer.drain()
    (size,) = struct.unpack(">I", await reader.readexactly(4))
    reply = json.loads(await reader.readexactly(size))
    writer.close()
    return reply


@pytest.mark.asyncio
async def test_server_checks_token_cell_and_limits(tmp_path):
    import tempfile
    d = tempfile.mkdtemp(prefix="mo-t")  # short path: AF_UNIX limit
    server = KernelToolServer(os.path.join(d, "tools.sock"))
    await server.start()
    try:
        assert oct(os.stat(server.socket_path).st_mode & 0o777) == "0o600"
        seen = []

        async def dispatch(name, args, parent_call_id, *, call_id):
            seen.append((name, args, parent_call_id, call_id))
            return ToolResult(ok=True, output=f"read {args['path']}")

        ok = {"token": server.token, "op": "call", "name": "read_file", "args": {"path": "a"}}
        reply = await _raw_request(server.socket_path, ok)
        assert reply["code"] == "CODE_MODE_NO_CELL"
        bad = await _raw_request(server.socket_path, {**ok, "token": "x" * 64})
        assert bad["code"] == "CODE_MODE_UNAUTHORIZED"

        ctx, _ = make_ctx(Workspace("c1", tmp_path / "ws"))
        binding = KernelToolBinding(ctx=ctx, call_id="call_py", dispatch=dispatch,
                                    registry=ToolRegistry([_FakeRead(), _FakeBash()]), max_calls=2)
        server.activate(binding)
        assert (await _raw_request(server.socket_path, {**ok, "token": "nope"}))["code"] == "CODE_MODE_UNAUTHORIZED"
        assert (await _raw_request(server.socket_path, ok)) == {"ok": True, "output": "read a", "error": None, "code": None}
        denied = await _raw_request(server.socket_path, {**ok, "name": "bash", "args": {"command": "ls"}})
        assert denied["code"] == "TOOL_NOT_KERNEL_CALLABLE"
        assert (await _raw_request(server.socket_path, ok))["ok"]
        assert (await _raw_request(server.socket_path, ok))["code"] == "CODE_MODE_CALL_LIMIT"
        assert [s[3] for s in seen] == ["kernel:call_py:1", "kernel:call_py:2"]
        assert all(s[2] == "call_py" for s in seen)
        listed = await _raw_request(server.socket_path, {"token": server.token, "op": "list"})
        assert [t["name"] for t in listed["output"]] == ["read_file"]
        server.deactivate(binding)
        assert (await _raw_request(server.socket_path, ok))["code"] == "CODE_MODE_NO_CELL"
    finally:
        await server.close()
        os.rmdir(d)


@pytest.mark.asyncio
async def test_binding_single_flight_and_three_arg_dispatch(tmp_path):
    gate = asyncio.Event()

    async def dispatch(name, args, parent_call_id):  # no call_id keyword: still supported
        await gate.wait()
        return ToolResult(ok=False, output="partial", error="boom", meta={"code": "X"})

    ctx, _ = make_ctx(Workspace("c1", tmp_path / "ws"))
    binding = KernelToolBinding(ctx=ctx, call_id="p", dispatch=dispatch)
    first = asyncio.create_task(binding.call("read_file", {"path": "a"}))
    await asyncio.sleep(0.05)
    assert (await binding.call("read_file", {"path": "b"}))["code"] == "CODE_MODE_BUSY"
    gate.set()
    assert await first == {"ok": False, "output": "partial", "error": "boom", "code": "X"}


@pytest.mark.asyncio
async def test_kernel_calls_tools(tmp_path, code_mode):
    ws = Workspace("c1", tmp_path / "ws")
    kernels = KernelPool(lambda cid: ws)
    calls = []

    async def dispatch(name, args, parent_call_id, *, call_id):
        calls.append((name, args, parent_call_id, call_id))
        if args.get("path") == "missing.csv":
            return ToolResult(ok=False, error="File not found: missing.csv", meta={"code": "NOT_FOUND"})
        return ToolResult(ok=True, output=f"contents of {args['path']}")

    registry = ToolRegistry([_FakeRead(), _FakeBash()])
    ctx, _ = make_ctx(ws, kernels, tool_dispatch=dispatch, tool_registry=registry)
    tool = PythonTool()
    try:
        r = await tool(ctx, {"code": "import os\nprint(tools.read_file(path='a.csv'))\n"
                                     "print('MATOPT_TOOLS_TOKEN' in os.environ, 'MATOPT_TOOLS_SOCK' in os.environ)"})
        assert r.ok, r.output
        assert "contents of a.csv" in r.output and "False False" in r.output
        assert calls == [("read_file", {"path": "a.csv"}, "call_py", "kernel:call_py:1")]

        r = await tool(ctx, {"code": "try:\n    tools.read_file(path='missing.csv')\n"
                                     "except ToolError as e:\n    print('ERR', e.code, e)"})
        assert "ERR NOT_FOUND File not found: missing.csv" in r.output

        r = await tool(ctx, {"code": "try:\n    tools.bash(command='ls')\nexcept ToolError as e:\n    print(e.code)"})
        assert "TOOL_NOT_KERNEL_CALLABLE" in r.output
        assert len(calls) == 2  # the denied call never reached the dispatcher

        r = await tool(ctx, {"code": "print([t['name'] for t in tools.list()]); "
                                     "print(tools.describe('read_file')['parameters']['required'])"})
        assert "['read_file']" in r.output and "['path']" in r.output

        # Cap: three calls per cell (fixture), the fourth is refused.
        r = await tool(ctx, {"code": "out = []\nfor i in range(4):\n    try:\n        tools.read_file(path=f'f{i}')\n"
                                     "        out.append('ok')\n    except ToolError as e:\n        out.append(e.code)\nprint(out)"})
        assert "['ok', 'ok', 'ok', 'CODE_MODE_CALL_LIMIT']" in r.output
        assert calls[-1][3] == "kernel:call_py:3"  # numbering restarts per cell (new binding)

        # Outside a cell: the kernel's server refuses even a holder of the token.
        server = kernels.get("c1")._tool_server
        reply = await _raw_request(server.socket_path, {"token": server.token, "op": "call",
                                                        "name": "read_file", "args": {"path": "a"}})
        assert reply["code"] == "CODE_MODE_NO_CELL"

        # Without a dispatcher, code mode reports itself unavailable.
        ctx2, _ = make_ctx(ws, kernels)
        r = await tool(ctx2, {"code": "try:\n    tools.read_file(path='a')\nexcept ToolError as e:\n    print(e.code)"})
        assert "CODE_MODE_UNAVAILABLE" in r.output
    finally:
        await kernels.shutdown_all()


@pytest.mark.asyncio
async def test_cell_timeout_cancels_nested_call(tmp_path, code_mode):
    ws = Workspace("c1", tmp_path / "ws")
    kernels = KernelPool(lambda cid: ws)
    cancelled = asyncio.Event()

    async def dispatch(name, args, parent_call_id, *, call_id):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    ctx, _ = make_ctx(ws, kernels, tool_dispatch=dispatch, tool_registry=ToolRegistry([_FakeRead()]))
    try:
        r = await PythonTool()(ctx, {"code": "tools.read_file(path='slow')", "timeout": 3})
        assert not r.ok
        assert cancelled.is_set()
        r = await PythonTool()(ctx, {"code": "print('alive')"})
        assert r.ok and "alive" in r.output
    finally:
        await kernels.shutdown_all()


@pytest.mark.skipif(sys.platform != "darwin" or not os.path.exists(SEATBELT), reason="needs macOS seatbelt")
@pytest.mark.asyncio
async def test_tools_reachable_under_seatbelt_without_network(tmp_path, code_mode, monkeypatch):
    monkeypatch.setattr(settings, "sandbox_mode", "workspace-write")
    monkeypatch.setattr(settings, "sandbox_network", False)
    monkeypatch.setattr(settings, "sandbox_backend", "seatbelt")
    ws = Workspace("c1", tmp_path / "ws")
    kernels = KernelPool(lambda cid: ws)

    async def dispatch(name, args, parent_call_id, *, call_id):
        return ToolResult(ok=True, output="sandboxed ok")

    ctx, _ = make_ctx(ws, kernels, tool_dispatch=dispatch, tool_registry=ToolRegistry([_FakeRead()]))
    try:
        r = await PythonTool()(ctx, {"code": "import os, socket\nprint(os.environ.get('MATOPT_SANDBOX'))\n"
                                             "print(tools.read_file(path='x'))\n"
                                             "s = socket.socket()\ntry:\n    s.connect(('1.1.1.1', 80)); print('NET')\n"
                                             "except OSError:\n    print('no network')"})
        assert r.ok, r.output
        assert "sandboxed ok" in r.output and "no network" in r.output
        assert "seatbelt" in r.output
        server = kernels.get("c1")._tool_server
        assert server is not None
    finally:
        await kernels.shutdown_all()


def test_seatbelt_profile_names_tools_socket():
    from pathlib import Path

    from app.agent.sandbox import SandboxPolicy, seatbelt_profile

    sock = Path("/private/tmp/mo-kx/tools.sock")
    profile = seatbelt_profile(SandboxPolicy(mode="workspace-write", backend="seatbelt", network=False,
                                             unix_sockets=(sock,)))
    assert f'(allow network-outbound (remote unix-socket (path-literal "{sock}")))' in profile
