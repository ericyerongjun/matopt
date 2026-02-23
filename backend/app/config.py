"""
Application settings loaded from environment variables.
Uses pydantic-settings for validation and .env file support.
"""

from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────────────────
    app_name: str = "MatOpt"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # ── OpenAI / LLM ────────────────────────────────────────────────────
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o"
    openai_base_url: Optional[str] = None          # set for local Qwen2.5-Math via vLLM/TGI

    # ── Wolfram Alpha ────────────────────────────────────────────────────
    wolfram_app_id: Optional[str] = None

    # ── File storage ─────────────────────────────────────────────────────
    upload_dir: Path = Path("uploads")
    export_dir: Path = Path("exports")
    max_upload_size_mb: int = 50

    # ── Timeouts (seconds) ───────────────────────────────────────────────
    sympy_timeout: int = 10
    sandbox_timeout: int = 5
    ocr_timeout: int = 30
    llm_timeout: int = 120

    def ensure_dirs(self) -> None:
        """Create upload/export directories if they don't exist."""
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)


# Singleton – import this everywhere
settings = Settings()
settings.ensure_dirs()
