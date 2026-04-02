"""Comprehensive tests for the event system and health monitor."""

import asyncio

import pytest

import finspark.core.events as events_module
from finspark.core.events import (
    ADAPTER_DEPRECATED,
    CONFIG_CREATED,
    CONFIG_DEPLOYED,
    CONFIG_ROLLED_BACK,
    CONFIG_UPDATED,
    DOCUMENT_PARSED,
    SIMULATION_COMPLETED,
    SIMULATION_STARTED,
    clear,
    emit,
    on,
)
from finspark.services.health_monitor import HealthMonitor
from finspark.services.health_monitor import monitor as singleton_monitor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Event System Tests
# ---------------------------------------------------------------------------


class TestEventOn:
    def setup_method(self):
        clear()

    def teardown_method(self):
        clear()

    def test_on_registers_handler(self):
        called = []
        on(CONFIG_CREATED, lambda data: called.append(data))
        emit(CONFIG_CREATED, {"id": 1})
        assert called == [{"id": 1}]

    def test_on_multiple_handlers_same_event(self):
        results = []
        on(CONFIG_CREATED, lambda d: results.append("h1"))
        on(CONFIG_CREATED, lambda d: results.append("h2"))
        emit(CONFIG_CREATED, {})
        assert results == ["h1", "h2"]

    def test_on_does_not_cross_contaminate_events(self):
        a_calls = []
        b_calls = []
        on(CONFIG_CREATED, lambda d: a_calls.append(d))
        on(CONFIG_UPDATED, lambda d: b_calls.append(d))
        emit(CONFIG_CREATED, {"x": 1})
        assert a_calls == [{"x": 1}]
        assert b_calls == []

    def test_handler_receives_correct_data(self):
        received = {}

        def capture(data):
            received.update(data)

        on(CONFIG_DEPLOYED, capture)
        emit(CONFIG_DEPLOYED, {"env": "prod", "version": 42})
        assert received == {"env": "prod", "version": 42}


class TestEventEmit:
    def setup_method(self):
        clear()

    def teardown_method(self):
        clear()

    def test_emit_no_handlers_does_not_error(self):
        # Should not raise even with zero handlers registered
        emit("nonexistent.event", {"payload": True})

    def test_emit_failing_handler_does_not_propagate(self):
        def bad_handler(data):
            raise RuntimeError("boom")

        good_calls = []
        on(CONFIG_ROLLED_BACK, bad_handler)
        on(CONFIG_ROLLED_BACK, lambda d: good_calls.append(d))

        # Must not raise
        emit(CONFIG_ROLLED_BACK, {"reason": "test"})
        # Good handler still runs after the bad one
        assert good_calls == [{"reason": "test"}]

    def test_emit_calls_handler_with_exact_data(self):
        payloads = []
        on(SIMULATION_STARTED, lambda d: payloads.append(d))
        data = {"run_id": "abc", "steps": 10}
        emit(SIMULATION_STARTED, data)
        assert payloads[0] is data

    def test_emit_unknown_event_does_not_affect_known_handlers(self):
        calls = []
        on(DOCUMENT_PARSED, lambda d: calls.append(d))
        emit("totally.unknown", {"x": 1})
        assert calls == []


class TestEventClear:
    def setup_method(self):
        clear()

    def teardown_method(self):
        clear()

    def test_clear_removes_all_handlers(self):
        calls = []
        on(CONFIG_CREATED, lambda d: calls.append(d))
        on(CONFIG_UPDATED, lambda d: calls.append(d))
        clear()
        emit(CONFIG_CREATED, {})
        emit(CONFIG_UPDATED, {})
        assert calls == []

    def test_clear_then_re_register_works(self):
        calls = []
        on(ADAPTER_DEPRECATED, lambda d: calls.append("first"))
        clear()
        on(ADAPTER_DEPRECATED, lambda d: calls.append("second"))
        emit(ADAPTER_DEPRECATED, {})
        assert calls == ["second"]


class TestEventConstants:
    def test_standard_event_constants_exist(self):
        assert CONFIG_CREATED == "config.created"
        assert CONFIG_UPDATED == "config.updated"
        assert CONFIG_DEPLOYED == "config.deployed"
        assert CONFIG_ROLLED_BACK == "config.rolled_back"
        assert SIMULATION_STARTED == "simulation.started"
        assert SIMULATION_COMPLETED == "simulation.completed"
        assert DOCUMENT_PARSED == "document.parsed"
        assert ADAPTER_DEPRECATED == "adapter.deprecated"

    def test_constants_are_strings(self):
        for attr in (
            "CONFIG_CREATED",
            "CONFIG_UPDATED",
            "CONFIG_DEPLOYED",
            "CONFIG_ROLLED_BACK",
            "SIMULATION_STARTED",
            "SIMULATION_COMPLETED",
            "DOCUMENT_PARSED",
            "ADAPTER_DEPRECATED",
        ):
            assert isinstance(getattr(events_module, attr), str), f"{attr} must be str"


class TestMultipleHandlersSameEvent:
    def setup_method(self):
        clear()

    def teardown_method(self):
        clear()

    def test_all_handlers_invoked_in_order(self):
        order = []
        on(SIMULATION_COMPLETED, lambda d: order.append(1))
        on(SIMULATION_COMPLETED, lambda d: order.append(2))
        on(SIMULATION_COMPLETED, lambda d: order.append(3))
        emit(SIMULATION_COMPLETED, {})
        assert order == [1, 2, 3]

    def test_handlers_for_different_events_are_independent(self):
        a, b = [], []
        on(CONFIG_CREATED, lambda d: a.append("a"))
        on(CONFIG_UPDATED, lambda d: b.append("b"))
        emit(CONFIG_CREATED, {})
        assert a == ["a"]
        assert b == []
        emit(CONFIG_UPDATED, {})
        assert a == ["a"]
        assert b == ["b"]


# ---------------------------------------------------------------------------
# Health Monitor Tests
# ---------------------------------------------------------------------------


class TestHealthMonitorRegister:
    def test_register_check_adds_entry(self):
        m = HealthMonitor()
        assert "my_check" not in m._checks
        m.register_check("my_check", lambda: {})
        assert "my_check" in m._checks

    def test_register_multiple_checks(self):
        m = HealthMonitor()
        m.register_check("alpha", lambda: {})
        m.register_check("beta", lambda: {})
        assert "alpha" in m._checks
        assert "beta" in m._checks


class TestHealthMonitorRunAllChecks:
    def test_all_pass_returns_healthy(self):
        m = HealthMonitor()
        m.register_check("ok1", lambda: {"status": "ok"})
        m.register_check("ok2", lambda: {"status": "ok"})
        result = _run(m.run_all_checks())
        assert result["overall"] == "healthy"
        assert result["healthy"] == 2
        assert result["total"] == 2

    def test_one_failing_returns_degraded(self):
        m = HealthMonitor()
        m.register_check("good", lambda: {"ok": True})
        m.register_check("bad", lambda: (_ for _ in ()).throw(ValueError("oops")))
        result = _run(m.run_all_checks())
        assert result["overall"] == "degraded"
        assert result["checks"]["good"]["status"] == "healthy"
        assert result["checks"]["bad"]["status"] == "unhealthy"
        assert "oops" in result["checks"]["bad"]["error"]

    def test_all_failing_returns_degraded(self):
        m = HealthMonitor()
        m.register_check("x", lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        result = _run(m.run_all_checks())
        assert result["overall"] == "degraded"
        assert result["healthy"] == 0

    def test_no_checks_returns_healthy_with_zero_total(self):
        m = HealthMonitor()
        result = _run(m.run_all_checks())
        assert result["overall"] == "healthy"
        assert result["total"] == 0
        assert result["healthy"] == 0

    def test_exception_in_check_is_caught(self):
        m = HealthMonitor()

        def exploding():
            raise Exception("unexpected failure")

        m.register_check("exploding", exploding)
        # Must not raise
        result = _run(m.run_all_checks())
        assert result["checks"]["exploding"]["status"] == "unhealthy"
        assert "unexpected failure" in result["checks"]["exploding"]["error"]


class TestHealthMonitorUptime:
    def test_get_uptime_returns_positive_number(self):
        m = HealthMonitor()
        uptime = m.get_uptime()
        assert isinstance(uptime, float)
        assert uptime >= 0

    def test_uptime_increases_over_time(self):
        import time

        m = HealthMonitor()
        t1 = m.get_uptime()
        time.sleep(0.05)
        t2 = m.get_uptime()
        assert t2 > t1

    def test_run_all_checks_includes_uptime_seconds(self):
        m = HealthMonitor()
        result = _run(m.run_all_checks())
        assert "uptime_seconds" in result
        assert result["uptime_seconds"] >= 0


class TestSingletonMonitor:
    def test_singleton_has_default_checks(self):
        assert "database" in singleton_monitor._checks
        assert "parser" in singleton_monitor._checks
        assert "simulator" in singleton_monitor._checks
        assert "field_mapper" in singleton_monitor._checks

    def test_singleton_default_checks_pass(self):
        result = _run(singleton_monitor.run_all_checks())
        assert result["overall"] == "healthy"
        for name in ("database", "parser", "simulator", "field_mapper"):
            assert result["checks"][name]["status"] == "healthy"

    def test_singleton_uptime_is_positive(self):
        assert singleton_monitor.get_uptime() >= 0
