"""Tests for AnalyticsService.get_dashboard_metrics including chart data fields."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.audit import AuditLog
from finspark.models.configuration import Configuration
from finspark.models.document import Document
from finspark.models.simulation import Simulation
from finspark.services.analytics import AnalyticsService

TENANT = "test-tenant"


async def _seed_data(db: AsyncSession) -> None:
    """Insert sample rows for analytics queries."""
    now = datetime.now(UTC)

    # Documents
    doc = Document(
        id="doc-1",
        tenant_id=TENANT,
        filename="test.yaml",
        file_type="yaml",
        doc_type="api_spec",
        status="parsed",
    )
    db.add(doc)

    # Configurations — one active, one draft (warning)
    cfg_active = Configuration(
        id="cfg-1",
        tenant_id=TENANT,
        name="Active Config",
        adapter_version_id="av-1",
        status="active",
    )
    cfg_draft = Configuration(
        id="cfg-2",
        tenant_id=TENANT,
        name="Draft Config",
        adapter_version_id="av-1",
        status="draft",
    )
    db.add_all([cfg_active, cfg_draft])

    # Simulations — one passed, one failed (warning)
    sim_pass = Simulation(
        id="sim-1",
        tenant_id=TENANT,
        configuration_id="cfg-1",
        status="passed",
        duration_ms=150,
    )
    sim_fail = Simulation(
        id="sim-2",
        tenant_id=TENANT,
        configuration_id="cfg-1",
        status="failed",
        duration_ms=200,
    )
    db.add_all([sim_pass, sim_fail])

    # Audit logs — recent entries for weekly_activity and throughput
    for i, rtype in enumerate(["document", "simulation", "configuration"]):
        entry = AuditLog(
            id=f"audit-{i}",
            tenant_id=TENANT,
            actor="system",
            action="create",
            resource_type=rtype,
            resource_id=f"res-{i}",
            created_at=now - timedelta(hours=i),
        )
        db.add(entry)

    await db.commit()


@pytest.mark.asyncio
async def test_dashboard_metrics_returns_all_fields(db_session: AsyncSession) -> None:
    await _seed_data(db_session)
    svc = AnalyticsService(db_session, TENANT)
    metrics = await svc.get_dashboard_metrics()

    # Original fields present
    assert "configurations" in metrics
    assert "simulations" in metrics
    assert "documents" in metrics
    assert "audit_entries" in metrics
    assert "health_score" in metrics

    # New chart fields present
    assert "weekly_activity" in metrics
    assert "throughput" in metrics
    assert "total_processed" in metrics
    assert "total_warnings" in metrics


@pytest.mark.asyncio
async def test_total_processed_counts_docs_and_configs(db_session: AsyncSession) -> None:
    await _seed_data(db_session)
    svc = AnalyticsService(db_session, TENANT)
    metrics = await svc.get_dashboard_metrics()

    # 1 document + 2 configurations = 3
    assert metrics["total_processed"] == 3


@pytest.mark.asyncio
async def test_total_warnings_counts_draft_and_failed(db_session: AsyncSession) -> None:
    await _seed_data(db_session)
    svc = AnalyticsService(db_session, TENANT)
    metrics = await svc.get_dashboard_metrics()

    # 1 draft config + 1 failed simulation = 2
    assert metrics["total_warnings"] == 2


@pytest.mark.asyncio
async def test_weekly_activity_has_seven_days(db_session: AsyncSession) -> None:
    await _seed_data(db_session)
    svc = AnalyticsService(db_session, TENANT)
    metrics = await svc.get_dashboard_metrics()

    activity = metrics["weekly_activity"]
    assert len(activity) == 7
    assert activity[0]["name"] == "Mon"
    assert activity[6]["name"] == "Sun"
    for day in activity:
        assert "documents" in day
        assert "simulations" in day


@pytest.mark.asyncio
async def test_throughput_has_six_buckets(db_session: AsyncSession) -> None:
    await _seed_data(db_session)
    svc = AnalyticsService(db_session, TENANT)
    metrics = await svc.get_dashboard_metrics()

    tp = metrics["throughput"]
    assert len(tp) == 6
    assert tp[0]["hour"] == "00:00"
    assert tp[-1]["hour"] == "20:00"
    for bucket in tp:
        assert "records" in bucket


@pytest.mark.asyncio
async def test_health_score_range(db_session: AsyncSession) -> None:
    await _seed_data(db_session)
    svc = AnalyticsService(db_session, TENANT)
    metrics = await svc.get_dashboard_metrics()

    score = metrics["health_score"]
    assert 0 <= score <= 100


@pytest.mark.asyncio
async def test_empty_tenant_returns_defaults(db_session: AsyncSession) -> None:
    svc = AnalyticsService(db_session, "empty-tenant")
    metrics = await svc.get_dashboard_metrics()

    assert metrics["total_processed"] == 0
    assert metrics["total_warnings"] == 0
    assert len(metrics["weekly_activity"]) == 7
    assert len(metrics["throughput"]) == 6
    assert metrics["health_score"] == 50.0
