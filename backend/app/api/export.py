# API for exporting data
"""
POST /api/export — export content as PDF, LaTeX, or Markdown.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.core.exporter import exporter
from app.schemas.export import ExportRequest

router = APIRouter(prefix="/api", tags=["export"])


@router.post("/export")
async def export_content(request: ExportRequest):
    """
    Export Markdown content to the requested format.
    Returns the generated file as a download.
    """
    file_path, meta = await exporter.export(request)
    return FileResponse(
        path=str(file_path),
        media_type=meta.content_type,
        filename=meta.filename,
    )