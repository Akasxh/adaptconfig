"""Tests for the event system."""

from finspark.core import events


class TestEventSystem:
    def setup_method(self) -> None:
        events.clear()

    def test_on_registers_handler(self) -> None:
        called = []
        events.on("test.event", lambda data: called.append(data))
        events.emit("test.event", {"key": "value"})
        assert len(called) == 1
        assert called[0] == {"key": "value"}

    def test_emit_no_handlers(self) -> None:
        # Should not raise
        events.emit("nonexistent.event", {"data": 1})

    def test_emit_calls_multiple_handlers(self) -> None:
        results: list[str] = []
        events.on("multi", lambda d: results.append("a"))
        events.on("multi", lambda d: results.append("b"))
        events.emit("multi", {})
        assert results == ["a", "b"]

    def test_emit_handler_exception_does_not_propagate(self) -> None:
        called = []

        def failing_handler(data: dict) -> None:
            raise ValueError("boom")

        def good_handler(data: dict) -> None:
            called.append("ok")

        events.on("err.event", failing_handler)
        events.on("err.event", good_handler)
        # Should not raise even though first handler fails
        events.emit("err.event", {})
        # Second handler should NOT be called because first fails silently
        # Actually, looking at the code, it catches per-handler, so second runs
        assert called == ["ok"]

    def test_clear_removes_all_handlers(self) -> None:
        called = []
        events.on("clear.test", lambda d: called.append(1))
        events.clear()
        events.emit("clear.test", {})
        assert called == []

    def test_different_events_are_independent(self) -> None:
        a_calls: list[int] = []
        b_calls: list[int] = []
        events.on("event.a", lambda d: a_calls.append(1))
        events.on("event.b", lambda d: b_calls.append(1))
        events.emit("event.a", {})
        assert len(a_calls) == 1
        assert len(b_calls) == 0

    def test_handler_receives_data(self) -> None:
        received: list[dict] = []
        events.on("data.test", lambda d: received.append(d))
        events.emit("data.test", {"config_id": "123", "status": "active"})
        assert received[0]["config_id"] == "123"
        assert received[0]["status"] == "active"

    def test_standard_event_types_defined(self) -> None:
        assert events.CONFIG_CREATED == "config.created"
        assert events.CONFIG_UPDATED == "config.updated"
        assert events.CONFIG_DEPLOYED == "config.deployed"
        assert events.CONFIG_ROLLED_BACK == "config.rolled_back"
        assert events.SIMULATION_STARTED == "simulation.started"
        assert events.SIMULATION_COMPLETED == "simulation.completed"
        assert events.DOCUMENT_PARSED == "document.parsed"
        assert events.ADAPTER_DEPRECATED == "adapter.deprecated"
