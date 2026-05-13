"""Adapter registry routes."""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from finspark.models.adapter import Adapter as AdapterModel, AdapterVersion as AdapterVersionModel

from finspark.api.dependencies import get_adapter_registry, get_deprecation_tracker, get_tenant_context
from finspark.core.database import get_db
from finspark.core.json_utils import safe_json_loads
from finspark.models.document import Document
from finspark.schemas.adapters import (
    AdapterEndpoint,
    AdapterListResponse,
    AdapterResponse,
    AdapterVersionResponse,
    DeprecationInfoResponse,
    MigrationStep,
)
from finspark.schemas.common import APIResponse, TenantContext
from finspark.schemas.documents import ParsedDocumentResult
from finspark.services.registry.adapter_registry import AdapterRegistry
from finspark.services.registry.deprecation import DeprecationTracker

router = APIRouter(prefix="/adapters", tags=["Adapters"])


def compute_adapter_match_score(
    parsed_result: dict[str, Any],
    adapter_versions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return list of {adapter_name, version, score} sorted by score desc."""
    doc_fields = {f["name"] for f in parsed_result.get("fields", [])}
    doc_endpoints = {f["path"] for f in parsed_result.get("endpoints", [])}

    matches = []
    for av in adapter_versions:
        adapter_endpoints = {ep["path"] for ep in av.get("endpoints", [])}
        adapter_fields = set(av.get("request_schema", {}).get("properties", {}).keys())

        endpoint_overlap = len(doc_endpoints & adapter_endpoints) / max(len(doc_endpoints | adapter_endpoints), 1)
        field_overlap = len(doc_fields & adapter_fields) / max(len(doc_fields | adapter_fields), 1)
        score = endpoint_overlap * 0.6 + field_overlap * 0.4

        matches.append({
            "adapter_name": av["adapter_name"],
            "version": av["version"],
            "score": round(score, 2),
        })

    return sorted(matches, key=lambda x: x["score"], reverse=True)


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


@router.post("/from-document", response_model=APIResponse[AdapterResponse])
async def create_adapter_from_document(
    document_id: str = Query(...),
    name: str = Query(...),
    category: str = Query(default="custom"),
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
    registry: AdapterRegistry = Depends(get_adapter_registry),
) -> APIResponse[AdapterResponse]:
    """Create a new adapter and version from a parsed document."""
    stmt = select(Document).where(
        Document.id == document_id,
        Document.tenant_id == tenant.tenant_id,
    )
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if not doc.parsed_result:
        raise HTTPException(status_code=422, detail="Document has not been parsed yet")

    parsed = ParsedDocumentResult.model_validate_json(doc.parsed_result)

    # Resolve base_url with strict URL validation — earlier code blindly trusted
    # parsed.sections["servers"], which the LLM parser fills with description prose.
    from urllib.parse import urlparse

    def _is_url(v: str) -> bool:
        if not v or " " in v:
            return False
        if not (v.startswith("http://") or v.startswith("https://")):
            return False
        try:
            u = urlparse(v)
            return bool(u.netloc) and "." in u.netloc
        except Exception:  # noqa: BLE001
            return False

    # 1. Prefer the structured base_url field populated by the parser.
    base_url = (parsed.base_url or "").strip()

    # 2. Fall back to whichever sections key holds a real URL (legacy data).
    if not _is_url(base_url):
        for key in ("base_urls", "base_url", "servers"):
            candidate = (parsed.sections.get(key, "") or "").split(",")[0].strip()
            if _is_url(candidate):
                base_url = candidate
                break

    # 3. Last resort: infer from a fully-qualified endpoint path.
    if not _is_url(base_url) and parsed.endpoints:
        first_path = parsed.endpoints[0].path
        if first_path.startswith("http"):
            u = urlparse(first_path)
            base_url = f"{u.scheme}://{u.netloc}"

    # 4. Never persist descriptive prose into base_url.
    if not _is_url(base_url):
        base_url = ""

    # Extract auth_type from first auth requirement
    auth_type = "api_key"
    if parsed.auth_requirements:
        auth_type = parsed.auth_requirements[0].auth_type

    # Build endpoints list with per-endpoint fields
    endpoints = []
    for ep in parsed.endpoints:
        ep_key = f"{ep.method} {ep.path}".lower()
        ep_req = [f.name for f in parsed.fields if "request" in f.source_section.lower() and ep_key in f.source_section.lower()]
        ep_resp = [f.name for f in parsed.fields if "response" in f.source_section.lower() and ep_key in f.source_section.lower()]
        endpoints.append({
            "path": ep.path,
            "method": ep.method,
            "description": ep.description,
            "request_fields": ep_req,
            "response_fields": ep_resp,
        })

    # Build request_schema from all request fields
    request_fields = [f for f in parsed.fields if "request" in f.source_section.lower()]
    # If no source_section info, use all fields
    if not request_fields:
        request_fields = list(parsed.fields)
    request_schema: dict[str, Any] = {}
    if request_fields:
        request_schema = {
            "type": "object",
            "properties": {
                f.name: {"type": f.data_type, "description": f.description}
                for f in request_fields
            },
            "required": [f.name for f in request_fields if f.is_required],
        }

    # Build response_schema from response fields
    response_fields = [f for f in parsed.fields if "response" in f.source_section.lower()]
    response_schema: dict[str, Any] | None = None
    if response_fields:
        response_schema = {
            "type": "object",
            "properties": {
                f.name: {"type": f.data_type, "description": f.description}
                for f in response_fields
            },
        }

    description = parsed.title or f"Auto-generated from {doc.filename}"

    adapter = await registry.create_adapter(
        name=name,
        category=category,
        description=description,
    )

    av = await registry.add_version(
        adapter_id=adapter.id,
        version="v1",
        base_url=base_url,
        auth_type=auth_type,
        endpoints=endpoints,
        request_schema=request_schema if request_schema else None,
        response_schema=response_schema,
        changelog=f"Auto-created from document '{doc.filename}'",
    )

    return APIResponse(
        data=AdapterResponse(
            id=adapter.id,
            name=adapter.name,
            category=adapter.category,
            description=adapter.description,
            is_active=adapter.is_active,
            icon=adapter.icon,
            versions=[
                AdapterVersionResponse(
                    id=av.id,
                    version=av.version,
                    status=av.status,
                    auth_type=av.auth_type,
                    base_url=av.base_url,
                    endpoints=[AdapterEndpoint(**ep) for ep in endpoints],
                    changelog=av.changelog,
                )
            ],
            created_at=adapter.created_at,
        ),
        message=f"Adapter '{name}' created with {len(endpoints)} endpoints",
    )


class AdapterVersionPatch(BaseModel):
    """Mutable fields on an adapter version."""
    base_url: str | None = None


@router.patch("/{adapter_id}/versions/{version_id}", response_model=APIResponse[AdapterVersionResponse])
async def patch_adapter_version(
    adapter_id: str,
    version_id: str,
    body: AdapterVersionPatch,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[AdapterVersionResponse]:
    """Update mutable fields on an adapter version (currently: base_url).

    Used when an auto-generated adapter has a broken base_url and the user
    needs to correct it without re-uploading the source document.
    """
    stmt = select(AdapterVersionModel).where(
        AdapterVersionModel.id == version_id,
        AdapterVersionModel.adapter_id == adapter_id,
    )
    result = await db.execute(stmt)
    av = result.scalar_one_or_none()
    if not av:
        raise HTTPException(status_code=404, detail="Adapter version not found")

    if body.base_url is not None:
        new_url = body.base_url.strip()
        if new_url:
            from urllib.parse import urlparse
            if " " in new_url or not (new_url.startswith("http://") or new_url.startswith("https://")):
                raise HTTPException(status_code=400, detail="base_url must be an absolute http(s) URL")
            u = urlparse(new_url)
            if not u.netloc or "." not in u.netloc:
                raise HTTPException(status_code=400, detail="base_url must include a valid host")
        av.base_url = new_url or None
        await db.flush()

    endpoints_json = safe_json_loads(av.endpoints or "[]", default=[])
    return APIResponse(
        data=AdapterVersionResponse(
            id=av.id,
            version=av.version,
            status=av.status,
            auth_type=av.auth_type,
            base_url=av.base_url,
            endpoints=[AdapterEndpoint(**e) for e in endpoints_json],
        ),
        message="Adapter version updated",
    )


@router.delete("/{adapter_id}", response_model=APIResponse[dict])
async def delete_adapter(
    adapter_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[dict]:
    """Delete an adapter and all its versions."""
    stmt = select(AdapterModel).where(AdapterModel.id == adapter_id)
    result = await db.execute(stmt)
    adapter = result.scalar_one_or_none()

    if not adapter:
        raise HTTPException(status_code=404, detail="Adapter not found")

    adapter_name = adapter.name
    await db.delete(adapter)
    await db.flush()

    return APIResponse(
        data={"id": adapter_id, "deleted": True},
        message=f"Adapter '{adapter_name}' deleted",
    )
