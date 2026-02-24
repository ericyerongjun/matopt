"""API router registration."""

from fastapi import APIRouter

from .chat import router as chat_router
from .documents import router as documents_router
from .ocr import router as ocr_router
from .export import router as export_router
from .math import router as math_router
from .suggestions import router as suggestions_router
from .followups import router as followups_router

api_router = APIRouter()
api_router.include_router(chat_router)
api_router.include_router(documents_router)
api_router.include_router(ocr_router)
api_router.include_router(export_router)
api_router.include_router(math_router)
api_router.include_router(suggestions_router)
api_router.include_router(followups_router)

__all__ = ["api_router"]
