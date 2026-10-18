"""Natural language search route for integrations."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from finspark.api.dependencies import get_tenant_context
from finspark.core.database import get_db
from finspark.schemas.common import APIResponse, TenantContext
from finspark.services.llm.client import is_llm_ready
from finspark.services.search import IntegrationSearch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["Search"])


@router.get("/", response_model=APIResponse[dict[str, Any]])
async def search_integrations(
    q: str = Query(..., min_length=1, description="Natural language search query"),
    tenant: TenantContext = Depends(get_tenant_context),
    db: Any = Depends(get_db),
) -> APIResponse[dict[str, Any]]:
    """Search adapters, configurations, and simulations using natural language."""
    search_service = IntegrationSearch(db)

    use_llm = is_llm_ready()
    if use_llm:
        try:
            from finspark.services.llm.client import get_llm_client

            client = get_llm_client()
            results = await search_service.search_with_llm(
                query=q, tenant_id=tenant.tenant_id, client=client,
            )
        except Exception:
            logger.warning("LLM search failed, falling back to rule-based", exc_info=True)
            results = await search_service.search(query=q, tenant_id=tenant.tenant_id)
    else:
        results = await search_service.search(query=q, tenant_id=tenant.tenant_id)

    def _result_to_dict(r: Any) -> dict[str, Any]:
        return {
            "type": r.type,
            "id": r.id,
            "name": r.name,
            "score": r.score,
            "details": r.details,
        }

    return APIResponse(
        data={
            "query": results.query,
            "total": results.total,
            "adapters": [_result_to_dict(r) for r in results.adapters],
            "configurations": [_result_to_dict(r) for r in results.configurations],
            "simulations": [_result_to_dict(r) for r in results.simulations],
        },
    )
