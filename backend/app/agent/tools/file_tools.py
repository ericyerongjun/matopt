"""Workspace file tools: read_file, write_file, edit_file, list_files, grep."""

from __future__ import annotations

import fnmatch
import re
from typing import Any

from app.agent.events import ToolResult
from app.agent.tools.base import Tool, ToolContext, truncate_text
from app.agent.workspace import WorkspaceError

MAX_READ_BYTES = 2_000_000
BINARY_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".zip", ".xlsx", ".xls", ".docx", ".pptx", ".parquet", ".npy", ".npz", ".pkl", ".pt", ".pth", ".h5"}


class ReadFileTool(Tool):
    name = "read_file"
    read_only = True
    ui_kind = "file"
    description = (
        "Read a text file from the workspace, optionally a line range. Returns numbered lines. "
        "For PDFs and images use parse_document instead; for tabular data prefer pandas in the python tool."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative path, e.g. uploads/data.csv or parsed/paper.md"},
            "start_line": {"type": "integer", "description": "1-based first line to return (default 1)."},
            "max_lines": {"type": "integer", "description": "Maximum number of lines to return (default 400, max 2000)."},
        },
        "required": ["path"],
    }

    async def run(self, ctx: ToolContext, path: str, start_line: int = 1, max_lines: int = 400, **_: Any) -> ToolResult:
        try:
            p = ctx.workspace.resolve(path, must_exist=True)
        except WorkspaceError as exc:
            return ToolResult(ok=False, output="", error=str(exc))
        if p.is_dir():
            listing = ctx.workspace.list_files(subdir=ctx.workspace.relpath(p), limit=200)
            return ToolResult(ok=True, output="\n".join(f["path"] for f in listing) or "(empty directory)")
        if p.suffix.lower() in BINARY_EXT:
            return ToolResult(ok=False, output="", error=f"{p.name} is a binary file. Use parse_document (PDF/images) or pandas (tables).")
        size = p.stat().st_size
        raw = p.read_bytes()[:MAX_READ_BYTES]
        if b"\x00" in raw[:4096]:
            return ToolResult(ok=False, output="", error=f"{p.name} looks binary; use parse_document or python instead.")
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        total = len(lines)
        start = max(1, int(start_line or 1))
        count = max(1, min(int(max_lines or 400), 2000))
        chunk = lines[start - 1:start - 1 + count]
        width = len(str(start + len(chunk)))
        body = "\n".join(f"{i:>{width}}| {line}" for i, line in enumerate(chunk, start=start))
        header = f"{ctx.workspace.relpath(p)} — lines {start}-{start + len(chunk) - 1} of {total} ({size} bytes)"
        if start + len(chunk) - 1 < total:
            header += f"; continue with start_line={start + len(chunk)}"
        return ToolResult(ok=True, output=truncate_text(f"{header}\n{body}", 40000), meta={"total_lines": total, "size": size})


class WriteFileTool(Tool):
    name = "write_file"
    ui_kind = "file"
    description = "Create or overwrite a text file in the workspace (parent directories are created). Returns the file as an artifact."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative path, e.g. report.md or scripts/solve.py"},
            "content": {"type": "string", "description": "Full text content to write."},
        },
        "required": ["path", "content"],
    }

    async def run(self, ctx: ToolContext, path: str, content: str = "", **_: Any) -> ToolResult:
        try:
            p = ctx.workspace.resolve(path)
        except WorkspaceError as exc:
            return ToolResult(ok=False, output="", error=str(exc))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content or "", encoding="utf-8")
        art = ctx.workspace.artifact_for(p)
        n_lines = (content or "").count("\n") + (1 if content else 0)
        return ToolResult(ok=True, output=f"Wrote {art.path} ({len(content or '')} chars, {n_lines} lines).", artifacts=[art], meta={"path": art.path})


class EditFileTool(Tool):
    name = "edit_file"
    ui_kind = "file"
    description = (
        "Replace an exact text snippet in a workspace file. `old_string` must occur exactly once unless replace_all is true."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string", "description": "Exact text to find."},
            "new_string": {"type": "string", "description": "Replacement text."},
            "replace_all": {"type": "boolean", "description": "Replace every occurrence (default false)."},
        },
        "required": ["path", "old_string", "new_string"],
    }

    async def run(self, ctx: ToolContext, path: str, old_string: str, new_string: str, replace_all: bool = False, **_: Any) -> ToolResult:
        try:
            p = ctx.workspace.resolve(path, must_exist=True)
        except WorkspaceError as exc:
            return ToolResult(ok=False, output="", error=str(exc))
        text = p.read_text(encoding="utf-8", errors="replace")
        n = text.count(old_string)
        if n == 0:
            return ToolResult(ok=False, output="", error="old_string not found in file.")
        if n > 1 and not replace_all:
            return ToolResult(ok=False, output="", error=f"old_string occurs {n} times; provide more context or set replace_all=true.")
        new_text = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
        p.write_text(new_text, encoding="utf-8")
        art = ctx.workspace.artifact_for(p)
        return ToolResult(ok=True, output=f"Edited {art.path}: replaced {n if replace_all else 1} occurrence(s).", artifacts=[art])


class ListFilesTool(Tool):
    name = "list_files"
    read_only = True
    ui_kind = "file"
    description = "List files in the workspace, optionally filtered by a glob pattern (e.g. *.csv, figures/*.png)."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern matched against file names or relative paths (default: all)."},
            "path": {"type": "string", "description": "Sub-directory to list (default: workspace root)."},
        },
    }

    async def run(self, ctx: ToolContext, pattern: str = "**/*", path: str | None = None, **_: Any) -> ToolResult:
        try:
            files = ctx.workspace.list_files(pattern=pattern or "**/*", subdir=path, limit=300)
        except WorkspaceError as exc:
            return ToolResult(ok=False, output="", error=str(exc))
        if not files:
            return ToolResult(ok=True, output="No files found.")
        lines = [f"{f['path']}  ({f['size']} bytes)" for f in files]
        return ToolResult(ok=True, output="\n".join(lines), meta={"count": len(files)})


class GrepTool(Tool):
    name = "grep"
    read_only = True
    ui_kind = "file"
    description = "Search text files in the workspace with a regular expression; returns path:line: text matches."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python regular expression."},
            "path": {"type": "string", "description": "File or directory to search (default: workspace root)."},
            "glob": {"type": "string", "description": "Only search files whose name matches this glob, e.g. *.md"},
            "case_sensitive": {"type": "boolean", "description": "Default true."},
            "max_results": {"type": "integer", "description": "Default 100, max 500."},
        },
        "required": ["pattern"],
    }

    async def run(self, ctx: ToolContext, pattern: str, path: str | None = None, glob: str | None = None, case_sensitive: bool = True, max_results: int = 100, **_: Any) -> ToolResult:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            rx = re.compile(pattern, flags)
        except re.error:
            rx = re.compile(re.escape(pattern), flags)
        limit = max(1, min(int(max_results or 100), 500))
        try:
            files = list(ctx.workspace.iter_files(path))
        except WorkspaceError as exc:
            return ToolResult(ok=False, output="", error=str(exc))
        matches: list[str] = []
        for p in files:
            if glob and not fnmatch.fnmatch(p.name, glob):
                continue
            if p.suffix.lower() in BINARY_EXT or p.stat().st_size > MAX_READ_BYTES:
                continue
            try:
                for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
                    if rx.search(line):
                        matches.append(f"{ctx.workspace.relpath(p)}:{i}: {line.strip()[:300]}")
                        if len(matches) >= limit:
                            break
            except OSError:
                continue
            if len(matches) >= limit:
                break
        if not matches:
            return ToolResult(ok=True, output="No matches.")
        return ToolResult(ok=True, output=truncate_text("\n".join(matches), 30000), meta={"count": len(matches)})
