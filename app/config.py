"""Application settings, loaded from the environment (never hardcoded)."""

from __future__ import annotations

import logging
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "School Analytics Platform"
    debug: bool = False

    secret_key: str = ""
    database_url: str = "sqlite:///./school.db"

    session_cookie_name: str = "sa_session"
    session_cookie_secure: bool = False
    session_max_age: int = Field(default=8 * 60 * 60, ge=300, le=30 * 24 * 60 * 60)

    # When on, the login page lists the seeded demo accounts and can prefill them.
    # Intended for local evaluation only; leave off anywhere real users exist.
    demo_mode: bool = False
    demo_password: str = ""

    pass_percentage: float = Field(default=33.0, ge=0, le=100)
    at_risk_percentage: float = Field(default=45.0, ge=0, le=100)
    at_risk_attendance: float = Field(default=75.0, ge=0, le=100)

    @field_validator("secret_key")
    @classmethod
    def _ensure_secret_key(cls, value: str) -> str:
        if value.strip():
            return value
        # No secret configured: mint an ephemeral one so local development works.
        # Sessions are invalidated on restart, which is why production must set SECRET_KEY.
        logger.warning(
            "SECRET_KEY is not set; generating an ephemeral key. "
            "Sessions will not survive a restart. Set SECRET_KEY in .env."
        )
        return secrets.token_urlsafe(48)

    @property
    def templates_dir(self) -> Path:
        return BASE_DIR / "templates"

    @property
    def static_dir(self) -> Path:
        return BASE_DIR / "static"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
