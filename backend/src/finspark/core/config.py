"""Application settings — sourced from environment / .env file."""

from typing import Literal

from pydantic import AnyHttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_PATTERNS = ("change-me", "insecure")
_MIN_KEY_LENGTH = 32


def _is_insecure(value: str) -> bool:
    lower = value.lower()
    return any(pat in lower for pat in _INSECURE_PATTERNS)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Application
    APP_ENV: Literal["development", "staging", "production"] = "production"
    APP_DEBUG: bool = False
    APP_SECRET_KEY: str = "insecure-default-change-in-production"
    APP_ALLOWED_HOSTS: list[str] = ["*"]
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./finspark.db"

    # Auth
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # LLM — Gemini is the primary provider
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # OpenAI (legacy fallback, unused when Gemini key is set)
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"

    # Embeddings
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    EMBEDDING_DEVICE: str = "cpu"

    # File uploads
    UPLOAD_DIR: str = "/tmp/finspark_uploads"
    MAX_UPLOAD_SIZE_MB: int = 50

    # Logging
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "json"

    @model_validator(mode="after")
    def validate_secret_key_in_production(self) -> "Settings":
        if self.APP_ENV == "development":
            return self
        if _is_insecure(self.APP_SECRET_KEY):
            raise ValueError(
                "APP_SECRET_KEY contains an insecure default value; "
                "set a strong secret when APP_ENV is not 'development'."
            )
        if len(self.APP_SECRET_KEY) < _MIN_KEY_LENGTH:
            raise ValueError(
                f"APP_SECRET_KEY must be at least {_MIN_KEY_LENGTH} characters "
                "when APP_ENV is not 'development'."
            )
        return self

    @field_validator("APP_ALLOWED_HOSTS", "ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_allowed_hosts(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            import json
            return json.loads(v)
        return v

    @model_validator(mode="after")
    def reject_wildcard_origins_in_production(self) -> "Settings":
        if not self.APP_DEBUG and "*" in self.ALLOWED_ORIGINS:
            raise ValueError(
                "ALLOWED_ORIGINS must not contain '*' when APP_DEBUG is False"
            )
        return self


settings = Settings()
