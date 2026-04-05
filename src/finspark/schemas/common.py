"""Common schema types used across the application."""

from datetime import datetime
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict


class DocType(str, Enum):
    BRD = "brd"
    SOW = "sow"
    API_SPEC = "api_spec"
    OTHER = "other"


class FileType(str, Enum):
    DOCX = "docx"
    PDF = "pdf"
    YAML = "yaml"
    JSON = "json"


class AdapterCategory(str, Enum):
    BUREAU = "bureau"
    KYC = "kyc"
    GST = "gst"
    PAYMENT = "payment"
    FRAUD = "fraud"
    NOTIFICATION = "notification"
    OPEN_BANKING = "open_banking"
    CUSTOM = "custom"


class ConfigStatus(str, Enum):
    DRAFT = "draft"
    CONFIGURED = "configured"
    VALIDATING = "validating"
    TESTING = "testing"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ROLLBACK = "rollback"


class SimulationStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    """Standard API response wrapper."""

    success: bool = True
    data: T | None = None
    message: str = ""
    errors: list[str] = []

    model_config = ConfigDict(from_attributes=True)


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated API response."""

    items: list[T]
    total: int
    page: int = 1
    page_size: int = 20
    has_next: bool = False


class TenantContext(BaseModel):
    """Tenant context passed through middleware."""

    tenant_id: str
    tenant_name: str
    role: str = "viewer"  # admin, configurator, viewer


class HealthCheck(BaseModel):
    status: str = "healthy"
    version: str
    timestamp: datetime
    checks: dict[str, Any] = {}
