"""Command hooks (ported from Codex) and AGENTS.md instruction files."""

import asyncio
import json
import shlex
import sys
from types import SimpleNamespace

import pytest

from app.agent.execution import ToolExecutionService
from app.agent.events import ToolResult
from app.agent.hooks import HookEngine, parse_hooks_config
from app.agent.instructions import operator_instructions, workspace_instructions
from app.agent.tools.base import Tool, ToolContext
from app.agent.workspace import Workspace


def script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text("import json, sys\nevent = json.load(sys.stdin)\n" + body)
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(path))}"


def engine(config):
    return HookEngine(parse_hooks_config({"hooks": config}), default_timeout=10)


class Echo(Tool):
    name = "echo"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

    def __init__(self):
        self.seen = []

    async def run(self, ctx, text, **_):
        self.seen.append(text)
        return ToolResult(ok=True, output=f"echo: {text}")


@pytest.fixture
def ctx(tmp_path):
    async def emit(_event):
        pass
    return ToolContext("c", Workspace("c", tmp_path / "ws"), emit, asyncio.Event(), call_id="call")


def service(hooks):
    return ToolExecutionService(SimpleNamespace(tool_timeout=5, tool_max_timeout=10, align_monitor=False), hooks=hooks)


def test_config_validation_rejects_unknown_events():
    with pytest.raises(ValueError):
        parse_hooks_config({"hooks": {"OnWhatever": []}})
    with pytest.raises(ValueError):
        parse_hooks_config({"hooks": {"PreToolUse": [{"hooks": [{"type": "prompt"}]}]}})


@pytest.mark.asyncio
async def test_pre_tool_use_exit_2_blocks(tmp_path, ctx):
    cmd = script(tmp_path, "deny.py", "sys.stderr.write('no echo for ' + event['tool_name']); sys.exit(2)\n")
    tool = Echo()
    result = await service(engine({"PreToolUse": [{"matcher": "echo", "hooks": [{"command": cmd}]}]})).dispatch(
        tool, ctx, {"text": "hi"})
    assert not result.ok and result.meta["code"] == "TOOL_HOOK_BLOCKED"
    assert "no echo for echo" in result.error and tool.seen == []


@pytest.mark.asyncio
async def test_pre_tool_use_matcher_skips_other_tools(tmp_path, ctx):
    cmd = script(tmp_path, "deny.py", "sys.exit(2)\n")
    tool = Echo()
    result = await service(engine({"PreToolUse": [{"matcher": "bash|python", "hooks": [{"command": cmd}]}]})).dispatch(
        tool, ctx, {"text": "hi"})
    assert result.ok and tool.seen == ["hi"]


@pytest.mark.asyncio
async def test_pre_tool_use_rewrites_input_and_records_it(tmp_path, ctx):
    cmd = script(tmp_path, "rewrite.py", "print(json.dumps({'hookSpecificOutput': {'updatedInput': "
                                         "{'text': event['tool_input']['text'].upper()}}}))\n")
    tool = Echo()
    result = await service(engine({"PreToolUse": [{"hooks": [{"command": cmd}]}]})).dispatch(tool, ctx, {"text": "hi"})
    assert tool.seen == ["HI"] and result.meta["hook_updated_input"] == {"text": "HI"}


@pytest.mark.asyncio
async def test_pre_tool_use_invalid_rewrite_is_refused(tmp_path, ctx):
    cmd = script(tmp_path, "bad.py", "print(json.dumps({'hookSpecificOutput': {'updatedInput': {'text': 3}}}))\n")
    tool = Echo()
    result = await service(engine({"PreToolUse": [{"hooks": [{"command": cmd}]}]})).dispatch(tool, ctx, {"text": "hi"})
    assert result.meta["code"] == "TOOL_ARGUMENTS_INVALID" and tool.seen == []


@pytest.mark.asyncio
async def test_post_tool_use_feeds_back_to_the_model(tmp_path, ctx):
    cmd = script(tmp_path, "post.py", "print(json.dumps({'decision': 'block', 'reason': 'cite the source of '"
                                      " + event['tool_response']['output']}))\n")
    result = await service(engine({"PostToolUse": [{"hooks": [{"command": cmd}]}]})).dispatch(Echo(), ctx, {"text": "x"})
    assert result.ok and "[PostToolUse hook] cite the source of echo: x" in result.output


@pytest.mark.asyncio
async def test_failing_and_slow_hooks_do_not_block(tmp_path):
    crash = script(tmp_path, "crash.py", "sys.exit(1)\n")
    slow = script(tmp_path, "slow.py", "import time; time.sleep(5)\n")
    hooks = HookEngine(parse_hooks_config({"hooks": {"UserPromptSubmit": [
        {"hooks": [{"command": crash}, {"command": slow, "timeout": 0.5}]}]}}))
    outcome = await hooks.run("UserPromptSubmit", {"prompt": "x"})
    assert not outcome.blocked and outcome.additional_context == ""


@pytest.mark.asyncio
async def test_plain_stdout_is_context_for_prompt_events(tmp_path):
    cmd = script(tmp_path, "ctx.py", "print('Use SI units.')\n")
    outcome = await engine({"UserPromptSubmit": [{"hooks": [{"command": cmd}]}]}).run("UserPromptSubmit", {"prompt": "x"})
    assert outcome.additional_context == "Use SI units."


def test_hooks_off_unless_enabled(tmp_path):
    (tmp_path / "hooks.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"command": "true"}]}]}}))
    assert not HookEngine.from_settings(SimpleNamespace(hooks_enabled=False, data_dir=tmp_path)).has("Stop")
    assert HookEngine.from_settings(SimpleNamespace(hooks_enabled=True, hooks_path=None, data_dir=tmp_path,
                                                    hook_timeout=5)).has("Stop")
    (tmp_path / "hooks.json").write_text("{not json")
    assert not HookEngine.from_settings(SimpleNamespace(hooks_enabled=True, hooks_path=None, data_dir=tmp_path,
                                                        hook_timeout=5)).has("Stop")


def test_agents_md_scopes_override_and_cap(tmp_path, monkeypatch):
    home, data, ws = tmp_path / "home", tmp_path / "data", tmp_path / "ws"
    (home / ".matopt").mkdir(parents=True)
    data.mkdir()
    ws.mkdir()
    monkeypatch.setenv("HOME", str(home))
    (home / ".matopt" / "AGENTS.md").write_text("Prefer exact arithmetic.")
    (data / "AGENTS.md").write_text("ignored")
    (data / "AGENTS.override.md").write_text("Cite sources.")
    settings = SimpleNamespace(data_dir=data, agents_md_enabled=True, agents_md_max_bytes=1024)
    assert operator_instructions(settings) == "Prefer exact arithmetic.\n\nCite sources."
    (ws / "AGENTS.md").write_text("x" * 5000)
    capped = workspace_instructions(ws, settings)
    assert capped.endswith("[AGENTS.md truncated]") and len(capped) < 1100
    assert operator_instructions(SimpleNamespace(data_dir=data, agents_md_enabled=False)) == ""
