"""Schemas for integration adapters."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from finspark.schemas.common import AdapterCategory


class AdapterEndpoint(BaseModel):
    """Single endpoint definition within an adapter."""

    path: str
    method: str
    description: str = ""
    request_fields: list[dict[str, Any]] = []
    response_fields: list[dict[str, Any]] = []


class AdapterVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    version: str
    status: str
    auth_type: str
    base_url: str | None = None
    endpoints: list[AdapterEndpoint] = []
    changelog: str | None = None


class AdapterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    category: AdapterCategory
    description: str | None = None
    is_active: bool
    icon: str | None = None
    versions: list[AdapterVersionResponse] = []
    created_at: datetime


class AdapterListResponse(BaseModel):
    adapters: list[AdapterResponse]
    total: int
    categories: list[str] = []


class MigrationStep(BaseModel):
    action: str
    description: str


class DeprecationInfoResponse(BaseModel):
    version: str
    status: str
    sunset_date: str | None = None
    days_until_sunset: int | None = None
    replacement_version: str | None = None
    migration_guide: list[MigrationStep] = []


class AdapterSuggestRequest(BaseModel):
    """Request body for POST /adapters/suggest."""

    document_id: str


class AdapterSuggestMatch(BaseModel):
    """One ranked suggestion in the suggest response."""

    adapter_id: str
    version_id: str
    adapter_name: str
    version: str
    category: AdapterCategory
    score: float
    reason: str = ""


class AdapterSuggestResponse(BaseModel):
    """Response body for POST /adapters/suggest."""

    matches: list[AdapterSuggestMatch] = []
    suggest_custom: bool = False
    threshold: float = 0.55
