"""Core proxy service for forwarding transformed requests."""

import asyncio
import json
import logging
import time
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.configuration import Configuration
from finspark.services.observability.call_logger import CallLogger
from finspark.services.proxy.auth_injector import AuthInjector
from finspark.services.proxy.circuit_breaker import CircuitBreaker
from finspark.services.proxy.models import ProxyResult
from finspark.services.transformation.engine import TransformationEngine

logger = logging.getLogger(__name__)

_circuit_breaker = CircuitBreaker()


class ProxyRouter:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.transformer = TransformationEngine()
        self.logger = CallLogger(session)
        self.auth_injector = AuthInjector()

    async def proxy_request(
        self,
        config_id: str,
        endpoint_path: str,
        tenant_id: str,
        request_body: dict | None = None,
        request_headers: dict | None = None,
        request_method: str = "POST",
    ) -> ProxyResult:
        config = await self._load_config(config_id, tenant_id)
        if config is None:
            return ProxyResult(
                success=False, status_code=404, response_body=None,
                response_headers={}, response_time_ms=0, retries_attempted=0,
                error="Configuration not found",
            )

        if _circuit_breaker.is_open(config_id):
            return ProxyResult(
                success=False, status_code=503, response_body=None,
                response_headers={}, response_time_ms=0, retries_attempted=0,
                error="Circuit breaker is open", circuit_open=True,
            )

        full_config = json.loads(config.full_config) if config.full_config else {}
        field_mappings = json.loads(config.field_mappings) if config.field_mappings else []
        auth_config = self._resolve_auth(config, full_config)
        base_url = full_config.get("base_url", "")
        retry_count = full_config.get("retry_count", 3)
        retry_backoff = full_config.get("retry_backoff", 1.0)

        transformed_body = self._apply_transform(request_body, field_mappings)
        outbound_headers = self.auth_injector.inject(request_headers or {}, auth_config)
        target_url = f"{base_url.rstrip('/')}/{endpoint_path.lstrip('/')}"

        result = await self._forward_with_retry(
            method=request_method,
            url=target_url,
            headers=outbound_headers,
            body=transformed_body,
            retry_count=retry_count,
            retry_backoff=retry_backoff,
            config_id=config_id,
        )

        adapter_name = full_config.get("adapter_name", "")
        adapter_version = full_config.get("version", "")
        await self.logger.log_call(
            tenant_id=tenant_id,
            configuration_id=config_id,
            adapter_name=adapter_name,
            adapter_version=adapter_version,
            endpoint_path=endpoint_path,
            http_method=request_method,
            request_headers=outbound_headers,
            request_body=transformed_body,
            response_status=result.status_code,
            response_headers=result.response_headers,
            response_body=result.response_body,
            response_time_ms=result.response_time_ms,
            error_message=result.error,
        )

        return result

    async def _load_config(
        self, config_id: str, tenant_id: str,
    ) -> Configuration | None:
        stmt = select(Configuration).where(
            Configuration.id == config_id,
            Configuration.tenant_id == tenant_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    def _resolve_auth(
        self, config: Configuration, full_config: dict[str, Any],
    ) -> dict[str, Any]:
        if config.auth_config:
            try:
                return json.loads(config.auth_config)
            except (json.JSONDecodeError, TypeError):
                pass
        return full_config.get("auth", {})

    def _apply_transform(
        self,
        body: dict | None,
        field_mappings: list[dict[str, Any]],
    ) -> dict | None:
        if body is None or not field_mappings:
            return body
        result = self.transformer.transform(body, field_mappings)
        return result.payload

    async def _forward_with_retry(
        self,
        method: str,
        url: str,
        headers: dict,
        body: dict | None,
        retry_count: int,
        retry_backoff: float,
        config_id: str,
    ) -> ProxyResult:
        retries = 0
        last_error: str | None = None

        async with httpx.AsyncClient(timeout=30.0) as client:
            for attempt in range(retry_count + 1):
                start = time.monotonic()
                try:
                    response = await client.request(
                        method=method, url=url, headers=headers, json=body,
                    )
                    elapsed_ms = int((time.monotonic() - start) * 1000)
                    resp_body = self._parse_response_body(response)
                    resp_headers = dict(response.headers)

                    if response.status_code >= 500 and attempt < retry_count:
                        retries += 1
                        await asyncio.sleep(retry_backoff * (2 ** attempt))
                        continue

                    if response.status_code < 400:
                        _circuit_breaker.record_success(config_id)

                    success = response.status_code < 400
                    if not success:
                        _circuit_breaker.record_failure(config_id)

                    return ProxyResult(
                        success=success,
                        status_code=response.status_code,
                        response_body=resp_body,
                        response_headers=resp_headers,
                        response_time_ms=elapsed_ms,
                        retries_attempted=retries,
                    )
                except httpx.HTTPError as exc:
                    elapsed_ms = int((time.monotonic() - start) * 1000)
                    last_error = str(exc)
                    retries = attempt
                    _circuit_breaker.record_failure(config_id)
                    if attempt < retry_count:
                        await asyncio.sleep(retry_backoff * (2 ** attempt))
                        continue
                    return ProxyResult(
                        success=False, status_code=502,
                        response_body=None, response_headers={},
                        response_time_ms=elapsed_ms,
                        retries_attempted=retries,
                        error=last_error,
                    )

        return ProxyResult(
            success=False, status_code=502,
            response_body=None, response_headers={},
            response_time_ms=0, retries_attempted=retries,
            error=last_error or "No attempts made",
        )

    @staticmethod
    def _parse_response_body(response: httpx.Response) -> dict | None:
        try:
            return response.json()
        except Exception:
            return None
