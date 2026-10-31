from app.agent.session.critical_path import CriticalPath, turn_critical_path
from app.agent.session.telemetry import project_trajectory

T0 = 1_000.0


def ev(kind, at, **data):
    return {"seq": 0, "time": T0 + at, "type": kind, "data": {"turn": 1, **data}}


def attempt(start, end, step=1, kind="assistant/message"):
    return ev(kind, end, step=step, started_at=T0 + start, completed_at=T0 + end, duration_ms=(end - start) * 1000,
              stream=[], message={"role": "assistant", "content": ""})


def call(cid, start, step=1):
    return ev("tool/call", start, step=step, callId=cid, name="t", arguments="{}")


def result(cid, end, step=1, **meta):
    return ev("tool/result", end, step=step, callId=cid, name="t", ok=True, meta=meta,
              message={"role": "tool", "tool_call_id": cid, "content": ""})


def turn(*events, end=10.0):
    return [ev("turn/start", 0), *events, ev("turn/end", end, reason="completed")]


def test_sequential_turn_attributes_inference_tools_and_overhead():
    path = turn_critical_path(turn(attempt(0.5, 2), call("a", 2.5), result("a", 5), attempt(5, 9)))
    assert path == {"status": "complete", "duration_ms": 10_000, "inference_ms": 5_500, "auto_review_ms": 0,
                    "user_wait_ms": 0, "tools_ms": 2_500, "overhead_ms": 2_000, "unknown_ms": 0}


def test_parallel_batch_counts_only_the_longest_branch():
    path = turn_critical_path(turn(attempt(0, 1), call("a", 1), call("b", 1), call("c", 1.5),
                                   result("a", 3), result("c", 6), result("b", 4), attempt(6, 10)))
    # Only the branch that released the join (c: 1.5..6) is on the path, not
    # 2 + 3 + 4.5; its late start is unrecorded dispatch overhead.
    assert path["tools_ms"] == 4_500 and path["overhead_ms"] == 500
    assert path["inference_ms"] == 5_000


def test_sequential_groups_in_one_step_are_separate_batches():
    path = turn_critical_path(turn(attempt(0, 1), call("a", 1), result("a", 3), call("b", 4), result("b", 6),
                                   attempt(6, 10)))
    assert path["tools_ms"] == 4_000 and path["overhead_ms"] == 1_000


def test_guardian_review_and_user_wait_are_carved_out_of_the_tool():
    review = ev("llm/side-task", 4, task="guardian", task_id="g", call_id="a", started_at=T0 + 2,
                completed_at=T0 + 4)
    path = turn_critical_path(turn(attempt(0, 1), call("a", 1), review, result("a", 9, approval_wait_ms=3000),
                                   attempt(9, 10)))
    assert path["auto_review_ms"] == 2_000
    assert path["user_wait_ms"] == 3_000
    assert path["tools_ms"] == 3_000  # 1..2 and 7..9
    assert path["inference_ms"] == 2_000


def test_compaction_counts_as_inference():
    compaction = ev("llm/side-task", 5, task="compaction", task_id="k", started_at=T0 + 2, completed_at=T0 + 5)
    path = turn_critical_path(turn(attempt(0, 2), compaction, attempt(5, 10)))
    assert path["inference_ms"] == 10_000 and path["overhead_ms"] == 0


def test_missing_measurement_is_incomplete_never_zero():
    open_call = turn_critical_path(turn(attempt(0, 1), call("a", 1)))
    assert open_call["status"] == "incomplete" and open_call["duration_ms"] == 10_000
    assert all(open_call[k] is None for k in ("inference_ms", "tools_ms", "user_wait_ms", "overhead_ms",
                                               "unknown_ms"))
    untimed = ev("assistant/message", 2, step=1, stream=[], message={})
    assert turn_critical_path(turn(untimed))["status"] == "incomplete"
    running = turn_critical_path([ev("turn/start", 0), attempt(0, 1)])
    assert running == {**running, "status": "incomplete", "duration_ms": None, "inference_ms": None}
    unfinished_review = ev("llm/side-task", 2, task="guardian", task_id="g", started_at=T0 + 1, status="started")
    assert turn_critical_path(turn(unfinished_review))["status"] == "incomplete"


def test_legacy_replays_are_ignored():
    legacy = ev("assistant/message", 5, legacy=True, message={})
    assert turn_critical_path(turn(legacy))["status"] == "complete"


def test_path_join_prefers_latest_and_clips_overlap():
    base = CriticalPath().work(0, 1, "inference")
    a, b = base.work(1, 3, "tools"), base.work(1, 5, "tools")
    joined = a.latest(b)
    assert joined.end == 5
    assert joined.work(4, 6, "inference").durations(0, 6) == {
        "inference": 2, "auto_review": 0, "user_wait": 0, "tools": 4, "overhead": 0, "unknown": 0}


def test_project_trajectory_includes_critical_path_per_turn():
    events = [ev("turn/start", 0), ev("step/start", 0, step=1), attempt(0, 2), ev("step/end", 2, step=1),
              ev("turn/end", 3, reason="completed")]
    for seq, event in enumerate(events):
        event["seq"] = seq
    projected = project_trajectory(events)
    path = projected["turns"][0]["critical_path"]
    assert path["status"] == "complete" and path["inference_ms"] == 2_000 and path["overhead_ms"] == 1_000
