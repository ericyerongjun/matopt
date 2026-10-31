"""Tolerant location of an edit's old text, ported from Codex's apply-patch.

Models copy context with trailing spaces trimmed, indentation re-flowed or
typographic punctuation (‘ ’ “ ” – — and non-breaking spaces) where the file
has ASCII, or the reverse. Codex's ``seek_sequence.rs`` therefore looks for a
patch's context lines in four passes, each more lenient than the last: exact,
ignoring trailing whitespace, ignoring surrounding whitespace, and with
Unicode punctuation normalised. ``edit_file`` uses passes two to four after
its exact search found nothing, and still demands a unique match.
"""

from __future__ import annotations

from typing import Callable, Optional

_PUNCTUATION = str.maketrans({
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ", " ": " ", "　": " ",
})

PASSES: tuple[tuple[str, Callable[[str], str]], ...] = (
    ("trailing whitespace", str.rstrip),
    ("surrounding whitespace", str.strip),
    ("whitespace and Unicode punctuation", lambda s: s.translate(_PUNCTUATION).strip()),
)


def find_unique(text: str, needle: str) -> tuple[Optional[tuple[int, int]], str, int]:
    """(span, pass name, match count) of ``needle``'s lines in ``text``.

    The span covers whole lines of ``text``; a needle without a final newline
    leaves the matched block's final newline in place. A pass with more than
    one match stops the search: a looser pass would only match more.
    """
    wanted = needle.splitlines()
    if not wanted:
        return None, "", 0
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    for name, norm in PASSES:
        target = [norm(line) for line in wanted]
        normalised = [norm(line.rstrip("\r\n")) for line in lines]
        hits = [i for i in range(len(lines) - len(target) + 1) if normalised[i:i + len(target)] == target]
        if len(hits) > 1:
            return None, name, len(hits)
        if hits:
            start = offsets[hits[0]]
            end = offsets[hits[0] + len(target)]
            if not needle.endswith(("\n", "\r")):
                last = lines[hits[0] + len(target) - 1]
                end -= len(last) - len(last.rstrip("\r\n"))
            return (start, end), name, 1
    return None, "", 0
