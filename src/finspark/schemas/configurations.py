"""Schemas for integration configurations."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from finspark.schemas.common import ConfigStatus


class FieldMapping(BaseModel):
    """A single field mapping from source to target."""

    source_field: str
    target_field: str
    transformation: str | None = None  # e.g., "uppercase", "date_format", "split"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    is_confirmed: bool = False


class TransformationRule(BaseModel):
    """A transformation rule applied during integration."""

    name: str
    rule_type: str  # type_cast, format, map, custom
    source_path: str
    target_path: str
    expression: str  # e.g., "upper()", "date_format('YYYY-MM-DD')", "lookup(table)"
    params: dict[str, Any] = {}


class HookConfig(BaseModel):
    """Hook configuration for pre/post processing."""

    name: str
    hook_type: str  # pre_request, post_response, on_error, on_timeout
    handler: str  # function reference or expression
    is_active: bool = True
    order: int = 0
    params: dict[str, Any] = {}


class ConfigurationPartialUpdate(BaseModel):
    """Partial update for configuration fields."""

    name: str | None = None
    field_mappings: list[FieldMapping] | None = None
    notes: str | None = None


class GenerateConfigRequest(BaseModel):
    """Request to generate a configuration from a parsed document."""

    document_id: str
    adapter_version_id: str
    name: str
    auto_map: bool = True  # Use AI for field mapping


class ConfigurationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    adapter_version_id: str
    document_id: str | None = None
    status: ConfigStatus
    version: int
    field_mappings: list[FieldMapping] = []
    transformation_rules: list[TransformationRule] = []
    hooks: list[HookConfig] = []
    created_at: datetime
    updated_at: datetime


class ConfigDiffItem(BaseModel):
    """Single diff item between two configurations."""

    path: str
    change_type: str  # added, removed, modified
    old_value: Any | None = None
    new_value: Any | None = None
    is_breaking: bool = False


class ConfigDiffResponse(BaseModel):
    """Configuration diff comparison result."""

    config_a_id: str
    config_b_id: str
    total_changes: int
    breaking_changes: int
    diffs: list[ConfigDiffItem] = []


class TransitionRequest(BaseModel):
    """Request to transition a configuration to a new lifecycle state."""

    target_state: ConfigStatus
    reason: str | None = None


class TransitionResponse(BaseModel):
    """Response after a successful lifecycle transition."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    previous_state: ConfigStatus
    new_state: ConfigStatus
    available_transitions: list[ConfigStatus] = []


class ConfigValidationResult(BaseModel):
    """Result of validating a configuration."""

    is_valid: bool
    errors: list[str] = []
    warnings: list[str] = []
    coverage_score: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_required_fields: list[str] = []
    unmapped_source_fields: list[str] = []


class ConfigTemplateResponse(BaseModel):
    """A pre-built configuration template."""

    name: str
    description: str
    adapter_category: str
    default_config: dict[str, Any]


class BatchConfigRequest(BaseModel):
    """Request body for batch operations on configurations."""

    config_ids: list[str]


class BatchValidationItem(BaseModel):
    """Validation result for a single config in a batch."""

    config_id: str
    result: ConfigValidationResult | None = None
    error: str | None = None


class BatchSimulationItem(BaseModel):
    """Simulation result for a single config in a batch."""

    config_id: str
    status: str | None = None
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    duration_ms: int = 0
    error: str | None = None


class ConfigSummaryResponse(BaseModel):
    """Summary statistics for a tenant's configurations."""

    total: int
    by_status: dict[str, int]
    by_adapter: dict[str, int]
    avg_confidence: float


class ConfigHistoryEntry(BaseModel):
    """A single version history entry."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    configuration_id: str
    version: int
    change_type: str
    previous_value: dict[str, Any] | None = None
    new_value: dict[str, Any] | None = None
    changed_by: str | None = None
    created_at: datetime


class RollbackRequest(BaseModel):
    """Request to rollback a configuration to a specific version."""

    target_version: int


class RollbackResponse(BaseModel):
    """Response after a successful rollback."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    previous_version: int
    restored_version: int
    status: str


class VersionComparisonResponse(BaseModel):
    """Comparison between two versions of the same configuration."""

    configuration_id: str
    version_a: int
    version_b: int
    total_changes: int
    breaking_changes: int
    diffs: list[ConfigDiffItem] = []
