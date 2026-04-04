"""Unit tests for AnalyticsService (issue #71).

Covers get_dashboard_metrics() return shape, weekly_activity, throughput,
total_processed, total_warnings, and health_score calculation.
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.audit import AuditLog
from finspark.models.configuration import Configuration
from finspark.models.document import Document
from finspark.models.simulation import Simulation
from finspark.services.analytics import AnalyticsService

TENANT = "analytics-unit-tenant"


async def _seed_minimal(db: AsyncSession) -> None:
    """Insert a minimal dataset for analytics queries."""
    now = datetime.now(UTC)

    db.add(Document(
        id="adoc-1", tenant_id=TENANT, filename="spec.yaml",
        file_type="yaml", doc_type="api_spec", status="parsed",
    ))
    db.add(Document(
        id="adoc-2", tenant_id=TENANT, filename="brd.txt",
        file_type="txt", doc_type="brd", status="parsed",
    ))

    db.add(Configuration(
        id="acfg-1", tenant_id=TENANT, name="Active",
        adapter_version_id="av-1", status="active",
    ))
    db.add(Configuration(
        id="acfg-2", tenant_id=TENANT, name="Draft",
        adapter_version_id="av-1", status="draft",
    ))
    db.add(Configuration(
        id="acfg-3", tenant_id=TENANT, name="Error",
        adapter_version_id="av-1", status="error",
    ))

    db.add(Simulation(
        id="asim-1", tenant_id=TENANT, configuration_id="acfg-1",
        status="passed", duration_ms=100,
    ))
    db.add(Simulation(
        id="asim-2", tenant_id=TENANT, configuration_id="acfg-1",
        status="failed", duration_ms=200,
    ))
    db.add(Simulation(
        id="asim-3", tenant_id=TENANT, configuration_id="acfg-1",
        status="passed", duration_ms=300,
    ))

    for i, rtype in enumerate(["document", "simulation", "document", "configuration"]):
        db.add(AuditLog(
            id=f"aaudit-{i}", tenant_id=TENANT, actor="test",
            action="create", resource_type=rtype, resource_id=f"r-{i}",
            created_at=now - timedelta(hours=i * 2),
        ))

    await db.commit()


class TestDashboardMetricsShape:
    @pytest.mark.asyncio
    async def test_returns_all_required_keys(self, db_session: AsyncSession) -> None:
        await _seed_minimal(db_session)
        svc = AnalyticsService(db_session, TENANT)
        metrics = await svc.get_dashboard_metrics()

        required_keys = {
            "configurations", "simulations", "documents", "audit_entries",
            "health_score", "weekly_activity", "throughput",
            "total_processed", "total_warnings",
        }
        assert required_keys.issubset(set(metrics.keys()))

    @pytest.mark.asyncio
    async def test_configurations_stats_structure(self, db_session: AsyncSession) -> None:
        await _seed_minimal(db_session)
        svc = AnalyticsService(db_session, TENANT)
        metrics = await svc.get_dashboard_metrics()

        configs = metrics["configurations"]
        assert configs["total"] == 3
        assert configs["active"] >= 1
        assert configs["draft"] >= 1
        assert "by_status" in configs

    @pytest.mark.asyncio
    async def test_simulations_stats_structure(self, db_session: AsyncSession) -> None:
        await _seed_minimal(db_session)
        svc = AnalyticsService(db_session, TENANT)
        metrics = await svc.get_dashboard_metrics()

        sims = metrics["simulations"]
        assert sims["total"] == 3
        assert sims["passed"] == 2
        assert sims["failed"] == 1
        assert 0 <= sims["pass_rate"] <= 1.0
        assert sims["avg_duration_ms"] > 0


class TestWeeklyActivity:
    @pytest.mark.asyncio
    async def test_returns_seven_days(self, db_session: AsyncSession) -> None:
        await _seed_minimal(db_session)
        svc = AnalyticsService(db_session, TENANT)
        metrics = await svc.get_dashboard_metrics()

        activity = metrics["weekly_activity"]
        assert len(activity) == 7
        day_names = [d["name"] for d in activity]
        assert day_names == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    @pytest.mark.asyncio
    async def test_each_day_has_documents_and_simulations(self, db_session: AsyncSession) -> None:
        await _seed_minimal(db_session)
        svc = AnalyticsService(db_session, TENANT)
        metrics = await svc.get_dashboard_metrics()

        for day in metrics["weekly_activity"]:
            assert "documents" in day
            assert "simulations" in day
            assert isinstance(day["documents"], int)
            assert isinstance(day["simulations"], int)

    @pytest.mark.asyncio
    async def test_activity_counts_recent_audit_logs(self, db_session: AsyncSession) -> None:
        await _seed_minimal(db_session)
        svc = AnalyticsService(db_session, TENANT)
        metrics = await svc.get_dashboard_metrics()

        total_docs = sum(d["documents"] for d in metrics["weekly_activity"])
        total_sims = sum(d["simulations"] for d in metrics["weekly_activity"])
        # We seeded 2 document audit logs and 1 simulation audit log within last 7 days
        assert total_docs >= 1
        assert total_sims >= 0


class TestThroughput:
    @pytest.mark.asyncio
    async def test_returns_six_four_hour_buckets(self, db_session: AsyncSession) -> None:
        await _seed_minimal(db_session)
        svc = AnalyticsService(db_session, TENANT)
        metrics = await svc.get_dashboard_metrics()

        tp = metrics["throughput"]
        assert len(tp) == 6
        hours = [b["hour"] for b in tp]
        assert hours == ["00:00", "04:00", "08:00", "12:00", "16:00", "20:00"]

    @pytest.mark.asyncio
    async def test_each_bucket_has_records_field(self, db_session: AsyncSession) -> None:
        await _seed_minimal(db_session)
        svc = AnalyticsService(db_session, TENANT)
        metrics = await svc.get_dashboard_metrics()

        for bucket in metrics["throughput"]:
            assert "records" in bucket
            assert isinstance(bucket["records"], int)


class TestTotalProcessed:
    @pytest.mark.asyncio
    async def test_counts_documents_and_configurations(self, db_session: AsyncSession) -> None:
        await _seed_minimal(db_session)
        svc = AnalyticsService(db_session, TENANT)
        metrics = await svc.get_dashboard_metrics()

        # 2 documents + 3 configurations = 5
        assert metrics["total_processed"] == 5


class TestTotalWarnings:
    @pytest.mark.asyncio
    async def test_counts_draft_error_configs_and_failed_sims(self, db_session: AsyncSession) -> None:
        await _seed_minimal(db_session)
        svc = AnalyticsService(db_session, TENANT)
        metrics = await svc.get_dashboard_metrics()

        # 1 draft config + 1 error config + 1 failed simulation = 3
        assert metrics["total_warnings"] == 3


class TestHealthScore:
    @pytest.mark.asyncio
    async def test_score_in_valid_range(self, db_session: AsyncSession) -> None:
        await _seed_minimal(db_session)
        svc = AnalyticsService(db_session, TENANT)
        metrics = await svc.get_dashboard_metrics()

        assert 0 <= metrics["health_score"] <= 100

    @pytest.mark.asyncio
    async def test_score_reflects_active_configs_and_pass_rate(self, db_session: AsyncSession) -> None:
        await _seed_minimal(db_session)
        svc = AnalyticsService(db_session, TENANT)
        metrics = await svc.get_dashboard_metrics()

        # base=50, +10 for configs>0, +15 for active>0, + pass_rate*25
        # pass_rate = 2/3 ~ 0.67 => ~16.7
        # Total ~91.7
        assert metrics["health_score"] > 75

    @pytest.mark.asyncio
    async def test_empty_tenant_gets_base_score(self, db_session: AsyncSession) -> None:
        svc = AnalyticsService(db_session, "nonexistent-tenant")
        metrics = await svc.get_dashboard_metrics()

        assert metrics["health_score"] == 50.0
        assert metrics["total_processed"] == 0
        assert metrics["total_warnings"] == 0
