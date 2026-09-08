from pathlib import Path
import asyncio

import pytest

from app.agent.events import Event
from app.agent.skills.loader import SkillRegistry, load_skill_dir, parse_skill_md
from app.agent.skills.materialize import materialize_skill
from app.agent.skills.router import SkillRouter
from app.agent.tools.base import ToolContext
from app.agent.tools.router import ToolRouter
from app.agent.tools.skill_tools import LoadSkillTool, SearchSkillsTool
from app.agent.workspace import Workspace
from app.config import REPO_ROOT
from app.runtime import build_tools

ANTHROPIC = REPO_ROOT / "skills" / "anthropic"
MATOPT = REPO_ROOT / "skills" / "matopt"
FIXTURES = Path(__file__).parent / "fixtures" / "skills"


def _registry():
    return SkillRegistry.discover([MATOPT, ANTHROPIC])


def test_parse_frontmatter_multiline():
    meta, body = parse_skill_md("---\nname: n\ndescription: |\n  hello\n  world\n---\n# Hi\n")
    assert meta["name"] == "n"
    assert "hello" in meta["description"]
    assert body.startswith("# Hi")


def test_discover_anthropic_and_matopt():
    reg = _registry()
    names = set(reg.names())
    for expected in (
        "pdf", "docx", "pptx", "xlsx", "mcp-builder", "skill-creator",
        "algorithmic-art", "frontend-design", "webapp-testing", "claude-api",
        "sympy-compute", "statistical-inference", "numerical-optimization",
        "ml-experiment", "math-verification",
    ):
        assert expected in names, expected
    assert len(reg) >= 24
    pdf = reg.get("pdf")
    assert pdf.source == "anthropic" and "pdf" in pdf.description.lower()
    assert (pdf.path / "SKILL.md").is_file()


def test_router_xlsx_from_filename():
    plan = SkillRouter(_registry()).plan("please add a totals column", files=[{"path": "uploads/report.xlsx"}])
    assert plan.activate and plan.activate[0].name == "xlsx"


def test_router_docx_not_xlsx():
    plan = SkillRouter(_registry()).plan("Write a Word document memo for leadership", files=["memo.docx"])
    names = [s.name for s in plan.activate]
    assert "xlsx" not in names
    assert names and names[0] == "docx"


def test_router_sympy_integral():
    plan = SkillRouter(_registry()).plan("Compute the symbolic integral of sin(x)*exp(-x)")
    assert plan.activate and plan.activate[0].name == "sympy-compute"


def test_router_simple_arithmetic_does_not_force_a_skill():
    plan = SkillRouter(_registry()).plan("What is 6 times 7?")
    assert plan.activate == []


@pytest.mark.asyncio
async def test_search_and_load_skill(tmp_path):
    reg = _registry()
    ws = Workspace("t", tmp_path / "ws")
    events = []

    async def emit(e: Event):
        events.append(e)

    ctx = ToolContext(conversation_id="t", workspace=ws, emit=emit, cancel=asyncio.Event())
    r = await SearchSkillsTool(reg)(ctx, {"query": "powerpoint slides"})
    assert r.ok and "pptx" in r.output
    r = await LoadSkillTool(reg)(ctx, {"name": "pptx"})
    assert r.ok and "Loaded skill" in r.output
    assert (ws.root / "skills" / "pptx" / "SKILL.md").is_file()
    r = await LoadSkillTool(reg)(ctx, {"name": "no-such-skill"})
    assert not r.ok


def test_materialize_fixture(tmp_path):
    if not (FIXTURES / "demo-skill" / "SKILL.md").exists():
        pytest.skip("fixture missing")
    skill = load_skill_dir(FIXTURES / "demo-skill", source="test")
    ws = Workspace("c", tmp_path / "ws")
    dest = materialize_skill(skill, ws)
    assert (dest / "SKILL.md").read_text().startswith("---")


def test_tool_router_aliases():
    tools = build_tools(SkillRegistry())
    router = ToolRouter()
    t, err = router.resolve(tools, "read")
    assert err is None and t.name == "read_file"
    t, err = router.resolve(tools, "shell")
    assert t.name == "bash"
    t, err = router.resolve(tools, "nope")
    assert t is None and "Unknown tool" in err
    schemas = router.schemas(tools, "plot a figure")
    names = [s["function"]["name"] for s in schemas]
    assert "python" in names and "load_skill" in names
