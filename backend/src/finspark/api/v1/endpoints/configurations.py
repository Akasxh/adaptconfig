"""
POST   /configurations/generate              — LLM-driven config generation
GET    /configurations/                      — list configs (tenant-scoped)
GET    /configurations/{config_id}           — get config detail
POST   /configurations/{config_id}/validate  — validate against adapter schema
POST   /configurations/compare               — diff two configs
POST   /configurations/{config_id}/deploy    — deploy to an environment
DELETE /configurations/{config_id}           — archive config
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.api.deps import (
    CurrentUser,
    DbDep,
    PaginationDep,
    TenantCtx,
    UserContext,
    require_roles,
)
from finspark.schemas.common import MessageResponse
from finspark.schemas.configurations import (
    ConfigCompareRequest,
    ConfigCompareResponse,
    ConfigDeployRequest,
    ConfigDeployResponse,
    ConfigGenerateRequest,
    ConfigListResponse,
    ConfigRecord,
    ConfigStatus,
    ConfigTransitionRequest,
    ConfigValidateResponse,
    DeployEnvironment,
)
from finspark.services.lifecycle import (
    InvalidTransitionError,
    InsufficientRoleError,
    validate_rollback,
    validate_transition,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/configurations", tags=["Configurations"])


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------


@router.post(
    "/generate",
    response_model=ConfigRecord,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Auto-generate an integration configuration",
    description=(
        "Uses the LLM engine to derive a configuration for the specified "
        "adapter by cross-referencing parsed document entities.  Generation "
        "is asynchronous when document parsing is still in progress."
    ),
    responses={
        202: {"description": "Generation accepted; config status is `draft`."},
        404: {"description": "Adapter or documents not found."},
        403: {"description": "Tenant access denied."},
    },
)
async def generate_configuration(
    body: ConfigGenerateRequest,
    db: DbDep,
    _user: CurrentUser,
) -> ConfigRecord:
    # TODO: invoke LLM auto-config service
    now = datetime.utcnow()
    return ConfigRecord(
        id=uuid.uuid4(),
        tenant_id=body.tenant_id,
        adapter_id=body.adapter_id,
        status=ConfigStatus.DRAFT,
        environment=None,
        payload={},
        version=1,
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get(
    "/",
    response_model=ConfigListResponse,
    summary="List tenant configurations",
    responses={200: {"description": "Paginated config records."}},
)
async def list_configurations(
    tenant_ctx: TenantCtx,
    db: DbDep,
    pagination: PaginationDep,
) -> ConfigListResponse:
    return ConfigListResponse(
        items=[],
        total=0,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=0,
    )


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


@router.get(
    "/{config_id}",
    response_model=ConfigRecord,
    summary="Get configuration detail",
    responses={
        200: {"description": "Config record with full payload."},
        404: {"description": "Config not found."},
        403: {"description": "Tenant access denied."},
    },
)
async def get_configuration(
    config_id: UUID,
    tenant_ctx: TenantCtx,
    db: DbDep,
) -> ConfigRecord:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Configuration {config_id} not found.",
    )


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


@router.post(
    "/{config_id}/validate",
    response_model=ConfigValidateResponse,
    summary="Validate configuration against its adapter schema",
    description=(
        "Runs structural + semantic validation.  Returns a list of issues "
        "with severity (error|warning|info).  A config with zero `error` "
        "issues transitions to `validated` status."
    ),
    responses={
        200: {"description": "Validation result."},
        404: {"description": "Config not found."},
        403: {"description": "Tenant access denied."},
    },
)
async def validate_configuration(
    config_id: UUID,
    tenant_ctx: TenantCtx,
    db: DbDep,
) -> ConfigValidateResponse:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Configuration {config_id} not found.",
    )


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------


@router.post(
    "/compare",
    response_model=ConfigCompareResponse,
    summary="Diff two configurations",
    description=(
        "Returns a JSON-Patch-style diff between `base_config_id` and "
        "`target_config_id`.  `is_breaking` is true when any diff path "
        "maps to a mandatory field in the adapter schema."
    ),
    responses={
        200: {"description": "Diff result."},
        404: {"description": "One or both configs not found."},
        403: {"description": "Both configs must belong to the same tenant."},
    },
)
async def compare_configurations(
    body: ConfigCompareRequest,
    tenant_ctx: TenantCtx,
    db: DbDep,
) -> ConfigCompareResponse:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="One or both configurations not found.",
    )


# ---------------------------------------------------------------------------
# Deploy
# ---------------------------------------------------------------------------


@router.post(
    "/{config_id}/deploy",
    response_model=ConfigDeployResponse,
    summary="Deploy a validated configuration",
    description=(
        "Pushes the configuration to the target environment.  "
        "Only `validated` configs can be deployed.  "
        "Set `dry_run=true` to preview the deployment plan without applying it."
    ),
    responses={
        200: {"description": "Deploy result."},
        400: {"description": "Config is not in `validated` status."},
        404: {"description": "Config not found."},
        403: {"description": "Deployer role required."},
    },
)
async def deploy_configuration(
    config_id: UUID,
    body: ConfigDeployRequest,
    tenant_ctx: TenantCtx,
    db: DbDep,
    user: Annotated[UserContext, Depends(require_roles("admin", "deployer", "superadmin"))],
) -> ConfigDeployResponse:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Configuration {config_id} not found.",
    )


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


@router.post(
    "/{config_id}/rollback",
    response_model=MessageResponse,
    summary="Rollback configuration to draft",
    responses={
        200: {"description": "Configuration rolled back to draft."},
        400: {"description": "Rollback not permitted from current state."},
        404: {"description": "Config not found."},
        403: {"description": "Tenant access denied."},
    },
)
async def rollback_configuration(
    config_id: UUID,
    tenant_ctx: TenantCtx,
    db: DbDep,
    _user: CurrentUser,
) -> MessageResponse:
    # In a real implementation we'd load the config record from DB here.
    # For now we demonstrate the state validation guard with a placeholder.
    # Replace `current_status` with the DB-fetched value when persistence is wired.
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Configuration {config_id} not found.",
    )


# ---------------------------------------------------------------------------
# Transition
# ---------------------------------------------------------------------------


@router.post(
    "/{config_id}/transition",
    response_model=ConfigRecord,
    summary="Transition configuration state",
    description=(
        "Moves a configuration through the lifecycle state machine. "
        "Gate guards enforce prerequisites for each transition. "
        "Transitions to `active` or `deprecated` require admin role."
    ),
    responses={
        200: {"description": "Configuration transitioned successfully."},
        400: {"description": "Invalid transition or gate check failed."},
        403: {"description": "Insufficient role or tenant access denied."},
        404: {"description": "Config not found."},
    },
)
async def transition_configuration(
    config_id: UUID,
    body: ConfigTransitionRequest,
    tenant_ctx: TenantCtx,
    db: DbDep,
    user: CurrentUser,
) -> ConfigRecord:
    # In a real implementation we'd load the config record from DB here.
    # Gate validation is exercised via the lifecycle service:
    #
    #   validate_transition(
    #       current_status=config.status,
    #       target_status=body.target_status,
    #       payload=config.payload,
    #       simulation_results=body.simulation_results,
    #       caller_roles=user.roles,
    #   )
    #
    # If validate_transition raises, translate to HTTP 400/403 as below.
    try:
        # Placeholder: no DB record yet, so we raise 404.
        # When persistence lands, remove this raise and load the real record.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Configuration {config_id} not found.",
        )
    except InvalidTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except InsufficientRoleError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc


# ---------------------------------------------------------------------------
# Archive / delete
# ---------------------------------------------------------------------------


@router.delete(
    "/{config_id}",
    response_model=MessageResponse,
    summary="Archive a configuration",
    responses={
        200: {"description": "Config archived."},
        404: {"description": "Config not found."},
        409: {"description": "Cannot archive a deployed config."},
        403: {"description": "Admin required."},
    },
)
async def archive_configuration(
    config_id: UUID,
    tenant_ctx: TenantCtx,
    db: DbDep,
    _user: Annotated[UserContext, Depends(require_roles("admin", "superadmin"))],
) -> MessageResponse:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Configuration {config_id} not found.",
    )
