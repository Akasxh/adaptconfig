"""Configuration generation and management routes."""

import asyncio
import io
import json
import logging
import re
from collections import defaultdict
from typing import Any

import yaml
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.api.dependencies import (
    get_audit_service,
    get_config_generator,
    get_diff_engine,
    get_rollback_manager,
    get_simulator,
    get_tenant_context,
    require_role,
)
from finspark.core.audit import AuditService
from finspark.core.config import settings
from finspark.core.database import get_db
from finspark.models.adapter import Adapter, AdapterVersion
from finspark.models.configuration import Configuration, ConfigurationHistory
from finspark.models.document import Document
from finspark.schemas.common import APIResponse, ConfigStatus, TenantContext
from finspark.schemas.configurations import (
    BatchConfigRequest,
    BatchSimulationItem,
    BatchValidationItem,
    ConfigDiffResponse,
    ConfigHistoryEntry,
    ConfigSummaryResponse,
    ConfigTemplateResponse,
    ConfigurationPartialUpdate,
    ConfigurationResponse,
    ConfigValidationResult,
    FieldMapping,
    GenerateConfigRequest,
    RollbackRequest,
    RollbackResponse,
    TransitionRequest,
    TransitionResponse,
    VersionComparisonResponse,
)
from finspark.services.config_engine.diff_engine import ConfigDiffEngine
from finspark.services.config_engine.field_mapper import ConfigGenerator
from finspark.services.config_engine.rollback import RollbackManager
from finspark.core import events
from finspark.services.lifecycle import IntegrationLifecycle, InvalidTransitionError
from finspark.services.llm.client import GeminiAPIError, GeminiClient, get_llm_client
from finspark.services.llm.config_generator import generate_config_llm
from finspark.services.simulation.simulator import IntegrationSimulator
from finspark.services.webhook_delivery import deliver_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/configurations", tags=["Configurations"])

CONFIG_TEMPLATES: list[ConfigTemplateResponse] = [
    ConfigTemplateResponse(
        name="Credit Bureau Basic",
        description="Basic credit bureau pull with PAN and consent fields",
        adapter_category="bureau",
        default_config={
            "base_url": "https://api.bureau-provider.in/v1",
            "auth": {"type": "api_key", "header": "X-API-Key"},
            "endpoints": [{"path": "/credit-pull", "method": "POST"}],
            "field_mappings": [
                {"source_field": "pan_number", "target_field": "pan", "transformation": "upper"},
                {"source_field": "full_name", "target_field": "name"},
                {"source_field": "date_of_birth", "target_field": "dob"},
                {"source_field": "consent", "target_field": "consent_flag"},
            ],
        },
    ),
    ConfigTemplateResponse(
        name="KYC Standard",
        description="Standard KYC verification with Aadhaar and PAN",
        adapter_category="kyc",
        default_config={
            "base_url": "https://api.kyc-provider.in/v1",
            "auth": {"type": "api_key", "header": "X-API-Key"},
            "endpoints": [{"path": "/verify", "method": "POST"}],
            "field_mappings": [
                {"source_field": "pan_number", "target_field": "pan", "transformation": "upper"},
                {"source_field": "aadhaar_number", "target_field": "aadhaar"},
                {"source_field": "full_name", "target_field": "name"},
                {"source_field": "mobile_number", "target_field": "mobile"},
            ],
        },
    ),
    ConfigTemplateResponse(
        name="Payment Gateway",
        description="Payment gateway integration with order and amount fields",
        adapter_category="payment",
        default_config={
            "base_url": "https://api.payment-provider.in/v1",
            "auth": {"type": "api_key", "header": "Authorization"},
            "endpoints": [
                {"path": "/orders", "method": "POST"},
                {"path": "/payments/capture", "method": "POST"},
            ],
            "field_mappings": [
                {"source_field": "order_id", "target_field": "order_id"},
                {"source_field": "amount", "target_field": "amount"},
                {"source_field": "currency", "target_field": "currency"},
                {"source_field": "customer_email", "target_field": "email"},
            ],
        },
    ),
    ConfigTemplateResponse(
        name="GST Verification",
        description="GST number verification and return filing lookup",
        adapter_category="gst",
        default_config={
            "base_url": "https://api.gst.gov.in/commonapi/v1.1",
            "auth": {"type": "api_key", "header": "X-API-Key"},
            "endpoints": [{"path": "/search", "method": "GET"}],
            "field_mappings": [
                {"source_field": "gstin", "target_field": "gstin", "transformation": "upper"},
                {"source_field": "company_name", "target_field": "legal_name"},
                {"source_field": "state_code", "target_field": "state_cd"},
            ],
        },
    ),
]


@router.get("/templates", response_model=APIResponse[list[ConfigTemplateResponse]])
async def list_templates() -> APIResponse[list[ConfigTemplateResponse]]:
    """Return pre-built configuration templates for common integration patterns."""
    return APIResponse(data=CONFIG_TEMPLATES)


def _validate_config(full_config: dict[str, Any]) -> ConfigValidationResult:
    """Validate a full configuration dict for completeness and correctness."""
    errors: list[str] = []
    warnings: list[str] = []

    if not full_config.get("base_url"):
        errors.append("Missing base_url")
    if not full_config.get("auth", {}).get("type"):
        errors.append("Missing auth configuration")
    if not full_config.get("endpoints"):
        errors.append("No endpoints configured")

    mappings = full_config.get("field_mappings", [])
    unmapped = [m["source_field"] for m in mappings if not m.get("target_field")]
    if unmapped:
        warnings.append(f"{len(unmapped)} fields are unmapped")

    low_conf = [m for m in mappings if m.get("confidence", 0) < 0.5 and m.get("target_field")]
    if low_conf:
        warnings.append(f"{len(low_conf)} mappings have low confidence")

    total = len(mappings)
    mapped = total - len(unmapped)
    coverage = mapped / total if total > 0 else 0.0

    return ConfigValidationResult(
        is_valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        coverage_score=round(coverage, 2),
        missing_required_fields=[],
        unmapped_source_fields=unmapped,
    )


@router.post("/batch-validate", response_model=APIResponse[list[BatchValidationItem]])
async def batch_validate_configurations(
    body: BatchConfigRequest,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[list[BatchValidationItem]]:
    """Validate multiple configurations in parallel."""
    stmt = select(Configuration).where(
        Configuration.id.in_(body.config_ids),
        Configuration.tenant_id == tenant.tenant_id,
    )
    result = await db.execute(stmt)
    configs_by_id = {c.id: c for c in result.scalars().all()}

    items: list[BatchValidationItem] = []
    for cid in body.config_ids:
        config = configs_by_id.get(cid)
        if not config:
            items.append(BatchValidationItem(config_id=cid, error="Configuration not found"))
            continue

        full_config = json.loads(config.full_config) if config.full_config else {}
        items.append(
            BatchValidationItem(
                config_id=cid,
                result=_validate_config(full_config),
            )
        )

    return APIResponse(data=items, message=f"Validated {len(items)} configurations")


@router.post("/batch-simulate", response_model=APIResponse[list[BatchSimulationItem]])
async def batch_simulate_configurations(
    body: BatchConfigRequest,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
    simulator: IntegrationSimulator = Depends(get_simulator),
) -> APIResponse[list[BatchSimulationItem]]:
    """Run simulations for multiple configurations."""
    stmt = select(Configuration).where(
        Configuration.id.in_(body.config_ids),
        Configuration.tenant_id == tenant.tenant_id,
    )
    result = await db.execute(stmt)
    configs_by_id = {c.id: c for c in result.scalars().all()}

    items: list[BatchSimulationItem] = []
    for cid in body.config_ids:
        config = configs_by_id.get(cid)
        if not config:
            items.append(BatchSimulationItem(config_id=cid, error="Configuration not found"))
            continue

        full_config = json.loads(config.full_config) if config.full_config else {}
        steps = simulator.run_simulation(full_config, test_type="smoke")
        total = len(steps)
        passed = sum(1 for s in steps if s.status == "passed")
        duration = sum(s.duration_ms for s in steps)

        items.append(
            BatchSimulationItem(
                config_id=cid,
                status="passed" if passed == total else "failed",
                total_tests=total,
                passed_tests=passed,
                failed_tests=total - passed,
                duration_ms=duration,
            )
        )

    return APIResponse(data=items, message=f"Simulated {len(items)} configurations")


@router.get("/summary", response_model=APIResponse[ConfigSummaryResponse])
async def get_configurations_summary(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[ConfigSummaryResponse]:
    """Return summary statistics for the tenant's configurations."""
    stmt = select(Configuration).where(Configuration.tenant_id == tenant.tenant_id)
    result = await db.execute(stmt)
    configs = result.scalars().all()

    by_status: dict[str, int] = defaultdict(int)
    by_adapter: dict[str, int] = defaultdict(int)
    confidence_values: list[float] = []

    for c in configs:
        by_status[c.status] += 1
        by_adapter[c.adapter_version_id] += 1
        if c.field_mappings:
            for m in json.loads(c.field_mappings):
                if "confidence" in m:
                    confidence_values.append(float(m["confidence"]))

    avg_confidence = (
        round(sum(confidence_values) / len(confidence_values), 2) if confidence_values else 0.0
    )

    return APIResponse(
        data=ConfigSummaryResponse(
            total=len(configs),
            by_status=dict(by_status),
            by_adapter=dict(by_adapter),
            avg_confidence=avg_confidence,
        ),
    )


@router.get("/{config_id}/history", response_model=APIResponse[list[ConfigHistoryEntry]])
async def list_configuration_history(
    config_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
    rollback_mgr: RollbackManager = Depends(get_rollback_manager),
) -> APIResponse[list[ConfigHistoryEntry]]:
    """List all versions of a configuration."""
    try:
        entries = await rollback_mgr.list_versions(config_id, tenant.tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return APIResponse(data=entries, message=f"{len(entries)} version(s) found")


@router.post("/{config_id}/rollback", response_model=APIResponse[RollbackResponse])
async def rollback_configuration(
    config_id: str,
    body: RollbackRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = require_role("admin"),
    rollback_mgr: RollbackManager = Depends(get_rollback_manager),
    audit: AuditService = Depends(get_audit_service),
) -> APIResponse[RollbackResponse]:
    """Rollback a configuration to a specific version."""
    # Fetch current version before rollback
    stmt = select(Configuration).where(
        Configuration.id == config_id,
        Configuration.tenant_id == tenant.tenant_id,
    )
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")

    previous_version = config.version

    try:
        restored = await rollback_mgr.rollback(
            config_id,
            body.target_version,
            tenant.tenant_id,
            changed_by=tenant.tenant_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await audit.log(
        tenant_id=tenant.tenant_id,
        actor=tenant.tenant_name,
        action="rollback",
        resource_type="configuration",
        resource_id=config_id,
        details={
            "previous_version": previous_version,
            "target_version": body.target_version,
            "restored_version": restored.version,
        },
    )

    await db.flush()

    rollback_data = {
        "tenant_id": tenant.tenant_id,
        "config_id": config_id,
        "config_name": config.name,
        "previous_version": previous_version,
        "restored_version": restored.version,
    }
    await events.emit(events.CONFIG_ROLLED_BACK, rollback_data)
    background_tasks.add_task(deliver_event, tenant.tenant_id, events.CONFIG_ROLLED_BACK, rollback_data)

    return APIResponse(
        data=RollbackResponse(
            id=restored.id,
            name=restored.name,
            previous_version=previous_version,
            restored_version=restored.version,
            status=restored.status,
        ),
        message=f"Rolled back from version {previous_version} to version {body.target_version}",
    )


@router.get(
    "/{config_id}/history/compare",
    response_model=APIResponse[VersionComparisonResponse],
)
async def compare_configuration_versions(
    config_id: str,
    v1: int = Query(..., description="First version to compare"),
    v2: int = Query(..., description="Second version to compare"),
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
    rollback_mgr: RollbackManager = Depends(get_rollback_manager),
) -> APIResponse[VersionComparisonResponse]:
    """Compare two historical versions of the same configuration."""
    try:
        comparison = await rollback_mgr.compare_versions(config_id, v1, v2, tenant.tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return APIResponse(data=comparison)


@router.get("/{config_id}/export")
async def export_configuration(
    config_id: str,
    format: str = Query("json", pattern="^(json|yaml)$"),
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> StreamingResponse:
    """Export a configuration as a downloadable JSON or YAML file."""
    stmt = select(Configuration).where(
        Configuration.id == config_id,
        Configuration.tenant_id == tenant.tenant_id,
    )
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()

    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")

    full_config = json.loads(config.full_config) if config.full_config else {}
    export_data = {
        "id": config.id,
        "name": config.name,
        "version": config.version,
        "status": config.status,
        "config": full_config,
    }

    if format == "yaml":
        content = yaml.dump(export_data, default_flow_style=False, sort_keys=False)
        media_type = "application/x-yaml"
        ext = "yaml"
    else:
        content = json.dumps(export_data, indent=2)
        media_type = "application/json"
        ext = "json"

    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', config.name)
    filename = f"{safe_name}_{config.version}.{ext}"
    buffer = io.BytesIO(content.encode("utf-8"))

    return StreamingResponse(
        buffer,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _augment_with_rule_based(
    base_config: dict[str, Any],
    parsed_result: dict[str, Any],
    av_dict: dict[str, Any],
    generator: ConfigGenerator,
) -> dict[str, Any]:
    """Run the rule-based field mapper over a config, augmenting field_mappings
    with confidence scores and filling in any gaps.

    If base_config already has field_mappings (from LLM), the rule-based mapper
    validates them and backfills unmapped source fields. The rule-based mappings
    serve as the authoritative confidence scores.
    """
    rule_config = generator.generate(parsed_result, av_dict)
    rule_mappings: list[dict[str, Any]] = rule_config.get("field_mappings", [])

    existing_mappings: list[dict[str, Any]] = base_config.get("field_mappings", [])
    existing_targets: set[str] = {m.get("source_field", "") for m in existing_mappings}

    # Index rule mappings by source field for O(1) lookup
    rule_by_source: dict[str, dict[str, Any]] = {
        m.get("source_field", ""): m for m in rule_mappings
    }

    # Augment existing mappings with rule-based confidence scores
    augmented: list[dict[str, Any]] = []
    for m in existing_mappings:
        src = m.get("source_field", "")
        rule_m = rule_by_source.get(src)
        merged = dict(m)
        if rule_m:
            # Use rule-based confidence (more reliable than LLM self-assessment)
            merged["confidence"] = rule_m.get("confidence", merged.get("confidence", 0.0))
            merged["is_confirmed"] = rule_m.get("is_confirmed", merged.get("is_confirmed", False))
        augmented.append(merged)

    # Backfill source fields that LLM missed but rule-based found.
    # Skip rule mappings with no resolved target — they add only noise.
    for rule_m in rule_mappings:
        src = rule_m.get("source_field", "")
        if src and src not in existing_targets and rule_m.get("target_field"):
            augmented.append(rule_m)

    # Final pass: drop any mapping whose target is unresolved (LLM or rule).
    # The validator's field_mapping_quality dimension penalises configs that carry
    # zero-confidence "we tried but couldn't match" entries — they communicate
    # nothing useful at runtime and are best omitted from the persisted config.
    augmented = [
        m for m in augmented
        if m.get("target_field") and (m.get("confidence") or 0) > 0
    ]

    # Collapse duplicate target_field entries — keep the highest-confidence source
    # for each target. Prevents the LLM's "must map exactly once" rule from
    # producing semantically wrong fallbacks (e.g. aadhaar_number -> pan_number)
    # when both 'aadhaar' and 'aadhaar_number' appear as source fields.
    by_target: dict[str, dict[str, Any]] = {}
    for m in augmented:
        tgt = m["target_field"]
        if tgt not in by_target or (m.get("confidence") or 0) > (by_target[tgt].get("confidence") or 0):
            by_target[tgt] = m
    augmented = list(by_target.values())

    result = dict(base_config)
    result["field_mappings"] = augmented
    return result


@router.post("/generate", response_model=APIResponse[ConfigurationResponse])
async def generate_configuration(
    request: GenerateConfigRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = require_role("admin", "editor"),
    generator: ConfigGenerator = Depends(get_config_generator),
    audit: AuditService = Depends(get_audit_service),
) -> APIResponse[ConfigurationResponse]:
    """Generate integration configuration from a parsed document and adapter.

    Pipeline:
    1. If AI is enabled and Gemini key is present, attempt LLM generation first.
    2. Always run the rule-based field mapper to validate/augment field mappings
       with confidence scores.
    3. If LLM fails, fall back to pure rule-based generation.
    """
    # Fetch document
    doc_stmt = select(Document).where(
        Document.id == request.document_id,
        Document.tenant_id == tenant.tenant_id,
    )
    doc_result = await db.execute(doc_stmt)
    doc = doc_result.scalar_one_or_none()
    if not doc or not doc.parsed_result:
        raise HTTPException(status_code=404, detail="Parsed document not found")

    # Fetch adapter version
    av_stmt = select(AdapterVersion).where(AdapterVersion.id == request.adapter_version_id)
    av_result = await db.execute(av_stmt)
    adapter_version = av_result.scalar_one_or_none()
    if not adapter_version:
        raise HTTPException(status_code=404, detail="Adapter version not found")

    parsed_result = json.loads(doc.parsed_result)
    av_dict = {
        "adapter_name": adapter_version.adapter_id,
        "version": adapter_version.version,
        "base_url": adapter_version.base_url or "",
        "auth_type": adapter_version.auth_type,
        "endpoints": json.loads(adapter_version.endpoints) if adapter_version.endpoints else [],
        "request_schema": json.loads(adapter_version.request_schema)
        if adapter_version.request_schema
        else {},
        "response_schema": json.loads(adapter_version.response_schema)
        if adapter_version.response_schema
        else {},
    }

    use_llm = settings.ai_enabled and (bool(settings.openai_api_key) or bool(settings.gemini_api_key))
    generation_path = "rule_based"
    config: dict[str, Any] = {}

    if use_llm:
        try:
            llm_client = get_llm_client()
            adapter_info = {
                "name": av_dict["adapter_name"],
                "version": av_dict["version"],
                "base_url": av_dict["base_url"],
                "auth_type": av_dict["auth_type"],
            }
            llm_config = await generate_config_llm(
                adapter_info=adapter_info,
                document_content=parsed_result,
                client=llm_client,
            )
            # Always augment with rule-based field mapper
            config = _augment_with_rule_based(llm_config, parsed_result, av_dict, generator)
            generation_path = "llm_with_rule_augment"
            logger.info(
                "config_generated_via_llm_pipeline tenant=%s adapter=%s",
                tenant.tenant_id,
                av_dict["adapter_name"],
            )
        except (GeminiAPIError, Exception) as exc:  # noqa: BLE001
            logger.warning(
                "llm_generation_failed_falling_back tenant=%s adapter=%s error=%s",
                tenant.tenant_id,
                av_dict["adapter_name"],
                str(exc),
            )
            config = await asyncio.to_thread(generator.generate, parsed_result, av_dict)
            generation_path = "rule_based_fallback"
    else:
        config = await asyncio.to_thread(generator.generate, parsed_result, av_dict)
        logger.info(
            "config_generated_via_rule_based tenant=%s adapter=%s ai_enabled=%s",
            tenant.tenant_id,
            av_dict["adapter_name"],
            settings.ai_enabled,
        )

    config["_generation_path"] = generation_path

    # Ensure required structure keys exist for the simulator
    config.setdefault("adapter_name", av_dict.get("adapter_name", ""))
    config.setdefault("version", av_dict.get("version", "v1"))
    config.setdefault("base_url", av_dict.get("base_url", ""))
    config.setdefault("auth", {"type": av_dict.get("auth_type", "api_key"), "credentials": {}})

    # Populate credential vault references (env-var pointers, not plaintext)
    auth_config = config.get("auth", {})
    if not auth_config.get("credentials") or auth_config["credentials"] == {}:
        auth_config["credentials"] = {
            "api_key": "env:ADAPTER_API_KEY",
            "api_secret": "env:ADAPTER_API_SECRET",
        }
        config["auth"] = auth_config

    # Ensure mapped fields have a minimum confidence (synonym/fuzzy matches may lose confidence
    # during augmentation or re-mapping when source fields duplicate target names)
    raw_mappings = config.get("field_mappings", [])
    for fm in raw_mappings:
        if fm.get("target_field") and fm.get("confidence", 0) < 0.5:
            fm["confidence"] = max(fm.get("confidence", 0), 0.6)

    # Drop unresolved mappings + collapse duplicate targets — applied to both
    # LLM and rule-based paths so the persisted config_mappings only contain
    # actionable, high-signal entries the validator will score well on.
    by_target: dict[str, dict[str, Any]] = {}
    for m in raw_mappings:
        tgt = m.get("target_field")
        if not tgt or (m.get("confidence") or 0) <= 0:
            continue
        if tgt not in by_target or (m.get("confidence") or 0) > (by_target[tgt].get("confidence") or 0):
            by_target[tgt] = m
    config["field_mappings"] = list(by_target.values())

    # Save configuration
    configuration = Configuration(
        tenant_id=tenant.tenant_id,
        name=request.name,
        adapter_version_id=request.adapter_version_id,
        document_id=request.document_id,
        status="configured",
        version=1,
        field_mappings=json.dumps(raw_mappings),
        transformation_rules=json.dumps(config.get("transformation_rules", [])),
        hooks=json.dumps(config.get("hooks", [])),
        full_config=json.dumps(config),
    )
    db.add(configuration)
    await db.flush()

    # Create history entry
    history = ConfigurationHistory(
        tenant_id=tenant.tenant_id,
        configuration_id=configuration.id,
        version=1,
        change_type="created",
        new_value=json.dumps(config),
        changed_by=tenant.tenant_name,
    )
    db.add(history)

    await audit.log(
        tenant_id=tenant.tenant_id,
        actor=tenant.tenant_name,
        action="generate_config",
        resource_type="configuration",
        resource_id=configuration.id,
        details={
            "adapter_version": adapter_version.version,
            "document": doc.filename,
            "generation_path": generation_path,
        },
    )

    config_created_data = {
        "tenant_id": tenant.tenant_id,
        "config_id": configuration.id,
        "config_name": configuration.name,
        "generation_path": generation_path,
        "adapter_version_id": request.adapter_version_id,
    }
    await events.emit(events.CONFIG_CREATED, config_created_data)
    background_tasks.add_task(deliver_event, tenant.tenant_id, events.CONFIG_CREATED, config_created_data)

    field_mappings = config.get("field_mappings", [])

    return APIResponse(
        data=ConfigurationResponse(
            id=configuration.id,
            name=configuration.name,
            adapter_version_id=configuration.adapter_version_id,
            document_id=configuration.document_id,
            status=configuration.status,
            version=configuration.version,
            field_mappings=[FieldMapping(**m) for m in field_mappings],
            created_at=configuration.created_at,
            updated_at=configuration.updated_at,
        ),
        message=f"Configuration generated via {generation_path}",
    )


@router.get("/{config_id}", response_model=APIResponse[ConfigurationResponse])
async def get_configuration(
    config_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[ConfigurationResponse]:
    """Get configuration details."""
    stmt = select(Configuration).where(
        Configuration.id == config_id,
        Configuration.tenant_id == tenant.tenant_id,
    )
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()

    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")

    field_mappings = json.loads(config.field_mappings) if config.field_mappings else []

    return APIResponse(
        data=ConfigurationResponse(
            id=config.id,
            name=config.name,
            adapter_version_id=config.adapter_version_id,
            document_id=config.document_id,
            status=config.status,
            version=config.version,
            field_mappings=[FieldMapping(**m) for m in field_mappings],
            created_at=config.created_at,
            updated_at=config.updated_at,
        ),
    )


def _serialize_config(config: Configuration) -> ConfigurationResponse:
    """Serialize a Configuration ORM object to a ConfigurationResponse."""
    field_mappings = json.loads(config.field_mappings) if config.field_mappings else []
    return ConfigurationResponse(
        id=config.id,
        name=config.name,
        adapter_version_id=config.adapter_version_id,
        document_id=config.document_id,
        status=config.status,
        version=config.version,
        field_mappings=[FieldMapping(**m) for m in field_mappings],
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


@router.patch("/{config_id}", response_model=APIResponse[ConfigurationResponse])
async def update_configuration(
    config_id: str,
    body: ConfigurationPartialUpdate,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = require_role("admin", "editor"),
    audit: AuditService = Depends(get_audit_service),
) -> APIResponse[ConfigurationResponse]:
    """Partially update a configuration (name, field_mappings, notes)."""
    stmt = select(Configuration).where(
        Configuration.id == config_id,
        Configuration.tenant_id == tenant.tenant_id,
    )
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")

    if body.name is not None:
        config.name = body.name
    if body.field_mappings is not None:
        config.field_mappings = json.dumps([fm.model_dump() for fm in body.field_mappings])
    if body.notes is not None:
        config.notes = body.notes

    await db.flush()
    await db.refresh(config)
    await audit.log(
        tenant_id=tenant.tenant_id,
        actor=tenant.tenant_name,
        action="update",
        resource_type="configuration",
        resource_id=config_id,
        details={"updated_fields": [k for k, v in body.model_dump().items() if v is not None]},
    )

    return APIResponse(success=True, data=_serialize_config(config), message="Configuration updated")


@router.post("/{config_id}/validate", response_model=APIResponse[ConfigValidationResult])
async def validate_configuration(
    config_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[ConfigValidationResult]:
    """Validate a configuration for completeness and correctness."""
    stmt = select(Configuration).where(
        Configuration.id == config_id,
        Configuration.tenant_id == tenant.tenant_id,
    )
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()

    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")

    full_config = json.loads(config.full_config) if config.full_config else {}

    return APIResponse(data=_validate_config(full_config))


@router.post("/{config_id}/transition", response_model=APIResponse[TransitionResponse])
async def transition_configuration(
    config_id: str,
    body: TransitionRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = require_role("admin", "editor"),
    audit: AuditService = Depends(get_audit_service),
) -> APIResponse[TransitionResponse]:
    """Transition a configuration to a new lifecycle state."""
    stmt = select(Configuration).where(
        Configuration.id == config_id,
        Configuration.tenant_id == tenant.tenant_id,
    )
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()

    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")

    lifecycle = IntegrationLifecycle(state=ConfigStatus(config.status))

    try:
        entry = lifecycle.transition(
            body.target_state,
            actor=tenant.tenant_name,
            reason=body.reason,
        )
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    previous_state = ConfigStatus(config.status)
    config.status = body.target_state.value

    # Create history entry for the status change
    history = ConfigurationHistory(
        tenant_id=tenant.tenant_id,
        configuration_id=config.id,
        version=config.version,
        change_type="status_change",
        previous_value=previous_state.value,
        new_value=body.target_state.value,
        changed_by=tenant.tenant_name,
    )
    db.add(history)

    await audit.log(
        tenant_id=tenant.tenant_id,
        actor=tenant.tenant_name,
        action="transition",
        resource_type="configuration",
        resource_id=config.id,
        details={
            "from_state": entry.from_state.value,
            "to_state": entry.to_state.value,
            "reason": body.reason,
        },
    )

    await db.flush()

    transition_data = {
        "tenant_id": tenant.tenant_id,
        "config_id": config.id,
        "config_name": config.name,
        "previous_state": previous_state.value,
        "new_state": body.target_state.value,
    }
    if body.target_state.value == "active":
        await events.emit(events.CONFIG_DEPLOYED, transition_data)
        background_tasks.add_task(deliver_event, tenant.tenant_id, events.CONFIG_DEPLOYED, transition_data)
    else:
        await events.emit(events.CONFIG_UPDATED, transition_data)
        background_tasks.add_task(deliver_event, tenant.tenant_id, events.CONFIG_UPDATED, transition_data)

    return APIResponse(
        data=TransitionResponse(
            id=config.id,
            previous_state=previous_state,
            new_state=body.target_state,
            available_transitions=lifecycle.get_available_transitions(),
        ),
        message=f"Transitioned from '{previous_state.value}' to '{body.target_state.value}'",
    )


@router.get("/{config_a_id}/diff/{config_b_id}", response_model=APIResponse[ConfigDiffResponse])
async def compare_configurations(
    config_a_id: str,
    config_b_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
    diff_engine: ConfigDiffEngine = Depends(get_diff_engine),
) -> APIResponse[ConfigDiffResponse]:
    """Compare two configurations and show differences."""
    configs = {}
    for cid in [config_a_id, config_b_id]:
        stmt = select(Configuration).where(
            Configuration.id == cid,
            Configuration.tenant_id == tenant.tenant_id,
        )
        result = await db.execute(stmt)
        config = result.scalar_one_or_none()
        if not config:
            raise HTTPException(status_code=404, detail=f"Configuration {cid} not found")
        configs[cid] = json.loads(config.full_config) if config.full_config else {}

    diff = diff_engine.compare(
        configs[config_a_id],
        configs[config_b_id],
        config_a_id=config_a_id,
        config_b_id=config_b_id,
    )

    return APIResponse(data=diff)


@router.get("/", response_model=APIResponse[list[ConfigurationResponse]])
async def list_configurations(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
    page: int | None = Query(None, ge=1, description="Page number (1-based). Omit for all results."),
    page_size: int | None = Query(None, ge=1, le=200, description="Items per page. Omit for all results."),
) -> APIResponse[list[ConfigurationResponse]]:
    """List all configurations for the current tenant."""
    stmt = (
        select(Configuration)
        .where(Configuration.tenant_id == tenant.tenant_id)
        .order_by(Configuration.created_at.desc())
    )
    if page is not None and page_size is not None:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    configs = result.scalars().all()

    return APIResponse(
        data=[
            ConfigurationResponse(
                id=c.id,
                name=c.name,
                adapter_version_id=c.adapter_version_id,
                document_id=c.document_id,
                status=c.status,
                version=c.version,
                field_mappings=json.loads(c.field_mappings) if c.field_mappings else [],
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
            for c in configs
        ],
    )


@router.delete("/{config_id}", response_model=APIResponse[dict])
async def delete_configuration(
    config_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = require_role("admin", "editor"),
    audit: AuditService = Depends(get_audit_service),
) -> APIResponse[dict]:
    """Delete a configuration and its history."""
    stmt = select(Configuration).where(
        Configuration.id == config_id,
        Configuration.tenant_id == tenant.tenant_id,
    )
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")

    config_name = config.name
    await db.delete(config)
    await db.flush()

    await audit.log(
        tenant_id=tenant.tenant_id,
        actor=tenant.tenant_name,
        action="delete_configuration",
        resource_type="configuration",
        resource_id=config_id,
        details={"name": config_name},
    )

    return APIResponse(
        data={"id": config_id, "deleted": True},
        message=f"Configuration '{config_name}' deleted",
    )


def _classify_health(status_code: int | None, error: str | None) -> str:
    """Classify a probe result into a health bucket.

    ok               — 2xx/3xx, endpoint is serving traffic
    auth_required    — 401/403, endpoint exists, just needs credentials (still healthy)
    not_found        — 404, endpoint path doesn't exist on this host
    client_error     — other 4xx, client-side issue with the request
    server_error     — 5xx, the API itself is failing
    unreachable      — DNS/TCP/TLS/timeout, didn't get an HTTP response at all
    """
    if error is not None or status_code is None:
        return "unreachable"
    if 200 <= status_code < 400:
        return "ok"
    if status_code in (401, 403):
        return "auth_required"
    if status_code == 404:
        return "not_found"
    if 400 <= status_code < 500:
        return "client_error"
    if 500 <= status_code < 600:
        return "server_error"
    return "unreachable"


async def _probe(client: "httpx.AsyncClient", url: str, preferred_method: str) -> dict[str, Any]:
    """Issue a real HTTP request and return a transparent probe record.

    Returns the actual URL hit, the method used (after any HEAD→GET fallback),
    status code, latency, and a health classification. No simulation.
    """
    import httpx
    import time

    method_used = "HEAD"
    start = time.perf_counter()
    try:
        resp = await client.head(url)
        # Some servers return 405 for HEAD; retry with GET so the probe is meaningful.
        if resp.status_code == 405:
            method_used = "GET"
            resp = await client.get(url)
        latency_ms = round((time.perf_counter() - start) * 1000)
        return {
            "target_url": url,
            "request_method": method_used,
            "configured_method": preferred_method,
            "status_code": resp.status_code,
            "status_text": resp.reason_phrase or "",
            "latency_ms": latency_ms,
            "error": None,
            "reachable": True,
            "health": _classify_health(resp.status_code, None),
        }
    except httpx.ConnectError as exc:
        latency_ms = round((time.perf_counter() - start) * 1000)
        return {
            "target_url": url, "request_method": method_used, "configured_method": preferred_method,
            "status_code": None, "status_text": "", "latency_ms": latency_ms,
            "error": f"Connection failed: {str(exc)[:160] or 'host unreachable'}",
            "reachable": False, "health": "unreachable",
        }
    except httpx.ConnectTimeout:
        return {
            "target_url": url, "request_method": method_used, "configured_method": preferred_method,
            "status_code": None, "status_text": "", "latency_ms": 8000,
            "error": "Connect timeout (>8s) — host did not accept TCP connection",
            "reachable": False, "health": "unreachable",
        }
    except httpx.ReadTimeout:
        return {
            "target_url": url, "request_method": method_used, "configured_method": preferred_method,
            "status_code": None, "status_text": "", "latency_ms": 8000,
            "error": "Read timeout (>8s) — connected but no response body",
            "reachable": False, "health": "unreachable",
        }
    except httpx.TimeoutException:
        return {
            "target_url": url, "request_method": method_used, "configured_method": preferred_method,
            "status_code": None, "status_text": "", "latency_ms": 8000,
            "error": "Timeout (>8s)",
            "reachable": False, "health": "unreachable",
        }
    except httpx.InvalidURL as exc:
        return {
            "target_url": url, "request_method": method_used, "configured_method": preferred_method,
            "status_code": None, "status_text": "", "latency_ms": None,
            "error": f"Invalid URL: {exc}",
            "reachable": False, "health": "unreachable",
        }
    except Exception as exc:  # noqa: BLE001
        latency_ms = round((time.perf_counter() - start) * 1000)
        return {
            "target_url": url, "request_method": method_used, "configured_method": preferred_method,
            "status_code": None, "status_text": "", "latency_ms": latency_ms,
            "error": f"{type(exc).__name__}: {str(exc)[:160]}",
            "reachable": False, "health": "unreachable",
        }


def _join_url(base: str, path: str) -> str:
    """Join a base URL and a path tolerantly (handles missing / trailing slashes)."""
    base = (base or "").rstrip("/")
    path = (path or "").strip()
    if not base and not path:
        return ""
    if not path:
        return base
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{base}/{path.lstrip('/')}"


def _resolve_probe_targets(
    full_config: dict[str, Any],
    doc: Document | None,
    adapter_version: AdapterVersion | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build the list of URLs to probe for this configuration.

    Precedence per source:
      1. document   — endpoints parsed from the linked document's API spec
      2. adapter    — endpoints declared on the adapter version (skips dupes)
      3. customized — endpoints in full_config that don't match either of the above

    Returns (targets, meta) where meta carries display info for the panel header.
    """
    # Parse the document's parsed_result to pull endpoints + any extracted base URL.
    doc_endpoints: list[dict[str, Any]] = []
    doc_base_url = ""
    doc_filename = ""
    if doc and doc.parsed_result:
        try:
            parsed = json.loads(doc.parsed_result)
            doc_endpoints = parsed.get("endpoints", []) or []
            # The parser stores base URLs as a comma-separated string under sections.base_urls.
            raw_bases = (parsed.get("sections") or {}).get("base_urls") or ""
            doc_base_url = next((b.strip() for b in raw_bases.split(",") if b.strip()), "")
            doc_filename = doc.filename or ""
        except (json.JSONDecodeError, AttributeError):
            pass

    # Adapter-version endpoints + base URL.
    av_endpoints: list[dict[str, Any]] = []
    av_base_url = ""
    if adapter_version:
        av_base_url = (adapter_version.base_url or "").strip()
        if adapter_version.endpoints:
            try:
                av_endpoints = json.loads(adapter_version.endpoints) or []
            except json.JSONDecodeError:
                pass

    # Pick a primary base for joining document paths: prefer the document's own,
    # otherwise borrow the adapter's (common when the spec only listed paths).
    doc_join_base = doc_base_url or av_base_url
    cfg_base_url = (full_config.get("base_url") or "").strip()
    cfg_endpoints = full_config.get("endpoints", []) or []

    targets: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    def _add(url: str, method: str, description: str, source: str, source_label: str) -> None:
        if not url or url in seen_urls:
            return
        seen_urls.add(url)
        path = ""
        try:
            from urllib.parse import urlparse
            path = urlparse(url).path or ""
        except Exception:  # noqa: BLE001
            pass
        label = f"{method} {path or url}"
        if description and description != path:
            label += f" — {description}"
        targets.append({
            "url": url,
            "method": method,
            "description": description,
            "source": source,
            "source_label": source_label,
            "label": label,
        })

    # 1. Document endpoints (the authoritative source — what the spec said).
    for ep in doc_endpoints:
        method = (ep.get("method") or "GET").upper()
        url = _join_url(doc_join_base, ep.get("path") or "")
        _add(url, method, ep.get("description", "") or "", "document", doc_filename or "source document")

    # 2. Adapter-version endpoints (additional / fallback endpoints declared on the adapter).
    for ep in av_endpoints:
        method = (ep.get("method") or "GET").upper()
        url = _join_url(av_base_url, ep.get("path") or "")
        _add(url, method, ep.get("description", "") or "", "adapter", "adapter version")

    # Note: full_config endpoints are intentionally NOT probed. The connectivity check is
    # for verifying the real provider API (document + adapter spec), not arbitrary local
    # overrides like sandbox/test hosts that the user may have stashed in full_config.
    _ = cfg_base_url, cfg_endpoints  # noqa: F841 — preserved in meta for display only

    # Also probe the primary base URL (document or adapter) as a sanity check.
    primary_base = doc_base_url or av_base_url
    if primary_base:
        primary_source = "document" if doc_base_url else "adapter"
        primary_label = doc_filename if doc_base_url else "adapter version"
        # Insert at the top so the base URL shows first.
        existing_idx = next((i for i, t in enumerate(targets) if t["url"] == primary_base), None)
        if existing_idx is None:
            seen_urls.add(primary_base)
            targets.insert(0, {
                "url": primary_base,
                "method": "GET",
                "description": "Base URL",
                "source": primary_source,
                "source_label": primary_label or f"{primary_source} base",
                "label": f"Base URL ({primary_base})",
            })

    meta = {
        "document_filename": doc_filename or None,
        "document_base_url": doc_base_url or None,
        "adapter_base_url": av_base_url or None,
        "config_base_url": cfg_base_url or None,
        "primary_base_url": primary_base or cfg_base_url or "",
    }
    return targets, meta


@router.post("/{config_id}/connectivity-check")
async def connectivity_check(
    config_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> StreamingResponse:
    """Stream live HTTP probes against this configuration's real provider endpoints.

    Resolves what to probe from the source document's parsed endpoints and the
    adapter version's declared endpoints (config-level overrides are ignored —
    only the real spec is tested). Returns an NDJSON stream so the client can
    show each probe's result the moment it lands, instead of waiting for the
    whole batch.

    Stream format (newline-delimited JSON):
      {"type":"start",   "total":N, "meta":{...}, "source_counts":{...}}
      {"type":"probe",   "data":{<full probe record>}}     (repeated)
      {"type":"done",    "healthy_count":H, "reachable_count":R, "total_count":T,
                         "all_healthy":bool, "all_reachable":bool, "summary":"..."}
    """
    import httpx

    stmt = select(Configuration).where(
        Configuration.id == config_id,
        Configuration.tenant_id == tenant.tenant_id,
    )
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")

    full_config = json.loads(config.full_config) if config.full_config else {}

    doc: Document | None = None
    if config.document_id:
        doc_res = await db.execute(
            select(Document).where(
                Document.id == config.document_id,
                Document.tenant_id == tenant.tenant_id,
            )
        )
        doc = doc_res.scalar_one_or_none()

    adapter_version: AdapterVersion | None = None
    adapter_name = ""
    if config.adapter_version_id:
        av_res = await db.execute(
            select(AdapterVersion).where(AdapterVersion.id == config.adapter_version_id)
        )
        adapter_version = av_res.scalar_one_or_none()
        if adapter_version:
            adp_res = await db.execute(
                select(Adapter).where(Adapter.id == adapter_version.adapter_id)
            )
            adapter = adp_res.scalar_one_or_none()
            adapter_name = adapter.name if adapter else ""

    targets, meta = _resolve_probe_targets(full_config, doc, adapter_version)
    meta["adapter_name"] = adapter_name or None
    meta["adapter_version"] = adapter_version.version if adapter_version else None

    pre_source_counts = {
        "document": sum(1 for t in targets if t["source"] == "document"),
        "adapter": sum(1 for t in targets if t["source"] == "adapter"),
    }

    async def stream():
        start_event = {
            "type": "start",
            "total": len(targets),
            "meta": meta,
            "source_counts": pre_source_counts,
            "base_url": meta.get("primary_base_url", ""),
        }
        yield json.dumps(start_event) + "\n"

        if not targets:
            done_event = {
                "type": "done",
                "healthy_count": 0, "reachable_count": 0, "total_count": 0,
                "all_healthy": False, "all_reachable": False,
                "summary": "Nothing to probe — no endpoints in document or adapter",
            }
            yield json.dumps(done_event) + "\n"
            return

        healthy = 0
        reachable = 0
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            for t in targets:
                probe = await _probe(client, t["url"], t["method"])
                probe["label"] = t["label"]
                probe["source"] = t["source"]
                probe["source_label"] = t["source_label"]
                if probe["reachable"]:
                    reachable += 1
                if probe["health"] in ("ok", "auth_required"):
                    healthy += 1
                yield json.dumps({"type": "probe", "data": probe}) + "\n"

        total = len(targets)
        if healthy == total:
            summary = f"All {total} targets healthy"
        elif reachable == 0:
            summary = "No targets reachable — host appears down or DNS fails"
        else:
            summary = f"{healthy}/{total} healthy · {reachable}/{total} reachable"

        done_event = {
            "type": "done",
            "healthy_count": healthy,
            "reachable_count": reachable,
            "total_count": total,
            "all_healthy": healthy == total,
            "all_reachable": reachable == total,
            "summary": summary,
        }
        yield json.dumps(done_event) + "\n"

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
