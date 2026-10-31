"""Phase 1: extract a memory record from one idle conversation.

Follows Codex ``memories/write/src/phase1.rs`` and ``rollout_input.rs``:

* **Eligibility** (Codex's startup claim rules): a conversation is read only
  when it has been idle for ``memory_min_idle_hours`` (so a live chat is never
  summarised half-way), it changed since its last extraction, the user has not
  opted it out, it is newer than the last "clear all", and — with
  ``memory_disable_on_web`` — it never used web or MCP tools, whose output is
  third-party text that could plant "memories" (Codex skips rollouts whose
  memory mode is ``polluted`` by external context for the same reason).
* **Input filtering**: only user text, assistant answers and one-line tool
  summaries go in (``serialize_filtered_rollout_response_items``); tool output
  bodies stay out. The rendering is capped at ``MAX_TRANSCRIPT_CHARS`` keeping
  the head and the tail.
* **Redaction** before the model call and again on the output fields (Codex
  ``serializes_memory_rollout_redacts_secrets_before_prompt_upload`` and the
  redaction of generated fields).
* **No-op output** is a success: an all-empty answer stores an empty record so
  the conversation is not re-read until it changes.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.agent.exec_output import redact_secrets
from app.agent.memory import prompts
from app.agent.memory.store import MemoryStore, slugify

logger = logging.getLogger(__name__)

MAX_TRANSCRIPT_CHARS = 60_000
MAX_MESSAGE_CHARS = 6_000
MAX_ARGS_CHARS = 160
MAX_RAW_MEMORY_CHARS = 12_000
MAX_SUMMARY_CHARS = 600

WEB_TOOLS = frozenset({"web_search", "web_fetch"})


def is_external_tool(name: str) -> bool:
    """Web tools and namespaced MCP tools, recognised by name alone."""
    return name in WEB_TOOLS or name.startswith("mcp__")


def is_external_call(record: dict) -> bool:
    """Web and MCP calls bring third-party text into the conversation.

    MCP tools are usually advertised under their bare remote name (one server)
    or ``<server>_<tool>``; only collisions get the ``mcp__`` prefix. The
    harness therefore records the server on the call itself (``mcp_server``),
    which is the provenance this checks first.
    """
    server = record.get("mcp_server")
    return bool(isinstance(server, str) and server) or is_external_tool(str(record.get("name") or ""))


@dataclass
class Transcript:
    text: str
    tools: set[str] = field(default_factory=set)
    user_turns: int = 0
    external: bool = False      # a web or MCP call brought in third-party text


def _clip(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    return f"{text[:head]}\n[... {len(text) - limit} characters omitted ...]\n{text[-(limit - head):]}"


def _assistant_text(content: dict) -> str:
    final = content.get("final_text")
    if isinstance(final, str) and final.strip():
        return final
    parts = content.get("parts") or []
    return "\n\n".join(p.get("text", "") for p in parts
                       if isinstance(p, dict) and p.get("type") == "text"
                       and p.get("phase", "answer") in ("answer", "revise")).strip()


def _tool_line(part: dict) -> str:
    args = part.get("arguments")
    if not isinstance(args, str):
        try:
            args = json.dumps(args, ensure_ascii=False)
        except (TypeError, ValueError):
            args = ""
    args = re.sub(r"\s+", " ", args or "").strip()
    if len(args) > MAX_ARGS_CHARS:
        args = args[:MAX_ARGS_CHARS] + "..."
    return f"[tool {part.get('name', '?')}] {args}".rstrip()


def render_conversation(db, cid: str, *, max_chars: int = MAX_TRANSCRIPT_CHARS) -> Transcript:
    """The filtered, redacted transcript and the set of tools the conversation used."""
    tools: set[str] = set()
    external = False
    lines: list[str] = []
    user_turns = 0
    for message in db.list_messages(cid):
        content = message.get("content") or {}
        if not isinstance(content, dict):
            content = {"text": str(content)}
        role = message.get("role")
        if role == "user":
            text = content.get("text") or ""
            if text.strip():
                user_turns += 1
                lines.append(f"USER:\n{_clip(text, MAX_MESSAGE_CHARS)}")
        elif role == "assistant":
            for part in content.get("parts") or []:
                if isinstance(part, dict) and part.get("type") == "tool_call":
                    tools.add(str(part.get("name") or ""))
                    external = external or is_external_call(part)
                    lines.append(_tool_line(part))
            answer = _assistant_text(content)
            if answer:
                lines.append(f"ASSISTANT:\n{_clip(answer, MAX_MESSAGE_CHARS)}")
    # The session log records every call, including ones a saved message lost.
    try:
        for event in db.list_session_events(cid):
            if event.get("type") == "tool/call":
                data = event.get("data") or {}
                name = data.get("name")
                if isinstance(name, str):
                    tools.add(name)
                external = external or is_external_call(data)
    except Exception:  # noqa: BLE001 — a missing log only weakens the web check
        logger.debug("memory: no session events for %s", cid, exc_info=True)
    tools.discard("")
    text = redact_secrets(_clip("\n\n".join(lines), max_chars))
    return Transcript(text=text, tools=tools, user_turns=user_turns,
                      external=external or any(is_external_tool(t) for t in tools))


def ineligible_reason(conv: dict, store: MemoryStore, settings, *, now: float | None = None,
                      disabled: set[str] | None = None, cleared_at: float | None = None) -> Optional[str]:
    """Why ``conv`` must not be extracted now, or ``None``. Cheap checks only."""
    now = time.time() if now is None else now
    cid = conv["id"]
    updated = float(conv.get("updated_at") or 0)
    if cid in (disabled if disabled is not None else store.disabled()):
        return "disabled"
    if cleared_at and updated <= cleared_at:
        return "cleared"
    if now - updated < settings.memory_min_idle_hours * 3600:
        return "active"
    record = store.get_stage1(cid)
    if record is not None and float(record.get("source_updated_at") or 0) >= updated:
        return "unchanged"
    return None


def transcript_reason(transcript: Transcript, settings) -> Optional[str]:
    if transcript.user_turns == 0:
        return "empty"
    if settings.memory_disable_on_web and (transcript.external or any(is_external_tool(t) for t in transcript.tools)):
        return "external_context"
    return None


def parse_stage1(text: str) -> Optional[dict]:
    """``{raw_memory, summary, slug}`` from the model's answer, or ``None`` if unparseable."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        text = text[start:end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("raw_memory", data.get("rollout_summary", ""))
    summary = data.get("summary", "")
    slug = data.get("slug", data.get("rollout_slug", ""))
    if not all(isinstance(v, str) or v is None for v in (raw, summary, slug)):
        return None
    return {
        "raw_memory": redact_secrets((raw or "").strip())[:MAX_RAW_MEMORY_CHARS],
        "summary": redact_secrets((summary or "").strip())[:MAX_SUMMARY_CHARS],
        "slug": slugify(slug or "", fallback="") if (raw or "").strip() else "",
    }


def stage1_messages(conv: dict, transcript: Transcript) -> list[dict]:
    updated = conv.get("updated_at")
    stamp = datetime.fromtimestamp(updated).isoformat(timespec="minutes") if updated else "?"
    user = prompts.STAGE_ONE_INPUT.format(conversation_id=conv["id"], title=conv.get("title") or "",
                                          updated=stamp, transcript=transcript.text)
    return [{"role": "system", "content": prompts.STAGE_ONE_SYSTEM}, {"role": "user", "content": user}]


async def extract(complete, conv: dict, transcript: Transcript) -> Optional[dict]:
    """Run the Phase 1 model call. ``complete(messages) -> text`` is the side-call.

    Returns the record to store, or ``None`` when the call failed or was not
    parseable (the conversation is then retried on a later pass).
    """
    text = await complete(stage1_messages(conv, transcript))
    if text is None:
        return None
    parsed = parse_stage1(text)
    if parsed is None:
        logger.info("memory: unparseable stage-1 output for %s", conv["id"])
        return None
    return {
        "conversation_id": conv["id"],
        "source_updated_at": float(conv.get("updated_at") or 0),
        **parsed,
        "generated_at": time.time(),
        "usage_count": 0,
    }
