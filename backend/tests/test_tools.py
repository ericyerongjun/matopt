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
from app.agent.kernel import ExecResult, KernelSession
from app.agent.workspace import Workspace, WorkspaceError, is_internal_output
from app.runtime import build_tools


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
        nb = (ws.root / "analysis.ipynb").read_text(encoding="utf-8")
        assert "fenced" in nb
        assert not any(a.path == "analysis.ipynb" for a in r.artifacts)
        assert "analysis.ipynb" not in r.output
    finally:
        await kernels.shutdown_all()


def test_internal_output_hides_sandbox_notebook():
    assert is_internal_output("analysis.ipynb")
    assert is_internal_output("./analysis.ipynb")
    assert is_internal_output(".ipynb_checkpoints/analysis.ipynb")
    assert not is_internal_output("notes/analysis.ipynb")
    assert not is_internal_output("figures/fig_001.png")


def test_collect_display_skips_identical_png_bytes(ws):
    session = KernelSession("conv1", ws)
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f"
        b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    import base64

    payload = {"image/png": base64.b64encode(png).decode("ascii")}
    first, second = ExecResult(), ExecResult()
    session._collect_display(payload, first)
    session._collect_display(payload, second)
    assert len(first.artifacts) == 1 and first.artifacts[0].kind == "image"
    assert first.artifacts[0].digest
    assert second.artifacts == []
    assert len(list((ws.root / "figures").glob("*.png"))) == 1


@pytest.mark.asyncio
async def test_python_tool_hides_notebook_from_changed_files(ws, monkeypatch):
    kernels = KernelPool(lambda cid: ws)
    ctx, _ = make_ctx(ws, kernels)

    async def fake_execute(self, code, timeout=120, on_stream=None, cancel=None):
        (ws.root / "analysis.ipynb").write_text("{}", encoding="utf-8")
        (ws.root / "notes.txt").write_text("ok", encoding="utf-8")
        return ExecResult(status="ok", stdout="ok\n")

    monkeypatch.setattr(KernelSession, "execute", fake_execute)
    r = await PythonTool()(ctx, {"code": "print(1)"})
    assert r.ok
    assert any(a.path == "notes.txt" for a in r.artifacts)
    assert not any(a.path == "analysis.ipynb" for a in r.artifacts)
    assert "analysis.ipynb" not in r.output
    await kernels.shutdown_all()


@pytest.mark.asyncio
async def test_python_writes_invalidate_prior_reads_even_on_cell_error(ws, monkeypatch):
    kernels = KernelPool(lambda cid: ws)
    ctx, _ = make_ctx(ws, kernels)
    (ws.root / "notes.txt").write_text("before", encoding="utf-8")
    observed = await ReadFileTool()(ctx, {"path": "notes.txt"})
    assert observed.ok

    async def fake_execute(self, code, timeout=120, on_stream=None, cancel=None):
        (ws.root / "notes.txt").write_text("after", encoding="utf-8")
        return ExecResult(status="error", error_name="ValueError", error_value="after writing")

    monkeypatch.setattr(KernelSession, "execute", fake_execute)
    result = await PythonTool()(ctx, {"code": "write_then_fail()"})
    assert not result.ok and result.meta["read_state_invalidated"] == ["notes.txt"]
    edit = await EditFileTool()(ctx, {"path": "notes.txt", "old_string": "after", "new_string": "new"})
    assert not edit.ok and edit.meta["code"] == "FS_NOT_OBSERVED"
    await kernels.shutdown_all()


@pytest.mark.asyncio
async def test_shell_write_invalidates_read_state(ws):
    ctx, _ = make_ctx(ws)
    (ws.root / "notes.txt").write_text("before", encoding="utf-8")
    await ReadFileTool()(ctx, {"path": "notes.txt"})
    result = await ShellTool()(ctx, {"command": "printf after > notes.txt"})
    assert result.ok and result.meta["read_state_invalidated"] == ["notes.txt"]
    edit = await EditFileTool()(ctx, {"path": "notes.txt", "old_string": "after", "new_string": "new"})
    assert not edit.ok and edit.meta["code"] == "FS_NOT_OBSERVED"


@pytest.mark.asyncio
async def test_shell_deleted_file_invalidates_read_state(ws):
    ctx, _ = make_ctx(ws)
    (ws.root / "notes.txt").write_text("before", encoding="utf-8")
    await ReadFileTool()(ctx, {"path": "notes.txt"})
    result = await ShellTool()(ctx, {"command": "rm notes.txt"})
    assert result.ok and result.meta["read_state_invalidated"] == ["notes.txt"]


@pytest.mark.asyncio
async def test_kernel_does_not_recapture_leftover_or_identical_figures(ws):
    kernels = KernelPool(lambda cid: ws)
    ctx, _ = make_ctx(ws, kernels)
    tool = PythonTool()
    plot = (
        "import matplotlib.pyplot as plt\n"
        "plt.plot([1, 2], [3, 4])\n"
        "plt.title('same')\n"
        "plt.show()\n"
    )
    try:
        first = await tool(ctx, {"code": plot})
        assert first.ok
        images = [a for a in first.artifacts if a.kind == "image"]
        assert len(images) == 1
        pngs = list((ws.root / "figures").glob("*.png"))
        assert len(pngs) == 1

        leftover = await tool(ctx, {"code": "print('no plot')"})
        assert leftover.ok
        assert not any(a.kind == "image" for a in leftover.artifacts)
        assert len(list((ws.root / "figures").glob("*.png"))) == 1
        assert "analysis.ipynb" not in leftover.output

        again = await tool(ctx, {"code": plot})
        assert again.ok
        assert not any(a.kind == "image" for a in again.artifacts)
        assert len(list((ws.root / "figures").glob("*.png"))) == 1
    finally:
        await kernels.shutdown_all()


# ── plan §3.8a: the declared safety surface ──────────────────────────────


def test_every_registered_tool_declares_its_safety_surface():
    """The scheduler (phase 7b) and the read-only classifier read these."""
    tools = {t.name: t for t in build_tools()}
    assert set(tools) >= {
        "python", "bash", "read_file", "write_file", "edit_file", "list_files",
        "grep", "parse_document", "check_equivalence", "update_plan",
        "search_skills", "load_skill",
    }
    for name, tool in tools.items():
        assert tool.side_effect_scope in ("none", "workspace", "system"), name
        assert tool.risk_level in ("low", "medium", "high"), name
        for flag in ("read_only", "destructive", "concurrent_safe", "needs_approval",
                     "provider_visible", "stop_turn_on_success"):
            assert isinstance(getattr(tool, flag), bool), f"{name}.{flag}"
        assert tool.timeout_ms is None or (isinstance(tool.timeout_ms, int) and tool.timeout_ms > 0), name
        assert tool.max_output_bytes is None or (isinstance(tool.max_output_bytes, int) and tool.max_output_bytes > 0), name
        if tool.read_only:
            # A read-only tool is fully benign: nothing durable changes, so no
            # approval and no reason to keep it out of a parallel batch.
            assert tool.side_effect_scope == "none", name
            assert not tool.destructive and not tool.needs_approval, name
            assert tool.concurrent_safe, name


def test_tool_safety_surface_matches_behaviour():
    tools = {t.name: t for t in build_tools()}
    # The kernel and the shell are not sandboxed to the workspace (cwd only),
    # so both can reach the host: scope `system`, not `workspace`.
    for name in ("python", "bash"):
        assert tools[name].side_effect_scope == "system", name
        assert tools[name].risk_level == "high", name
        assert not tools[name].concurrent_safe, name
        assert tools[name].needs_approval, name
    # It writes parsed/<stem>.md and OCR images, so it must not claim read-only.
    assert tools["parse_document"].read_only is False
    assert tools["update_plan"].side_effect_scope == "none"       # agent state only
    assert tools["load_skill"].side_effect_scope == "workspace"   # materialises skills/<name>/
    assert tools["web_search"].requires_flags == ("web_search",)  # the Web Search gate


def test_python_tool_normalizes_fenced_code(ws):
    """The fenced form is what the model typed; the unfenced form is what runs."""
    tool = PythonTool()
    plain = {"code": "print(1)"}
    assert tool.resolve_input(plain) is plain
    assert tool.resolve_input({"code": "\n```python\n  x = 1\n  print(x)\n```\n", "timeout": 30}) == {
        "code": "  x = 1\n  print(x)",
        "timeout": 30,
    }
