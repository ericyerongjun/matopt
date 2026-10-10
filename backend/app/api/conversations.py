"""Conversation CRUD, kernel control and export."""

from __future__ import annotations

import base64
import asyncio
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from app.runtime import get_runtime
from app.agent.presentation import public_content, public_messages

router = APIRouter(prefix="/api", tags=["conversations"])


class ConversationCreate(BaseModel):
    title: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    reasoning: Optional[str] = None


class ConversationUpdate(BaseModel):
    title: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    reasoning: Optional[str] = None


@router.get("/conversations")
async def list_conversations(limit: int = Query(200, ge=1, le=1000)):
    rt = get_runtime()
    items = rt.db.list_conversations(limit=limit)
    for c in items:
        c["running"] = rt.runs.is_running(c["id"]) or rt.harness.is_running(c["id"])
    return {"conversations": items}


@router.post("/conversations")
async def create_conversation(body: ConversationCreate):
    rt = get_runtime()
    conv = rt.db.create_conversation(title=body.title or "New chat", provider=body.provider, model=body.model, reasoning=body.reasoning)
    rt.workspace(conv["id"])  # create directories
    return conv


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    rt = get_runtime()
    conv = rt.db.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await rt.harness.prepare_session(conversation_id)
    conv["messages"] = public_messages(rt.harness.ui.store.hydrate(conversation_id, rt.db.list_messages(conversation_id)))
    conv["running"] = rt.runs.is_running(conversation_id) or rt.harness.is_running(conversation_id)
    return conv


async def _session_events(conversation_id: str) -> list[dict]:
    rt = get_runtime()
    if rt.db.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await rt.harness.session_events(conversation_id)


def _require_diagnostics() -> None:
    if not getattr(get_runtime().harness.settings, "diagnostics_enabled", False):
        raise HTTPException(status_code=404, detail="Diagnostics are disabled")


@router.get("/conversations/{conversation_id}/events")
async def conversation_events(conversation_id: str, from_seq: int = Query(-1, ge=-1),
                              limit: int = Query(200, ge=1, le=1000)):
    _require_diagnostics()
    rt = get_runtime()
    if rt.db.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await rt.harness.prepare_session(conversation_id)
    rows = await asyncio.to_thread(rt.db.list_session_events, conversation_id, from_seq, limit + 1)
    events = rows[:limit]
    return {"events": events, "next_seq": events[-1]["seq"] if events else from_seq,
            "has_more": len(rows) > limit}


@router.get("/conversations/{conversation_id}/trajectory")
async def conversation_trajectory(conversation_id: str):
    _require_diagnostics()
    from app.agent.session.telemetry import project_trajectory
    return project_trajectory(await _session_events(conversation_id))


@router.get("/conversations/{conversation_id}/usage")
async def conversation_usage(conversation_id: str):
    from app.agent.session.telemetry import project_usage
    return project_usage(await _session_events(conversation_id))


@router.get("/conversations/{conversation_id}/requests/{seq}")
async def conversation_request(conversation_id: str, seq: int):
    _require_diagnostics()
    from app.agent.session import derive_request
    events = await _session_events(conversation_id)
    if seq < 0 or seq >= len(events) or events[seq]["type"] != "request/header":
        raise HTTPException(status_code=404, detail="Request header not found")
    return derive_request(events[:seq + 1])


@router.get("/conversations/{conversation_id}/stats")
async def conversation_stats(conversation_id: str):
    from app.agent.session.telemetry import project_trajectory
    return project_trajectory(await _session_events(conversation_id))["stats"]


@router.get("/conversations/{conversation_id}/context")
async def conversation_context(conversation_id: str):
    from app.agent.session.telemetry import project_usage
    return project_usage(await _session_events(conversation_id))["context"]


@router.patch("/conversations/{conversation_id}")
async def update_conversation(conversation_id: str, body: ConversationUpdate):
    rt = get_runtime()
    conv = rt.db.update_conversation(conversation_id, **body.model_dump(exclude_none=True))
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    rt = get_runtime()
    await rt.runs.cancel_and_wait(conversation_id)
    await rt.kernels.shutdown(conversation_id)
    ok = rt.db.delete_conversation(conversation_id)
    rt.workspace(conversation_id).delete()
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"deleted": True}


@router.post("/conversations/{conversation_id}/kernel/restart")
async def restart_kernel(conversation_id: str):
    rt = get_runtime()
    if rt.db.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if rt.runs.is_running(conversation_id) or rt.harness.is_running(conversation_id):
        raise HTTPException(status_code=409, detail="Stop the active run before restarting its kernel")
    await rt.kernels.restart(conversation_id)
    return {"restarted": True}


@router.get("/conversations/{conversation_id}/export")
async def export_conversation(conversation_id: str, format: str = Query("markdown", pattern="^(markdown|md|ipynb)$")):
    rt = get_runtime()
    conv = rt.db.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await rt.harness.prepare_session(conversation_id)
    messages = public_messages(rt.harness.ui.store.hydrate(conversation_id, rt.db.list_messages(conversation_id)))
    ws = rt.workspace(conversation_id)
    safe_title = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in (conv["title"] or "matopt")).strip() or "matopt"
    if format in ("markdown", "md"):
        body = _to_markdown(conv, messages)
        return Response(body, media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{safe_title}.md"'})
    nb = _to_notebook(conv, messages, ws)
    return Response(nb, media_type="application/x-ipynb+json",
                    headers={"Content-Disposition": f'attachment; filename="{safe_title}.ipynb"'})


# ── export helpers ───────────────────────────────────────────────────────

def _to_markdown(conv: dict, messages: list[dict]) -> str:
    out = [f"# {conv['title']}\n"]
    for m in messages:
        c = public_content(m["content"]) if m["role"] == "assistant" else (m["content"] or {})
        if m["role"] == "user":
            out.append(f"## You\n\n{c.get('text', '')}\n")
            if c.get("attachments"):
                out.append("Attachments: " + ", ".join(a.get("path", a.get("name", "")) for a in c["attachments"]) + "\n")
        else:
            out.append(f"## MatOpt ({c.get('model', '')})\n")
            if c.get("notice"):
                out.append("> " + c["notice"] + "\n")
            for p in c.get("parts") or []:
                t = p.get("type")
                if t == "text":
                    out.append(p.get("text", "") + "\n")
                elif t == "tool_call":
                    args = p.get("arguments") or {}
                    code = args.get("code") or args.get("command")
                    out.append(f"<details><summary>Tool: {p.get('name')}</summary>\n")
                    if code:
                        lang = "python" if p.get("name") == "python" else "bash"
                        out.append(f"```{lang}\n{code}\n```\n")
                    else:
                        out.append("```json\n" + json.dumps(args, ensure_ascii=False, indent=2) + "\n```\n")
                    res = p.get("result") or {}
                    if res.get("output"):
                        out.append("Output:\n\n```\n" + res["output"] + "\n```\n")
                    for a in res.get("artifacts") or []:
                        if a.get("kind") == "image" and a.get("path"):
                            out.append(f"![{a.get('name')}]({a['path']})\n")
                    out.append("</details>\n")
                elif t == "ui":
                    out.append(_ui_markdown(p.get("surface", {})))
                elif t == "artifact":
                    artifact = p.get("artifact") or {}
                    target = artifact.get("path") or artifact.get("url")
                    if target:
                        prefix = "!" if artifact.get("kind") == "image" else ""
                        out.append(f"{prefix}[{artifact.get('name', 'Output')}]({target})\n")
            for draft in c.get("earlier_drafts", []):
                out.append("<details><summary>Earlier draft</summary>\n\n" + draft + "\n\n</details>\n")
    return "\n".join(out)


def _to_notebook(conv: dict, messages: list[dict], ws) -> str:
    import nbformat
    from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook, new_output

    nb = new_notebook()
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    nb.cells.append(new_markdown_cell(f"# {conv['title']}\n\nExported from MatOpt."))
    for m in messages:
        c = public_content(m["content"]) if m["role"] == "assistant" else (m["content"] or {})
        if m["role"] == "user":
            nb.cells.append(new_markdown_cell(f"**You:** {c.get('text', '')}"))
            continue
        if c.get("notice"):
            nb.cells.append(new_markdown_cell("> " + c["notice"]))
        for p in c.get("parts") or []:
            t = p.get("type")
            if t == "text" and p.get("text", "").strip():
                nb.cells.append(new_markdown_cell(p["text"]))
            elif t == "tool_call":
                args = p.get("arguments") or {}
                res = p.get("result") or {}
                if p.get("name") == "python" and args.get("code"):
                    outputs = []
                    if res.get("meta", {}).get("stdout"):
                        outputs.append(new_output("stream", name="stdout", text=res["meta"]["stdout"]))
                    elif res.get("output"):
                        outputs.append(new_output("stream", name="stdout", text=res["output"]))
                    for a in res.get("artifacts") or []:
                        if a.get("kind") == "image" and a.get("path"):
                            try:
                                data = base64.b64encode(ws.resolve(a["path"]).read_bytes()).decode()
                                outputs.append(new_output("display_data", data={"image/png": data}))
                            except Exception:  # noqa: BLE001
                                pass
                    nb.cells.append(new_code_cell(args["code"], outputs=outputs))
                elif p.get("name") == "bash" and args.get("command"):
                    nb.cells.append(new_code_cell(f"!{args['command']}", outputs=[new_output("stream", name="stdout", text=res.get("output", ""))]))
                else:
                    nb.cells.append(new_markdown_cell(f"*Tool `{p.get('name')}`*\n\n```json\n{json.dumps(args, ensure_ascii=False, indent=2)}\n```\n\n```\n{res.get('output', '')}\n```"))
            elif t == "ui":
                nb.cells.append(new_markdown_cell(_ui_markdown(p.get("surface", {}))))
            elif t == "artifact":
                artifact = p.get("artifact") or {}
                target = artifact.get("path") or artifact.get("url")
                if target:
                    prefix = "!" if artifact.get("kind") == "image" else ""
                    nb.cells.append(new_markdown_cell(f"{prefix}[{artifact.get('name', 'Output')}]({target})"))
        for draft in c.get("earlier_drafts", []):
            nb.cells.append(new_markdown_cell("### Earlier draft\n\n" + draft))
    return nbformat.writes(nb)


def _ui_markdown(surface: dict) -> str:
    """Exports remain useful without an interactive renderer or JavaScript."""
    out = ["### " + surface.get("title", "Interactive output"), surface.get("fallback", "")]
    if surface.get("state"):
        out.append("Parameters:\n```json\n" + json.dumps(surface["state"], ensure_ascii=False, indent=2) + "\n```")
    def nodes(items):
        for item in items:
            yield item
            yield from nodes(item.get("children", []))
    for node in nodes(surface.get("nodes", [])):
        if node.get("type") in {"table", "plot", "comparison"}:
            out.append("Data:\n```json\n" + json.dumps(node.get("props", {}), ensure_ascii=False, indent=2) + "\n```")
    out.append(f"Surface `{surface.get('id', '')}`, revision {surface.get('revision', 1)}.")
    return "\n\n".join(out) + "\n"
