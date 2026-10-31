"""On-disk layout of the memory folder (``<data_dir>/memories/``).

Codex keeps stage-1 outputs in its state DB and the consolidated artifacts in a
git-tracked folder (``memories/write/src/storage.rs``, ``workspace.rs``). MatOpt
keeps everything as plain files so the user can inspect, edit or delete them:

``stage1/<conversation_id>.json``  one Phase 1 record per conversation:
                                   ``{conversation_id, source_updated_at,
                                   raw_memory, summary, slug, generated_at,
                                   usage_count}``. An empty ``raw_memory`` is a
                                   successful no-op (Codex ``succeeded_no_output``)
                                   so the conversation is not re-read.
``memory_summary.md``              the short form injected into system prompts.
``MEMORY.md``                      the longer registry read on demand.
``notes/<stamp>-<slug>.md``        ad-hoc notes (Codex ``extensions/ad_hoc/notes``):
                                   things the user asked to remember or forget.
``state.json``                     ``disabled`` conversations, ``cleared_at``,
                                   and the Phase 2 input ``signature`` and
                                   ``watermark``.

Writes are atomic (temp file + ``os.replace``) so a crash never leaves a torn
file for the next prompt to inject. Paths handed in by the model or the API go
through :meth:`MemoryStore.resolve`, which refuses anything outside the folder.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

SUMMARY_FILE = "memory_summary.md"
MEMORY_FILE = "MEMORY.md"
STATE_FILE = "state.json"
STAGE1_DIR = "stage1"
NOTES_DIR = "notes"

_NOTE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
_CID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def slugify(text: str, *, limit: int = 40, fallback: str = "note") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    slug = slug[:limit].rstrip("-")
    return slug or fallback


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


class MemoryPathError(ValueError):
    """A path that does not name a memory file inside the memory folder."""


class MemoryStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.RLock()

    # ── paths ───────────────────────────────────────────────────────────

    @property
    def summary_path(self) -> Path:
        return self.root / SUMMARY_FILE

    @property
    def memory_md_path(self) -> Path:
        return self.root / MEMORY_FILE

    @property
    def stage1_dir(self) -> Path:
        return self.root / STAGE1_DIR

    @property
    def notes_dir(self) -> Path:
        return self.root / NOTES_DIR

    def resolve(self, rel: str) -> Path:
        """The memory file ``rel`` names, refusing any escape from the folder."""
        rel = (rel or "").strip().replace("\\", "/")
        if not rel or rel.startswith("/") or "\x00" in rel:
            raise MemoryPathError("Give a path relative to the memory folder, e.g. MEMORY.md")
        root = self.root.resolve()
        target = (root / rel).resolve()
        if target != root and root not in target.parents:
            raise MemoryPathError(f"{rel!r} is outside the memory folder")
        if target.name == STATE_FILE and target.parent == root:
            raise MemoryPathError("state.json is not a memory file")
        return target

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    # ── small files ─────────────────────────────────────────────────────

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except (FileNotFoundError, IsADirectoryError):
            return ""

    def read_summary(self) -> str:
        return self._read(self.summary_path)

    def read_memory_md(self) -> str:
        return self._read(self.memory_md_path)

    def write_summary(self, text: str) -> None:
        atomic_write(self.summary_path, text)

    def write_memory_md(self, text: str) -> None:
        atomic_write(self.memory_md_path, text)

    # ── state ───────────────────────────────────────────────────────────

    def load_state(self) -> dict:
        with self._lock:
            try:
                data = json.loads(self._read(self.root / STATE_FILE) or "{}")
            except json.JSONDecodeError:
                data = {}
            if not isinstance(data, dict):
                data = {}
            data.setdefault("disabled", [])
            return data

    def save_state(self, state: dict) -> None:
        with self._lock:
            atomic_write(self.root / STATE_FILE, json.dumps(state, indent=2, sort_keys=True))

    def update_state(self, **fields: Any) -> dict:
        with self._lock:
            state = self.load_state()
            state.update(fields)
            self.save_state(state)
            return state

    def disabled(self) -> set[str]:
        return set(self.load_state().get("disabled") or [])

    def set_disabled(self, cid: str, disabled: bool) -> None:
        with self._lock:
            state = self.load_state()
            current = set(state.get("disabled") or [])
            (current.add if disabled else current.discard)(cid)
            state["disabled"] = sorted(current)
            self.save_state(state)

    # ── stage-1 records ─────────────────────────────────────────────────

    def _stage1_path(self, cid: str) -> Path:
        if not _CID.match(cid or ""):
            raise MemoryPathError(f"Invalid conversation id {cid!r}")
        return self.stage1_dir / f"{cid}.json"

    def get_stage1(self, cid: str) -> Optional[dict]:
        try:
            data = json.loads(self._read(self._stage1_path(cid)) or "null")
        except (json.JSONDecodeError, MemoryPathError):
            return None
        return data if isinstance(data, dict) else None

    def put_stage1(self, record: dict) -> None:
        atomic_write(self._stage1_path(record["conversation_id"]),
                     json.dumps(record, indent=2, ensure_ascii=False))

    def delete_stage1(self, cid: str) -> bool:
        try:
            self._stage1_path(cid).unlink()
            return True
        except (FileNotFoundError, MemoryPathError):
            return False

    def list_stage1(self) -> list[dict]:
        out = []
        for path in sorted(self.stage1_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("conversation_id"):
                out.append(data)
        return out

    def bump_usage(self, cid: str) -> None:
        with self._lock:
            record = self.get_stage1(cid)
            if record is not None:
                record["usage_count"] = int(record.get("usage_count") or 0) + 1
                self.put_stage1(record)

    @staticmethod
    def render_stage1(record: dict) -> str:
        """The Markdown view of a record that ``memory_read``/``memory_search`` see."""
        updated = record.get("source_updated_at")
        stamp = datetime.fromtimestamp(updated).strftime("%Y-%m-%d") if isinstance(updated, (int, float)) else "?"
        return (f"# {record.get('slug') or record.get('conversation_id')}\n"
                f"conversation_id: {record.get('conversation_id')}\n"
                f"last_updated: {stamp}\n\n"
                f"## Summary\n{(record.get('summary') or '').strip()}\n\n"
                f"## Raw memory\n{(record.get('raw_memory') or '').strip()}\n")

    # ── notes ───────────────────────────────────────────────────────────

    def _note_path(self, note_id: str) -> Path:
        if not _NOTE_ID.match(note_id or "") or ".." in note_id:
            raise MemoryPathError(f"Invalid note id {note_id!r}")
        return self.notes_dir / f"{note_id}.md"

    @staticmethod
    def _note_title(text: str) -> str:
        first = next((line for line in text.splitlines() if line.strip()), "")
        return first.lstrip("#").strip()[:80]

    def _note(self, path: Path) -> dict:
        text = self._read(path)
        stamp = path.stem[:19]
        try:
            created = datetime.strptime(stamp, "%Y-%m-%dT%H-%M-%S").timestamp()
        except ValueError:
            created = path.stat().st_mtime
        return {"id": path.stem, "title": self._note_title(text), "text": text, "created_at": created,
                "updated_at": path.stat().st_mtime}

    def list_notes(self) -> list[dict]:
        notes = [self._note(p) for p in self.notes_dir.glob("*.md") if _NOTE_ID.match(p.stem)]
        return sorted(notes, key=lambda n: (n["created_at"], n["id"]))

    def get_note(self, note_id: str) -> Optional[dict]:
        path = self._note_path(note_id)
        return self._note(path) if path.is_file() else None

    def add_note(self, text: str, *, now: float | None = None) -> dict:
        with self._lock:
            stamp = datetime.fromtimestamp(now or time.time()).strftime("%Y-%m-%dT%H-%M-%S")
            base = f"{stamp}-{slugify(self._note_title(text))}"
            note_id, n = base, 2
            while self._note_path(note_id).exists():
                note_id, n = f"{base}-{n}", n + 1
            atomic_write(self._note_path(note_id), text.rstrip() + "\n")
            return self._note(self._note_path(note_id))

    def update_note(self, note_id: str, text: str) -> Optional[dict]:
        with self._lock:
            path = self._note_path(note_id)
            if not path.is_file():
                return None
            atomic_write(path, text.rstrip() + "\n")
            return self._note(path)

    def delete_note(self, note_id: str) -> bool:
        try:
            self._note_path(note_id).unlink()
            return True
        except FileNotFoundError:
            return False

    # ── reading and searching ───────────────────────────────────────────

    def searchable(self) -> list[tuple[str, str]]:
        """``(relative path, text)`` of every file the memory tools may see."""
        out = []
        for path in (self.summary_path, self.memory_md_path):
            if path.is_file():
                out.append((path.name, self._read(path)))
        for note in self.list_notes():
            out.append((f"{NOTES_DIR}/{note['id']}.md", note["text"]))
        for record in self.list_stage1():
            if (record.get("raw_memory") or "").strip():
                out.append((f"{STAGE1_DIR}/{record['conversation_id']}.json", self.render_stage1(record)))
        return out

    def read_text(self, rel: str) -> tuple[str, str]:
        """``(canonical relative path, text)`` of one memory file."""
        target = self.resolve(rel)
        canonical = self.rel(target)
        if target.parent == self.stage1_dir.resolve() and target.suffix == ".json":
            record = self.get_stage1(target.stem)
            if record is None:
                raise FileNotFoundError(canonical)
            return canonical, self.render_stage1(record)
        if not target.is_file():
            raise FileNotFoundError(canonical)
        return canonical, target.read_text(encoding="utf-8", errors="replace")

    def search(self, query: str, *, limit: int = 50) -> list[dict]:
        """Lines matching every whitespace-separated term (case-insensitive)."""
        terms = [t.lower() for t in (query or "").split() if t.strip()]
        if not terms:
            return []
        hits: list[dict] = []
        for rel, text in self.searchable():
            for lineno, line in enumerate(text.splitlines(), 1):
                low = line.lower()
                if all(t in low for t in terms):
                    hits.append({"path": rel, "line": lineno, "text": line.strip()})
                    if len(hits) >= limit:
                        return hits
        if hits or len(terms) == 1:
            return hits
        # No line holds every term: fall back to lines holding any of them.
        for rel, text in self.searchable():
            for lineno, line in enumerate(text.splitlines(), 1):
                low = line.lower()
                if any(t in low for t in terms):
                    hits.append({"path": rel, "line": lineno, "text": line.strip()})
                    if len(hits) >= limit:
                        return hits
        return hits

    # ── maintenance ─────────────────────────────────────────────────────

    def clear(self) -> None:
        """Delete every memory; keep the per-conversation opt-outs (a preference)."""
        with self._lock:
            disabled = sorted(self.disabled())
            for directory in (self.stage1_dir, self.notes_dir):
                for path in directory.glob("*"):
                    if path.is_file():
                        path.unlink()
            for path in (self.summary_path, self.memory_md_path):
                path.unlink(missing_ok=True)
            self.save_state({"disabled": disabled, "cleared_at": time.time()})
