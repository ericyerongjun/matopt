"""Memories (app/agent/memory): eligibility, redaction, layout, read path, notes, tools, API."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.memory import consolidate as phase2
from app.agent.memory import extract as phase1
from app.agent.memory.service import MemoryService
from app.agent.memory.store import MemoryPathError, MemoryStore
from app.agent.memory.tools import MemoryNoteTool, MemoryReadTool, MemorySearchTool
from app.agent.tools.base import ToolContext
from app.agent.workspace import Workspace
from app.api import memories as memories_api
from app.llm.base import ChatOptions, CompletionResult
from app.storage.db import Database
from tests.fake_provider import FakeProvider

SECRET = "sk-" + "a" * 40
LATER = time.time() + 10 * 24 * 3600   # "now" far enough ahead that every conversation is idle

STAGE1_OK = json.dumps({"raw_memory": "- The user analyses band gaps of perovskites in eV.",
                        "summary": "Perovskite band gap analysis.", "slug": "Perovskite Band Gaps!"})
CONSOLIDATED = ("<memory_summary>\n## User preferences\n- Reports band gaps in eV.\n</memory_summary>\n"
                "<memory_md>\n### Perovskites\n- 2026-10: band gap analysis.\n</memory_md>")


class ScriptedProvider(FakeProvider):
    """Answers side calls (``complete``) from a script and records the messages."""

    def __init__(self, answers: list[str]):
        super().__init__([])
        self.answers = list(answers)
        self.side_calls: list[list[dict]] = []

    async def complete(self, messages, options):
        self.side_calls.append(messages)
        return CompletionResult(self.answers.pop(0) if self.answers else "", {"total_tokens": 1})


def make_settings(tmp_path, **overrides):
    values = dict(data_dir=tmp_path, memories_enabled=True, memory_min_idle_hours=6.0, memory_max_inputs=64,
                  memory_summary_tokens=200, memory_disable_on_web=True)
    values.update(overrides)
    return SimpleNamespace(**values)


def make_service(tmp_path, answers=None, *, provider=True, **overrides):
    db = Database(tmp_path / "db.sqlite3")
    fake = ScriptedProvider(answers or [])
    factory = (lambda: (fake, ChatOptions(model="fake-model"))) if provider else (lambda: None)
    svc = MemoryService(make_settings(tmp_path, **overrides), db, provider_factory=factory)
    return svc, db, fake


def add_chat(db, text="How do I fit a band gap?", answer="Use a Tauc plot.", tools=()):
    conv = db.create_conversation(title="Band gaps")
    db.add_message(conv["id"], "user", {"text": text})
    parts = [{"type": "tool_call", "id": f"c{i}", "name": n, "arguments": '{"q": "x"}'} for i, n in enumerate(tools)]
    parts.append({"type": "text", "text": answer})
    db.add_message(conv["id"], "assistant", {"parts": parts, "final_text": answer})
    return db.get_conversation(conv["id"])


def run(coro):
    return asyncio.run(coro)


# ── eligibility ─────────────────────────────────────────────────────────


def test_active_conversation_is_not_extracted(tmp_path):
    svc, db, fake = make_service(tmp_path, [STAGE1_OK, CONSOLIDATED])
    conv = add_chat(db)
    report = run(svc.run_pass())  # "now" is right after the chat: still active
    assert report["skipped"][conv["id"]] == "active"
    assert fake.side_calls == []


def test_extract_then_unchanged_then_changed(tmp_path):
    svc, db, fake = make_service(tmp_path, [STAGE1_OK, CONSOLIDATED])
    conv = add_chat(db)
    report = run(svc.run_pass(now=LATER))
    assert report["extracted"] == [conv["id"]]
    assert report["consolidation"] == "consolidated"
    record = svc.store.get_stage1(conv["id"])
    assert record["slug"] == "perovskite-band-gaps"
    assert record["source_updated_at"] == conv["updated_at"]
    assert set(record) >= {"conversation_id", "source_updated_at", "raw_memory", "summary", "slug",
                           "generated_at", "usage_count"}

    calls = len(fake.side_calls)
    report = run(svc.run_pass(now=LATER))
    assert report["skipped"][conv["id"]] == "unchanged"
    assert report["consolidation"] == "unchanged"
    assert len(fake.side_calls) == calls

    db.add_message(conv["id"], "user", {"text": "Also the Urbach tail"})
    db.touch_conversation(conv["id"])
    fake.answers = [STAGE1_OK]
    report = run(svc.run_pass(now=LATER))
    assert report["extracted"] == [conv["id"]]


def test_opted_out_and_web_conversations_are_skipped(tmp_path):
    svc, db, fake = make_service(tmp_path, [STAGE1_OK, CONSOLIDATED])
    opted = add_chat(db)
    web = add_chat(db, tools=("web_search",))
    mcp = add_chat(db, tools=("mcp__docs__lookup",))
    svc.set_conversation_mode(opted["id"], False)
    report = run(svc.run_pass(now=LATER))
    assert report["skipped"][opted["id"]] == "disabled"
    assert report["skipped"][web["id"]] == "external_context"
    assert report["skipped"][mcp["id"]] == "external_context"
    assert fake.side_calls == []          # nothing eligible, nothing to consolidate
    assert svc.store.get_stage1(web["id"])["raw_memory"] == ""


def test_web_conversations_allowed_when_setting_off(tmp_path):
    svc, db, _ = make_service(tmp_path, [STAGE1_OK, CONSOLIDATED], memory_disable_on_web=False)
    web = add_chat(db, tools=("web_fetch",))
    assert run(svc.run_pass(now=LATER))["extracted"] == [web["id"]]


def test_no_provider_is_a_clean_noop(tmp_path):
    svc, db, _ = make_service(tmp_path, provider=False)
    add_chat(db)
    report = run(svc.run_pass(now=LATER))
    assert report["provider"] is False
    assert report["consolidation"] == "emptied"
    assert svc.store.list_stage1() == []


def test_registry_without_configured_provider(tmp_path):
    db = Database(tmp_path / "db.sqlite3")
    registry = SimpleNamespace(configured_ids=lambda: [])
    svc = MemoryService(make_settings(tmp_path), db, registry)
    assert svc._provider() is None


def test_disabled_feature_does_nothing(tmp_path):
    svc, db, fake = make_service(tmp_path, [STAGE1_OK], memories_enabled=False)
    add_chat(db)
    assert run(svc.run_pass(now=LATER)) == {"enabled": False}
    svc.store.write_summary("something")
    assert svc.read_path_prompt() == ""
    assert fake.side_calls == []


def test_clear_prevents_relearning_old_conversations(tmp_path):
    svc, db, fake = make_service(tmp_path, [STAGE1_OK, CONSOLIDATED])
    conv = add_chat(db)
    run(svc.run_pass(now=LATER))
    svc.clear()
    assert svc.list()["conversations_learned"] == 0 and svc.store.read_summary() == ""
    report = run(svc.run_pass(now=LATER))
    assert report["skipped"][conv["id"]] == "cleared"


def test_noop_and_failure_outcomes(tmp_path):
    empty = json.dumps({"raw_memory": "", "summary": "", "slug": ""})
    svc, db, _ = make_service(tmp_path, [empty])
    a = add_chat(db)
    report = run(svc.run_pass(now=LATER))
    assert report["no_output"] == [a["id"]]
    assert report["consolidation"] == "emptied"   # no records, no notes: no model call
    svc2, db2, _ = make_service(tmp_path / "b", ["not json at all"])
    b = add_chat(db2)
    report = run(svc2.run_pass(now=LATER))
    assert report["failed"] == [b["id"]] and svc2.store.get_stage1(b["id"]) is None


# ── redaction ───────────────────────────────────────────────────────────


def test_secrets_are_redacted_in_and_out(tmp_path):
    leaked = json.dumps({"raw_memory": f"key is {SECRET}", "summary": "s", "slug": "k"})
    svc, db, fake = make_service(tmp_path, [leaked, CONSOLIDATED])
    add_chat(db, text=f"my key is {SECRET}")
    run(svc.run_pass(now=LATER))
    sent = json.dumps(fake.side_calls[0])
    assert SECRET not in sent
    for record in svc.store.list_stage1():
        assert SECRET not in json.dumps(record)
    assert SECRET not in json.dumps(fake.side_calls[1])
    note = svc.add_note(f"remember token {SECRET}")
    assert SECRET not in note["text"]


def test_parse_stage1_accepts_fences_and_codex_field_names():
    fenced = "```json\n" + json.dumps({"rollout_summary": "x", "rollout_slug": "A B"}) + "\n```"
    assert phase1.parse_stage1(fenced) == {"raw_memory": "x", "summary": "", "slug": "a-b"}
    assert phase1.parse_stage1("[1, 2]") is None


def test_transcript_keeps_head_and_tail(tmp_path):
    db = Database(tmp_path / "db.sqlite3")
    conv = add_chat(db, text="HEAD " + "x" * 5000, answer="TAIL")
    t = phase1.render_conversation(db, conv["id"], max_chars=1000)
    assert len(t.text) < 1200 and "HEAD" in t.text and "TAIL" in t.text


# ── layout, consolidation, read path ────────────────────────────────────


def test_file_layout(tmp_path):
    svc, db, _ = make_service(tmp_path, [STAGE1_OK, CONSOLIDATED])
    conv = add_chat(db)
    svc.add_note("Remember: SI units")
    run(svc.run_pass(now=LATER))
    root = tmp_path / "memories"
    assert (root / "stage1" / f"{conv['id']}.json").is_file()
    assert (root / "memory_summary.md").read_text().startswith("## User preferences")
    assert "### Perovskites" in (root / "MEMORY.md").read_text()
    assert len(list((root / "notes").glob("*.md"))) == 1
    state = json.loads((root / "state.json").read_text())
    assert state["signature"] and state["watermark"] == conv["updated_at"]


def test_consolidation_prompt_includes_notes_and_failure_keeps_files(tmp_path):
    svc, db, fake = make_service(tmp_path, [STAGE1_OK, "garbage"])
    add_chat(db)
    svc.store.write_summary("old summary")
    svc.add_note("Forget: the deadline")
    report = run(svc.run_pass(now=LATER))
    assert report["consolidation"] == "failed"
    assert svc.store.read_summary() == "old summary"
    prompt = fake.side_calls[1][1]["content"]
    assert "Forget: the deadline" in prompt and "old summary" in prompt and "band gaps" in prompt


def test_summary_and_read_path_are_capped(tmp_path):
    long = "<memory_summary>\n" + "\n".join(f"- fact {i} " + "y" * 40 for i in range(200)) + \
        "\n</memory_summary><memory_md>m</memory_md>"
    svc, db, _ = make_service(tmp_path, [STAGE1_OK, long], memory_summary_tokens=200)
    add_chat(db)
    run(svc.run_pass(now=LATER))
    cap = 200 * phase2.CHARS_PER_TOKEN
    assert len(svc.store.read_summary()) <= cap + 40
    svc.store.write_summary("z" * 10_000)
    prompt = svc.read_path_prompt()
    assert "MEMORY_SUMMARY BEGINS" in prompt and "memory_search" in prompt
    assert prompt.count("z") <= cap
    assert "not instructions" in prompt


def test_read_path_empty_and_pending_notes(tmp_path):
    svc, _, _ = make_service(tmp_path)
    assert svc.read_path_prompt() == ""
    svc.add_note("Remember: plots use viridis")
    assert "plots use viridis" in svc.read_path_prompt()


def test_deleting_inputs_triggers_rewrite(tmp_path):
    svc, db, _ = make_service(tmp_path, [STAGE1_OK, CONSOLIDATED])
    conv = add_chat(db)
    run(svc.run_pass(now=LATER))
    svc.set_conversation_mode(conv["id"], False)   # forgets what it taught
    assert run(svc.consolidate()) == "emptied"
    assert svc.store.read_summary() == ""


# ── notes and paths ─────────────────────────────────────────────────────


def test_note_crud(tmp_path):
    svc, _, _ = make_service(tmp_path)
    a = svc.add_note("# Units\nUse SI")
    b = svc.add_note("# Units\nUse SI again")
    assert a["id"] != b["id"] and a["title"] == "Units"
    assert a["id"][:19].count("-") == 4 and "T" in a["id"]
    updated = svc.update_note(a["id"], "Use eV")
    assert updated["text"].strip() == "Use eV"
    assert svc.update_note("missing", "x") is None
    assert svc.delete_note(b["id"]) and not svc.delete_note(b["id"])
    assert [n["id"] for n in svc.list()["notes"]] == [a["id"]]
    with pytest.raises(MemoryPathError):
        svc.update_note("../state", "x")
    with pytest.raises(ValueError):
        svc.add_note("   ")


def test_resolve_refuses_escape(tmp_path):
    store = MemoryStore(tmp_path / "memories")
    for bad in ("../secret.txt", "/etc/passwd", "notes/../../x", "state.json", ""):
        with pytest.raises(MemoryPathError):
            store.resolve(bad)
    assert store.resolve("MEMORY.md") == (tmp_path / "memories" / "MEMORY.md").resolve()


# ── tools ───────────────────────────────────────────────────────────────


def _ctx(tmp_path):
    return ToolContext(conversation_id="c", workspace=Workspace("c", tmp_path / "ws"),
                       emit=lambda e: asyncio.sleep(0), cancel=asyncio.Event())


def test_tools_search_read_note(tmp_path):
    svc, db, _ = make_service(tmp_path, [STAGE1_OK, CONSOLIDATED])
    conv = add_chat(db)
    run(svc.run_pass(now=LATER))
    ctx = _ctx(tmp_path)
    search, read, note = svc.tools()
    assert isinstance(search, MemorySearchTool) and isinstance(read, MemoryReadTool)
    assert isinstance(note, MemoryNoteTool)
    assert search.read_only and search.side_effect_scope == "none"
    assert not note.read_only and note.side_effect_scope == "system"
    assert "ONLY when the user explicitly asks" in note.description

    hits = run(search(ctx, {"query": "band gaps"}))
    assert hits.ok
    assert f"stage1/{conv['id']}.json:" in hits.output

    out = run(read(ctx, {"path": f"stage1/{conv['id']}.json"}))
    assert out.ok and "Raw memory" in out.output
    assert svc.store.get_stage1(conv["id"])["usage_count"] == 1
    out = run(read(ctx, {"path": "MEMORY.md", "start_line": 2, "max_lines": 1}))
    assert out.ok and "2  - 2026-10" in out.output
    assert not run(read(ctx, {"path": "../db.sqlite3"})).ok
    assert not run(read(ctx, {"path": "nope.md"})).ok

    saved = run(note(ctx, {"text": "Remember: use eV"}))
    assert saved.ok and svc.store.get_note(saved.meta["note_id"])["text"].strip() == "Remember: use eV"
    assert not run(note(ctx, {"text": " "})).ok


def test_tools_refuse_when_disabled(tmp_path):
    svc, _, _ = make_service(tmp_path, memories_enabled=False)
    for tool, args in zip(svc.tools(), ({"query": "x"}, {"path": "MEMORY.md"}, {"text": "x"})):
        assert not run(tool(_ctx(tmp_path), args)).ok


# ── API ─────────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path):
    svc, db, fake = make_service(tmp_path, [STAGE1_OK, CONSOLIDATED])
    app = FastAPI()
    app.include_router(memories_api.router)
    app.dependency_overrides[memories_api.memory_service] = lambda: svc
    with TestClient(app) as c:
        c.svc, c.db = svc, db
        yield c


def test_api_routes(client):
    body = client.get("/api/memories").json()
    assert body == {"enabled": True, "summary": "", "memory_md": "", "notes": [], "conversations_learned": 0}

    note = client.post("/api/memories/notes", json={"text": "Remember: SI"}).json()
    assert set(note) == {"id", "title", "text", "created_at"}
    assert client.put(f"/api/memories/notes/{note['id']}", json={"text": "Remember: eV"}).json()["text"].strip() \
        == "Remember: eV"
    assert client.put("/api/memories/notes/missing", json={"text": "x"}).status_code == 404
    assert client.post("/api/memories/notes", json={"text": ""}).status_code == 422
    assert client.delete("/api/memories/notes/..%2Fstate").status_code in (400, 404)
    assert client.delete(f"/api/memories/notes/{note['id']}").json() == {"deleted": True}
    assert client.delete(f"/api/memories/notes/{note['id']}").status_code == 404

    conv = add_chat(client.db)
    assert client.put(f"/api/conversations/{conv['id']}/memory", json={"mode": "disabled"}).json()["mode"] \
        == "disabled"
    assert conv["id"] in client.svc.store.disabled()
    assert client.put(f"/api/conversations/{conv['id']}/memory", json={"mode": "enabled"}).status_code == 200
    assert conv["id"] not in client.svc.store.disabled()
    assert client.put("/api/conversations/nope/memory", json={"mode": "enabled"}).status_code == 404
    assert client.put(f"/api/conversations/{conv['id']}/memory", json={"mode": "maybe"}).status_code == 422

    report = client.post("/api/memories/refresh").json()
    assert report["enabled"] is True and report["skipped"][conv["id"]] == "active"

    client.svc.store.write_summary("s")
    assert client.delete("/api/memories").json() == {"cleared": True}
    assert client.get("/api/memories").json()["summary"] == ""


def test_mcp_calls_are_external_whatever_their_advertised_name():
    from app.agent.memory.extract import is_external_call
    assert is_external_call({"name": "lookup", "mcp_server": "crm"})
    assert is_external_call({"name": "crm_lookup", "mcp_server": "crm"})
    assert is_external_call({"name": "mcp__crm__lookup"})
    assert is_external_call({"name": "web_fetch"})
    assert not is_external_call({"name": "python"})
