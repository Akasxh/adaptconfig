"""API call logger with PII masking and version comparison."""

import copy
import json
import re
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.api_call_log import APICallLog

_AADHAAR_RE = re.compile(r"\b\d{12}\b")
_PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_PHONE_RE = re.compile(r"\b\d{10}\b")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_SENSITIVE_KEYS = {"password", "secret", "token", "key", "authorization"}


def _mask_pii(data: dict) -> dict:
    """Deep-clone and mask PII fields in a dictionary."""
    result = copy.deepcopy(data)
    return _mask_dict(result)


def _mask_dict(data: dict) -> dict:
    for key, value in data.items():
        if any(s in key.lower() for s in _SENSITIVE_KEYS):
            data[key] = "[REDACTED]"
        elif isinstance(value, dict):
            data[key] = _mask_dict(value)
        elif isinstance(value, list):
            data[key] = [
                _mask_dict(v) if isinstance(v, dict) else _mask_str(v) if isinstance(v, str) else v
                for v in value
            ]
        elif isinstance(value, str):
            data[key] = _mask_str(value)
    return data


def _mask_str(value: str) -> str:
    value = _AADHAAR_RE.sub("XXXX-XXXX-XXXX", value)
    value = _PAN_RE.sub("XXXXX****X", value)
    value = _PHONE_RE.sub("XXXXXX****", value)
    value = _EMAIL_RE.sub("***@***.***", value)
    return value


class CallLogger:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def log_call(
        self,
        tenant_id: str,
        configuration_id: str,
        adapter_name: str,
        adapter_version: str,
        endpoint_path: str,
        http_method: str,
        request_headers: dict | None,
        request_body: dict | None,
        response_status: int,
        response_headers: dict | None,
        response_body: dict | None,
        response_time_ms: int,
        schema_match: bool = True,
        drift_fields: dict | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> APICallLog:
        """Log an API call with PII masking applied to request/response bodies."""
        masked_req_headers = json.dumps(_mask_pii(request_headers)) if request_headers else None
        masked_req_body = json.dumps(_mask_pii(request_body)) if request_body else None
        masked_resp_headers = json.dumps(_mask_pii(response_headers)) if response_headers else None
        masked_resp_body = json.dumps(_mask_pii(response_body)) if response_body else None

        log = APICallLog(
            tenant_id=tenant_id,
            configuration_id=configuration_id,
            adapter_name=adapter_name,
            adapter_version=adapter_version,
            endpoint_path=endpoint_path,
            http_method=http_method,
            request_headers=masked_req_headers,
            request_body=masked_req_body,
            response_status=response_status,
            response_headers=masked_resp_headers,
            response_body=masked_resp_body,
            response_time_ms=response_time_ms,
            schema_match=schema_match,
            drift_fields=json.dumps(drift_fields) if drift_fields else None,
            error_code=error_code,
            error_message=error_message,
        )
        self.session.add(log)
        await self.session.flush()
        return log

    async def get_calls(
        self,
        tenant_id: str,
        adapter_name: str | None = None,
        adapter_version: str | None = None,
        endpoint_path: str | None = None,
        status_min: int | None = None,
        status_max: int | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[APICallLog]:
        """Query call logs with filters."""
        filters = self._build_filters(
            tenant_id, adapter_name, adapter_version, endpoint_path,
            status_min, status_max, from_date, to_date,
        )
        stmt = (
            select(APICallLog)
            .where(*filters)
            .order_by(APICallLog.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_calls(
        self,
        tenant_id: str,
        adapter_name: str | None = None,
        adapter_version: str | None = None,
        endpoint_path: str | None = None,
        status_min: int | None = None,
        status_max: int | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> int:
        """Count total matching calls for pagination."""
        filters = self._build_filters(
            tenant_id, adapter_name, adapter_version, endpoint_path,
            status_min, status_max, from_date, to_date,
        )
        stmt = select(func.count()).select_from(APICallLog).where(*filters)
        return (await self.session.execute(stmt)).scalar() or 0

    async def get_call_by_id(self, tenant_id: str, call_id: str) -> APICallLog | None:
        """Get a single call log by ID with tenant scoping."""
        stmt = select(APICallLog).where(
            APICallLog.tenant_id == tenant_id,
            APICallLog.id == call_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def compare_versions(
        self,
        tenant_id: str,
        adapter_name: str,
        version_a: str,
        version_b: str,
        endpoint_path: str | None = None,
    ) -> dict:
        """Compare response patterns between two adapter versions."""
        stats_a = await self._version_stats(tenant_id, adapter_name, version_a, endpoint_path)
        stats_b = await self._version_stats(tenant_id, adapter_name, version_b, endpoint_path)
        return {
            "adapter_name": adapter_name,
            "version_a": {"version": version_a, **stats_a},
            "version_b": {"version": version_b, **stats_b},
        }

    async def _version_stats(
        self,
        tenant_id: str,
        adapter_name: str,
        version: str,
        endpoint_path: str | None,
    ) -> dict:
        filters = [
            APICallLog.tenant_id == tenant_id,
            APICallLog.adapter_name == adapter_name,
            APICallLog.adapter_version == version,
        ]
        if endpoint_path:
            filters.append(APICallLog.endpoint_path == endpoint_path)

        error_case = case((APICallLog.response_status >= 400, 1), else_=0)
        drift_case = case((APICallLog.schema_match == False, 1), else_=0)  # noqa: E712

        stmt = select(
            func.count().label("total_calls"),
            func.avg(APICallLog.response_time_ms).label("avg_response_time_ms"),
            func.sum(error_case).label("error_count"),
            func.sum(drift_case).label("drift_count"),
        ).where(*filters)

        row = (await self.session.execute(stmt)).one()
        total = row.total_calls or 0
        return {
            "total_calls": total,
            "avg_response_time_ms": round(float(row.avg_response_time_ms or 0), 2),
            "error_count": int(row.error_count or 0),
            "error_rate": round(int(row.error_count or 0) / total, 4) if total else 0.0,
            "drift_count": int(row.drift_count or 0),
        }

    @staticmethod
    def _build_filters(
        tenant_id: str,
        adapter_name: str | None = None,
        adapter_version: str | None = None,
        endpoint_path: str | None = None,
        status_min: int | None = None,
        status_max: int | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> list:
        filters = [APICallLog.tenant_id == tenant_id]
        if adapter_name:
            filters.append(APICallLog.adapter_name == adapter_name)
        if adapter_version:
            filters.append(APICallLog.adapter_version == adapter_version)
        if endpoint_path:
            filters.append(APICallLog.endpoint_path == endpoint_path)
        if status_min is not None:
            filters.append(APICallLog.response_status >= status_min)
        if status_max is not None:
            filters.append(APICallLog.response_status <= status_max)
        if from_date:
            filters.append(APICallLog.created_at >= from_date)
        if to_date:
            filters.append(APICallLog.created_at <= to_date)
        return filters
