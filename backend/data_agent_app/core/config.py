import os
from pathlib import Path
from typing import List
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.llm.catalog import MODEL_ID, canonical_model


# MATOPT HOST GLUE: isolate data-agent files from MatOpt conversation workspaces.
# DATA_DIR is MatOpt's env (tests point it at a temp dir).
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_DATA_DIR = Path(os.environ.get("DATA_DIR") or (_BACKEND_DIR / "data"))
_DATA_AGENT_ROOT = _DATA_DIR / "data_agent"
_SQLITE = (_DATA_AGENT_ROOT / "data_agent_local.db").as_posix()
_WORKSPACES = str(_DATA_AGENT_ROOT / "workspaces")


class Settings(BaseSettings):
    # Local-first defaults. The open-source build stores data under ./data.
    DATABASE_URL: str = f"sqlite:///{_SQLITE}"
    LOCAL_MODE: bool = True
    LOCAL_USER_EMAIL: str = "local@data-agent.local"
    LOCAL_USER_NAME: str = "Local User"
    
    # Legacy auth utility defaults. Local mode does not require these in .env.
    SECRET_KEY: str = "your-super-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080  # 7 days
    
    # Share MatOpt's single DeepSeek V4.1 Flash route and credentials.
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEFAULT_MODEL: str = Field(
        default=MODEL_ID,
        validation_alias=AliasChoices("DEFAULT_MODEL", "MODEL"),
    )

    # Legacy model-list environment variables remain readable for migration,
    # but they cannot expose or dispatch another provider/model.
    MODEL_LIST: List[str] = Field(
        default=[MODEL_ID],
        validation_alias=AliasChoices("MODEL_LIST", "AVAILABLE_MODELS"),
    )
    MULTIMODAL_MODELS: List[str] = [MODEL_ID]
    MULTIMODAL_IMAGE_DETAIL: str = "high"
    MULTIMODAL_IMAGE_MAX_BYTES: int = 8 * 1024 * 1024

    @field_validator("DEFAULT_MODEL", mode="before")
    @classmethod
    def only_flash_model(cls, value):
        return canonical_model(value)

    @field_validator("MODEL_LIST", "MULTIMODAL_MODELS", mode="before")
    @classmethod
    def only_flash_list(cls, value):
        return [MODEL_ID]

    @field_validator("DEEPSEEK_BASE_URL", mode="before")
    @classmethod
    def default_deepseek_url(cls, value):
        return (value or "").strip() or "https://api.deepseek.com"

    @property
    def DATA_AGENT_DATA_ROOT(self) -> Path:
        return Path(self.LOCAL_WORKSPACE_ROOT).expanduser().resolve().parent

    @property
    def AVAILABLE_MODELS(self) -> List[str]:
        return self.MODEL_LIST
    
    # Code Execution
    CODE_EXECUTION_TIMEOUT: int = 900
    MAX_OUTPUT_LENGTH: int = 1000000
    
    # Local open-source build executes tools in a local workspace directory.
    CODE_EXECUTION_MODE: str = "local"
    LOCAL_WORKSPACE_ROOT: str = _WORKSPACES
    
    # OpenSandbox Configuration
    # Custom sandbox image with additional packages (e.g., LaTeX)
    # Build custom image: cd sandbox-docker && docker build -t data-sandbox:latest .
    SANDBOX_IMAGE: str = "opensandbox/code-interpreter:v1.0.2"
    
    # OpenSandbox container max lifetime. OpenSandbox treats this as container
    # TTL, not app-level idle timeout, so keep it comfortably above user work.
    SANDBOX_CONTAINER_TIMEOUT_MINUTES: int = 180

    # App-level idle timeout. The backend reaps sessions after this much
    # inactivity, while active sandboxes can live longer than 30 minutes.
    SANDBOX_IDLE_TIMEOUT_MINUTES: int = 30
    SANDBOX_CLEANUP_ORPHANS_ON_STARTUP: bool = True
    SANDBOX_PREINSTALLED_PACKAGES: bool = False
    
    # CORS
    FRONTEND_URL: str = "http://localhost:3000"
    
    # Agent Configuration
    MAX_AGENT_ITERATIONS: int = 50  # Maximum tool execution iterations per conversation
    
    # Aliyun OSS Configuration (unused in local mode)
    ALIYUN_OSS_ACCESS_KEY_ID: str = ""
    ALIYUN_OSS_ACCESS_KEY_SECRET: str = ""
    ALIYUN_OSS_BUCKET_NAME: str = "data-app-prod"
    ALIYUN_OSS_ENDPOINT: str = "oss-cn-hongkong.aliyuncs.com"
    ALIYUN_OSS_BASE_URL: str = "https://data-app-prod.oss-cn-hongkong.aliyuncs.com"
    
    # File Storage Mode
    FILE_STORAGE_MODE: str = "local"
    
    model_config = SettingsConfigDict(
        env_file=str(_BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )


settings = Settings()
