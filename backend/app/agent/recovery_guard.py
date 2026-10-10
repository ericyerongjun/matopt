"""Require evidence before repeating a tool whose crash-time outcome is unknown.

This is a checkpoint guard, not a general approval or intent classifier. It
matches the canonical tool and normalized JSON arguments actually journaled by
the harness. Other operations and read-only work remain available. Opaque
commands need an explicit human retry instruction; only the built-in file tools
can use a full read of their exact affected file as an inspection receipt.
"""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any

from app.agent.events import ToolResult
from app.agent.tools.base import Tool


def _arguments(data: dict) -> dict | None:
    value = data.get("normalized_arguments")
    if not isinstance(value, dict):
        value = data.get("arguments")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                return None
    return value if isinstance(value, dict) else None


def _path(value: Any) -> str | None:
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    return posixpath.normpath(value)


def _identity(name: str, arguments: dict) -> tuple[str, str]:
    # These fields are file paths, not command strings. Avoid guessing whether
    # two different programs or API requests have equivalent side effects.
    normalized = dict(arguments)
    for key in ("path", "file_path", "target_path"):
        if key in normalized and (path := _path(normalized[key])) is not None:
            normalized[key] = path
    return name, json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@dataclass
class _UnknownCall:
    name: str
    arguments: dict
    reference: str
    inspected: set[str] = field(default_factory=set)
    authorized: bool = False

    @property
    def file_target(self) -> str | None:
        # A read of an attachment cannot prove the outcome of sending that file
        # to a remote service. Restrict this receipt to known local mutations.
        return _path(self.arguments.get("path")) if self.name in {"write_file", "edit_file"} else None

    def consume_permission(self) -> bool:
        permitted = self.authorized or (self.file_target is not None and self.file_target in self.inspected)
        self.authorized = False
        self.inspected.clear()
        return permitted


class RecoveryGuard:
    def __init__(self) -> None:
        self._unknown: dict[tuple[str, str], _UnknownCall] = {}

    @classmethod
    def from_events(cls, events: list[dict], user_text: str) -> "RecoveryGuard":
        guard = cls()
        calls: dict[tuple, tuple[str, dict]] = {}
        for event in events:
            data = event.get("data", {})
            key = (data.get("turn"), data.get("step"), data.get("callId"))
            if event.get("type") == "tool/call":
                arguments = _arguments(data)
                name = data.get("resolved_name") or data.get("name")
                if not isinstance(name, str) or arguments is None:
                    continue
                calls[key] = (name, arguments)
                prior = guard._unknown.get(_identity(name, arguments))
                if prior is not None:
                    # A previous inspection/authorization is one attempt only,
                    # including when that attempt later failed or was blocked.
                    prior.consume_permission()
            elif event.get("type") == "tool/result" and key in calls:
                name, arguments = calls[key]
                meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
                identity = _identity(name, arguments)
                if meta.get("recovery_code") == "TOOL_OUTCOME_UNKNOWN":
                    guard._unknown[identity] = _UnknownCall(name, arguments, ":".join(str(part) for part in key))
                else:
                    guard._observe(name, arguments, data.get("ok") is True, meta)
        # Only actual user input is passed here, never assistant/tool prose or
        # uploaded document contents. Old user permissions are not reusable.
        guard.authorize_user_text(user_text)
        return guard

    def authorize_user_text(self, text: str) -> None:
        """Recognize an unambiguous direct human retry command, once per call.

        Deliberately small grammar: a generic 'continue', a quoted instruction,
        an asserted approval in model output, or a negation cannot authorize a
        repeat. Complex requests can still continue through a relevant read.
        """
        # A later human correction replaces an unused retry grant. In
        # particular 'do not retry' must not leave an earlier permission live.
        for call in self._unknown.values():
            call.authorized = False
        if not isinstance(text, str) or "\n" in text or "\r" in text:
            return
        command = text.strip().lower().rstrip(".! ")
        match = re.fullmatch(r"(?:yes[,;:]?\s+)?(?:please\s+)?(?:retry|re-?run|re-?attempt)\s+(.+)", command)
        if match is None:
            return
        target = match[1]
        candidates = list(self._unknown.values())
        generic = r"(?:the |that |this )?(?:interrupted|uncertain) (?:operation|tool call|call|action)"
        if re.fullmatch(generic, target):
            selected = candidates if len(candidates) == 1 else []
        elif target in {"all interrupted operations", "all interrupted tool calls"}:
            selected = candidates
        else:
            selected = [call for call in candidates if target in {
                call.name.lower(),
                f"the interrupted {call.name.lower()} operation",
                f"interrupted call {call.reference.lower()}",
                f"call {call.reference.lower()}",
            }]
            if len(selected) != 1:
                selected = []
        for call in selected:
            call.authorized = True

    def check(self, tool: Tool, arguments: dict) -> ToolResult | None:
        if tool.read_only or tool.side_effect_scope == "none":
            return None
        prior = self._unknown.get(_identity(tool.name, arguments))
        if prior is None or prior.consume_permission():
            return None
        inspection = (f"Read the complete affected file {prior.file_target!r} with read_file before retrying, or "
                      if prior.file_target else "")
        return ToolResult(
            ok=False,
            error=(f"Recovery check required: {tool.name!r} previously started without a durable result, "
                   "so its outcome is unknown. Do not repeat this operation blindly. "
                   f"{inspection}obtain a direct user instruction: 'Retry interrupted call {prior.reference}.' "
                   "Fresh operations and read-only work may continue."),
            meta={"recovery_code": "RECOVERY_CHECK_REQUIRED", "recovery_call": prior.reference},
        )

    def observe(self, tool: Tool, arguments: dict, result: ToolResult) -> None:
        if result.meta.get("recovery_code") == "TOOL_OUTCOME_UNKNOWN":
            # A timed-out transport can lose the outcome without restarting
            # the process. Enforce the same guard for the next call this turn.
            reference = str(result.meta.get("execution_id") or "interrupted")
            self._unknown[_identity(tool.name, arguments)] = _UnknownCall(tool.name, dict(arguments), reference)
            return
        self._observe(tool.name, arguments, result.ok, result.meta)

    def _observe(self, name: str, arguments: dict, ok: bool, meta: dict) -> None:
        invalidated = meta.get("read_state_invalidated")
        if isinstance(invalidated, list):
            paths = {_path(path) for path in invalidated}
            for call in self._unknown.values():
                call.inspected.difference_update(paths)
        if not ok:
            return
        # A recorded successful execution receipt resolves that exact previous
        # uncertainty. Failed or synthetic recovery results never do.
        self._unknown.pop(_identity(name, arguments), None)
        receipt = meta.get("read_state")
        if name != "read_file" or not isinstance(receipt, dict):
            return
        if (receipt.get("source_tool") != "read_file" or receipt.get("schema_version") != 1
                or receipt.get("is_partial_view") is not False or receipt.get("offset") not in (None, 1)
                or receipt.get("limit") is not None
                or not isinstance(receipt.get("revision_id"), str)
                or re.fullmatch(r"[0-9a-f]{64}", receipt["revision_id"]) is None):
            return
        path = _path(receipt.get("path"))
        if path is None or path != _path(arguments.get("path")):
            return
        for call in self._unknown.values():
            if call.file_target == path:
                call.inspected.add(path)
