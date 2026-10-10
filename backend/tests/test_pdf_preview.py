"""Local PDF preview uses bounded raster pages, never active embeds or OCR."""
import io
from types import SimpleNamespace

import pymupdf
import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.agent.workspace import Workspace
from app.main import app
from app.storage.db import Database


@pytest.fixture
def preview(tmp_path, monkeypatch):
    import app.api.files as files_api
    db = Database(tmp_path / "preview.sqlite")
    cid = db.create_conversation()["id"]
    workspace = Workspace(cid, tmp_path / "workspaces")
    rt = SimpleNamespace(db=db, workspace=lambda _cid: workspace)
    monkeypatch.setattr(files_api, "get_runtime", lambda: rt)
    with pymupdf.open() as document:
        for number in (1, 2):
            page = document.new_page(width=1800, height=2400)
            page.insert_text((80, 100), f"Preview page {number}", fontsize=48)
            if number == 1:
                workspace.resolve("one.pdf").write_bytes(document.tobytes())
        workspace.resolve("two.pdf").write_bytes(document.tobytes())
        workspace.resolve("locked.pdf").write_bytes(document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner"))
    workspace.resolve("pretend.pdf").write_text("not a PDF")
    workspace.resolve("broken.pdf").write_bytes(b"%PDF-1.7\nnot a decodable document")
    workspace.resolve("empty.pdf").write_bytes(b"")
    (tmp_path / "outside.pdf").write_bytes(workspace.resolve("two.pdf").read_bytes())
    workspace.resolve("escape.pdf").symlink_to(tmp_path / "outside.pdf")
    yield SimpleNamespace(cid=cid, workspace=workspace, path=f"/api/files/{cid}/pdf-preview/")
    db.close()


@pytest.mark.asyncio
async def test_pdf_preview_pages_metadata_and_pixel_bound(preview):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        single = await client.get(preview.path + "one.pdf")
        assert single.status_code == 200 and single.headers["x-pdf-pages"] == "1"
        first = await client.get(preview.path + "two.pdf")
        second = await client.get(preview.path + "two.pdf?page=2")
        for number, response in ((1, first), (2, second)):
            assert response.status_code == 200
            assert response.headers["content-type"] == "image/png"
            assert response.headers["x-pdf-pages"] == "2"
            assert response.headers["x-pdf-page"] == str(number)
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["cache-control"] == "no-store"
            with Image.open(io.BytesIO(response.content)) as image:
                assert max(image.size) <= 1600 and min(image.size) > 100
        assert first.content != second.content
        # Generic original-file download stays reachable and unchanged.
        original = await client.get(f"/api/files/{preview.cid}/two.pdf?download=true")
        assert original.content == preview.workspace.resolve("two.pdf").read_bytes()


@pytest.mark.asyncio
async def test_pdf_preview_rejects_bad_pages_files_and_paths(preview, monkeypatch):
    import app.api.files as files_api
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for page in (0, -1, 3, "not-a-page"):
            assert (await client.get(preview.path + f"two.pdf?page={page}")).status_code == 422
        assert (await client.get(preview.path + "pretend.pdf")).status_code == 415
        for path in ("broken.pdf", "locked.pdf", "empty.pdf"):
            assert (await client.get(preview.path + path)).status_code == 422
        for path in ("missing.pdf", "escape.pdf", "%2e%2e/%2e%2e/outside.pdf"):
            assert (await client.get(preview.path + path)).status_code == 404
        assert (await client.get(preview.path.replace(preview.cid, "missing-conversation") + "two.pdf")).status_code == 404
        assert (await client.get(preview.path + "uploads")).status_code == 400
        monkeypatch.setattr(files_api, "PDF_PREVIEW_MAX_BYTES", 32)
        assert (await client.get(preview.path + "two.pdf")).status_code == 413
