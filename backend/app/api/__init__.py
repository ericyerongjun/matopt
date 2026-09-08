"""API router registration."""

from fastapi import APIRouter

from .chat import router as chat_router
from .conversations import router as conversations_router
from .files import router as files_router
from .providers import router as providers_router
from .skills import router as skills_router
from .suggestions import router as suggestions_router
from .followups import router as followups_router

api_router = APIRouter()
api_router.include_router(chat_router)
api_router.include_router(conversations_router)
api_router.include_router(files_router)
api_router.include_router(providers_router)
api_router.include_router(skills_router)
api_router.include_router(suggestions_router)
api_router.include_router(followups_router)

__all__ = ["api_router"]
