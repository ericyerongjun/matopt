"""
Per-conversation workspace on disk.

Layout (under ``settings.data_dir / workspaces / <conversation_id>``)::

    uploads/    files the user attached
    figures/    images / plots captured from the Python kernel
    parsed/     Markdown produced by parse_document
    ...         anything the agent writes with write_file / python / bash

All tool file access is confined to the workspace root; attempts to escape
it (``..``, absolute paths elsewhere, symlinks) raise ``WorkspaceError``.
"""

from __future__ import annotations

import fnmatch
import hashlib
import mimetypes
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from app.agent.events import Artifact

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}
TEXT_EXT = {".txt", ".md", ".csv", ".json", ".tex", ".py", ".r", ".jl", ".yaml", ".yml", ".toml", ".html", ".xml", ".log", ".ipynb", ".sql", ".sh", ".js", ".ts"}
INTERNAL_DIRS = {".ipynb_checkpoints", "__pycache__", ".git", ".matopt"}
# Live notebook the kernel appends after every cell — not a user-facing artifact.
SANDBOX_NOTEBOOK = "analysis.ipynb"

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\- ()\[\]+]")


class WorkspaceError(ValueError):
    pass


def file_digest(path: Path) -> Optional[str]:
    """SHA-256 of a file's bytes, or None if it cannot be read."""
    try:
        h = hashlib.sha256()
        with Path(path).open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def is_internal_output(rel: str) -> bool:
    """True for sandbox bookkeeping files that should not become tool artifacts."""
    norm = (rel or "").replace("\\", "/").lstrip("./")
    if not norm:
        return False
    if norm == SANDBOX_NOTEBOOK:
        return True
    parts = norm.split("/")
    return any(p in INTERNAL_DIRS or p.startswith(".") for p in parts)


@dataclass(frozen=True)
class FileStat:
    size: int
    mtime_ns: int


def safe_filename(name: str) -> str:
    """Strip directory components and odd characters from an uploaded filename."""
    base = os.path.basename(name or "").strip() or "file"
    base = _SAFE_NAME.sub("_", base)
    return base[:180]


class Workspace:
    def __init__(self, conversation_id: str, base_dir: Path):
        self.conversation_id = conversation_id
        self.root = (Path(base_dir) / conversation_id).resolve()
        for sub in ("uploads", "figures", "parsed"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # ── paths ───────────────────────────────────────────────────────────

    def resolve(self, path: str | os.PathLike[str] | None, must_exist: bool = False) -> Path:
        """Resolve *path* (relative to the workspace, or absolute inside it)."""
        raw = str(path or "").strip() or "."
        candidate = Path(os.path.expanduser(raw))
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceError(f"Path is outside the workspace: {raw}") from exc
        if must_exist and not resolved.exists():
            raise WorkspaceError(f"No such file: {raw}")
        return resolved

    def relpath(self, path: Path | str) -> str:
        return str(Path(path).resolve().relative_to(self.root)).replace(os.sep, "/")

    def url_for(self, path: Path | str) -> str:
        rel = self.relpath(path) if isinstance(path, Path) or os.path.isabs(str(path)) else str(path)
        return f"/api/files/{self.conversation_id}/{rel}"

    # ── listing & snapshots ────────────────────────────────────────────

    def iter_files(self, subdir: str | None = None) -> Iterable[Path]:
        base = self.resolve(subdir) if subdir else self.root
        if base.is_file():
            yield base
            return
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in INTERNAL_DIRS and not d.startswith(".")]
            for fn in filenames:
                if fn.startswith("."):
                    continue
                yield Path(dirpath) / fn

    def snapshot(self) -> dict[str, FileStat]:
        snap: dict[str, FileStat] = {}
        for p in self.iter_files():
            try:
                st = p.stat()
            except OSError:
                continue
            snap[self.relpath(p)] = FileStat(st.st_size, st.st_mtime_ns)
        return snap

    def changed_since(self, before: dict[str, FileStat]) -> list[str]:
        """Workspace-relative paths that are new or modified since *before*."""
        after = self.snapshot()
        return sorted(rel for rel, st in after.items() if before.get(rel) != st)

    def list_files(self, pattern: str = "**/*", subdir: str | None = None, limit: int = 200) -> list[dict]:
        out = []
        for p in self.iter_files(subdir):
            rel = self.relpath(p)
            if pattern not in ("**/*", "*", "") and not (
                fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(p.name, pattern)
            ):
                continue
            st = p.stat()
            out.append({"path": rel, "size": st.st_size, "mtime": st.st_mtime})
            if len(out) >= limit:
                break
        out.sort(key=lambda d: d["path"])
        return out

    # ── artifacts ───────────────────────────────────────────────────────

    def artifact_for(self, path: Path | str) -> Artifact:
        p = self.resolve(path)
        rel = self.relpath(p)
        ext = p.suffix.lower()
        mime = mimetypes.guess_type(p.name)[0]
        if ext in IMAGE_EXT:
            kind = "image"
        elif ext in {".md", ".markdown"}:
            kind = "markdown"
        elif ext == ".json":
            kind = "json"
        elif ext in {".html", ".htm"}:
            kind = "html_file"
        else:
            kind = "file"
        size = p.stat().st_size if p.exists() else None
        digest = file_digest(p) if kind == "image" and p.exists() else None
        return Artifact(kind=kind, name=p.name, path=rel, url=self.url_for(rel), mime=mime, size=size, digest=digest)

    # ── mutations ───────────────────────────────────────────────────────

    def save_upload(self, filename: str, data: bytes) -> Path:
        name = safe_filename(filename)
        dest = self.root / "uploads" / name
        stem, suffix = dest.stem, dest.suffix
        n = 1
        while dest.exists():
            dest = dest.with_name(f"{stem}_{n}{suffix}")
            n += 1
        dest.write_bytes(data)
        return dest

    def next_figure_path(self, ext: str = ".png", prefix: str = "fig") -> Path:
        figures = self.root / "figures"
        figures.mkdir(exist_ok=True)
        existing = [p for p in figures.glob(f"{prefix}_*{ext}")]
        idx = len(existing) + 1
        dest = figures / f"{prefix}_{idx:03d}{ext}"
        while dest.exists():
            idx += 1
            dest = figures / f"{prefix}_{idx:03d}{ext}"
        return dest

    def delete(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def describe_for_prompt(self, limit: int = 40) -> str:
        files = self.list_files(limit=limit)
        if not files:
            return "The workspace is empty."
        lines = [f"- {f['path']} ({_human_size(f['size'])})" for f in files]
        more = "" if len(files) < limit else f"\n- ... (more files; use list_files)"
        return "\n".join(lines) + more


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"
