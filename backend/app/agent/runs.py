"""Own agent executions independently of HTTP subscribers.

Only the harness's public projection enters this journal. A reconnect reads
the committed prefix and subscribes from the same cursor; it never restarts a
tool or a model request. The private session journal remains separate.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import time
from copy import deepcopy
from collections.abc import AsyncIterator
from typing import Any

from app.agent.events import Event, ev
from app.agent.harness import RunRequest
from app.agent.presentation import public_content, public_work, public_counts
from app.storage.db import Database, new_id

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = frozenset({"queued", "running", "paused"})
TERMINAL_STATUSES = frozenset({"completed", "stopped", "failed", "interrupted"})
RUN_FIELDS = ("id", "conversation_id", "message_id", "status", "created_at", "updated_at", "last_seq")


class RunConflict(ValueError):
    pass


def public_run(run: dict | None) -> dict | None:
    return {key: run[key] for key in RUN_FIELDS} if run else None


def _strings(data: dict, *keys: str) -> dict:
    return {key: data[key] for key in keys if isinstance(data.get(key), str)}


def _usage(data: dict) -> dict:
    return public_counts(data)


def project_public_event(event: Event) -> Event | None:
    """Allowlisted wire contract, with nested activity and UI validation.

    In particular this cannot journal raw tool arguments/results, reasoning,
    model requests, plans, or arbitrary diagnostic events. Text deltas are
    additionally gated on an explicit final_start by RunManager._drive.
    """
    kind, data = event.type, event.data
    out: dict[str, Any]
    if kind == "conversation":
        out = _strings(data, "title")
    elif kind == "message_start":
        out = _strings(data, "message_id", "model", "provider")
        if data.get("reasoning") in {"off", "low", "high", "max"}:
            out["reasoning"] = data["reasoning"]
    elif kind == "work":
        out = {"work": public_work(data.get("work"))}
    elif kind == "activity":
        from app.agent.activity import public_activity
        item = public_activity(data.get("item"))
        if not item:
            return None
        out = {"item": item}
    elif kind == "transcript":
        from app.agent.transcript import public_transcript
        entries = public_transcript([data.get("entry")])
        if not entries:
            return None
        out = {"entry": entries[0]}
    elif kind == "progress":
        from app.agent.activity import public_progress
        update = public_progress(data.get("update"))
        if not update:
            return None
        out = {"update": update}
    elif kind == "result_block":
        from app.agent.results import public_result
        block = public_result(data.get("block"))
        message_id = data.get("message_id")
        if not block or not isinstance(message_id, str) or not message_id or not block["id"].startswith(message_id + ":result:"):
            return None
        out = {"message_id": message_id, "block": block}
    elif kind == "run_state":
        if data.get("status") not in ACTIVE_STATUSES | TERMINAL_STATUSES:
            return None
        out = {"status": data["status"]}
    elif kind == "final_start":
        out = _strings(data, "message_id")
        if data.get("mode") in {"streaming", "buffered"}:
            out["mode"] = data["mode"]
    elif kind == "text_delta":
        out = _strings(data, "delta")
        if not out:
            return None
    elif kind == "artifact":
        artifact = data.get("artifact")
        if not isinstance(artifact, dict) or artifact.get("kind") not in {
            "image", "plotly", "html", "file", "markdown", "json", "svg", "text",
        }:
            return None
        out = {"artifact": {key: artifact[key] for key in
               ("kind", "name", "path", "url", "mime", "size", "data", "digest") if key in artifact}}
    elif kind in {"ui_start", "ui_patch", "ui_commit"}:
        from app.ui.contracts import UISurface
        try:
            surface = UISurface.model_validate(data.get("surface"))
        except (TypeError, ValueError):
            return None
        # Executable HTML is published only as a complete committed surface.
        if surface.kind == "mini_app" and (kind != "ui_commit" or surface.status != "committed"):
            return None
        out = {"surface": surface.model_dump(mode="json", exclude_none=True)}
    elif kind == "ui_error":
        out = _strings(data, "surface_id", "fallback")
        if type(data.get("revision")) is int:
            out["revision"] = data["revision"]
        out["error"] = "The interactive view could not be completed."
    elif kind == "usage":
        out = _usage(data)
    elif kind == "options":
        out = {"items": [item for item in data.get("items", []) if isinstance(item, str)][:8]}
    elif kind == "control_ack":
        if data.get("action") not in {"pause", "resume", "stop", "steer"} or data.get("status") not in {"queued", "applied"}:
            return None
        out = _strings(data, "action", "status", "control_id")
    elif kind == "steer_applied":
        # The user's steering text is already visible as their own message.
        out = _strings(data, "text")
        for key in ("skills", "tools"):
            out[key] = [value for value in data.get(key, []) if isinstance(value, str)]
    elif kind == "message_end":
        out = _strings(data, "message_id", "status")
        out["content"] = public_content(data.get("content"))
    elif kind in {"done", "stopped"}:
        out = _strings(data, "message_id")
    elif kind == "error":
        out = _strings(data, "message_id")
        if data.get("code") == "interrupted":
            out.update(code="interrupted", message="This run was interrupted by a server restart. Its completed work is saved; no actions were repeated.")
        else:
            out["message"] = "The run could not be completed. Its completed work is saved."
    else:
        return None
    return ev(kind, **out)


class RunManager:
    def __init__(self, db: Database, harness, *, heartbeat_seconds: float = 15.0,
                 stop_grace_seconds: float = 2.0):
        self.db = db
        self.harness = harness
        self.heartbeat_seconds = heartbeat_seconds
        self.stop_grace_seconds = stop_grace_seconds
        self._tasks: dict[str, asyncio.Task] = {}
        self._conditions: dict[str, asyncio.Condition] = {}
        self._start_lock = asyncio.Lock()
        self._stop_requested: set[str] = set()
        self._stop_watchdogs: dict[str, asyncio.Task] = {}
        self._pending_controls: dict[str, list[tuple[str, dict]]] = {}
        self._claimed_runs: set[str] = set()
        self._shutting_down = False

    def get(self, run_id: str) -> dict | None:
        return public_run(self.db.get_run(run_id))

    def latest(self, cid: str) -> dict | None:
        return public_run(self.db.latest_run(cid, active_only=True) or self.db.latest_run(cid))

    def is_running(self, cid: str) -> bool:
        return self.db.latest_run(cid, active_only=True) is not None

    @staticmethod
    def _fingerprint(req: RunRequest, cid: str) -> str:
        payload = dataclasses.asdict(req)
        payload.pop("run_id", None)
        payload["conversation_id"] = cid
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()

    async def start(self, req: RunRequest, client_run_id: str | None = None) -> dict:
        async with self._start_lock:
            if self._shutting_down:
                raise RunConflict("The server is shutting down. Please reconnect shortly.")
            if client_run_id:
                previous = self.db.get_run_by_client_id(client_run_id)
                if previous:
                    cid = previous["conversation_id"]
                    if (req.conversation_id not in (None, cid)
                            or self._fingerprint(req, cid) != previous["request_hash"]):
                        raise RunConflict("This client_run_id was already used for a different request.")
                    return public_run(previous)  # type: ignore[return-value]
            cid = req.conversation_id
            if cid and (self.is_running(cid) or self.harness.is_running(cid)):
                raise RunConflict("A run is already in progress for this conversation.")
            conversation = self.db.get_conversation(cid) if cid else None
            if conversation is None:
                conversation = self.db.create_conversation(
                    title=" ".join(req.text.split())[:80] or "New chat", conversation_id=cid,
                    provider=req.provider_id, model=req.model_id, reasoning=req.reasoning,
                )
            cid = conversation["id"]
            run_id = new_id("r_")
            run = self.db.create_run(run_id, cid, self._fingerprint(req, cid), client_run_id)
            values = {"conversation_id": cid}
            if "run_id" in {field.name for field in dataclasses.fields(req)}:
                values["run_id"] = run_id
            request = dataclasses.replace(req, **values)
            self._conditions[run_id] = asyncio.Condition()
            await self._publish(run_id, ev("conversation", title=conversation["title"]))
            await self._publish(run_id, ev("run_state", status="queued"))
            self._tasks[run_id] = asyncio.create_task(self._drive(run_id, request), name=f"agent-run:{run_id}")
            return self.get(run["id"])  # type: ignore[return-value]

    async def recover(self) -> None:
        """Seal abandoned runs; recovery never invokes a model or a tool."""
        for run in self.db.active_runs():
            if run["id"] in self._tasks:
                continue
            prepare = getattr(self.harness, "prepare_session", None)
            if prepare:
                await prepare(run["conversation_id"])
            if not run["message_id"]:
                # The private checkpoint may have committed message_start
                # just before the public consumer received its identity.
                recovered = [message for message in self.db.list_messages(run["conversation_id"])
                             if message["role"] == "assistant" and message["created_at"] >= run["created_at"]]
                if recovered:
                    run["message_id"] = recovered[-1]["id"]
            saved = self.db.get_message(run["message_id"]) if run["message_id"] else None
            # A crash after the final answer's durable commit but before its
            # terminal SSE frame must not turn successful work into failure.
            completed = bool(saved and saved["content"].get("status") == "complete"
                             and saved["content"].get("final_ready"))
            work = self._recovered_work(run, interrupted=not completed)
            content = deepcopy(saved["content"]) if saved else {"parts": [], "final_text": "", "final_ready": False}
            content["work"] = work
            if not completed:
                from app.agent.results import result_parts
                content["parts"] = [*content.get("parts", []), *self._recovered_results(run)]
                public_blocks = result_parts(content["parts"])
                content["parts"] = [part for part in content["parts"] if part.get("type") != "result_block"] + public_blocks
                content.update(status="stopped", recovered=True,
                               notice="This run was interrupted by a server restart. Its completed work is saved; no actions were repeated.")
            persist = getattr(self.harness, "persist_recovered_run", None)
            if run["message_id"]:
                if persist:
                    await persist(run["conversation_id"], run["message_id"], content)
                elif saved:
                    self.db.update_message(run["message_id"], content)
                else:
                    self.db.add_message(run["conversation_id"], "assistant", content, run["message_id"])
            await self._publish(run["id"], ev("work", work=work))
            if run["message_id"]:
                await self._publish(run["id"], ev("message_end", message_id=run["message_id"], status=content.get("status"), content=content))
            status = "completed" if completed else "interrupted"
            await self._publish(run["id"], ev("run_state", status=status))
            await self._publish(run["id"], ev("done", message_id=run["message_id"]) if completed else
                                ev("error", code="interrupted", message_id=run["message_id"]), status=status)

    def _recovered_results(self, run: dict) -> list[dict]:
        from app.agent.results import result_parts
        parts = []
        cursor = 0
        while rows := self.db.list_run_events(run["id"], cursor):
            for row in rows:
                data = row["data"]
                if row["type"] == "result_block" and data.get("message_id") == run["message_id"]:
                    parts.append({"type": "result_block", "block": data.get("block")})
                cursor = row["seq"]
            # Bound intermediate memory as well as the resulting projection.
            parts = result_parts(parts)
        return parts

    def _recovered_work(self, run: dict, *, interrupted: bool) -> dict:
        """Fold only public journal data into the canonical recovered history."""
        work: dict = {"status": "running", "steps": [], "tool_count": 0, "started_at": run["created_at"]}
        items: dict[str, dict] = {}
        cursor = 0
        while rows := self.db.list_run_events(run["id"], cursor):
            for row in rows:
                kind, data = row["type"], row["data"]
                if kind == "work":
                    work.update(deepcopy(data.get("work") or {}))
                    for item in work.get("items", []):
                        if isinstance(item, dict) and isinstance(item.get("id"), str):
                            items[item["id"]] = item
                elif kind == "activity":
                    item = data.get("item") or {}
                    if isinstance(item.get("id"), str):
                        items[item["id"]] = deepcopy(item)
                elif kind == "progress":
                    work["current_progress"] = deepcopy(data.get("update"))
                    work["milestones"] = [*work.get("milestones", []), deepcopy(data.get("update"))][-12:]
                cursor = row["seq"]
        now = time.time()
        for item in items.values():
            if item.get("status") in {"queued", "running", "paused"}:
                item.update(status="failed" if interrupted else "completed", finished_at=now, updated_at=now,
                            seq=int(item.get("seq", 0)) + 1,
                            summary="Interrupted before completion." if interrupted else "Completed.")
        for step in work.get("steps", []):
            if step.get("status") == "running":
                step["status"] = "error" if interrupted else "complete"
        work.update(status="error" if interrupted else "complete", finished_at=now, items=list(items.values()))
        work["tool_count"] = max(work.get("tool_count", 0), sum(item.get("kind") in {"tool", "agent"} for item in items.values()))
        return public_work(work)

    async def _publish(self, run_id: str, event: Event, *, status: str | None = None) -> Event | None:
        projected = project_public_event(event)
        if projected is None:
            return None
        if projected.type == "run_state":
            status = projected.data["status"]
        message_id = projected.data.get("message_id")
        row = self.db.append_run_event(run_id, projected.type, projected.data, status=status, message_id=message_id)
        condition = self._conditions.get(run_id)
        if condition:
            async with condition:
                condition.notify_all()
        return Event(row["type"], row["data"], row["seq"])

    async def _drive(self, run_id: str, req: RunRequest) -> None:
        terminal: Event | None = None
        final_started = False
        message_ended = False
        iterator = None

        async def on_claim(cid: str) -> None:
            # This hook runs in the producer before any provider or tool work.
            # A queued message_start frame can reach its consumer after a fast
            # producer has already finished, so it is not a control boundary.
            self._claimed_runs.add(run_id)
            if run_id in self._stop_requested:
                self.harness.stop(cid)
            for action, options in self._pending_controls.pop(run_id, []):
                if action == "pause":
                    self.harness.pause(cid)
                elif action == "steer":
                    self.harness.enqueue_steer(cid, **options)

        try:
            if run_id in self._stop_requested:
                terminal = ev("stopped")
            else:
                await self._publish(run_id, ev("run_state", status="running"))
                iterator = self.harness.run(req, on_claim=on_claim)
                async for event in iterator:
                    if event.type == "keepalive":
                        continue  # Transport heartbeats are comments, never journal entries.
                    if event.type == "final_start":
                        final_started = True
                    if event.type == "message_end":
                        message_ended = True
                    if event.type == "text_delta" and not final_started:
                        continue
                    if event.type in {"done", "stopped", "error"}:
                        terminal = event
                        continue  # Commit only after the harness finishes saving its answer.
                    await self._publish(run_id, event)
        except asyncio.CancelledError:
            terminal = ev("stopped") if run_id in self._stop_requested else ev("error", code="interrupted")
        except Exception:  # noqa: BLE001
            logger.exception("Agent run %s failed", run_id)
            terminal = ev("error")
        finally:
            if iterator is not None:
                try:
                    await iterator.aclose()
                except Exception:  # noqa: BLE001
                    logger.exception("Could not close run %s", run_id)
            if self.db.get_run(run_id) is not None:
                run = self.db.get_run(run_id)
                saved = self.db.get_message(run["message_id"]) if run and run["message_id"] else None
                if self._shutting_down:
                    # The answer commit is authoritative even if shutdown
                    # arrives before the final SSE frame reaches this task.
                    terminal = (ev("done", message_id=saved["id"])
                                if saved and saved["content"].get("status") == "complete" and saved["content"].get("final_ready")
                                else ev("error", code="interrupted"))
                elif run_id in self._stop_requested and (terminal is None or terminal.type != "done"):
                    terminal = ev("stopped")
                terminal = terminal or ev("error")
                status = ("interrupted" if terminal.data.get("code") == "interrupted" else
                          {"done": "completed", "stopped": "stopped", "error": "failed"}[terminal.type])
                if saved and not message_ended:
                    await self._publish(run_id, ev("message_end", message_id=saved["id"],
                                                  status=saved["content"].get("status", "error"), content=saved["content"]))
                await self._publish(run_id, ev("run_state", status=status))
                await self._publish(run_id, terminal, status=status)
            self._stop_requested.discard(run_id)
            self._claimed_runs.discard(run_id)
            self._pending_controls.pop(run_id, None)
            watchdog = self._stop_watchdogs.pop(run_id, None)
            if watchdog is not None:
                watchdog.cancel()
            self._tasks.pop(run_id, None)
            condition = self._conditions.get(run_id)
            if condition:
                async with condition:
                    condition.notify_all()
            self._conditions.pop(run_id, None)

    async def subscribe(self, run_id: str, after: int = 0) -> AsyncIterator[Event | None]:
        """Replay then tail a run. Closing this iterator never closes its run."""
        if self.db.get_run(run_id) is None:
            raise KeyError(run_id)
        cursor = after
        while True:
            condition = self._conditions.get(run_id)
            if condition is None:
                rows = self.db.list_run_events(run_id, cursor)
                for row in rows:
                    cursor = row["seq"]
                    yield Event(row["type"], row["data"], row["seq"])
                if rows:
                    continue
                break
            # Reading and registering the waiter under the same condition
            # prevents a lost wake-up between the prefix and live stream.
            async with condition:
                rows = self.db.list_run_events(run_id, cursor)
                run = self.db.get_run(run_id)
                if not rows:
                    if run is None or (run["status"] in TERMINAL_STATUSES and run_id not in self._tasks):
                        break
                    try:
                        await asyncio.wait_for(condition.wait(), self.heartbeat_seconds)
                    except asyncio.TimeoutError:
                        pass
            if not rows:
                yield None
                continue
            for row in rows:
                cursor = row["seq"]
                yield Event(row["type"], row["data"], row["seq"])

    async def acknowledge(self, cid: str, action: str, *, status: str = "queued") -> str | None:
        run = self.db.latest_run(cid, active_only=True)
        if not run:
            return None
        await self._publish(run["id"], ev("control_ack", action=action, status=status, control_id=new_id("ctl_")))
        return run["id"]

    async def pause(self, cid: str) -> tuple[bool, str | None]:
        run = self.db.latest_run(cid, active_only=True)
        if run is None:
            return False, None
        if not self.harness.pause(cid):
            # Accepted start and harness claim are distinct boundaries. Keep
            # controls that arrive in between rather than losing the click.
            if run["id"] not in self._tasks or run["id"] in self._claimed_runs or self.harness.is_running(cid):
                return False, None
            pending = self._pending_controls.setdefault(run["id"], [])
            if not any(action == "pause" for action, _ in pending):
                pending.append(("pause", {}))
        return True, await self.acknowledge(cid, "pause")

    async def resume(self, cid: str) -> tuple[bool, str | None]:
        run = self.db.latest_run(cid, active_only=True)
        if run is None:
            return False, None
        pending = self._pending_controls.get(run["id"], [])
        had_pause = any(action == "pause" for action, _ in pending)
        if had_pause:
            self._pending_controls[run["id"]] = [(action, options) for action, options in pending if action != "pause"]
        if not self.harness.resume(cid) and not had_pause:
            return False, None
        return True, await self.acknowledge(cid, "resume")

    async def steer(self, cid: str, text: str, *, skills: list[str] | None = None,
                    tools: list[str] | None = None) -> tuple[bool, str | None]:
        run = self.db.latest_run(cid, active_only=True)
        if run is None:
            return False, None
        options = {"text": text, "skills": skills or [], "tools": tools or []}
        if not self.harness.enqueue_steer(cid, **options):
            if run["id"] not in self._tasks or run["id"] in self._claimed_runs or self.harness.is_running(cid):
                return False, None
            self._pending_controls.setdefault(run["id"], []).append(("steer", options))
        return True, await self.acknowledge(cid, "steer")

    async def stop(self, cid: str) -> tuple[bool, str | None]:
        run = self.db.latest_run(cid, active_only=True)
        if run is None:
            return self.harness.stop(cid), None
        run_id = run["id"]
        self._stop_requested.add(run_id)
        await self.acknowledge(cid, "stop")
        self.harness.stop(cid)
        if run_id not in self._stop_watchdogs:
            self._stop_watchdogs[run_id] = asyncio.create_task(self._cancel_unresponsive(run_id))
        return True, run_id

    async def _cancel_unresponsive(self, run_id: str) -> None:
        await asyncio.sleep(self.stop_grace_seconds)
        task = self._tasks.get(run_id)
        if task and not task.done():
            task.cancel()

    async def wait(self, run_id: str) -> None:
        task = self._tasks.get(run_id)
        if task:
            await asyncio.shield(task)

    async def cancel_and_wait(self, cid: str) -> None:
        """Drain cleanup before deleting a conversation or its workspace."""
        _, run_id = await self.stop(cid)
        if run_id:
            await self.wait(run_id)

    async def shutdown(self) -> None:
        self._shutting_down = True
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Cancellation before a task's first instruction cannot run its
        # finally block. Seal those queued records as interrupted as well.
        self._tasks = {run_id: task for run_id, task in self._tasks.items() if not task.done()}
        await self.recover()
        watchdogs = tuple(self._stop_watchdogs.values())
        for task in watchdogs:
            task.cancel()
        if watchdogs:
            await asyncio.gather(*watchdogs, return_exceptions=True)
        self._stop_watchdogs.clear()
