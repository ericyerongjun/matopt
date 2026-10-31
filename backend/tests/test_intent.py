"""Mat (chat and explain) or Opt (work on files): classification and enforcement."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.agent.execution import ToolExecutionService
from app.agent.intent import MAT_EXCLUDED, SwitchToOptTool, classify, decide, mode_prompt
from app.agent.tools.base import ToolContext
from app.agent.tools.shell_tool import ShellTool
from app.agent.workspace import Workspace


@pytest.mark.parametrize("text", [
    "What is the Fourier transform of a Gaussian?",
    "Explain why the eigenvalues of a symmetric matrix are real",
    "Plot sin(x) and cos(x) on [0, 2π] and explain where they intersect",
    "Prove that sqrt(2) is irrational.",
    "hi",
    "为什么矩阵的秩等于非零奇异值的个数？",
    "Summarize the attached paper",
])
def test_questions_and_explanations_are_mat(text):
    assert classify(text).mode == "mat", text


@pytest.mark.parametrize("text", [
    "Refactor my script so the plotting code is in its own function",
    "Rename all the csv files in the data folder to lowercase",
    "Fix the bug in analysis.py where the mean is computed on the wrong axis",
    "Clean up this folder and delete the temporary files",
    "Here is my code:\n```python\nx = 1/0\n```\nfix this error",
    "帮我重构这个项目的代码",
    "Update results.csv with the new column",
])
def test_requests_to_change_files_are_opt(text):
    assert classify(text).mode == "opt", text


def test_workspace_file_names_count_as_targets():
    decision = classify("make sales_q3 better", workspace_files=["data/sales_q3.xlsx"])
    assert decision.mode in ("opt", "mat")  # weak alone, but noted
    assert "names a file" in classify("tidy up sales_q3.xlsx please", workspace_files=["sales_q3.xlsx"]).reason


def test_ambiguous_follow_up_keeps_previous_mode():
    assert classify("and the other one too", previous="opt").mode == "opt"
    assert classify("and the other one too").mode == "mat"


def test_user_choice_wins():
    assert decide("opt", "What is 2+2?").source == "user"
    assert decide("opt", "What is 2+2?").mode == "opt"
    assert decide("mat", "refactor my code").mode == "mat"
    assert decide("auto", "refactor my code").source == "auto"


def test_mode_prompts():
    mat = decide("auto", "Explain entropy")
    assert "switch_to_opt" in mode_prompt(mat, can_switch=True)
    assert "suggest switching to Opt" in mode_prompt(decide("mat", "x"), can_switch=False)
    assert "Opt" in mode_prompt(decide("opt", "x"), can_switch=False)


def _ctx(tmp_path, **state):
    async def emit(_):
        pass
    return ToolContext("c", Workspace("c", tmp_path), emit, asyncio.Event(), call_id="k", state=dict(state))


@pytest.mark.asyncio
async def test_mat_refuses_file_and_shell_tools_at_the_boundary(tmp_path):
    service = ToolExecutionService(SimpleNamespace(tool_timeout=5, tool_max_timeout=10, align_monitor=False))
    ctx = _ctx(tmp_path, mode="mat", mode_excluded=MAT_EXCLUDED, mode_can_switch=True)
    result = await service.dispatch(ShellTool(), ctx, {"command": "echo hi"})
    assert result.meta["code"] == "TOOL_NOT_IN_MODE" and "switch_to_opt" in result.error


@pytest.mark.asyncio
async def test_switch_to_opt_unlocks_tools_once(tmp_path):
    switched = []
    async def on_switch(reason):
        switched.append(reason)
    ctx = _ctx(tmp_path, mode="mat", mode_excluded=MAT_EXCLUDED, mode_can_switch=True, mode_switched=on_switch)
    tool = SwitchToOptTool()
    assert tool.is_provider_visible(ctx)
    result = await tool(ctx, {"reason": "needs to edit data.csv"})
    assert result.ok and result.meta["refresh_tools"]
    assert ctx.state["mode"] == "opt" and not ctx.state["mode_excluded"] and switched == ["needs to edit data.csv"]
    assert not tool.is_provider_visible(ctx)
    again = await tool(ctx, {"reason": "x"})
    assert not again.ok


def test_switched_mode_survives_in_public_content():
    from app.agent.intent import IntentDecision
    from app.agent.presentation import public_content
    decision = IntentDecision("opt", "auto", "needs to edit data.csv", switched_from="mat")
    saved = public_content({"final_text": "done", "mode": decision.public()})
    assert saved["mode"] == {"mode": "opt", "source": "auto", "reason": "needs to edit data.csv",
                             "switched": True, "from": "mat"}
