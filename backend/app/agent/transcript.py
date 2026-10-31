"""The visible work transcript: narration and actions, Claude Code style.

While the agent works, the user sees its short narration between steps and
one row per action ("Ran Python", "Read uploads/data.csv") that expands to the
code or command and its output. Hidden model reasoning is never included, nor
are verifier steps or the harness's own bookkeeping tools. Entries stream live
as ``transcript`` events and are saved with the answer.
"""

from __future__ import annotations

import json
from typing import Any, Optional

INPUT_LIMIT = 20_000
OUTPUT_LIMIT = 8_000
ENTRY_LIMIT = 300

# tool name -> (title, argument shown as the row subject, argument shown as input, input language)
ACTIONS: dict[str, tuple[str, Optional[str], Optional[str], Optional[str]]] = {
    "python": ("Ran Python", None, "code", "python"),
    "bash": ("Ran command", None, "command", "bash"),
    "read_file": ("Read", "path", None, None),
    "write_file": ("Wrote", "path", "content", None),
    "edit_file": ("Edited", "path", None, None),
    "list_files": ("Listed files", "path", None, None),
    "grep": ("Searched files for", "pattern", None, None),
    "parse_document": ("Read document", "path", None, None),
    "web_search": ("Searched the web for", "query", None, None),
    "web_fetch": ("Fetched", "url", None, None),
    "check_equivalence": ("Checked equivalence", None, None, None),
    "wolfram_alpha": ("Asked Wolfram|Alpha", "query", None, None),
    "lean_check": ("Checked Lean proof", "name", "source", "lean"),
    "search_skills": ("Searched skills for", "query", None, None),
    "load_skill": ("Loaded skill", "name", None, None),
    "render_ui": ("Built interactive view", "title", None, None),
    "create_ui_app": ("Built interactive app", "title", None, None),
    "register_ui_action": ("Registered interactive action", "action_id", None, None),
    "data_agent": ("Ran the data agent", None, "task", None),
    "find_files": ("Found files matching", "query", None, None),
    "bash_poll": ("Checked background job", "session_id", "input", None),
    "spawn_agent": ("Started sub-agent", "task_name", "message", None),
    "wait_agent": ("Waited for sub-agents", None, None, None),
    "send_message": ("Messaged sub-agent", "target", "message", None),
    "followup_task": ("Gave sub-agent a follow-up", "target", "message", None),
    "interrupt_agent": ("Stopped sub-agent", "target", None, None),
    "list_agents": ("Listed sub-agents", None, None, None),
    "memory_search": ("Searched memories for", "query", None, None),
    "memory_read": ("Read memory", "path", None, None),
    "memory_note": ("Saved a memory", None, "note", None),
    "list_mcp_resources": ("Listed resources", "server", None, None),
    "read_mcp_resource": ("Read resource", "uri", None, None),
}
# Bookkeeping the user sees elsewhere (plan checklist, published results).
HIDDEN = {"update_plan", "publish_result", "search_tools", "load_tool_schemas"}
# Delegated agents return their internal exchanges; show the task, not that output.
OUTPUT_PRIVATE = {"data_agent"}


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n… [{len(text) - limit} more characters]"


def _arguments(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


class Transcript:
    def __init__(self) -> None:
        self.entries: dict[str, dict] = {}
        self._actions_in_step: dict[int, int] = {}

    def _store(self, entry: dict) -> Optional[dict]:
        if entry["id"] not in self.entries and len(self.entries) >= ENTRY_LIMIT:
            return None
        self.entries[entry["id"]] = entry
        return entry

    def note(self, step: int, text: str) -> Optional[dict]:
        text = text.strip()
        if not text:
            return None
        return self._store({"id": f"note:{step}", "kind": "note", "order": [step, 0], "text": _clip(text, 4_000)})

    def action(self, step: int, call_id: str, name: str, raw_arguments: Any) -> Optional[dict]:
        if name in HIDDEN:
            return None
        args = _arguments(raw_arguments)
        if name == "report_progress":
            return self._store({"id": f"progress:{call_id}", "kind": "note", "order": [step, self._next(step)],
                                "text": _clip(str(args.get("text", "")), 400)}) if args.get("text") else None
        title, subject_key, input_key, language = ACTIONS.get(name, (f"Used {name}", None, None, None))
        entry = {"id": f"action:{call_id}", "kind": "action", "order": [step, self._next(step)], "tool": name,
                 "title": title, "status": "running"}
        if subject_key and isinstance(args.get(subject_key), str):
            entry["subject"] = _clip(args[subject_key].strip(), 200)
        if input_key and isinstance(args.get(input_key), str) and args[input_key].strip():
            entry["input"] = _clip(args[input_key], INPUT_LIMIT)
            entry["language"] = language
        elif not input_key and args:
            entry["input"] = _clip(json.dumps(args, ensure_ascii=False, indent=2), INPUT_LIMIT)
            entry["language"] = "json"
        return self._store(entry)

    def result(self, call_id: str, ok: Optional[bool], output: Any, error: Any) -> Optional[dict]:
        entry = self.entries.get(f"action:{call_id}")
        if entry is None:
            return None
        entry["status"] = "done" if ok else "error"
        if entry.get("tool") in OUTPUT_PRIVATE:
            return entry
        text = str(output or "") if ok else str(error or output or "The action failed.")
        if text.strip():
            entry["output"] = _clip(text.strip(), OUTPUT_LIMIT)
        return entry

    def stop_running(self) -> None:
        for entry in self.entries.values():
            if entry.get("status") == "running":
                entry["status"] = "stopped"

    def _next(self, step: int) -> int:
        self._actions_in_step[step] = self._actions_in_step.get(step, 0) + 1
        return self._actions_in_step[step]

    def snapshot(self) -> list[dict]:
        return sorted((dict(entry) for entry in self.entries.values()), key=lambda e: e["order"])


def public_transcript(value: Any) -> list[dict]:
    """Saved entries re-validated for presentation (older or edited records)."""
    out = []
    for entry in value if isinstance(value, list) else []:
        if not isinstance(entry, dict) or entry.get("kind") not in {"note", "action"}:
            continue
        order = entry.get("order")
        if not (isinstance(order, list) and len(order) == 2 and all(type(n) is int for n in order)):
            continue
        clean = {"id": str(entry.get("id", "")), "kind": entry["kind"], "order": order}
        for key, limit in (("text", 4_000), ("tool", 64), ("title", 120), ("subject", 200), ("language", 16),
                           ("input", INPUT_LIMIT + 64), ("output", OUTPUT_LIMIT + 64)):
            if isinstance(entry.get(key), str):
                clean[key] = entry[key][:limit]
        if entry.get("status") in {"running", "done", "error", "stopped"}:
            clean["status"] = entry["status"]
        out.append(clean)
    return out[:ENTRY_LIMIT]
