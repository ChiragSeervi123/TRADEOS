"""Application configuration, loaded from environment variables (see .env.example)."""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

API_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../../.env"), extra="ignore")

    environment: str = "development"  # development | test | production
    database_url: str = f"sqlite:///{(API_ROOT / 'tradeos_dev.db').as_posix()}"
    secret_key: str = "dev-only-insecure-secret-change-me-0123456789"
    access_token_minutes: int = 15
    refresh_token_days: int = 14
    password_reset_minutes: int = 30
    cors_origins: str = "http://localhost:3000"
    storage_dir: str = str(API_ROOT / "storage")
    max_upload_mb: int = 10
    signed_url_seconds: int = 300
    # Jobs: "inline" runs background jobs right after the request commits (dev/tests);
    # "worker" leaves them queued for `python -m app.worker`.
    jobs_mode: str = "inline"
    # AI provider. Without ANTHROPIC_API_KEY the deterministic demo parser is used.
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5-5"
    ai_timeout_seconds: int = 60
    auth_rate_limit_per_minute: int = 10
    enable_demo_login: bool = True
    # Create missing tables at startup. Stopgap until Alembic migrations exist: it never
    # alters existing tables, so after a model change the table must be migrated by hand.
    auto_create_schema: bool = False

    @field_validator("database_url")
    @classmethod
    def _use_psycopg3(cls, v: str) -> str:
        # Hosted Postgres (Neon, Vercel) hands out postgres:// or postgresql:// URLs, which
        # SQLAlchemy maps to psycopg2. Only psycopg 3 is installed, so name it explicitly.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix) :]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_production and s.secret_key.startswith("dev-only"):
        raise RuntimeError("SECRET_KEY must be set in production")
    return s
