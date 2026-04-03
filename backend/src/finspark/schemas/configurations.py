"""
Pydantic schemas for the /configurations API surface.

Configurations are auto-generated adapter-wire-ups per tenant.  They go
through: generate → validate → (optionally compare) → deploy.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from finspark.schemas.common import PaginatedResponse


class ConfigStatus(str, Enum):
    DRAFT = "draft"
    CONFIGURED = "configured"
    VALIDATING = "validating"
    TESTING = "testing"
    VALIDATED = "validated"
    ACTIVE = "active"
    DEPLOYED = "deployed"
    DEPRECATED = "deprecated"
    FAILED = "failed"
    ARCHIVED = "archived"


class DeployEnvironment(str, Enum):
    SANDBOX = "sandbox"
    STAGING = "staging"
    PRODUCTION = "production"


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------


class ConfigGenerateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tenant_id: UUID
    adapter_id: UUID
    document_ids: list[UUID] = Field(
        default_factory=list,
        description="ParsedDocument IDs whose extracted data seeds the config.",
    )
    overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="Key-value pairs that forcibly override generated values.",
    )
    llm_hint: str = Field(
        default="",
        description="Free-text guidance injected into the LLM generation prompt.",
    )


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


class ValidationIssue(BaseModel):
    severity: str = Field(..., pattern="^(error|warning|info)$")
    field: str | None = None
    message: str
    code: str


class ConfigValidateResponse(BaseModel):
    config_id: UUID
    is_valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    validated_at: datetime


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------


class ConfigCompareRequest(BaseModel):
    base_config_id: UUID
    target_config_id: UUID


class ConfigDiff(BaseModel):
    path: str = Field(..., description="JSON-pointer to the changed key")
    op: str = Field(..., pattern="^(add|remove|replace)$")
    old_value: Any = None
    new_value: Any = None


class ConfigCompareResponse(BaseModel):
    base_config_id: UUID
    target_config_id: UUID
    diffs: list[ConfigDiff]
    is_breaking: bool
    summary: str


# ---------------------------------------------------------------------------
# Deploy
# ---------------------------------------------------------------------------


class ConfigDeployRequest(BaseModel):
    environment: DeployEnvironment = DeployEnvironment.SANDBOX
    dry_run: bool = False
    notes: str = ""


class ConfigDeployResponse(BaseModel):
    config_id: UUID
    environment: DeployEnvironment
    deploy_id: UUID
    dry_run: bool
    status: str
    deployed_at: datetime
    deployed_by: UUID


# ---------------------------------------------------------------------------
# CRUD response
# ---------------------------------------------------------------------------


class ConfigRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    tenant_id: UUID
    adapter_id: UUID
    status: ConfigStatus
    environment: DeployEnvironment | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime


ConfigListResponse = PaginatedResponse[ConfigRecord]


# ---------------------------------------------------------------------------
# Transition
# ---------------------------------------------------------------------------


class ConfigTransitionRequest(BaseModel):
    target_status: ConfigStatus
    simulation_results: dict[str, Any] | None = Field(
        default=None,
        description="Required when transitioning testing → active.",
    )
