"""
``lambda_data_agent`` — run the original LAMBDA data-analysis agent.

MatOpt's kernel can already load CSVs; this tool is for LAMBDA's own loop:
inspect data, execute analysis code, charts, LaTeX/PDF reports, notebooks, PPT.
It calls LAMBDA's ``stream_chat_response`` and binds that loop to this
conversation's Jupyter kernel and workspace.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import shutil
from pathlib import Path
from contextlib import aclosing
from typing import Any, Optional

from app.agent.events import Artifact, ToolResult
from app.agent.lambda_bridge import LambdaExecContext, bind_lambda_exec_context, controlled_items
from app.agent.tools.base import Tool, ToolContext, output_bound_bytes, truncate_text
from app.agent.workspace import is_internal_output
from app.config import settings

logger = logging.getLogger(__name__)

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


def _load_prior_lambda_id(matopt_cid: str, ctx_state: dict) -> Optional[str]:
    prior = ctx_state.get("lambda_conversation_id")
    if prior:
        return str(prior)
    try:
        from app.runtime import get_runtime

        conv = get_runtime().db.get_conversation(matopt_cid)
        meta = (conv or {}).get("meta") or {}
        stored = meta.get("lambda_conversation_id")
        return str(stored) if stored else None
    except Exception:  # noqa: BLE001
        logger.debug("could not read lambda_conversation_id from meta", exc_info=True)
        return None


def _persist_lambda_id(matopt_cid: str, hash_id: str, ctx_state: dict) -> None:
    ctx_state["lambda_conversation_id"] = hash_id
    try:
        from app.runtime import get_runtime

        rt = get_runtime()
        conv = rt.db.get_conversation(matopt_cid)
        meta = dict((conv or {}).get("meta") or {})
        if meta.get("lambda_conversation_id") == hash_id:
            return
        meta["lambda_conversation_id"] = hash_id
        rt.db.update_conversation(matopt_cid, meta=meta)
    except Exception:  # noqa: BLE001
        logger.debug("could not persist lambda_conversation_id", exc_info=True)


def ensure_lambda_conversation(db: Any, user: Any, title: str, prior_hash: Optional[str]) -> str:
    """Create or reuse a LAMBDA conversation and return its hash id."""
    from lambda_app.core.config import settings as ls
    from lambda_app.core.hashid import decode_id, encode_id
    from lambda_app.models.models import Conversation

    if prior_hash:
        numeric = decode_id(prior_hash)
        if numeric:
            existing = db.query(Conversation).filter(
                Conversation.id == numeric,
                Conversation.user_id == user.id,
            ).first()
            if existing is not None:
                return prior_hash
    conv = Conversation(
        user_id=user.id,
        title=(title or "MatOpt analysis")[:50],
        model=ls.DEFAULT_MODEL,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return encode_id(conv.id)


def _register_workspace_uploads(
    uploads: Path,
    user_id: int,
    db: Any,
    dest_dir: Path,
    conversation_id: Optional[str] = None,
) -> list[int]:
    from lambda_app.models.models import UploadedFile

    if not uploads.is_dir():
        return []
    dest_dir.mkdir(parents=True, exist_ok=True)
    ids: list[int] = []
    for path in sorted(uploads.iterdir()):
        if not path.is_file():
            continue
        existing = None
        if conversation_id:
            existing = db.query(UploadedFile).filter(
                UploadedFile.user_id == user_id,
                UploadedFile.conversation_id == conversation_id,
                UploadedFile.original_name == path.name,
            ).first()
        dest = dest_dir / f"matopt_{path.name}"
        shutil.copy2(path, dest)
        mime, _ = mimetypes.guess_type(path.name)
        if existing is not None:
            existing.file_path = str(dest)
            existing.file_size = dest.stat().st_size
            existing.mime_type = mime or existing.mime_type
            existing.filename = dest.name
            db.commit()
            ids.append(existing.id)
            continue
        rec = UploadedFile(
            user_id=user_id,
            conversation_id=conversation_id,
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


class LambdaDataAgentTool(Tool):
    name = "lambda_data_agent"
    # Runs a whole autonomous analysis agent: it executes code through the
    # conversation kernel (unsandboxed, like `python`), writes reports and
    # notebooks into both workspaces, copies uploads into LAMBDA's own upload
    # directory and creates rows in LAMBDA's database. Effects leave the
    # conversation workspace, so the honest scope is `system`, not `workspace`.
    destructive = False
    concurrent_safe = False
    side_effect_scope = "system"
    risk_level = "high"
    needs_approval = True
    timeout_ms = 900_000
    max_output_bytes = output_bound_bytes(settings.tool_max_output_chars)
    ui_kind = "code"
    description = (
        "Run LAMBDA, MatOpt's data-analysis agent (original AMA-CMFAI/LAMBDA backend). "
        "Use for substantial specialized EDA, chart/report, modeling, notebook, or PowerPoint "
        "workflows, autonomous exploration, or an explicit user request for LAMBDA. "
        "Use native python and file tools by default for short bounded calculations, single "
        "statistics, table inspections, and file edits. Specify a clear delegated deliverable "
        "and completion evidence. Workspace uploads are forwarded automatically. Delegation "
        "shares the parent run's model/tool limits and deadline; it does not reset them."
    )
    parameters = {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Scoped analysis request: inputs, question, expected deliverable/output paths, and completion evidence. May be empty when autonomous is true and the user explicitly requested full exploration.",
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
        from lambda_app.models.schemas import ChatRequest

        text = (message or "").strip()
        if not text and not autonomous:
            return ToolResult(ok=False, output="", error="Provide a message or set autonomous=true.")

        db = SessionLocal()
        try:
            user = get_current_user(db)
            prior = _load_prior_lambda_id(ctx.conversation_id, ctx.state)
            hash_id = ensure_lambda_conversation(db, user, text or "MatOpt analysis", prior)
            _persist_lambda_id(ctx.conversation_id, hash_id, ctx.state)
            numeric_id = decode_id(hash_id)
            session_key = str(numeric_id) if numeric_id else None
            file_ids = _register_workspace_uploads(
                ctx.workspace.root / "uploads",
                user.id,
                db,
                UPLOADS_DIR,
                conversation_id=session_key,
            )
            req = ChatRequest(
                message=text,
                conversation_id=hash_id,
                files=file_ids or None,
                analysis_mode="autonomous_exploration" if autonomous else None,
                language=(language or "").strip() or None,
            )
            kernel = ctx.kernels.get(ctx.conversation_id) if ctx.kernels is not None else None
            exec_ctx = LambdaExecContext(
                conversation_id=ctx.conversation_id,
                workspace_root=ctx.workspace.root,
                kernel=kernel,
                sandbox_session_id=f"matopt-{ctx.conversation_id}",
                run_budget=ctx.state.get("run_budget"),
                cancel=ctx.cancel,
                control_boundary=ctx.state.get("control_boundary"),
                record_model_call=ctx.state.get("lambda_record_call"),
                tool_context=ctx,
            )
            before = ctx.workspace.snapshot()
            chunks: list[str] = []
            error: Optional[str] = None
            incomplete = False
            incomplete_reason: Optional[str] = None
            completed_tool_results: list[dict] = []
            with bind_lambda_exec_context(exec_ctx):
                async with asyncio.timeout(self.timeout_ms / 1000), aclosing(
                    controlled_items(stream_chat_response(req, user, db), exec_ctx)
                ) as frames:
                    async for frame in frames:
                        for event in _parse_sse_frame(frame):
                            kind = event.get("type")
                            if event.get("incomplete") is True:
                                incomplete = True
                                incomplete_reason = event.get("finish_reason")
                                error = error or "LAMBDA returned an incomplete response."
                            if kind == "delta":
                                delta = event.get("content") or ""
                                chunks.append(delta)
                                await ctx.progress(delta)
                            elif kind == "status":
                                await ctx.progress((event.get("content") or "") + "\n")
                            elif kind == "conversation_created":
                                hash_id = event.get("conversation_id") or hash_id
                                _persist_lambda_id(ctx.conversation_id, hash_id, ctx.state)
                            elif kind == "error":
                                error = event.get("content") or "LAMBDA error"
                            elif kind == "tool_result" and isinstance(event.get("result"), dict):
                                completed_tool_results.append({
                                    "tool": event.get("tool_name"),
                                    "call_id": event.get("tool_call_id"),
                                    "result": event["result"],
                                })

            artifacts: list[Artifact] = []
            seen: set[str] = set()
            for rel in ctx.workspace.changed_since(before):
                if rel in seen or is_internal_output(rel):
                    continue
                try:
                    artifacts.append(ctx.workspace.artifact_for(rel))
                    seen.add(rel)
                except Exception:  # noqa: BLE001
                    continue

            # Legacy fallback: copy anything LAMBDA wrote into its own workspace.
            if numeric_id and exec_ctx.kernel is None:
                lambda_ws = Path(ls.LOCAL_WORKSPACE_ROOT).resolve() / str(numeric_id)
                dest = ctx.workspace.resolve("lambda_output")
                for path in _copy_lambda_outputs(lambda_ws, dest):
                    rel = ctx.workspace.relpath(path)
                    if rel in seen:
                        continue
                    artifacts.append(ctx.workspace.artifact_for(path))
                    seen.add(rel)

            output = "".join(chunks).strip() or (
                "(LAMBDA stopped before producing text.)" if incomplete else "(LAMBDA finished with no text.)"
            )
            if incomplete and completed_tool_results:
                # These are private tool receipts for the parent, not a claim of
                # a completed answer. Preserve useful work if synthesis was cut
                # short; normal public output remains the parent's responsibility.
                output += "\n\nCompleted tool receipts (response incomplete):\n" + json.dumps(
                    completed_tool_results, ensure_ascii=False, default=str,
                )
            if artifacts:
                listing = "\n".join(f"- {a.path}" for a in artifacts[:40] if a.path)
                if listing:
                    output = f"{output}\n\nWorkspace artifacts:\n{listing}"
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
                    "sandbox_session_id": exec_ctx.sandbox_session_id,
                    **({"incomplete": True, "finish_reason": incomplete_reason} if incomplete else {}),
                },
            )
        finally:
            db.close()
