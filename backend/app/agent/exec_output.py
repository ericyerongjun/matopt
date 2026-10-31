"""Bounded capture, model-facing formatting and secret redaction of exec output.

Three mechanics ported from Codex (``openai/codex``, ``codex-rs``, Apache-2.0):

``HeadTailBuffer``  ``utils/head-tail-buffer``. A runaway ``bash`` loop or a
                    kernel cell printing in a ``while True`` used to grow an
                    unbounded list of chunks in the backend process. The buffer
                    keeps the first half and the last half of a fixed budget
                    and counts what it dropped, so memory is bounded no matter
                    what the child prints, and the model still sees how the
                    output began and how it ended.

``exec_header``     ``core/src/tools/mod.rs::format_exec_output_for_model``.
                    Status comes *before* content: exit code, wall time and a
                    truncation note, then ``Output:``. A model that reads the
                    header first stops mistaking a traceback's last line for the
                    command's result.

``redact_secrets``  ``secrets/src/sanitizer.rs``. ``child_env`` keeps MatOpt's
                    own keys out of a child's environment, but a workspace file,
                    a fetched page or ``cat ~/.netrc`` on a dev machine can
                    still print one. Credential-shaped substrings are masked
                    before a result reaches the model, the session log or the UI.
"""

from __future__ import annotations

import re

DEFAULT_CAPTURE_BYTES = 1024 * 1024


class HeadTailBuffer:
    """Keep at most ``max_bytes`` of a stream: its head and its tail.

    The head fills first; once full, later chunks rotate through a tail window
    of the same size. ``omitted`` counts the bytes dropped between the two.
    """

    def __init__(self, max_bytes: int = DEFAULT_CAPTURE_BYTES):
        self.max_bytes = max(2, int(max_bytes))
        self._head_cap = self.max_bytes // 2
        self._tail_cap = self.max_bytes - self._head_cap
        self._head = bytearray()
        self._tail = bytearray()
        self.omitted = 0
        self.total = 0

    def write(self, data: bytes | str) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8", errors="replace")
        self.total += len(data)
        room = self._head_cap - len(self._head)
        if room > 0:
            self._head += data[:room]
            data = data[room:]
        if not data:
            return
        self._tail += data
        overflow = len(self._tail) - self._tail_cap
        if overflow > 0:
            del self._tail[:overflow]
            self.omitted += overflow

    def text(self) -> str:
        head = self._head.decode("utf-8", errors="replace")
        tail = self._tail.decode("utf-8", errors="replace")
        if not self.omitted:
            return head + tail
        return f"{head}\n... {self.omitted} bytes omitted ...\n{tail}"


def exec_header(*, exit_code: int | None, wall_ms: int, status: str | None = None,
                original_chars: int | None = None, shown_chars: int | None = None) -> str:
    """The status block that precedes exec output for the model."""
    lines = []
    if status:
        lines.append(status)
    if exit_code is not None:
        lines.append(f"Exit code: {exit_code}")
    lines.append(f"Wall time: {wall_ms / 1000:.1f} seconds")
    if original_chars is not None and shown_chars is not None and original_chars > shown_chars:
        lines.append(f"Output truncated: showing {shown_chars} of {original_chars} characters")
    lines.append("Output:")
    return "\n".join(lines)


# Codex's sanitizer patterns plus the provider-key shapes MatOpt is configured
# with. Each keeps a short recognisable prefix so the model can still tell what
# kind of value was there.
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(sk-(?:ant-|proj-)?)[A-Za-z0-9_\-]{16,}"), r"\1[REDACTED]"),
    (re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), r"\1[REDACTED]"),
    (re.compile(r"\b(gh[pousr]_)[A-Za-z0-9]{20,}"), r"\1[REDACTED]"),
    (re.compile(r"\b(xox[abprs]-)[A-Za-z0-9\-]{10,}"), r"\1[REDACTED]"),
    (re.compile(r"\b(tvly-)[A-Za-z0-9]{16,}"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/\-]{16,}=*"), r"\1[REDACTED]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
     "-----BEGIN PRIVATE KEY-----[REDACTED]-----END PRIVATE KEY-----"),
    (re.compile(r"(?i)\b((?:api[_-]?key|secret|token|password|passwd)\s*[=:]\s*['\"]?)[^\s'\"]{8,}"),
     r"\1[REDACTED]"),
)


def redact_secrets(text: str) -> str:
    if not text:
        return text
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text
