"""MatOpt backend — FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import api_router
from app.config import settings
from app.runtime import get_runtime

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    rt = get_runtime()
    await rt.start()
    logger.info(
        "MatOpt backend ready (data_dir=%s, providers=%s, skills=%d, tools=%s)",
        settings.data_dir, rt.providers.configured_ids(), len(rt.skills), rt.tools.names(),
    )
    try:
        yield
    finally:
        await rt.stop()


def create_app() -> FastAPI:
    mcp_asgi = None
    if settings.mcp_enabled:
        try:
            from app.agent.mcp.sandbox import build_mcp_asgi

            mcp_asgi = build_mcp_asgi()
        except Exception:  # noqa: BLE001
            logger.exception("MCP sandbox HTTP transport is disabled")

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        if mcp_asgi is not None and getattr(mcp_asgi, "lifespan", None) is not None:
            async with mcp_asgi.lifespan(app):
                async with lifespan(app):
                    yield
        else:
            async with lifespan(app):
                yield

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description="Agentic assistant for mathematics, statistics, ML and CS: tool-calling harness with a persistent Python kernel, Agent Skills, and MCP sandbox.",
        lifespan=_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)

    @app.middleware("http")
    async def html_isolation_headers(request, call_next):
        response = await call_next(request)
        if response.headers.get("content-type", "").startswith("text/html") and not request.url.path.startswith("/api/"):
            from urllib.parse import urlsplit
            host = request.url.hostname or "localhost"
            origin = settings.ui_sandbox_origin or f"{request.url.scheme}://{host}:3001"
            target = urlsplit(origin)
            request_port = request.url.port or (443 if request.url.scheme == "https" else 80)
            target_port = target.port or (443 if target.scheme == "https" else 80)
            if (target.scheme, target.hostname, target_port) == (request.url.scheme, request.url.hostname, request_port):
                origin = "'none'"
            response.headers["Content-Security-Policy"] = f"frame-src {origin} blob:; object-src 'none'; base-uri 'self'"
        return response

    if settings.lambda_enabled:
        from app.data.lambda_host import attach_lambda

        attach_lambda(app)

    @app.get("/health")
    async def health():
        rt = get_runtime()
        return {
            "status": "ok",
            "providers": rt.providers.configured_ids(),
            "tools": rt.tools.names(),
            "skills": rt.skills.names(),
            "mcp": settings.mcp_enabled,
            "lambda": settings.lambda_enabled,
        }

    if mcp_asgi is not None:
        app.mount(settings.mcp_mount_path, mcp_asgi)

    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
    return app


app = create_app()
