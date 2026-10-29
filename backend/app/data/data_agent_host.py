"""Mount the data-agent backend onto MatOpt."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings

logger = logging.getLogger(__name__)


def attach_data_agent(app: FastAPI) -> None:
    """Include the data agent's `/api/v1` router and local-file mounts."""
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

    @app.get("/api/v1/public/config")
    async def data_agent_public_config():
        return {
            "local_mode": ls.LOCAL_MODE,
            "turnstile_enabled": False,
            "turnstile_site_key": "",
        }

    @app.get("/api/v1/health")
    async def data_agent_api_health():
        return {
            "status": "healthy",
            "service": "data-api",
            "database": "connected",
            "storage": ls.FILE_STORAGE_MODE,
            "code_execution": ls.CODE_EXECUTION_MODE,
        }

    if ls.FILE_STORAGE_MODE == "local":
        app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="data-uploads")
        app.mount("/images", StaticFiles(directory=str(IMAGES_DIR)), name="data-images")

    logger.info(
        "Data agent mounted at /api/v1 (storage=%s, code=%s, workspace=%s)",
        ls.FILE_STORAGE_MODE,
        ls.CODE_EXECUTION_MODE,
        ls.LOCAL_WORKSPACE_ROOT,
    )
