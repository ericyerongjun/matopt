"""
Helpers for saving, validating, and cleaning up uploaded files.
"""

import uuid
import aiofiles
from pathlib import Path
from fastapi import UploadFile, HTTPException

from app.config import settings

# Allowed MIME types per endpoint
ALLOWED_DOCUMENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/tiff",
}

ALLOWED_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
}


async def save_upload(file: UploadFile, allowed_types: set[str] | None = None) -> Path:
    """
    Persist an uploaded file to disk and return its path.

    Raises HTTPException(415) if the MIME type is not allowed.
    Raises HTTPException(413) if the file exceeds the configured size limit.
    """
    if allowed_types and file.content_type not in allowed_types:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {file.content_type}. Allowed: {allowed_types}",
        )

    ext = Path(file.filename or "upload").suffix or ".bin"
    dest = settings.upload_dir / f"{uuid.uuid4().hex}{ext}"

    size = 0
    max_bytes = settings.max_upload_size_mb * 1024 * 1024

    async with aiofiles.open(dest, "wb") as out:
        while chunk := await file.read(1024 * 256):
            size += len(chunk)
            if size > max_bytes:
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "File too large")
            await out.write(chunk)

    return dest


def cleanup_file(path: Path) -> None:
    """Remove a temporary file (best-effort, no exception on failure)."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
