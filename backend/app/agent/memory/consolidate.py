"""Phase 2: consolidate stage-1 records and notes into the two memory files.

Follows Codex ``memories/write/src/phase2.rs``:

* **One global lock** serialises consolidation (Codex claims a global phase-2
  job before touching the memories root). Here it is an ``asyncio.Lock`` owned
  by the service; the files are written atomically.
* **Input selection**: the newest ``memory_max_inputs`` non-empty stage-1
  records (ranked by ``usage_count`` then recency, Codex's ranking) plus every
  ad-hoc note and the previous ``memory_summary.md`` / ``MEMORY.md``.
* **Dirty check**: Codex runs its agent only when the git workspace changed.
  MatOpt keeps a signature of the inputs (record ids + generation times, note
  ids + mtimes) and skips the model call when it is unchanged, so additions,
  edits *and deletions* all trigger a rewrite. The ``watermark`` (newest
  consumed ``source_updated_at``) is bookkeeping and never moves backwards.
* **Empty input**: with no records and no notes left, both files are emptied
  without a model call (Codex writes the empty-input placeholder).
* **Caps**: ``memory_summary.md`` is held to ``memory_summary_tokens`` at about
  four characters per token, cut on a line boundary.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Awaitable, Callable, Optional

from app.agent.exec_output import redact_secrets
from app.agent.memory import prompts
from app.agent.memory.store import MemoryStore

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4
MAX_RECORD_CHARS = 6_000
MAX_NOTE_CHARS = 4_000
MAX_MEMORY_MD_CHARS = 60_000

Complete = Callable[[list[dict]], Awaitable[Optional[str]]]


def summary_char_cap(settings) -> int:
    return int(settings.memory_summary_tokens) * CHARS_PER_TOKEN


def cap_lines(text: str, limit: int) -> str:
    """Cut ``text`` to at most ``limit`` characters, on a line boundary when possible."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    newline = cut.rfind("\n")
    if newline > limit // 2:
        cut = cut[:newline]
    return cut.rstrip() + "\n[... memory truncated ...]"


def select_records(store: MemoryStore, limit: int) -> list[dict]:
    records = [r for r in store.list_stage1() if (r.get("raw_memory") or "").strip()]
    records.sort(key=lambda r: (int(r.get("usage_count") or 0), float(r.get("source_updated_at") or 0)),
                 reverse=True)
    return records[:limit]


def input_signature(records: list[dict], notes: list[dict]) -> str:
    items = sorted((r["conversation_id"], r.get("generated_at")) for r in records)
    items += sorted((n["id"], round(n.get("updated_at") or 0, 3)) for n in notes)
    return hashlib.sha256(json.dumps(items, default=str).encode()).hexdigest()


def _render_records(records: list[dict]) -> str:
    if not records:
        return "(none)"
    blocks = []
    for r in sorted(records, key=lambda r: float(r.get("source_updated_at") or 0), reverse=True):
        body = MemoryStore.render_stage1(r)
        if len(body) > MAX_RECORD_CHARS:
            body = body[:MAX_RECORD_CHARS] + "\n[... record truncated ...]"
        blocks.append(f'<record path="stage1/{r["conversation_id"]}.json">\n{body}\n</record>')
    return "\n".join(blocks)


def _render_notes(notes: list[dict]) -> str:
    if not notes:
        return "(none)"
    return "\n".join(f'<note id="{n["id"]}">\n{n["text"][:MAX_NOTE_CHARS].strip()}\n</note>' for n in notes)


def consolidation_messages(store: MemoryStore, records: list[dict], notes: list[dict], settings) -> list[dict]:
    system = prompts.CONSOLIDATION_SYSTEM.format(summary_chars=summary_char_cap(settings))
    user = prompts.CONSOLIDATION_INPUT.format(
        summary=store.read_summary().strip() or "(empty)",
        memory_md=cap_lines(store.read_memory_md(), MAX_MEMORY_MD_CHARS) or "(empty)",
        record_count=len(records), records=_render_records(records),
        note_count=len(notes), notes=_render_notes(notes),
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": redact_secrets(user)}]


def _tag(text: str, tag: str) -> Optional[str]:
    m = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", text or "", re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else None


def parse_consolidation(text: str) -> Optional[tuple[str, str]]:
    summary, memory_md = _tag(text, "memory_summary"), _tag(text, "memory_md")
    if summary is None or memory_md is None:
        return None
    return redact_secrets(summary.strip()), redact_secrets(memory_md.strip())


async def consolidate(store: MemoryStore, settings, complete: Optional[Complete], *,
                      force: bool = False) -> str:
    """Rewrite the memory files when their inputs changed.

    Returns an outcome: ``unchanged``, ``emptied``, ``consolidated``,
    ``no_provider`` or ``failed``. The caller holds the phase-2 lock.
    """
    records = select_records(store, int(settings.memory_max_inputs))
    notes = store.list_notes()
    signature = input_signature(records, notes)
    state = store.load_state()
    if not force and state.get("signature") == signature:
        return "unchanged"
    watermark = max([float(state.get("watermark") or 0)]
                    + [float(r.get("source_updated_at") or 0) for r in records])
    consumed = {n["id"]: n.get("updated_at") for n in notes}
    if not records and not notes:
        store.write_summary("")
        store.write_memory_md("")
        store.update_state(signature=signature, watermark=watermark, consumed_notes=consumed)
        return "emptied"
    if complete is None:
        return "no_provider"
    text = await complete(consolidation_messages(store, records, notes, settings))
    parsed = parse_consolidation(text or "")
    if parsed is None:
        logger.info("memory: consolidation output missing or unparseable; keeping previous files")
        return "failed"
    summary, memory_md = parsed
    store.write_summary(cap_lines(summary, summary_char_cap(settings)) + "\n")
    store.write_memory_md(cap_lines(memory_md, MAX_MEMORY_MD_CHARS) + "\n")
    store.update_state(signature=signature, watermark=watermark, consumed_notes=consumed)
    return "consolidated"
