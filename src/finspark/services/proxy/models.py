"""Data models for proxy results."""

from dataclasses import dataclass, field


@dataclass
class ProxyResult:
    success: bool
    status_code: int
    response_body: dict | None
    response_headers: dict
    response_time_ms: int
    retries_attempted: int
    error: str | None = None
    circuit_open: bool = False
