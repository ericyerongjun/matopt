"""The public execution view. Never project raw prompts, arguments or stdout.

The private session journal and this presentation have deliberately different
schemas. Item identities survive repeated tools, parallel completion and replay.
All timestamps are Unix seconds; item seq is the public projection revision.
The run journal supplies a separate, monotonically increasing SSE cursor.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import PurePosixPath
import re
import time
from urllib.parse import urlsplit

from app.agent.events import ev

STATUSES = {"queued", "running", "completed", "failed", "cancelled", "paused"}
PHASES = {"work", "check", "final"}
KINDS = {"phase", "tool", "agent", "control"}
TOOLS = {
    "parse_document": ("Reading document", "Extract text and understand the uploaded pages"),
    "read_file": ("Reading file", "Read the relevant workspace content"),
    "list_files": ("Inspecting files", "Locate files for this task"),
    "grep": ("Searching files", "Find relevant content in workspace files"),
    "write_file": ("Writing file", "Save an output in the workspace"),
    "edit_file": ("Updating file", "Apply changes to a workspace file"),
    "web_search": ("Searching the web", "Find current information and sources"),
    "web_fetch": ("Reading source", "Read a retrieved web source"),
    "python": ("Running Python", "Compute and check results"),
    "bash": ("Running command", "Carry out the requested workspace operation"),
    "check_equivalence": ("Checking expressions", "Compare mathematical expressions"),
    "wolfram_alpha": ("Computing", "Evaluate the requested calculation"),
    "data_agent": ("Data analysis agent", "Analyze the workspace data and produce results"),
    "render_ui": ("Creating view", "Build an interactive result"),
    "create_ui_app": ("Creating app", "Build and validate an interactive app"),
    "register_ui_action": ("Connecting interaction", "Connect a result to a workspace action"),
    "load_skill": ("Loading skill", "Load the instructions for the selected skill"),
    "search_skills": ("Finding skill", "Find relevant task instructions"),
    "update_plan": ("Updating plan", "Track the task's public milestones"),
    "find_files": ("Finding files", "Locate files by approximate name"),
    "bash_poll": ("Checking background job", "Read the output of a running job"),
    "spawn_agent": ("Starting sub-agent", "Hand a self-contained task to a helper agent"),
    "wait_agent": ("Waiting for sub-agents", "Collect results from helper agents"),
    "send_message": ("Messaging sub-agent", "Give a helper agent more information"),
    "followup_task": ("Assigning follow-up", "Give a helper agent its next task"),
    "interrupt_agent": ("Stopping sub-agent", "Stop a helper agent's current work"),
    "list_agents": ("Listing sub-agents", "Check the status of helper agents"),
    "memory_search": ("Searching memories", "Recall relevant notes from earlier conversations"),
    "memory_read": ("Reading memory", "Read a remembered note"),
    "memory_note": ("Saving memory", "Remember what the user asked to keep"),
    "list_mcp_resources": ("Listing resources", "Find resources offered by a connected server"),
    "read_mcp_resource": ("Reading resource", "Read a resource from a connected server"),
}


def _text(value, limit=240):
    if not isinstance(value, str):
        return ""
    return re.sub(r"[\x00-\x1f\x7f]", " ", value).strip()[:limit]


def _number(value, default=0):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else default


def public_output(value):
    if not isinstance(value, dict) or value.get("kind") not in {"file", "source", "artifact", "ui"}:
        return None
    out = {"kind": value["kind"], "label": _text(value.get("label"), 160)}
    path = value.get("path")
    if isinstance(path, str) and path and not path.startswith(("/", "\\")) and ".." not in PurePosixPath(path).parts:
        out["path"] = _text(path, 512)
    url = value.get("url")
    if isinstance(url, str):
        try:
            parsed = urlsplit(url)
            if (parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password) or url.startswith("/api/files/"):
                out["url"] = url[:2048]
        except ValueError:
            pass
    if value.get("kind") == "ui" and isinstance(value.get("surface_id"), str):
        out["surface_id"] = _text(value["surface_id"], 100)
    return out if any(k in out for k in ("url", "path", "surface_id")) else None


def public_activity(item):
    """Nested allowlist shared by history, SSE and the durable run journal."""
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        return None
    if item.get("kind") not in KINDS or item.get("status") not in STATUSES or item.get("phase") not in PHASES:
        return None
    out = {"id": _text(item["id"], 160), "parent_id": _text(item.get("parent_id"), 160) or None,
           "seq": int(_number(item.get("seq"))), "kind": item["kind"], "phase": item["phase"],
           "label": _text(item.get("label"), 160), "purpose": _text(item.get("purpose")),
           "status": item["status"], "started_at": _number(item.get("started_at"), None),
           "updated_at": _number(item.get("updated_at")), "finished_at": _number(item.get("finished_at"), None),
           "attempt": max(1, int(_number(item.get("attempt"), 1))),
           "outputs": [p for value in (item.get("outputs") if isinstance(item.get("outputs"), list) else [])
                       if (p := public_output(value)) is not None]}
    if item.get("summary"):
        out["summary"] = _text(item["summary"], 400)
    if item.get("tool") and re.fullmatch(r"[\w.:-]{1,100}", str(item["tool"])):
        out["tool"] = item["tool"]
    progress = item.get("progress")
    if isinstance(progress, dict) and type(progress.get("completed")) in (int, float):
        out["progress"] = {"completed": _number(progress["completed"])}
        if _number(progress.get("total")) > 0:
            out["progress"]["total"] = max(progress["total"], out["progress"]["completed"])
        if progress.get("unit") in {"pages", "sources", "files", "checks", "bytes"}:
            out["progress"]["unit"] = progress["unit"]
    return out


def public_progress(update):
    if not isinstance(update, dict) or not isinstance(update.get("text"), str) or not update["text"].strip():
        return None
    out = {"id": _text(update.get("id"), 160), "seq": int(_number(update.get("seq"))),
           "text": _text(update["text"], 400), "created_at": _number(update.get("created_at")),
           "phase": update.get("phase") if update.get("phase") in PHASES else "work"}
    if isinstance(update.get("item_id"), str):
        out["item_id"] = _text(update["item_id"], 160)
    return out


class PublicActivity:
    def __init__(self, message_id, work, emit, *, enabled=True):
        self.id = message_id
        self.work = work
        self.emit = emit
        self.enabled = enabled
        self.seq = 0
        self.phase = "work"
        self.items = {}
        self.calls = {}
        self.attempts = {}
        self.plan_parent = None
        self.work.update(items=[], started_at=time.time(), current_progress=None)

    async def item(self, item_id, **changes):
        now = time.time()
        previous = self.items.get(item_id, {})
        value = {"id": item_id, "parent_id": None, "seq": 0, "kind": "tool", "phase": self.phase,
                 "label": "Working", "purpose": "Carry out the requested task", "status": "queued",
                 "started_at": None, "finished_at": None, "attempt": 1, "outputs": [], **previous, **changes}
        if value["status"] == "running" and value["started_at"] is None:
            value["started_at"] = now
        if value["status"] in {"completed", "failed", "cancelled"}:
            value["finished_at"] = now
        elif value["status"] in {"running", "paused"}:
            value["finished_at"] = None
        self.seq += 1
        value.update(seq=self.seq, updated_at=now)
        value = public_activity(value)
        self.items[item_id] = value
        self.work["items"] = list(self.items.values())
        if self.enabled:
            await self.emit(ev("activity", item=deepcopy(value)))
        return value

    async def progress(self, text, item_id=None):
        text = _text(text, 400)
        if not text or text == (self.work.get("current_progress") or {}).get("text"):
            return
        self.seq += 1
        update = public_progress({"id": f"{self.id}:update:{self.seq}", "seq": self.seq, "text": text,
                                  "phase": self.phase, "created_at": time.time(), "item_id": item_id})
        self.work["current_progress"] = update
        self.work["milestones"] = [*self.work.get("milestones", []), update][-12:]
        if self.enabled:
            await self.emit(ev("progress", update=update))

    async def set_phase(self, phase):
        old_id = f"{self.id}:phase:{self.phase}"
        if self.phase != phase and old_id in self.items:
            await self.item(old_id, status="completed")
        self.phase = phase
        label, purpose = {"work": ("Working", "Gather information and carry out the task"),
                          "check": ("Checking results", "Verify the relevant results and correct any issues"),
                          "final": ("Preparing final answer", "Present the checked results and deliverables")}[phase]
        return await self.item(f"{self.id}:phase:{phase}", kind="phase", phase=phase,
                               label=label, purpose=purpose, status="running")

    async def queued(self, step, call_id, name, arguments):
        if name in {"report_progress", "publish_result"}:
            return
        key = (step, call_id)
        item_id = f"{self.id}:tool:{step}:{call_id}"
        self.calls[key] = item_id
        label, purpose = TOOLS.get(name, ("Using tool", "Carry out the requested tool operation"))
        # Only the safe file target is public. Code, query strings, prompts and
        # all remaining arguments stay in the execution journal.
        path = arguments.get("path") if isinstance(arguments, dict) else None
        if isinstance(path, str) and path and not path.startswith("/") and ".." not in PurePosixPath(path).parts:
            label += " · " + _text(PurePosixPath(path).name, 90)
        # Retry identity is private and includes the full operation. A different
        # Python calculation after a failed one is not automatically a retry.
        fingerprint = (name, hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str).encode()).hexdigest())
        previous = self.attempts.get(fingerprint)
        attempt = previous[1] + 1 if previous and self.items.get(previous[0], {}).get("status") == "failed" else 1
        self.attempts[fingerprint] = (item_id, attempt)
        await self.item(item_id, parent_id=self.plan_parent if self.phase == "work" and self.plan_parent else f"{self.id}:phase:{self.phase}",
                        kind="agent" if name == "data_agent" else "tool", tool=name,
                        label=label, purpose=purpose, status="queued", attempt=attempt)
        self.work["tool_count"] = len(self.calls)

    async def running(self, step, call_id):
        item_id = self.calls.get((step, call_id))
        if item_id:
            await self.item(item_id, status="running")
            await self.progress(self.items[item_id]["label"] + "…", item_id)

    async def measured(self, step, call_id, data):
        item_id = self.calls.get((step, call_id))
        if item_id and self.items[item_id]["status"] == "running":
            await self.item(item_id, progress=data)

    async def result(self, step, call_id, name, ok, meta, artifacts):
        item_id = self.calls.get((step, call_id))
        if not item_id:
            return
        outputs = []
        for artifact in artifacts or []:
            outputs.append({"kind": "file" if artifact.get("path") else "artifact",
                            "label": "Extracted document text" if name == "parse_document" else artifact.get("name", "Output"),
                            "path": artifact.get("path"), "url": artifact.get("url")})
        for source in meta.get("public_sources", []):
            if isinstance(source, dict):
                outputs.append({"kind": "source", "label": source.get("title", "Source"), "url": source.get("url")})
        surface_id = meta.get("ui_surface_id") or meta.get("surface_id")
        if surface_id:
            outputs.append({"kind": "ui", "label": "Interactive result", "surface_id": surface_id})
        cancelled = meta.get("cancelled") is True
        summary = "Completed." if ok else "Stopped before completion." if cancelled else "This action failed."
        progress = None
        if ok and name == "parse_document" and isinstance(meta.get("pages_read"), list):
            count = len(meta["pages_read"])
            total = _number(meta.get("total_pages"))
            summary = f"Read {count} of {total} pages." if total else f"Read {count} pages."
            progress = {"completed": count, "unit": "pages", **({"total": total} if total else {})}
            if meta.get("notes"):
                summary += " Some pages or details need review."
        elif ok and name == "web_search" and type(meta.get("count")) is int:
            summary = f"Retrieved {meta['count']} sources."
            progress = {"completed": meta["count"], "unit": "sources"}
        elif ok and outputs:
            summary = f"Produced {len(outputs)} output{'s' if len(outputs) != 1 else ''}."
        elif name in {"bash", "bash_poll"} and meta.get("running") is True:
            # A background job is still going; it is not "completed" yet.
            summary = f"Running in the background (job {_text(str(meta.get('session_id')), 16)})."
        elif name in {"python", "bash", "bash_poll"} and type(meta.get("exit_code")) is int:
            summary = "Computation completed." if ok else f"Computation failed (exit {meta['exit_code']})."
        await self.item(item_id, status="completed" if ok else "cancelled" if cancelled else "failed",
                        summary=summary, outputs=outputs, **({"progress": progress} if progress else {}))
        await self.progress(self.items[item_id]["label"] + ": " + summary, item_id)

    async def plan(self, steps):
        """High-level task milestones explicitly authored for the public plan."""
        active = None
        present = set()
        for index, step in enumerate(steps):
            if not isinstance(step, dict) or not isinstance(step.get("content"), str):
                continue
            title = _text(step["content"], 160)
            item_id = f"{self.id}:task:" + hashlib.sha256(title.encode()).hexdigest()[:16]
            if item_id in present:
                item_id += f":{index}"
            present.add(item_id)
            status = {"pending": "queued", "in_progress": "running", "completed": "completed"}.get(step.get("status"), "queued")
            await self.item(item_id, parent_id=f"{self.id}:phase:work", kind="phase", phase="work",
                            label=title, purpose="Task milestone", status=status)
            if status == "running" and active is None:
                active = item_id
        for item_id, item in list(self.items.items()):
            if item_id.startswith(f"{self.id}:task:") and item_id not in present and item["status"] in {"queued", "running"}:
                await self.item(item_id, status="cancelled", summary="Replaced when the task plan changed.")
        self.plan_parent = active

    async def finish(self, status):
        for item_id, item in list(self.items.items()):
            if item["status"] in {"queued", "running", "paused"}:
                terminal = "completed" if status == "complete" and item_id.startswith(f"{self.id}:phase:") else "cancelled" if status in {"stopped", "complete"} else "failed"
                await self.item(item_id, status=terminal)
        self.work["finished_at"] = time.time()
        self.work["duration_ms"] = max(0, int((self.work["finished_at"] - self.work["started_at"]) * 1000))
