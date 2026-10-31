"""
Application settings (pydantic-settings). Values come from the environment
or ``backend/.env``; see ``.env.example`` for the full list.

Below those sits a user TOML layer (Codex's ``config.toml``, from
``codex-rs/config``): ``~/.matopt/config.toml`` or ``$MATOPT_CONFIG``, with an
optional ``[profiles.<name>]`` table selected by ``$MATOPT_PROFILE`` and a
``[features]`` table of feature keys (``app/features.py``). Precedence, highest
first: constructor arguments, environment, ``.env``, profile, TOML, defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import os
import tomllib

from pydantic import Field, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from app.llm.catalog import LEVELS, MAX_OUTPUT_TOKENS, MODEL_ID, PROVIDER_ID, canonical_model, canonical_provider, canonical_reasoning

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
SKILL_GROUPS = ("matopt", "data", "anthropic")


def default_skill_group_dirs(*roots: Path) -> list[Path]:
    """Skill group folders under a checkout root and/or the backend tree.

    Locally ``REPO_ROOT/skills`` is the catalog. The Docker image copies
    ``backend/`` to ``/app`` and ``skills/`` to ``/app/skills``, so the
    groups live next to the backend package, not under filesystem root.
    """
    seen: set[Path] = set()
    dirs: list[Path] = []
    for root in roots:
        for group in SKILL_GROUPS:
            path = root / "skills" / group
            if path in seen:
                continue
            seen.add(path)
            dirs.append(path)
    return dirs


def user_config_path() -> Path:
    return Path(os.environ.get("MATOPT_CONFIG") or Path.home() / ".matopt" / "config.toml").expanduser()


def load_user_layers(path: Path | None = None) -> tuple[dict, dict]:
    """(base table, selected profile table) of the user TOML, both flattened to field names."""
    path = path or user_config_path()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, IsADirectoryError):
        return {}, {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        import logging
        logging.getLogger(__name__).error("Ignoring invalid %s: %s", path, exc)
        return {}, {}

    def flatten(table: dict) -> dict:
        from app.features import BY_KEY
        out = {k: v for k, v in table.items() if not isinstance(v, dict)}
        for key, enabled in (table.get("features") or {}).items():
            spec = BY_KEY.get(key)
            if spec is not None and isinstance(enabled, bool):
                out[spec.setting] = enabled
        return out

    profile_name = os.environ.get("MATOPT_PROFILE")
    profile = (data.get("profiles") or {}).get(profile_name, {}) if profile_name else {}
    return flatten(data), flatten(profile if isinstance(profile, dict) else {})


class _TomlLayer(PydanticBaseSettingsSource):
    def __init__(self, settings_cls, values: dict):
        super().__init__(settings_cls)
        self.values = {k: v for k, v in values.items() if k in settings_cls.model_fields}

    def get_field_value(self, field: FieldInfo, field_name: str):
        return self.values.get(field_name), field_name, False

    def __call__(self) -> dict:
        return dict(self.values)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BACKEND_DIR / ".env"), env_file_encoding="utf-8", extra="ignore")

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings,
                                   file_secret_settings):
        base, profile = load_user_layers()
        return (init_settings, env_settings, dotenv_settings, _TomlLayer(settings_cls, profile),
                _TomlLayer(settings_cls, base), file_secret_settings)

    # ── App ──────────────────────────────────────────────────────────────
    app_name: str = "MatOpt"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"]
    data_dir: Path = BACKEND_DIR / "data"
    max_upload_size_mb: int = 200
    ui_native_enabled: bool = True
    ui_mini_apps_enabled: bool = True
    ui_sandbox_origin: Optional[str] = None  # defaults to this host on port 3001

    @field_validator("ui_sandbox_origin")
    @classmethod
    def validate_sandbox_origin(cls, value):
        if value is None:
            return value
        from urllib.parse import urlsplit
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("UI_SANDBOX_ORIGIN must be a single http(s) origin")
        return value.rstrip("/")

    # ── Default model selection ─────────────────────────────────────────
    default_provider: str = PROVIDER_ID
    default_model: str = MODEL_ID
    default_reasoning: str = "high"

    @field_validator("default_provider", mode="before")
    @classmethod
    def only_deepseek_provider(cls, value):
        return canonical_provider(value)

    @field_validator("default_model", mode="before")
    @classmethod
    def only_flash_model(cls, value):
        return canonical_model(value)

    @field_validator("default_reasoning", "verify_reasoning", mode="before")
    @classmethod
    def normalize_reasoning(cls, value):
        if value is None:
            return value
        normalized = canonical_reasoning(value)
        if normalized not in LEVELS:
            raise ValueError("Reasoning level must be off, low, high or max.")
        return normalized

    # ── Providers (OpenAI-compatible) ───────────────────────────────────
    deepseek_api_key: Optional[str] = None
    deepseek_base_url: Optional[str] = None
    wolfram_app_id: Optional[str] = None
    # Lean 4 proof checking (lean_check tool): a Lake project with Mathlib, or a
    # lean binary (LEAN_COMMAND, PATH or ~/.elan/bin). Absent both, no tool.
    lean_project_dir: Optional[str] = None
    lean_command: Optional[str] = None
    lean_timeout: int = 300
    llm_timeout: float = 600.0
    max_output_tokens: int = Field(default=0, ge=0, le=MAX_OUTPUT_TOKENS)  # 0 = provider default

    # ── Agent loop ──────────────────────────────────────────────────────
    agent_max_steps: int = 40
    agent_max_consecutive_failures: int = 4
    loop_policy: Literal["operation", "legacy"] = "operation"
    # One allowance across worker, checks, OCR and delegated data-agent work.
    # Engineering defaults; per-operation limits can be stricter. The absolute
    # deadline includes paused time, and missing token usage remains unknown.
    run_max_model_attempts: int = Field(default=80, ge=1)
    run_max_tool_starts: int = Field(default=160, ge=1)
    run_timeout_seconds: float = Field(default=1800, gt=0, allow_inf_nan=False)
    recent_turns_full_replay: int = 3
    # History may use the model's whole context window less a reply reserve.
    # Unset, the character budget is derived from that window (about 3
    # characters per token); compaction also triggers from the provider's
    # reported prompt tokens, so token-dense text cannot overflow the window.
    history_char_budget: Optional[int] = None
    context_reserve_tokens: int = 65_536
    compaction_target_ratio: float = 0.5  # compact to this share of the budget so it fires rarely
    keepalive_seconds: float = 10.0
    session_log_enabled: bool = True             # disable to use the legacy message replay path
    verify_rounds: int = 2
    verify_max_steps: int = 10
    verify_reasoning: Optional[str] = None         # override slider level for the verifier
    diagnostics_enabled: bool = False            # operator-only raw execution inspection

    # ── Tools ───────────────────────────────────────────────────────────
    tool_timeout: int = 120
    tool_max_timeout: int = 900
    tool_max_output_chars: int = 100_000  # per call; one runaway print cannot fill the window
    kernel_idle_seconds: int = 1800
    # Children spawned for agent actions (bash, the Jupyter kernel) get the
    # explicit allowlist in app/agent/child_env.py instead of the backend's
    # ambient environment, which is how the agent could otherwise read MatOpt's
    # own API keys with one `python` cell. False restores the pre-Tier-1
    # `dict(os.environ)` behaviour: the
    # hatch exists so a deployment whose tooling needs an inherited variable
    # (proxy, CA bundle, model-download token) can be unblocked without a code
    # change — but it re-exposes DEEPSEEK_API_KEY & co. to anything the agent
    # runs, so treat it as a temporary override rather than a setting.
    child_env_allowlist: bool = True
    # Mask credential-shaped strings in tool output before the model, the
    # session log or the UI see it (app/agent/exec_output.py).
    redact_tool_output: bool = True

    # ── Execution policy (ported from Codex; docs/CODEX-INTEGRATION.md) ─
    # OS sandbox around bash, lean and the Jupyter kernel (app/agent/sandbox.py).
    # "off" runs unsandboxed (the historical behaviour); "workspace-write" lets
    # children write only the workspace and temp; "read-only" writes nothing;
    # "external" means the deployment already isolates (a container) and only
    # the network setting is advertised to children.
    sandbox_mode: Literal["off", "workspace-write", "read-only", "external"] = "off"
    sandbox_network: bool = True               # outbound network inside the sandbox (pip, web data)
    sandbox_backend: Literal["auto", "seatbelt", "bwrap", "none"] = "auto"
    # Prefix rules for shell commands (app/agent/exec_rules.py). Empty uses the
    # built-in defaults; a TOML file adds to them.
    exec_rules_path: Optional[Path] = None
    # Command hooks (app/agent/hooks.py). Off by default: a hook runs as the
    # backend user, so only an operator-owned file may enable them.
    hooks_enabled: bool = False
    hooks_path: Optional[Path] = None          # default: <data_dir>/hooks.json
    hook_timeout: float = Field(default=60.0, gt=0, le=600)
    # AGENTS.md instruction files (app/agent/instructions.py).
    agents_md_enabled: bool = True
    agents_md_max_bytes: int = Field(default=32 * 1024, ge=1024, le=256 * 1024)
    # Approvals for calls that need them (Tool.needs_approval, exec rules that
    # say "prompt"): "off" runs needs_approval tools and refuses prompt rules
    # (the historical behaviour); "guardian" asks a reviewer model
    # (app/agent/guardian.py); "user" asks the person in the UI.
    approvals_reviewer: Literal["off", "guardian", "user"] = "off"
    # Configuration writes through the HTTP API (feature toggles, plugin
    # install, config import). Turn off when the sandbox allows network: the
    # agent's kernel can reach the API on localhost.
    settings_api_writable: bool = True
    approval_timeout: float = Field(default=600.0, gt=0, le=3600)
    guardian_timeout: float = Field(default=60.0, gt=0, le=600)
    guardian_fail_closed: bool = True

    # ── Sub-agents (app/agent/subagents/) ───────────────────────────────
    subagents_enabled: bool = False
    subagent_max_concurrent: int = Field(default=3, ge=1, le=8)
    subagent_max_per_turn: int = Field(default=6, ge=1, le=32)
    subagent_max_depth: int = Field(default=1, ge=1, le=3)
    subagent_max_steps: int = Field(default=20, ge=1, le=100)

    # ── Memories (app/agent/memory/) ────────────────────────────────────
    memories_enabled: bool = False
    memory_min_idle_hours: float = Field(default=6.0, ge=0)
    memory_max_inputs: int = Field(default=64, ge=1, le=1024)
    memory_summary_tokens: int = Field(default=2500, ge=200, le=20000)
    memory_disable_on_web: bool = True       # chats that read the web are not learned from

    # ── Code mode: Python cells call MatOpt tools (app/agent/kernel_tools.py)
    code_mode_enabled: bool = False
    code_mode_max_calls_per_cell: int = Field(default=50, ge=1, le=500)

    # ── Plugins (app/agent/plugins/) ────────────────────────────────────
    plugins_dir: Optional[Path] = None        # default: <data_dir>/plugins

    # Model-request retries (app/llm/retry.py): exponential backoff with jitter.
    llm_max_retries: int = Field(default=4, ge=0, le=10)
    llm_retry_base_delay: float = Field(default=0.2, gt=0, le=10)

    # Operator supplied per-million-token prices, keyed by provider/model.
    model_pricing: dict = Field(default_factory=dict)
    max_turn_cost: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    max_conversation_cost: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)

    # ── Skills (Agent Skills folders) ───────────────────────────────────
    skills_dirs: str = ""                  # extra comma-separated directories
    skill_auto_activate: bool = True
    skill_auto_activate_max: int = 2
    skill_catalog_limit: int = 16

    # ── MCP ─────────────────────────────────────────────────────────────
    mcp_enabled: bool = True
    mcp_config_path: Optional[Path] = None     # default: <repo>/mcp.json
    mcp_mount_path: str = "/mcp"
    # HTTP /mcp is unauthenticated unless one of these is set (stdio needs none).
    mcp_auth_token: Optional[str] = None       # static bearer token
    mcp_oauth_issuer: Optional[str] = None     # OAuth authorization server issuer
    mcp_oauth_jwks_uri: Optional[str] = None   # its JWKS; tokens are verified locally
    mcp_oauth_audience: Optional[str] = None   # default: <mcp_public_url><mcp_mount_path>
    mcp_oauth_scopes: str = ""                 # comma-separated required scopes
    mcp_public_url: Optional[str] = None       # e.g. https://matopt.example.com

    # ── Data agent (backend/data_agent_app) ───────────
    data_agent_enabled: bool = True

    # ── Web search (ddgs by default; optional SearXNG / Tavily / Brave) ─
    web_search_enabled: bool = True
    searxng_url: Optional[str] = None
    tavily_api_key: Optional[str] = None
    brave_search_api_key: Optional[str] = None

    # ── Alignment monitor (local analog of Astra misalignment monitoring)
    align_monitor: bool = True
    align_monitor_llm: bool = False

    # ── Voice: local Whisper transcription and Kokoro speech (models in DATA_DIR/models) ──
    speech_enabled: bool = True
    speech_prefetch: bool = True                   # download/load models at startup, in the background
    whisper_model: str = "small"                   # tiny | base | small | medium | large-v3 | turbo

    # ── Document understanding (the configured DeepSeek vision model) ──
    document_ocr_enabled: bool = True
    document_auto_parse: bool = True
    document_ocr_max_pages: int = Field(default=500, ge=1, le=2000)  # per call; whole documents by default
    document_ocr_concurrency: int = Field(default=8, ge=1, le=32)    # pages read in parallel
    document_ocr_max_files: int = Field(default=20, ge=1, le=50)
    document_ocr_max_file_mb: int = Field(default=200, ge=1, le=1000)
    document_ocr_max_dimension: int = Field(default=2048, ge=512, le=4096)
    document_ocr_max_pixels: int = Field(default=32_000_000, ge=1_000_000, le=64_000_000)
    document_ocr_page_timeout: float = Field(default=90.0, gt=0, le=600)
    document_ocr_timeout: float = Field(default=1800.0, gt=0, le=7200)
    document_ocr_max_tokens: int = Field(default=6000, ge=256, le=16384)

    document_preview_chars: int = 100_000

    # ── Derived paths ───────────────────────────────────────────────────
    @property
    def workspace_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "matopt.sqlite3"

    def ensure_dirs(self) -> None:
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def skill_directories(self) -> list[Path]:
        dirs = default_skill_group_dirs(REPO_ROOT, BACKEND_DIR)
        dirs.append(Path.home() / ".matopt" / "skills")
        for raw in (self.skills_dirs or "").split(","):
            raw = raw.strip()
            if raw:
                dirs.append(Path(raw).expanduser())
        seen: set[Path] = set()
        existing: list[Path] = []
        for d in dirs:
            resolved = d.resolve() if d.exists() else d
            if not d.is_dir() or resolved in seen:
                continue
            seen.add(resolved)
            existing.append(d)
        return existing

    def resolved_mcp_config_path(self) -> Path:
        if self.mcp_config_path:
            return Path(self.mcp_config_path)
        return REPO_ROOT / "mcp.json"


settings = Settings()
settings.ensure_dirs()
