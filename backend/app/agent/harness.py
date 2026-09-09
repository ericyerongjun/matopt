"""
The agent harness: streams one assistant turn.

    user message ─► [LLM ⇄ tools]* ─► final answer ─► (verify ⇄ revise)? ─► persist

Design notes
- Everything the client needs is emitted as :class:`Event`s through an
  asyncio queue, so tools can push progress while the model is idle.
- The turn is bounded: ``max_steps`` LLM calls, ``max_consecutive_failures``
  failed tool batches; the model is nudged to conclude when limits approach.
- Cancellation is cooperative: ``cancel`` is checked between deltas and tool
  calls, running kernels are interrupted, and partial work is persisted.
- Verification (optional) runs an independent verifier with tools; if it
  reports "incorrect" the solver revises, up to ``verify_rounds`` times.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from app.agent.datasets import datasets_block
from app.agent.events import Event, ToolResult, ev, keepalive
from app.agent.prompts import (
    FAILURE_NUDGE,
    FINAL_STEP_NUDGE,
    REPEAT_NUDGE,
    REVISION_TEMPLATE,
    TITLE_PROMPT,
    VERIFIER_PROMPT,
    VERIFIER_USER_TEMPLATE,
    build_system_prompt,
)
from app.agent.monitor import inspect_tool_call, next_step_options
from app.agent.skills.loader import SkillRegistry
from app.agent.skills.slash import default_slash_prompt, split_slash_commands
from app.agent.skills.materialize import materialize_skill
from app.agent.skills.router import RoutePlan, SkillRouter
from app.agent.tools.base import ToolContext, ToolRegistry, truncate_text
from app.agent.tools.router import ToolRouter
from app.agent.workspace import Workspace
from app.llm.base import ChatOptions, LLMProvider, ToolCallSpec
from app.storage.db import Database, new_id

logger = logging.getLogger(__name__)

EmitFn = Callable[[Event], Awaitable[None]]


class Stopped(Exception):
    """Raised inside the loop when the user cancelled the run."""


@dataclass
class RunRequest:
    conversation_id: Optional[str]
    text: str
    attachments: list[dict] = field(default_factory=list)   # [{name, path, url, size}]
    provider_id: Optional[str] = None
    model_id: Optional[str] = None
    reasoning: Optional[str] = None
    verify: bool = False
    temperature: Optional[float] = None
    skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    web_search: bool = False


@dataclass
class SteerNote:
    text: str
    skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)


REPEAT_LIMIT = 3
SOFT_SUCCESS_TOOLS = frozenset({
    "list_files", "read_file", "grep", "bash", "web_search", "web_fetch",
    "update_plan", "search_skills", "load_skill",
})


@dataclass
class TurnState:
    parts: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    llm_calls: int = 0
    tool_calls: int = 0
    phase: str = "answer"
    plan: list = field(default_factory=list)
    failure_counts: dict[str, int] = field(default_factory=dict)
    blocked_tools: set[str] = field(default_factory=set)
    repeat_nudge_sent: bool = False


class AgentHarness:
    def __init__(self, db: Database, kernels, settings, providers, tools: ToolRegistry,
                 skills: Optional[SkillRegistry] = None, tool_router: Optional[ToolRouter] = None):
        self.db = db
        self.kernels = kernels
        self.settings = settings
        self.providers = providers
        self.tools = tools
        self.skills = skills or SkillRegistry()
        self.skill_router = SkillRouter(
            self.skills,
            auto_activate_max=getattr(settings, "skill_auto_activate_max", 2),
        )
        self.tool_router = tool_router or ToolRouter()
        self._active: dict[str, asyncio.Event] = {}     # conversation_id -> cancel flag
        self._steer: dict[str, asyncio.Queue[SteerNote]] = {}

    # ── public API ──────────────────────────────────────────────────────

    def is_running(self, conversation_id: str) -> bool:
        return conversation_id in self._active

    def stop(self, conversation_id: str) -> bool:
        flag = self._active.get(conversation_id)
        if flag is None:
            return False
        flag.set()
        return True

    def enqueue_steer(
        self,
        conversation_id: str,
        text: str,
        skills: Optional[list[str]] = None,
        tools: Optional[list[str]] = None,
    ) -> bool:
        """Queue a mid-turn correction (Claude/Astra-style fold at the next tool boundary)."""
        q = self._steer.get(conversation_id)
        if q is None or not (text or "").strip():
            return False
        q.put_nowait(SteerNote(text=text.strip(), skills=list(skills or []), tools=list(tools or [])))
        return True

    def _parse_slash(self, text: str, extra_skills: Optional[list[str]] = None,
                     extra_tools: Optional[list[str]] = None) -> tuple[str, list[str], list[str]]:
        rest, skills, tools = split_slash_commands(text, self.skills.names(), self.tools.names())
        forced_skills = list(dict.fromkeys([*(extra_skills or []), *skills]))
        forced_tools = list(dict.fromkeys([*(extra_tools or []), *tools]))
        return rest, forced_skills, forced_tools

    def _resolve_tools(self, names: list[str]) -> list[str]:
        resolved: list[str] = []
        for name in names:
            tool, _ = self.tool_router.resolve(self.tools, name)
            if tool is not None and tool.name not in resolved:
                resolved.append(tool.name)
        return resolved

    async def _drain_steer(
        self,
        conversation_id: str,
        history: list,
        emit: EmitFn,
        ctx: ToolContext,
        state: TurnState,
        route: Optional[RoutePlan] = None,
    ) -> bool:
        q = self._steer.get(conversation_id)
        notes: list[SteerNote] = []
        while q is not None and not q.empty():
            notes.append(q.get_nowait())
        if not notes:
            return False
        for note in notes:
            rest, skill_names, tool_names = self._parse_slash(note.text, note.skills, note.tools)
            tool_names = self._resolve_tools(tool_names)
            llm_text = rest.strip() or default_slash_prompt(skill_names, tool_names) or note.text
            if skill_names:
                await self._force_skills(skill_names, ctx, emit, state, route, history)
            if tool_names:
                preferred = list(ctx.state.get("tools") or [])
                for name in tool_names:
                    if name not in preferred:
                        preferred.append(name)
                ctx.state["tools"] = preferred
                if any(n in {"web_search", "web_fetch"} for n in tool_names):
                    ctx.state["web_search"] = True
            prefix = default_slash_prompt(skill_names, tool_names)
            block = f"{prefix}\n\n{llm_text}".strip() if prefix and prefix not in llm_text else llm_text
            history.append({"role": "user", "content": f"[Steer] {block}"})
            stored = note.text.strip()
            self.db.add_message(conversation_id, "user", {"text": stored, "attachments": []})
            await emit(ev("steer_applied", text=stored, skills=skill_names, tools=tool_names))
        return True

    async def run(self, req: RunRequest) -> AsyncIterator[Event]:
        queue: asyncio.Queue[Optional[Event]] = asyncio.Queue()

        async def emit(event: Event) -> None:
            await queue.put(event)

        task = asyncio.create_task(self._run_inner(req, emit, queue))
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=self.settings.keepalive_seconds)
                except asyncio.TimeoutError:
                    if task.done() and queue.empty():
                        break
                    yield keepalive()
                    continue
                if item is None:
                    break
                yield item
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    # ── the turn ────────────────────────────────────────────────────────

    async def _run_inner(self, req: RunRequest, emit: EmitFn, queue: asyncio.Queue) -> None:
        cid = req.conversation_id or ""
        cancel = asyncio.Event()
        try:
            spec, model = self.providers.resolve(req.provider_id, req.model_id)
            provider = self.providers.get(spec.id)
            level = req.reasoning if req.reasoning in model.reasoning_levels else model.default_reasoning

            conv = self.db.get_conversation(cid) if cid else None
            if conv is None:
                conv = self.db.create_conversation(title=_heuristic_title(req.text), provider=spec.id, model=model.id, reasoning=level, conversation_id=cid or None)
                cid = conv["id"]
            else:
                self.db.update_conversation(cid, provider=spec.id, model=model.id, reasoning=level)
            if cid in self._active:
                await emit(ev("error", message="A run is already in progress for this conversation."))
                return
            self._active[cid] = cancel
            self._steer[cid] = asyncio.Queue()

            workspace = Workspace(cid, self.settings.workspace_dir)
            prior_messages = self.db.list_messages(cid)
            original_text = req.text
            slash_rest, slash_skills, slash_tools = self._parse_slash(req.text, req.skills, req.tools)
            slash_tools = self._resolve_tools(slash_tools)
            hint = default_slash_prompt(slash_skills, slash_tools)
            rest = slash_rest.strip()
            if rest and hint:
                llm_text = f"{hint}\n\n{rest}"
            else:
                llm_text = rest or hint or req.text
            req = dataclasses.replace(
                req,
                text=llm_text,
                skills=slash_skills,
                tools=slash_tools,
                web_search=bool(req.web_search or any(n in {"web_search", "web_fetch"} for n in slash_tools)),
            )
            self.db.add_message(cid, "user", {"text": original_text, "attachments": req.attachments})
            await emit(ev("conversation", conversation_id=cid, title=conv["title"]))

            message_id = new_id("m_")
            await emit(ev("message_start", message_id=message_id, provider=spec.id, model=model.id, reasoning=level))

            state = TurnState()
            ctx = ToolContext(
                conversation_id=cid, workspace=workspace, emit=emit, cancel=cancel,
                state={"plan": state.plan, "skills": []}, kernels=self.kernels, settings=self.settings,
            )
            route = self._route_turn(req, workspace)
            await self._activate_skills(route, ctx, emit, forced=bool(req.skills), state=state)
            if req.tools:
                ctx.state["tools"] = list(req.tools)
            options = ChatOptions(model=model.id, reasoning=level, temperature=req.temperature, max_tokens=self.settings.max_output_tokens or None)
            system_prompt = self._system_prompt(workspace, route)
            history = [{"role": "system", "content": system_prompt}]
            history += self._replay(prior_messages, provider)
            history.append({"role": "user", "content": _user_content(req.text, req.attachments)})

            status = "complete"
            final_text = ""
            try:
                final_text = await self._agent_loop(
                    provider, options, history, ctx, state, emit, tools=self.tools,
                    max_steps=self.settings.agent_max_steps, query=req.text, route=route,
                    web_search=bool(req.web_search),
                )
                if req.verify and final_text.strip() and not cancel.is_set():
                    final_text = await self._verify_and_revise(provider, options, history, ctx, state, emit, req.text, final_text)
                choices = next_step_options(ctx.state.get("plan") or state.plan, final_text)
                if choices:
                    state.parts.append({"type": "options", "items": choices})
                    await emit(ev("options", items=choices))
            except Stopped:
                status = "stopped"
            except asyncio.CancelledError:
                status = "stopped"
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("Agent run failed")
                status = "error"
                await emit(ev("error", message=_friendly_error(exc)))
            finally:
                content = {
                    "parts": state.parts, "model": model.id, "provider": spec.id, "reasoning": level,
                    "usage": state.usage, "status": status, "plan": ctx.state.get("plan", []),
                    "stats": {"llm_calls": state.llm_calls, "tool_calls": state.tool_calls},
                }
                self.db.add_message(cid, "assistant", content, message_id=message_id)
                await emit(ev("message_end", message_id=message_id, status=status, content=content))

            if status == "stopped":
                await emit(ev("stopped", conversation_id=cid, message_id=message_id))
            elif status == "complete":
                await emit(ev("done", conversation_id=cid, message_id=message_id))
            if not prior_messages and status != "error":
                asyncio.create_task(self._auto_title(cid, provider, options, req.text))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Run setup failed")
            await emit(ev("error", message=_friendly_error(exc)))
        finally:
            self._active.pop(cid, None)
            self._steer.pop(cid, None)
            await queue.put(None)

    # ── system prompt & history ─────────────────────────────────────────

    def _system_prompt(self, workspace: Workspace, route: Optional[RoutePlan] = None) -> str:
        files_block = workspace.describe_for_prompt()
        try:
            ds = datasets_block(workspace)
        except Exception:  # noqa: BLE001
            ds = ""
        extra = []
        if self.tools.get("wolfram_alpha"):
            extra.append("`wolfram_alpha` — short factual/computational answers from Wolfram|Alpha for cross-checking.")
        extra.append("`search_skills` / `load_skill` — find and activate a specialised playbook from the skill space.")
        if self.tools.get("lambda_data_agent"):
            extra.append(
                "`lambda_data_agent` — original LAMBDA data-analysis agent (EDA, charts, LaTeX/PDF reports, notebooks, PPT). "
                "Use for dataset work instead of reinventing it in `python`."
            )
        if self.tools.get("web_search"):
            extra.append(
                "`web_search` / `web_fetch` — live web search (open metasearch: ddgs, optional SearXNG/Tavily/Brave) "
                "and page fetch. Only advertised when the user turns Web Search on."
            )
        catalog, active = "", ""
        if route is not None:
            catalog, active = route.as_prompt(getattr(self.settings, "skill_catalog_limit", 16))
        elif len(self.skills):
            catalog = self.skills.catalog_lines()
        return build_system_prompt(files_block, ds, extra, skills_catalog=catalog, skills_active=active)

    def _route_turn(self, req: RunRequest, workspace: Workspace) -> RoutePlan:
        if not len(self.skills):
            return RoutePlan()
        files = []
        try:
            files = workspace.list_files(limit=80)
        except Exception:  # noqa: BLE001
            files = []
        route = self.skill_router.plan(req.text, attachments=req.attachments, files=files)
        forced: list = []
        for name in req.skills or []:
            skill = self.skills.get(name)
            if skill is not None:
                forced.append(skill)
        if not forced:
            return route
        seen = {s.name for s in forced}
        route.activate = forced + [s for s in route.activate if s.name not in seen]
        return route

    async def _activate_skills(
        self,
        route: RoutePlan,
        ctx: ToolContext,
        emit: EmitFn,
        forced: bool = False,
        state: Optional[TurnState] = None,
    ) -> None:
        if not route.activate:
            return
        if not forced and not getattr(self.settings, "skill_auto_activate", True):
            return
        names = []
        for skill in route.activate:
            try:
                materialize_skill(skill, ctx.workspace)
                names.append(skill.name)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to materialise skill %s", skill.name)
        if names:
            ctx.state["skills"] = names
            part = {"type": "skills", "names": names, "auto": not forced}
            if state is not None:
                state.parts.append(part)
            await emit(ev("status", message="Loaded skill" + ("s" if len(names) > 1 else "") + ": " + ", ".join(names)))
            await emit(ev("skills", names=names, auto=not forced))

    async def _force_skills(
        self,
        names: list[str],
        ctx: ToolContext,
        emit: EmitFn,
        state: TurnState,
        route: Optional[RoutePlan],
        history: list,
    ) -> None:
        """Activate slash-invoked skills mid-turn: materialise, inject bodies, refresh system prompt."""
        if not names:
            return
        plan = route or RoutePlan()
        seen = {s.name for s in plan.activate}
        added = []
        for name in names:
            skill = self.skills.get(name)
            if skill is None:
                continue
            if skill.name not in seen:
                plan.activate.append(skill)
                seen.add(skill.name)
            added.append(skill)
        if not added:
            return
        await self._activate_skills(RoutePlan(activate=added), ctx, emit, forced=True, state=state)
        loaded = list(ctx.state.get("skills") or [])
        for skill in added:
            if skill.name not in loaded:
                loaded.append(skill.name)
        ctx.state["skills"] = loaded
        if route is not None:
            route.activate = plan.activate
        if history and history[0].get("role") == "system":
            history[0] = {"role": "system", "content": self._system_prompt(ctx.workspace, route or plan)}
        bodies = []
        for skill in added:
            body = skill.body
            if len(body) > 12_000:
                body = body[:12_000] + "\n\n… [truncated; read skills/" + skill.name + "/SKILL.md]"
            bodies.append(f"### Skill `{skill.name}`\n\n{body}")
        if bodies:
            history.append({
                "role": "user",
                "content": (
                    "The user invoked "
                    + ", ".join(f"`/{s.name}`" for s in added)
                    + ". Follow these skill instructions for the rest of this turn.\n\n"
                    + "\n\n".join(bodies)
                ),
            })

    def _replay(self, messages: list[dict], provider: LLMProvider) -> list[dict]:
        """Rebuild LLM history from stored messages; recent turns keep full tool detail."""
        out: list[dict] = []
        assistant_idx = [i for i, m in enumerate(messages) if m["role"] == "assistant"]
        keep = self.settings.recent_turns_full_replay
        full_from = assistant_idx[-keep] if len(assistant_idx) >= keep else 0
        for i, m in enumerate(messages):
            c = m["content"] or {}
            if m["role"] == "user":
                out.append({"role": "user", "content": _user_content(c.get("text", ""), c.get("attachments") or [])})
            elif m["role"] == "assistant":
                parts = c.get("parts") or []
                if i >= full_from:
                    out.extend(_expand_parts(parts, provider, self.settings.tool_max_output_chars // 2))
                else:
                    out.append({"role": "assistant", "content": _collapse_parts(parts)})
        # Trim very long histories from the front (keep the last turns).
        budget = self.settings.history_char_budget
        while len(out) > 4 and sum(len(json.dumps(m, ensure_ascii=False)) for m in out) > budget:
            out.pop(0)
            while out and out[0]["role"] != "user":
                out.pop(0)
        return out

    # ── core loop ───────────────────────────────────────────────────────

    async def _agent_loop(self, provider, options, history, ctx: ToolContext, state: TurnState, emit: EmitFn,
                          tools: ToolRegistry, max_steps: int, query: str = "", route: Optional[RoutePlan] = None,
                          web_search: bool = False) -> str:
        consecutive_failures = 0
        last_text = ""

        def current_schemas() -> list[dict]:
            search_on = bool(web_search or ctx.state.get("web_search"))
            preferred = list(ctx.state.get("tools") or [])
            out = self.tool_router.schemas(tools, query, route, preferred_tools=preferred)
            if not search_on:
                out = [s for s in out if (s.get("function") or {}).get("name") not in {"web_search", "web_fetch"}]
            return out

        schemas = current_schemas()
        for step in range(max_steps):
            if ctx.cancel.is_set():
                raise Stopped()
            if await self._drain_steer(ctx.conversation_id, history, emit, ctx, state, route):
                schemas = current_schemas()
            last_step = step == max_steps - 1
            if last_step:
                history.append({"role": "user", "content": FINAL_STEP_NUDGE})
            text, reasoning, calls = await self._stream_llm(provider, options, history, None if last_step else schemas, emit, state, ctx.cancel)
            if reasoning.strip():
                state.parts.append({"type": "reasoning", "text": reasoning, "phase": state.phase})
            if text.strip():
                state.parts.append({"type": "text", "text": text, "phase": state.phase})
                last_text = text
            if ctx.cancel.is_set():
                raise Stopped()
            if not calls:
                return text
            history.append(provider.assistant_message(text, reasoning, calls))
            results = await self._execute_calls(calls, tools, ctx, emit, state)
            for call, res in zip(calls, results):
                history.append(provider.tool_message(call.id, call.name, _tool_message_text(res)))
            newly_blocked = _record_failures(calls, results, state, tools, self.tool_router)
            if newly_blocked and not state.repeat_nudge_sent:
                history.append({"role": "user", "content": REPEAT_NUDGE})
                state.repeat_nudge_sent = True
            if await self._drain_steer(ctx.conversation_id, history, emit, ctx, state, route):
                schemas = current_schemas()
            consecutive_failures = _next_consecutive_failures(
                consecutive_failures, calls, results, tools, self.tool_router,
            )
            if consecutive_failures >= self.settings.agent_max_consecutive_failures:
                history.append({"role": "user", "content": FAILURE_NUDGE})
                consecutive_failures = 0
        return last_text

    async def _stream_llm(self, provider, options, history, schemas, emit: EmitFn, state: TurnState, cancel: asyncio.Event):
        """One model call. Returns (text, reasoning, tool_calls)."""
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        pending: dict[int, dict] = {}
        state.llm_calls += 1
        attempts = 0
        while True:
            attempts += 1
            try:
                async for delta in provider.stream(history, schemas, options):
                    if cancel.is_set():
                        break
                    if delta.kind == "text":
                        text_parts.append(delta.text)
                        await emit(ev("text_delta", delta=delta.text))
                    elif delta.kind == "reasoning":
                        reasoning_parts.append(delta.text)
                        await emit(ev("reasoning_delta", delta=delta.text))
                    elif delta.kind == "tool_call" and delta.tool_call is not None:
                        tc = delta.tool_call
                        slot = pending.get(tc.index)
                        if slot is None:
                            slot = {"id": tc.id or f"call_{state.llm_calls}_{tc.index}", "name": tc.name or "", "arguments": ""}
                            pending[tc.index] = slot
                            await emit(ev("tool_call_start", id=slot["id"], name=slot["name"]))
                        else:
                            if tc.id and not slot["id"].startswith("call_") and tc.id != slot["id"]:
                                slot["id"] = tc.id
                            if tc.name and not slot["name"]:
                                slot["name"] = tc.name
                                await emit(ev("tool_call_start", id=slot["id"], name=slot["name"]))
                        if tc.arguments:
                            slot["arguments"] += tc.arguments
                            await emit(ev("tool_call_delta", id=slot["id"], delta=tc.arguments))
                    elif delta.kind == "usage" and delta.usage:
                        for k, v in delta.usage.items():
                            if isinstance(v, (int, float)):
                                state.usage[k] = state.usage.get(k, 0) + v
                        await emit(ev("usage", **delta.usage))
                break
            except Exception as exc:  # noqa: BLE001
                if attempts < 2 and _is_transient(exc) and not text_parts and not pending:
                    logger.warning("Transient LLM error, retrying: %s", exc)
                    await emit(ev("status", message="Model connection hiccup, retrying…"))
                    await asyncio.sleep(1.5)
                    continue
                raise
        calls = [ToolCallSpec(id=s["id"], name=s["name"], arguments=s["arguments"]) for _, s in sorted(pending.items())]
        return "".join(text_parts), "".join(reasoning_parts), calls

    async def _execute_calls(self, calls: list[ToolCallSpec], tools: ToolRegistry, ctx: ToolContext, emit: EmitFn, state: TurnState) -> list[ToolResult]:
        parsed: list[tuple[ToolCallSpec, Optional[dict], Optional[str]]] = []
        for call in calls:
            args, err = _parse_arguments(call.arguments)
            parsed.append((call, args, err))
            await emit(ev("tool_call_ready", id=call.id, name=call.name, arguments=args if args is not None else {"_raw": call.arguments}))

        async def run_one(call: ToolCallSpec, args: Optional[dict], err: Optional[str]) -> ToolResult:
            started = time.monotonic()
            tool, resolve_err = self.tool_router.resolve(tools, call.name)
            resolved_name = tool.name if tool is not None else call.name
            if resolved_name in state.blocked_tools:
                res = ToolResult(
                    ok=False,
                    output="",
                    error=(
                        f"Blocked: `{resolved_name}` failed the same way "
                        f"{REPEAT_LIMIT} times this turn. Do not retry it."
                    ),
                    meta={"blocked_repeat": True},
                )
                res.meta.setdefault("duration_ms", int((time.monotonic() - started) * 1000))
                return res
            if err is not None:
                res = ToolResult(ok=False, output="", error=f"Could not parse tool arguments as JSON: {err}")
            elif tool is None:
                res = ToolResult(ok=False, output="", error=resolve_err or f"Unknown tool '{call.name}'. Available: {', '.join(tools.names())}.")
            elif ctx.cancel.is_set():
                res = ToolResult(ok=False, output="", error="Skipped: the run was stopped.")
            else:
                if getattr(self.settings, "align_monitor", True):
                    verdict = inspect_tool_call(tool.name, args or {})
                    if verdict.level != "ok":
                        await emit(ev("monitor", level=verdict.level, summary=verdict.summary, tool=tool.name))
                        state.parts.append({"type": "monitor", "level": verdict.level, "summary": verdict.summary, "tool": tool.name})
                    if verdict.level == "block":
                        res = ToolResult(ok=False, output="", error=verdict.summary, meta={"monitor": "block"})
                        res.meta.setdefault("duration_ms", int((time.monotonic() - started) * 1000))
                        return res
                call_ctx = dataclasses.replace(ctx, call_id=call.id)
                res = await tool(call_ctx, args or {})
            res.meta.setdefault("duration_ms", int((time.monotonic() - started) * 1000))
            return res

        def _resolved(name: str):
            return self.tool_router.resolve(tools, name)[0]

        writes = [c for c, _, _ in parsed if (_resolved(c.name) is None or not _resolved(c.name).read_only)]
        parallel = len(parsed) > 1 and len(writes) <= 1
        if parallel:
            results = await asyncio.gather(*(run_one(c, a, e) for c, a, e in parsed))
        else:
            results = []
            for c, a, e in parsed:
                results.append(await run_one(c, a, e))

        for (call, args, _), res in zip(parsed, results):
            state.tool_calls += 1
            part = {
                "type": "tool_call", "id": call.id, "name": call.name, "arguments": args if args is not None else {"_raw": call.arguments},
                "result": res.to_dict(), "status": "completed" if res.ok else "error", "phase": state.phase,
            }
            state.parts.append(part)
            await emit(ev("tool_result", id=call.id, name=call.name, ok=res.ok, output=res.output, error=res.error,
                          artifacts=[a.to_dict() for a in res.artifacts], meta=res.meta))
        if ctx.cancel.is_set():
            raise Stopped()
        return list(results)

    # ── verification ────────────────────────────────────────────────────

    async def _verify_and_revise(self, provider, options, history, ctx: ToolContext, state: TurnState, emit: EmitFn,
                                 problem: str, solution: str) -> str:
        verifier_tools = ToolRegistry([t for t in self.tools if t.name in ("python", "check_equivalence", "read_file", "list_files")])
        current = solution
        for round_no in range(1, self.settings.verify_rounds + 1):
            if ctx.cancel.is_set():
                raise Stopped()
            state.phase = "verify"
            state.parts.append({"type": "phase", "phase": "verify", "round": round_no})
            await emit(ev("phase", phase="verify", round=round_no))
            v_options = dataclasses.replace(options, reasoning=self.settings.verify_reasoning or options.reasoning)
            v_history = [
                {"role": "system", "content": VERIFIER_PROMPT},
                {"role": "user", "content": VERIFIER_USER_TEMPLATE.format(problem=problem, solution=current)},
            ]
            report_text = await self._agent_loop(provider, v_options, v_history, ctx, state, emit, tools=verifier_tools, max_steps=self.settings.verify_max_steps)
            verdict = _extract_verdict(report_text)
            report = _extract_tag(report_text, "report") or report_text.strip()
            state.parts.append({"type": "verification", "verdict": verdict, "report": report, "round": round_no})
            await emit(ev("verification", verdict=verdict, report=report, round=round_no))
            if verdict != "incorrect":
                break
            state.phase = "revise"
            state.parts.append({"type": "phase", "phase": "revise", "round": round_no})
            await emit(ev("phase", phase="revise", round=round_no))
            history.append({"role": "user", "content": REVISION_TEMPLATE.format(report=report)})
            current = await self._agent_loop(provider, options, history, ctx, state, emit, tools=self.tools, max_steps=self.settings.agent_max_steps // 2)
        state.phase = "answer"
        return current

    # ── side tasks ──────────────────────────────────────────────────────

    async def _auto_title(self, cid: str, provider, options: ChatOptions, text: str) -> None:
        try:
            opts = dataclasses.replace(options, reasoning="off", max_tokens=40, temperature=None)
            title = await provider.complete_text(
                [{"role": "user", "content": TITLE_PROMPT.format(message=text[:2000])}], opts
            )
            title = title.strip().strip('"').strip("'").splitlines()[0][:80] if title else ""
            if title:
                self.db.update_conversation(cid, title=title)
        except Exception:  # noqa: BLE001
            logger.debug("auto title failed", exc_info=True)


# ── helpers ─────────────────────────────────────────────────────────────

def _heuristic_title(text: str) -> str:
    cleaned = re.sub(r"\$[^$]*\$", "math", text or "").replace("\n", " ").strip()
    cleaned = re.sub(r"^(?:/[a-z0-9]+(?:[-_][a-z0-9]+)*\s*)+", "", cleaned, flags=re.I).strip()
    return (cleaned[:60] + "…") if len(cleaned) > 60 else (cleaned or "New chat")


def _user_content(text: str, attachments: list[dict]) -> str:
    if not attachments:
        return text
    names = ", ".join(f"`{a.get('path') or a.get('name')}`" for a in attachments)
    return f"{text}\n\n[Attached files in the workspace: {names}]"


def _parse_arguments(raw: str) -> tuple[Optional[dict], Optional[str]]:
    raw = (raw or "").strip()
    if not raw:
        return {}, None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        # tolerate trailing junk after a complete object
        try:
            obj, _ = json.JSONDecoder().raw_decode(raw)
        except Exception:  # noqa: BLE001
            return None, str(exc)
    if not isinstance(obj, dict):
        return None, "arguments must be a JSON object"
    return obj, None


def _failure_fingerprint(name: str, res: ToolResult) -> str:
    error = (res.error or "").strip() or (res.output or "").strip()
    first_line = error.splitlines()[0].strip() if error else "unknown"
    error_name = str((res.meta or {}).get("error_name") or "")
    if not error_name and first_line:
        error_name = first_line.split(":", 1)[0].strip()
    return f"{name}|{error_name}|{first_line[:240]}"


def _resolved_name(call: ToolCallSpec, tools: ToolRegistry, router: ToolRouter) -> str:
    tool, _ = router.resolve(tools, call.name)
    return tool.name if tool is not None else call.name


def _record_failures(
    calls: list[ToolCallSpec],
    results: list[ToolResult],
    state: TurnState,
    tools: ToolRegistry,
    router: ToolRouter,
) -> list[str]:
    newly: list[str] = []
    for call, res in zip(calls, results):
        if res.ok or (res.meta or {}).get("blocked_repeat"):
            continue
        name = _resolved_name(call, tools, router)
        fp = _failure_fingerprint(name, res)
        state.failure_counts[fp] = state.failure_counts.get(fp, 0) + 1
        if state.failure_counts[fp] >= REPEAT_LIMIT and name not in state.blocked_tools:
            state.blocked_tools.add(name)
            newly.append(name)
    return newly


def _next_consecutive_failures(
    current: int,
    calls: list[ToolCallSpec],
    results: list[ToolResult],
    tools: ToolRegistry,
    router: ToolRouter,
) -> int:
    """Count streaks of hard-tool failures; list/read/bash successes do not reset."""
    hard: list[ToolResult] = []
    for call, res in zip(calls, results):
        if _resolved_name(call, tools, router) in SOFT_SUCCESS_TOOLS:
            continue
        hard.append(res)
    if not hard:
        return current
    if all(not r.ok for r in hard):
        return current + 1
    if any(r.ok for r in hard):
        return 0
    return current


def _tool_message_text(res: ToolResult) -> str:
    text = res.output or ""
    if not res.ok and res.error and res.error not in text:
        text = f"{text}\n[error] {res.error}".strip()
    return text or "(no output)"


def _expand_parts(parts: list[dict], provider: LLMProvider, tool_char_limit: int) -> list[dict]:
    """Turn stored assistant parts into assistant/tool messages (verification phases are folded into text)."""
    out: list[dict] = []
    text_buf: list[str] = []
    reasoning_buf: list[str] = []
    batch: list[dict] = []

    def flush_batch() -> None:
        nonlocal text_buf, reasoning_buf, batch
        if not batch:
            return
        calls = [ToolCallSpec(id=p["id"], name=p["name"], arguments=json.dumps(p.get("arguments") or {}, ensure_ascii=False)) for p in batch]
        out.append(provider.assistant_message("".join(text_buf), "".join(reasoning_buf), calls))
        for p in batch:
            res = p.get("result") or {}
            content = res.get("output") or ""
            if not res.get("ok", False) and res.get("error"):
                content = f"{content}\n[error] {res['error']}".strip()
            if not content:
                content = "(no output)" if res else "[no result: execution was stopped]"
            out.append(provider.tool_message(p["id"], p["name"], truncate_text(content, tool_char_limit)))
        text_buf, reasoning_buf, batch = [], [], []

    for p in parts:
        t = p.get("type")
        if t == "text":
            if batch:
                flush_batch()
            text_buf.append(p.get("text", ""))
        elif t == "reasoning":
            if batch:
                flush_batch()
            reasoning_buf.append(p.get("text", ""))
        elif t == "tool_call":
            batch.append(p)
        elif t == "verification":
            if batch:
                flush_batch()
            text_buf.append(f"\n\n[Verifier ({p.get('verdict')}): {truncate_text(p.get('report', ''), 1500)}]")
    flush_batch()
    final = "".join(text_buf).strip()
    if final or not out:
        out.append({"role": "assistant", "content": final or "(no answer was produced)"})
    return out


def _collapse_parts(parts: list[dict]) -> str:
    texts = [p.get("text", "") for p in parts if p.get("type") == "text" and p.get("phase", "answer") in ("answer", "revise")]
    tools_used = [p.get("name", "?") for p in parts if p.get("type") == "tool_call"]
    body = "\n\n".join(t for t in texts if t.strip()).strip() or "(no answer)"
    if tools_used:
        counts: dict[str, int] = {}
        for n in tools_used:
            counts[n] = counts.get(n, 0) + 1
        body += "\n\n[Tools used in this turn: " + ", ".join(f"{k}×{v}" for k, v in counts.items()) + "]"
    return body


def _extract_tag(text: str, tag: str) -> Optional[str]:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text or "", re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else None


def _extract_verdict(text: str) -> str:
    v = (_extract_tag(text, "verdict") or "").strip().lower()
    if v in ("correct", "incorrect", "uncertain"):
        return v
    m = re.search(r"\\boxed\{\s*([01])\s*\}", text or "")
    if m:
        return "correct" if m.group(1) == "1" else "incorrect"
    return "uncertain"


def _is_transient(exc: Exception) -> bool:
    name = type(exc).__name__
    msg = str(exc).lower()
    return name in ("APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError") or "timeout" in msg or "connection" in msg


def _friendly_error(exc: Exception) -> str:
    msg = str(exc) or type(exc).__name__
    if "unsupported_country" in msg:
        return "This provider is blocked from your region. Switch provider or set a custom base URL in backend/.env."
    if "api key" in msg.lower() or "401" in msg:
        return f"Authentication failed with the model provider: {msg[:300]}"
    return msg[:600]
