"""Configuration generation and management routes."""

import io
import json
from collections import defaultdict

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query
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
)
from finspark.core.audit import AuditService
from finspark.core.database import get_db
from finspark.models.adapter import AdapterVersion
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
from finspark.services.lifecycle import IntegrationLifecycle, InvalidTransitionError
from finspark.services.simulation.simulator import IntegrationSimulator

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

        items.append(
            BatchValidationItem(
                config_id=cid,
                result=ConfigValidationResult(
                    is_valid=len(errors) == 0,
                    errors=errors,
                    warnings=warnings,
                    coverage_score=round(coverage, 2),
                    missing_required_fields=[],
                    unmapped_source_fields=unmapped,
                ),
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
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
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

    filename = f"{config.name.replace(' ', '_')}_{config.version}.{ext}"
    buffer = io.BytesIO(content.encode("utf-8"))

    return StreamingResponse(
        buffer,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/generate", response_model=APIResponse[ConfigurationResponse])
async def generate_configuration(
    request: GenerateConfigRequest,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
    generator: ConfigGenerator = Depends(get_config_generator),
    audit: AuditService = Depends(get_audit_service),
) -> APIResponse[ConfigurationResponse]:
    """Generate integration configuration from a parsed document and adapter."""
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

    # Generate config
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

    config = generator.generate(parsed_result, av_dict)

    # Save configuration
    configuration = Configuration(
        tenant_id=tenant.tenant_id,
        name=request.name,
        adapter_version_id=request.adapter_version_id,
        document_id=request.document_id,
        status="configured",
        version=1,
        field_mappings=json.dumps(config.get("field_mappings", [])),
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
        details={"adapter_version": adapter_version.version, "document": doc.filename},
    )

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
        message="Configuration generated successfully",
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
    errors: list[str] = []
    warnings: list[str] = []
    missing_required: list[str] = []

    # Validate structure
    if not full_config.get("base_url"):
        errors.append("Missing base_url")
    if not full_config.get("auth", {}).get("type"):
        errors.append("Missing auth configuration")
    if not full_config.get("endpoints"):
        errors.append("No endpoints configured")

    # Validate field mappings
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

    return APIResponse(
        data=ConfigValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            coverage_score=round(coverage, 2),
            missing_required_fields=missing_required,
            unmapped_source_fields=unmapped,
        ),
    )


@router.post("/{config_id}/transition", response_model=APIResponse[TransitionResponse])
async def transition_configuration(
    config_id: str,
    body: TransitionRequest,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
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
) -> APIResponse[list[ConfigurationResponse]]:
    """List all configurations for the current tenant."""
    stmt = (
        select(Configuration)
        .where(Configuration.tenant_id == tenant.tenant_id)
        .order_by(Configuration.created_at.desc())
    )
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
