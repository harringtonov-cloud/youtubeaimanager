"""Environment-driven configuration. All secrets come from environment variables only."""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # core
    database_url: str = "sqlite:///./data/dev.sqlite"
    redis_url: str = "redis://localhost:6379/0"
    admin_token: str = ""
    secret_key: str = ""
    backend_public_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:3000"
    timezone: str = "UTC"
    log_level: str = "INFO"
    # "celery" = always-on worker + Redis; "inline" = no Redis/Celery: a scheduled runner
    # (python -m app.runner, e.g. GitHub Actions cron) executes the pipeline in-process.
    queue_mode: str = "celery"
    runner_budget_minutes: int = 45

    # llm
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str = ""
    llm_model: str = "openai/gpt-4o-mini"
    llm_fallback_model: str = ""
    llm_http_referer: str = ""
    llm_app_title: str = "YouTube AI Manager"

    # tts
    tts_provider: str = "edge"
    tts_base_url: str = ""
    tts_api_key: str = ""
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "alloy"

    # stock
    pexels_api_key: str = ""
    pixabay_api_key: str = ""

    # google
    google_client_id: str = ""
    google_client_secret: str = ""
    youtube_api_key: str = ""

    # storage
    storage_backend: str = "local"
    data_dir: str = "./data"
    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "auto"

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        # Railway/Heroku style URLs -> SQLAlchemy + psycopg3 driver
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://"):]
        if v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://"):]
        return v

    @model_validator(mode="after")
    def _public_url_from_host(self) -> "Config":
        # Render exposes the service's public URL as RENDER_EXTERNAL_URL; an explicit
        # BACKEND_PUBLIC_URL always wins.
        ext = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
        if ext and not os.environ.get("BACKEND_PUBLIC_URL"):
            self.backend_public_url = ext.rstrip("/")
        return self

    @property
    def inline_mode(self) -> bool:
        return self.queue_mode.strip().lower() == "inline"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def oauth_redirect_uri(self) -> str:
        return self.backend_public_url.rstrip("/") + "/api/youtube/callback"

    @property
    def cors_origins(self) -> list[str]:
        origins = {self.frontend_url.rstrip("/")}
        origins.add("http://localhost:3000")
        return sorted(origins)


@lru_cache
def get_config() -> Config:
    return Config()


def secrets_status() -> dict[str, bool]:
    """Which secrets are configured (booleans only — values are never exposed)."""
    c = get_config()
    return {
        "ADMIN_TOKEN": bool(c.admin_token),
        "SECRET_KEY": bool(c.secret_key),
        "DATABASE_URL": bool(c.database_url),
        "REDIS_URL": bool(c.redis_url),
        "LLM_API_KEY": bool(c.llm_api_key),
        "GOOGLE_CLIENT_ID": bool(c.google_client_id),
        "GOOGLE_CLIENT_SECRET": bool(c.google_client_secret),
        "PEXELS_API_KEY": bool(c.pexels_api_key),
        "PIXABAY_API_KEY": bool(c.pixabay_api_key),
        "TTS_API_KEY": bool(c.tts_api_key or c.llm_api_key),
        "YOUTUBE_API_KEY": bool(c.youtube_api_key),
        "S3_BUCKET": bool(c.s3_bucket),
    }
