"""
Application settings (pydantic-settings). Values come from the environment
or ``backend/.env``; see ``.env.example`` for the full list.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.llm.catalog import LEVELS, MAX_OUTPUT_TOKENS, MODEL_ID, PROVIDER_ID, canonical_model, canonical_provider, canonical_reasoning

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
SKILL_GROUPS = ("matopt", "lambda", "anthropic")


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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BACKEND_DIR / ".env"), env_file_encoding="utf-8", extra="ignore")

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
    # One allowance across worker, checks, OCR and delegated LAMBDA work.
    # Engineering defaults; per-operation limits can be stricter. The absolute
    # deadline includes paused time, and missing token usage remains unknown.
    run_max_model_attempts: int = Field(default=80, ge=1)
    run_max_tool_starts: int = Field(default=160, ge=1)
    run_timeout_seconds: float = Field(default=1800, gt=0, allow_inf_nan=False)
    recent_turns_full_replay: int = 3
    history_char_budget: int = 240_000
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
    tool_max_output_chars: int = 12_000
    kernel_idle_seconds: int = 1800
    # Children spawned for agent actions (bash, the Jupyter kernel) get the
    # explicit allowlist in app/agent/child_env.py instead of the backend's
    # ambient environment, which is how the agent could otherwise read MatOpt's
    # own API keys with one `python` cell. False restores the pre-Tier-1
    # `dict(os.environ)` behaviour (docs/DSH-REFINEMENT-PLAN.md §3.9.3): the
    # hatch exists so a deployment whose tooling needs an inherited variable
    # (proxy, CA bundle, model-download token) can be unblocked without a code
    # change — but it re-exposes DEEPSEEK_API_KEY & co. to anything the agent
    # runs, so treat it as a temporary override rather than a setting.
    child_env_allowlist: bool = True

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

    # ── LAMBDA data agent (vendored AMA-CMFAI/LAMBDA backend) ───────────
    lambda_enabled: bool = True

    # ── Web search (ddgs by default; optional SearXNG / Tavily / Brave) ─
    web_search_enabled: bool = True
    searxng_url: Optional[str] = None
    tavily_api_key: Optional[str] = None
    brave_search_api_key: Optional[str] = None

    # ── Alignment monitor (local analog of Astra misalignment monitoring)
    align_monitor: bool = True
    align_monitor_llm: bool = False

    # ── Document understanding (the configured DeepSeek vision model) ──
    document_ocr_enabled: bool = True
    document_auto_parse: bool = True
    document_ocr_max_pages: int = Field(default=12, ge=1, le=100)
    document_ocr_max_files: int = Field(default=6, ge=1, le=20)
    document_ocr_max_file_mb: int = Field(default=25, ge=1, le=200)
    document_ocr_max_dimension: int = Field(default=2048, ge=512, le=4096)
    document_ocr_max_pixels: int = Field(default=32_000_000, ge=1_000_000, le=64_000_000)
    document_ocr_page_timeout: float = Field(default=90.0, gt=0, le=600)
    document_ocr_timeout: float = Field(default=300.0, gt=0, le=1800)
    document_ocr_max_tokens: int = Field(default=6000, ge=256, le=16384)

    # ── Optional local document parsing fallback (MinerU) ───────────────
    mineru_command: Optional[str] = None           # path to the mineru CLI; auto-detected if empty
    mineru_backend: str = "pipeline"               # pipeline | vlm-transformers | vlm-http-client ...
    mineru_device: str = "auto"                    # auto | cpu | mps | cuda
    mineru_lang: str = "en"
    mineru_timeout: int = 900
    mineru_model_source: str = "huggingface"       # huggingface | modelscope | local
    document_preview_chars: int = 6000

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
