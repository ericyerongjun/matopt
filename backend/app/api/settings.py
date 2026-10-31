"""Settings, feature flags, approvals and per-conversation job/agent listings.

``GET /api/settings`` is the read model the frontend Settings dialog renders:
feature flags (``app/features.py``), the operator's security posture, the
instruction sources, MCP server status and the config layer. Only feature
flags are writable here, into the user TOML layer (Codex's
``experimentalFeature/enablement/set``); security settings are reported but
never written, because the agent's own kernel can reach this API.
"""

from __future__ import annotations

import os
import re
import tempfile
import tomllib
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.config import settings, user_config_path
from app.features import BY_KEY, feature_states
from app.runtime import get_runtime

router = APIRouter(prefix="/api", tags=["settings"])


def require_operator_writes(request: Request) -> None:
    """Refuse configuration writes that a sandboxed agent could use to escape.

    With an active sandbox that still allows network, the agent's kernel can
    reach this API on localhost; installing a plugin or an MCP server from
    there would hand it unsandboxed code at the next start. Such deployments
    set ``SETTINGS_API_WRITABLE=false`` and configure through files instead.
    Reads are always allowed.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if not settings.settings_api_writable:
        raise HTTPException(status_code=403, detail="Configuration changes through the API are disabled "
                            "(SETTINGS_API_WRITABLE=false). Edit the config files instead.")


class FeatureToggle(BaseModel):
    enabled: bool


class ApprovalDecision(BaseModel):
    decision: str


def _origins() -> dict[str, str]:
    from app.config import load_user_layers
    base, profile = load_user_layers()
    origins = {key: "config" for key in base}
    origins.update({key: "profile" for key in profile})
    for name in settings.model_fields:
        if name.upper() in os.environ:
            origins[name] = "env"
    return origins


def _sandbox() -> dict:
    from app.agent.sandbox import SEATBELT, select_backend
    import shutil
    backend = select_backend(settings.sandbox_backend)
    available = (os.path.exists(SEATBELT) if backend == "seatbelt"
                 else shutil.which("bwrap") is not None if backend == "bwrap" else False)
    return {"mode": settings.sandbox_mode, "network": settings.sandbox_network, "backend": backend,
            "available": available}


def _exec_rules() -> dict:
    rt = get_runtime()
    shell = rt.tools.get("bash")
    policy = getattr(shell, "exec_policy", None)
    return {"path": str(settings.exec_rules_path) if settings.exec_rules_path else None,
            "rules": len(policy.rules) if policy is not None else 0}


def _instructions() -> dict:
    from app.agent.instructions import FILENAMES, operator_instructions
    sources = []
    for directory in dict.fromkeys([Path.home() / ".matopt", Path(settings.data_dir)]):
        for name in FILENAMES:
            if (directory / name).is_file():
                sources.append(str(directory / name))
                break
    return {"operator": {"chars": len(operator_instructions(settings)), "sources": sources}}


def _mcp() -> dict:
    hub = get_runtime().mcp
    status = getattr(hub, "status", None)
    if callable(status):
        return {"servers": status()}
    servers: dict[str, int] = {}
    for tool in hub.tools():
        servers[tool.mcp_server] = servers.get(tool.mcp_server, 0) + 1
    return {"servers": [{"name": name, "state": "connected", "tools": count, "resources": False}
                        for name, count in sorted(servers.items())]}


@router.get("/settings")
async def read_settings():
    rt = get_runtime()
    path = user_config_path()
    return {
        "features": feature_states(settings, _origins()),
        "security": {
            "sandbox": _sandbox(),
            "hooks": {"enabled": settings.hooks_enabled,
                      "path": str(settings.hooks_path or Path(settings.data_dir) / "hooks.json"),
                      "events": sorted(rt.harness.hooks.config)},
            "exec_rules": _exec_rules(),
            "approvals_reviewer": settings.approvals_reviewer,
            "redact_tool_output": settings.redact_tool_output,
        },
        "instructions": _instructions(),
        "mcp": _mcp(),
        "config": {"path": str(path), "exists": path.is_file(), "profile": os.environ.get("MATOPT_PROFILE")},
    }


def write_feature(path: Path, key: str, enabled: bool) -> None:
    """Set ``[features].<key>`` in the user TOML, keeping every other line as it was.

    A full TOML writer would reformat the user's file; this edits the one
    line (or adds the table) and leaves comments and layout alone.
    """
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    tomllib.loads(text)  # refuse to edit a file that does not parse
    value = "true" if enabled else "false"
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip() == "[features]"), None)
    if start is None:
        lines += ([""] if lines and lines[-1].strip() else []) + ["[features]", f"{key} = {value}"]
    else:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
        hit = next((i for i in range(start + 1, end) if pattern.match(lines[i])), None)
        if hit is None:
            lines.insert(end if end == len(lines) else end, f"{key} = {value}")
        else:
            lines[hit] = f"{key} = {value}"
    out = "\n".join(lines) + "\n"
    tomllib.loads(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config-")
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(out)
    os.replace(tmp, path)


@router.put("/features/{key}")
async def set_feature(key: str, body: FeatureToggle, request: Request):
    require_operator_writes(request)
    spec = BY_KEY.get(key)
    if spec is None or spec.stage in ("under_development", "removed"):
        raise HTTPException(status_code=404, detail="Unknown feature")
    if spec.setting.upper() in os.environ:
        raise HTTPException(status_code=409, detail=f"{spec.setting.upper()} is set in the environment, which "
                            "takes precedence over the config file.")
    try:
        write_feature(user_config_path(), key, body.enabled)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Could not update the config file: {exc}") from exc
    if spec.live:
        setattr(settings, spec.setting, body.enabled)
    return next(f for f in feature_states(settings, _origins()) if f["key"] == key) | {
        "restart_required": not spec.live}


@router.post("/runs/{run_id}/approvals/{approval_id}")
async def resolve_approval(run_id: str, approval_id: str, body: ApprovalDecision):
    rt = get_runtime()
    run = rt.db.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not rt.harness.approvals.resolve(run["conversation_id"], approval_id, body.decision):
        raise HTTPException(status_code=409, detail="This approval is no longer pending.")
    return {"id": approval_id, "decision": body.decision}


@router.get("/conversations/{conversation_id}/jobs")
async def conversation_jobs(conversation_id: str):
    import time
    shell = get_runtime().tools.get("bash")
    store = getattr(shell, "sessions", None)
    jobs = []
    now = time.monotonic()
    for session in (store._sessions.values() if store is not None else []):
        if session.conversation_id != conversation_id:
            continue
        jobs.append({"session_id": session.id, "command": session.command[:500], "running": session.running,
                     "exit_code": session.proc.returncode,
                     "started_at": time.time() - (now - session.started),
                     "duration_ms": int((now - session.started) * 1000)})
    return {"jobs": sorted(jobs, key=lambda j: j["started_at"], reverse=True)}


@router.get("/conversations/{conversation_id}/agents")
async def conversation_agents(conversation_id: str):
    rows = get_runtime().db.list_subagents(conversation_id)
    return {"agents": [{k: row[k] for k in ("id", "parent_id", "path", "nickname", "role", "status", "task",
                                              "summary", "created_at", "updated_at")} for row in rows]}
