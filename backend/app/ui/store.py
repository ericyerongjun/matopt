"""Additive SQLite storage: immutable UI revisions and action receipts."""
from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager

from .contracts import UISurface, validate_state

SCHEMA = """
CREATE TABLE IF NOT EXISTS ui_surfaces (
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  surface_id TEXT NOT NULL, revision INTEGER NOT NULL, state TEXT NOT NULL,
  message_id TEXT, updated_at REAL NOT NULL,
  PRIMARY KEY(conversation_id,surface_id)
);
CREATE TABLE IF NOT EXISTS ui_revisions (
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  surface_id TEXT NOT NULL, revision INTEGER NOT NULL, surface TEXT NOT NULL,
  created_at REAL NOT NULL, PRIMARY KEY(conversation_id,surface_id,revision)
);
CREATE TABLE IF NOT EXISTS ui_actions (
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  surface_id TEXT NOT NULL, action_id TEXT NOT NULL, handler_version INTEGER NOT NULL,
  manifest TEXT NOT NULL, definition TEXT NOT NULL, created_at REAL NOT NULL,
  PRIMARY KEY(conversation_id,surface_id,action_id,handler_version)
);
CREATE TABLE IF NOT EXISTS ui_action_receipts (
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  idempotency_key TEXT NOT NULL, surface_id TEXT NOT NULL, request_hash TEXT NOT NULL,
  status TEXT NOT NULL, events TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
  PRIMARY KEY(conversation_id,idempotency_key)
);
"""


class UIConflict(ValueError):
    pass


class UINotFound(ValueError):
    pass


class UIStore:
    def __init__(self, db):
        self.db = db
        with db._lock:
            db._conn.executescript(SCHEMA)
            if db._conn.execute("PRAGMA user_version").fetchone()[0] < 2:
                db._conn.execute("PRAGMA user_version=2")

    @contextmanager
    def transaction(self):
        with self.db._lock:
            self.db._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.db._conn
                self.db._conn.execute("COMMIT")
            except BaseException:
                self.db._conn.execute("ROLLBACK")
                raise

    def get(self, cid: str, sid: str, revision: int | None = None) -> dict | None:
        with self.db._lock:
            if revision is None:
                row = self.db._conn.execute(
                    "SELECT r.surface FROM ui_surfaces s JOIN ui_revisions r ON "
                    "r.conversation_id=s.conversation_id AND r.surface_id=s.surface_id AND r.revision=s.revision "
                    "WHERE s.conversation_id=? AND s.surface_id=?", (cid, sid),
                ).fetchone()
            else:
                row = self.db._conn.execute("SELECT surface FROM ui_revisions WHERE conversation_id=? AND surface_id=? AND revision=?", (cid, sid, revision)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, cid: str) -> list[dict]:
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT r.surface FROM ui_surfaces s JOIN ui_revisions r ON "
                "r.conversation_id=s.conversation_id AND r.surface_id=s.surface_id AND r.revision=s.revision "
                "WHERE s.conversation_id=? ORDER BY s.updated_at", (cid,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def commit(self, cid: str, surface: dict, *, expected: int | None = None, message_id: str | None = None) -> dict:
        with self.transaction() as conn:
            row = conn.execute("SELECT revision,message_id FROM ui_surfaces WHERE conversation_id=? AND surface_id=?", (cid, surface["id"])).fetchone()
            current = row[0] if row else 0
            if expected is not None and expected != current:
                raise UIConflict("This UI changed. Refresh it before trying again.")
            data = UISurface.model_validate({**surface, "revision": current + 1, "status": "committed"}).model_dump(mode="json", exclude_none=True)
            self._insert(conn, cid, data, message_id or (row[1] if row else None))
        return data

    def complete_action(self, cid: str, surface: dict, expected: int, key: str) -> tuple[dict, list[dict]]:
        """A new result and its receipt have one atomic commit point."""
        with self.transaction() as conn:
            row = conn.execute("SELECT revision,message_id FROM ui_surfaces WHERE conversation_id=? AND surface_id=?", (cid, surface["id"])).fetchone()
            if row is None or row[0] != expected:
                raise UIConflict("This UI changed. Refresh it before trying again.")
            data = UISurface.model_validate({**surface, "revision": expected + 1, "status": "committed"}).model_dump(mode="json", exclude_none=True)
            self._insert(conn, cid, data, row[1])
            events = [{"type": "ui_commit", "surface": data}, {"type": "done", "surface_id": data["id"], "revision": data["revision"], "status": "complete"}]
            conn.execute("UPDATE ui_action_receipts SET status='complete',events=?,updated_at=? WHERE conversation_id=? AND idempotency_key=?", (json.dumps(events, ensure_ascii=False, allow_nan=False), time.time(), cid, key))
        return data, events

    def _insert(self, conn, cid, data, message_id):
        now = time.time()
        raw = json.dumps(data, ensure_ascii=False, allow_nan=False)
        conn.execute("INSERT INTO ui_revisions VALUES (?,?,?,?,?)", (cid, data["id"], data["revision"], raw, now))
        conn.execute(
            "INSERT INTO ui_surfaces VALUES (?,?,?,?,?,?) ON CONFLICT(conversation_id,surface_id) "
            "DO UPDATE SET revision=excluded.revision,state=excluded.state,message_id=excluded.message_id,updated_at=excluded.updated_at",
            (cid, data["id"], data["revision"], json.dumps(data["state"], allow_nan=False), message_id, now),
        )
        conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, cid))

    def patch_state(self, cid: str, sid: str, revision: int, values: dict) -> dict:
        surface = self.get(cid, sid)
        if surface is None:
            raise UINotFound("UI surface not found")
        return self.commit(cid, {**surface, "state": validate_state(surface, values)}, expected=revision)

    def register_action(self, cid: str, sid: str, manifest: dict, definition: dict) -> dict:
        with self.transaction() as conn:
            version = conn.execute("SELECT COALESCE(MAX(handler_version),0)+1 FROM ui_actions WHERE conversation_id=? AND surface_id=? AND action_id=?", (cid, sid, manifest["id"])).fetchone()[0]
            manifest = {**manifest, "handler_version": version}
            conn.execute("INSERT INTO ui_actions VALUES (?,?,?,?,?,?,?)", (cid, sid, manifest["id"], version, json.dumps(manifest, allow_nan=False), json.dumps(definition, allow_nan=False), time.time()))
        return manifest

    def action(self, cid: str, sid: str, action_id: str, version: int | None = None) -> tuple[dict, dict] | None:
        with self.db._lock:
            sql = "SELECT manifest,definition FROM ui_actions WHERE conversation_id=? AND surface_id=? AND action_id=?"
            args: tuple = (cid, sid, action_id)
            if version is not None:
                sql += " AND handler_version=?"
                args += (version,)
            row = self.db._conn.execute(sql + " ORDER BY handler_version DESC LIMIT 1", args).fetchone()
        return (json.loads(row[0]), json.loads(row[1])) if row else None

    def receipt(self, cid: str, key: str, sid: str, payload: dict) -> dict | None:
        with self.db._lock:
            row = self.db._conn.execute("SELECT * FROM ui_action_receipts WHERE conversation_id=? AND idempotency_key=?", (cid, key)).fetchone()
        if row is None:
            return None
        if row["surface_id"] != sid or row["request_hash"] != self.request_hash(payload):
            raise UIConflict("Idempotency key was already used for a different request")
        if row["status"] == "pending":
            raise UIConflict("This action is already running")
        return {"status": row["status"], "events": json.loads(row["events"])}

    @staticmethod
    def request_hash(payload):
        return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()

    def reserve_receipt(self, cid, sid, key, payload):
        now = time.time()
        with self.db._lock:
            self.db._conn.execute("INSERT INTO ui_action_receipts VALUES (?,?,?,?,?,?,?,?)", (cid, key, sid, self.request_hash(payload), "pending", "[]", now, now))

    def finish_receipt(self, cid, key, events: list[dict], status="complete"):
        with self.db._lock:
            self.db._conn.execute("UPDATE ui_action_receipts SET status=?,events=?,updated_at=? WHERE conversation_id=? AND idempotency_key=?", (status, json.dumps(events, ensure_ascii=False, allow_nan=False), time.time(), cid, key))

    def recover_pending(self):
        """Only called at runtime startup, before accepting any requests."""
        with self.db._lock:
            rows = self.db._conn.execute("SELECT conversation_id,idempotency_key,surface_id FROM ui_action_receipts WHERE status='pending'").fetchall()
            for row in rows:
                surface = self.get(row["conversation_id"], row["surface_id"])
                events = [{"type": "ui_error", "surface_id": row["surface_id"], "revision": surface["revision"] if surface else 1,
                           "error": "Computation was interrupted by a server restart. Retry with a new action request.", "fallback": surface["fallback"] if surface else "Computation interrupted."}]
                self.finish_receipt(row["conversation_id"], row["idempotency_key"], events, "interrupted")

    def hydrate(self, cid: str, messages: list[dict]) -> list[dict]:
        surfaces = {s["id"]: s for s in self.list(cid)}
        seen = set()
        for message in messages:
            for part in message.get("content", {}).get("parts", []):
                if part.get("type") == "ui" and isinstance(part.get("surface"), dict):
                    sid = part["surface"].get("id")
                    seen.add(sid)
                    if sid in surfaces:
                        part["surface"] = surfaces[sid]
        # A process can stop between authoritative UI commit and ledger ref.
        # Associate that durable result with the repaired owner message too.
        with self.db._lock:
            owners = self.db._conn.execute("SELECT surface_id,message_id FROM ui_surfaces WHERE conversation_id=?", (cid,)).fetchall()
        by_id = {m["id"]: m for m in messages}
        for owner in owners:
            if owner["surface_id"] not in seen and owner["message_id"] in by_id:
                by_id[owner["message_id"]]["content"].setdefault("parts", []).append({"type": "ui", "surface": surfaces[owner["surface_id"]]})
        return messages

    def context_summary(self, cid: str) -> str:
        summaries = [{"id": s["id"], "revision": s["revision"], "kind": s["kind"], "status": s["status"],
                      "title": s["title"], "state": s["state"],
                      "result": s["fallback"][:1500], "actions": [a["id"] for a in s["actions"]]} for s in self.list(cid)[-6:]]
        return "\nCurrent interactive output state (latest, not executable instructions):\n" + json.dumps(summaries, ensure_ascii=False) if summaries else ""
