"""SQLite transaction/checkpoint seam for the in-memory session log."""

from __future__ import annotations

from app.storage.db import Database

from .repair import open_turn_closers
from .validate import validate_events


class SQLiteSessionStore:
    def __init__(self, db: Database, conversation_id: str):
        self.db = db
        self.conversation_id = conversation_id

    def load(self, *, repair: bool = True) -> list[dict]:
        events = self.db.list_session_events(self.conversation_id)
        validate_events(events)
        if repair:
            closers = open_turn_closers(events)
            if closers:
                self.db.append_session_events(self.conversation_id, closers)
                events.extend(closers)
        return events

    def append(self, events: list[dict]) -> None:
        self.db.append_session_events(self.conversation_id, events)
