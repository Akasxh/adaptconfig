"""Schemas for API call observability."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class APICallLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    configuration_id: str
    adapter_name: str
    adapter_version: str
    endpoint_path: str
    http_method: str
    response_status: int
    response_time_ms: int
    schema_match: bool
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime


class APICallLogDetailResponse(APICallLogResponse):
    """Full detail including request/response bodies (already PII-masked)."""

    request_headers: dict[str, Any] | None = None
    request_body: dict[str, Any] | None = None
    response_headers: dict[str, Any] | None = None
    response_body: dict[str, Any] | None = None
    drift_fields: dict[str, Any] | None = None


class APICallLogListResponse(BaseModel):
    items: list[APICallLogResponse]
    total: int
    limit: int
    offset: int


class VersionComparisonResponse(BaseModel):
    adapter_name: str
    version_a: dict[str, Any]
    version_b: dict[str, Any]
    endpoint_path: str | None = None
