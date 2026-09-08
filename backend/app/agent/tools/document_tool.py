"""
``parse_document`` tool: PDF / image → Markdown (+ LaTeX formulas, tables, figures).

Backends
- **mineru** (preferred): the MinerU CLI run as a subprocess. MinerU lives in
  its own virtualenv because its dependency set (torch, antlr4 runtime
  versions) conflicts with the main backend. Auto-detected from
  ``settings.mineru_command``, ``backend/.venv-mineru/bin/mineru`` or ``PATH``.
- **fast** (fallback): pymupdf4llm for born-digital PDFs (no formula OCR),
  pytesseract for images when available.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from app.agent.events import ToolResult
from app.agent.tools.base import Tool, ToolContext, truncate_text
from app.agent.workspace import WorkspaceError
from app.config import BACKEND_DIR, settings

PDF_EXT = {".pdf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
OFFICE_EXT = {".docx", ".pptx", ".doc", ".ppt"}


def find_mineru() -> Optional[str]:
    candidates = [
        settings.mineru_command,
        str(BACKEND_DIR / ".venv-mineru" / "bin" / "mineru"),
        str(Path.home() / ".matopt" / "mineru-venv" / "bin" / "mineru"),
        shutil.which("mineru"),
    ]
    for c in candidates:
        if c and Path(c).exists() and os.access(c, os.X_OK):
            return c
    return None


class ParseDocumentTool(Tool):
    name = "parse_document"
    read_only = True
    ui_kind = "document"
    description = (
        "Convert a PDF, scanned page or image (including handwritten/printed formulas) in the workspace into Markdown with LaTeX "
        "formulas, tables and extracted figures. The full Markdown is saved to parsed/<name>.md (read it with read_file); "
        "the tool returns the beginning of the text plus statistics. Backends: 'auto' (MinerU OCR when installed, else a fast text "
        "extractor), 'mineru', or 'fast'. Use pages='1-5' to limit long PDFs."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative path, e.g. uploads/paper.pdf"},
            "backend": {"type": "string", "enum": ["auto", "mineru", "fast"], "description": "Default auto."},
            "pages": {"type": "string", "description": "Page range like '1-5' or '3' (1-based, PDFs only)."},
            "lang": {"type": "string", "description": "OCR language hint for MinerU, e.g. en, ch, japan, korean (default from config)."},
        },
        "required": ["path"],
    }

    async def run(self, ctx: ToolContext, path: str, backend: str = "auto", pages: str | None = None, lang: str | None = None, **_: Any) -> ToolResult:
        try:
            src = ctx.workspace.resolve(path, must_exist=True)
        except WorkspaceError as exc:
            return ToolResult(ok=False, output="", error=str(exc))
        ext = src.suffix.lower()
        if ext not in PDF_EXT | IMAGE_EXT | OFFICE_EXT:
            return ToolResult(ok=False, output="", error=f"Unsupported file type {ext}. Supported: PDF, images, docx/pptx.")
        page_range = _parse_pages(pages)
        out_dir = ctx.workspace.root / "parsed" / src.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        md_path = ctx.workspace.root / "parsed" / f"{src.stem}.md"

        mineru = find_mineru() if backend in ("auto", "mineru") else None
        started = time.monotonic()
        used = None
        error_notes: list[str] = []
        markdown: Optional[str] = None
        if mineru:
            await ctx.emit(_status(ctx, "Running MinerU OCR…"))
            try:
                markdown = await self._run_mineru(mineru, src, out_dir, page_range, lang or settings.mineru_lang, ctx)
                used = "mineru"
            except Exception as exc:  # noqa: BLE001
                error_notes.append(f"MinerU failed: {exc}")
                if backend == "mineru":
                    return ToolResult(ok=False, output="", error=error_notes[-1])
        elif backend == "mineru":
            return ToolResult(ok=False, output="", error="MinerU is not installed. Run scripts/setup_mineru.sh, or use backend='fast'.")

        if markdown is None:
            try:
                markdown = await asyncio.to_thread(self._run_fast, src, out_dir, page_range)
                used = "fast"
            except Exception as exc:  # noqa: BLE001
                error_notes.append(f"fast extractor failed: {exc}")
                return ToolResult(ok=False, output="", error="; ".join(error_notes))

        markdown = _relink_images(markdown, ctx.workspace.relpath(out_dir))
        md_path.write_text(markdown, encoding="utf-8")
        artifacts = [ctx.workspace.artifact_for(md_path)]
        images = sorted((out_dir / "images").glob("*")) if (out_dir / "images").exists() else []
        n_formulas = len(re.findall(r"\$\$|\\\[|\\begin\{equation", markdown))
        header = (
            f"Parsed {ctx.workspace.relpath(src)} with backend={used} in {int(time.monotonic() - started)}s: "
            f"{len(markdown)} chars, {markdown.count(chr(10)) + 1} lines, ~{n_formulas} display formulas, {len(images)} images.\n"
            f"Full Markdown saved to {ctx.workspace.relpath(md_path)} (use read_file to read further sections)."
        )
        if error_notes:
            header += "\nNotes: " + "; ".join(error_notes)
        preview = truncate_text(markdown, settings.document_preview_chars, "document text")
        return ToolResult(ok=True, output=f"{header}\n\n{preview}", artifacts=artifacts,
                          meta={"backend": used, "chars": len(markdown), "images": len(images), "markdown_path": ctx.workspace.relpath(md_path)})

    # ── backends ────────────────────────────────────────────────────────

    async def _run_mineru(self, mineru: str, src: Path, out_dir: Path, page_range, lang: str, ctx: ToolContext) -> str:
        with tempfile.TemporaryDirectory(prefix="mineru_") as tmp:
            cmd = [mineru, "-p", str(src), "-o", tmp, "-b", settings.mineru_backend, "-l", lang]
            if settings.mineru_device and settings.mineru_device != "auto":
                cmd += ["-d", settings.mineru_device]
            if page_range:
                cmd += ["-s", str(page_range[0] - 1), "-e", str(page_range[1] - 1)]
            env = dict(os.environ)
            env.setdefault("MINERU_MODEL_SOURCE", settings.mineru_model_source)
            env.pop("PYTHONHOME", None)
            env.pop("VIRTUAL_ENV", None)
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=tmp, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True,
            )
            log: list[str] = []

            async def pump() -> None:
                assert proc.stdout is not None
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace")
                    log.append(text)
                    if len(log) % 20 == 0:
                        await ctx.progress(text)

            pump_task = asyncio.create_task(pump())
            try:
                await asyncio.wait_for(pump_task, timeout=settings.mineru_timeout)
                await proc.wait()
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(proc.pid), 9)
                except Exception:  # noqa: BLE001
                    proc.kill()
                raise RuntimeError(f"MinerU timed out after {settings.mineru_timeout}s")
            if proc.returncode != 0:
                tail = "".join(log[-15:]).strip()
                raise RuntimeError(f"mineru exited with code {proc.returncode}: {tail[-800:]}")
            md_files = sorted(Path(tmp).rglob("*.md"), key=lambda p: p.stat().st_size, reverse=True)
            if not md_files:
                raise RuntimeError("MinerU produced no Markdown output")
            md_file = md_files[0]
            markdown = md_file.read_text(encoding="utf-8", errors="replace")
            img_src = md_file.parent / "images"
            if img_src.exists():
                dest = out_dir / "images"
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(img_src, dest)
            for extra in md_file.parent.glob("*_content_list.json"):
                shutil.copy2(extra, out_dir / "content_list.json")
            return markdown

    @staticmethod
    def _run_fast(src: Path, out_dir: Path, page_range) -> str:
        ext = src.suffix.lower()
        if ext in PDF_EXT:
            import pymupdf4llm

            pages = list(range(page_range[0] - 1, page_range[1])) if page_range else None
            img_dir = out_dir / "images"
            img_dir.mkdir(parents=True, exist_ok=True)
            md = pymupdf4llm.to_markdown(str(src), pages=pages, write_images=True, image_path=str(img_dir), image_format="png", dpi=110)
            md = md.replace(str(img_dir) + "/", "images/").replace(str(img_dir), "images")
            if len(md.strip()) < 40:
                md += "\n\n> (Little text was extracted — this PDF is probably scanned. Install MinerU for OCR.)"
            return md
        if ext in IMAGE_EXT:
            try:
                import pytesseract  # type: ignore
                from PIL import Image

                text = pytesseract.image_to_string(Image.open(src))
                return f"# OCR of {src.name} (tesseract, no formula recognition)\n\n{text}"
            except ImportError as exc:
                raise RuntimeError("Image OCR needs MinerU (scripts/setup_mineru.sh) or pytesseract (`pip install pytesseract`, tesseract is installed via Homebrew).") from exc
        if ext in OFFICE_EXT:
            import pymupdf

            doc = pymupdf.open(str(src))
            parts = [page.get_text() for page in doc]
            return "\n\n".join(parts)
        raise RuntimeError(f"No fast extractor for {ext}")


def _parse_pages(pages: str | None) -> Optional[tuple[int, int]]:
    if not pages:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", pages)
    if not m:
        return None
    a = int(m.group(1))
    b = int(m.group(2) or a)
    return (max(1, min(a, b)), max(a, b))


def _relink_images(markdown: str, rel_dir: str) -> str:
    """Rewrite ``images/x.jpg`` links to workspace-relative paths so the UI and the model can address them."""
    return re.sub(r"\]\((?:\./)?images/", f"]({rel_dir}/images/", markdown)


def _status(ctx: ToolContext, message: str):
    from app.agent.events import Event

    return Event("status", {"message": message, "id": ctx.call_id})
