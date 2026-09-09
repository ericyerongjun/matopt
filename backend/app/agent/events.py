"""
Typed streaming events emitted by the agent harness.

Every event is serialised to a Server-Sent Events frame
(``data: {json}\n\n``) with a ``type`` field. The frontend renders a
message as an ordered list of *parts* (text, reasoning, tool calls,
artifacts) that it rebuilds from these events.

Event types
-----------
conversation      {conversation_id, title}                 new/known conversation
message_start     {message_id, model, provider}            assistant turn started
phase             {phase: "answer"|"verify"|"revise", round} harness phase change
text_delta        {delta}                                  visible assistant text
reasoning_delta   {delta}                                  model thinking (when exposed)
tool_call_start   {id, name}                               model began a tool call
tool_call_delta   {id, delta}                              streamed JSON arguments
tool_call_ready   {id, name, arguments}                    arguments parsed, executing
tool_progress     {id, delta}                              live stdout from a running tool
tool_result       {id, name, ok, output, artifacts, meta}  tool finished
plan              {steps: [{content, status}]}             task plan updated
status            {message}                                human readable status line
usage             {prompt_tokens, completion_tokens, ...}  token accounting for one LLM call
message_end       {message_id, parts}                      final persisted assistant message
done              {conversation_id, message_id}            run finished successfully
stopped           {reason}                                 run cancelled by the user
error             {message}                                run failed
keepalive         {ts}                                     heartbeat while waiting
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class Artifact:
    """A file or rich object produced by a tool, addressable by the UI."""

    kind: str                       # image | plotly | html | file | markdown | json
    name: str                       # display name (usually the file name)
    path: Optional[str] = None      # workspace-relative path when saved to disk
    url: Optional[str] = None       # /api/files/{conversation_id}/{path}
    mime: Optional[str] = None
    size: Optional[int] = None
    data: Any = None                # inline payload (plotly figure JSON, html snippet)
    digest: Optional[str] = None    # sha256 of image/plotly bytes; used to skip recaptures

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}


@dataclass
class ToolResult:
    """What a tool hands back to the harness."""

    ok: bool
    output: str = ""                        # text shown to the model (already truncated)
    artifacts: list[Artifact] = field(default_factory=list)
    error: Optional[str] = None
    meta: dict = field(default_factory=dict)  # exit_code, duration_ms, todos, ...

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "output": self.output,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "error": self.error,
            "meta": self.meta,
        }


@dataclass
class Event:
    type: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"type": self.type, **self.data}

    def to_sse(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, default=str)
        return f"data: {payload}\n\n"


# ── convenience constructors ────────────────────────────────────────────

def ev(type_: str, **data: Any) -> Event:
    return Event(type_, data)


def keepalive() -> Event:
    return Event("keepalive", {"ts": time.time()})


def error(message: str, **extra: Any) -> Event:
    return Event("error", {"message": message, **extra})
