#!/usr/bin/env python3
"""Matched local task-path benchmark. Default: fake providers, real file/kernel work.

--live is the only mode that dispatches configured paid provider calls. This
runner changes no application files and keeps all databases/workspaces in a
fresh temporary directory. It is a sequential data-agent comparison, not a parallel
multi-agent benchmark. See docs/DELEGATION-EXPERIMENT.md for the inference limit.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]

REGISTRY_PROMPT = """You are MatOpt, an agentic assistant for mathematics, statistics, and computing.
Work carefully: inspect evidence, run real computations, and give a complete final answer.

# Environment
The workspace is private to this conversation. Paths in tool calls are relative to it.
User uploads are in uploads/. Follow the user's file, network, and output constraints.
{workspace}

# Available tools
This is the complete menu of registered tools for this run. Use only these exact names
and their supplied JSON schemas. Tools and discovery commands absent from this menu
are unavailable; do not guess additional names.
{menu}

# How to work
Use the available tools for calculations and file operations; do not invent results.
Inspect every tool result. Resolve discrepancies before answering. If a tool fails,
read the error and correct the cause; never repeat an identical failing call.
Cross-check important results through an available independent route when useful.
Be economical: avoid needless tool switches and repeated reads of unchanged results.
Delegated work shares this run's model allowance, tool allowance, and deadline.
After completing the work, give a self-contained answer in the exact format requested
by the user. Distinguish observed results from assumptions. Do not claim an action or
check succeeded without evidence. Keep private reasoning and agent messages private.
"""


def registry_prompt(tools, workspace_description):
    # The exact registered names and first description sentences are the only
    # path-specific instructions. No unavailable capability catalog is inherited.
    menu = "\n".join(f"- `{tool.name}`: {tool.description.split('. ', 1)[0].rstrip('.')}." for tool in tools)
    return REGISTRY_PROMPT.format(workspace=workspace_description, menu=menu)


def registry_resolution_error(name, registered_names):
    return (f"Unknown tool {name!r}. No tool was executed. "
            f"Available tool IDs: {', '.join(registered_names)}. Use an exact listed ID.")

FIXTURES = {
    "invoice": {
        "data": [{"sku": "A", "quantity": 3, "unit_cents": 199},
                 {"sku": "B", "quantity": 2, "unit_cents": 349},
                 {"sku": "C", "quantity": 0, "unit_cents": 1200},
                 {"sku": "D", "quantity": 4, "unit_cents": 102}],
        "instruction": "Sum quantity across all rows and sum quantity * unit_cents. Include zero-quantity rows in row_count.",
        "expected": {"row_count": 4, "unit_count": 9, "total_cents": 1703},
        "smoke_solver": "answer = {'row_count': len(rows), 'unit_count': sum(r['quantity'] for r in rows), "
                        "'total_cents': sum(r['quantity']*r['unit_cents'] for r in rows)}",
    },
    "ledger": {
        "data": [{"account": "A", "cents": 1250}, {"account": "B", "cents": -350},
                 {"account": "A", "cents": -200}, {"account": "C", "cents": 900},
                 {"account": "B", "cents": 500}],
        "instruction": "Sum signed cents for each account into by_account, sum all signed cents into net_cents, and count entries.",
        "expected": {"entry_count": 5, "net_cents": 2100, "by_account": {"A": 1050, "B": 150, "C": 900}},
        "smoke_solver": "totals = {}\nfor r in rows: totals[r['account']] = totals.get(r['account'], 0) + r['cents']\n"
                        "answer = {'entry_count': len(rows), 'net_cents': sum(totals.values()), 'by_account': totals}",
    },
}


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class Journal:
    def __init__(self, path):
        self.path = path

    def append(self, event):
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def source_hashes():
    paths = [ROOT / "scripts/benchmark_delegation.py",
             ROOT / "docs/reference/delegation-result.schema.json"]
    for package in ("backend/app", "backend/data_agent_app"):
        paths.extend((ROOT / package).rglob("*.py"))
    return {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in sorted(paths)}


def fixture_prompt(name, fixture):
    keys = ", ".join(fixture["expected"])
    return (f"Read uploads/{name}.json in the current workspace. {fixture['instruction']} "
            f"Use exact integer arithmetic. Write summary.json at the workspace root with exactly these keys: {keys}. "
            "Leave the input unchanged. Do not install packages, access the network, or create a report. "
            "Your final reply must be the same JSON object as summary.json, without Markdown fences or additional prose.")


def smoke_code(name, fixture):
    return ("import json\nfrom pathlib import Path\n"
            f"rows = json.loads(Path('uploads/{name}.json').read_text())\n{fixture['smoke_solver']}\n"
            "Path('summary.json').write_text(json.dumps(answer, sort_keys=True))\nprint(json.dumps(answer, sort_keys=True))")


class FakeStream:
    def __init__(self, *, text="", tool=None, usage=None):
        call = NS(index=0, id="smoke_call", function=NS(name=tool[0], arguments=json.dumps(tool[1]))) if tool else None
        self.chunks = [NS(usage=None, choices=[NS(finish_reason="tool_calls" if tool else "stop",
                           delta=NS(content=text, tool_calls=[call] if call else [], reasoning_content=""))]),
                       NS(usage=NS(**usage) if usage else None, choices=[])]

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.chunks:
            raise StopAsyncIteration
        return self.chunks.pop(0)

    async def close(self):
        pass


class FakeCreate:
    def __init__(self, path, source, name, fixture, missing_usage=False):
        self.path, self.source, self.name, self.fixture = path, source, name, fixture
        self.calls = 0
        self.missing_usage = missing_usage

    async def __call__(self, **kwargs):
        self.calls += 1
        tool = None
        if self.calls == 1:
            if self.source == "data":
                tool = ("execute_python", {"code": smoke_code(self.name, self.fixture), "session_id": "overwritten"})
            elif self.path == "delegated":
                tool = ("data_agent", {"message": fixture_prompt(self.name, self.fixture), "language": "en"})
            else:
                tool = ("python", {"code": smoke_code(self.name, self.fixture)})
        usage = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
        if self.missing_usage and self.source == "data" and self.calls == 2:
            usage = None
        return FakeStream(text="" if tool else json.dumps(self.fixture["expected"], sort_keys=True), tool=tool, usage=usage)


class WireStream:
    """Observe real wire receipts independently of the shared admission ledger."""
    def __init__(self, stream, row, journal, run_id):
        self.stream, self.iterator, self.row = stream, aiter(stream), row
        self.journal, self.run_id = journal, run_id

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            chunk = await anext(self.iterator)
        except StopAsyncIteration:
            self.finish("completed")
            raise
        except asyncio.CancelledError:
            self.finish("aborted")
            raise
        except Exception:
            self.finish("error")
            raise
        from app.agent.data_agent_bridge import reported_model_usage
        if getattr(chunk, "usage", None) is not None:
            self.row["usage"] = reported_model_usage(chunk.usage)
            self.journal.append({"type": "provider_usage", "run": self.run_id,
                                 "index": self.row["index"], "usage": self.row["usage"]})
        for choice in getattr(chunk, "choices", []) or []:
            reason = getattr(choice, "finish_reason", None)
            if reason:
                self.row["finish_reason"] = reason
        return chunk

    def finish(self, outcome):
        if self.row["outcome"] == "pending":
            self.row.update(outcome=outcome, elapsed_ms=round((time.perf_counter() - self.row.pop("_clock")) * 1000, 3))
            self.journal.append({"type": "provider_end", "run": self.run_id, "receipt": self.row})

    async def close(self):
        try:
            await self.stream.close()
        finally:
            self.finish("aborted")


class WireRecorder:
    def __init__(self, create, source, rows, args, journal, run_id):
        self.create, self.source, self.rows, self.args = create, source, rows, args
        self.journal, self.run_id = journal, run_id

    async def __call__(self, **kwargs):
        from app.llm.catalog import MODEL_ID
        effort = kwargs.get("reasoning_effort")
        if kwargs.get("model") != MODEL_ID or effort not in ({"high", "none"} if self.source == "native" else {"high"}) or kwargs.get("stream") is not True:
            raise RuntimeError("Benchmark requires the configured model and matched worker-high/final-off streaming policy.")
        kwargs["max_tokens"] = self.args.max_output_tokens
        payload = json.dumps(kwargs, ensure_ascii=False, sort_keys=True, default=str).encode()
        row = {"index": len(self.rows), "source": self.source, "model": MODEL_ID, "reasoning": effort,
               "stage": "final" if self.source == "native" and effort == "none" else "worker",
               "max_output_tokens": kwargs["max_tokens"], "request_bytes": len(payload), "request_sha256": digest(payload),
               "dispatched": False, "outcome": "pending", "usage": None, "finish_reason": None,
               "elapsed_ms": None, "_clock": time.perf_counter()}
        self.rows.append(row)
        if len(payload) > self.args.max_request_bytes or len(self.rows) > self.args.model_attempts:
            row.update(outcome="local_rejection", elapsed_ms=0)
            row.pop("_clock")
            self.journal.append({"type": "provider_rejected", "run": self.run_id, "receipt": row})
            raise RuntimeError("Benchmark request size/count cap reached before provider dispatch.")
        row["dispatched"] = True
        self.journal.append({"type": "provider_start", "run": self.run_id, "time": time.time(),
                             "receipt": {key: value for key, value in row.items() if key != "_clock"}})
        try:
            stream = await self.create(**kwargs)
        except BaseException as exc:
            row.update(outcome="aborted" if isinstance(exc, asyncio.CancelledError) else "error",
                       elapsed_ms=round((time.perf_counter() - row.pop("_clock")) * 1000, 3))
            self.journal.append({"type": "provider_end", "run": self.run_id, "receipt": row})
            raise
        return WireStream(stream, row, self.journal, self.run_id)


def parsed_answer(text):
    try:
        return json.loads(text), True
    except (ValueError, TypeError):
        stripped = text.strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            try:
                return json.loads(stripped.split("\n", 1)[1].rsplit("```", 1)[0]), False
            except (ValueError, IndexError):
                pass
    return None, False


def inspect_oracle(workspace, name, fixture, input_digest, final_text):
    target = workspace / "summary.json"
    try:
        written = json.loads(target.read_text())
    except (OSError, ValueError):
        written = None
    answer, exact_format = parsed_answer(final_text)
    original = workspace / "uploads" / f"{name}.json"
    same = original.is_file() and digest(original.read_bytes()) == input_digest
    return {"expected": fixture["expected"], "observed_file": written, "observed_answer": answer,
            "file_exists_at_required_path": target.is_file(), "file_content_matches": written == fixture["expected"],
            "answer_content_matches": answer == fixture["expected"], "answer_exact_json_format": exact_format,
            "input_unchanged": same, "environment_pass": target.is_file() and written == fixture["expected"] and same,
            "all_summary_locations": sorted(str(p.relative_to(workspace)) for p in workspace.rglob("summary.json")),
            "workspace_files": sorted(str(p.relative_to(workspace)) for p in workspace.rglob("*") if p.is_file())}


def accounting(rows, budget, prices, live):
    from app.agent.session.telemetry import aggregate_attempts, price_usage
    reported = [row["usage"] for row in rows if row["dispatched"] and row["usage"] is not None]
    dispatched = sum(row["dispatched"] for row in rows)
    exact = aggregate_attempts(reported) if len(reported) == dispatched and dispatched else None
    subtotal = aggregate_attempts(reported)
    # Required counters are compared: optional provider cache fields need not
    # be projected identically by the native and delegated adapters.
    required = lambda usage: {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")} if usage else None
    ledger = budget.get("usage", {})
    parity = (budget.get("model_attempts") == len(rows)
              and ledger.get("reported_attempts") == len(reported)
              and required(ledger.get("reported_subtotal")) == required(subtotal)
              and required(ledger.get("exact")) == required(exact))
    rates = prices.get("deepseek/deepseek-flash")
    amount = price_usage(exact, rates) if live and exact is not None and isinstance(rates, dict) else None
    return {"actual_requests": dispatched, "reported_attempts": len(reported), "unknown_attempts": dispatched - len(reported),
            "exact_usage": exact, "reported_subtotal": subtotal, "parent_ledger_matches_wire": parity,
            "cost": {"amount": str(amount) if amount is not None else None,
                     "currency": rates.get("currency") if isinstance(rates, dict) else None,
                     "basis": "operator-configured rates" if amount is not None else "synthetic" if not live else "unknown usage or rates"}}


async def experiment(args, state_dir, receipts, trials):
    from app.agent.harness import AgentHarness, RunRequest
    from app.agent.tools.base import ToolRegistry
    from app.agent.tools.file_tools import ReadFileTool, WriteFileTool, ListFilesTool
    from app.agent.tools.python_tool import PythonTool
    from app.agent.tools.data_agent_tool import DataAgentTool
    from app.agent.tools.router import ToolRouter
    from app.config import settings
    from app.llm.catalog import MODEL_ID, PROVIDER_ID, PROVIDERS
    from app.llm.openai_compat import OpenAICompatProvider
    import app.runtime as runtime_module
    from data_agent_app.core import database as data_agent_database
    from data_agent_app.api.v1 import chat as data_agent_chat
    import data_agent_app.models.models  # register schema before creating isolated tables

    class BenchmarkHarness(AgentHarness):
        def _start_title(self, *args, **kwargs):
            pass  # Task-only comparison: disable optional title costs in both paths.

        def _system_prompt(self, workspace, route=None, ui=False):
            if args.tool_menu == "registry":
                return registry_prompt(self.tools, workspace.describe_for_prompt())
            return super()._system_prompt(workspace, route, ui)

    class RegistryMenuRouter(ToolRouter):
        def resolve(self, registry, name):
            tool, error = super().resolve(registry, name)
            if tool is None:
                return None, registry_resolution_error(name, registry.names())
            return tool, error

    class Registry:
        def __init__(self, provider):
            self.provider = provider

        def resolve(self, provider_id, model_id):
            return PROVIDERS[PROVIDER_ID], PROVIDERS[PROVIDER_ID].default_model

        def get(self, provider_id):
            return self.provider

        def configured_ids(self):
            return [PROVIDER_ID]

    if args.live and not settings.deepseek_api_key:
        raise RuntimeError("Live mode requires the existing configured DeepSeek API key.")
    data_agent_database.Base.metadata.create_all(data_agent_database.engine)
    rt = runtime_module.Runtime()
    runtime_module.get_runtime = lambda: rt  # Process-local glue uses the benchmark DB, never the live app DB.
    original_child_create = data_agent_chat.llm_service.client.chat.completions.create
    original_child_execute = data_agent_chat.agent_service.execute_tool
    runs = []
    try:
        for repetition in range(args.repeats):
            for fixture_index, name in enumerate(args.fixtures):
                fixture = FIXTURES[name]
                paths = ("direct", "delegated") if (repetition + fixture_index) % 2 == 0 else ("delegated", "direct")
                for order, path in enumerate(paths):
                    row_id = f"{name}-r{repetition + 1}-{path}"
                    conv = rt.db.create_conversation(title=row_id, provider=PROVIDER_ID, model=MODEL_ID, reasoning="high")
                    cid = conv["id"]
                    workspace = rt.workspace(cid)
                    input_path = workspace.resolve(f"uploads/{name}.json")
                    input_path.parent.mkdir(parents=True, exist_ok=True)
                    data = json.dumps(fixture["data"], indent=2).encode()
                    input_path.write_bytes(data)
                    prompt = fixture_prompt(name, fixture)
                    wire_rows = []
                    child_tools = []

                    async def observe_child_tool(tool_name, tool_input, *extra, **kwargs):
                        from app.agent.budget import BudgetExhausted
                        from app.agent.data_agent_bridge import get_data_agent_exec_context
                        bridge = get_data_agent_exec_context()
                        previous_count = bridge.run_budget.snapshot()["tool_starts"]
                        tool_row = {"name": tool_name, "call_id": kwargs.get("call_id"), "outcome": "pending",
                                    "admitted": False, "elapsed_ms": None}
                        child_tools.append(tool_row)
                        tool_clock = time.perf_counter()
                        receipts.append({"type": "child_tool_invocation", "run": row_id, "tool": tool_row})
                        try:
                            result = await original_child_execute(tool_name, tool_input, *extra, **kwargs)
                            tool_row["outcome"] = "success" if result.get("success") else "error"
                            return result
                        except BudgetExhausted:
                            tool_row["outcome"] = "budget_exhausted"
                            raise
                        except asyncio.CancelledError:
                            tool_row["outcome"] = "aborted"
                            raise
                        except Exception:
                            tool_row["outcome"] = "error"
                            raise
                        finally:
                            tool_row["admitted"] = bridge.run_budget.snapshot()["tool_starts"] > previous_count
                            tool_row["elapsed_ms"] = round((time.perf_counter() - tool_clock) * 1000, 3)
                            receipts.append({"type": "child_tool_end", "run": row_id, "tool": tool_row})

                    data_agent_chat.agent_service.execute_tool = observe_child_tool
                    provider = OpenAICompatProvider(PROVIDERS[PROVIDER_ID], settings.deepseek_api_key or "offline",
                                                    settings.deepseek_base_url, timeout=args.timeout)
                    outer_create = provider.client.chat.completions.create if args.live else FakeCreate(path, "native", name, fixture)
                    child_create = original_child_create if args.live else FakeCreate(path, "data", name, fixture, args.smoke_missing_usage)
                    provider.client.chat.completions.create = WireRecorder(outer_create, "native", wire_rows, args, receipts, row_id)
                    data_agent_chat.llm_service.client.chat.completions.create = WireRecorder(child_create, "data", wire_rows, args, receipts, row_id)
                    tools = ToolRegistry([DataAgentTool()] if path == "delegated" else [
                        PythonTool(default_timeout=30, max_timeout=60), ReadFileTool(), WriteFileTool(), ListFilesTool(),
                    ])
                    harness = BenchmarkHarness(rt.db, rt.kernels, settings, Registry(provider), tools,
                        tool_router=RegistryMenuRouter() if args.tool_menu == "registry" else None,
                        executions=rt.executions)
                    rt.harness = harness
                    started = time.perf_counter()
                    event_counts = {}
                    trials.append({"type": "run_start", "id": row_id, "conversation_id": cid, "time": time.time()})
                    try:
                        async for event in harness.run(RunRequest(cid, prompt, provider_id=PROVIDER_ID, model_id=MODEL_ID,
                                                                 reasoning="high", verify=False, web_search=False)):
                            event_counts[event.type] = event_counts.get(event.type, 0) + 1
                        await harness.wait_side_tasks()
                        elapsed = round((time.perf_counter() - started) * 1000, 3)
                        assistant = next((m["content"] for m in reversed(rt.db.list_messages(cid)) if m["role"] == "assistant"), {})
                        final_text = assistant.get("final_text", "")
                        budget = assistant.get("budget", {})
                        events = rt.db.list_session_events(cid)
                        result = {"id": row_id, "fixture": name, "path": path, "repetition": repetition + 1, "pair_order": order,
                                  "conversation_id": cid, "input_sha256": digest(data), "prompt_sha256": digest(prompt.encode()),
                                  "status": assistant.get("status", "missing"), "final_ready": assistant.get("final_ready", False),
                                  "final_text": final_text, "elapsed_ms": elapsed, "event_counts": event_counts,
                                  "oracle": inspect_oracle(workspace.root, name, fixture, digest(data), final_text),
                                  "budget": budget, "provider_calls": wire_rows,
                                  "child_tools": child_tools,
                                  "accounting": accounting(wire_rows, budget, settings.model_pricing, args.live),
                                  "native_attempt_retries": sum(e["type"] == "request/header" and e["data"].get("attempt", 1) > 1
                                                               and not e["data"].get("legacy") for e in events),
                                  "tool_failures": sum(e["type"] == "tool/result" and e["data"].get("ok") is False for e in events)}
                        runs.append(result)
                        trials.append({"type": "run_end", "result": result})
                        print(json.dumps({"run": row_id, "status": result["status"], "environment_pass": result["oracle"]["environment_pass"],
                                          "answer_match": result["oracle"]["answer_content_matches"], "requests": len(wire_rows),
                                          "accounting_match": result["accounting"]["parent_ledger_matches_wire"]}), flush=True)
                    finally:
                        await harness.shutdown()
                        await rt.kernels.shutdown_all()  # Each path starts with the same cold kernel.
                        await provider.client.close()
    finally:
        await rt.stop()
        await data_agent_chat.llm_service.client.close()
        data_agent_database.engine.dispose()
    return runs


def summarize(runs):
    groups = {}
    for path in ("direct", "delegated"):
        rows = [r for r in runs if r["path"] == path]
        groups[path] = {"runs": len(rows), "environment_passes": sum(r["oracle"]["environment_pass"] for r in rows),
                        "answer_matches": sum(r["oracle"]["answer_content_matches"] for r in rows),
                        "complete_runs": sum(r["status"] == "complete" for r in rows),
                        "median_elapsed_ms": statistics.median(r["elapsed_ms"] for r in rows),
                        "actual_requests": sum(r["accounting"]["actual_requests"] for r in rows),
                        "native_requests": sum(c["dispatched"] for r in rows for c in r["provider_calls"] if c["source"] == "native"),
                        "child_requests": sum(c["dispatched"] for r in rows for c in r["provider_calls"] if c["source"] == "data"),
                        "child_tool_failures": sum(t["outcome"] != "success" for r in rows for t in r["child_tools"]),
                        "unknown_usage_attempts": sum(r["accounting"]["unknown_attempts"] for r in rows)}
    paired = []
    for name, repetition in sorted({(r["fixture"], r["repetition"]) for r in runs}):
        by_path = {r["path"]: r for r in runs if r["fixture"] == name and r["repetition"] == repetition}
        a, b = by_path["direct"], by_path["delegated"]
        exact_a, exact_b = a["accounting"]["exact_usage"], b["accounting"]["exact_usage"]
        paired.append({"fixture": name, "repetition": repetition,
                       "both_environment_pass": a["oracle"]["environment_pass"] and b["oracle"]["environment_pass"],
                       "delegated_minus_direct_ms": round(b["elapsed_ms"] - a["elapsed_ms"], 3),
                       "delegated_minus_direct_total_tokens": exact_b["total_tokens"] - exact_a["total_tokens"] if exact_a and exact_b else None})
    return {"by_path": groups, "pairs": paired, "accounting_all_match": all(r["accounting"]["parent_ledger_matches_wire"] for r in runs)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Opt in to actual configured provider requests.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixtures", nargs="+", choices=tuple(FIXTURES), default=list(FIXTURES))
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--model-attempts", type=int, default=6)
    parser.add_argument("--tool-starts", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--max-output-tokens", type=int, default=2048)
    parser.add_argument("--max-request-bytes", type=int, default=60000)
    parser.add_argument("--smoke-missing-usage", action="store_true", help="In fake mode, omit one child usage receipt per run.")
    parser.add_argument("--tool-menu", choices=("inherited", "registry"), default="inherited",
                        help="registry uses matched outer prompts and errors naming only registered tools; inherited retains the original pilot setup.")
    args = parser.parse_args()
    if not 1 <= args.repeats <= 5 or not 1 <= args.model_attempts <= 12 or not 1 <= args.tool_starts <= 20:
        parser.error("Pilot limits: repeats 1..5, model attempts 1..12, tool starts 1..20.")
    if not 1 <= args.timeout <= 300 or not 128 <= args.max_output_tokens <= 4096 or not 1 <= args.max_request_bytes <= 120000:
        parser.error("Pilot limits: timeout 1..300s, output tokens 128..4096, request bytes 1..120000.")
    if args.live and args.smoke_missing_usage:
        parser.error("Synthetic usage fault injection is only allowed in fake mode.")
    if len(args.fixtures) != len(set(args.fixtures)):
        parser.error("Fixture names must be unique.")
    output = args.output.expanduser().resolve()
    if output.exists():
        parser.error("Output already exists; choose a fresh result path to preserve prior evidence.")
    output.parent.mkdir(parents=True, exist_ok=True)
    receipts = Journal(output.with_suffix(".receipts.jsonl"))
    trials = Journal(output.with_suffix(".runs.jsonl"))
    if receipts.path.exists() or trials.path.exists():
        parser.error("Raw journals already exist; choose a fresh result path.")
    state_dir = Path(tempfile.mkdtemp(prefix="matopt-delegation-"))
    # Override only this fresh process before application imports. Never use a
    # configured production DATABASE_URL, uploads directory or MCP connection.
    os.environ.update(DATA_DIR=str(state_dir), DATABASE_URL=f"sqlite:///{state_dir}/data_agent/data_agent.sqlite3",
                      LOCAL_WORKSPACE_ROOT=str(state_dir / "data_agent/workspaces"), LOCAL_MODE="true",
                      FILE_STORAGE_MODE="local", CODE_EXECUTION_MODE="local", MCP_ENABLED="false",
                      SKILL_AUTO_ACTIVATE="false", WEB_SEARCH_ENABLED="false", DOCUMENT_AUTO_PARSE="false",
                      AGENT_MAX_STEPS=str(args.model_attempts), RUN_MAX_MODEL_ATTEMPTS=str(args.model_attempts),
                      RUN_MAX_TOOL_STARTS=str(args.tool_starts), RUN_TIMEOUT_SECONDS=str(args.timeout),
                      MAX_OUTPUT_TOKENS=str(args.max_output_tokens), MAX_AGENT_ITERATIONS=str(args.model_attempts))
    if not args.live:
        os.environ.update(DEEPSEEK_API_KEY="offline-benchmark", DEEPSEEK_BASE_URL="http://127.0.0.1:9")
    sys.path.insert(0, str(ROOT / "backend"))
    run_count = len(args.fixtures) * args.repeats * 2
    conditions = {"model": "deepseek-flash", "worker_reasoning": "high", "native_final_reasoning": "none", "verify": False, "title_sidecar": False,
                  "tool_menu": args.tool_menu,
                  "outer_prompt_template_sha256": digest(REGISTRY_PROMPT.encode()) if args.tool_menu == "registry" else None,
                  "unknown_tool_hints": "registered IDs only" if args.tool_menu == "registry" else "inherited router",
                  "cold_kernel_each_run": True, "path_order": "counterbalanced by fixture/repetition",
                  "repeats": args.repeats, "fixtures": args.fixtures, "model_attempts_per_run": args.model_attempts,
                  "tool_starts_per_run": args.tool_starts, "timeout_seconds_per_run": args.timeout,
                  "output_tokens_per_request": args.max_output_tokens, "request_bytes_limit": args.max_request_bytes}
    envelope = {"runs": run_count, "maximum_provider_requests": run_count * args.model_attempts,
                "maximum_completion_tokens": run_count * args.model_attempts * args.max_output_tokens,
                "maximum_serialized_request_bytes": run_count * args.model_attempts * args.max_request_bytes,
                "summed_run_deadlines_seconds": run_count * args.timeout,
                "dollar_upper_bound": None, "note": "Request bytes are not token counts. No dollar cap is inferred without billing rates and a tokenizer."}
    print(json.dumps({"mode": "live" if args.live else "fake", "envelope": envelope, "state_dir": str(state_dir)}), flush=True)
    started_at = time.time()
    before_hashes = source_hashes()
    trials.append({"type": "experiment_start", "mode": "live" if args.live else "fake", "conditions": conditions,
                   "envelope": envelope, "state_dir": str(state_dir), "source_sha256": before_hashes})
    runs = asyncio.run(experiment(args, state_dir, receipts, trials))
    after_hashes = source_hashes()
    report = {"schema_version": 1, "mode": "live" if args.live else "fake", "synthetic_provider_usage": not args.live,
              "started_at": started_at, "state_dir": str(state_dir), "conditions": conditions, "envelope": envelope,
              "source_sha256": before_hashes, "source_sha256_after": after_hashes,
              "source_unchanged": before_hashes == after_hashes,
              "raw_journals": {"receipts": str(receipts.path), "trials": str(trials.path)},
              "runs": runs, "summary": summarize(runs)}
    import jsonschema
    schema = json.loads((ROOT / "docs/reference/delegation-result.schema.json").read_text())
    jsonschema.validate(report, schema)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(output), "summary": report["summary"]}), flush=True)
    if not report["summary"]["accounting_all_match"]:
        return 2
    if not args.live and not all(r["oracle"]["environment_pass"] and r["oracle"]["answer_content_matches"] for r in runs):
        return 3
    if not report["source_unchanged"]:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
