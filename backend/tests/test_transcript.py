"""The visible work transcript: ordering, titles and what stays private."""
from app.agent.transcript import Transcript, public_transcript


def test_narration_precedes_its_step_actions_and_rows_carry_code_and_output():
    t = Transcript()
    t.action(1, "c1", "python", '{"code": "print(6*7)"}')
    t.note(1, "I'll compute the product first.")
    t.result("c1", True, "42", None)
    t.action(2, "c2", "read_file", {"path": "uploads/data.csv"})
    t.result("c2", False, None, "No such file")
    entries = t.snapshot()
    assert [e["kind"] for e in entries] == ["note", "action", "action"]
    py, read = entries[1], entries[2]
    assert (py["title"], py["input"], py["language"], py["output"], py["status"]) == ("Ran Python", "print(6*7)", "python", "42", "done")
    assert (read["title"], read["subject"], read["status"], read["output"]) == ("Read", "uploads/data.csv", "error", "No such file")


def test_bookkeeping_hidden_progress_as_note_and_delegated_output_private():
    t = Transcript()
    assert t.action(1, "a", "update_plan", {"steps": []}) is None
    assert t.action(1, "b", "publish_result", {}) is None
    assert t.action(1, "c", "report_progress", {"text": "Data loaded."})["kind"] == "note"
    t.action(1, "d", "data_agent", {"task": "EDA"})
    t.result("d", True, "internal agent exchange", None)
    delegated = [e for e in t.snapshot() if e.get("tool") == "data_agent"][0]
    assert delegated["input"] == "EDA" and "output" not in delegated


def test_running_rows_stop_and_saved_entries_are_revalidated():
    t = Transcript()
    t.action(1, "c", "bash", {"command": "sleep 99"})
    t.stop_running()
    saved = t.snapshot() + [{"kind": "other"}, {"kind": "note", "order": "bad"}]
    clean = public_transcript(saved)
    assert len(clean) == 1 and clean[0]["status"] == "stopped"


def test_run_channel_forwards_validated_transcript_entries_only():
    from app.agent.events import Event
    from app.agent.runs import project_public_event
    t = Transcript()
    entry = t.action(1, "c1", "python", {"code": "print(1)"})
    forwarded = project_public_event(Event("transcript", {"entry": {**entry, "secret": "x"}}))
    assert forwarded is not None and forwarded.data["entry"]["input"] == "print(1)" and "secret" not in forwarded.data["entry"]
    assert project_public_event(Event("transcript", {"entry": {"kind": "bogus"}})) is None
