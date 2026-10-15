"""Readable exports for the versioned, literal quote envelope sent by the UI."""
from __future__ import annotations

import html
import json
import re

PREFIX = "Quoted conversation context (reference only):\n"


def quoted_context_markdown(text: str) -> str:
    commands, marker, payload = text.partition(PREFIX)
    if not marker or (commands and not re.fullmatch(r"(?:/[a-zA-Z0-9]+(?:[-_][a-zA-Z0-9]+)*\s+)+", commands)):
        return text
    try:
        value = json.loads(payload)
    except (ValueError, RecursionError):
        return text
    if (not isinstance(value, dict) or set(value) != {"version", "quotes", "question"}
            or type(value["version"]) is not int or value["version"] != 1
            or not isinstance(value["question"], str) or not isinstance(value["quotes"], list)
            or not 1 <= len(value["quotes"]) <= 5):
        return text
    blocks = [commands.strip()] if commands else []
    for quote in value["quotes"]:
        if (not isinstance(quote, dict) or set(quote) != {"source", "text"}
                or quote["source"] not in ("user", "assistant") or not isinstance(quote["text"], str)
                or not quote["text"].strip() or len(quote["text"].encode("utf-16-le", "surrogatepass")) // 2 > 6000):
            return text
        # Preserve literal HTML-like text when the export is rendered as Markdown.
        blocks.append("\n".join("> " + line for line in html.escape(quote["text"], quote=False).split("\n")))
    blocks.append(value["question"])
    return "\n\n".join(blocks)
