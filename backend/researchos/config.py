"""Environment-driven settings. Secrets are only ever read from the environment or .env."""

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
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

    # Literature search (specs/002). Base URLs exist so tests and e2e can point at a local
    # upstream; production keeps the defaults.
    openalex_api_key: str = ""
    contact_email: str = ""
    arxiv_base_url: str = "https://export.arxiv.org"
    openalex_base_url: str = "https://api.openalex.org"
    crossref_base_url: str = "https://api.crossref.org"
    search_source_timeout_seconds: float = Field(default=10.0, gt=0)
    search_max_wait_seconds: float = Field(default=8.0, ge=0)
    search_cache_ttl_seconds: int = Field(default=21600, ge=0)
    search_user_per_minute: int = Field(default=10, gt=0)
    arxiv_min_interval_seconds: float = Field(default=3.0, ge=0)

    # Model calls (specs/007). The key is only ever read from the environment and is never
    # logged or returned. The limits are placeholders until the user states a cost ceiling.
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    model_name: str = "claude-sonnet-5-5"
    model_max_output_tokens: int = Field(default=4096, gt=0)
    model_timeout_seconds: float = Field(default=120.0, gt=0)
    model_context_chars: int = Field(default=120_000, gt=0)
    model_user_per_minute: int = Field(default=5, gt=0)
    model_user_per_day: int = Field(default=50, gt=0)

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

    @model_validator(mode="after")
    def _respect_arxiv_terms(self) -> "Settings":
        # arXiv's terms: at most one request every three seconds. Only a local test
        # upstream may be polled faster.
        official = urlsplit(self.arxiv_base_url).hostname == "export.arxiv.org"
        if official and self.arxiv_min_interval_seconds < 3:
            raise ValueError("ARXIV_MIN_INTERVAL_SECONDS must be at least 3 for export.arxiv.org")
        return self

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def origin_allowlist(self) -> set[str]:
        return {o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
