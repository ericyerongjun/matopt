"""Summarising compaction for long conversations.

When the live provider history passes its character budget, the oldest whole
turns are replaced by one ``<conversation-summary>`` message instead of being
silently dropped. Compaction shrinks the history well below the budget so it
fires rarely: every compaction rewrites the prompt prefix and so invalidates
the provider's prompt cache once. An earlier summary sits at the head of the
compacted span and is folded into the next one.

The harness owns the model call; this module plans cut points, renders the
span for the summariser and supplies a deterministic digest when the model is
unavailable or the run budget has no room.
"""

from __future__ import annotations

import json
from typing import Optional

SUMMARY_TAG = "conversation-summary"
SPAN_CHAR_LIMIT = 200_000
MESSAGE_CHAR_LIMIT = 6_000

COMPACTION_PROMPT = """You compress the earlier part of a working session between a user and MatOpt, \
an agent for mathematics, statistics, machine learning and computer science, so the agent can continue without it.

Write a faithful, dense summary in Markdown with exactly these sections:
## User goals
What the user asked for, in their words where precision matters, including constraints and preferences.
## Results established
Every computed number, formula, estimate, conclusion and verification outcome the later work may rely on. \
Keep exact values, units, seeds and LaTeX. Mark anything that was withdrawn, contradicted or left unverified.
## Files and state
Workspace files created or changed and what they contain; Python variables or models the later work relies on.
## Decisions and open items
Methods chosen or rejected and why, corrections the user gave, and questions still open.

Use only the transcript. Do not add facts, calculations or advice. Omit pleasantries, tool mechanics, \
failed attempts that led nowhere and private reasoning. If an earlier summary is included, merge it rather than nesting it."""


def _size(message: dict) -> int:
    return len(json.dumps(message, ensure_ascii=False))


def is_summary(message: dict) -> bool:
    content = message.get("content")
    return message.get("role") == "user" and isinstance(content, str) and content.startswith(f"<{SUMMARY_TAG}>")


def turn_starts(history: list[dict]) -> list[int]:
    """Indices where a run of user messages begins after the system head.

    A turn's ``<turn-context>`` and the user's text form one run, so cutting at
    a run start never separates them, and never splits a tool call from its
    result. The leading summary, if any, is not a cut point.
    """
    starts = []
    for i in range(1, len(history)):
        previous = history[i - 1]
        if (history[i].get("role") == "user" and not is_summary(history[i])
                and (previous.get("role") != "user" or is_summary(previous))):
            starts.append(i)
    return starts


def plan_cut(history: list[dict], target: int) -> Optional[int]:
    """Smallest turn start whose tail, plus the system head and a summary, fits ``target``.

    The latest turn is always kept. Returns None when no older turn exists.
    """
    starts = [i for i in turn_starts(history) if i > 1]
    if not starts:
        return None
    latest = starts[-1]
    sizes = [_size(message) for message in history]
    head = sizes[0] + 4_000  # room for the summary that replaces the span
    for cut in starts:
        if cut > latest:
            break
        if head + sum(sizes[cut:]) <= target:
            return cut
    return latest


def render_span(span: list[dict]) -> str:
    """Transcript text for the summariser, bounded and with long outputs trimmed."""
    lines = []
    for message in span:
        role = message.get("role", "")
        content = message.get("content")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        if is_summary(message):
            lines.append("[Earlier summary]\n" + content)
            continue
        if role == "tool":
            label = f"[Tool result {message.get('name') or message.get('tool_call_id', '')}]"
        elif role == "assistant" and message.get("tool_calls"):
            calls = ", ".join(f"{c['function']['name']}({c['function'].get('arguments', '')[:1500]})"
                              for c in message["tool_calls"])
            label = f"[Assistant called {calls}]"
        else:
            label = f"[{role}]"
        if len(content) > MESSAGE_CHAR_LIMIT:
            half = MESSAGE_CHAR_LIMIT // 2
            content = content[:half] + f"\n… [{len(content) - MESSAGE_CHAR_LIMIT} characters omitted] …\n" + content[-half:]
        lines.append(f"{label}\n{content}" if content else label)
    text = "\n\n".join(lines)
    if len(text) > SPAN_CHAR_LIMIT:
        text = "… [oldest part omitted] …\n" + text[-SPAN_CHAR_LIMIT:]
    return text


def digest(span: list[dict]) -> str:
    """Deterministic fallback: earlier summary, user requests and assistant answers."""
    parts = []
    for message in span:
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        if is_summary(message):
            parts.append(_unwrap(content))
        elif message.get("role") == "user" and not content.startswith("<turn-context>"):
            parts.append("User: " + content[:1_500])
        elif message.get("role") == "assistant" and not message.get("tool_calls"):
            parts.append("Assistant: " + content[:3_000])
    return "\n\n".join(parts) or "Earlier turns contained no user-visible text."


def _unwrap(content: str) -> str:
    """The summary text without its tags or harness preamble."""
    body = content.split(">", 1)[1].rsplit(f"</{SUMMARY_TAG}>", 1)[0].strip()
    return body.split("\n\n", 1)[1].strip() if body.startswith("Harness-provided") and "\n\n" in body else body


def summary_message(text: str, *, turns: int, model_written: bool) -> dict:
    source = "a model-written summary" if model_written else "an automatic digest (the summariser was unavailable)"
    return {"role": "user", "content": (
        f"<{SUMMARY_TAG}>\nHarness-provided context, not written by the user: {source} of {turns} earlier "
        f"turn(s) removed to keep the conversation within its context budget. Full records remain in the "
        f"workspace and session log; re-read files rather than relying on remembered output.\n\n"
        f"{text.strip()}\n</{SUMMARY_TAG}>"
    )}
