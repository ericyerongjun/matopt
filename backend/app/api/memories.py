"""Memory management routes (contract in ``docs/CODEX-INTEGRATION.md``, Phase 2).

The user can see what MatOpt remembers, add, edit or delete ad-hoc notes, wipe
everything, opt one conversation out, and ask for a refresh now. Codex offers
the same controls as ``/memories`` commands and per-thread memory mode
(``ext/memories``); here they are REST for the settings UI.

Session memory (``app/agent/memory/session.py``) has its own per-conversation
routes: read, replace or clear the chat's working notes.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.agent.memory.service import MemoryService, get_memory_service
from app.agent.memory.session import SessionMemory, get_session_memory
from app.agent.memory.store import MemoryPathError

router = APIRouter(prefix="/api", tags=["memories"])


def memory_service() -> MemoryService:
    return get_memory_service()


def session_memory() -> SessionMemory:
    service = get_session_memory()
    if service is None:
        raise HTTPException(status_code=503, detail="Session memory is not available")
    return service


class SessionNotesBody(BaseModel):
    notes: str = Field(max_length=20_000)


class NoteBody(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


class ModeBody(BaseModel):
    mode: Literal["enabled", "disabled"]


def _note_out(note: dict) -> dict:
    return {k: note[k] for k in ("id", "title", "text", "created_at")}


@router.get("/memories")
async def list_memories(svc: MemoryService = Depends(memory_service)):
    return svc.list()


@router.post("/memories/notes")
async def add_note(body: NoteBody, svc: MemoryService = Depends(memory_service)):
    try:
        return _note_out(svc.add_note(body.text))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.put("/memories/notes/{note_id}")
async def update_note(note_id: str, body: NoteBody, svc: MemoryService = Depends(memory_service)):
    try:
        note = svc.update_note(note_id, body.text)
    except MemoryPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if note is None:
        raise HTTPException(status_code=404, detail="Unknown note")
    return _note_out(note)


@router.delete("/memories/notes/{note_id}")
async def delete_note(note_id: str, svc: MemoryService = Depends(memory_service)):
    try:
        deleted = svc.delete_note(note_id)
    except MemoryPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not deleted:
        raise HTTPException(status_code=404, detail="Unknown note")
    return {"deleted": True}


@router.delete("/memories")
async def clear_memories(svc: MemoryService = Depends(memory_service)):
    svc.clear()
    return {"cleared": True}


@router.put("/conversations/{conversation_id}/memory")
async def set_conversation_memory(conversation_id: str, body: ModeBody,
                                  svc: MemoryService = Depends(memory_service)):
    conversation = svc.db.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Unknown conversation")
    try:
        svc.set_conversation_mode(conversation_id, body.mode == "enabled")
    except MemoryPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Mirrored into the conversation's meta so the UI can show the toggle's state.
    meta = conversation.get("meta") if isinstance(conversation.get("meta"), dict) else {}
    svc.db.update_conversation(conversation_id, meta={**meta, "memory_mode": body.mode})
    return {"conversation_id": conversation_id, "mode": body.mode}


@router.post("/memories/refresh")
async def refresh_memories(svc: MemoryService = Depends(memory_service)):
    return await svc.run_pass()


@router.get("/memories/search")
async def search_memories(q: str, limit: int = 10, svc: MemoryService = Depends(memory_service)):
    if not svc.enabled:
        return {"enabled": False, "hits": []}
    return {"enabled": True, "hits": svc.search(q, limit=max(1, min(limit, 50)))}


def _session_out(session: SessionMemory, cid: str) -> dict:
    row = session.index.get_notes(cid) or {}
    return {"conversation_id": cid, "enabled": session.enabled, "notes": row.get("text", ""),
            "version": row.get("version", 0), "updated_at": row.get("updated_at")}


@router.get("/conversations/{conversation_id}/session-memory")
async def get_session_memory_route(conversation_id: str, session: SessionMemory = Depends(session_memory)):
    if session.db.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Unknown conversation")
    return _session_out(session, conversation_id)


@router.put("/conversations/{conversation_id}/session-memory")
async def put_session_memory(conversation_id: str, body: SessionNotesBody,
                             session: SessionMemory = Depends(session_memory)):
    if session.db.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Unknown conversation")
    try:
        session.set_notes(conversation_id, body.notes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _session_out(session, conversation_id)


@router.delete("/conversations/{conversation_id}/session-memory")
async def clear_session_memory(conversation_id: str, session: SessionMemory = Depends(session_memory)):
    if session.db.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Unknown conversation")
    session.set_notes(conversation_id, "")
    return _session_out(session, conversation_id)
