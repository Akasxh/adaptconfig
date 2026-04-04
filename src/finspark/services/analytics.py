"""Analytics service - provides integration metrics and insights."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.audit import AuditLog
from finspark.models.configuration import Configuration
from finspark.models.document import Document
from finspark.models.simulation import Simulation


class AnalyticsService:
    """Provides dashboard metrics and analytics for tenant integrations."""

    def __init__(self, db: AsyncSession, tenant_id: str) -> None:
        self.db = db
        self.tenant_id = tenant_id

    async def get_dashboard_metrics(self) -> dict[str, Any]:
        """Get overview metrics for the tenant dashboard."""
        configs = await self._config_stats()
        simulations = await self._simulation_stats()
        documents = await self._document_stats()
        audit_count = await self._audit_count()
        weekly_activity = await self._weekly_activity()
        throughput = await self._throughput()
        total_processed = await self._total_processed()
        total_warnings = await self._total_warnings()

        return {
            # Original fields (backward compat)
            "configurations": configs,
            "simulations": simulations,
            "documents": documents,
            "audit_entries": audit_count,
            "health_score": self._calculate_health_score(configs, simulations),
            # New fields for frontend charts
            "weekly_activity": weekly_activity,
            "throughput": throughput,
            "total_processed": total_processed,
            "total_warnings": total_warnings,
        }

    async def _config_stats(self) -> dict[str, Any]:
        stmt = (
            select(Configuration.status, func.count())
            .where(Configuration.tenant_id == self.tenant_id)
            .group_by(Configuration.status)
        )
        result = await self.db.execute(stmt)
        by_status = dict(result.all())

        total = sum(by_status.values())
        return {
            "total": total,
            "by_status": by_status,
            "active": by_status.get("active", 0) + by_status.get("testing", 0),
            "draft": by_status.get("draft", 0) + by_status.get("configured", 0),
        }

    async def _simulation_stats(self) -> dict[str, Any]:
        stmt = (
            select(Simulation.status, func.count())
            .where(Simulation.tenant_id == self.tenant_id)
            .group_by(Simulation.status)
        )
        result = await self.db.execute(stmt)
        by_status = dict(result.all())

        total = sum(by_status.values())
        passed = by_status.get("passed", 0)

        stmt_avg = select(func.avg(Simulation.duration_ms)).where(
            Simulation.tenant_id == self.tenant_id,
            Simulation.duration_ms.isnot(None),
        )
        avg_result = await self.db.execute(stmt_avg)
        avg_duration = avg_result.scalar() or 0

        return {
            "total": total,
            "passed": passed,
            "failed": by_status.get("failed", 0),
            "pass_rate": round(passed / total, 2) if total > 0 else 0.0,
            "avg_duration_ms": round(avg_duration, 0),
        }

    async def _document_stats(self) -> dict[str, Any]:
        stmt = (
            select(Document.status, func.count())
            .where(Document.tenant_id == self.tenant_id)
            .group_by(Document.status)
        )
        result = await self.db.execute(stmt)
        by_status = dict(result.all())
        return {"total": sum(by_status.values()), "by_status": by_status}

    async def _audit_count(self) -> int:
        stmt = (
            select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == self.tenant_id)
        )
        result = await self.db.execute(stmt)
        return result.scalar() or 0

    async def _weekly_activity(self) -> list[dict[str, Any]]:
        """Audit logs grouped by day of week for the last 7 days."""
        cutoff = datetime.now(UTC) - timedelta(days=7)
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        stmt = (
            select(AuditLog.created_at, AuditLog.resource_type)
            .where(
                AuditLog.tenant_id == self.tenant_id,
                AuditLog.created_at >= cutoff,
            )
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        buckets: dict[int, dict[str, int]] = {
            i: {"documents": 0, "simulations": 0} for i in range(7)
        }
        for created_at, resource_type in rows:
            dow = created_at.weekday()  # 0=Mon
            if resource_type == "document":
                buckets[dow]["documents"] += 1
            elif resource_type == "simulation":
                buckets[dow]["simulations"] += 1

        return [
            {"name": day_names[i], "documents": buckets[i]["documents"], "simulations": buckets[i]["simulations"]}
            for i in range(7)
        ]

    async def _throughput(self) -> list[dict[str, Any]]:
        """Audit logs grouped by hour (4-hour buckets)."""
        cutoff = datetime.now(UTC) - timedelta(days=1)

        stmt = (
            select(AuditLog.created_at)
            .where(
                AuditLog.tenant_id == self.tenant_id,
                AuditLog.created_at >= cutoff,
            )
        )
        result = await self.db.execute(stmt)
        rows = result.scalars().all()

        buckets: dict[int, int] = {h: 0 for h in range(0, 24, 4)}
        for created_at in rows:
            hour = created_at.hour
            bucket = (hour // 4) * 4
            buckets[bucket] += 1

        return [
            {"hour": f"{h:02d}:00", "records": buckets[h]}
            for h in sorted(buckets)
        ]

    async def _total_processed(self) -> int:
        """Count of all documents + configurations for the tenant."""
        doc_stmt = select(func.count()).select_from(Document).where(
            Document.tenant_id == self.tenant_id
        )
        cfg_stmt = select(func.count()).select_from(Configuration).where(
            Configuration.tenant_id == self.tenant_id
        )
        doc_result = await self.db.execute(doc_stmt)
        cfg_result = await self.db.execute(cfg_stmt)
        return (doc_result.scalar() or 0) + (cfg_result.scalar() or 0)

    async def _total_warnings(self) -> int:
        """Count configs in draft/error status + failed simulations."""
        warn_cfg_stmt = (
            select(func.count())
            .select_from(Configuration)
            .where(
                Configuration.tenant_id == self.tenant_id,
                Configuration.status.in_(["draft", "error", "deprecated"]),
            )
        )
        fail_sim_stmt = (
            select(func.count())
            .select_from(Simulation)
            .where(
                Simulation.tenant_id == self.tenant_id,
                Simulation.status == "failed",
            )
        )
        cfg_result = await self.db.execute(warn_cfg_stmt)
        sim_result = await self.db.execute(fail_sim_stmt)
        return (cfg_result.scalar() or 0) + (sim_result.scalar() or 0)

    @staticmethod
    def _calculate_health_score(configs: dict[str, Any], simulations: dict[str, Any]) -> float:
        """Calculate overall integration health score (0-100)."""
        score = 50.0  # base score

        # Config activity bonus
        if configs["total"] > 0:
            score += 10

        # Active configs bonus
        if configs["active"] > 0:
            score += 15

        # Simulation pass rate bonus
        pass_rate = simulations.get("pass_rate", 0)
        score += pass_rate * 25

        return min(100.0, round(score, 1))
