"""Adapter registry routes."""

import json

from fastapi import APIRouter, Depends, HTTPException

from finspark.api.dependencies import get_adapter_registry, get_deprecation_tracker
from finspark.core.json_utils import safe_json_loads
from finspark.schemas.adapters import (
    AdapterEndpoint,
    AdapterListResponse,
    AdapterResponse,
    AdapterVersionResponse,
    DeprecationInfoResponse,
    MigrationStep,
)
from finspark.schemas.common import APIResponse
from finspark.services.registry.adapter_registry import AdapterRegistry
from finspark.services.registry.deprecation import DeprecationTracker

router = APIRouter(prefix="/adapters", tags=["Adapters"])


@router.get("/", response_model=APIResponse[AdapterListResponse])
async def list_adapters(
    category: str | None = None,
    registry: AdapterRegistry = Depends(get_adapter_registry),
) -> APIResponse[AdapterListResponse]:
    """List all available integration adapters."""
    adapters = await registry.list_adapters(category=category)
    categories = await registry.get_categories()

    adapter_responses = []
    for a in adapters:
        versions = []
        for v in a.versions:
            endpoints = safe_json_loads(v.endpoints, [])
            versions.append(
                AdapterVersionResponse(
                    id=v.id,
                    version=v.version,
                    status=v.status,
                    auth_type=v.auth_type,
                    base_url=v.base_url,
                    endpoints=[AdapterEndpoint(**ep) for ep in endpoints],
                    changelog=v.changelog,
                )
            )

        adapter_responses.append(
            AdapterResponse(
                id=a.id,
                name=a.name,
                category=a.category,
                description=a.description,
                is_active=a.is_active,
                icon=a.icon,
                versions=versions,
                created_at=a.created_at,
            )
        )

    return APIResponse(
        data=AdapterListResponse(
            adapters=adapter_responses,
            total=len(adapter_responses),
            categories=categories,
        ),
    )


@router.get("/{adapter_id}", response_model=APIResponse[AdapterResponse])
async def get_adapter(
    adapter_id: str,
    registry: AdapterRegistry = Depends(get_adapter_registry),
) -> APIResponse[AdapterResponse]:
    """Get adapter details with all versions."""
    adapter = await registry.get_adapter(adapter_id)
    if not adapter:
        raise HTTPException(status_code=404, detail="Adapter not found")

    versions = []
    for v in adapter.versions:
        endpoints = json.loads(v.endpoints) if v.endpoints else []
        versions.append(
            AdapterVersionResponse(
                id=v.id,
                version=v.version,
                status=v.status,
                auth_type=v.auth_type,
                base_url=v.base_url,
                endpoints=[AdapterEndpoint(**ep) for ep in endpoints],
                changelog=v.changelog,
            )
        )

    return APIResponse(
        data=AdapterResponse(
            id=adapter.id,
            name=adapter.name,
            category=adapter.category,
            description=adapter.description,
            is_active=adapter.is_active,
            icon=adapter.icon,
            versions=versions,
            created_at=adapter.created_at,
        ),
    )


@router.get(
    "/{adapter_id}/versions/{version}/deprecation",
    response_model=APIResponse[DeprecationInfoResponse],
)
async def get_version_deprecation(
    adapter_id: str,
    version: str,
    tracker: DeprecationTracker = Depends(get_deprecation_tracker),
) -> APIResponse[DeprecationInfoResponse]:
    """Get deprecation info, sunset date, and migration guide for an adapter version."""
    health = await tracker.check_version_health(adapter_id, version)

    if health["status"] == "not_found":
        raise HTTPException(status_code=404, detail="Adapter version not found")

    migration_steps: list[MigrationStep] = []
    if health["replacement_version"]:
        guide = await tracker.get_migration_guide(
            adapter_id, version, health["replacement_version"]
        )
        migration_steps = [MigrationStep(**s) for s in guide.get("steps", [])]

    return APIResponse(
        data=DeprecationInfoResponse(
            version=version,
            status=health["status"],
            sunset_date=health.get("sunset_date"),
            days_until_sunset=health.get("days_until_sunset"),
            replacement_version=health.get("replacement_version"),
            migration_guide=migration_steps,
        ),
    )


@router.get("/{adapter_id}/match", response_model=APIResponse[list[str]])
async def find_matching_adapters(
    services: str,  # comma-separated service names
    registry: AdapterRegistry = Depends(get_adapter_registry),
) -> APIResponse[list[str]]:
    """Find adapters matching identified services."""
    service_list = [s.strip() for s in services.split(",")]
    matched = await registry.find_matching_adapters(service_list)
    return APIResponse(data=[a.name for a in matched])
