"""What a turn changed in the workspace, as a unified diff (from Codex).

Codex's ``TurnDiffTracker`` (``core/src/turn_diff_tracker.rs``) records the
first content it sees for every path a turn touches and renders a git-style
diff against the current files when the turn ends. MatOpt changes files three
ways, so the baseline comes from three places:

* ``write_file`` / ``edit_file`` hand over the exact prior bytes before they
  publish (``before_write``);
* ``python`` / ``bash`` report the paths their cell or command created,
  modified or removed (``observe_exec``); their prior bytes come from a cache of
  the workspace's small text files taken when the turn starts (``prime``);
* anything not in that cache (large, binary or appeared mid-turn and then
  changed) is listed as changed with no line diff, instead of failing the
  whole turn's diff as Codex does when it loses track.

The rendered result is recorded as one ``workspace/changes`` session event at
the end of the turn and served by ``GET /conversations/{id}/turns/{turn}/diff``.
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass, field
from typing import Iterable, Optional

from app.agent.workspace import Workspace, is_internal_output

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_CACHE_BYTES = 32 * 1024 * 1024
MAX_DIFF_CHARS = 200_000
_MISSING = object()


def _text(data: Optional[bytes]) -> Optional[str]:
    if data is None:
        return ""
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _blob_id(data: Optional[bytes]) -> str:
    if data is None:
        return "0" * 7
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()[:7]


@dataclass
class TurnDiffTracker:
    workspace: Workspace
    baseline: dict[str, Optional[bytes]] = field(default_factory=dict)   # None: did not exist
    untracked: set[str] = field(default_factory=set)
    _cache: dict[str, bytes] = field(default_factory=dict)

    def prime(self) -> None:
        """Cache the workspace's small text files as this turn's starting point."""
        used = 0
        for path in self.workspace.iter_files():
            try:
                size = path.stat().st_size
                if size > MAX_FILE_BYTES or used + size > MAX_CACHE_BYTES:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            if _text(data) is None:
                continue
            self._cache[self.workspace.relpath(path)] = data
            used += size

    def before_write(self, rel: str, prior: Optional[bytes]) -> None:
        if rel not in self.baseline and rel not in self.untracked:
            self.baseline[rel] = prior

    def observe_exec(self, before: dict, paths: Iterable[str]) -> None:
        for rel in paths:
            if rel in self.baseline or rel in self.untracked or is_internal_output(rel):
                continue
            if rel not in before:
                self.baseline[rel] = None
            elif rel in self._cache:
                self.baseline[rel] = self._cache[rel]
            else:
                self.untracked.add(rel)

    def _current(self, rel: str) -> Optional[bytes]:
        path = self.workspace.root / rel
        try:
            if not path.is_file():
                return None
            if path.stat().st_size > MAX_FILE_BYTES:
                return b"\0"  # too large to diff: treated like binary
            return path.read_bytes()
        except OSError:
            return None

    def render(self) -> dict:
        files: list[dict] = []
        chunks: list[str] = []
        for rel in sorted(self.baseline):
            old, new = self.baseline[rel], self._current(rel)
            if old == new:
                continue
            status = "A" if old is None else "D" if new is None else "M"
            entry = {"path": rel, "status": status}
            old_text, new_text = _text(old), _text(new)
            if old_text is None or new_text is None:
                entry["binary"] = True
            else:
                lines = list(difflib.unified_diff(
                    old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
                    "/dev/null" if old is None else f"a/{rel}", "/dev/null" if new is None else f"b/{rel}", n=3))
                entry["added"] = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
                entry["removed"] = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
                header = f"diff --git a/{rel} b/{rel}\nindex {_blob_id(old)}..{_blob_id(new)}\n"
                chunks.append(header + "".join(l if l.endswith("\n") else l + "\n\\ No newline at end of file\n"
                                               for l in lines))
            files.append(entry)
        for rel in sorted(self.untracked):
            files.append({"path": rel, "status": "D" if self._current(rel) is None else "M", "untracked": True})
        diff = "".join(chunks)
        truncated = len(diff) > MAX_DIFF_CHARS
        return {"files": files, "diff": diff[:MAX_DIFF_CHARS], "truncated": truncated}


def tracker(state: dict) -> Optional[TurnDiffTracker]:
    value = state.get("turn_diff")
    return value if isinstance(value, TurnDiffTracker) else None
