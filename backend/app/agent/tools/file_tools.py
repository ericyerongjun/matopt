"""Workspace file tools: read_file, write_file, edit_file, list_files, grep, find_files."""

from __future__ import annotations

import fnmatch
import re
from typing import Any

from app.agent.events import ToolResult
from app.agent.text_match import find_unique
from app.agent.turn_diff import tracker as turn_diff
from app.agent.tools.base import Tool, ToolContext, output_bound_bytes, truncate_text
from app.agent.tools.read_state import (
    ReadStateError, assert_fresh, assert_observed, latest_read, mutation_lock, publish_text,
    read_revision, record_read,
)
from app.agent.workspace import WorkspaceError

MAX_READ_BYTES = 2_000_000
BINARY_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".zip", ".xlsx", ".xls", ".docx", ".pptx", ".parquet", ".npy", ".npz", ".pkl", ".pt", ".pth", ".h5"}


class ReadFileTool(Tool):
    name = "read_file"
    read_only = True
    concurrent_safe = True
    side_effect_scope = "none"
    risk_level = "low"
    max_output_bytes = output_bound_bytes(40_000)   # truncate_text(..., 40_000)
    ui_kind = "file"
    description = (
        "Read a text file from the workspace, optionally a line range. Returns numbered lines. "
        "A complete requested line range permits exact edits anywhere in that file; a tool-truncated view does not. "
        "Unchanged repeated reads of the same range refer to the previous result. "
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
        try:
            revision = read_revision(p, max_bytes=MAX_READ_BYTES)
        except ReadStateError as exc:
            return ToolResult(ok=False, output="", error=str(exc), meta=exc.metadata())
        size = revision.size_bytes
        raw = revision.content
        if b"\x00" in raw[:4096]:
            return ToolResult(ok=False, output="", error=f"{p.name} looks binary; use parse_document or python instead.")
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        total = len(lines)
        start = max(1, int(start_line or 1))
        count = max(1, min(int(max_lines or 400), 2000))
        chunk = lines[start - 1:start - 1 + count]
        rel = ctx.workspace.relpath(p)
        full_view = start == 1 and len(chunk) == total and not revision.content_truncated
        offset, limit = (None, None) if full_view else (start, count)
        prior = latest_read(ctx.state, rel)
        if (
            prior is not None and prior.source_tool == self.name and not prior.is_partial_view
            and prior.offset == offset and prior.limit == limit
            and prior.revision_id == revision.revision_id and prior.mtime_ns == revision.mtime_ns
            and prior.size_bytes == size
        ):
            return ToolResult(
                ok=True,
                output=f"{rel}: file_unchanged. Wasted call — file unchanged since your last Read. Refer to that earlier tool_result instead.",
                meta={"file_unchanged": True, **record_read(
                    ctx.state, rel, revision, prior.content, source_tool=self.name,
                    offset=offset, limit=limit,
                )},
            )
        width = len(str(start + len(chunk)))
        body = "\n".join(f"{i:>{width}}| {line}" for i, line in enumerate(chunk, start=start))
        header = f"{rel} — lines {start}-{start + len(chunk) - 1} of {total} ({size} bytes)"
        if revision.content_truncated:
            header += f"; only the first {MAX_READ_BYTES} bytes were read"
        if start + len(chunk) - 1 < total:
            header += f"; continue with start_line={start + len(chunk)}"
        output = f"{header}\n{body}"
        partial = revision.content_truncated or len(output) > 40000
        meta = record_read(
            ctx.state, rel, revision, "\n".join(chunk)[:40000], source_tool=self.name,
            offset=offset, limit=limit, is_partial_view=partial,
        )
        return ToolResult(ok=True, output=truncate_text(output, 40000), meta={"total_lines": total, "size": size, **meta})


class WriteFileTool(Tool):
    name = "write_file"
    # Writes are not intrinsically destructive (ZCode marks Write the same
    # way): what a write *destroys* is decided by its arguments — a fresh path
    # destroys nothing. `destructive` stays reserved for argument-independent
    # operations, and `needs_approval` is the flag a future broker gates on.
    destructive = False
    concurrent_safe = False
    side_effect_scope = "workspace"
    risk_level = "medium"
    needs_approval = True
    ui_kind = "file"
    description = "Create or overwrite a text file in the workspace (parent directories are created). Existing files require a fresh read_file view without tool truncation. Returns the file as an artifact."
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
        rel = ctx.workspace.relpath(p)
        prior = latest_read(ctx.state, rel)
        try:
            async with mutation_lock(p):
                # Resolve again after waiting, retaining Workspace containment.
                checked = ctx.workspace.resolve(path)
                if checked != p:
                    raise ReadStateError("FS_STALE_VERSION", rel, "Resolved file changed while waiting. Read it again before writing.")
                if p.exists():
                    assert_observed(prior, rel)
                current = read_revision(p) if p.exists() else None
                if current is not None:
                    assert_fresh(prior, current, rel)
                elif prior is not None:
                    raise ReadStateError("FS_STALE_VERSION", rel, "File was removed since read. Read it again before writing.")
                if (diff := turn_diff(ctx.state)) is not None:
                    diff.before_write(rel, current.content if current is not None and not current.content_truncated
                                      else None if current is None else b"\0")
                revision = publish_text(p, content or "", current, rel)
                meta = record_read(ctx.state, rel, revision, (content or "")[:40000], source_tool=self.name)
        except ReadStateError as exc:
            return ToolResult(ok=False, output="", error=str(exc), meta=exc.metadata())
        art = ctx.workspace.artifact_for(p)
        n_lines = (content or "").count("\n") + (1 if content else 0)
        return ToolResult(ok=True, output=f"Wrote {art.path} ({len(content or '')} chars, {n_lines} lines).", artifacts=[art], meta={"path": art.path, **meta})


class EditFileTool(Tool):
    name = "edit_file"
    # Same reasoning as write_file: a targeted replacement, medium risk, no
    # argument-independent destructiveness.
    destructive = False
    concurrent_safe = False
    side_effect_scope = "workspace"
    risk_level = "medium"
    needs_approval = True
    ui_kind = "file"
    description = (
        "Replace an exact text snippet in a workspace file after a fresh read_file view without tool truncation. "
        "A complete requested line range permits edits anywhere in the file. "
        "`old_string` must be nonempty and occur exactly once unless replace_all is true."
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
        rel = ctx.workspace.relpath(p)
        prior = latest_read(ctx.state, rel)
        if not old_string:
            return ToolResult(ok=False, output="", error="old_string must not be empty.", meta={"code": "FS_EDIT_EMPTY", "path": rel})
        try:
            async with mutation_lock(p):
                checked = ctx.workspace.resolve(path, must_exist=True)
                if checked != p:
                    raise ReadStateError("FS_STALE_VERSION", rel, "Resolved file changed while waiting. Read it again before editing.")
                assert_observed(prior, rel)
                revision = read_revision(p)
                assert_fresh(prior, revision, rel)
                if revision.content_truncated:
                    return ToolResult(ok=False, output="", error=f"{rel}: file exceeds the {MAX_READ_BYTES} byte edit limit; use python for large files.")
                # Preserve CRLF and refuse invalid UTF-8 rather than silently
                # normalizing/replacing bytes outside the requested edit.
                text = revision.content.decode("utf-8")
                n = text.count(old_string)
                tolerance = ""
                if n == 0 and not replace_all:
                    # Codex's lenient context search: trailing/surrounding
                    # whitespace, then Unicode punctuation, still unique.
                    span, tolerance, hits = find_unique(text, old_string)
                    if hits > 1:
                        return ToolResult(ok=False, output="", error=f"old_string matches {hits} places when ignoring "
                                          f"{tolerance}; provide more context.")
                    if span is None:
                        return ToolResult(ok=False, output="", error="old_string not found in file.")
                    new_text, n = text[:span[0]] + new_string + text[span[1]:], 1
                elif n == 0:
                    return ToolResult(ok=False, output="", error="old_string not found in file.")
                elif n > 1 and not replace_all:
                    return ToolResult(ok=False, output="", error=f"old_string occurs {n} times; provide more context or set replace_all=true.")
                else:
                    new_text = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
                if (diff := turn_diff(ctx.state)) is not None:
                    diff.before_write(rel, revision.content)
                after = publish_text(p, new_text, revision, rel)
                meta = record_read(ctx.state, rel, after, new_text[:40000], source_tool=self.name)
        except ReadStateError as exc:
            return ToolResult(ok=False, output="", error=str(exc), meta=exc.metadata())
        art = ctx.workspace.artifact_for(p)
        note = f" (matched ignoring {tolerance})" if tolerance else ""
        return ToolResult(ok=True, output=f"Edited {art.path}: replaced {n if replace_all else 1} occurrence(s){note}.",
                          artifacts=[art], meta=meta)


class ListFilesTool(Tool):
    name = "list_files"
    read_only = True
    concurrent_safe = True
    side_effect_scope = "none"
    risk_level = "low"
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
    concurrent_safe = True
    side_effect_scope = "none"
    risk_level = "low"
    max_output_bytes = output_bound_bytes(30_000)   # truncate_text(..., 30_000)
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


def fuzzy_score(query: str, path: str) -> tuple[int, list[int]] | None:
    """fzf-style subsequence score of ``query`` in ``path``, or None (Codex file-search).

    Every query character must appear in order. Matches score more at the start
    of a path segment or a word, and when consecutive; a hit inside the file
    name beats one in a directory. Smart case: an all-lowercase query ignores case.
    """
    if not query:
        return 0, []
    hay = path if any(c.isupper() for c in query) else path.lower()
    base = path.rfind("/") + 1
    positions: list[int] = []
    score, start, previous = 0, 0, -2
    for char in query:
        index = hay.find(char, start)
        if index < 0:
            return None
        bonus = 1
        if index == 0 or path[index - 1] in "/_-. ":
            bonus += 8
        elif path[index - 1].islower() and path[index].isupper():
            bonus += 6
        if index == previous + 1:
            bonus += 5
        if index >= base:
            bonus += 2
        score += bonus
        positions.append(index)
        previous, start = index, index + 1
    score -= (positions[-1] - positions[0]) // 4 + len(path) // 16
    return score, positions


class FindFilesTool(Tool):
    name = "find_files"
    read_only = True
    concurrent_safe = True
    side_effect_scope = "none"
    risk_level = "low"
    ui_kind = "file"
    description = (
        "Fuzzy-find workspace files by approximate name, like an editor's file picker: `fig rev` finds "
        "figures/revenue_by_region.png. Use when you know roughly what a file is called but not its path."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Approximate file name or path; spaces separate terms."},
            "limit": {"type": "integer", "description": "Maximum results (default 20, max 100)."},
        },
        "required": ["query"],
    }

    async def run(self, ctx: ToolContext, query: str, limit: int = 20, **_: Any) -> ToolResult:
        terms = [t for t in (query or "").split() if t]
        if not terms:
            return ToolResult(ok=False, output="", error="query must not be empty.")
        limit = max(1, min(int(limit or 20), 100))
        ranked: list[tuple[int, str]] = []
        for p in ctx.workspace.iter_files():
            rel = ctx.workspace.relpath(p)
            scores = [fuzzy_score(term, rel) for term in terms]
            if all(s is not None for s in scores):
                ranked.append((sum(s[0] for s in scores), rel))
        # Score, then path: Codex's cmp_by_score_desc_then_path_asc.
        ranked.sort(key=lambda item: (-item[0], item[1]))
        shown = [rel for _, rel in ranked[:limit]]
        if not shown:
            return ToolResult(ok=True, output=f"No workspace files match {query!r}.", meta={"matches": 0})
        more = f"\n… {len(ranked) - limit} more" if len(ranked) > limit else ""
        return ToolResult(ok=True, output="\n".join(shown) + more, meta={"matches": len(ranked)})
