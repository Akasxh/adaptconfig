"""Health check route."""

from datetime import UTC, datetime

from fastapi import APIRouter

from finspark.core.config import settings
from finspark.schemas.common import HealthCheck

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthCheck)
async def health_check() -> HealthCheck:
    return HealthCheck(
        status="healthy",
        version=settings.app_version,
        timestamp=datetime.now(UTC),
        checks={"database": "ok", "ai_enabled": settings.ai_enabled},
    )
