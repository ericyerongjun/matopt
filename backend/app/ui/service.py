"""Server-owned UI actions share the agent's conversation and kernel lease."""
from __future__ import annotations

import asyncio
import ast
import json
import logging
from dataclasses import dataclass

from jsonschema import Draft202012Validator, ValidationError as SchemaValidationError

from app.agent.events import Event, ev
from app.agent.tools.base import ToolContext
from app.agent.tools.python_tool import PythonTool
from app.agent.workspace import Workspace
from app.storage.db import new_id
from .contracts import UIAction, UISurface, validate_state, walk_nodes
from .store import UIConflict, UINotFound, UIStore

logger = logging.getLogger(__name__)


def validate_input_schema(schema: dict) -> dict:
    """A local, bounded JSON Schema; references can never fetch network data."""
    raw = json.dumps(schema, allow_nan=False)
    if len(raw.encode()) > 16000:
        raise ValueError("Input schema exceeds 16 KiB")
    def check(value, depth=0):
        if depth > 10:
            raise ValueError("Input schema is too deeply nested")
        if isinstance(value, dict):
            if any(k in value for k in ("$ref", "$dynamicRef", "$id")):
                raise ValueError("Input schemas must be self-contained without references")
            for child in value.values():
                check(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                check(child, depth + 1)
    check(schema)
    if schema.get("type") != "object":
        raise ValueError("Action input schema must describe an object")
    schema = {**schema, "additionalProperties": False}
    Draft202012Validator.check_schema(schema)
    return schema


def validate_inputs(schema: dict, values: dict):
    if len(json.dumps(values, allow_nan=False).encode()) > 65536:
        raise ValueError("Action inputs exceed 64 KiB")
    try:
        Draft202012Validator(schema).validate(values)
    except SchemaValidationError as exc:
        raise ValueError("Invalid action inputs: " + exc.message) from exc


@dataclass
class ActionLease:
    cid: str
    sid: str
    request: dict
    surface: dict
    manifest: dict
    definition: dict
    cancel: asyncio.Event


class UIService:
    def __init__(self, harness):
        self.harness = harness
        self.store = UIStore(harness.db)
        self.settings = harness.settings
        self.python = PythonTool(getattr(self.settings, "tool_timeout", 120), getattr(self.settings, "tool_max_timeout", 900), 16000)

    def enabled(self, kind: str) -> bool:
        return bool(getattr(self.settings, "ui_native_enabled" if kind == "native" else "ui_mini_apps_enabled", True))

    def register(self, ctx, surface_id, action_id, label, input_schema, code, description=None, timeout=60, input_files=None, seed=0):
        if len(code.encode()) > 65536:
            raise ValueError("Computation handler exceeds 64 KiB")
        tree = ast.parse(code)
        if not any(isinstance(node, ast.FunctionDef) and node.name == "compute" for node in tree.body):
            raise ValueError("Handler must define compute(values), returning {nodes?, state?, fallback?}")
        schema = validate_input_schema(input_schema)
        paths = []
        for path in input_files or []:
            resolved = ctx.workspace.resolve(path, must_exist=True)
            if not resolved.is_file():
                raise ValueError("Computation inputs must be files")
            paths.append(ctx.workspace.relpath(resolved))
        manifest = UIAction(id=action_id, label=label, description=description, input_schema=schema).model_dump(mode="json", exclude_none=True)
        # Surface id validation before storing an unattached action.
        UISurface(id=surface_id, title="Pending computation", fallback="Pending computation")
        return self.store.register_action(ctx.conversation_id, surface_id, manifest, {
            "code": code, "input_files": paths, "seed": seed,
            "timeout": max(1, min(int(timeout), int(getattr(self.settings, "tool_max_timeout", 900)))),
        })

    def commit(self, ctx, surface: dict) -> dict:
        data = UISurface.model_validate(surface).model_dump(mode="json", exclude_none=True)
        if not self.enabled(data["kind"]):
            raise ValueError("This interactive UI kind is disabled")
        declared = {a["id"]: a for a in data["actions"]}
        for node in walk_nodes(data["nodes"]):
            if node["type"] == "button":
                action_id = node["props"]["actionId"]
                if action_id not in declared:
                    saved = self.store.action(ctx.conversation_id, data["id"], action_id)
                    if saved is None:
                        raise ValueError(f"Register action {action_id} before rendering its button")
                    declared[action_id] = saved[0]
            elif node["type"] in {"slider", "number", "select", "toggle"}:
                props = node["props"]
                key = props["stateKey"]
                if key not in data["state"]:
                    if "value" not in props:
                        raise ValueError(f"Control {key} requires an initial value in state or props.value")
                    data["state"][key] = props["value"]
        # The tool/model cannot forge an action definition or choose old code.
        resolved = []
        for action_id in declared:
            saved = self.store.action(ctx.conversation_id, data["id"], action_id)
            if saved is None:
                raise ValueError(f"Unregistered action: {action_id}")
            resolved.append(saved[0])
        data["actions"] = resolved
        data["state"] = validate_state(data, data["state"])
        data["computed_state"] = dict(data["state"])
        return self.store.commit(ctx.conversation_id, data, message_id=ctx.state.get("ui_message_id"))

    def prepare_action(self, cid: str, sid: str, request: dict) -> ActionLease | dict:
        receipt = self.store.receipt(cid, request["idempotency_key"], sid, request)
        if receipt is not None:
            return receipt
        surface = self.store.get(cid, sid)
        if surface is None:
            raise UINotFound("UI surface not found")
        if not self.enabled(surface["kind"]):
            raise UIConflict("Interactive computations are disabled")
        if surface["revision"] != request["revision"]:
            raise UIConflict("This UI changed. Refresh it before trying again.")
        manifest = next((a for a in surface["actions"] if a["id"] == request["action_id"]), None)
        if manifest is None:
            raise UINotFound("Action is not declared on this UI surface")
        handler = self.store.action(cid, sid, manifest["id"], manifest["handler_version"])
        if handler is None:
            raise UINotFound("The computation handler is unavailable")
        validate_inputs(manifest["input_schema"], request["values"])
        cancel = self.harness.claim_external_run(cid)
        try:
            self.store.reserve_receipt(cid, sid, request["idempotency_key"], request)
        except BaseException:
            self.harness.release_external_run(cid, cancel)
            raise
        return ActionLease(cid, sid, request, surface, manifest, handler[1], cancel)

    async def _ledger(self, lease: ActionLease, kind: str, **data):
        if not getattr(self.settings, "session_log_enabled", True):
            return
        log = await self.harness._open_session(lease.cid)
        log.append(kind, {"surface_id": lease.sid, "action_id": lease.manifest["id"], **data}, ignorable=True)
        await log.flush()

    async def actions(self, lease: ActionLease):
        """Yield computation events; cancellation always closes the durable receipt."""
        events = []
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        closing = asyncio.Event()
        async def emit(event):
            if not closing.is_set():
                await queue.put(event)
        async def work():
            error = None
            committed = False
            try:
                await self._ledger(lease, "ui/action-start", revision=lease.request["revision"],
                                   idempotency_key=lease.request["idempotency_key"], values=lease.request["values"],
                                   handler_version=lease.manifest["handler_version"])
                await emit(ev("status", message="Computing " + lease.manifest["label"] + "…"))
                ws = Workspace(lease.cid, self.settings.workspace_dir)
                for path in lease.definition["input_files"]:
                    try:
                        ws.resolve(path, must_exist=True)
                    except ValueError as exc:
                        raise ValueError(f"Required input is missing: {path}. Upload it again before recomputing.") from exc
                dest = ws.resolve(".matopt/ui/" + new_id("result_") + ".json")
                dest.parent.mkdir(parents=True, exist_ok=True)
                # Definitions are persisted server-side and reloaded on every call,
                # so a kernel restart cannot leave a stale or missing function.
                wrapper = (
                    "import json as _matopt_ui_json, random as _matopt_ui_random\n"
                    "import numpy as _matopt_ui_np\n"
                    f"_matopt_ui_random.seed({lease.definition['seed']!r})\n"
                    f"_matopt_ui_np.random.seed({lease.definition['seed']!r})\n"
                    "_matopt_ui_scope = {}\n"
                    f"exec({lease.definition['code']!r}, _matopt_ui_scope)\n"
                    f"_matopt_ui_result = _matopt_ui_scope['compute'](_matopt_ui_json.loads({json.dumps(lease.request['values'], allow_nan=False)!r}))\n"
                    "from pathlib import Path as _MatoptUIPath\n"
                    f"_MatoptUIPath({str(dest)!r}).write_text(_matopt_ui_json.dumps(_matopt_ui_result, allow_nan=False), encoding='utf-8')\n"
                )
                ctx = ToolContext(lease.cid, ws, emit, lease.cancel, call_id=lease.request["idempotency_key"], kernels=self.harness.kernels, settings=self.settings)
                result = await self.python.run(ctx, code=wrapper, timeout=lease.definition["timeout"])
                if lease.cancel.is_set():
                    raise ValueError("Computation stopped. The previous result is still available.")
                if not result.ok:
                    raise ValueError(result.error or "Computation failed")
                if not dest.exists():
                    raise ValueError("Computation did not return a result")
                try:
                    if dest.stat().st_size > 1048576:
                        raise ValueError("Computation result exceeds 1 MiB")
                    change = json.loads(dest.read_text())
                finally:
                    dest.unlink(missing_ok=True)
                if not isinstance(change, dict) or set(change) - {"nodes", "state", "fallback", "title"}:
                    raise ValueError("compute(values) must return only nodes, state, fallback, and/or title")
                candidate = {**lease.surface, **change}
                parameter_state = {k: v for k, v in lease.request["values"].items() if k in lease.surface["state"]}
                candidate["state"] = validate_state(candidate, {**lease.surface["state"], **parameter_state, **change.get("state", {})})
                candidate["computed_state"] = dict(candidate["state"])
                # Revalidate IDs/props/actions and reject late results by revision.
                surface = UISurface.model_validate(candidate).model_dump(mode="json", exclude_none=True)
                for node in walk_nodes(surface["nodes"]):
                    if node["type"] == "button" and node["props"]["actionId"] not in {a["id"] for a in surface["actions"]}:
                        raise ValueError("Computation referenced an undeclared action")
                surface, terminal = self.store.complete_action(lease.cid, surface, lease.request["revision"], lease.request["idempotency_key"])
                committed = True
                events.extend(terminal)
                try:
                    await self._ledger(lease, "ui/action-end", revision=surface["revision"], status="complete", result=surface["fallback"][:1500])
                except Exception:
                    logger.exception("UI result committed, but its optional ledger reference failed")
                for item in terminal:
                    await emit(Event(item["type"], {k: v for k, v in item.items() if k != "type"}))
            except asyncio.CancelledError:
                error = "Computation interrupted. The previous result is still available."
                lease.cancel.set()
                session = self.harness.kernels.peek(lease.cid)
                if session is not None:
                    await session.interrupt()
            except Exception as exc:  # noqa: BLE001
                error = str(exc)[:2000]
            finally:
                if error and not committed:
                    event = ev("ui_error", surface_id=lease.sid, revision=lease.request["revision"], error=error, fallback=lease.surface["fallback"])
                    events.append(event.to_dict())
                    self.store.finish_receipt(lease.cid, lease.request["idempotency_key"], events, "error")
                    try:
                        await self._ledger(lease, "ui/action-end", revision=lease.request["revision"], status="error", error=error)
                    except Exception:
                        logger.exception("Unable to checkpoint UI action failure")
                    await emit(event)
                self.harness.release_external_run(lease.cid, lease.cancel)
                if not closing.is_set():
                    await queue.put(None)
        task = asyncio.create_task(work())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    if task.done():
                        break
                    yield ev("keepalive")
                    continue
                if event is None:
                    break
                yield event
        finally:
            closing.set()
            while not queue.empty():
                queue.get_nowait()
            if not task.done():
                task.cancel()
            cancelled = False
            try:
                # Finish receipt/ledger cleanup even if response teardown sends
                # another cancellation while the first is being processed.
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        cancelled = True
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass
            finally:
                self.harness.release_external_run(lease.cid, lease.cancel)
            if cancelled:
                raise asyncio.CancelledError
