"""Tests for the health monitoring service."""

import pytest

from finspark.services.health_monitor import HealthMonitor, monitor


class TestHealthMonitor:
    def test_register_check(self) -> None:
        hm = HealthMonitor()
        hm.register_check("test", lambda: "ok")
        assert "test" in hm._checks

    @pytest.mark.asyncio
    async def test_run_all_checks_healthy(self) -> None:
        hm = HealthMonitor()
        hm.register_check("db", lambda: {"status": "connected"})
        hm.register_check("cache", lambda: {"status": "ready"})
        result = await hm.run_all_checks()
        assert result["overall"] == "healthy"
        assert result["healthy"] == 2
        assert result["total"] == 2

    @pytest.mark.asyncio
    async def test_run_all_checks_degraded(self) -> None:
        hm = HealthMonitor()
        hm.register_check("db", lambda: {"status": "connected"})

        def failing_check() -> dict:
            raise ConnectionError("Redis down")

        hm.register_check("cache", failing_check)
        result = await hm.run_all_checks()
        assert result["overall"] == "degraded"
        assert result["healthy"] == 1
        assert result["total"] == 2
        assert result["checks"]["cache"]["status"] == "unhealthy"
        assert "Redis down" in result["checks"]["cache"]["error"]

    @pytest.mark.asyncio
    async def test_run_all_checks_empty(self) -> None:
        hm = HealthMonitor()
        result = await hm.run_all_checks()
        assert result["overall"] == "healthy"
        assert result["healthy"] == 0
        assert result["total"] == 0

    def test_get_uptime(self) -> None:
        hm = HealthMonitor()
        uptime = hm.get_uptime()
        assert uptime >= 0

    @pytest.mark.asyncio
    async def test_last_status_updated(self) -> None:
        hm = HealthMonitor()
        hm.register_check("db", lambda: "ok")
        await hm.run_all_checks()
        assert hm._checks["db"]["last_status"] == "healthy"

    @pytest.mark.asyncio
    async def test_last_status_unhealthy(self) -> None:
        hm = HealthMonitor()

        def fail() -> None:
            raise RuntimeError("fail")

        hm.register_check("broken", fail)
        await hm.run_all_checks()
        assert hm._checks["broken"]["last_status"] == "unhealthy"

    def test_singleton_has_default_checks(self) -> None:
        assert "database" in monitor._checks
        assert "parser" in monitor._checks
        assert "simulator" in monitor._checks
        assert "field_mapper" in monitor._checks

    @pytest.mark.asyncio
    async def test_singleton_all_healthy(self) -> None:
        result = await monitor.run_all_checks()
        assert result["overall"] == "healthy"
        assert result["total"] == 4
