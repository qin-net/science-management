"""Environment-driven settings. Secrets are only ever read from the environment or .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Validation errors must not print DATABASE_URL, which may hold a password.
        hide_input_in_errors=True,
    )

    # SQLAlchemy URL; postgresql+psycopg:// is the only supported driver.
    database_url: str = ""
    storage_dir: Path = REPO_ROOT / "data" / "files"
    max_upload_mb: int = Field(default=50, gt=0)

    session_ttl_hours: int = Field(default=168, gt=0)
    cookie_secure: bool = False
    allow_registration: bool = True
    allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    parser_default: str = "pdfium"
    parse_timeout_seconds: int = Field(default=600, gt=0)
    parse_max_attempts: int = Field(default=3, gt=0)
    parse_retry_base_seconds: float = Field(default=5.0, ge=0)
    docling_python: str = ""

    worker_poll_seconds: float = Field(default=1.0, gt=0)
    worker_lease_seconds: int = Field(default=60, ge=5)
    worker_stale_seconds: int = Field(default=30, ge=5)

    log_level: str = "INFO"

    @field_validator("database_url")
    @classmethod
    def _postgres_only(cls, value: str) -> str:
        if value and not value.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must start with postgresql+psycopg://")
        return value

    @field_validator("parser_default")
    @classmethod
    def _known_parser(cls, value: str) -> str:
        if value not in {"pdfium", "docling"}:
            raise ValueError("PARSER_DEFAULT must be pdfium or docling")
        return value

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def origin_allowlist(self) -> set[str]:
        return {o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
