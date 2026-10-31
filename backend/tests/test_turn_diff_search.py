"""Turn diff tracking, tolerant edit matching and fuzzy file search (Codex ports)."""

import asyncio

import pytest

from app.agent.text_match import find_unique
from app.agent.tools.base import ToolContext
from app.agent.tools.file_tools import EditFileTool, FindFilesTool, ReadFileTool, WriteFileTool, fuzzy_score
from app.agent.tools.read_state import invalidate_execution_changes
from app.agent.turn_diff import TurnDiffTracker
from app.agent.workspace import Workspace


@pytest.fixture
def ctx(tmp_path):
    async def emit(_):
        pass
    return ToolContext("c", Workspace("c", tmp_path), emit, asyncio.Event(), call_id="x")


def test_find_unique_passes():
    text = "def f(x):\n    return x  \n\nprint(“done”)\n"
    span, name, hits = find_unique(text, "def f(x):\n    return x")
    assert hits == 1 and name == "trailing whitespace" and text[span[0]:span[1]] == "def f(x):\n    return x  "
    span, name, _ = find_unique(text, "  return x")
    assert name == "surrounding whitespace"
    span, name, _ = find_unique(text, 'print("done")')
    assert name == "whitespace and Unicode punctuation" and text[span[0]:span[1]] == "print(“done”)"
    assert find_unique("a \na\n", "a ")[2] == 2


@pytest.mark.asyncio
async def test_edit_file_tolerates_copied_whitespace_and_tracks_diff(ctx):
    tracker = TurnDiffTracker(ctx.workspace)
    ctx.state["turn_diff"] = tracker
    await WriteFileTool()(ctx, {"path": "notes.md", "content": "Result:  \nx = 1\n"})
    await ReadFileTool()(ctx, {"path": "notes.md"})
    result = await EditFileTool()(ctx, {"path": "notes.md", "old_string": "Result:\nx = 1", "new_string": "Result:\nx = 2"})
    assert result.ok and "matched ignoring trailing whitespace" in result.output
    assert (ctx.workspace.root / "notes.md").read_text() == "Result:\nx = 2\n"
    rendered = tracker.render()
    assert rendered["files"] == [{"path": "notes.md", "status": "A", "added": 2, "removed": 0}]
    assert "+x = 2" in rendered["diff"]


@pytest.mark.asyncio
async def test_edit_file_still_refuses_ambiguous_tolerant_match(ctx):
    (ctx.workspace.root / "a.txt").write_text("x = 1 \nx = 1\n")
    await ReadFileTool()(ctx, {"path": "a.txt"})
    result = await EditFileTool()(ctx, {"path": "a.txt", "old_string": "x = 1  ", "new_string": "y"})
    assert not result.ok and "matches 2 places" in result.error


def test_tracker_covers_exec_changes_and_deletions(tmp_path):
    ws = Workspace("c", tmp_path)
    (ws.root / "keep.csv").write_text("a,b\n1,2\n")
    (ws.root / "gone.txt").write_text("bye\n")
    (ws.root / "big.bin").write_bytes(b"\0" * 10)
    tracker = TurnDiffTracker(ws)
    tracker.prime()
    state = {"turn_diff": tracker}
    before = ws.snapshot()
    (ws.root / "keep.csv").write_text("a,b\n1,3\n")
    (ws.root / "gone.txt").unlink()
    (ws.root / "new.txt").write_text("hello\n")
    (ws.root / "big.bin").write_bytes(b"\0" * 20)
    invalidate_execution_changes(state, ws, before, ws.changed_since(before))
    files = {f["path"]: f for f in tracker.render()["files"]}
    assert files["keep.csv"]["status"] == "M" and files["keep.csv"]["added"] == 1
    assert files["gone.txt"]["status"] == "D"
    assert files["new.txt"]["status"] == "A"
    assert files["big.bin"] == {"path": "big.bin", "status": "M", "untracked": True}


def test_unchanged_turn_renders_nothing(tmp_path):
    ws = Workspace("c", tmp_path)
    tracker = TurnDiffTracker(ws)
    tracker.before_write("x.txt", b"same\n")
    (ws.root / "x.txt").write_text("same\n")
    assert tracker.render()["files"] == []


def test_fuzzy_score_prefers_segment_starts():
    assert fuzzy_score("frev", "figures/revenue.png") is not None
    assert fuzzy_score("zzz", "figures/revenue.png") is None
    good = fuzzy_score("rev", "figures/revenue.png")[0]
    worse = fuzzy_score("rev", "archive/prevalence.csv")[0]
    assert good > worse
    assert fuzzy_score("Rev", "figures/revenue.png") is None, "smart case"


@pytest.mark.asyncio
async def test_find_files_ranks_and_limits(ctx):
    for rel in ["figures/revenue_by_region.png", "data/raw_revenue.csv", "notes/review.md", "x/y/z.txt"]:
        path = ctx.workspace.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    result = await FindFilesTool()(ctx, {"query": "fig rev"})
    assert result.output.splitlines()[0] == "figures/revenue_by_region.png"
    result = await FindFilesTool()(ctx, {"query": "rev", "limit": 1})
    assert result.meta["matches"] == 3 and "… 2 more" in result.output
