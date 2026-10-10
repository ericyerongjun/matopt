from __future__ import annotations

import asyncio
import base64
import io
import json
from types import SimpleNamespace

import httpx
import pymupdf
import pytest
from PIL import Image, ImageDraw
from openai import AsyncOpenAI

from app.agent.harness import AgentHarness, RunRequest
from app.agent.kernel import KernelPool
from app.agent.session.telemetry import project_usage
from app.agent.tools.base import ToolContext, ToolRegistry
from app.agent.tools.document_tool import ParseDocumentTool
from app.agent.workspace import Workspace
from app.config import settings
from app.llm.base import CompletionResult
from app.llm.catalog import MODEL_ID, PROVIDERS
from app.llm.openai_compat import OpenAICompatProvider
from app.storage.db import Database
from tests.fake_provider import FakeProvider, FakeRegistry


def png_bytes(size=(640, 240)):
    image = Image.new("RGB", size, "white")
    ImageDraw.Draw(image).text((20, 20), "OCR TEST: 2 + 3 = 5", fill="black")
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def pdf_bytes(*texts, scanned=False):
    with pymupdf.open() as doc:
        for text in texts or ("",):
            page = doc.new_page()
            if scanned:
                page.insert_image(pymupdf.Rect(30, 30, 550, 250), stream=png_bytes())
            elif text:
                page.insert_text((40, 50), text)
        return doc.tobytes()


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "document_ocr_max_dimension", 768)
    monkeypatch.setattr(settings, "document_ocr_max_pages", 2)
    monkeypatch.setattr(settings, "document_ocr_page_timeout", 1.0)
    monkeypatch.setattr(settings, "document_ocr_timeout", 3.0)
    monkeypatch.setattr(settings, "document_auto_parse", True)
    monkeypatch.setattr(settings, "document_ocr_enabled", True)
    events = []

    async def emit(event):
        events.append(event.to_dict())

    return ToolContext("ocr-tests", Workspace("ocr-tests", tmp_path), emit, asyncio.Event(),
                       call_id="ocr-test", settings=settings), events


class OCRProvider(FakeProvider):
    def __init__(self, text="Observed text: $2+3=5$", failure=None, delay=0):
        super().__init__([{"text": "The uploaded page says 2 + 3 = 5."}])
        self.ocr_requests = []
        self.ocr_text = text
        self.failure = failure
        self.delay = delay
        self.cancelled = False

    async def complete(self, messages, options):
        if not isinstance(messages[-1].get("content"), list):
            return CompletionResult("Document test", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        self.ocr_requests.append((messages, options))
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.failure:
                raise self.failure
            return CompletionResult(self.ocr_text, {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}, "stop")
        except asyncio.CancelledError:
            self.cancelled = True
            raise


@pytest.mark.asyncio
async def test_multimodal_transport_uses_actual_normalized_image_and_same_provider(context):
    ctx, events = context
    src = ctx.workspace.save_upload("screenshot.webp", png_bytes((1900, 400)))
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        assert request.url.host == "deepseek.example.test"
        assert request.headers["authorization"] == "Bearer test-placeholder"
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 1,
            "model": MODEL_ID, "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "Equation: $2+3=5$"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})

    provider = OpenAICompatProvider(PROVIDERS["deepseek"], "test-placeholder", "https://deepseek.example.test/v1")
    await provider.client.close()
    provider.client = AsyncOpenAI(api_key="test-placeholder", base_url="https://deepseek.example.test/v1",
                                  http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    ctx.state["document_provider"] = provider
    try:
        result = await ParseDocumentTool().run(ctx, path=ctx.workspace.relpath(src))
    finally:
        await provider.client.close()
    assert result.ok and result.meta["backend"] == "multimodal"
    assert len(seen) == 1 and seen[0]["model"] == MODEL_ID
    assert seen[0]["reasoning_effort"] == "none"
    assert "untrusted" in seen[0]["messages"][0]["content"]
    blocks = seen[0]["messages"][-1]["content"]
    image_url = blocks[1]["image_url"]["url"]
    assert image_url.startswith("data:image/png;base64,")
    with Image.open(io.BytesIO(base64.b64decode(image_url.split(",", 1)[1]))) as normalized:
        assert normalized.width == 768 and normalized.height < 768
    assert src.read_bytes() == png_bytes((1900, 400))
    assert "$2+3=5$" in ctx.workspace.resolve(result.meta["markdown_path"]).read_text()
    assert any(event["type"] == "tool_progress" for event in events)


@pytest.mark.asyncio
async def test_pdf_scans_page_labels_scope_and_page_range(context):
    ctx, _ = context
    provider = OCRProvider()
    ctx.state["document_provider"] = provider
    src = ctx.workspace.save_upload("scan.pdf", pdf_bytes("", "", "", scanned=True))
    result = await ParseDocumentTool().run(ctx, path=ctx.workspace.relpath(src))
    assert result.ok and result.meta["pages_read"] == [1, 2]
    assert result.meta["total_pages"] == 3 and result.meta["vision_pages"] == 2
    assert "pages 3–3 were not read" in result.output
    md = ctx.workspace.resolve(result.meta["markdown_path"]).read_text()
    assert "## Page 1" in md and "## Page 2" in md and "## Page 3" not in md
    second = await ParseDocumentTool().run(ctx, path=ctx.workspace.relpath(src), pages="3")
    assert second.ok and second.meta["pages_read"] == [3]
    assert second.meta["markdown_path"] != result.meta["markdown_path"]
    assert len(provider.ocr_requests) == 3


@pytest.mark.asyncio
async def test_repeated_image_uses_content_cache_without_rebilling(context):
    ctx, _ = context
    provider = OCRProvider()
    ctx.state["document_provider"] = provider
    src = ctx.workspace.save_upload("math.png", png_bytes())
    first = await ParseDocumentTool().run(ctx, path=ctx.workspace.relpath(src))
    second = await ParseDocumentTool().run(ctx, path=ctx.workspace.relpath(src))
    assert first.ok and second.ok and second.meta["cached"]
    assert len(provider.ocr_requests) == 1
    # The original may be replaced by a workspace operation without changing
    # its filename; source bytes, not filename/mtime, define the OCR identity.
    src.write_bytes(png_bytes((800, 300)))
    changed = await ParseDocumentTool().run(ctx, path=ctx.workspace.relpath(src))
    assert changed.ok and not changed.meta["cached"]
    assert changed.meta["markdown_path"] != first.meta["markdown_path"]
    assert len(provider.ocr_requests) == 2


@pytest.mark.asyncio
async def test_native_pdf_fallback_is_labelled_and_not_cached(context):
    ctx, _ = context
    ctx.state["document_provider"] = OCRProvider(failure=RuntimeError("secret request contents should not leak"))
    src = ctx.workspace.save_upload("text.pdf", pdf_bytes("Native fallback: alpha = 7"))
    result = await ParseDocumentTool().run(ctx, path=ctx.workspace.relpath(src))
    assert result.ok and result.meta["native_fallback_pages"] == 1
    assert "Native fallback: alpha = 7" in result.output
    assert "diagrams, handwriting and scanned text" in result.output
    assert "secret request contents" not in result.output
    assert not list((ctx.workspace.root / "parsed").glob(".*.json"))


@pytest.mark.asyncio
async def test_unreadable_image_does_not_claim_success_or_echo_error_payload(context):
    ctx, _ = context
    ctx.state["document_provider"] = OCRProvider(failure=RuntimeError("sk-secret https://secret-endpoint"))
    src = ctx.workspace.save_upload("broken-result.png", png_bytes())
    result = await ParseDocumentTool().run(ctx, path=ctx.workspace.relpath(src))
    assert not result.ok and "No pages could be read" in result.error
    assert "sk-secret" not in result.error and not result.artifacts


@pytest.mark.asyncio
async def test_invalid_bytes_ranges_size_and_pixels_rejected_before_provider(context, monkeypatch):
    ctx, _ = context
    provider = OCRProvider()
    ctx.state["document_provider"] = provider
    bad = ctx.workspace.save_upload("bad.png", b"<svg>not a raster image</svg>")
    result = await ParseDocumentTool().run(ctx, path=ctx.workspace.relpath(bad))
    assert not result.ok and "file bytes" in result.error
    pdf = ctx.workspace.save_upload("one.pdf", pdf_bytes("one"))
    for pages in ("0", "2", "2-1", "bad", "1-4"):
        result = await ParseDocumentTool().run(ctx, path=ctx.workspace.relpath(pdf), pages=pages)
        assert not result.ok
    monkeypatch.setattr(settings, "document_ocr_max_pixels", 10)
    big = ctx.workspace.save_upload("large.png", png_bytes())
    result = await ParseDocumentTool().run(ctx, path=ctx.workspace.relpath(big))
    assert not result.ok and "pixel" in result.error
    monkeypatch.setattr(settings, "document_ocr_max_file_mb", 0)
    result = await ParseDocumentTool().run(ctx, path=ctx.workspace.relpath(pdf))
    assert not result.ok and "MB" in result.error
    assert not provider.ocr_requests


@pytest.mark.asyncio
async def test_ocr_cancellation_and_timeout_cancel_provider(context, monkeypatch):
    ctx, _ = context
    provider = OCRProvider(delay=5)
    ctx.state["document_provider"] = provider
    src = ctx.workspace.save_upload("stop.png", png_bytes())
    task = asyncio.create_task(ParseDocumentTool().run(ctx, path=ctx.workspace.relpath(src)))
    while not provider.ocr_requests:
        await asyncio.sleep(.005)
    ctx.cancel.set()
    result = await asyncio.wait_for(task, 1)
    assert not result.ok and result.meta["cancelled"] and provider.cancelled
    assert not list((ctx.workspace.root / "parsed").glob("*.md"))
    ctx.cancel.clear()
    provider.cancelled = False
    monkeypatch.setattr(settings, "document_ocr_page_timeout", .01)
    result = await ParseDocumentTool().run(ctx, path=ctx.workspace.relpath(src))
    assert not result.ok and "timed out" in result.error and provider.cancelled


@pytest.mark.asyncio
async def test_harness_stop_during_automatic_read_persists_stopped_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "document_auto_parse", True)
    monkeypatch.setattr(settings, "document_ocr_enabled", True)
    monkeypatch.setattr(settings, "skill_auto_activate", False)
    db = Database(tmp_path / "stop.sqlite3")
    cid = db.create_conversation()["id"]
    ws = Workspace(cid, settings.workspace_dir)
    src = ws.save_upload("stop.png", png_bytes())
    provider = OCRProvider(delay=10)
    kernels = KernelPool(lambda cid: Workspace(cid, settings.workspace_dir))
    harness = AgentHarness(db, kernels, settings, FakeRegistry(provider), ToolRegistry([ParseDocumentTool()]))
    events = []

    async def consume():
        async for event in harness.run(RunRequest(conversation_id=cid, text="Read this.", attachments=[{"name": src.name, "path": ws.relpath(src)}])):
            events.append(event.to_dict())

    try:
        task = asyncio.create_task(consume())
        while not provider.ocr_requests and not task.done():
            await asyncio.sleep(.005)
        assert harness.stop(cid)
        await asyncio.wait_for(task, 2)
        assert provider.cancelled and not provider.calls
        assert events[-1]["type"] == "stopped"
        content = db.list_messages(cid)[-1]["content"]
        assert content["status"] == "stopped"
        assert content["parts"][0]["type"] == "tool_call"
        assert content["parts"][0]["result"]["meta"]["cancelled"]
    finally:
        await harness.shutdown()
        await kernels.shutdown_all()
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("ledger", [True, False])
async def test_uploaded_pdf_and_screenshot_are_automatically_understood_without_slash(tmp_path, monkeypatch, ledger):
    from app.agent.runs import RunManager
    import app.api.files as files_api
    import app.api.chat as chat_api
    from app.main import app
    monkeypatch.setattr(settings, "session_log_enabled", ledger)
    monkeypatch.setattr(settings, "skill_auto_activate", False)
    monkeypatch.setattr(settings, "document_auto_parse", True)
    monkeypatch.setattr(settings, "document_ocr_enabled", True)
    db = Database(tmp_path / "test.sqlite3")
    cid = db.create_conversation()["id"]
    provider = OCRProvider()
    provider.script.append({"text": "The uploaded page says 2 + 3 = 5."})
    registry = FakeRegistry(provider)
    kernels = KernelPool(lambda cid: Workspace(cid, settings.workspace_dir))
    harness = AgentHarness(db, kernels, settings, registry, ToolRegistry([ParseDocumentTool()]))
    rt = SimpleNamespace(db=db, harness=harness, runs=RunManager(db, harness), providers=registry, workspace=lambda cid: Workspace(cid, settings.workspace_dir))
    monkeypatch.setattr(files_api, "get_runtime", lambda: rt)
    monkeypatch.setattr(chat_api, "get_runtime", lambda: rt)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            uploaded = await client.post(f"/api/conversations/{cid}/files", files=[
                ("files", ("screenshot.png", png_bytes(), "image/png")),
                ("files", ("scanned.pdf", pdf_bytes("", scanned=True), "application/pdf")),
            ])
            assert uploaded.status_code == 200
            response = await client.post("/api/chat/stream", json={"conversation_id": cid,
                "message": "What do my uploaded documents say?", "attachments": uploaded.json()["files"]})
            assert response.status_code == 200
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        await harness.wait_side_tasks()
        assert len(provider.ocr_requests) == 2
        assert len(provider.calls) == 2  # Work result, then streamed final presentation.
        first_answer_input = provider.calls[0]
        tool_results = [m for m in first_answer_input if m["role"] == "tool"]
        assert len(tool_results) == 2 and all("$2+3=5$" in m["content"] for m in tool_results)
        assert events[-1]["type"] == "done"
        assert not any(e["type"] == "tool_result" for e in events)
        presentation = next(e["content"] for e in events if e["type"] == "message_end")
        assert presentation["work"]["tool_count"] == 2
        assert {"label": "Reading uploaded file", "status": "complete"} in presentation["work"]["steps"]
        assert [p["type"] for p in presentation["parts"]] == ["text"]
        messages = db.list_messages(cid)
        assert len(messages) == 2
        assert [p["type"] for p in messages[-1]["content"]["parts"]] == ["tool_call", "tool_call", "text", "text"]
        if ledger:
            log = await harness._open_session(cid)
            usage = project_usage(log.events)
            assert usage["turns"][0]["exact"]["total_tokens"] == 30
            assert usage["exact"]["total_tokens"] == 66  # 30 work/final + 2*17 OCR + 2 title
            assert usage["coverage"]["attempts"] == 5
            assert not any("data:image" in json.dumps(e) for e in log.events)
            # Simulate restart after OCR committed but before the answer. The
            # materialized view retains both readings and replay closes cleanly.
            from app.agent.session import SessionLog
            from app.agent.session.derive import derive_messages
            from app.agent.session.repair import open_turn_closers
            cut = next(e["seq"] for e in log.events if e["type"] == "step/end") + 1
            interrupted = SessionLog(log.events[:cut])
            interrupted.append_batch(open_turn_closers(interrupted.events))
            recovered = next(e["data"]["content"] for e in reversed(interrupted.events) if e["type"] == "ui/message" and e["data"]["role"] == "assistant")
            assert [p["type"] for p in recovered["parts"]] == ["tool_call", "tool_call"]
            assert all(p["status"] == "completed" for p in recovered["parts"])
            assert recovered["stats"]["llm_calls"] == 2
            assert recovered["usage"]["total_tokens"] == 34
            assert len([m for m in derive_messages(interrupted.events) if m["role"] == "tool"]) == 2
    finally:
        await harness.shutdown()
        await kernels.shutdown_all()
        db.close()
