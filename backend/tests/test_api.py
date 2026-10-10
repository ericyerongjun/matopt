import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
@pytest.mark.parametrize("option, expected", [({}, True), ({"web_search": True}, True), ({"web_search": False}, False)])
async def test_chat_web_search_defaults_to_automatic(option, expected, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from app.agent.events import ev
    from app.api import chat as chat_api
    from app.agent.runs import RunManager
    from app.storage.db import Database

    received = []

    async def run(req, *, on_claim=None):
        if on_claim is not None:
            await on_claim(req.conversation_id)
        received.append(req)
        yield ev("done")

    db = Database(tmp_path / "runs.sqlite3")
    harness = SimpleNamespace(run=run, is_running=lambda _: False, stop=lambda _: False)
    runtime = SimpleNamespace(
        providers=SimpleNamespace(resolve=lambda *_: None),
        harness=harness, db=db, runs=RunManager(db, harness),
    )
    monkeypatch.setattr(chat_api, "get_runtime", lambda: runtime)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/chat/stream", json={"message": "What is happening today?", **option})
    assert response.status_code == 200
    assert received[0].web_search is expected
    await runtime.runs.shutdown()
    db.close()


@pytest.mark.asyncio
async def test_conversation_and_files_roundtrip():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/providers")
        assert r.status_code == 200
        cat = r.json()
        assert [p["id"] for p in cat["providers"]] == ["deepseek"]
        assert [m["id"] for m in cat["providers"][0]["models"]] == ["deepseek-flash"]
        assert cat["providers"][0]["models"][0]["reasoning_levels"] == ["off", "low", "high", "max"]

        r = await client.post("/api/conversations", json={"title": "t"})
        assert r.status_code == 200
        cid = r.json()["id"]

        r = await client.post(f"/api/conversations/{cid}/files", files=[("files", ("data.csv", b"a,b\n1,2\n3,4\n", "text/csv"))])
        assert r.status_code == 200
        f = r.json()["files"][0]
        assert f["path"] == "uploads/data.csv" and "2 rows" in f["card"]

        r = await client.get(f"/api/conversations/{cid}/files")
        assert any(x["path"] == "uploads/data.csv" for x in r.json()["files"])

        r = await client.get(f"/api/files/{cid}/uploads/data.csv")
        assert r.status_code == 200 and r.text.startswith("a,b")
        r = await client.get(f"/api/files/{cid}/../../etc/passwd")
        assert r.status_code in (404, 400)

        r = await client.get(f"/api/conversations/{cid}/export?format=markdown")
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
        r = await client.get(f"/api/conversations/{cid}/export?format=ipynb")
        assert r.status_code == 200 and "cells" in r.text

        # no provider configured -> 400 on stream
        r = await client.post("/api/chat/stream", json={"conversation_id": cid, "message": "hi"})
        assert r.status_code == 400

        r = await client.post(f"/api/chat/{cid}/steer", json={"message": "try a simpler proof"})
        assert r.status_code == 409

        r = await client.delete(f"/api/conversations/{cid}")
        assert r.status_code == 200
        r = await client.get(f"/api/conversations/{cid}")
        assert r.status_code == 404

        r = await client.get("/api/skills")
        assert r.status_code == 200
        names = {s["name"] for s in r.json()["skills"]}
        assert "pdf" in names and "sympy-compute" in names
        assert "lambda-eda" in names and "lambda-report" in names and "lambda-slides" in names
        r = await client.get("/api/skills/sympy-compute")
        assert r.status_code == 200 and "symbolic" in r.json()["body"].lower()

        r = await client.get("/api/tools")
        assert r.status_code == 200
        tool_names = {t["name"] for t in r.json()["tools"]}
        assert "python" in tool_names and "bash" in tool_names
        assert "load_skill" not in tool_names
