"""Public chat projection. Internal execution records never cross this boundary."""
from __future__ import annotations

from copy import deepcopy
import re
import math
from app.agent.activity import public_activity, public_progress
from app.agent.results import result_parts
from app.agent.transcript import public_transcript

WORK_LABELS = frozenset({
    "Working", "Reading uploaded file", "Reading files", "Searching sources", "Computing",
    "Creating interactive view", "Checking answer", "Correcting answer", "Preparing final answer",
})
VERIFICATION_STATUSES = frozenset({
    "not_requested", "skipped", "correct", "incorrect", "uncertain", "limit_reached", "error",
})
USAGE_FIELDS = frozenset({
    "prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens", "cached_tokens",
    "cache_read_tokens", "cache_write_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
    "input_tokens", "output_tokens",
})


def public_counts(value, fields=USAGE_FIELDS) -> dict:
    """Do not let provider-specific diagnostics enter public metadata."""
    if not isinstance(value, dict):
        return {}
    return {key: count for key, count in value.items() if key in fields
            and type(count) in (int, float) and math.isfinite(count) and count >= 0}


def _count(value, default=0):
    return int(value) if type(value) in (int, float) and math.isfinite(value) and value >= 0 else default


def tool_label(name: str) -> str:
    if name == "parse_document":
        return "Reading uploaded file"
    if name in {"read_file", "list_files", "grep", "search_skills", "load_skill"}:
        return "Reading files"
    if name in {"web_search", "web_fetch"}:
        return "Searching sources"
    if name in {"render_ui", "create_ui_app", "register_ui_action"}:
        return "Creating interactive view"
    if name in {"python", "check_equivalence", "wolfram_alpha", "data_agent", "bash"}:
        return "Computing"
    return "Working"


def public_work(work: dict | None, *, status: str = "complete", tool_count: int = 0) -> dict:
    work = work if isinstance(work, dict) else {}
    status = work.get("status", status)
    verification = work.get("verification") if isinstance(work.get("verification"), dict) else {}
    verdict = verification.get("status", "not_requested")
    out = {
        "status": status if status in {"running", "complete", "stopped", "error"} else "error",
        "tool_count": _count(work.get("tool_count", tool_count)),
        "verification": {
            "status": verdict if verdict in VERIFICATION_STATUSES else "uncertain",
            "rounds": _count(verification.get("rounds")),
        },
        "steps": [{"label": step["label"], "status": step["status"]}
                  for step in (work.get("steps") if isinstance(work.get("steps"), list) else [])
                  if isinstance(step, dict) and step.get("label") in WORK_LABELS
                  and step.get("status") in {"running", "complete", "error"}],
    }
    if isinstance(work.get("items"), list):
        out["items"] = [p for item in work["items"] if (p := public_activity(item)) is not None]
    progress = public_progress(work.get("current_progress"))
    if progress:
        out["current_progress"] = progress
    if isinstance(work.get("milestones"), list):
        out["milestones"] = [update for item in work["milestones"][-12:]
                             if (update := public_progress(item)) is not None]
    for key in ("started_at", "finished_at", "duration_ms"):
        value = work.get(key)
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            out[key] = value
    return out


def _terminal_texts(parts: list[dict]) -> list[dict]:
    """Select completed answer attempts, excluding text that introduced tools."""
    selected = []
    for index, part in enumerate(parts):
        if part.get("type") != "text" or part.get("phase", "answer") not in {"answer", "revise"} or not part.get("text", "").strip():
            continue
        following = next((p for p in parts[index + 1:] if p.get("type") in {"text", "tool_call", "phase"}), None)
        if following is not None and following.get("type") == "tool_call":
            continue
        selected.append(part)
    return selected


def _referenced(artifact: dict, answer: str) -> bool:
    """A tool-produced file is a deliverable only when the answer points to it.

    Figures the answer embeds as Markdown images already render inline, so they
    are not repeated; files it never mentions are intermediates and stay in the
    workspace (Files) instead of being appended to the answer.
    """
    path = (artifact.get("path") or "").removeprefix("./")
    if not path:
        return False
    names = {path, path.rsplit("/", 1)[-1]}
    if not any(name and name in answer for name in names):
        return False
    if artifact.get("kind") == "image":
        embedded = r"!\[[^\]]*\]\(\s*(?:\./)?(?:" + "|".join(re.escape(n) for n in names) + r")\s*\)"
        return re.search(embedded, answer) is None
    return True


def _output_parts(parts: list[dict], answer: str) -> list[dict]:
    out = []
    seen = set()
    artifacts_by_key = {}
    for part in parts:
        if part.get("phase") == "verify":
            continue
        kind = part.get("type")
        if kind == "ui":
            surface = part.get("surface") or {}
            if surface.get("status", "committed") == "committed":
                key = ("ui", surface.get("id"))
                existing = next((p for p in out if p.get("type") == "ui" and p["surface"].get("id") == surface.get("id")), None)
                if existing:
                    existing["surface"] = deepcopy(surface)
                else:
                    out.append({"type": "ui", "surface": deepcopy(surface)})
                seen.add(key)
        artifacts = []
        if kind == "artifact":
            artifacts = [part.get("artifact") or {}]
        elif kind == "tool_call" and part.get("name") not in {"parse_document", "read_file", "list_files", "grep"}:
            artifacts = [a for a in (part.get("result") or {}).get("artifacts") or [] if _referenced(a, answer)]
        for artifact in artifacts:
            if not artifact.get("kind"):
                continue
            key = ("artifact", artifact.get("path") or artifact.get("url") or artifact.get("digest") or artifact.get("name"))
            if key in artifacts_by_key:
                artifacts_by_key[key]["artifact"] = deepcopy(artifact)
            else:
                output = {"type": "artifact", "artifact": deepcopy(artifact)}
                out.append(output)
                artifacts_by_key[key] = output
                seen.add(key)
    return out


def _legacy_answer_text(text: str) -> str:
    """Remove only a recognizable leading handoff sentence, preserving the answer."""
    pattern = (r"\A\s*(?:Confirmed\s*[,—–:-]?\s*)?(?:the\s+)?(?:independent\s+)?verifier\s+"
               r"(?:is\s+correct|was\s+right|confirms?\s+(?:the\s+)?(?:answer|solution))"
               r"(?:\s+(?:about|regarding)\s+([^.!?\n]{1,120}))?\s*[.!:]\s*")
    return re.sub(pattern, lambda match: ("Correction regarding " + match[1] + ".\n\n") if match[1] else "",
                  text, count=1, flags=re.IGNORECASE)


def public_content(content: dict | None) -> dict:
    """One final answer plus safe activity and deliverables; never mutate storage."""
    content = content if isinstance(content, dict) else {}
    parts = [p for p in content.get("parts", []) if isinstance(p, dict)] if isinstance(content.get("parts"), list) else []
    out = {k: content[k] for k in ("model", "provider") if isinstance(content.get(k), str)}
    if content.get("reasoning") in {"off", "low", "high", "max"}:
        out["reasoning"] = content["reasoning"]
    if content.get("status") in {"running", "complete", "stopped", "error"}:
        out["status"] = content["status"]
    for key, fields in (("usage", USAGE_FIELDS), ("stats", {"llm_calls", "tool_calls"})):
        if key in content:
            out[key] = public_counts(content[key], fields)
    if type(content.get("recovered")) is bool:
        out["recovered"] = content["recovered"]
    if type(content.get("turn")) is int:
        out["turn"] = content["turn"]
    mode = content.get("mode")
    if isinstance(mode, dict) and mode.get("mode") in {"mat", "opt"} and mode.get("source") in {"user", "auto"}:
        out["mode"] = {"mode": mode["mode"], "source": mode["source"], "reason": str(mode.get("reason") or "")[:300]}
        if mode.get("switched") is True and mode.get("from") in {"mat", "opt"}:
            out["mode"].update(switched=True, **{"from": mode["from"]})
    changes = content.get("changes")
    if isinstance(changes, dict) and type(changes.get("turn")) is int and isinstance(changes.get("files"), list):
        out["changes"] = {"turn": changes["turn"], "files": [
            {k: c[k] for k in ("path", "status", "added", "removed", "binary", "untracked") if k in c}
            for c in changes["files"][:500]
            if isinstance(c, dict) and isinstance(c.get("path"), str) and c.get("status") in {"A", "M", "D"}]}
    out["presentation"] = "final"
    current = "final_text" in content
    already_public = content.get("presentation") == "final" and not current
    if current:
        text = content["final_text"] or ""
        if content.get("notice"):
            out["notice"] = content["notice"]
    elif already_public:
        text = "\n\n".join(p.get("text", "") for p in parts if p.get("type") == "text")
    else:
        terminal = _terminal_texts(parts)
        text = _legacy_answer_text(terminal[-1]["text"]) if terminal else ""
        revised = [p for p in terminal if p.get("phase") == "revise"]
        earlier = [p for p in terminal if p.get("phase", "answer") == "answer"]
        if revised and earlier and earlier[-1]["text"] != text:
            out["earlier_drafts"] = [_legacy_answer_text(earlier[-1]["text"])]
            out["notice"] = "This older reply saved a correction separately. The latest reply may be partial; its earlier draft is retained below."
    if already_public:
        for key in ("notice", "earlier_drafts"):
            if key in content:
                out[key] = deepcopy(content[key])
    out["parts"] = ([{"type": "text", "text": text, "phase": "answer"}] if text.strip() else [])
    # Only explicitly published answer blocks survive an interrupted turn.
    # Strict mode keeps every intermediate result private, including reloads.
    if not content.get("strict_final", False):
        out["parts"].extend(result_parts(parts))
    # Arbitrary tool-produced UI remains gated on a finalized candidate.
    if not current or content.get("final_ready", False):
        if current:
            output_parts = [p for p in parts if p.get("publication", 0) == content.get("output_generation", 0)] if content.get("publish_outputs", True) else []
        elif not already_public and any(p.get("phase") == "revise" for p in parts):
            output_parts = [p for p in parts if p.get("phase") == "revise"]
        else:
            output_parts = parts
        out["parts"].extend(_output_parts(output_parts, text))
        if content.get("status", "complete") == "complete":
            out["parts"].extend(deepcopy(p) for p in parts if p.get("type") == "options")
    if not content.get("strict_final", False) and content.get("transcript"):
        out["transcript"] = public_transcript(content["transcript"])
    work = content.get("work")
    if not work:
        checks = [p for p in parts if p.get("type") == "verification"]
        steps = []
        for part in parts:
            if part.get("type") == "tool_call":
                label = tool_label(part.get("name", ""))
                if not any(s["label"] == label for s in steps):
                    steps.append({"label": label, "status": "complete"})
        if checks:
            steps.append({"label": "Checking answer", "status": "complete"})
        work = {"steps": steps, "verification": {"status": checks[-1].get("verdict", "uncertain") if checks else "not_requested", "rounds": len(checks)}}
    out["work"] = public_work(work, status=content.get("status", "complete"),
                              tool_count=out.get("stats", {}).get("tool_calls", sum(p.get("type") == "tool_call" for p in parts)))
    return out


def public_messages(messages: list[dict]) -> list[dict]:
    return [{**message, "content": public_content(message.get("content"))} if message.get("role") == "assistant" else deepcopy(message)
            for message in messages]
