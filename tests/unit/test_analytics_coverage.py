"""Comprehensive tests for AnalyticsService to boost coverage."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.audit import AuditLog
from finspark.models.configuration import Configuration
from finspark.models.document import Document
from finspark.models.simulation import Simulation
from finspark.services.analytics import AnalyticsService


TENANT = "test-tenant"


async def _seed_configs(db: AsyncSession, statuses: list[str]) -> None:
    """Insert configurations with given statuses."""
    for i, status in enumerate(statuses):
        cfg = Configuration(
            tenant_id=TENANT,
            name=f"cfg-{i}",
            adapter_version_id="av-1",
            status=status,
            version=1,
        )
        db.add(cfg)
    await db.flush()


async def _seed_simulations(
    db: AsyncSession,
    statuses: list[str],
    durations: list[int | None] | None = None,
) -> None:
    # Need a configuration first
    cfg = Configuration(
        tenant_id=TENANT,
        name="sim-parent",
        adapter_version_id="av-1",
        status="active",
        version=1,
    )
    db.add(cfg)
    await db.flush()

    for i, status in enumerate(statuses):
        sim = Simulation(
            tenant_id=TENANT,
            configuration_id=cfg.id,
            status=status,
            total_tests=5,
            passed_tests=5 if status == "passed" else 2,
            failed_tests=0 if status == "passed" else 3,
            duration_ms=durations[i] if durations else 100,
        )
        db.add(sim)
    await db.flush()


async def _seed_documents(db: AsyncSession, statuses: list[str]) -> None:
    for i, status in enumerate(statuses):
        doc = Document(
            tenant_id=TENANT,
            filename=f"doc-{i}.pdf",
            file_type="pdf",
            doc_type="brd",
            status=status,
        )
        db.add(doc)
    await db.flush()


async def _seed_audit_logs(
    db: AsyncSession,
    count: int,
    resource_type: str = "configuration",
    created_at: datetime | None = None,
) -> None:
    for i in range(count):
        log = AuditLog(
            tenant_id=TENANT,
            actor="tester",
            action="test_action",
            resource_type=resource_type,
            resource_id=f"res-{i}",
        )
        db.add(log)
    await db.flush()

    # Manually set created_at if needed (after flush to get defaults)
    if created_at:
        from sqlalchemy import update

        await db.execute(update(AuditLog).values(created_at=created_at))
        await db.flush()


class TestConfigStats:
    @pytest.mark.asyncio
    async def test_empty_configs(self, db_session: AsyncSession) -> None:
        svc = AnalyticsService(db_session, TENANT)
        result = await svc._config_stats()
        assert result["total"] == 0
        assert result["active"] == 0
        assert result["draft"] == 0
        assert result["by_status"] == {}

    @pytest.mark.asyncio
    async def test_configs_by_status(self, db_session: AsyncSession) -> None:
        await _seed_configs(db_session, ["active", "active", "draft", "testing", "configured"])
        svc = AnalyticsService(db_session, TENANT)
        result = await svc._config_stats()
        assert result["total"] == 5
        assert result["active"] == 3  # 2 active + 1 testing
        assert result["draft"] == 2  # 1 draft + 1 configured


class TestSimulationStats:
    @pytest.mark.asyncio
    async def test_empty_simulations(self, db_session: AsyncSession) -> None:
        svc = AnalyticsService(db_session, TENANT)
        result = await svc._simulation_stats()
        assert result["total"] == 0
        assert result["pass_rate"] == 0.0
        assert result["avg_duration_ms"] == 0

    @pytest.mark.asyncio
    async def test_simulations_with_data(self, db_session: AsyncSession) -> None:
        await _seed_simulations(
            db_session,
            ["passed", "passed", "failed"],
            durations=[100, 200, 300],
        )
        svc = AnalyticsService(db_session, TENANT)
        result = await svc._simulation_stats()
        assert result["total"] == 3
        assert result["passed"] == 2
        assert result["failed"] == 1
        assert result["pass_rate"] == round(2 / 3, 2)
        assert result["avg_duration_ms"] == 200


class TestDocumentStats:
    @pytest.mark.asyncio
    async def test_empty_documents(self, db_session: AsyncSession) -> None:
        svc = AnalyticsService(db_session, TENANT)
        result = await svc._document_stats()
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_documents_with_data(self, db_session: AsyncSession) -> None:
        await _seed_documents(db_session, ["parsed", "parsed", "uploaded"])
        svc = AnalyticsService(db_session, TENANT)
        result = await svc._document_stats()
        assert result["total"] == 3
        assert result["by_status"]["parsed"] == 2
        assert result["by_status"]["uploaded"] == 1


class TestAuditCount:
    @pytest.mark.asyncio
    async def test_empty_audit(self, db_session: AsyncSession) -> None:
        svc = AnalyticsService(db_session, TENANT)
        result = await svc._audit_count()
        assert result == 0

    @pytest.mark.asyncio
    async def test_audit_count(self, db_session: AsyncSession) -> None:
        await _seed_audit_logs(db_session, 5)
        svc = AnalyticsService(db_session, TENANT)
        result = await svc._audit_count()
        assert result == 5


class TestCalculateHealthScore:
    def test_base_score_no_data(self) -> None:
        configs = {"total": 0, "active": 0, "draft": 0, "by_status": {}}
        sims = {"total": 0, "pass_rate": 0}
        score = AnalyticsService._calculate_health_score(configs, sims)
        assert score == 50.0

    def test_with_configs_and_active(self) -> None:
        configs = {"total": 5, "active": 2, "draft": 3, "by_status": {}}
        sims = {"total": 0, "pass_rate": 0}
        score = AnalyticsService._calculate_health_score(configs, sims)
        assert score == 75.0  # 50 + 10 (configs) + 15 (active)

    def test_perfect_pass_rate(self) -> None:
        configs = {"total": 5, "active": 2, "draft": 3, "by_status": {}}
        sims = {"total": 10, "pass_rate": 1.0}
        score = AnalyticsService._calculate_health_score(configs, sims)
        assert score == 100.0  # 50 + 10 + 15 + 25 = 100

    def test_partial_pass_rate(self) -> None:
        configs = {"total": 1, "active": 0, "draft": 1, "by_status": {}}
        sims = {"total": 10, "pass_rate": 0.5}
        score = AnalyticsService._calculate_health_score(configs, sims)
        assert score == 72.5  # 50 + 10 + 0.5*25 = 72.5


class TestWeeklyActivity:
    @pytest.mark.asyncio
    async def test_empty_weekly_activity(self, db_session: AsyncSession) -> None:
        svc = AnalyticsService(db_session, TENANT)
        result = await svc._weekly_activity()
        assert len(result) == 7
        assert all(r["documents"] == 0 and r["simulations"] == 0 for r in result)

    @pytest.mark.asyncio
    async def test_weekly_activity_with_data(self, db_session: AsyncSession) -> None:
        now = datetime.now(UTC)
        for resource_type in ["document", "document", "simulation"]:
            log = AuditLog(
                tenant_id=TENANT,
                actor="tester",
                action="test",
                resource_type=resource_type,
                resource_id="r1",
            )
            db_session.add(log)
        await db_session.flush()

        svc = AnalyticsService(db_session, TENANT)
        result = await svc._weekly_activity()
        assert len(result) == 7
        today_dow = now.weekday()
        today_entry = result[today_dow]
        assert today_entry["documents"] == 2
        assert today_entry["simulations"] == 1


class TestThroughput:
    @pytest.mark.asyncio
    async def test_empty_throughput(self, db_session: AsyncSession) -> None:
        svc = AnalyticsService(db_session, TENANT)
        result = await svc._throughput()
        assert len(result) == 6  # 0, 4, 8, 12, 16, 20
        assert all(r["records"] == 0 for r in result)

    @pytest.mark.asyncio
    async def test_throughput_with_data(self, db_session: AsyncSession) -> None:
        for _ in range(3):
            log = AuditLog(
                tenant_id=TENANT,
                actor="tester",
                action="test",
                resource_type="configuration",
                resource_id="r1",
            )
            db_session.add(log)
        await db_session.flush()

        svc = AnalyticsService(db_session, TENANT)
        result = await svc._throughput()
        total_records = sum(r["records"] for r in result)
        assert total_records == 3


class TestTotalProcessed:
    @pytest.mark.asyncio
    async def test_empty_total(self, db_session: AsyncSession) -> None:
        svc = AnalyticsService(db_session, TENANT)
        result = await svc._total_processed()
        assert result == 0

    @pytest.mark.asyncio
    async def test_total_with_data(self, db_session: AsyncSession) -> None:
        await _seed_configs(db_session, ["active", "draft"])
        await _seed_documents(db_session, ["parsed"])
        svc = AnalyticsService(db_session, TENANT)
        result = await svc._total_processed()
        assert result == 3  # 2 configs + 1 doc


class TestTotalWarnings:
    @pytest.mark.asyncio
    async def test_empty_warnings(self, db_session: AsyncSession) -> None:
        svc = AnalyticsService(db_session, TENANT)
        result = await svc._total_warnings()
        assert result == 0

    @pytest.mark.asyncio
    async def test_warnings_with_data(self, db_session: AsyncSession) -> None:
        await _seed_configs(db_session, ["draft", "error", "active", "deprecated"])
        await _seed_simulations(db_session, ["failed", "passed"])
        svc = AnalyticsService(db_session, TENANT)
        result = await svc._total_warnings()
        assert result == 4  # 3 warn configs (draft, error, deprecated) + 1 failed sim


class TestGetDashboardMetrics:
    @pytest.mark.asyncio
    async def test_full_dashboard(self, db_session: AsyncSession) -> None:
        await _seed_configs(db_session, ["active"])
        await _seed_documents(db_session, ["parsed"])
        await _seed_audit_logs(db_session, 2)

        svc = AnalyticsService(db_session, TENANT)
        metrics = await svc.get_dashboard_metrics()

        assert "configurations" in metrics
        assert "simulations" in metrics
        assert "documents" in metrics
        assert "audit_entries" in metrics
        assert "health_score" in metrics
        assert "weekly_activity" in metrics
        assert "throughput" in metrics
        assert "total_processed" in metrics
        assert "total_warnings" in metrics
        assert metrics["audit_entries"] == 2
