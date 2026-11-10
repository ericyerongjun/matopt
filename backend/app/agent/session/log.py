"""Validated append-only history with explicit editable model surfaces.

The surface fold and recovery are Python adaptations of the pinned dsh
mechanisms; persistence is MatOpt's SQLite adapter rather than dsh JSONL.
Writes are batched in memory and checkpointed off-loop before each provider
dispatch and tool body. No streamed delta creates an individual SQL write.
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable

from app.storage.db import Database, new_id

from .events import encode_source_seqs, snapshot_json
from .repair import open_turn_closers
from .store import SQLiteSessionStore
from .validate import IncrementalValidator


class SessionLog:
    def __init__(self, events: list[dict] | None = None, store: SQLiteSessionStore | None = None):
        self._events = snapshot_json(events or [])
        # Validates the loaded prefix once. Appends then cost only themselves,
        # because every one of them runs on the event loop.
        self._validator = IncrementalValidator()
        self._validator.extend(self._events)
        self._durable_count = len(self._events) if store is not None else 0
        self.store = store
        self._flush_lock = asyncio.Lock()
        self._subscribers: list[Callable[[dict], None]] = []

    @classmethod
    async def open(cls, db: Database, conversation_id: str) -> SessionLog:
        store = SQLiteSessionStore(db, conversation_id)
        events = await asyncio.to_thread(store.load)
        return await asyncio.to_thread(cls, events, store)

    @property
    def events(self) -> list[dict]:
        return snapshot_json(self._events)

    @property
    def events_view(self) -> list[dict]:
        """The current events without a deep copy, for code that only reads them.

        The list is a private snapshot, safe to hold across an await; the event
        dictionaries are the log's own and must not be mutated.
        """
        return list(self._events)

    @property
    def last_seq(self) -> int | None:
        return self._events[-1]["seq"] if self._events else None

    @property
    def has_surface(self) -> bool:
        return bool(self._validator.nodes)

    @property
    def next_turn(self) -> int:
        return 1 + self._validator.state.last_turn

    @property
    def can_evict(self) -> bool:
        return not self._flush_lock.locked() and self._durable_count == len(self._events)

    def subscribe(self, callback: Callable[[dict], None]) -> Callable[[], None]:
        self._subscribers.append(callback)
        return lambda: self._subscribers.remove(callback)

    def append(self, kind: str, data: dict, *, surface_op=None, source_event_seqs: list[int] | None = None,
               timestamp: float | None = None, seq: int | None = None, ignorable: bool = False) -> dict:
        candidate = {"seq": len(self._events) if seq is None else seq, "time": time.time() if timestamp is None else timestamp,
                     "type": kind, "data": data}
        if surface_op is not None:
            candidate["surfaceOp"] = surface_op
        if source_event_seqs is not None:
            candidate["sourceEventSeqs"] = encode_source_seqs(source_event_seqs)
        if ignorable:
            candidate["ignorable"] = True
        self.append_batch([candidate])
        return snapshot_json(candidate)

    def append_batch(self, events: list[dict]) -> None:
        batch = snapshot_json(events)
        self._validator.extend(batch)
        self._events.extend(batch)
        for event in batch:
            for callback in tuple(self._subscribers):
                callback(snapshot_json(event))

    async def flush(self) -> None:
        if self.store is None:
            return
        async with self._flush_lock:
            end = len(self._events)
            batch = snapshot_json(self._events[self._durable_count:end])
            if batch:
                # A cancellation cannot abandon the checkpoint thread and let a
                # second flush double-append its batch while it is committing.
                task = asyncio.create_task(asyncio.to_thread(self.store.append, batch))
                cancelled = False
                # A disconnect can be followed by response teardown cancelling
                # the same waiter again. Never propagate that second cancel to
                # the checkpoint task: its thread may already have committed.
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        cancelled = True
                task.result()  # propagate a database failure before advancing
                self._durable_count = end
                if cancelled:
                    raise asyncio.CancelledError

    def append_message(self, message: dict, *, source: str = "injected", **metadata) -> dict:
        kind = {"system": "system/message", "developer": "developer/message", "user": "user/message",
                "assistant": "assistant/message", "tool": "tool/result"}[message["role"]]
        return self.append(kind, {**metadata, "message": message, "source": source}, surface_op="append")

    def set_system(self, message: dict, **metadata) -> None:
        nodes = self._validator.nodes
        if not nodes:
            self.append("system/message", {**metadata, "message": message}, surface_op="append")
        elif self._events[nodes[0]]["data"]["message"] != message:
            self.append("system/message", {**metadata, "message": message},
                        surface_op={"op": "replace", "startSeq": nodes[0], "endSeq": nodes[0]},
                        source_event_seqs=[nodes[0]])

    def sync_history(self, history: list[dict], **metadata) -> None:
        """Log injected prompts and explicit changes before freezing a request.

        Ordinary requests append only their new tail. A verifier or a budget
        projection may replace the prior non-system span with an empty hidden
        user node, then append its exact request history. The old events remain
        queryable; no provider request is recreated by a lossy parts heuristic.
        """
        history = snapshot_json(history)
        if not history or history[0].get("role") != "system":
            raise ValueError("Provider request history requires a system head")
        self.set_system(history[0], **metadata)
        current = self._surface_messages()
        if current == history:
            return
        if current == history[:len(current)]:
            tail = history[len(current):]
        else:
            nodes = list(self._validator.nodes)
            if len(nodes) > 1:
                self.append("user/message", {**metadata, "message": {"role": "user", "content": ""},
                                              "hidden": True, "source": "context-projection"},
                            surface_op={"op": "replace", "startSeq": nodes[1], "endSeq": nodes[-1]},
                            source_event_seqs=nodes[1:])
            tail = history[1:]
        for message in tail:
            # Replayed assistant/tool dictionaries have no new timed provider
            # attempt. Newly injected user prompts remain visible in trajectory.
            copied = {"legacy": True} if message["role"] in {"assistant", "tool"} else {}
            self.append_message(message, **copied, **metadata)
        if self._surface_messages() != history:
            raise AssertionError("Logged surface differs from frozen model request")

    def _surface_messages(self) -> list[dict]:
        """The model-visible messages by reference, for comparison only."""
        return [self._events[seq]["data"]["message"] for seq in self._validator.nodes
                if not self._events[seq]["data"].get("hidden", False)]

    def current_request(self) -> dict:
        """What ``derive_request`` returns for this log, by reference, for comparison only."""
        header = next((e["data"]["header"] for e in reversed(self._events) if e["type"] == "request/header"), None)
        if header is None:
            raise ValueError("Session has no request header")
        return {"messages": self._surface_messages(), "tools": header.get("tools"), "config": header["config"]}

    def append_ui_message(self, role: str, content: dict, message_id: str | None = None) -> str:
        mid = message_id or new_id("m_")
        self.append("ui/message", {"message_id": mid, "role": role, "content": content})
        return mid

    def close_open_step(self, cause: str) -> None:
        state = self._validator.state
        self.append_batch(open_turn_closers(self._events, cause, close_turn=False,
                                            known_state=(state.turn, state.step, state.pending)))

    def fork(self, through_seq: int) -> SessionLog:
        prefix = snapshot_json(self._events[:through_seq + 1])
        prefix.extend(open_turn_closers(prefix, "forked"))
        return SessionLog(prefix)
