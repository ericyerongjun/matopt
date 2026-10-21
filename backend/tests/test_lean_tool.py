"""lean_check accepts only complete proofs and rejects proof shortcuts."""
import asyncio
import shutil
from types import SimpleNamespace

import pytest

from app.agent.tools.lean_tool import LeanCheckTool, find_lean
from app.agent.workspace import Workspace


def ctx(tmp_path):
    return SimpleNamespace(workspace=Workspace("c_lean", tmp_path), cancel=asyncio.Event())


@pytest.mark.asyncio
async def test_shortcuts_fail_even_when_the_compiler_succeeds(tmp_path):
    tool = LeanCheckTool([shutil.which("true")], None, 10)
    source = "-- sorry in a comment is fine\ntheorem t : 1 = 1 := by\n  sorry\naxiom cheat : False\n"
    result = await tool.run(ctx(tmp_path), source=source, name="cheat")
    assert not result.ok and result.meta["shortcuts"] == ["axiom", "sorry"]
    assert (tmp_path / "c_lean" / "proofs" / "cheat.lean").read_text() == source


@pytest.mark.asyncio
async def test_comment_mentions_do_not_count_as_shortcuts(tmp_path):
    tool = LeanCheckTool([shutil.which("true")], None, 10)
    result = await tool.run(ctx(tmp_path), source="/- no sorry here -/\ntheorem t : 1 = 1 := rfl -- admit nothing\n")
    assert result.ok and result.meta["verified"]


@pytest.mark.asyncio
@pytest.mark.skipif(find_lean() is None, reason="no Lean toolchain installed")
async def test_real_lean_accepts_a_proof_and_rejects_a_false_claim(tmp_path):
    tool = LeanCheckTool.detect()
    good = await tool.run(ctx(tmp_path), source="theorem add_comm' (a b : Nat) : a + b = b + a := Nat.add_comm a b\n",
                          name="good")
    assert good.ok, good.error
    bad = await tool.run(ctx(tmp_path), source="theorem wrong : 2 + 2 = 5 := rfl\n", name="bad")
    assert not bad.ok and bad.meta["exit_code"] != 0
