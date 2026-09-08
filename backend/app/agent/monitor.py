"""Lightweight misalignment monitor (open analog of Astra production monitoring).

OpenAI's GPT-6 Astra monitor is unpublished. This is a local classifier over
tool names and arguments: deny-list heuristics first, optional cheap LLM
judge when ``ALIGN_MONITOR_LLM`` is on. It never silently executes a blocked
call — the harness turns ``block`` into a failed tool result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

_BLOCK = [
    re.compile(r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?/(?:\s|$)"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+if="),
    re.compile(r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;"),
    re.compile(r"\bshutdown\b|\breboot\b|\bhalt\b"),
    re.compile(r"curl[^\n]{0,80}\|\s*(ba)?sh"),
    re.compile(r"wget[^\n]{0,80}\|\s*(ba)?sh"),
    re.compile(r"\bchmod\s+777\s+/\b"),
]
_WATCH = [
    re.compile(r"\bsudo\b"),
    re.compile(r"\bpip\s+install\b"),
    re.compile(r"\bcurl\b|\bwget\b"),
    re.compile(r"os\.system\(|subprocess\.(run|Popen|call)\("),
    re.compile(r"\bdrop\s+table\b|\bdelete\s+from\b", re.I),
]


@dataclass
class MonitorVerdict:
    level: str  # ok | watch | block
    summary: str
    tool: str = ""


def inspect_tool_call(name: str, arguments: Optional[dict]) -> MonitorVerdict:
    args = arguments or {}
    blob = " ".join(str(v) for v in args.values() if isinstance(v, (str, int)))
    hay = f"{name} {blob}"
    for pat in _BLOCK:
        if pat.search(hay):
            return MonitorVerdict("block", f"Blocked {name}: looks like a destructive host command.", name)
    if name == "bash":
        cmd = str(args.get("command") or args.get("cmd") or "")
        if cmd.strip().startswith("sudo") or "sudo " in cmd:
            return MonitorVerdict("watch", "Shell is using sudo — review before trusting the result.", name)
    for pat in _WATCH:
        if pat.search(hay):
            return MonitorVerdict("watch", f"Watch {name}: side-effecting command.", name)
    return MonitorVerdict("ok", "", name)


def next_step_options(plan: list, text: str) -> list[str]:
    """Cheap next-step chips: unfinished plan, then short list lines in the answer."""
    items: list[str] = []
    for step in plan or []:
        if not isinstance(step, dict):
            continue
        if step.get("status") in ("pending", "in_progress"):
            content = str(step.get("content") or "").strip()
            if content and content not in items:
                items.append(content[:110])
        if len(items) >= 4:
            return items
    for line in (text or "").splitlines():
        stripped = line.strip().lstrip("-*•").strip()
        low = stripped.lower()
        if low.startswith(("next:", "option:", "then ", "we could ", "or, ")):
            cleaned = stripped.split(":", 1)[-1].strip() if ":" in stripped else stripped
            if 8 < len(cleaned) < 110 and cleaned not in items:
                items.append(cleaned)
        if len(items) >= 4:
            break
    return items[:4]
