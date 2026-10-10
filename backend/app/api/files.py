"""Workspace files: upload, list, download, delete."""

from __future__ import annotations

import asyncio
import math
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response

from app.agent.datasets import dataset_card
from app.agent.workspace import WorkspaceError
from app.config import settings
from app.runtime import get_runtime

router = APIRouter(prefix="/api", tags=["files"])


@router.get("/conversations/{conversation_id}/files")
async def list_files(conversation_id: str):
    rt = get_runtime()
    if rt.db.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    ws = rt.workspace(conversation_id)
    files = []
    for f in ws.list_files(limit=1000):
        art = ws.artifact_for(f["path"])
        files.append({**f, "url": art.url, "kind": art.kind, "name": art.name})
    return {"files": files}


@router.post("/conversations/{conversation_id}/files")
async def upload_files(conversation_id: str, files: list[UploadFile] = File(...)):
    rt = get_runtime()
    if rt.db.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    ws = rt.workspace(conversation_id)
    limit = settings.max_upload_size_mb * 1024 * 1024
    saved = []
    for up in files:
        data = await up.read()
        if len(data) > limit:
            raise HTTPException(status_code=413, detail=f"{up.filename} exceeds {settings.max_upload_size_mb} MB")
        dest = ws.save_upload(up.filename or "file", data)
        art = ws.artifact_for(dest)
        card = dataset_card(dest)
        saved.append({"name": art.name, "path": art.path, "url": art.url, "size": art.size, "kind": art.kind, "card": card})
    rt.db.touch_conversation(conversation_id)
    return {"files": saved}


@router.delete("/conversations/{conversation_id}/files/{path:path}")
async def delete_file(conversation_id: str, path: str):
    rt = get_runtime()
    ws = rt.workspace(conversation_id)
    try:
        p = ws.resolve(path, must_exist=True)
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if p.is_dir():
        raise HTTPException(status_code=400, detail="Cannot delete a directory")
    p.unlink()
    return {"deleted": True}


PDF_PREVIEW_MAX_BYTES = 25 * 1024 * 1024
PDF_PREVIEW_MAX_DIMENSION = 1600


def _render_pdf_preview(path: Path, page_number: int) -> tuple[bytes, int]:
    """Decode one bounded page locally. Never invokes OCR or a model provider."""
    import pymupdf

    if not path.is_file():
        raise HTTPException(400, "Not a regular file")
    size = path.stat().st_size
    if size > PDF_PREVIEW_MAX_BYTES:
        raise HTTPException(413, "PDF preview accepts files up to 25 MB. Download the original to view it.")
    if not size:
        raise HTTPException(422, "The PDF is empty")
    with path.open("rb") as handle:
        if not handle.read(1024).lstrip().startswith(b"%PDF-"):
            raise HTTPException(415, "Preview requires a PDF document; the filename is not trusted")
    try:
        with pymupdf.open(path) as document:
            if not document.is_pdf:
                raise HTTPException(415, "Preview requires a PDF document")
            if document.needs_pass:
                raise HTTPException(422, "This PDF is password protected. Upload an unlocked copy to preview it.")
            pages = document.page_count
            if not 1 <= page_number <= pages:
                raise HTTPException(422, f"Page must be inside 1–{pages}")
            page = document.load_page(page_number - 1)
            rect = page.rect
            if min(rect.width, rect.height) <= 0 or not math.isfinite(rect.width + rect.height):
                raise HTTPException(422, "This PDF page has invalid dimensions")
            # Leave a pixel for the renderer's outward rounding. The returned
            # PNG stays at or below 1600 pixels on both axes, including scans.
            scale = min(2.0, (PDF_PREVIEW_MAX_DIMENSION - 1) / max(rect.width, rect.height))
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
            return pixmap.tobytes("png"), pages
    except HTTPException:
        raise
    except Exception as exc:  # malformed PDFs must not crash the preview pane
        raise HTTPException(422, "The PDF could not be decoded. Download the original or upload a valid PDF.") from exc


@router.get("/files/{conversation_id}/pdf-preview/{path:path}")
async def pdf_preview(conversation_id: str, path: str, page: int = Query(1, ge=1)):
    rt = get_runtime()
    if rt.db.get_conversation(conversation_id) is None:
        raise HTTPException(404, "Conversation not found")
    try:
        source = rt.workspace(conversation_id).resolve(path, must_exist=True)
    except WorkspaceError as exc:
        raise HTTPException(404, str(exc)) from exc
    png, count = await asyncio.to_thread(_render_pdf_preview, source, page)
    return Response(png, media_type="image/png", headers={
        "X-PDF-Pages": str(count), "X-PDF-Page": str(page),
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
    })


@router.get("/files/{conversation_id}/{path:path}")
async def get_file(conversation_id: str, path: str, download: bool = False):
    rt = get_runtime()
    ws = rt.workspace(conversation_id)
    try:
        p = ws.resolve(path, must_exist=True)
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if p.is_dir():
        raise HTTPException(status_code=400, detail="Not a file")
    headers = {"X-Content-Type-Options": "nosniff"}
    if p.suffix.lower() in {".html", ".htm", ".svg", ".xml"}:
        # The separate mini-app runtime is the only active HTML renderer. A
        # direct API URL must never acquire the host application's authority.
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'none'; base-uri 'none'"
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{p.name}"'
    return FileResponse(str(p), headers=headers)
