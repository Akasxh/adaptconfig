"""Simple in-memory circuit breaker."""

import time


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, reset_timeout: int = 60) -> None:
        self._failure_threshold = failure_threshold
        self._reset_timeout = reset_timeout
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}

    def is_open(self, config_id: str) -> bool:
        if config_id not in self._opened_at:
            return False
        elapsed = time.monotonic() - self._opened_at[config_id]
        if elapsed >= self._reset_timeout:
            self._failures.pop(config_id, None)
            self._opened_at.pop(config_id, None)
            return False
        return True

    def record_success(self, config_id: str) -> None:
        self._failures.pop(config_id, None)
        self._opened_at.pop(config_id, None)

    def record_failure(self, config_id: str) -> None:
        count = self._failures.get(config_id, 0) + 1
        self._failures[config_id] = count
        if count >= self._failure_threshold:
            self._opened_at[config_id] = time.monotonic()
