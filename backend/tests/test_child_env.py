"""The child-process environment allowlist (Tier 1 of the refinement plan §3.9.3).

The kernel is never launched here: these tests monkeypatch ``os.environ`` and
assert on the environment dict each spawn site builds, plus one wiring test
that replaces ``AsyncKernelManager`` so ``KernelSession.start`` is stopped
before it can talk to a real kernel.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import jupyter_client
import pytest

from app.agent.child_env import (
    ALLOWED_ENV_NAMES,
    DEFAULT_MPLBACKEND,
    KERNEL_MPLBACKEND,
    SENSITIVE_ENV_PATTERN,
    assert_no_sensitive_env,
    build_child_env,
)
from app.agent.kernel import KernelSession, _child_env
from app.agent.tools.base import ToolContext
from app.agent.tools.shell_tool import ShellTool
from app.agent.workspace import Workspace
from app.config import settings

# Credentials-shaped names MatOpt's own environment is known to hold, plus the
# shapes the plan calls out (AWS_*, GITHUB_TOKEN, SSH_AUTH_SOCK) that belong to
# the user rather than to MatOpt.
SENSITIVE = {
    "DEEPSEEK_API_KEY": "sk-planted-deepseek",
    "OPENAI_API_KEY": "sk-planted-openai",
    "QWEN_API_KEY": "sk-planted-qwen",
    "TAVILY_API_KEY": "tvly-planted",
    "BRAVE_SEARCH_API_KEY": "bsa-planted",
    "WOLFRAM_APP_ID": "planted-app-id",
    "GITHUB_TOKEN": "ghp_planted",
    "AWS_SECRET_ACCESS_KEY": "planted-aws-secret",
    "AWS_PROFILE": "planted-profile",
    "DB_PASSWORD": "hunter2",
    "SSH_AUTH_SOCK": "/tmp/planted-agent.sock",
    "PYTHONPATH": "/planted/path",
    "MATOPT_LEGACY_TOKEN": "planted-namespaced-token",
}

BENIGN = {
    "LANG": "en_US.UTF-8",
    "TZ": "UTC",
    "PYTHONDONTWRITEBYTECODE": "1",
    "MATOPT_DATA_DIR": "/planted/matopt-data",
    "PYTHONHOME": "/planted/python-home",
    "MPLBACKEND": "TkAgg",
    "PATH": "/matopt/ambient/bin",
}


@pytest.fixture
def planted_env(monkeypatch):
    """A process environment holding credentials, as a real deployment's is."""
    for name, value in {**SENSITIVE, **BENIGN}.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def ws(tmp_path):
    return Workspace("conv1", tmp_path / "ws")


def make_ctx(ws) -> ToolContext:
    async def emit(_event):
        return None

    return ToolContext(conversation_id="conv1", workspace=ws, emit=emit, cancel=asyncio.Event(), call_id="c")


# ── the allowlist ───────────────────────────────────────────────────────


def test_allowlist_is_the_documented_set():
    assert ALLOWED_ENV_NAMES >= {
        "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM", "NO_COLOR",
        "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE", "MPLBACKEND",
    }
    # Nothing credential-shaped may be listed: the assertion at the end of
    # build_child_env would reject its own allowlist otherwise.
    assert not [name for name in ALLOWED_ENV_NAMES if SENSITIVE_ENV_PATTERN.search(name)]


def test_planted_credentials_do_not_reach_the_child(planted_env, tmp_path):
    env = build_child_env(workspace_root=tmp_path / "ws")
    for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "QWEN_API_KEY", "TAVILY_API_KEY",
                 "BRAVE_SEARCH_API_KEY", "WOLFRAM_APP_ID", "GITHUB_TOKEN",
                 "AWS_SECRET_ACCESS_KEY", "AWS_PROFILE", "DB_PASSWORD",
                 "SSH_AUTH_SOCK", "PYTHONPATH", "MATOPT_LEGACY_TOKEN"):
        assert name not in env, f"{name} leaked into the child environment"


def test_only_allowlisted_names_are_forwarded(planted_env, tmp_path):
    env = build_child_env(workspace_root=tmp_path / "ws")
    # Every surviving name is either on the allowlist or MatOpt's own namespace.
    assert all(name in ALLOWED_ENV_NAMES or name.startswith("MATOPT_") for name in env)
    assert not [name for name in env if SENSITIVE_ENV_PATTERN.search(name)]


def test_allowlisted_values_survive_and_matopt_namespace_is_forwarded(planted_env, tmp_path):
    env = build_child_env(workspace_root=tmp_path / "ws")
    assert env["LANG"] == "en_US.UTF-8"
    assert env["TZ"] == "UTC"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["MATOPT_DATA_DIR"] == "/planted/matopt-data"


def test_path_survives_with_the_venv_bin_dir_first(planted_env):
    env = build_child_env()
    bindir = Path(sys.executable).parent
    parts = env["PATH"].split(os.pathsep)
    assert Path(parts[0]) == bindir
    assert str(bindir) in env["PATH"]
    # The ambient entries are kept, not replaced.
    assert parts[1:] == ["/matopt/ambient/bin"]


def test_python_runtime_guards_are_forced(planted_env, tmp_path):
    env = build_child_env(workspace_root=tmp_path / "ws")
    assert env["PYTHONUNBUFFERED"] == "1"          # ambient may not switch streaming off
    assert "PYTHONHOME" not in env                 # breaks the venv interpreter's stdlib lookup
    assert env["MPLBACKEND"] == DEFAULT_MPLBACKEND  # a shell cannot open a GUI backend


def test_extra_wins_over_the_defaults(tmp_path):
    env = build_child_env(workspace_root=tmp_path / "ws", extra={"MPLBACKEND": KERNEL_MPLBACKEND})
    assert env["MPLBACKEND"] == KERNEL_MPLBACKEND


# ── HOME ────────────────────────────────────────────────────────────────


def test_home_points_at_the_workspace(tmp_path, planted_env):
    root = tmp_path / "ws"
    env = build_child_env(workspace_root=root)
    assert env["HOME"] == str(root)
    assert root.is_dir(), "the redirected HOME must exist, or pip/matplotlib misbehave"


def test_home_is_the_ambient_home_without_a_workspace(planted_env):
    env = build_child_env()
    assert env.get("HOME") == os.environ.get("HOME")


# ── the assertion ───────────────────────────────────────────────────────


def test_assert_no_sensitive_env_passes_on_a_clean_env():
    assert_no_sensitive_env({"PATH": "/usr/bin", "HOME": "/tmp/x", "LANG": "C"})
    assert_no_sensitive_env(build_child_env())


@pytest.mark.parametrize("name", ["DEEPSEEK_API_KEY", "MY_TOKEN", "MY_SECRET", "MY_PASSWORD", "some_key", "ApiKey"])
def test_assert_no_sensitive_env_raises_on_a_planted_name(name):
    with pytest.raises(RuntimeError, match="credential-shaped"):
        assert_no_sensitive_env({name: "value"})


def test_a_caller_cannot_smuggle_a_credential_through_extra():
    with pytest.raises(RuntimeError, match="credential-shaped"):
        build_child_env(extra={"HF_TOKEN": "planted"})


# ── spawn sites ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shell_tool_env_has_no_credentials(ws, planted_env, monkeypatch):
    """The env ``bash`` is exec'd with, captured instead of running a command."""
    captured: dict = {}

    class FakeStdout:
        async def readline(self):
            return b""

    class FakeProc:
        returncode = 0
        stdout = FakeStdout()

        async def wait(self):
            return 0

    async def fake_exec(*argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs.get("env")
        captured["cwd"] = kwargs.get("cwd")
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = await ShellTool()(make_ctx(ws), {"command": "echo hi"})
    assert result.ok, result.error

    env = captured["env"]
    assert captured["cwd"] == str(ws.root)
    assert "DEEPSEEK_API_KEY" not in env
    assert "MATOPT_LEGACY_TOKEN" not in env
    assert env["HOME"] == str(ws.root)
    assert env["MPLBACKEND"] == DEFAULT_MPLBACKEND
    assert env["PYTHONUNBUFFERED"] == "1"


def test_kernel_child_env_has_no_credentials(tmp_path, planted_env):
    root = tmp_path / "ws"
    env = _child_env(root)
    assert "DEEPSEEK_API_KEY" not in env
    assert "MATOPT_LEGACY_TOKEN" not in env
    assert env["HOME"] == str(root)
    assert env["MPLBACKEND"] == KERNEL_MPLBACKEND
    assert env["PYTHONUNBUFFERED"] == "1"
    assert Path(env["PATH"].split(os.pathsep)[0]) == Path(sys.executable).parent


@pytest.mark.asyncio
async def test_kernel_session_hands_that_env_to_the_kernel_manager(ws, planted_env, monkeypatch):
    """Wiring check: ``start`` passes the allowlisted dict to ``start_kernel``."""
    recorded: dict = {}

    class StopHere(Exception):
        pass

    class FakeKernelManager:
        def __init__(self, **kwargs):
            pass

        async def start_kernel(self, **kwargs):
            recorded.update(kwargs)
            raise StopHere  # before any real kernel process exists

    monkeypatch.setattr(jupyter_client, "AsyncKernelManager", FakeKernelManager)
    with pytest.raises(StopHere):
        await KernelSession("conv1", ws).start()

    env = recorded["env"]
    assert recorded["cwd"] == str(ws.root)
    assert "DEEPSEEK_API_KEY" not in env
    assert env["HOME"] == str(ws.root)
    assert env["MPLBACKEND"] == KERNEL_MPLBACKEND


# ── escape hatch ────────────────────────────────────────────────────────


def test_escape_hatch_restores_the_ambient_environment(planted_env, monkeypatch, tmp_path):
    """``CHILD_ENV_ALLOWLIST=false`` is exactly the old, leaking behaviour."""
    monkeypatch.setattr(settings, "child_env_allowlist", False)
    env = build_child_env(workspace_root=tmp_path / "ws")
    assert env["DEEPSEEK_API_KEY"] == "sk-planted-deepseek"
    assert env["GITHUB_TOKEN"] == "ghp_planted"
    # The venv PATH and streaming defaults still apply: the hatch only swaps
    # the base environment, it does not drop the behavioural guarantees.
    assert Path(env["PATH"].split(os.pathsep)[0]) == Path(sys.executable).parent
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["HOME"] == str(tmp_path / "ws")


# ── explicitly forwarded operator credentials ───────────────────────────
#
# `allow_sensitive_extra` exists for the MinerU subprocess, which needs
# HF_TOKEN to fetch gated model weights. These tests pin the narrowness of
# that escape: it forwards exactly what the caller named, and nothing else.


def test_allow_sensitive_extra_forwards_only_the_named_credential(planted_env):
    env = build_child_env(
        extra={"MINERU_MODEL_SOURCE": "huggingface", "HF_TOKEN": "hf-operator"},
        allow_sensitive_extra=True,
    )
    assert env["HF_TOKEN"] == "hf-operator"
    assert env["MINERU_MODEL_SOURCE"] == "huggingface"
    # Everything the caller did NOT name stays out, including other
    # credential-shaped names that were present in the ambient environment.
    assert "DEEPSEEK_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env


def test_allow_sensitive_extra_does_not_disable_the_ambient_allowlist(planted_env):
    """The flag permits a named extra; it must not re-open ambient inheritance."""
    env = build_child_env(extra={"HF_TOKEN": "hf-operator"}, allow_sensitive_extra=True)
    leaked = [k for k in env if SENSITIVE_ENV_PATTERN.search(k) and k != "HF_TOKEN"]
    assert leaked == []


def test_sensitive_extra_still_rejected_without_the_flag(planted_env):
    with pytest.raises(RuntimeError):
        build_child_env(extra={"HF_TOKEN": "smuggled"})


def test_mineru_keeps_the_real_home(planted_env, tmp_path):
    """MinerU must NOT get workspace_root: its model cache lives under HOME.

    Redirecting HOME would re-download multi-GB weights per conversation.
    """
    real_home = os.path.expanduser("~")
    env = build_child_env(
        extra={"MINERU_MODEL_SOURCE": "huggingface"},
        allow_sensitive_extra=True,
    )
    assert env.get("HOME", real_home) == real_home
