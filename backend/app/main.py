"""
MatOpt backend — FastAPI application factory.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api import api_router

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Intelligent mathematics assistant — chat, OCR, document parsing, symbolic computation, export.",
    )

    # CORS (allow the Vite dev server and any configured origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register all API routes
    app.include_router(api_router)

    # Health check
    @app.get("/health")
    async def health():
        return {"status": "ok"}

    logger.info("MatOpt backend ready  (debug=%s)", settings.debug)
    return app


app = create_app()
