"""
Minimal SQLite store (stdlib ``sqlite3``) for conversations and messages.

Messages are stored as JSON documents:

user       {"text": str, "attachments": [{name, path, url, size}]}
assistant  {"parts": [...], "model": str, "provider": str, "reasoning": str,
            "usage": {...}, "status": "complete"|"stopped"|"error", "plan": [...]}

Assistant *parts* (ordered) are the unit the UI renders and the harness
replays to the LLM:
  {"type": "text", "text": str}
  {"type": "reasoning", "text": str}
  {"type": "tool_call", "id": str, "name": str, "arguments": dict,
   "result": {ok, output, artifacts, error, meta} | None, "status": str}
  {"type": "phase", "phase": str, "round": int}
  {"type": "verification", "verdict": str, "report": str}
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New chat',
    provider TEXT,
    model TEXT,
    reasoning TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, seq);
"""


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── conversations ───────────────────────────────────────────────────

    def create_conversation(self, title: str = "New chat", provider: str | None = None, model: str | None = None,
                            reasoning: str | None = None, conversation_id: str | None = None) -> dict:
        cid = conversation_id or new_id("c_")
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversations (id, title, provider, model, reasoning, created_at, updated_at, meta) VALUES (?,?,?,?,?,?,?,?)",
                (cid, title[:200], provider, model, reasoning, now, now, "{}"),
            )
        return self.get_conversation(cid)  # type: ignore[return-value]

    def get_conversation(self, cid: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        return self._conv_row(row) if row else None

    def list_conversations(self, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS message_count "
                "FROM conversations c ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._conv_row(r) for r in rows]

    def update_conversation(self, cid: str, **fields: Any) -> Optional[dict]:
        allowed = {"title", "provider", "model", "reasoning", "meta"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if "meta" in updates and not isinstance(updates["meta"], str):
            updates["meta"] = json.dumps(updates["meta"], ensure_ascii=False)
        if "title" in updates:
            updates["title"] = str(updates["title"])[:200]
        updates["updated_at"] = time.time()
        sets = ", ".join(f"{k}=?" for k in updates)
        with self._lock:
            self._conn.execute(f"UPDATE conversations SET {sets} WHERE id=?", (*updates.values(), cid))
        return self.get_conversation(cid)

    def touch_conversation(self, cid: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (time.time(), cid))

    def delete_conversation(self, cid: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM conversations WHERE id=?", (cid,))
        return cur.rowcount > 0

    # ── messages ────────────────────────────────────────────────────────

    def add_message(self, cid: str, role: str, content: dict, message_id: str | None = None) -> dict:
        mid = message_id or new_id("m_")
        now = time.time()
        with self._lock:
            seq = self._conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE conversation_id=?", (cid,)).fetchone()[0]
            self._conn.execute(
                "INSERT INTO messages (id, conversation_id, seq, role, content, created_at) VALUES (?,?,?,?,?,?)",
                (mid, cid, seq, role, json.dumps(content, ensure_ascii=False, default=str), now),
            )
            self._conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, cid))
        return {"id": mid, "conversation_id": cid, "seq": seq, "role": role, "content": content, "created_at": now}

    def update_message(self, mid: str, content: dict) -> None:
        with self._lock:
            self._conn.execute("UPDATE messages SET content=? WHERE id=?", (json.dumps(content, ensure_ascii=False, default=str), mid))

    def get_message(self, mid: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        return self._msg_row(row) if row else None

    def list_messages(self, cid: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY seq", (cid,)).fetchall()
        return [self._msg_row(r) for r in rows]

    def delete_messages_from(self, cid: str, seq: int) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM messages WHERE conversation_id=? AND seq>=?", (cid, seq))
        return cur.rowcount

    # ── helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _conv_row(row: sqlite3.Row) -> dict:
        d = dict(row)
        try:
            d["meta"] = json.loads(d.get("meta") or "{}")
        except json.JSONDecodeError:
            d["meta"] = {}
        return d

    @staticmethod
    def _msg_row(row: sqlite3.Row) -> dict:
        d = dict(row)
        try:
            d["content"] = json.loads(d["content"])
        except json.JSONDecodeError:
            d["content"] = {"text": d["content"]}
        return d
