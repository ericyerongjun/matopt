"""Workspace files: upload, list, download, delete."""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.agent.datasets import dataset_card
from app.agent.workspace import WorkspaceError
from app.config import settings
from app.runtime import get_runtime

router = APIRouter(prefix="/api", tags=["files"])


@router.get("/conversations/{conversation_id}/files")
async def list_files(conversation_id: str):
    rt = get_runtime()
    if rt.db.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    ws = rt.workspace(conversation_id)
    files = []
    for f in ws.list_files(limit=1000):
        art = ws.artifact_for(f["path"])
        files.append({**f, "url": art.url, "kind": art.kind, "name": art.name})
    return {"files": files}


@router.post("/conversations/{conversation_id}/files")
async def upload_files(conversation_id: str, files: list[UploadFile] = File(...)):
    rt = get_runtime()
    if rt.db.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    ws = rt.workspace(conversation_id)
    limit = settings.max_upload_size_mb * 1024 * 1024
    saved = []
    for up in files:
        data = await up.read()
        if len(data) > limit:
            raise HTTPException(status_code=413, detail=f"{up.filename} exceeds {settings.max_upload_size_mb} MB")
        dest = ws.save_upload(up.filename or "file", data)
        art = ws.artifact_for(dest)
        card = dataset_card(dest)
        saved.append({"name": art.name, "path": art.path, "url": art.url, "size": art.size, "kind": art.kind, "card": card})
    rt.db.touch_conversation(conversation_id)
    return {"files": saved}


@router.delete("/conversations/{conversation_id}/files/{path:path}")
async def delete_file(conversation_id: str, path: str):
    rt = get_runtime()
    ws = rt.workspace(conversation_id)
    try:
        p = ws.resolve(path, must_exist=True)
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if p.is_dir():
        raise HTTPException(status_code=400, detail="Cannot delete a directory")
    p.unlink()
    return {"deleted": True}


@router.get("/files/{conversation_id}/{path:path}")
async def get_file(conversation_id: str, path: str, download: bool = False):
    rt = get_runtime()
    ws = rt.workspace(conversation_id)
    try:
        p = ws.resolve(path, must_exist=True)
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if p.is_dir():
        raise HTTPException(status_code=400, detail="Not a file")
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{p.name}"'
    return FileResponse(str(p), headers=headers)
