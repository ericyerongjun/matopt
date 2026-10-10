"""Actual harness policy comparison with deterministic provider and kernel state.

The Python tool, scheduler, execution boundary, run budget and session receipts
are production code. The provider is scripted and the kernel executes only the
fixed test programs in a persistent dict; this is not live-model benchmarking.
"""
import asyncio
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent.budget import BudgetExhausted, RunBudget
from app.agent.events import ToolResult
from app.agent.harness import AgentHarness, RunRequest, TurnState, _operation_for_call
from app.agent.kernel import ExecResult
from app.agent.loop_policy import Operation, Outcome
from app.agent.recovery_guard import RecoveryGuard
from app.agent.session import SessionLog
from app.agent.tools.base import Tool, ToolContext, ToolRegistry
from app.agent.tools.file_tools import ReadFileTool
from app.agent.tools.python_tool import PythonTool
from app.agent.tools.read_state import record_read, read_revision
from app.agent.workspace import Workspace
from app.config import Settings, settings
from app.llm.base import ToolCallSpec
from app.storage.db import Database
from tests.fake_provider import FakeProvider, FakeRegistry


BAD_CODE = "counter = globals().get('counter', 0) + 1\nprint(missing_value)"
CORRECTED_CODE = "missing_value = 42\nprint(missing_value)"


def record_experiment(name, measured, *, agent_max_steps, boundary):
    """Opt-in raw fixture records; ordinary tests never modify the worktree."""
    destination = os.environ.get("MATOPT_LOOP_POLICY_RESULTS_PATH")
    if not destination:
        return
    path = Path(destination)
    root = Path(__file__).resolve().parents[2]
    sources = ["backend/app/agent/harness.py", "backend/app/agent/execution.py", "backend/app/agent/loop_policy.py",
               "backend/app/agent/tools/python_tool.py", "backend/app/config.py", "backend/tests/test_loop_policy_harness.py"]
    document = json.loads(path.read_text()) if path.exists() else {"schema_version": 1, "cases": {}}
    document.update(evaluation="deterministic_actual_native_harness", generated_at=datetime.now(timezone.utc).isoformat(),
                    source_sha256={source: hashlib.sha256((root / source).read_bytes()).hexdigest() for source in sources})
    document["cases"][name] = {"boundary": boundary,
        "shared_settings": {"strict_final": True, "verify": False, "web_search": False, "agent_max_steps": agent_max_steps},
        "policies": measured}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")


class StatefulKernel:
    def __init__(self):
        self.namespace = {}
        self.executed = []

    async def execute(self, code, *, timeout, on_stream, cancel):
        self.executed.append(code)
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                exec(code, self.namespace)
        except Exception as exc:
            return ExecResult(status="error", stdout=output.getvalue(), error_name=type(exc).__name__,
                              error_value=str(exc), execution_count=len(self.executed))
        await on_stream("stdout", output.getvalue())
        return ExecResult(stdout=output.getvalue(), execution_count=len(self.executed))


class Kernels:
    def __init__(self):
        self.kernel = StatefulKernel()

    def get(self, _cid):
        return self.kernel

    def peek(self, _cid):
        return None


class CorrectionProvider(FakeProvider):
    """The final answer depends on the actual tool receipt, never the policy."""
    def __init__(self):
        super().__init__([])

    async def stream(self, messages, tools, options):
        receipts = [message for message in messages if message["role"] == "tool"]
        if len(receipts) < 3:
            # Explicit aliases and fenced source normalize to the same operation.
            name = ("python", "execute", "repl")[len(receipts)]
            code = BAD_CODE if len(receipts) != 1 else f"```python\n{BAD_CODE}\n```"
            step = {"tool": name, "args": {"code": code}}
        elif len(receipts) == 3:
            assert all("NameError" in receipt["content"] for receipt in receipts)
            step = {"tool": "python", "args": {"code": CORRECTED_CODE}}
        else:
            succeeded = receipts[-1]["content"].strip() == "42"
            step = {"text": "The computed result is 42." if succeeded else "The corrected computation was blocked; no computed result is available."}
        self.script.append(step)
        async for delta in super().stream(messages, tools, options):
            yield delta


async def paired_run(tmp_path, policy):
    config = settings.model_copy(update={"data_dir": tmp_path / policy, "loop_policy": policy,
        "agent_max_steps": 12, "agent_max_consecutive_failures": 4, "run_max_model_attempts": 20,
        "run_max_tool_starts": 20, "run_timeout_seconds": 30, "document_auto_parse": False})
    db, provider, kernels = Database(tmp_path / f"{policy}.sqlite"), CorrectionProvider(), Kernels()
    harness = AgentHarness(db, kernels, config, FakeRegistry(provider), ToolRegistry([PythonTool()]))
    try:
        events = [event.to_dict() async for event in harness.run(RunRequest(conversation_id=None,
            text="Compute the value using the Python tool and report only an observed result.",
            web_search=False, strict_final=True), internal_events=True)]
        await harness.wait_side_tasks()
        cid = events[0]["conversation_id"]
        results = [event for event in events if event["type"] == "tool_result"]
        final = next(event for event in events if event["type"] == "message_end")
        final_text = "".join(part.get("text", "") for part in final["content"]["parts"] if part.get("type") == "text")
        journal = db.list_session_events(cid)
        return {
            "model_requests": len(provider.calls), "executions": len(kernels.kernel.executed),
            "known_failures": sum(not result["ok"] and not result["meta"].get("blocked_repeat") for result in results),
            "blocked_calls": sum(bool(result["meta"].get("blocked_repeat")) for result in results),
            "oracle_pass": final_text == "The computed result is 42.",
            "counter": kernels.kernel.namespace.get("counter"), "final_status": final["content"]["status"],
            "durable_tool_calls": sum(event["type"] == "tool/call" for event in journal),
            "durable_tool_results": sum(event["type"] == "tool/result" for event in journal),
            "running_events": sum(event["type"] == "tool_running" for event in events),
        }
    finally:
        db.close()


@pytest.mark.asyncio
async def test_paired_harness_three_failures_then_corrected_python(tmp_path):
    results = {policy: await paired_run(tmp_path, policy) for policy in ("legacy", "operation")}
    for value in results.values():
        assert value["model_requests"] == 5
        assert value["known_failures"] == value["counter"] == 3
        assert value["durable_tool_calls"] == value["durable_tool_results"] == 4
        assert value["final_status"] == "complete"
    assert results["legacy"]["executions"] == results["legacy"]["running_events"] == 3
    assert results["legacy"]["blocked_calls"] == 1 and not results["legacy"]["oracle_pass"]
    assert results["operation"]["executions"] == results["operation"]["running_events"] == 4
    assert results["operation"]["blocked_calls"] == 0 and results["operation"]["oracle_pass"]
    record_experiment("stateful_python_correction", results, agent_max_steps=12,
        boundary="Actual harness, PythonTool, execution service and journal; offline stateful fake provider; fixed Python programs executed in one persistent in-process dict instead of Jupyter; final answer oracle depends on the actual tool receipt.")
    print("paired_harness=" + json.dumps(results, sort_keys=True))


class FailingTool(Tool):
    name = "python"
    parameters = {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}
    read_only = True
    concurrent_safe = True

    def __init__(self):
        self.calls = []

    async def run(self, ctx, code):
        self.calls.append(code)
        return ToolResult(ok=code == "corrected", output="42" if code == "corrected" else "", error=None if code == "corrected" else "ValueError: unchanged")


class ParallelFailureTool(FailingTool):
    def __init__(self):
        super().__init__()
        self.initial_started = 0
        self.release_initial = asyncio.Event()

    async def run(self, ctx, code):
        if code == "bad" and self.initial_started < 3:
            self.initial_started += 1
            if self.initial_started == 3:
                self.release_initial.set()
            await self.release_initial.wait()
        return await super().run(ctx, code)


class UnrelatedTool(Tool):
    name = "list_files"
    read_only = concurrent_safe = True

    async def run(self, ctx):
        return ToolResult(ok=True, output="An unrelated inspection completed.")


@pytest.mark.asyncio
async def test_paired_provider_parallel_alias_and_unrelated_tool_control(tmp_path):
    measured = {}
    for policy in ("legacy", "operation"):
        tool = ParallelFailureTool()

        class ParallelProvider(FakeProvider):
            async def stream(self, messages, tools, options):
                receipts = [message for message in messages if message["role"] == "tool"]
                if not receipts:
                    step = {"tools": [{"tool": name, "args": {"code": "bad"}} for name in ("python", "execute", "repl")]}
                elif len(receipts) == 3:
                    step = {"tools": [{"tool": "python", "args": {"code": "bad"}},
                                      {"tool": "execute", "args": {"code": "corrected"}},
                                      {"tool": "list_files", "args": {}}]}
                else:
                    step = {"text": "The result is 42; inspection completed." if receipts[-2]["content"] == "42"
                            else "The unchanged operation and corrected operation were blocked; inspection completed."}
                self.script.append(step)
                async for delta in super().stream(messages, tools, options):
                    yield delta

        provider, db = ParallelProvider([]), Database(tmp_path / f"parallel-{policy}.sqlite")
        config = settings.model_copy(update={"data_dir": tmp_path / policy, "loop_policy": policy,
            "agent_max_steps": 8, "document_auto_parse": False})
        harness = AgentHarness(db, Kernels(), config, FakeRegistry(provider), ToolRegistry([tool, UnrelatedTool()]))
        try:
            async def collect():
                return [event.to_dict() async for event in harness.run(RunRequest(conversation_id=None,
                    text="Compute a result and inspect files.", strict_final=True, web_search=False), internal_events=True)]
            events = await asyncio.wait_for(collect(), 5)
            await harness.wait_side_tasks()
            results = [event for event in events if event["type"] == "tool_result"]
            final = next(event for event in events if event["type"] == "message_end")
            answer = "".join(part.get("text", "") for part in final["content"]["parts"] if part.get("type") == "text")
            measured[policy] = {"model_requests": len(provider.calls), "executions": sum(event["type"] == "tool_running" for event in events),
                "blocks": sum(bool(result["meta"].get("blocked_repeat")) for result in results),
                "oracle_pass": answer == "The result is 42; inspection completed."}
            assert tool.initial_started == 3  # Gate requires three simultaneously admitted calls.
            assert len(results) == 6
            assert next(result for result in results if result["name"] == "list_files")["ok"]
        finally:
            db.close()
    assert measured == {
        "legacy": {"model_requests": 3, "executions": 4, "blocks": 2, "oracle_pass": False},
        "operation": {"model_requests": 3, "executions": 5, "blocks": 1, "oracle_pass": True},
    }
    record_experiment("parallel_alias_and_unrelated_control", measured, agent_max_steps=8,
        boundary="Actual harness, scheduler, execution service and journal; offline stateful fake provider; declared concurrent synthetic Python-named tool with async gate requiring all three initial calls to start, plus unrelated read-only tool; final answer oracle depends on corrected-call receipt.")
    print("paired_parallel_harness=" + json.dumps(measured, sort_keys=True))


@pytest.fixture
def runtime(tmp_path):
    db, events = Database(tmp_path / "loop.sqlite"), []
    tools = ToolRegistry([FailingTool(), ReadFileTool()])
    config = settings.model_copy(update={"data_dir": tmp_path, "loop_policy": "operation"})
    harness = AgentHarness(db, Kernels(), config, FakeRegistry(FakeProvider([])), tools)

    async def emit(event):
        events.append(event)

    ctx = ToolContext("loop", Workspace("loop", tmp_path / "workspace"), emit, asyncio.Event(),
                      state={"run_budget": RunBudget(tool_start_limit=40), "recovery_guard": RecoveryGuard()})
    state = TurnState(turn=1)
    yield harness, tools, ctx, state, events
    db.close()


async def dispatch(runtime, calls):
    harness, tools, ctx, state, _ = runtime
    state.step += 1
    specs = [ToolCallSpec(f"call-{index}", name, json.dumps(args)) for index, (name, args) in enumerate(calls)]
    return await harness._execute_calls(specs, tools, ctx, ctx.emit, state)


@pytest.mark.asyncio
async def test_alias_parallel_failures_block_only_matching_operation_and_publish_receipts(runtime):
    _, tools, ctx, state, events = runtime
    failures = await dispatch(runtime, [(name, {"code": "bad"}) for name in ("python", "execute", "repl")])
    assert len(failures) == 3 and all(not result.ok for result in failures)
    # All three were admitted before their completions. The following exact
    # operation is blocked, while corrected arguments in the same batch run.
    results = await dispatch(runtime, [("execute", {"code": "bad"}), ("python", {"code": "corrected"})])
    assert results[0].meta["blocked_repeat"] and results[1].ok
    assert tools.get("python").calls == ["bad", "bad", "bad", "corrected"]
    assert ctx.state["run_budget"].snapshot()["tool_starts"] == 4
    assert sum(event.type == "tool_result" for event in events) == 5
    assert sum(event.type == "tool_running" for event in events) == 4
    assert not state.blocked_tools and not state.failure_counts


@pytest.mark.asyncio
async def test_steer_generation_and_observed_file_revision_scope_exact_failures(runtime):
    _, tools, ctx, state, _ = runtime
    for _ in range(3):
        await dispatch(runtime, [("python", {"code": "bad"})])
    assert (await dispatch(runtime, [("python", {"code": "bad"})]))[0].meta["blocked_repeat"]
    state.applied_steers.append({"text": "Retry this calculation under the updated task."})
    assert not (await dispatch(runtime, [("python", {"code": "bad"})]))[0].meta.get("blocked_repeat")

    class FileFailure(FailingTool):
        name = "edit_file"
        read_only = False
        concurrent_safe = False
        parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}

        async def run(self, ctx, path):
            self.calls.append(path)
            return ToolResult(ok=False, error="ValueError: unchanged")

    tool = FileFailure()
    tools.register(tool)
    path = ctx.workspace.root / "data.txt"
    path.write_text("before")
    record_read(ctx.state, "data.txt", read_revision(path), "before", source_tool="read_file")
    for _ in range(3):
        await dispatch(runtime, [("edit_file", {"path": "data.txt"})])
    assert (await dispatch(runtime, [("edit_file", {"path": "data.txt"})]))[0].meta["blocked_repeat"]
    path.write_text("after")
    # A fresh complete read supplies a real resource revision; no stdout guess.
    assert (await dispatch(runtime, [("read_file", {"path": "data.txt"})]))[0].ok
    retried = (await dispatch(runtime, [("edit_file", {"path": "data.txt"})]))[0]
    assert not retried.meta.get("blocked_repeat") and len(tool.calls) == 4


@pytest.mark.asyncio
async def test_recovery_permission_consumed_once_before_operation_policy(runtime):
    _, tools, ctx, state, _ = runtime
    tool = tools.get("python")
    tool.read_only, tool.side_effect_scope = False, "system"
    arguments = {"code": "bad"}
    for _ in range(3):
        await dispatch(runtime, [("python", arguments)])
    guard = ctx.state["recovery_guard"]
    guard.observe(tool, arguments, ToolResult(ok=False, meta={"recovery_code": "TOOL_OUTCOME_UNKNOWN", "execution_id": "lost"}))
    blocked = (await dispatch(runtime, [("python", arguments)]))[0]
    assert blocked.meta["recovery_code"] == "RECOVERY_CHECK_REQUIRED"
    assert not blocked.meta.get("blocked_repeat")
    # Actual new user instructions give a fresh operation generation; a single
    # recovery permission must survive exactly one execution-boundary check.
    state.applied_steers.append({"text": "Retry the interrupted operation."})
    guard.authorize_user_text("Retry the interrupted operation.")
    allowed = (await dispatch(runtime, [("python", arguments)]))[0]
    assert not allowed.meta.get("recovery_code") and not allowed.meta.get("blocked_repeat")
    assert len(tool.calls) == 4


@pytest.mark.asyncio
async def test_budget_control_never_counts_as_a_repair_failure(runtime):
    _, _, ctx, state, _ = runtime
    ctx.state["run_budget"] = RunBudget(tool_start_limit=0)
    with pytest.raises(BudgetExhausted):
        await dispatch(runtime, [("python", {"code": "bad"})])
    operation = Operation.from_call("python", {"code": "bad"})
    assert state.operation_guard.admit(operation).allowed
    assert not state.operation_guard._failures
    assert state.parts[-1]["result"]["meta"]["code"] == "RUN_BUDGET_EXHAUSTED"


@pytest.mark.asyncio
async def test_unchanged_reads_are_advisory_without_completing_or_blocking_work(runtime):
    _, _, ctx, state, _ = runtime
    (ctx.workspace.root / "source.txt").write_text("Stable source text")
    results = []
    for _ in range(6):
        results.extend(await dispatch(runtime, [("read_file", {"path": "source.txt"})]))
    assert all(result.ok for result in results)
    assert any(result.meta.get("loop_advisory") == "unchanged_full_read" for result in results)
    assert state.end_reason is None and not state.final_ready
    assert state.work["status"] == "running"
    assert ctx.state["run_budget"].snapshot()["tool_starts"] == 6


def test_loop_policy_configuration_and_opaque_resource_boundary(runtime):
    assert Settings.model_fields["loop_policy"].default == "operation"
    assert Settings(loop_policy="legacy").loop_policy == "legacy"
    with pytest.raises(ValidationError):
        Settings(loop_policy="guess")
    _, tools, ctx, state, _ = runtime
    operation = _operation_for_call(tools.get("python"), {"code": "bad"}, ctx, state)
    assert operation.kind == "opaque" and not operation.resource_revisions
