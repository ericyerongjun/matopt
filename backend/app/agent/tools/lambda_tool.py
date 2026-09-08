"""
``lambda_data_agent`` — run the original LAMBDA data-analysis agent.

MatOpt's kernel can already load CSVs; this tool is for LAMBDA's own loop:
inspect data, execute analysis code, charts, LaTeX/PDF reports, notebooks, PPT.
It calls LAMBDA's ``stream_chat_response`` and copies artifacts back.
"""

from __future__ import annotations

import json
import mimetypes
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from app.agent.events import Artifact, ToolResult
from app.agent.tools.base import Tool, ToolContext, truncate_text
from app.config import settings

SKIP_DIR_PARTS = {".skills", ".lambda_skills", "__pycache__", ".ipynb_checkpoints"}
COPY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp",
    ".pdf", ".pptx", ".ppt", ".xlsx", ".xls", ".csv", ".tsv",
    ".html", ".md", ".tex", ".ipynb", ".json", ".txt",
}


def _parse_sse_frame(chunk: str) -> list[dict]:
    events = []
    for raw in (chunk or "").split("\n"):
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            events.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return events


def _copy_lambda_outputs(src: Path, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    if not src.is_dir():
        return copied
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIR_PARTS for part in path.parts):
            continue
        if path.suffix.lower() not in COPY_EXT:
            continue
        rel = path.relative_to(src)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append(target)
    return copied


class LambdaDataAgentTool(Tool):
    name = "lambda_data_agent"
    ui_kind = "code"
    description = (
        "Run LAMBDA, MatOpt's data-analysis agent (original AMA-CMFAI/LAMBDA backend). "
        "Use for tabular EDA, charts, modeling, autonomous dataset exploration, "
        "LaTeX/PDF reports, Jupyter notebooks, and PowerPoint decks. "
        "Workspace uploads are forwarded automatically. Prefer this over ad-hoc python "
        "when the user wants a full data-analysis report rather than a single statistic."
    )
    parameters = {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Analysis request in natural language. May be empty when autonomous is true.",
            },
            "autonomous": {
                "type": "boolean",
                "description": "Run LAMBDA autonomous exploration (EDA + LaTeX/PDF report) on uploaded data.",
            },
            "language": {
                "type": "string",
                "description": "UI/report language: en or zh.",
            },
        },
    }

    async def run(
        self,
        ctx: ToolContext,
        message: str = "",
        autonomous: bool = False,
        language: str = "",
        **_: Any,
    ) -> ToolResult:
        if not settings.lambda_enabled:
            return ToolResult(ok=False, output="", error="LAMBDA data agent is disabled (LAMBDA_ENABLED=false).")

        from lambda_app.api.deps import get_current_user
        from lambda_app.api.v1.chat import stream_chat_response
        from lambda_app.api.v1.files import UPLOADS_DIR
        from lambda_app.core.config import settings as ls
        from lambda_app.core.database import SessionLocal
        from lambda_app.core.hashid import decode_id
        from lambda_app.models.models import UploadedFile
        from lambda_app.models.schemas import ChatRequest

        text = (message or "").strip()
        if not text and not autonomous:
            return ToolResult(ok=False, output="", error="Provide a message or set autonomous=true.")

        db = SessionLocal()
        try:
            user = get_current_user(db)
            file_ids = _register_workspace_uploads(ctx.workspace.root / "uploads", user.id, db, UPLOADS_DIR)
            prior = ctx.state.get("lambda_conversation_id")
            req = ChatRequest(
                message=text,
                conversation_id=prior,
                files=file_ids or None,
                analysis_mode="autonomous_exploration" if autonomous else None,
                language=(language or "").strip() or None,
            )
            chunks: list[str] = []
            hash_id: Optional[str] = prior
            error: Optional[str] = None
            async for frame in stream_chat_response(req, user, db):
                if ctx.cancel.is_set():
                    return ToolResult(ok=False, output="".join(chunks), error="Cancelled.")
                for event in _parse_sse_frame(frame):
                    kind = event.get("type")
                    if kind == "delta":
                        delta = event.get("content") or ""
                        chunks.append(delta)
                        await ctx.progress(delta)
                    elif kind == "status":
                        await ctx.progress((event.get("content") or "") + "\n")
                    elif kind == "conversation_created":
                        hash_id = event.get("conversation_id") or hash_id
                    elif kind == "error":
                        error = event.get("content") or "LAMBDA error"
            if hash_id:
                ctx.state["lambda_conversation_id"] = hash_id

            numeric_id = decode_id(hash_id) if hash_id else None
            lambda_ws = Path(ls.LOCAL_WORKSPACE_ROOT).resolve() / str(numeric_id or "")
            dest = ctx.workspace.resolve("lambda_output")
            copied = _copy_lambda_outputs(lambda_ws, dest) if numeric_id else []
            artifacts = []
            for path in copied:
                rel = ctx.workspace.relpath(path)
                mime, _ = mimetypes.guess_type(path.name)
                kind = "image" if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"} else "file"
                artifacts.append(
                    Artifact(
                        kind=kind,
                        name=path.name,
                        path=rel,
                        url=ctx.workspace.url_for(path),
                        mime=mime,
                        size=path.stat().st_size,
                    )
                )
            output = "".join(chunks).strip() or "(LAMBDA finished with no text.)"
            if copied:
                listing = "\n".join(f"- {ctx.workspace.relpath(p)}" for p in copied[:40])
                output = f"{output}\n\nCopied artifacts to `lambda_output/`:\n{listing}"
            output = truncate_text(output, settings.tool_max_output_chars, "lambda output")
            return ToolResult(
                ok=error is None,
                output=output,
                error=error,
                artifacts=artifacts,
                meta={
                    "lambda_conversation_id": hash_id,
                    "files_forwarded": len(file_ids),
                    "artifact_count": len(artifacts),
                    "autonomous": bool(autonomous),
                },
            )
        finally:
            db.close()


def _register_workspace_uploads(uploads: Path, user_id: int, db: Any, dest_dir: Path) -> list[int]:
    if not uploads.is_dir():
        return []
    dest_dir.mkdir(parents=True, exist_ok=True)
    ids: list[int] = []
    for path in sorted(uploads.iterdir()):
        if not path.is_file():
            continue
        stamp = time.strftime("%Y%m%d_%H%M%S")
        dest = dest_dir / f"matopt_{stamp}_{path.name}"
        shutil.copy2(path, dest)
        mime, _ = mimetypes.guess_type(path.name)
        rec = UploadedFile(
            user_id=user_id,
            filename=dest.name,
            original_name=path.name,
            file_path=str(dest),
            file_size=dest.stat().st_size,
            mime_type=mime or "application/octet-stream",
        )
        db.add(rec)
        db.commit()
        db.refresh(rec)
        ids.append(rec.id)
    return ids
