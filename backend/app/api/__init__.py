"""API router registration."""

from fastapi import APIRouter

from .chat import router as chat_router
from .conversations import router as conversations_router
from .files import router as files_router
from .providers import router as providers_router
from .skills import router as skills_router
from .tools import router as tools_router
from .suggestions import router as suggestions_router
from .followups import router as followups_router
from .ui import router as ui_router
from .speech import router as speech_router
from .settings import router as settings_router, require_operator_writes
from .memories import router as memories_router
from .plugins import router as plugins_router
from .migration import router as migration_router
from fastapi import Depends

api_router = APIRouter()
api_router.include_router(chat_router)
api_router.include_router(conversations_router)
api_router.include_router(files_router)
api_router.include_router(providers_router)
api_router.include_router(skills_router)
api_router.include_router(tools_router)
api_router.include_router(suggestions_router)
api_router.include_router(followups_router)
api_router.include_router(ui_router)
api_router.include_router(speech_router)
api_router.include_router(settings_router)
api_router.include_router(memories_router)
# Installing plugins and importing other agents' MCP servers configure code
# that runs at the next start: operator-only (see require_operator_writes).
api_router.include_router(plugins_router, dependencies=[Depends(require_operator_writes)])
api_router.include_router(migration_router, dependencies=[Depends(require_operator_writes)])

__all__ = ["api_router"]
