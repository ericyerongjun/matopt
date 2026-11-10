"""File management API endpoints with OSS support."""

import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session
from typing import Optional
import mimetypes
from pathlib import Path
from datetime import datetime
from urllib.parse import unquote
from urllib.parse import urlparse, parse_qs

from data_agent_app.api.deps import get_db
from data_agent_app.core.config import settings
from app.security import file_headers

logger = logging.getLogger(__name__)

router = APIRouter()


def get_oss_service():
    from data_agent_app.services.oss_service import get_oss_service as _get_oss_service
    return _get_oss_service()

# Base directories (fallback for local mode)
_DATA_ROOT = Path(settings.LOCAL_WORKSPACE_ROOT).expanduser().resolve().parent
IMAGES_DIR = _DATA_ROOT / "images"
UPLOADS_DIR = _DATA_ROOT / "uploads"
LATEX_DIR = _DATA_ROOT / "latex"
WORKSPACES_DIR = Path(settings.LOCAL_WORKSPACE_ROOT)

# Ensure directories exist for local fallback
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
LATEX_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_OSS_DOMAINS = [
    "data-app-prod.oss-cn-hongkong.aliyuncs.com",
]
ALLOWED_OSS_PREFIXES = [
    "generated/",
    "uploads/",
    "latex/",
]


def is_allowed_oss_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and parsed.hostname in ALLOWED_OSS_DOMAINS
    except ValueError:
        return False


def extract_oss_object_name(url_or_path: str) -> str:
    """Convert a signed OSS URL or object path into a stable OSS object path."""
    if not url_or_path:
        return ""

    if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        object_name = get_oss_service().get_object_name_from_url(url_or_path.split("?", 1)[0])
    else:
        object_name = url_or_path.lstrip("/")

    return unquote(object_name)


def is_allowed_oss_object_path(object_name: str) -> bool:
    return any(object_name.startswith(prefix) for prefix in ALLOWED_OSS_PREFIXES)


async def read_content_from_reference(reference: str) -> tuple[bytes, str]:
    """Read bytes from an OSS URL, proxy-object path, or local file path."""
    import httpx

    if reference.startswith("/api/v1/files/proxy-object"):
        parsed = urlparse(reference)
        path_values = parse_qs(parsed.query).get("path", [])
        object_name = extract_oss_object_name(path_values[0] if path_values else "")
        if not object_name or not is_allowed_oss_object_path(object_name):
            raise HTTPException(status_code=403, detail="Access denied")

        content = await get_oss_service().download_file(object_name)
        media_type, _ = mimetypes.guess_type(object_name)
        return content, media_type or "application/octet-stream"

    object_name = extract_oss_object_name(reference)
    if object_name and is_allowed_oss_object_path(object_name) and not reference.startswith("http"):
        content = await get_oss_service().download_file(object_name)
        media_type, _ = mimetypes.guess_type(object_name)
        return content, media_type or "application/octet-stream"

    if reference.startswith("http"):
        if not is_allowed_oss_url(reference):
            raise HTTPException(status_code=403, detail="Access denied: URL not in allowed list")
        resolved_url = reference.replace("http://", "https://", 1) if reference.startswith("http://") else reference
        async with httpx.AsyncClient() as client:
            response = await client.get(resolved_url, timeout=60.0)
            response.raise_for_status()
            return response.content, response.headers.get("content-type", "application/octet-stream")

    file_path = Path(reference)
    allowed_roots = [IMAGES_DIR, UPLOADS_DIR, LATEX_DIR, WORKSPACES_DIR.resolve()]
    resolved_path = file_path.resolve()
    is_allowed = any(
        str(resolved_path).startswith(str(Path(root).resolve()))
        for root in allowed_roots
    )
    if not is_allowed or not resolved_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    media_type, _ = mimetypes.guess_type(str(resolved_path))
    return resolved_path.read_bytes(), media_type or "application/octet-stream"


def get_oss_object_name(file_type: str, filename: str, user_id: int, session_id: Optional[str] = None) -> str:
    """Generate OSS object name for file."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if session_id:
        return f"{file_type}/{user_id}/{session_id}/{timestamp}_{filename}"
    return f"{file_type}/{user_id}/{timestamp}_{filename}"


@router.get("/content")
async def get_file_content(
    url: str,
):
    """Get local file content for browser previews."""
    # Local edition is a single-user app. read_content_from_reference still
    # restricts local paths to the app data directories.
    if not settings.LOCAL_MODE:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        content, content_type = await read_content_from_reference(url)
        filename = Path(extract_oss_object_name(url) or url).name or "file"
        return Response(
            content=content,
            media_type=content_type,
            headers=file_headers(content_type, filename),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@router.get("/proxy-object")
async def proxy_object_file(
    path: str,
    db: Session = Depends(get_db),
):
    """Proxy OSS objects by stable object path instead of signed URL."""
    object_name = extract_oss_object_name(path)
    if not object_name:
        raise HTTPException(status_code=400, detail="Missing object path")

    if not is_allowed_oss_object_path(object_name):
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        oss_service = get_oss_service()
        content = await oss_service.download_file(object_name)
        media_type, _ = mimetypes.guess_type(object_name)
        filename = Path(object_name).name

        return Response(
            content=content,
            media_type=media_type or "application/octet-stream",
            headers={**file_headers(media_type, filename), "Cache-Control": "public, max-age=300"},
        )
    except Exception as e:
        if "NoSuchKey" in str(e) and object_name.startswith("generated/"):
            parts = object_name.split("/")
            if len(parts) >= 4:
                try:
                    user_id = int(parts[1])
                    session_id = parts[2]
                    relative_path = "/".join(parts[3:])
                    sandbox_path = f"/tmp/workspace/{relative_path}"
                    filename = Path(relative_path).name

                    from data_agent_app.services.sandbox_file_manager import get_sandbox_file_manager

                    repaired_url = await get_sandbox_file_manager().upload_generated_file(
                        session_id,
                        sandbox_path,
                        filename,
                        user_id,
                        db,
                        sandbox_session_id=f"user-{user_id}",
                    )
                    if repaired_url:
                        content = await get_oss_service().download_file(object_name)
                        media_type, _ = mimetypes.guess_type(object_name)
                        return Response(
                            content=content,
                            media_type=media_type or "application/octet-stream",
                            headers={**file_headers(media_type, filename), "Cache-Control": "public, max-age=300"},
                        )
                except Exception as repair_e:
                    logger.warning("Failed to repair missing proxy object %s: %s", object_name, repair_e)

        status_code = 404 if "NoSuchKey" in str(e) else 502
        raise HTTPException(status_code=status_code, detail=f"Failed to fetch file: {str(e)}")


# Internal function to upload generated files (images, PDFs) to OSS
async def upload_generated_file(
    file_path: str, 
    file_type: str,
    user_id: int,
    session_id: Optional[str] = None
) -> str:
    """Upload a generated file to OSS and return the URL."""
    if settings.FILE_STORAGE_MODE != "oss":
        return file_path
    
    path = Path(file_path)
    if not path.exists():
        return file_path
    
    oss_service = get_oss_service()
    object_name = get_oss_object_name(file_type, path.name, user_id, session_id)
    
    # Determine content type
    content_type = 'application/octet-stream'
    if path.suffix == '.png':
        content_type = 'image/png'
    elif path.suffix == '.pdf':
        content_type = 'application/pdf'
    elif path.suffix == '.tex':
        content_type = 'application/x-tex'
    
    url = await oss_service.upload_file_from_path(file_path, object_name)
    return url


