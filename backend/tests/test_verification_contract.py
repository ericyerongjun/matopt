"""Paired publication/checker fault injection through the real agent harness.

These tests assert controller behavior, never model quality. The narrow numeric,
unit, citation and limitation oracles are independent of the injected verdicts.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/benchmark_verification_contract.py"
spec = importlib.util.spec_from_file_location("verification_contract_benchmark", SCRIPT)
benchmark = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = benchmark
spec.loader.exec_module(benchmark)
CASES = {case.id: case for case in benchmark.scenarios()}


async def pair(identifier, tmp_path):
    rows = [await benchmark.run_case(CASES[identifier], strict=strict, directory=tmp_path / str(strict))
            for strict in (False, True)]
    for row in rows:
        assert row["canonical_reload_matches"] and row["final_deltas_match_canonical"]
        assert not row["private_text_leaked"]
        assert not row["final_writer_tools_offered"]
        assert row["model_calls"] == row["model_attempts"] <= row["model_limit"]
        assert row["usage_complete"]
        records = [check["record"] for check in row["checks"] if check["completed_record"]]
        assert len({record["check_id"] for record in records}) == len(records)
        assert all(check["digest_matches_input"] for check in row["checks"] if check["completed_record"])
        assert all(record["round"] <= 2 for record in records)
    return rows


@pytest.mark.asyncio
@pytest.mark.parametrize("dimension", ["clean", "value", "unit", "citation", "limitation"])
async def test_normal_writer_can_drift_after_check_but_strict_releases_exact_checked_text(dimension, tmp_path):
    normal, strict = await pair("writer_" + dimension, tmp_path)
    expected_errors = [] if dimension == "clean" else ["citations" if dimension == "citation" else dimension]
    assert normal["final_oracle_errors"] == expected_errors
    assert strict["final_oracle_errors"] == []
    assert normal["request_sequence"] == ["worker", "check", "final"]
    assert strict["request_sequence"] == ["worker", "check"]
    assert normal["unchecked_final_writer_calls"] == 1
    assert strict["unchecked_final_writer_calls"] == strict["final_writer_calls"] == 0
    assert normal["final_mode"] == "streaming" and normal["final_delta_count"] > 1
    assert strict["final_mode"] == "buffered"
    assert strict["final_equals_last_checked_candidate"]
    assert normal["final_equals_last_checked_candidate"] == (dimension == "clean")
    for row in (normal, strict):
        assert row["status"] == "complete"
        assert row["verification"] == {"status": "correct", "rounds": 1}
        assert row["checks"][0]["oracle_errors"] == []
        assert row["missed_errors"] == row["false_failures"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier", ["checker_missed_error", "checker_false_failure", "checker_inconclusive"])
async def test_checker_faults_are_measured_against_explicit_oracle_in_both_modes(identifier, tmp_path):
    for row in await pair(identifier, tmp_path):
        assert row["status"] == "complete"
        if identifier == "checker_missed_error":
            assert row["final_fields"]["value"] == 15
            assert row["final_oracle_errors"] == ["value"]
            assert row["missed_errors"] == 1 and row["false_failures"] == 0
            assert row["worker_calls"] == row["checker_calls"] == 1
            assert row["verification"]["status"] == "correct"
        elif identifier == "checker_false_failure":
            assert row["final_oracle_errors"] == []
            assert row["false_failures"] == 1 and row["missed_errors"] == 0
            assert row["worker_calls"] == row["checker_calls"] == 2
            assert row["verification"] == {"status": "correct", "rounds": 2}
            assert all(check["oracle_errors"] == [] for check in row["checks"])
        else:
            assert row["final_oracle_errors"] == []
            assert row["missed_errors"] == row["false_failures"] == 0
            assert row["worker_calls"] == row["checker_calls"] == 1
            assert row["verification"] == {"status": "uncertain", "rounds": 1}
            assert "inconclusive" in row["notice"]


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier", ["correction_succeeds", "correction_limit"])
async def test_one_bounded_correction_has_distinct_check_identity_and_candidate_hash(identifier, tmp_path):
    for row in await pair(identifier, tmp_path):
        assert row["worker_calls"] == row["checker_calls"] == row["total_checks_started"] == 2
        assert row["unused_script_steps"] == 0
        records = [check["record"] for check in row["checks"]]
        assert [record["check_id"] for record in records] == ["1:check:1", "1:check:2"]
        assert [record["round"] for record in records] == [1, 2]
        assert [record["total_checks"] for record in records] == [1, 2]
        assert [record["result_generation"] for record in records] == [0, 1]
        assert len({record["candidate_sha256"] for record in records}) == 2
        if identifier == "correction_succeeds":
            assert row["final_fields"]["value"] == 12
            assert row["final_oracle_errors"] == []
            assert row["verification"] == {"status": "correct", "rounds": 2}
        else:
            assert row["final_fields"]["value"] == 14
            assert row["final_oracle_errors"] == ["value"]
            assert row["verification"] == {"status": "limit_reached", "rounds": 2}
            assert "provisional" in row["notice"]
        if row["mode"] == "strict":
            assert row["final_equals_last_checked_candidate"]


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier", ["steer_new_episode", "steer_shared_cap"])
async def test_steering_restarts_episode_round_without_resetting_check_identity_or_run_allowance(identifier, tmp_path):
    for row in await pair(identifier, tmp_path):
        assert row["steers_applied"] == 1
        assert row["worker_calls"] == 2
        assert row["total_checks_started"] == 2
        assert not row["checks"][0]["completed_record"]  # Stale check is discarded.
        assert benchmark.STEER not in row["checks"][0]["candidate_input"]["problem"]
        if identifier == "steer_new_episode":
            assert row["status"] == "complete"
            assert row["verification"] == {"status": "correct", "rounds": 1}
            assert row["checker_calls"] == 2 and row["unused_script_steps"] == 0
            current = row["checks"][1]
            assert current["episode"] == 2
            assert current["record"]["check_id"] == "1:check:2"
            assert current["record"]["round"] == 1 and current["record"]["total_checks"] == 2
            assert current["record"]["result_generation"] == 1
            assert current["record"]["candidate_sha256"] != benchmark.candidate_digest(row["checks"][0]["candidate_input"])
            assert benchmark.STEER in current["candidate_input"]["problem"]
            assert row["final_fields"]["value"] == 20 and row["final_oracle_errors"] == []
            assert row["model_calls"] == (5 if row["mode"] == "normal" else 4)
        else:
            assert row["status"] == "error" and row["verification"]["status"] == "limit_reached"
            assert row["model_calls"] == row["model_limit"] == 3
            # The counter records entry into a check; the budget counts actual
            # admitted provider attempts, and the denied check has no receipt.
            assert row["checker_calls"] == 1 and row["unused_script_steps"] == 1
            assert row["final_writer_calls"] == 0 and row["final_text"] == ""
            assert "model attempt limit" in row["notice"]
