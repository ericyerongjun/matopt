# For hand-written text recognition using OCR
"""
POST /api/ocr — upload an image of a handwritten formula and get LaTeX back.
"""

from __future__ import annotations

from fastapi import APIRouter, UploadFile, File

from app.core.ocr_service import ocr_service
from app.schemas.ocr import OcrResponse
from app.utils.file_handler import save_upload, cleanup_file, ALLOWED_IMAGE_TYPES

router = APIRouter(prefix="/api", tags=["ocr"])


@router.post("/ocr", response_model=OcrResponse)
async def recognise_formula(image: UploadFile = File(...)):
    """
    Upload an image of a handwritten formula → returns LaTeX string + confidence.
    """
    path = await save_upload(image, allowed_types=ALLOWED_IMAGE_TYPES)
    try:
        return await ocr_service.recognise(path)
    finally:
        cleanup_file(path)