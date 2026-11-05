"""Session memory: the per-conversation history and working context of the model.

This layer is separate from the cross-chat memory in :mod:`.service` (the
user's personal memory, updated chat to chat). It never leaves its own
conversation, except through the opt-in past-chat search below, and needs no
background model calls. Three parts:

**Recall** (MemGPT's recall memory, ReadAgent's page lookup). Compaction
(:mod:`app.agent.compaction`) folds old turns into a ``<conversation-summary>``
so the prompt stays within budget and the provider's prefix cache stays warm;
the turns themselves stay in the message table. The ``recall`` tool searches
them with the hybrid index (:mod:`.index`) and reads any turn back verbatim,
so a summary never has to carry every number.

**Session notes** (a Letta core-memory block, Manus' ``todo.md``, the
structured notes of Anthropic's context-engineering guidance). A short
Markdown block the model edits with ``session_notes``: goal, notation and
definitions fixed in this chat, assumptions, established results with their
status, open questions. It lives outside the history, so it survives
compaction. It is shown in the per-turn ``<turn-context>`` only when it
changed since it was last shown (or after a compaction removed that copy),
which keeps the system prompt — and so the cached prefix — byte-stable.

**Past chats** (Claude's "search past chats", ChatGPT's chat history
reference, but on demand rather than always-on). With personal memory on,
``recall`` with ``scope="past_chats"`` searches the user's other conversations.
Chats opted out of memory are never searched, and an opted-out chat cannot
search the others.

At the end of a chat the notes also feed the personal-memory extraction
(:func:`.extract.render_conversation`), which is how session memory hands
durable facts to the cross-chat layer.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional

from app.agent.exec_output import redact_secrets
from app.agent.memory.extract import _assistant_text, _clip, _tool_line
from app.agent.memory.index import Doc, RecallIndex

logger = logging.getLogger(__name__)

CHAT_SCOPE = "chat"
TURN_CHARS = 6_000
READ_TURN_CHARS = 16_000
NOTES_MAX_CHARS = 6_000
PAST_CHAT_SCAN = 200
SNIPPET_CHARS = 420


def _stamp(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "?"


def render_turns(messages: list[dict]) -> list[dict]:
    """``[{turn, created_at, text}]`` — one entry per user request with the work and answer that followed."""
    turns: list[dict] = []
    for message in messages:
        content = message.get("content") or {}
        if not isinstance(content, dict):
            content = {"text": str(content)}
        role = message.get("role")
        if role == "user":
            text = (content.get("text") or "").strip()
            turns.append({"turn": len(turns) + 1, "created_at": message.get("created_at") or 0,
                          "lines": [f"User: {text}" if text else "User: (attachment or empty message)"]})
        elif role == "assistant" and turns:
            lines = turns[-1]["lines"]
            for part in content.get("parts") or []:
                if isinstance(part, dict) and part.get("type") == "tool_call":
                    lines.append(_tool_line(part))
            answer = _assistant_text(content)
            if answer:
                lines.append(f"Assistant: {answer}")
    return [{"turn": t["turn"], "created_at": t["created_at"], "text": redact_secrets("\n".join(t["lines"]))}
            for t in turns]


def snippet(text: str, query: str, limit: int = SNIPPET_CHARS) -> str:
    """A window of ``text`` around the first query word it contains."""
    from app.agent.memory.terms import query_terms

    flat = re.sub(r"\s+", " ", text or "").strip()
    if len(flat) <= limit:
        return flat
    low = flat.lower()
    positions = [low.find(t) for t in query_terms(query)]
    positions = [p for p in positions if p >= 0]
    start = max(0, min(positions) - limit // 3) if positions else 0
    window = flat[start:start + limit]
    return ("… " if start else "") + window + (" …" if start + limit < len(flat) else "")


class SessionMemory:
    def __init__(self, settings, db, index: RecallIndex, memories=None):
        self.settings = settings
        self.db = db
        self.index = index
        self.memories = memories

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.settings, "session_memory_enabled", True))

    # ── recall: this chat ───────────────────────────────────────────────

    def _docs(self, cid: str, title: str) -> list[Doc]:
        docs = []
        for turn in render_turns(self.db.list_messages(cid)):
            docs.append(Doc(ref=f"turn:{turn['turn']}", text=_clip(turn["text"], TURN_CHARS),
                            title=f"{title} — turn {turn['turn']}".strip(" —"),
                            meta={"conversation_id": cid, "turn": turn["turn"]}, created_at=turn["created_at"]))
        return docs

    def sync_conversation(self, cid: str, signature: str | None = None, title: str | None = None) -> None:
        if signature is None:
            signature = self.db.message_signatures([cid]).get(cid, "0")
        if self.index.signature(CHAT_SCOPE, cid) == signature:
            return
        if title is None:
            conv = self.db.get_conversation(cid) or {}
            title = conv.get("title") or ""
        self.index.replace_source(CHAT_SCOPE, cid, signature, self._docs(cid, title))

    def search_conversation(self, cid: str, query: str, *, limit: int = 6) -> list:
        self.sync_conversation(cid)
        return self.index.search(query, scope=CHAT_SCOPE, owners=[cid], limit=limit)

    def read_turn(self, cid: str, turn: int) -> Optional[dict]:
        turns = render_turns(self.db.list_messages(cid))
        if 1 <= turn <= len(turns):
            found = turns[turn - 1]
            return {**found, "total": len(turns), "text": _clip(found["text"], READ_TURN_CHARS)}
        return None

    def turn_count(self, cid: str) -> int:
        return sum(1 for m in self.db.list_messages(cid) if m.get("role") == "user")

    # ── recall: past chats ──────────────────────────────────────────────

    def past_chats_allowed(self, cid: str) -> Optional[str]:
        """Why this conversation may not search the others, or ``None``."""
        if self.memories is None or not self.memories.enabled:
            return "Searching past chats needs the Memories feature, which is off."
        if not self.memories.conversation_enabled(cid):
            return "Memory is turned off for this chat, so it cannot search other chats."
        return None

    def searchable_chats(self, cid: str) -> list[dict]:
        disabled = self.memories.store.disabled() if self.memories is not None else set()
        return [c for c in self.db.list_conversations(limit=PAST_CHAT_SCAN)
                if c["id"] != cid and c["id"] not in disabled]

    def search_past_chats(self, cid: str, query: str, *, limit: int = 8) -> list:
        chats = self.searchable_chats(cid)
        if not chats:
            return []
        signatures = self.db.message_signatures([c["id"] for c in chats])
        indexed = self.index.signatures(CHAT_SCOPE)
        for chat in chats:
            sig = signatures.get(chat["id"], "0")
            if indexed.get(chat["id"]) != sig:
                self.sync_conversation(chat["id"], sig, chat.get("title") or "")
        return self.index.search(query, scope=CHAT_SCOPE, owners=[c["id"] for c in chats], limit=limit,
                                 recency_days=90)

    # ── session notes ───────────────────────────────────────────────────

    def notes(self, cid: str) -> str:
        row = self.index.get_notes(cid)
        return row["text"] if row else ""

    def set_notes(self, cid: str, text: str) -> dict:
        text = redact_secrets((text or "").strip())
        if len(text) > NOTES_MAX_CHARS:
            raise ValueError(f"Session notes are limited to {NOTES_MAX_CHARS} characters; condense them.")
        return self.index.put_notes(cid, text)

    def turn_context_block(self, cid: str) -> str:
        """The notes for this turn's context when they changed since last shown; marks them shown."""
        if not self.enabled:
            return ""
        row = self.index.get_notes(cid)
        if not row or not row["text"].strip() or row["injected_version"] >= row["version"]:
            return ""
        self.index.mark_notes_injected(cid, row["version"])
        return row["text"].strip()

    def notes_compacted(self, cid: str) -> None:
        """A compaction removed the copy shown earlier; show the notes again next turn."""
        self.index.mark_notes_injected(cid, 0)

    # ── lifecycle ───────────────────────────────────────────────────────

    def forget_conversation(self, cid: str) -> None:
        self.index.delete_source(CHAT_SCOPE, cid)
        self.index.delete_notes(cid)

    def tools(self) -> list:
        from app.agent.memory.tools import RecallTool, SessionNotesTool
        return [RecallTool(self), SessionNotesTool(self)]


_session: Optional[SessionMemory] = None


def get_session_memory() -> Optional[SessionMemory]:
    return _session


def set_session_memory(service: Optional[SessionMemory]) -> None:
    global _session
    _session = service
