"""Command hooks around the agent loop, ported from Codex (``codex-rs/hooks``).

An operator configures shell commands that run at fixed points of a turn. Each
hook gets one JSON object on stdin and answers through its exit code and
stdout, the protocol Codex shares with Claude Code:

``exit 0``   stdout, if any, is parsed as JSON (plain text is taken as
             additional context for the events that accept it).
``exit 2``   block; stderr is the reason, fed back to the model.
other        a non-blocking hook error, logged and otherwise ignored.

Events and what they may do:

``SessionStart``      first turn of a conversation; add context.
``UserPromptSubmit``  before a request runs; block it, or add context.
``PreToolUse``        before a tool runs; deny it, or rewrite its arguments
                      (``hookSpecificOutput.updatedInput``, re-validated).
``PostToolUse``       after a tool ran; feed a reason or context back to the
                      model alongside the result.
``Stop``              the agent is about to answer; block to make it continue
                      with the reason (bounded per turn).

Configuration is a ``hooks.json`` in Codex's shape::

    {"hooks": {"PreToolUse": [{"matcher": "bash|python",
                               "hooks": [{"type": "command", "command": "...", "timeout": 30}]}]}}

``matcher`` is a regular expression over the tool name (``*`` or empty
matches everything) and is read only for the tool events. Hooks are off unless
``settings.hooks_enabled``: a hook runs as the backend user with the scrubbed
child environment, so only an operator-owned file may turn them on — never a
workspace file the agent can write.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.agent.child_env import build_child_env

logger = logging.getLogger(__name__)

EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")
MATCHER_EVENTS = frozenset({"PreToolUse", "PostToolUse"})
CONTEXT_EVENTS = frozenset({"SessionStart", "UserPromptSubmit"})
MAX_CONTEXT_CHARS = 10_000
MAX_STOP_BLOCKS = 2


@dataclass
class HookHandler:
    command: str
    timeout: Optional[float] = None


@dataclass
class MatcherGroup:
    matcher: Optional[re.Pattern[str]]
    handlers: list[HookHandler]

    def matches(self, target: str) -> bool:
        return self.matcher is None or self.matcher.fullmatch(target) is not None


@dataclass
class HookOutcome:
    """The merged result of every hook that ran for one event."""

    blocked: bool = False
    reason: str = ""
    updated_input: Optional[dict] = None
    context: list[str] = field(default_factory=list)

    @property
    def additional_context(self) -> str:
        return "\n\n".join(c for c in self.context if c).strip()[:MAX_CONTEXT_CHARS]


def _compile_matcher(raw: Any) -> Optional[re.Pattern[str]]:
    if raw in (None, "", "*"):
        return None
    if not isinstance(raw, str):
        raise ValueError("matcher must be a string")
    return re.compile(raw)


def parse_hooks_config(raw: Any) -> dict[str, list[MatcherGroup]]:
    """Validate a ``hooks.json`` document. Unknown events are rejected loudly."""
    if not isinstance(raw, dict):
        raise ValueError("hooks config must be an object")
    hooks = raw.get("hooks", raw)
    if not isinstance(hooks, dict):
        raise ValueError("'hooks' must be an object")
    parsed: dict[str, list[MatcherGroup]] = {}
    for event, groups in hooks.items():
        if event not in EVENTS:
            raise ValueError(f"Unknown hook event {event!r}; supported: {', '.join(EVENTS)}")
        if not isinstance(groups, list):
            raise ValueError(f"{event} must be a list of matcher groups")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError(f"{event}: each group needs a 'hooks' list")
            handlers = []
            for handler in group["hooks"]:
                if not isinstance(handler, dict) or handler.get("type", "command") != "command":
                    raise ValueError(f"{event}: only command hooks are supported")
                command = handler.get("command")
                if not isinstance(command, str) or not command.strip():
                    raise ValueError(f"{event}: a command hook needs a 'command'")
                timeout = handler.get("timeout")
                if timeout is not None and (type(timeout) not in (int, float) or timeout <= 0):
                    raise ValueError(f"{event}: timeout must be a positive number")
                handlers.append(HookHandler(command, float(timeout) if timeout is not None else None))
            matcher = _compile_matcher(group.get("matcher")) if event in MATCHER_EVENTS else None
            parsed.setdefault(event, []).append(MatcherGroup(matcher, handlers))
    return parsed


class HookEngine:
    def __init__(self, config: Optional[dict[str, list[MatcherGroup]]] = None, *, default_timeout: float = 60.0):
        self.config = config or {}
        self.default_timeout = default_timeout

    @classmethod
    def from_settings(cls, settings: Any, extra: Optional[dict] = None) -> "HookEngine":
        """Hooks from ``hooks.json`` plus ``extra`` (enabled plugins' hooks), when hooks are on."""
        if not getattr(settings, "hooks_enabled", False):
            return cls()
        path = getattr(settings, "hooks_path", None) or Path(settings.data_dir) / "hooks.json"
        timeout = float(getattr(settings, "hook_timeout", 60.0))
        try:
            config = parse_hooks_config(json.loads(Path(path).read_text(encoding="utf-8")))
        except FileNotFoundError:
            config = {}
        except (OSError, ValueError, re.error) as exc:
            # A broken hooks file disables hooks rather than half-applying them.
            logger.error("Hooks disabled: invalid %s (%s)", path, exc)
            return cls(default_timeout=timeout)
        if extra:
            try:
                for event, groups in parse_hooks_config(extra).items():
                    config.setdefault(event, []).extend(groups)
            except (ValueError, re.error) as exc:
                logger.error("Plugin hooks ignored: %s", exc)
        logger.info("Hooks loaded from %s: %s", path, ", ".join(sorted(config)) or "none")
        return cls(config, default_timeout=timeout)

    def has(self, event: str) -> bool:
        return bool(self.config.get(event))

    async def run(self, event: str, payload: dict, *, cwd: Optional[Path] = None,
                  target: str = "") -> HookOutcome:
        handlers = [h for group in self.config.get(event, []) if group.matches(target) for h in group.handlers]
        outcome = HookOutcome()
        if not handlers:
            return outcome
        body = json.dumps({"hook_event_name": event, **payload}, ensure_ascii=False, default=str)
        # Handlers for one event run concurrently; results merge in config order.
        results = await asyncio.gather(*(self._run_one(event, h, body, cwd) for h in handlers))
        for result in results:
            if result is None:
                continue
            if result.blocked and not outcome.blocked:
                outcome.blocked, outcome.reason = True, result.reason
            if result.updated_input is not None and outcome.updated_input is None:
                outcome.updated_input = result.updated_input
            outcome.context.extend(result.context)
        return outcome

    async def _run_one(self, event: str, handler: HookHandler, body: str,
                       cwd: Optional[Path]) -> Optional[HookOutcome]:
        timeout = handler.timeout or self.default_timeout
        env = build_child_env(extra={"MATOPT_HOOK_EVENT": event})
        try:
            proc = await asyncio.create_subprocess_shell(
                handler.command, cwd=str(cwd) if cwd else None, env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True)
        except OSError as exc:
            logger.warning("Hook %s could not start: %s", event, exc)
            return None
        try:
            # The timeout covers writing stdin too, as in Codex.
            stdout, stderr = await asyncio.wait_for(proc.communicate(body.encode()), timeout=timeout)
        except asyncio.TimeoutError:
            # The shell's whole process group, so a child it started dies too.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            await proc.wait()
            logger.warning("Hook %s timed out after %.0fs: %s", event, timeout, handler.command)
            return None
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode == 2:
            return HookOutcome(blocked=True, reason=err or f"Blocked by a {event} hook.")
        if proc.returncode != 0:
            logger.warning("Hook %s exited %s: %s", event, proc.returncode, err[:500])
            return None
        return _parse_output(event, out)


def _parse_output(event: str, out: str) -> HookOutcome:
    if not out:
        return HookOutcome()
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return HookOutcome(context=[out] if event in CONTEXT_EVENTS else [])
    if not isinstance(data, dict):
        return HookOutcome()
    specific = data.get("hookSpecificOutput") if isinstance(data.get("hookSpecificOutput"), dict) else {}
    outcome = HookOutcome()
    reason = specific.get("permissionDecisionReason") or data.get("reason") or data.get("stopReason") or ""
    if (data.get("continue") is False or data.get("decision") == "block"
            or specific.get("permissionDecision") == "deny"):
        outcome.blocked, outcome.reason = True, str(reason or f"Blocked by a {event} hook.")
    updated = specific.get("updatedInput")
    if event == "PreToolUse" and isinstance(updated, dict):
        outcome.updated_input = updated
    # ``systemMessage`` is for the user in Codex, not the model; it is ignored.
    for text in (specific.get("additionalContext"), data.get("additionalContext")):
        if isinstance(text, str) and text.strip():
            outcome.context.append(text.strip())
    return outcome
