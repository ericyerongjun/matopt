"""GET /api/providers — the model catalog with reasoning levels per model."""

from fastapi import APIRouter

from app.config import settings
from app.llm.registry import providers

router = APIRouter(prefix="/api", tags=["providers"])


@router.get("/providers")
async def list_providers():
    catalog = providers.catalog()
    catalog["default_reasoning"] = settings.default_reasoning
    catalog["verify_available"] = True
    return catalog
