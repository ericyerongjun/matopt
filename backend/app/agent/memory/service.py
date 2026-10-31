"""The memory service: runs the pipeline and serves the read path.

Codex starts the pipeline when a root session starts (``memories/write/src/
start.rs``) and injects the read path as developer instructions
(``memories/read``). MatOpt exposes the same two halves on one object:

``run_pass()``          Phase 1 for every eligible conversation (bounded per
                        pass, a few in parallel), then Phase 2. Safe to call
                        from a background task: one pass runs at a time and a
                        pass without a configured provider is a clean no-op.
``read_path_prompt()``  the block appended to the system prompt: the capped
                        ``memory_summary.md`` plus the rules of Codex
                        ``read_path.md`` (when to look, staleness, never
                        instructions).

Side calls use the first configured provider with reasoning off, the way the
harness's compaction and title tasks do (``AgentHarness._summarize_span``); an
optional :class:`~app.agent.budget.RunBudget` admits and settles each call.
A ``provider_factory`` may replace the registry lookup (tests, or a dedicated
memory model later).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Optional

from app.agent.memory import consolidate as phase2
from app.agent.memory import extract as phase1
from app.agent.memory.prompts import READ_PATH
from app.agent.memory.store import MemoryStore
from app.llm.base import ChatOptions

logger = logging.getLogger(__name__)

MAX_EXTRACTIONS_PER_PASS = 16
EXTRACTION_CONCURRENCY = 4
SCAN_LIMIT = 200
STAGE1_MAX_TOKENS = 4_000
PENDING_NOTES_CHARS = 2_000

ProviderFactory = Callable[[], Optional[tuple[Any, ChatOptions]]]


class MemoryService:
    def __init__(self, settings, db, providers=None, *, provider_factory: ProviderFactory | None = None,
                 root=None):
        self.settings = settings
        self.db = db
        self.providers = providers
        self.provider_factory = provider_factory
        self.store = MemoryStore(root or settings.data_dir / "memories")
        self._pass_lock = asyncio.Lock()
        self._phase2_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.memories_enabled)

    # ── provider ────────────────────────────────────────────────────────

    def _provider(self) -> Optional[tuple[Any, ChatOptions]]:
        if self.provider_factory is not None:
            try:
                return self.provider_factory()
            except Exception:  # noqa: BLE001
                logger.warning("memory: provider factory failed", exc_info=True)
                return None
        registry = self.providers
        if registry is None:
            return None
        try:
            if not registry.configured_ids():
                return None
            spec, model = registry.resolve(None, None)
            provider = registry.get(spec.id)
        except Exception:  # noqa: BLE001 — not configured is not an error here
            logger.debug("memory: no provider", exc_info=True)
            return None
        return provider, ChatOptions(model=model.id, reasoning="off")

    def _completer(self, task: str, max_tokens: int, budget=None):
        """A ``complete(messages) -> text | None`` side-call, or ``None`` with no provider."""
        resolved = self._provider()
        if resolved is None:
            return None
        provider, options = resolved
        opts = ChatOptions(model=options.model, reasoning="off", max_tokens=max_tokens, temperature=None,
                           extra=dict(options.extra or {}))

        async def complete(messages: list[dict]) -> Optional[str]:
            from app.agent.budget import BudgetExhausted
            from app.storage.db import new_id

            task_id = new_id("task_")
            if budget is not None:
                try:
                    budget.admit_model(task_id, source=task, provider=getattr(provider, "id", ""),
                                       model=opts.model)
                except BudgetExhausted:
                    return None
            usage, outcome = None, "error"
            try:
                result = await provider.complete(messages, opts)
                usage, outcome = result.usage, "completed"
                return result.text
            except asyncio.CancelledError:
                outcome = "aborted"
                raise
            except Exception:  # noqa: BLE001 — a failed side task is retried next pass
                logger.warning("memory: %s call failed", task, exc_info=True)
                return None
            finally:
                if budget is not None:
                    budget.settle_model(task_id, usage=usage, outcome=outcome)

        return complete

    # ── pipeline ────────────────────────────────────────────────────────

    def eligible_conversations(self, *, now: float | None = None) -> tuple[list[dict], dict[str, str]]:
        """Conversations Phase 1 may read now, and why the others were skipped."""
        state = self.store.load_state()
        disabled = set(state.get("disabled") or [])
        cleared_at = state.get("cleared_at")
        eligible, skipped = [], {}
        for conv in self.db.list_conversations(limit=SCAN_LIMIT):
            reason = phase1.ineligible_reason(conv, self.store, self.settings, now=now,
                                              disabled=disabled, cleared_at=cleared_at)
            if reason:
                skipped[conv["id"]] = reason
            else:
                eligible.append(conv)
        return eligible, skipped

    async def run_pass(self, *, force_consolidate: bool = False, budget=None,
                       now: float | None = None) -> dict:
        """Phase 1 for eligible conversations, then Phase 2. Returns a report."""
        if not self.enabled:
            return {"enabled": False}
        if self._pass_lock.locked():
            return {"enabled": True, "busy": True}
        async with self._pass_lock:
            report: dict[str, Any] = {"enabled": True, "extracted": [], "no_output": [], "failed": [],
                                      "skipped": {}}
            eligible, report["skipped"] = self.eligible_conversations(now=now)
            complete = self._completer("memory_stage1", STAGE1_MAX_TOKENS, budget)
            if complete is None:
                report["provider"] = False
            elif eligible:
                await self._phase1(eligible[:MAX_EXTRACTIONS_PER_PASS], complete, report)
            report["consolidation"] = await self.consolidate(force=force_consolidate, budget=budget)
            return report

    async def _phase1(self, convs: list[dict], complete, report: dict) -> None:
        gate = asyncio.Semaphore(EXTRACTION_CONCURRENCY)

        async def one(conv: dict) -> None:
            cid = conv["id"]
            transcript = phase1.render_conversation(self.db, cid)
            reason = phase1.transcript_reason(transcript, self.settings)
            if reason:
                report["skipped"][cid] = reason
                # Remember the decision so the conversation is not re-read
                # until it changes; an empty record carries no memory.
                self.store.put_stage1({"conversation_id": cid,
                                       "source_updated_at": float(conv.get("updated_at") or 0),
                                       "raw_memory": "", "summary": "", "slug": "",
                                       "generated_at": time.time(), "usage_count": 0, "skipped": reason})
                return
            async with gate:
                record = await phase1.extract(complete, conv, transcript)
            if record is None:
                report["failed"].append(cid)
                return
            # The user may have opted out while the call ran.
            if cid in self.store.disabled():
                return
            self.store.put_stage1(record)
            report["extracted" if record["raw_memory"] else "no_output"].append(cid)

        await asyncio.gather(*(one(c) for c in convs))

    async def consolidate(self, *, force: bool = False, budget=None) -> str:
        if not self.enabled:
            return "disabled"
        async with self._phase2_lock:
            max_tokens = min(16_000, int(self.settings.memory_summary_tokens) + 8_000)
            complete = self._completer("memory_consolidation", max_tokens, budget)
            return await phase2.consolidate(self.store, self.settings, complete, force=force)

    # ── read path ───────────────────────────────────────────────────────

    def read_path_prompt(self) -> str:
        """The system-prompt block, or ``""`` when memory is off or empty."""
        if not self.enabled:
            return ""
        summary = phase2.cap_lines(self.store.read_summary(), phase2.summary_char_cap(self.settings))
        pending = self.pending_notes()
        if pending:
            # Notes the user just asked for apply now, before a refresh folds them in.
            block = "\n".join(f"- {n['text'].strip()} [ad-hoc note]" for n in pending)
            summary = (summary + "\n\n" if summary else "") + "## Recent notes (not yet consolidated)\n" \
                + phase2.cap_lines(block, PENDING_NOTES_CHARS)
        if not summary:
            return ""
        return READ_PATH.format(summary=summary)

    def pending_notes(self) -> list[dict]:
        consumed = self.store.load_state().get("consumed_notes") or {}
        return [n for n in self.store.list_notes() if consumed.get(n["id"]) != n.get("updated_at")]

    def tools(self) -> list:
        from app.agent.memory.tools import MemoryNoteTool, MemoryReadTool, MemorySearchTool
        return [MemorySearchTool(self), MemoryReadTool(self), MemoryNoteTool(self)]

    # ── management (API) ────────────────────────────────────────────────

    def list(self) -> dict:
        records = self.store.list_stage1()
        return {
            "enabled": self.enabled,
            "summary": self.store.read_summary(),
            "memory_md": self.store.read_memory_md(),
            "notes": [{k: n[k] for k in ("id", "title", "text", "created_at")} for n in self.store.list_notes()],
            "conversations_learned": sum(1 for r in records if (r.get("raw_memory") or "").strip()),
        }

    def add_note(self, text: str) -> dict:
        from app.agent.exec_output import redact_secrets
        text = redact_secrets((text or "").strip())
        if not text:
            raise ValueError("A note needs text")
        return self.store.add_note(text)

    def update_note(self, note_id: str, text: str) -> Optional[dict]:
        from app.agent.exec_output import redact_secrets
        text = redact_secrets((text or "").strip())
        if not text:
            raise ValueError("A note needs text")
        return self.store.update_note(note_id, text)

    def delete_note(self, note_id: str) -> bool:
        return self.store.delete_note(note_id)

    def clear(self) -> None:
        self.store.clear()

    def set_conversation_mode(self, cid: str, enabled: bool) -> None:
        """Opt a conversation in or out; opting out forgets what it taught."""
        self.store.set_disabled(cid, not enabled)
        if not enabled:
            self.store.delete_stage1(cid)

    def conversation_enabled(self, cid: str) -> bool:
        return cid not in self.store.disabled()


_service: Optional[MemoryService] = None


def get_memory_service() -> MemoryService:
    """The process-wide service, built lazily from the runtime's pieces."""
    global _service
    if _service is None:
        from app.runtime import get_runtime
        rt = get_runtime()
        from app.config import settings
        _service = MemoryService(settings, rt.db, rt.providers)
    return _service


def set_memory_service(service: Optional[MemoryService]) -> None:
    """Install (or reset with ``None``) the process-wide service; for wiring and tests."""
    global _service
    _service = service
