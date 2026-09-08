import asyncio

import pytest

from app.agent.events import Event
from app.agent.kernel import KernelPool
from app.agent.tools.base import ToolContext
from app.agent.tools.file_tools import EditFileTool, GrepTool, ListFilesTool, ReadFileTool, WriteFileTool
from app.agent.tools.math_tools import CheckEquivalenceTool
from app.agent.tools.plan_tool import UpdatePlanTool
from app.agent.tools.python_tool import PythonTool
from app.agent.tools.shell_tool import ShellTool
from app.agent.workspace import Workspace, WorkspaceError


@pytest.fixture
def ws(tmp_path):
    return Workspace("conv1", tmp_path / "ws")


def make_ctx(ws, kernels=None):
    events = []

    async def emit(e: Event):
        events.append(e)

    ctx = ToolContext(conversation_id="conv1", workspace=ws, emit=emit, cancel=asyncio.Event(), call_id="c", kernels=kernels)
    return ctx, events


def test_workspace_path_safety(ws):
    assert ws.resolve("uploads/a.txt").parent == ws.root / "uploads"
    with pytest.raises(WorkspaceError):
        ws.resolve("../../etc/passwd")
    with pytest.raises(WorkspaceError):
        ws.resolve("/etc/passwd")
    p = ws.save_upload("../weird name?.csv", b"a,b\n1,2\n")
    assert p.parent == ws.root / "uploads" and ".." not in p.name


@pytest.mark.asyncio
async def test_file_tools(ws):
    ctx, _ = make_ctx(ws)
    r = await WriteFileTool()(ctx, {"path": "notes/a.md", "content": "hello\nworld\n"})
    assert r.ok and r.artifacts[0].path == "notes/a.md"
    r = await ReadFileTool()(ctx, {"path": "notes/a.md"})
    assert r.ok and "1| hello" in r.output
    r = await EditFileTool()(ctx, {"path": "notes/a.md", "old_string": "world", "new_string": "there"})
    assert r.ok
    r = await GrepTool()(ctx, {"pattern": "there"})
    assert r.ok and "notes/a.md:2" in r.output
    r = await ListFilesTool()(ctx, {"pattern": "*.md"})
    assert r.ok and "notes/a.md" in r.output
    r = await ReadFileTool()(ctx, {"path": "missing.txt"})
    assert not r.ok
    r = await ReadFileTool()(ctx, {"path": "../outside"})
    assert not r.ok and "outside" in r.error.lower()


@pytest.mark.asyncio
async def test_shell_tool(ws):
    ctx, events = make_ctx(ws)
    r = await ShellTool()(ctx, {"command": "echo hi && echo data > out.txt"})
    assert r.ok and "hi" in r.output and any(a.path == "out.txt" for a in r.artifacts)
    r = await ShellTool()(ctx, {"command": "sleep 5", "timeout": 1})
    assert not r.ok and "timed out" in r.error
    r = await ShellTool()(ctx, {"command": "sudo rm -rf /"})
    assert not r.ok and "refused" in r.error


@pytest.mark.asyncio
async def test_plan_and_equivalence(ws):
    ctx, events = make_ctx(ws)
    r = await UpdatePlanTool()(ctx, {"steps": [{"content": "a", "status": "in_progress"}, "b"]})
    assert r.ok and ctx.state["plan"][1]["status"] == "pending" and events[0].type == "plan"
    r = await CheckEquivalenceTool()(ctx, {"expression_a": "\\frac{1}{2}", "expression_b": "0.5"})
    assert r.ok and "EQUIVALENT" in r.output and "NOT" not in r.output
    r = await CheckEquivalenceTool()(ctx, {"expression_a": "x^2-1", "expression_b": "(x-1)(x+1)"})
    assert r.ok and r.output.startswith("EQUIVALENT")
    r = await CheckEquivalenceTool()(ctx, {"expression_a": "2", "expression_b": "3"})
    assert "NOT equivalent" in r.output


@pytest.mark.asyncio
async def test_python_tool_with_kernel(ws):
    kernels = KernelPool(lambda cid: ws)
    ctx, events = make_ctx(ws, kernels)
    try:
        tool = PythonTool()
        r = await tool(ctx, {"code": "import matplotlib.pyplot as plt\nplt.plot([1,2],[3,4]); plt.show()\nprint('done'); 2+2"})
        assert r.ok and "done" in r.output and "4" in r.output
        assert any(a.kind == "image" for a in r.artifacts)
        assert any(e.type == "tool_progress" for e in events)
        r = await tool(ctx, {"code": "undefined_name"})
        assert not r.ok and "NameError" in r.output
        r = await tool(ctx, {"code": "```python\nprint('fenced')\n```"})
        assert r.ok and "fenced" in r.output
    finally:
        await kernels.shutdown_all()
