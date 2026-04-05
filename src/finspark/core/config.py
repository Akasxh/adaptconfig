"""Application configuration."""

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings

_INSECURE_PATTERNS = ("change-me", "insecure")
_MIN_KEY_LENGTH = 32


def _is_insecure(value: str) -> bool:
    lower = value.lower()
    return any(pat in lower for pat in _INSECURE_PATTERNS)


class Settings(BaseSettings):
    app_name: str = "AdaptConfig Integration Engine"
    app_version: str = "0.1.0"
    debug: bool = False

    # Database
    database_url: str = "sqlite+aiosqlite:///./finspark.db"

    # Security
    secret_key: str = "change-me-in-production-use-openssl-rand-hex-32"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60
    encryption_key: str = "change-me-in-production"

    # Hosting — use ["*"] for Railway/cloud deployments where host varies
    allowed_hosts: list[str] = ["*"]

    # CORS — origins allowed to make cross-origin requests
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Rate limiting
    rate_limit_max_requests: int = 100
    rate_limit_window_seconds: int = 60

    # File uploads
    upload_dir: Path = Path("./uploads")
    max_upload_size_mb: int = 50

    # AI (optional)
    llm_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3-flash-preview"
    ai_enabled: bool = False

    model_config = {"env_prefix": "FINSPARK_", "env_file": ".env", "extra": "ignore"}

    @model_validator(mode="after")
    def validate_keys_in_production(self) -> "Settings":
        if self.debug:
            return self
        for field_name in ("secret_key", "encryption_key"):
            value: str = getattr(self, field_name)
            if _is_insecure(value):
                raise ValueError(
                    f"{field_name} contains an insecure default value; "
                    "set a strong secret before disabling debug mode."
                )
            if len(value) < _MIN_KEY_LENGTH:
                raise ValueError(
                    f"{field_name} must be at least {_MIN_KEY_LENGTH} characters "
                    "when debug is False."
                )
        return self


settings = Settings()
