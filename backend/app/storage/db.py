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
CREATE TABLE IF NOT EXISTS session_events (
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    time REAL NOT NULL,
    type TEXT NOT NULL,
    data TEXT NOT NULL,
    surface_op TEXT,
    source_seqs TEXT,
    ignorable INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (conversation_id, seq)
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    message_id TEXT,
    client_run_id TEXT UNIQUE,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_seq INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_conversation ON runs(conversation_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_active ON runs(conversation_id)
    WHERE status IN ('queued', 'running', 'paused');
CREATE TABLE IF NOT EXISTS run_events (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    time REAL NOT NULL,
    type TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
-- Sub-agents a turn spawned (Codex's thread_spawn_edges plus agent metadata).
CREATE TABLE IF NOT EXISTS subagents (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    run_turn INTEGER NOT NULL,
    parent_id TEXT,
    path TEXT NOT NULL,
    nickname TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL,
    task TEXT NOT NULL,
    summary TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_subagents_conversation ON subagents(conversation_id, created_at);
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
            if self._conn.execute("PRAGMA user_version").fetchone()[0] < 1:
                self._conn.execute("PRAGMA user_version=1")

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

    # ── sub-agents ──────────────────────────────────────────────────────

    def upsert_subagent(self, row: dict) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO subagents (id,conversation_id,run_turn,parent_id,path,nickname,role,status,task,summary,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "status=excluded.status, summary=excluded.summary, updated_at=excluded.updated_at",
                (row["id"], row["conversation_id"], row["run_turn"], row.get("parent_id"), row["path"], row["nickname"],
                 row["role"], row["status"], row["task"], row.get("summary"), row.get("created_at", now), now))

    def list_subagents(self, cid: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM subagents WHERE conversation_id=? ORDER BY created_at",
                                      (cid,)).fetchall()
        return [dict(r) for r in rows]

    def interrupt_running_subagents(self) -> int:
        """After a restart nothing is running: mark leftovers interrupted."""
        with self._lock:
            cur = self._conn.execute("UPDATE subagents SET status='interrupted', updated_at=? "
                                     "WHERE status IN ('pending','running')", (time.time(),))
        return cur.rowcount

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

    # ── public runs ─────────────────────────────────────────────────────

    def create_run(self, run_id: str, cid: str, request_hash: str,
                   client_run_id: str | None = None) -> dict:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs (id,conversation_id,client_run_id,request_hash,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)", (run_id, cid, client_run_id, request_hash, "queued", now, now),
            )
        return self.get_run(run_id)  # type: ignore[return-value]

    def get_run(self, run_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def get_run_by_client_id(self, client_run_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE client_run_id=?", (client_run_id,)).fetchone()
        return dict(row) if row else None

    def latest_run(self, cid: str, *, active_only: bool = False) -> Optional[dict]:
        sql = "SELECT * FROM runs WHERE conversation_id=?"
        if active_only:
            sql += " AND status IN ('queued','running','paused')"
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT 1"
        with self._lock:
            row = self._conn.execute(sql, (cid,)).fetchone()
        return dict(row) if row else None

    def active_runs(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM runs WHERE status IN ('queued','running','paused')").fetchall()
        return [dict(row) for row in rows]

    def append_run_event(self, run_id: str, event_type: str, data: dict, *,
                         status: str | None = None, message_id: str | None = None) -> dict:
        """Commit one already-public event and its exclusive replay cursor atomically.

        The run journal never receives the private session log or raw requests.
        RunManager owns its allowlisted projection before calling this method.
        """
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute("SELECT conversation_id,last_seq FROM runs WHERE id=?", (run_id,)).fetchone()
                if row is None:
                    raise KeyError(run_id)
                seq = row["last_seq"] + 1
                envelope = {**data, "run_id": run_id, "conversation_id": row["conversation_id"], "sequence": seq}
                self._conn.execute(
                    "INSERT INTO run_events (run_id,seq,time,type,data) VALUES (?,?,?,?,?)",
                    (run_id, seq, now, event_type, json.dumps(envelope, ensure_ascii=False, allow_nan=False)),
                )
                self._conn.execute(
                    "UPDATE runs SET last_seq=?,updated_at=?,status=COALESCE(?,status),"
                    "message_id=COALESCE(?,message_id) WHERE id=?", (seq, now, status, message_id, run_id),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return {"seq": seq, "time": now, "type": event_type, "data": envelope}

    def list_run_events(self, run_id: str, after: int = 0, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq,time,type,data FROM run_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
                (run_id, after, limit),
            ).fetchall()
        return [{"seq": row["seq"], "time": row["time"], "type": row["type"], "data": json.loads(row["data"])} for row in rows]

    # ── durable sessions ────────────────────────────────────────────────

    def list_session_events(self, cid: str, from_seq: int = -1, limit: int | None = None) -> list[dict]:
        """Return the exclusive cursor prefix/page; times are epoch seconds."""
        sql = "SELECT * FROM session_events WHERE conversation_id=? AND seq>? ORDER BY seq"
        values: tuple = (cid, from_seq)
        if limit is not None:
            sql += " LIMIT ?"
            values += (limit,)
        with self._lock:
            rows = self._conn.execute(sql, values).fetchall()
        return [self._event_row(row) for row in rows]

    def append_session_events(self, cid: str, events: list[dict]) -> None:
        """Atomically append a validated batch and update the legacy UI view.

        SQLite commits whole rows/batches, so torn JSON tails are impossible.
        An interrupted lifecycle is repaired by SessionLog.open; malformed
        committed rows fail closed and are never silently truncated.
        """
        from app.agent.session.events import snapshot_json
        from app.agent.session.validate import validate_events

        batch = snapshot_json(events)
        if not batch:
            return
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self._conn.execute(
                    "SELECT * FROM session_events WHERE conversation_id=? ORDER BY seq", (cid,),
                ).fetchall()
                prior = [self._event_row(row) for row in rows]
                validate_events(prior + batch)
                for event in batch:
                    self._conn.execute(
                        "INSERT INTO session_events (conversation_id,seq,time,type,data,surface_op,source_seqs,ignorable) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (cid, event["seq"], event["time"], event["type"],
                         json.dumps(event["data"], ensure_ascii=False, allow_nan=False),
                         json.dumps(event["surfaceOp"], ensure_ascii=False) if "surfaceOp" in event else None,
                         json.dumps(event["sourceEventSeqs"]) if "sourceEventSeqs" in event else None,
                         int(event.get("ignorable", False))),
                    )
                    if event["type"] == "ui/message":
                        data = event["data"]
                        row = self._conn.execute("SELECT seq FROM messages WHERE id=?", (data["message_id"],)).fetchone()
                        seq = row[0] if row else self._conn.execute(
                            "SELECT COALESCE(MAX(seq),0)+1 FROM messages WHERE conversation_id=?", (cid,),
                        ).fetchone()[0]
                        self._conn.execute(
                            "INSERT INTO messages (id,conversation_id,seq,role,content,created_at) VALUES (?,?,?,?,?,?) "
                            "ON CONFLICT(id) DO UPDATE SET content=excluded.content",
                            (data["message_id"], cid, seq, data["role"],
                             json.dumps(data["content"], ensure_ascii=False, allow_nan=False), event["time"]),
                        )
                self._conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (time.time(), cid))
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    @staticmethod
    def _event_row(row: sqlite3.Row) -> dict:
        event = {"seq": row["seq"], "time": row["time"], "type": row["type"], "data": json.loads(row["data"])}
        if row["surface_op"] is not None:
            event["surfaceOp"] = json.loads(row["surface_op"])
        if row["source_seqs"] is not None:
            event["sourceEventSeqs"] = json.loads(row["source_seqs"])
        if row["ignorable"]:
            event["ignorable"] = True
        return event

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
