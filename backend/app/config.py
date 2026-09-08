"""
Application settings (pydantic-settings). Values come from the environment
or ``backend/.env``; see ``.env.example`` for the full list.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


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

    # ── Default model selection ─────────────────────────────────────────
    default_provider: str = "deepseek"
    default_model: str = "deepseek-v4-pro"
    default_reasoning: str = "medium"

    # ── Providers (OpenAI-compatible) ───────────────────────────────────
    deepseek_api_key: Optional[str] = None
    deepseek_base_url: Optional[str] = None
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    qwen_api_key: Optional[str] = None
    qwen_base_url: Optional[str] = None
    qwen_region: str = "intl"                      # intl | cn
    wolfram_app_id: Optional[str] = None
    llm_timeout: float = 600.0
    max_output_tokens: int = 0                     # 0 = provider default

    # ── Agent loop ──────────────────────────────────────────────────────
    agent_max_steps: int = 40
    agent_max_consecutive_failures: int = 4
    recent_turns_full_replay: int = 3
    history_char_budget: int = 240_000
    keepalive_seconds: float = 10.0
    verify_rounds: int = 2
    verify_max_steps: int = 10
    verify_reasoning: Optional[str] = None         # override slider level for the verifier

    # ── Tools ───────────────────────────────────────────────────────────
    tool_timeout: int = 120
    tool_max_timeout: int = 900
    tool_max_output_chars: int = 12_000
    kernel_idle_seconds: int = 1800

    # ── Document parsing (MinerU) ───────────────────────────────────────
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


settings = Settings()
settings.ensure_dirs()
