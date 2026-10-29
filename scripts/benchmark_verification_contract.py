#!/usr/bin/env python3
"""Unpaid, deterministic actual-harness verification/publication experiment.

Model text and verdicts are injected fixtures, not quality measurements. Real
AgentHarness, RunBudget, private session logging, and public projection execute.
Run with the backend test environment; no provider or external service is used.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agent.harness import AgentHarness, RunRequest
from app.agent.presentation import public_content
from app.agent.prompts import FINAL_RESPONSE_PROMPT, VERIFIER_PROMPT
from app.agent.tools.base import ToolRegistry
from app.agent.tools.progress_tool import ReportProgressTool
from app.config import settings
from app.llm.base import StreamDelta
from app.storage.db import Database
from tests.fake_provider import FakeProvider, FakeRegistry

SOURCE = "https://fixture.test/measurement"
LIMITATION = "Instantaneous speed is not established."
PROBLEM = ("Compute the average speed from the supplied measurement: distance 60 m, elapsed time 5 s. "
           f"Cite [Measurement]({SOURCE}) and state the limit of this inference: {LIMITATION}")
STEER = "Correct the elapsed time to 3 s while retaining distance 60 m, the source, and the limitation."
PRIVATE_REPORT = "PRIVATE_CHECKER_REPORT_CANARY"
PRIVATE_REASONING = "PRIVATE_REASONING_CANARY"


def answer(value=12, *, prefix=""):
    return f"{prefix}Average speed is **{value} m/s**. [Measurement]({SOURCE}). {LIMITATION}"


def fields(text):
    """A deliberately narrow oracle for this fixture's stated answer grammar."""
    measured = re.search(r"Average speed is \*\*(\d+(?:\.\d+)?) (m/s|km/h)\*\*", text)
    return {"value": float(measured[1]) if measured else None,
            "unit": measured[2] if measured else None,
            "citations": re.findall(r"\[Measurement\]\(([^)]+)\)", text),
            "limitation": LIMITATION in text}


def errors(text, expected_value=12):
    observed = fields(text)
    expected = {"value": float(expected_value), "unit": "m/s", "citations": [SOURCE], "limitation": True}
    return [key for key in expected if observed[key] != expected[key]]


def mutate(text, dimension):
    return {"clean": text, "value": text.replace("**12 m/s**", "**15 m/s**"),
            "unit": text.replace("**12 m/s**", "**12 km/h**"),
            "citation": text.replace(SOURCE, "https://fixture.test/unsupported"),
            "limitation": text.replace(LIMITATION, "").strip()}[dimension]


@dataclass(frozen=True)
class Scenario:
    id: str
    family: str
    drafts: tuple[str, ...]
    verdicts: tuple[str, ...]
    writer_change: str = "clean"
    steer: bool = False
    model_limit: int = 12


def scenarios():
    cases = [Scenario(f"writer_{change}", "writer_drift", (answer(),), ("correct",), change)
             for change in ("clean", "value", "unit", "citation", "limitation")]
    cases += [
        Scenario("checker_missed_error", "checker", (answer(15),), ("correct",)),
        Scenario("checker_false_failure", "checker", (answer(), answer(prefix="Rechecked: ")), ("incorrect", "correct")),
        Scenario("checker_inconclusive", "checker", (answer(),), ("uncertain",)),
        Scenario("correction_succeeds", "correction", (answer(15), answer()), ("incorrect", "correct")),
        Scenario("correction_limit", "correction", (answer(15), answer(14)), ("incorrect", "incorrect")),
        Scenario("steer_new_episode", "steering", (answer(), answer(20)), ("correct", "correct"), steer=True, model_limit=5),
        Scenario("steer_shared_cap", "steering", (answer(), answer(20)), ("correct", "correct"), steer=True, model_limit=3),
    ]
    return cases


def check_input(messages):
    text = messages[-1]["content"]
    problem, _, rest = text.removeprefix("Problem statement (from the user):\n").partition(
        "\n\nProposed solution (from the solver):\n")
    candidate, _, sources = rest.partition("\n\nSource excerpts already read from the workspace (untrusted source material):\n")
    return {"problem": problem, "candidate": candidate, "sources": sources.rstrip("\n")}


def candidate_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ContractProvider(FakeProvider):
    def __init__(self, case):
        script = []
        for draft, verdict in zip(case.drafts, case.verdicts):
            script.extend([("worker", draft), ("check", f"<report>{PRIVATE_REPORT}</report><verdict>{verdict}</verdict>")])
        super().__init__([])
        self.steps = script
        self.case = case
        self.requests = []
        self.check_started = asyncio.Event()
        self.release_check = asyncio.Event()

    async def stream(self, messages, tools, options):
        phase = "final" if messages[0]["content"] == FINAL_RESPONSE_PROMPT else "check" if messages[0]["content"] == VERIFIER_PROMPT else "worker"
        request = {"phase": phase, "tools_offered": bool(tools)}
        self.requests.append(request)
        if phase == "final":
            payload = json.loads(messages[-1]["content"])
            request["payload"] = payload
            text = mutate(payload["answer"], self.case.writer_change)
        else:
            assert self.steps, f"Unexpected {phase} request in {self.case.id}"
            expected_phase, text = self.steps.pop(0)
            assert phase == expected_phase, (self.case.id, expected_phase, phase)
            if phase == "check":
                request["input"] = check_input(messages)
                request["injected_verdict"] = re.search(r"<verdict>(.*?)</verdict>", text)[1]
                if self.case.steer and not self.check_started.is_set():
                    self.check_started.set()
                    await self.release_check.wait()
        request["output"] = text
        yield StreamDelta(kind="reasoning", text=PRIVATE_REASONING)
        for start in range(0, len(text), 23):
            yield StreamDelta(kind="text", text=text[start:start + 23])
        yield StreamDelta(kind="usage", usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        yield StreamDelta(kind="finish", finish_reason="stop")


class NoKernels:
    def peek(self, _cid):
        return None


async def run_case(case, *, strict, directory):
    directory.mkdir(parents=True, exist_ok=True)
    db = Database(directory / "experiment.sqlite")
    provider = ContractProvider(case)
    config = settings.model_copy(update={"data_dir": directory, "skill_auto_activate": False,
        "data_agent_enabled": False, "align_monitor": False, "document_auto_parse": False,
        "session_log_enabled": True, "agent_max_steps": 16, "verify_rounds": 50,
        "max_turn_cost": None, "max_conversation_cost": None,
        "run_max_model_attempts": case.model_limit, "run_max_tool_starts": 8, "run_timeout_seconds": 30})
    # A real inert public tool keeps the request on the agent path. These
    # fixtures do not execute tool bodies or silently imply recomputation.
    harness = AgentHarness(db, NoKernels(), config, FakeRegistry(provider), ToolRegistry([ReportProgressTool()]))
    cid = db.create_conversation(title="Verification contract fixture")["id"]
    events = []

    async def collect():
        async for event in harness.run(RunRequest(cid, PROBLEM, verify=True, strict_final=strict, web_search=False)):
            events.append(deepcopy(event.to_dict()))
        await harness.wait_side_tasks()

    task = asyncio.create_task(collect())
    try:
        if case.steer:
            await asyncio.wait_for(provider.check_started.wait(), 5)
            assert harness.enqueue_steer(cid, STEER)
            provider.release_check.set()
        await asyncio.wait_for(task, 10)
        end = next(event for event in events if event["type"] == "message_end")
        saved = db.get_message(end["message_id"])["content"]
        final_text = "\n\n".join(part["text"] for part in end["content"]["parts"] if part["type"] == "text")
        checks = [request for request in provider.requests if request["phase"] == "check"]
        records = [part for part in saved["parts"] if part["type"] == "verification"]
        check_rows = []
        for index, check in enumerate(checks, 1):
            record = next((part for part in records if part["total_checks"] == index), None)
            check_rows.append({"ordinal": index, "episode": index if case.steer else 1,
                "candidate": check["input"]["candidate"], "candidate_input": check["input"],
                "oracle_errors": errors(check["input"]["candidate"], 20 if case.steer and index > 1 else 12),
                "injected_verdict": check["injected_verdict"], "completed_record": record is not None,
                "record": {key: record[key] for key in ("check_id", "candidate_sha256", "round", "result_generation", "total_checks", "verdict")} if record else None,
                "digest_matches_input": record["candidate_sha256"] == candidate_digest(check["input"]) if record else None})
        completed = [check for check in check_rows if check["completed_record"]]
        final_indices = [index for index, request in enumerate(provider.requests) if request["phase"] == "final"]
        last_check_index = max((index for index, request in enumerate(provider.requests) if request["phase"] == "check"), default=-1)
        row = {"fixture": case.id, "family": case.family, "mode": "strict" if strict else "normal",
            "injection": {"writer_change": case.writer_change, "drafts": list(case.drafts), "verdicts": list(case.verdicts), "steer": STEER if case.steer else None},
            "status": end["status"], "final_text": final_text, "final_fields": fields(final_text),
            "final_oracle_errors": errors(final_text, 20 if case.steer else 12),
            "verification": saved["work"]["verification"], "notice": saved.get("notice"),
            "total_checks_started": saved["stats"]["verification_checks"], "checks": check_rows,
            "missed_errors": sum(bool(check["oracle_errors"]) and check["injected_verdict"] == "correct" for check in completed),
            "false_failures": sum(not check["oracle_errors"] and check["injected_verdict"] == "incorrect" for check in completed),
            "worker_calls": sum(request["phase"] == "worker" for request in provider.requests),
            "checker_calls": len(checks), "final_writer_calls": len(final_indices),
            "request_sequence": [request["phase"] for request in provider.requests],
            "final_writer_tools_offered": any(provider.requests[index]["tools_offered"] for index in final_indices),
            "unchecked_final_writer_calls": sum(index > last_check_index for index in final_indices),
            "final_equals_last_checked_candidate": final_text == completed[-1]["candidate"] if completed else None,
            "final_mode": next(event["mode"] for event in events if event["type"] == "final_start"),
            "final_delta_count": sum(event["type"] == "text_delta" for event in events),
            "final_deltas_match_canonical": "".join(event["delta"] for event in events if event["type"] == "text_delta") == final_text,
            "model_calls": len(provider.requests), "model_attempts": saved["budget"]["model_attempts"],
            "model_limit": case.model_limit, "usage_complete": saved["budget"]["usage"]["complete"],
            "steers_applied": sum(event["type"] == "steer_applied" for event in events),
            "canonical_reload_matches": public_content(saved) == end["content"],
            "private_text_leaked": any(token in json.dumps(events) for token in (PRIVATE_REASONING, PRIVATE_REPORT)),
            "unused_script_steps": len(provider.steps)}
        return row
    finally:
        provider.release_check.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await harness.wait_side_tasks()
        db.close()


def summarize(rows):
    summary = {}
    for mode in ("normal", "strict"):
        subset = [row for row in rows if row["mode"] == mode]
        drift = [row for row in subset if row["family"] == "writer_drift" and row["fixture"] != "writer_clean"]
        summary[mode] = {"runs": len(subset),
            "writer_drift_injections": len(drift), "writer_drift_injections_reaching_final": sum(bool(row["final_oracle_errors"]) for row in drift),
            "injected_checker_errors_missed": sum(row["missed_errors"] for row in subset),
            "injected_checker_false_failures": sum(row["false_failures"] for row in subset),
            "model_attempts": sum(row["model_attempts"] for row in subset),
            "canonical_mismatches": sum(not row["canonical_reload_matches"] for row in subset),
            "private_text_leaks": sum(row["private_text_leaked"] for row in subset)}
    return summary


async def experiment():
    with tempfile.TemporaryDirectory(prefix="matopt-verification-contract-") as temp:
        rows = [await run_case(case, strict=strict, directory=Path(temp) / case.id / str(strict))
                for case in scenarios() for strict in (False, True)]
    tracked = ["backend/app/agent/harness.py", "backend/app/agent/prompts.py", "backend/app/agent/presentation.py",
               "backend/app/agent/budget.py", "scripts/benchmark_verification_contract.py"]
    return {"schema_version": 1, "kind": "actual_harness_injected_provider_contract",
        "paid_calls": 0, "real_provider_quality_measured": False,
        "oracle": {"problem": PROBLEM, "field_grammar": "Average speed literal, linked Measurement URL, explicit limitation sentence", "expected": fields(answer())},
        "settings": {"verify": True, "verify_rounds": 50, "effective_max_checks_per_episode": 2, "session_log_enabled": True},
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in tracked},
        "summary": summarize(rows), "runs": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/reference/verification-contract-results.json")
    args = parser.parse_args()
    result = asyncio.run(experiment())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
