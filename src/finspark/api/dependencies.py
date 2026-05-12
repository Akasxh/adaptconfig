"""FastAPI dependencies for dependency injection."""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.core.audit import AuditService
from finspark.core.database import get_db
from finspark.schemas.common import TenantContext
from finspark.services.config_engine.diff_engine import ConfigDiffEngine
from finspark.services.config_engine.field_mapper import ConfigGenerator
from finspark.services.config_engine.rollback import RollbackManager
from finspark.services.parsing.document_parser import DocumentParser
from finspark.services.registry.adapter_registry import AdapterRegistry
from finspark.services.registry.deprecation import DeprecationTracker
from finspark.services.simulation.simulator import IntegrationSimulator


def get_tenant_context(request: Request) -> TenantContext:
    return TenantContext(
        tenant_id=getattr(request.state, "tenant_id", "default"),
        tenant_name=getattr(request.state, "tenant_name", "Default"),
        role=getattr(request.state, "role", "viewer"),
    )


def require_role(*allowed_roles: str) -> Depends:
    """Dependency factory that enforces role-based access control."""

    def dependency(request: Request) -> TenantContext:
        tenant = get_tenant_context(request)
        if tenant.role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return tenant

    return Depends(dependency)


def get_document_parser() -> DocumentParser:
    return DocumentParser()


def get_config_generator() -> ConfigGenerator:
    return ConfigGenerator()


def get_diff_engine() -> ConfigDiffEngine:
    return ConfigDiffEngine()


def get_simulator() -> IntegrationSimulator:
    return IntegrationSimulator()


async def get_adapter_registry(db: AsyncSession = Depends(get_db)) -> AdapterRegistry:
    return AdapterRegistry(db)


async def get_deprecation_tracker(db: AsyncSession = Depends(get_db)) -> DeprecationTracker:
    return DeprecationTracker(db)


async def get_rollback_manager(db: AsyncSession = Depends(get_db)) -> RollbackManager:
    return RollbackManager(db)


async def get_audit_service(db: AsyncSession = Depends(get_db)) -> AuditService:
    return AuditService(db)
