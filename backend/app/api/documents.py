# For document uploading and parsing
"""
POST /api/documents/parse — upload a PDF or image and get structured Markdown back.
"""

from __future__ import annotations

from fastapi import APIRouter, UploadFile, File

from app.core.document_parser import document_parser
from app.schemas.document import DocumentParseResponse
from app.utils.file_handler import save_upload, cleanup_file, ALLOWED_DOCUMENT_TYPES

router = APIRouter(prefix="/api", tags=["documents"])


@router.post("/documents/parse", response_model=DocumentParseResponse)
async def parse_document(file: UploadFile = File(...)):
    """
    Upload a PDF or image → returns extracted Markdown with LaTeX blocks.
    """
    path = await save_upload(file, allowed_types=ALLOWED_DOCUMENT_TYPES)
    try:
        result = await document_parser.parse(path)
        return result
    finally:
        cleanup_file(path)