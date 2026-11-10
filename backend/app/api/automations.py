"""Scheduled and watched agent turns (``/loop``, ``/automate``): list, pause, run, delete.

Automations are created by the agent's ``create_automation`` tool (which the
approval policy can gate); this API is the user's control surface for them.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.runtime import get_runtime

router = APIRouter(prefix="/api", tags=["automations"])


class AutomationPatch(BaseModel):
    enabled: bool


def _service():
    service = get_runtime().automations
    if service is None:
        raise HTTPException(status_code=404, detail="Automations are disabled on this server")
    return service


@router.get("/automations")
async def list_automations(conversation_id: Optional[str] = None):
    return {"automations": _service().list(conversation_id)}


@router.patch("/automations/{automation_id}")
async def update_automation(automation_id: str, body: AutomationPatch):
    row = _service().update(automation_id, enabled=body.enabled)
    if row is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    return row


@router.post("/automations/{automation_id}/run")
async def run_automation(automation_id: str):
    row = await _service().run_now(automation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    return row


@router.delete("/automations/{automation_id}")
async def delete_automation(automation_id: str):
    if not _service().delete(automation_id):
        raise HTTPException(status_code=404, detail="Automation not found")
    return {"deleted": True}
