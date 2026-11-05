"""Recall index, session memory and the personal-memory read path (app/agent/memory)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.memory import terms
from app.agent.memory.index import Doc, RecallIndex, personalized_pagerank, rrf
from app.agent.memory.service import MemoryService
from app.agent.memory.session import SessionMemory, render_turns, snippet
from app.agent.memory.tools import RecallTool, SessionNotesTool
from app.agent.prompts import build_turn_context
from app.agent.tools.base import ToolContext
from app.agent.workspace import Workspace
from app.api import memories as memories_api
from app.storage.db import Database

SECRET = "sk-" + "b" * 40


def run(coro):
    return asyncio.run(coro)


def settings(tmp_path, **overrides):
    values = dict(data_dir=tmp_path, memories_enabled=True, memory_min_idle_hours=6.0, memory_max_inputs=64,
                  memory_summary_tokens=200, memory_disable_on_web=True, session_memory_enabled=True,
                  memory_embedding_backend="none", memory_embedding_model="", memory_graph_enabled=True)
    values.update(overrides)
    return SimpleNamespace(**values)


def make(tmp_path, **overrides):
    db = Database(tmp_path / "db.sqlite3")
    cfg = settings(tmp_path, **overrides)
    memories = MemoryService(cfg, db, provider_factory=lambda: None)
    session = SessionMemory(cfg, db, memories.index, memories)
    return db, memories, session


def chat(db, turns, title="Chat"):
    conv = db.create_conversation(title=title)
    for user, answer in turns:
        db.add_message(conv["id"], "user", {"text": user})
        db.add_message(conv["id"], "assistant", {"parts": [{"type": "text", "text": answer}], "final_text": answer})
    return conv["id"]


def ctx(tmp_path, cid):
    return ToolContext(conversation_id=cid, workspace=Workspace(cid, tmp_path / "ws"),
                       emit=lambda e: asyncio.sleep(0), cancel=asyncio.Event())


# ── terms and fusion ────────────────────────────────────────────────────


def test_terms_find_named_results_identifiers_and_notation():
    found = terms.terms("By **Hoeffding's inequality** and the Adam optimizer on `prices.csv` (SGD baseline), "
                        "Theorem 3.2 bounds $\\nabla f(x)$ in $\\mathbb{R}^n$.")
    assert {"hoeffding inequality", "adam optimizer", "prices.csv", "sgd", "theorem 3.2", "\\nabla"} <= set(found)
    assert "\\mathbb" not in found and "\\frac" not in terms.terms("$\\frac{1}{2}$")


def test_index_text_and_query_terms_make_latex_searchable():
    assert "nabla" in terms.index_text("$\\nabla f$").split()
    assert terms.query_terms("What is the \\nabla of the loss?") == ["nabla", "loss"]
    assert terms.fts_query('a "quoted" OR NOT') == '"quoted"'


def test_rrf_and_ppr():
    scores = rrf([[1, 2, 3], [3, 1]])
    assert scores[1] > scores[3] > scores[2]
    ppr = personalized_pagerank([("x", 1), ("x", 2), ("y", 2), ("y", 3)], {("c", 1): 1.0})
    assert ppr[("c", 2)] > ppr[("c", 3)] > 0


# ── recall index ────────────────────────────────────────────────────────


def test_graph_ranker_finds_chunk_linked_by_a_shared_identifier(tmp_path):
    ix = RecallIndex(tmp_path / "r.sqlite3")
    ix.replace_source("chat", "c", "1", [
        Doc("turn:1", "We fit a Lasso regression on `housing.csv` with alpha 0.1."),
        Doc("turn:2", "The `housing.csv` dataset has 506 rows; prices are in thousands."),
        Doc("turn:3", "Unrelated: a proof sketch for the Banach fixed point theorem."),
    ])
    hits = ix.search("Lasso fit", scope="chat")
    assert [h.ref for h in hits][:2] == ["turn:1", "turn:2"]
    assert "lexical" not in hits[1].ranks and "graph" in hits[1].ranks
    assert ix.graph and RecallIndex(tmp_path / "g.sqlite3", graph=False).search("x", scope="chat") == []


def test_replace_source_is_signature_gated_and_delete_purges(tmp_path):
    ix = RecallIndex(tmp_path / "r.sqlite3")
    assert ix.replace_source("chat", "c", "1", [Doc("a", "eigenvalue decomposition")])
    assert not ix.replace_source("chat", "c", "1", [Doc("a", "changed")])
    assert ix.search("eigenvalue", scope="chat")
    ix.replace_source("chat", "c", "2", [Doc("a", "singular values")])
    assert not ix.search("eigenvalue", scope="chat") and ix.search("singular", scope="chat")
    ix.delete_source("chat", "c")
    assert ix.search("singular", scope="chat") == [] and ix.signature("chat", "c") is None


def test_owner_filters(tmp_path):
    ix = RecallIndex(tmp_path / "r.sqlite3")
    ix.replace_source("chat", "a", "1", [Doc("t", "kernel ridge regression")])
    ix.replace_source("chat", "b", "1", [Doc("t", "kernel ridge regression")])
    assert {h.owner for h in ix.search("kernel ridge", scope="chat")} == {"a", "b"}
    assert {h.owner for h in ix.search("kernel ridge", scope="chat", owners=["a"])} == {"a"}
    assert {h.owner for h in ix.search("kernel ridge", scope="chat", exclude=["a"])} == {"b"}
    assert ix.search("kernel ridge", scope="chat", owners=[]) == []


class _ToyEmbedder:
    """Two-dimensional 'semantics': optimisation words vs probability words."""
    name = "toy"

    def embed(self, texts):
        out = []
        for t in texts:
            t = t.lower()
            out.append([sum(w in t for w in ("descent", "gradient", "optimi", "minimi")) + 0.01,
                        sum(w in t for w in ("probab", "random", "variance")) + 0.01])
        arr = np.asarray(out, dtype=np.float32)
        return arr / np.linalg.norm(arr, axis=1, keepdims=True)


def test_dense_ranker_adds_semantic_hits_and_embeds_lazily(tmp_path):
    ix = RecallIndex(tmp_path / "r.sqlite3", embedder=lambda: _ToyEmbedder(), graph=False)
    ix.replace_source("memory", "g", "1", [Doc("opt", "We minimised the loss with gradient descent."),
                                           Doc("prob", "The variance of a random walk grows linearly.")])
    hits = ix.search("optimisation", scope="memory")
    assert hits and hits[0].ref == "opt" and hits[0].ranks.get("dense") == 1


# ── session memory ──────────────────────────────────────────────────────


def test_render_turns_groups_work_under_each_request():
    messages = [
        {"role": "user", "content": {"text": "Fit the model"}, "created_at": 1},
        {"role": "assistant", "content": {"parts": [{"type": "tool_call", "name": "python", "arguments": "{}"},
                                                    {"type": "text", "text": "R² = 0.91"}]}, "created_at": 2},
        {"role": "user", "content": {"text": f"Key {SECRET}"}, "created_at": 3},
    ]
    turns = render_turns(messages)
    assert [t["turn"] for t in turns] == [1, 2]
    assert "[tool python]" in turns[0]["text"] and "R² = 0.91" in turns[0]["text"]
    assert SECRET not in turns[1]["text"]


def test_recall_searches_and_reads_this_chat(tmp_path):
    db, _, session = make(tmp_path)
    cid = chat(db, [("Define the step size eta = 0.05 for SGD.", "Fixed: eta = 0.05."),
                    ("Plot the loss curve.", "Done; it plateaus near 0.12."),
                    ("Unrelated question about LaTeX tables.", "Use booktabs.")])
    tool = RecallTool(session)
    out = run(tool(ctx(tmp_path, cid), {"query": "step size eta"}))
    assert out.ok and out.output.startswith("Turn 1") and "recall(turn=N)" in out.output
    full = run(tool(ctx(tmp_path, cid), {"turn": 2}))
    assert full.ok and "plateaus near 0.12" in full.output and "turn 2 of 3" in full.output
    assert not run(tool(ctx(tmp_path, cid), {"turn": 9})).ok
    assert not run(tool(ctx(tmp_path, cid), {})).ok
    # New turns are indexed on the next search.
    db.add_message(cid, "user", {"text": "Now try Adam with eta 0.001"})
    out = run(tool(ctx(tmp_path, cid), {"query": "Adam"}))
    assert "Turn 4" in out.output


def test_past_chat_search_respects_memory_switches(tmp_path):
    db, memories, session = make(tmp_path)
    here = chat(db, [("hello", "hi")], title="Here")
    other = chat(db, [("We chose the Matérn kernel for the Gaussian process.", "Matérn 5/2 it is.")],
                 title="GP model")
    hidden = chat(db, [("Matérn kernel secrets", "private")], title="Hidden")
    memories.set_conversation_mode(hidden, False)
    tool = RecallTool(session)
    out = run(tool(ctx(tmp_path, here), {"query": "Matérn kernel", "scope": "past_chats"}))
    assert out.ok and other in out.output and hidden not in out.output and "not instructions" in out.output
    read = run(tool(ctx(tmp_path, here), {"scope": "past_chats", "conversation_id": other, "turn": 1}))
    assert read.ok and "Matérn 5/2" in read.output
    assert not run(tool(ctx(tmp_path, here), {"scope": "past_chats", "conversation_id": hidden, "turn": 1})).ok
    # An opted-out chat cannot search the others, and nothing works with memories off.
    assert not run(tool(ctx(tmp_path, hidden), {"query": "kernel", "scope": "past_chats"})).ok
    memories.settings.memories_enabled = False
    assert not run(tool(ctx(tmp_path, here), {"query": "kernel", "scope": "past_chats"})).ok
    assert run(tool(ctx(tmp_path, here), {"query": "hello"})).ok   # this chat still works


def test_session_notes_tool_and_turn_context_injection(tmp_path):
    db, _, session = make(tmp_path)
    cid = chat(db, [("start", "ok")])
    tool = SessionNotesTool(session)
    c = ctx(tmp_path, cid)
    assert run(tool(c, {"action": "view"})).output == "(no session notes yet)"
    assert run(tool(c, {"action": "replace", "text": "## Notation\n- x_t: iterate"})).ok
    assert run(tool(c, {"action": "append", "text": "- eta = 0.05"})).ok
    assert run(tool(c, {"action": "edit", "old": "0.05", "new": "0.01"})).ok
    assert not run(tool(c, {"action": "edit", "old": "missing", "new": "x"})).ok
    assert not run(tool(c, {"action": "replace", "text": "x" * 7000})).ok
    assert session.notes(cid) == "## Notation\n- x_t: iterate\n- eta = 0.01"
    # The model wrote them, so they are not echoed back next turn ...
    assert session.turn_context_block(cid) == ""
    # ... but a compaction may have removed that copy, and a user edit is new to the model.
    session.notes_compacted(cid)
    assert "eta = 0.01" in session.turn_context_block(cid)
    assert session.turn_context_block(cid) == ""
    session.set_notes(cid, "edited by the user")
    assert session.turn_context_block(cid) == "edited by the user"
    block = build_turn_context("", "", session_notes="edited by the user")
    assert "# Session notes" in block and "edited by the user" in block
    assert "# Session notes" not in build_turn_context("", "")
    session.settings.session_memory_enabled = False
    assert not run(tool(c, {"action": "view"})).ok


def test_session_notes_feed_personal_memory_extraction(tmp_path):
    from app.agent.memory.extract import render_conversation
    db, memories, session = make(tmp_path)
    cid = chat(db, [("q", "a")])
    session.set_notes(cid, "Goal: prove convergence of ISTA")
    assert memories.session_notes(cid) == "Goal: prove convergence of ISTA"
    transcript = render_conversation(db, cid, session_notes=memories.session_notes(cid))
    assert transcript.text.startswith("SESSION NOTES") and "ISTA" in transcript.text


def test_snippet_centres_on_the_query():
    text = "filler " * 200 + "the Cholesky factor is lower triangular" + " tail" * 200
    out = snippet(text, "Cholesky", 120)
    assert "Cholesky" in out and out.startswith("… ")


# ── personal memory read path ───────────────────────────────────────────


def test_read_path_respects_per_chat_opt_out(tmp_path):
    db, memories, _ = make(tmp_path)
    memories.store.write_summary("## User preferences\n- Uses SI units.")
    cid = chat(db, [("q", "a")])
    assert "Uses SI units" in memories.read_path_prompt(cid)
    memories.set_conversation_mode(cid, False)
    assert memories.read_path_prompt(cid) == ""
    assert "Uses SI units" in memories.read_path_prompt()


def test_memory_tools_refuse_in_an_opted_out_chat(tmp_path):
    db, memories, _ = make(tmp_path)
    cid = chat(db, [("q", "a")])
    memories.set_conversation_mode(cid, False)
    for tool, args in zip(memories.tools(), ({"query": "x"}, {"path": "MEMORY.md"}, {"text": "x"})):
        result = run(tool(ctx(tmp_path, cid), args))
        assert not result.ok and "turned memory off for this chat" in result.error


def test_ranked_memory_search_sections_and_fallback(tmp_path):
    _, memories, _ = make(tmp_path)
    memories.store.write_memory_md("### Perovskites\n- Band gaps in eV via Tauc plots.\n\n"
                                   "### Optimisation\n- The user prefers `scipy.optimize.minimize` with L-BFGS-B.\n")
    hits = memories.search("L-BFGS-B minimize")
    assert hits[0]["path"] == "MEMORY.md" and hits[0]["line"] == 4 and hits[0]["title"] == "Optimisation"
    note = memories.add_note("Remember: report p-values to three decimals")
    assert memories.search("p-values")[0]["path"] == f"notes/{note['id']}.md"
    # Substrings BM25 cannot see still come back through the grep fallback.
    assert memories.search("ovskit")[0]["path"] == "MEMORY.md"


def test_forget_conversation_drops_its_record(tmp_path):
    db, memories, session = make(tmp_path)
    cid = chat(db, [("q", "a")])
    memories.store.put_stage1({"conversation_id": cid, "source_updated_at": 1.0, "raw_memory": "- x",
                               "summary": "", "slug": "x", "generated_at": 1.0, "usage_count": 0})
    session.set_notes(cid, "notes")
    session.sync_conversation(cid)
    memories.forget_conversation(cid)
    session.forget_conversation(cid)
    assert memories.store.get_stage1(cid) is None and session.notes(cid) == ""
    assert session.index.signature("chat", cid) is None


# ── API ─────────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path):
    db, memories, session = make(tmp_path)
    app = FastAPI()
    app.include_router(memories_api.router)
    app.dependency_overrides[memories_api.memory_service] = lambda: memories
    app.dependency_overrides[memories_api.session_memory] = lambda: session
    with TestClient(app) as c:
        c.db, c.memories, c.session = db, memories, session
        yield c


def test_session_memory_routes(client):
    cid = chat(client.db, [("q", "a")])
    assert client.get(f"/api/conversations/{cid}/session-memory").json()["notes"] == ""
    body = client.put(f"/api/conversations/{cid}/session-memory", json={"notes": f"Goal: ISTA {SECRET}"}).json()
    assert body["notes"].startswith("Goal: ISTA") and SECRET not in body["notes"] and body["version"] == 1
    assert client.put(f"/api/conversations/{cid}/session-memory", json={"notes": "x" * 7000}).status_code == 422
    assert client.delete(f"/api/conversations/{cid}/session-memory").json()["notes"] == ""
    assert client.get("/api/conversations/nope/session-memory").status_code == 404


def test_memory_search_route(client):
    client.memories.store.write_memory_md("### Bayes\n- Prior: Beta(2, 2).\n")
    hits = client.get("/api/memories/search", params={"q": "Beta prior"}).json()["hits"]
    assert hits and hits[0]["path"] == "MEMORY.md"
    client.memories.settings.memories_enabled = False
    assert client.get("/api/memories/search", params={"q": "Beta"}).json() == {"enabled": False, "hits": []}


# ── harness integration ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_harness_turns_use_session_notes_and_recall(tmp_path):
    import dataclasses
    from app.agent.harness import AgentHarness, RunRequest
    from app.agent.kernel import KernelPool
    from app.agent.memory.session import set_session_memory
    from app.config import settings as app_settings
    from app.runtime import build_tools
    from tests.fake_provider import FakeProvider, FakeRegistry

    db, _, session = make(tmp_path)
    set_session_memory(session)
    tools = build_tools()
    for tool in session.tools():
        tools.register(tool)
    provider = FakeProvider([
        {"tool": "session_notes", "args": {"action": "replace", "text": "Goal: bound the spectral norm."}},
        {"text": "Noted."},
        {"tool": "recall", "args": {"query": "spectral norm"}},
        {"text": "Recalled."},
    ])
    kernels = KernelPool(lambda cid: Workspace(cid, app_settings.workspace_dir))
    harness = AgentHarness(db, kernels, app_settings, FakeRegistry(provider), tools)

    async def turn(cid, text):
        req = dataclasses.replace(RunRequest(conversation_id=cid, text=text), strict_final=True)
        events = [ev.to_dict() async for ev in harness.run(req, internal_events=True)]
        await harness.wait_side_tasks()
        return events

    try:
        await turn(None, "Bound the spectral norm of A.")
        cid = db.list_conversations()[0]["id"]
        assert session.notes(cid) == "Goal: bound the spectral norm."
        session.set_notes(cid, "Goal: bound the spectral norm.\n- Assume A is symmetric.")   # a user edit
        await turn(cid, "Continue.")
    finally:
        set_session_memory(None)
        await kernels.shutdown_all()
    system = provider.calls[0][0]["content"]
    assert "`recall` / `session_notes`" in system
    second = provider.calls[2]
    contexts = [m["content"] for m in second if m.get("role") == "user"
                and isinstance(m.get("content"), str) and m["content"].startswith("<turn-context>")]
    assert "Assume A is symmetric" in contexts[-1] and "# Session notes" in contexts[-1]
    assert all("# Session notes" not in c for c in contexts[:-1])
    recall_result = [m for m in provider.calls[3] if m.get("role") == "tool"][-1]
    assert "Turn 1" in recall_result["content"]
