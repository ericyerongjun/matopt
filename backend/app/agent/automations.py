"""Scheduled and watched agent turns: the engine behind ``/loop`` and ``/automate``.

An *automation* starts an ordinary agent run in a conversation on a schedule:

``every_minutes``   a fixed interval (``/loop`` "check every five minutes");
``cron``            a five-field cron expression in an IANA ``timezone``
                    (``/automate`` "Mondays at 9 AM, Asia/Shanghai").

An optional *watch* makes a loop quiet: instead of running the model on every
tick, the service reads one workspace file and starts a turn only when the
file changes, or, with a ``pattern``, when its content first matches that
regular expression (a finished or failed experiment). A pattern watch fires
once and then completes. No command is executed to check a watch.

Each automation gets ``automations/<id>/state.md`` in its conversation
workspace; the prompt wrapper asks the agent to read it first and record what
it reported, so recurring reports do not repeat unchanged items.

State lives in ``<data_dir>/automations.json`` (atomic replace). The service
is one asyncio task in the backend process, matching the single-worker
runtime; a tick that finds its conversation busy retries a minute later.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.agent.events import ToolResult
from app.agent.tools.base import Tool, ToolContext

logger = logging.getLogger(__name__)

MAX_AUTOMATIONS = 50
MIN_INTERVAL_MINUTES = 1
MAX_PROMPT_CHARS = 4000
WATCH_SNIPPET_CHARS = 4000
BUSY_RETRY_SECONDS = 60
TICK_SECONDS = 15


# ── cron ────────────────────────────────────────────────────────────────

_CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_ALIASES = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
            "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *", "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *"}
_NAMES = {**{m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())},
          **{d: i for i, d in enumerate("sun mon tue wed thu fri sat".split())}}


@dataclass(frozen=True)
class CronSpec:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]      # 0 = Sunday
    dom_star: bool
    dow_star: bool

    def matches(self, t: datetime) -> bool:
        if t.minute not in self.minutes or t.hour not in self.hours or t.month not in self.months:
            return False
        dom = t.day in self.days
        dow = (t.isoweekday() % 7) in self.weekdays
        # Vixie cron: when both day fields are restricted, either may match.
        if self.dom_star or self.dow_star:
            return dom and dow
        return dom or dow


def _field(text: str, lo: int, hi: int) -> frozenset[int]:
    values: set[int] = set()
    for part in text.lower().split(","):
        if not part:
            raise ValueError("empty cron list item")
        rng, _, step_s = part.partition("/")
        step = int(step_s) if step_s else 1
        if step < 1:
            raise ValueError("cron step must be positive")
        if rng == "*":
            a, b = lo, hi
        else:
            a_s, _, b_s = rng.partition("-")
            a = int(_NAMES.get(a_s, a_s))
            b = int(_NAMES.get(b_s, b_s)) if b_s else (hi if step_s else a)
        if not (lo <= a <= hi and lo <= b <= hi and a <= b):
            raise ValueError(f"cron value out of range {lo}-{hi}: {part}")
        values.update(range(a, b + 1, step))
    return frozenset(values)


def parse_cron(expr: str) -> CronSpec:
    expr = _ALIASES.get(expr.strip().lower(), expr.strip())
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("cron needs five fields: minute hour day-of-month month day-of-week")
    try:
        fields = [_field(p, lo, hi) for p, (lo, hi) in zip(parts, _CRON_RANGES)]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid cron expression {expr!r}: {exc}") from None
    weekdays = frozenset(d % 7 for d in fields[4])
    return CronSpec(fields[0], fields[1], fields[2], fields[3], weekdays, parts[2] == "*", parts[4] == "*")


def next_cron(spec: CronSpec, after: datetime) -> datetime:
    """First matching wall-clock minute strictly after ``after`` (an aware datetime)."""
    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = t + timedelta(days=366 * 5)
    while t < limit:
        if t.month not in spec.months:
            t = (t.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
        elif not _day_possible(spec, t):
            t = t.replace(hour=0, minute=0) + timedelta(days=1)
        elif t.hour not in spec.hours:
            t = t.replace(minute=0) + timedelta(hours=1)
        elif t.minute not in spec.minutes:
            t += timedelta(minutes=1)
        else:
            return t
    raise ValueError("cron expression never fires")


def _day_possible(spec: CronSpec, t: datetime) -> bool:
    dom = t.day in spec.days
    dow = (t.isoweekday() % 7) in spec.weekdays
    return (dom and dow) if (spec.dom_star or spec.dow_star) else (dom or dow)


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"Unknown time zone {name!r}; use an IANA name such as Asia/Shanghai") from None


def next_run(schedule: dict, after: float) -> float:
    if schedule.get("every_minutes"):
        return after + float(schedule["every_minutes"]) * 60
    tz = _zone(schedule.get("timezone"))
    local = datetime.fromtimestamp(after, tz)
    return next_cron(parse_cron(schedule["cron"]), local).timestamp()


def describe_schedule(schedule: dict) -> str:
    if schedule.get("every_minutes"):
        m = schedule["every_minutes"]
        return f"every {m} minute{'s' if m != 1 else ''}"
    return f"cron `{schedule['cron']}` ({schedule.get('timezone') or 'UTC'})"


# ── store ───────────────────────────────────────────────────────────────

PUBLIC_FIELDS = ("id", "name", "prompt", "conversation_id", "schedule", "watch", "enabled", "status",
                 "created_at", "next_run_at", "last_run_at", "run_count", "max_runs", "expires_at",
                 "last_status")


def public(row: dict) -> dict:
    out = {k: row.get(k) for k in PUBLIC_FIELDS}
    out["schedule_text"] = describe_schedule(row["schedule"])
    return out


class AutomationStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            logger.warning("Unreadable automations file %s; starting empty", self.path)
            return []
        rows = data.get("automations") if isinstance(data, dict) else None
        return [r for r in rows or [] if isinstance(r, dict) and r.get("id")]

    def save(self, rows: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".automations.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "automations": rows}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


# ── service ─────────────────────────────────────────────────────────────

StartRun = Callable[[str, str, dict], Awaitable[Optional[str]]]   # (cid, prompt, row) -> run id
IsBusy = Callable[[str], bool]


def wrap_prompt(row: dict, trigger: str) -> str:
    state = f"automations/{row['id']}/state.md"
    lines = [
        f"[Automation `{row['name']}` ({row['id']}), {describe_schedule(row['schedule'])}; run {row.get('run_count', 0) + 1}"
        f"{' of ' + str(row['max_runs']) if row.get('max_runs') else ''}. Trigger: {trigger}]",
        f"Read `{state}` first if it exists: it records what earlier runs found and reported. Do not repeat items that"
        f" are unchanged since then. Before you finish, update `{state}` with what you reported this time (dated).",
        "If the task is complete or can no longer succeed, say so and call `delete_automation` with this id.",
        "",
        row["prompt"],
    ]
    return "\n".join(lines)


class AutomationService:
    def __init__(self, store: AutomationStore, workspace_root: Path, start_run: StartRun,
                 is_busy: IsBusy = lambda _cid: False, clock: Callable[[], float] = time.time):
        self.store = store
        self.workspace_root = Path(workspace_root)
        self.start_run = start_run
        self.is_busy = is_busy
        self.clock = clock
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    # CRUD ---------------------------------------------------------------
    def list(self, conversation_id: str | None = None) -> list[dict]:
        rows = self.store.load()
        return [public(r) for r in rows if conversation_id is None or r.get("conversation_id") == conversation_id]

    def get(self, aid: str) -> dict | None:
        return next((r for r in self.store.load() if r["id"] == aid), None)

    def create(self, *, name: str, prompt: str, conversation_id: str, every_minutes: float | None = None,
               cron: str | None = None, timezone: str | None = None, watch_path: str | None = None,
               watch_pattern: str | None = None, max_runs: int | None = None,
               expires_in_hours: float | None = None, run_now: bool = False) -> dict:
        name = " ".join(str(name or "").split())[:80]
        prompt = str(prompt or "").strip()
        if not name or not prompt:
            raise ValueError("name and prompt are required")
        if len(prompt) > MAX_PROMPT_CHARS:
            raise ValueError(f"prompt is longer than {MAX_PROMPT_CHARS} characters")
        if (every_minutes is None) == (cron is None):
            raise ValueError("give exactly one of every_minutes or cron")
        if every_minutes is not None:
            every = float(every_minutes)
            if every < MIN_INTERVAL_MINUTES:
                raise ValueError(f"every_minutes must be at least {MIN_INTERVAL_MINUTES}")
            schedule: dict = {"every_minutes": int(every) if every.is_integer() else every}
        else:
            parse_cron(cron)
            schedule = {"cron": cron.strip(), "timezone": _zone(timezone).key}
        watch = None
        if watch_path:
            self._resolve(conversation_id, watch_path)   # validates containment
            if watch_pattern:
                try:
                    re.compile(watch_pattern)
                except re.error as exc:
                    raise ValueError(f"Invalid watch_pattern: {exc}") from None
            watch = {"path": watch_path, "pattern": watch_pattern or None}
        elif watch_pattern:
            raise ValueError("watch_pattern needs watch_path")
        if max_runs is not None and int(max_runs) < 1:
            raise ValueError("max_runs must be positive")
        now = self.clock()
        rows = self.store.load()
        if len(rows) >= MAX_AUTOMATIONS:
            raise ValueError(f"At most {MAX_AUTOMATIONS} automations; delete one first")
        row = {
            "id": "auto_" + uuid.uuid4().hex[:10], "name": name, "prompt": prompt,
            "conversation_id": conversation_id, "schedule": schedule, "watch": watch,
            "enabled": True, "status": "active", "created_at": now,
            "next_run_at": now if run_now else next_run(schedule, now),
            "last_run_at": None, "run_count": 0, "max_runs": int(max_runs) if max_runs else None,
            "expires_at": now + float(expires_in_hours) * 3600 if expires_in_hours else None,
            "last_status": None, "watch_digest": None,
        }
        if watch and not watch["pattern"]:
            row["watch_digest"] = self._digest(row)[0]     # baseline: fire on the next change
        rows.append(row)
        self.store.save(rows)
        return public(row)

    def update(self, aid: str, *, enabled: bool) -> dict | None:
        rows = self.store.load()
        for r in rows:
            if r["id"] == aid:
                r["enabled"] = bool(enabled)
                if enabled and r.get("status") != "active":
                    r["status"] = "active"
                if enabled:
                    r["next_run_at"] = max(r.get("next_run_at") or 0, self.clock())
                self.store.save(rows)
                return public(r)
        return None

    def delete(self, aid: str) -> bool:
        rows = self.store.load()
        kept = [r for r in rows if r["id"] != aid]
        if len(kept) == len(rows):
            return False
        self.store.save(kept)
        return True

    def delete_for_conversation(self, cid: str) -> int:
        rows = self.store.load()
        kept = [r for r in rows if r.get("conversation_id") != cid]
        if len(kept) != len(rows):
            self.store.save(kept)
        return len(rows) - len(kept)

    # Firing -------------------------------------------------------------
    def _resolve(self, cid: str, rel: str) -> Path:
        root = (self.workspace_root / cid).resolve()
        target = (root / rel).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise ValueError("watch_path must stay inside the conversation workspace") from None
        return target

    def _digest(self, row: dict) -> tuple[str | None, str]:
        try:
            data = self._resolve(row["conversation_id"], row["watch"]["path"]).read_bytes()
        except (OSError, ValueError):
            return None, ""
        text = data[:200_000].decode("utf-8", errors="replace")
        return hashlib.sha256(data).hexdigest(), text

    async def run_now(self, aid: str) -> dict | None:
        async with self._lock:
            rows = self.store.load()
            row = next((r for r in rows if r["id"] == aid), None)
            if row is None:
                return None
            await self._fire(row, "run now (requested)", self.clock())
            self.store.save(rows)
            return public(row)

    async def tick(self) -> int:
        """Fire every due automation once; returns how many runs started."""
        async with self._lock:
            now = self.clock()
            rows = self.store.load()
            started, changed = 0, False
            for row in rows:
                if not row.get("enabled") or row.get("status") != "active":
                    continue
                if row.get("expires_at") and now >= row["expires_at"]:
                    row.update(status="expired", enabled=False)
                    changed = True
                    continue
                if (row.get("next_run_at") or 0) > now:
                    continue
                changed = True
                trigger = "schedule"
                if row.get("watch"):
                    digest, text = self._digest(row)
                    pattern = row["watch"].get("pattern")
                    if pattern:
                        if digest is None or not re.search(pattern, text):
                            row["next_run_at"] = next_run(row["schedule"], now)
                            continue
                        trigger = f"`{row['watch']['path']}` now matches /{pattern}/"
                    else:
                        if digest is None or digest == row.get("watch_digest"):
                            row["next_run_at"] = next_run(row["schedule"], now)
                            continue
                        trigger = f"`{row['watch']['path']}` changed"
                    snippet = text[-WATCH_SNIPPET_CHARS:]
                    trigger += f"\nCurrent content (last {len(snippet)} chars):\n```\n{snippet}\n```"
                if self.is_busy(row["conversation_id"]):
                    row["next_run_at"] = now + BUSY_RETRY_SECONDS
                    continue
                if await self._fire(row, trigger, now):
                    started += 1
            if changed:
                self.store.save(rows)
            return started

    async def _fire(self, row: dict, trigger: str, now: float) -> bool:
        try:
            run_id = await self.start_run(row["conversation_id"], wrap_prompt(row, trigger), row)
        except Exception as exc:  # noqa: BLE001 - a broken run must not stop the scheduler
            logger.warning("Automation %s failed to start: %s", row["id"], type(exc).__name__)
            row["last_status"] = f"failed to start: {type(exc).__name__}"
            row["next_run_at"] = now + BUSY_RETRY_SECONDS
            return False
        row["run_count"] = int(row.get("run_count") or 0) + 1
        row["last_run_at"] = now
        row["last_status"] = f"started run {run_id}" if run_id else "started"
        if row.get("watch"):
            row["watch_digest"] = self._digest(row)[0]
            if row["watch"].get("pattern"):
                row.update(status="completed", enabled=False)
        if row.get("max_runs") and row["run_count"] >= row["max_runs"]:
            row.update(status="completed", enabled=False)
        row["next_run_at"] = next_run(row["schedule"], now)
        return True

    # Lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="automations")

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning("Automation tick failed", exc_info=True)
            await asyncio.sleep(TICK_SECONDS)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None


_service: AutomationService | None = None


def set_automation_service(service: AutomationService | None) -> None:
    global _service
    _service = service


def get_automation_service() -> AutomationService | None:
    return _service


# ── tools ───────────────────────────────────────────────────────────────

def _unavailable() -> ToolResult:
    return ToolResult(ok=False, error="Automations are not available on this server.")


class CreateAutomationTool(Tool):
    name = "create_automation"
    side_effect_scope = "system"
    risk_level = "medium"
    needs_approval = True
    ui_kind = "generic"
    description = (
        "Schedule recurring agent work (the engine behind /loop and /automate). Each run is a normal agent turn in "
        "a conversation with `prompt` as the request. Give exactly one schedule: `every_minutes` (interval, >= 1) or "
        "`cron` (5 fields, e.g. '0 9 * * 1' = Mondays 09:00) with an IANA `timezone`. For a quiet monitor, set "
        "`watch_path` (a workspace file): a turn starts only when the file changes, or with `watch_pattern` "
        "(regex) once when it first matches, e.g. '\"status\":\\s*\"(finished|failed)\"'. `target` 'this' runs in "
        "this conversation; 'new' creates a dedicated conversation. Runs keep notes in "
        "automations/<id>/state.md so reports do not repeat. Only create one when the user asked for it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Short label, e.g. 'Weekly reading list'."},
            "prompt": {"type": "string", "description": "What each run should do and what to report."},
            "every_minutes": {"type": "number", "minimum": 1},
            "cron": {"type": "string", "description": "minute hour day-of-month month day-of-week"},
            "timezone": {"type": "string", "description": "IANA zone for cron, default UTC."},
            "target": {"type": "string", "enum": ["this", "new"], "description": "Default 'this'."},
            "watch_path": {"type": "string", "description": "Workspace-relative file to watch (quiet mode)."},
            "watch_pattern": {"type": "string", "description": "Fire once when the file matches this regex."},
            "max_runs": {"type": "integer", "minimum": 1},
            "expires_in_hours": {"type": "number", "exclusiveMinimum": 0},
            "run_now": {"type": "boolean", "description": "Also run once at the next tick."},
        },
        "required": ["name", "prompt"],
    }

    async def run(self, ctx: ToolContext, name: str, prompt: str, target: str = "this", **kw: Any) -> ToolResult:
        service = get_automation_service()
        if service is None:
            return _unavailable()
        cid = ctx.conversation_id
        if target == "new":
            from app.runtime import get_runtime
            cid = get_runtime().db.create_conversation(title=f"Automation: {name}"[:80])["id"]
        try:
            row = service.create(name=name, prompt=prompt, conversation_id=cid, **{
                k: kw[k] for k in ("every_minutes", "cron", "timezone", "watch_path", "watch_pattern",
                                   "max_runs", "expires_in_hours", "run_now") if kw.get(k) is not None})
        except ValueError as exc:
            if target == "new":
                from app.runtime import get_runtime
                get_runtime().db.delete_conversation(cid)
            return ToolResult(ok=False, error=str(exc))
        when = datetime.fromtimestamp(row["next_run_at"], _zone(row["schedule"].get("timezone"))).strftime("%Y-%m-%d %H:%M %Z")
        watch = f"; watching `{row['watch']['path']}`" if row.get("watch") else ""
        where = "this conversation" if cid == ctx.conversation_id else f"new conversation {cid}"
        return ToolResult(ok=True, output=(f"Created automation {row['id']} '{row['name']}': {row['schedule_text']}"
                                           f"{watch}; runs in {where}; next check {when}. "
                                           "The user can pause or delete it in Settings → Automations."),
                          meta={"automation": row})


class ListAutomationsTool(Tool):
    name = "list_automations"
    read_only = True
    concurrent_safe = True
    description = "List scheduled automations (all, or only this conversation's with scope='this')."
    parameters = {"type": "object", "properties": {"scope": {"type": "string", "enum": ["all", "this"]}}}

    async def run(self, ctx: ToolContext, scope: str = "all", **_: Any) -> ToolResult:
        service = get_automation_service()
        if service is None:
            return _unavailable()
        rows = service.list(ctx.conversation_id if scope == "this" else None)
        if not rows:
            return ToolResult(ok=True, output="No automations.")
        lines = [f"{r['id']}  {r['name']}  {r['schedule_text']}  {r['status']}{'' if r['enabled'] else ' (paused)'}"
                 f"  runs={r['run_count']}  conversation={r['conversation_id']}" for r in rows]
        return ToolResult(ok=True, output="\n".join(lines), meta={"automations": rows})


class DeleteAutomationTool(Tool):
    name = "delete_automation"
    side_effect_scope = "system"
    description = "Delete a scheduled automation by id (stop a /loop or /automate). Use when it is done or the user asks."
    parameters = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}

    async def run(self, ctx: ToolContext, id: str, **_: Any) -> ToolResult:
        service = get_automation_service()
        if service is None:
            return _unavailable()
        if not service.delete(id):
            return ToolResult(ok=False, error=f"No automation {id!r}")
        return ToolResult(ok=True, output=f"Deleted automation {id}.")


def automation_tools() -> list[Tool]:
    return [CreateAutomationTool(), ListAutomationsTool(), DeleteAutomationTool()]
