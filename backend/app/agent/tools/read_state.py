"""Observed revisions used by the workspace read-before-edit guard.

Python translation of dsh's ``fs-observation-policy/src/index.ts`` (observed
write/edit intents) and ``fs-local/src/index.ts`` (per-target mutation lock),
plus ZCode v3.14.3's ``core/src/tool/read-file-state.ts`` and
``read-file-state-metadata.ts`` (latest observation, durable metadata). The
upstream prefixes are ``packages/fs/`` and ``apps/zcode-cli/packages/``.

A requested line range observes the revision of the *whole* file and permits
exact edits elsewhere in it. Tool-imposed truncation does not. The latest
observation always wins, including a newer truncated view. State belongs to
one conversation and must be cleared when its visible history is compacted.

File-tool mutations are serialized and published atomically. The lock covers
this process's file tools; arbitrary external processes/kernel cells do not
participate, so a filesystem-wide compare-and-swap is not claimed here.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
import stat
import tempfile
import time
import weakref
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from app.agent.workspace import Workspace, WorkspaceError

STATE_KEY = "read_file_state"
SCHEMA_VERSION = 1
MAX_STATE_CONTENT_BYTES = 2_000_000
_TOOLS = {"read_file", "write_file", "edit_file"}
_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


class ReadStateError(ValueError):
    def __init__(self, code: str, path: str, message: str):
        self.code = code
        self.path = path
        super().__init__(f"{path}: {message}")

    def metadata(self) -> dict:
        return {"code": self.code, "path": self.path, "remedy": "Read the file again with read_file before editing or overwriting it."}


@dataclass(frozen=True)
class FileRevision:
    revision_id: str
    mtime_ns: int
    size_bytes: int
    content: bytes
    content_truncated: bool = False


@dataclass(frozen=True)
class ReadState:
    path: str
    content: str
    offset: int | None
    limit: int | None
    is_partial_view: bool
    read_at: float
    source_tool: str
    revision_id: str
    mtime_ns: int
    size_bytes: int

    @property
    def full_view(self) -> bool:
        return not self.is_partial_view and (self.offset or 1) == 1 and self.limit is None

    def metadata(self) -> dict:
        # POSIX nanoseconds exceed JavaScript's safe integer range. Keep the
        # exact revision stamp as decimal text in the browser-facing log.
        return {"schema_version": SCHEMA_VERSION, **asdict(self), "mtime_ns": str(self.mtime_ns)}


def read_revision(path: Path, max_bytes: int = MAX_STATE_CONTENT_BYTES) -> FileRevision:
    """Hash all bytes while retaining only a bounded content prefix.

    Read bytes and their timestamp from the same descriptor; refuse a read
    raced by a replacement or modification instead of publishing a mixed
    revision. Hashing also catches equal-size writes with a restored mtime.
    """
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    retained = 0
    total = 0
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ReadStateError("FS_NOT_REGULAR_FILE", str(path), "Not a regular file.")
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
            total += len(chunk)
            if retained < max_bytes:
                part = chunk[:max_bytes - retained]
                chunks.append(part)
                retained += len(part)
        after = os.fstat(stream.fileno())
    try:
        current = path.stat()
    except FileNotFoundError as exc:
        raise ReadStateError("FS_STALE_VERSION", str(path), "File was removed while being read. Read it again.") from exc
    stamp = lambda st: (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size)
    if stamp(before) != stamp(after) or stamp(after) != stamp(current) or total != after.st_size:
        raise ReadStateError("FS_STALE_VERSION", str(path), "File changed while it was being read. Read it again.")
    return FileRevision(digest.hexdigest(), after.st_mtime_ns, after.st_size, b"".join(chunks), total > max_bytes)


def latest_read(state: dict, path: str) -> ReadState | None:
    entry = state.get(STATE_KEY, {}).get(path)
    return entry if isinstance(entry, ReadState) else None


def record_read(
    state: dict, path: str, revision: FileRevision, content: str, *,
    source_tool: str, offset: int | None = None, limit: int | None = None,
    is_partial_view: bool = False,
) -> dict:
    entry = ReadState(
        path=path, content=content, offset=offset, limit=limit,
        is_partial_view=is_partial_view, read_at=time.time(), source_tool=source_tool,
        revision_id=revision.revision_id, mtime_ns=revision.mtime_ns, size_bytes=revision.size_bytes,
    )
    # A single slot per path implements ZCode's latest-read rule without an
    # ever-growing cache of ranges; the durable log preserves every observation.
    state.setdefault(STATE_KEY, {})[path] = entry
    return {"read_state": entry.metadata()}


def assert_observed(entry: ReadState | None, path: str) -> None:
    if entry is None or entry.is_partial_view:
        detail = " The last view was truncated by the tool; request a smaller line range." if entry else ""
        raise ReadStateError("FS_NOT_OBSERVED", path, "File has not been read yet. Read it first before writing to it." + detail)


def assert_fresh(entry: ReadState | None, current: FileRevision, path: str) -> None:
    assert_observed(entry, path)
    assert entry is not None
    changed = (
        current.revision_id != entry.revision_id or current.size_bytes != entry.size_bytes
        or current.mtime_ns > entry.mtime_ns
    )
    # ZCode's strict-full-read identical-content escape hatch, using the byte
    # hash rather than storing/recomparing a second whole-file string.
    if changed and not (entry.full_view and current.revision_id == entry.revision_id):
        raise ReadStateError("FS_STALE_VERSION", path, "File has been modified since read, either by the user or by a linter. Read it again before attempting to write it.")


def clear_read_state(state: dict) -> None:
    state.pop(STATE_KEY, None)


def invalidate_reads(state: dict, workspace: Workspace, paths: Iterable[str]) -> dict:
    invalidated = []
    for path in sorted(set(paths)):
        try:
            rel = workspace.relpath(workspace.resolve(path))
        except (WorkspaceError, OSError):
            continue
        state.get(STATE_KEY, {}).pop(rel, None)
        invalidated.append(rel)
    return {"read_state_invalidated": invalidated} if invalidated else {}


def invalidate_execution_changes(state: dict, workspace: Workspace, before: dict, changed: Iterable[str]) -> dict:
    """Include removed files, which the artifact-oriented changed_since omits."""
    removed = [rel for rel in before if not (workspace.root / rel).is_file()]
    from app.agent.turn_diff import tracker
    if (diff := tracker(state)) is not None:
        diff.observe_exec(before, [*changed, *removed])
    return invalidate_reads(state, workspace, [*changed, *removed])


def restore_read_state(state: dict, workspace: Workspace, tool_results: Iterable[dict]) -> None:
    """Replay chronological durable tool-result payloads; old metadata fails closed.

    Results contain their original ``ok``/``meta`` fields. Invalidations apply
    even to failed python/bash calls because those can still mutate files.
    Paths are resolved under the *current* workspace, never trusted from logs.
    """
    clear_read_state(state)
    for result in tool_results:
        if not isinstance(result, dict) or not isinstance(result.get("meta"), dict):
            continue
        meta = result["meta"]
        invalidated = meta.get("read_state_invalidated")
        if isinstance(invalidated, list):
            invalidate_reads(state, workspace, (p for p in invalidated if isinstance(p, str)))
        if result.get("ok") is not True:
            continue
        entry = parse_read_state_metadata(meta, workspace)
        if entry is not None:
            state.setdefault(STATE_KEY, {})[entry.path] = entry


def parse_read_state_metadata(meta: Any, workspace: Workspace) -> ReadState | None:
    record = meta.get("read_state") if isinstance(meta, dict) else None
    if not isinstance(record, dict) or type(record.get("schema_version")) is not int or record["schema_version"] != SCHEMA_VERSION:
        return None
    if record.get("source_tool") not in _TOOLS or not isinstance(record.get("path"), str):
        return None
    if not isinstance(record.get("content"), str) or len(record["content"].encode("utf-8")) > MAX_STATE_CONTENT_BYTES:
        return None
    if type(record.get("is_partial_view")) is not bool:
        return None
    if not isinstance(record.get("revision_id"), str) or not re.fullmatch(r"[0-9a-f]{64}", record["revision_id"]):
        return None
    stamp = record.get("mtime_ns")
    if not isinstance(stamp, str) or not re.fullmatch(r"[0-9]{1,30}", stamp):
        return None
    if type(record.get("size_bytes")) is not int or record["size_bytes"] < 0:
        return None
    read_at = record.get("read_at")
    if type(read_at) not in (int, float) or not math.isfinite(read_at) or read_at < 0:
        return None
    for key in ("offset", "limit"):
        if record.get(key) is not None and (type(record[key]) is not int or record[key] < 1):
            return None
    try:
        path = workspace.relpath(workspace.resolve(record["path"]))
    except (WorkspaceError, OSError, ValueError):
        return None
    fields = {key: record.get(key) for key in ReadState.__dataclass_fields__}
    fields.update(path=path, mtime_ns=int(stamp))
    return ReadState(**fields)


def mutation_lock(path: Path) -> asyncio.Lock:
    key = str(path)
    lock = _LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock


def publish_text(path: Path, content: str, current: FileRevision | None, display_path: str) -> FileRevision:
    """dsh's staged publication, with no-clobber creation and revision recheck.

    Caller holds ``mutation_lock`` and has validated the prior observation.
    New targets use hard-link publication so a competing creator is preserved.
    Existing targets preserve POSIX mode and recheck before atomic replacement.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if current is not None else 0o644
    fd, temporary = tempfile.mkstemp(prefix=".matopt-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        if current is None:
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise ReadStateError("FS_NOT_OBSERVED", display_path, "File appeared before creation. Read it first before overwriting it.") from exc
        else:
            try:
                latest = read_revision(path, max_bytes=0)
            except FileNotFoundError as exc:
                raise ReadStateError("FS_STALE_VERSION", display_path, "File was removed after reading it. Read it again before writing.") from exc
            if (latest.revision_id, latest.mtime_ns, latest.size_bytes) != (current.revision_id, current.mtime_ns, current.size_bytes):
                raise ReadStateError("FS_STALE_VERSION", display_path, "File changed before publication. Read it again before writing.")
            os.replace(temporary, path)
        return read_revision(path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
