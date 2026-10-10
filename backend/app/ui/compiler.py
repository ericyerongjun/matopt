"""Incrementally compile complete native nodes; never repair/trust partial JSON."""
from __future__ import annotations

import json

from pydantic import ValidationError

from app.agent.events import ev
from .contracts import UINode, UISurface

_DECODER = json.JSONDecoder()


def complete_fields(raw: str) -> dict:
    """Read only complete top-level values and complete items in a nodes array.

    An unfinished string/object is never closed synthetically. This makes every
    preview a real validated prefix of the eventual declarative surface.
    """
    out = {}
    i = 0
    def ws(pos):
        while pos < len(raw) and raw[pos].isspace():
            pos += 1
        return pos
    i = ws(i)
    if i >= len(raw) or raw[i] != "{":
        return out
    i += 1
    try:
        while True:
            i = ws(i)
            if i >= len(raw) or raw[i] == "}":
                return out
            key, end = _DECODER.raw_decode(raw, i)
            if not isinstance(key, str):
                return out
            i = ws(end)
            if i >= len(raw) or raw[i] != ":":
                return out
            i = ws(i + 1)
            if key == "nodes" and i < len(raw) and raw[i] == "[":
                nodes = []
                out[key] = nodes
                i += 1
                while True:
                    i = ws(i)
                    if i >= len(raw):
                        return out
                    if raw[i] == "]":
                        i += 1
                        break
                    value, i = _DECODER.raw_decode(raw, i)
                    nodes.append(value)
                    i = ws(i)
                    if i >= len(raw):
                        return out
                    if raw[i] == "]":
                        i += 1
                        break
                    if raw[i] != ",":
                        return out
                    i += 1
            else:
                value, i = _DECODER.raw_decode(raw, i)
                out[key] = value
            i = ws(i)
            if i >= len(raw) or raw[i] == "}":
                return out
            if raw[i] != ",":
                return out
            i += 1
    except (json.JSONDecodeError, ValueError):
        return out


class UIStreamCompiler:
    def __init__(self, store, conversation_id: str):
        self.store = store
        self.cid = conversation_id
        self._last: dict[str, str] = {}
        self._revision: dict[str, int] = {}

    def feed(self, name: str, call_id: str, raw: str):
        if name != "render_ui" or len(raw.encode()) > 1048576:
            return None
        fields = complete_fields(raw)
        if not all(isinstance(fields.get(k), str) and fields[k] for k in ("id", "title", "fallback")):
            return None
        nodes = []
        for item in fields.get("nodes", []):
            try:
                nodes.append(UINode.model_validate(item).model_dump(mode="json"))
            except ValidationError:
                # A complete invalid node waits for the model's repair tool call.
                continue
        if not nodes:
            return None
        sid = fields["id"]
        if call_id not in self._revision:
            prior = self.store.get(self.cid, sid)
            self._revision[call_id] = prior["revision"] + 1 if prior else 1
        try:
            surface = UISurface(id=sid, revision=self._revision[call_id], title=fields["title"],
                                fallback=fields["fallback"], nodes=nodes, status="preview",
                                state=fields.get("state", {}), actions=[]).model_dump(mode="json", exclude_none=True)
        except (ValueError, TypeError):
            return None
        signature = json.dumps(surface, sort_keys=True)
        if self._last.get(call_id) == signature:
            return None
        kind = "ui_patch" if call_id in self._last else "ui_start"
        self._last[call_id] = signature
        return ev(kind, surface=surface)
