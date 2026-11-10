"""Mount the data-agent backend onto MatOpt."""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.security import ISOLATED_FILE_CSP, file_headers

logger = logging.getLogger(__name__)


class IsolatedStaticFiles(StaticFiles):
    """Static files that can never run as the application's own origin."""

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        name = Path(full_path).name
        headers = file_headers(mimetypes.guess_type(name)[0], name)
        response.headers.update(headers)
        # A generic type also keeps the host page's HTML middleware from
        # replacing the sandbox policy on this response.
        if headers.get("Content-Security-Policy") == ISOLATED_FILE_CSP and "content-type" in response.headers:
            response.headers["content-type"] = "application/octet-stream"
        return response


def attach_data_agent(app: FastAPI) -> None:
    """Serve the data agent's generated-file links and local-file mounts."""
    if not settings.data_agent_enabled:
        logger.info("Data agent is disabled (DATA_AGENT_ENABLED=false)")
        return

    from data_agent_app.api.v1 import api_router
    from data_agent_app.api.v1.files import IMAGES_DIR, UPLOADS_DIR
    from data_agent_app.core.config import settings as ls
    from data_agent_app.core.database import Base, engine, ensure_schema_compatibility

    Path(ls.LOCAL_WORKSPACE_ROOT).mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    ensure_schema_compatibility()

    app.include_router(api_router, prefix="/api/v1")

    if ls.FILE_STORAGE_MODE == "local":
        app.mount("/uploads", IsolatedStaticFiles(directory=str(UPLOADS_DIR)), name="data-uploads")
        app.mount("/images", IsolatedStaticFiles(directory=str(IMAGES_DIR)), name="data-images")

    logger.info(
        "Data agent file links mounted at /api/v1/files (storage=%s, code=%s, workspace=%s)",
        ls.FILE_STORAGE_MODE,
        ls.CODE_EXECUTION_MODE,
        ls.LOCAL_WORKSPACE_ROOT,
    )
