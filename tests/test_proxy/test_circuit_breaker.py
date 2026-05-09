"""Tests for the circuit breaker."""

import time
from unittest.mock import patch

from finspark.services.proxy.circuit_breaker import CircuitBreaker


class TestCircuitBreaker:
    def test_circuit_starts_closed(self) -> None:
        cb = CircuitBreaker()
        assert cb.is_open("cfg-1") is False

    def test_circuit_opens_after_threshold(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            cb.record_failure("cfg-1")
        assert cb.is_open("cfg-1") is True

    def test_circuit_rejects_when_open(self) -> None:
        cb = CircuitBreaker(failure_threshold=2)
        cb.record_failure("cfg-1")
        cb.record_failure("cfg-1")
        assert cb.is_open("cfg-1") is True

    def test_circuit_resets_after_timeout(self) -> None:
        cb = CircuitBreaker(failure_threshold=2, reset_timeout=1)
        cb.record_failure("cfg-1")
        cb.record_failure("cfg-1")
        assert cb.is_open("cfg-1") is True

        with patch("finspark.services.proxy.circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + 2
            assert cb.is_open("cfg-1") is False

    def test_circuit_resets_on_success(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure("cfg-1")
        cb.record_failure("cfg-1")
        cb.record_success("cfg-1")
        cb.record_failure("cfg-1")
        assert cb.is_open("cfg-1") is False
