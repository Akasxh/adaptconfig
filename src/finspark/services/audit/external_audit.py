"""Compliance-grade external API audit trail with hash chain tamper detection."""

import copy
import csv
import hashlib
import io
import json
import re
import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.external_api_audit import ExternalAPIAudit

# PII patterns (same as call_logger.py)
_AADHAAR_RE = re.compile(r"\b\d{12}\b")
_PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_PHONE_RE = re.compile(r"\b\d{10}\b")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_SENSITIVE_KEYS = {"password", "secret", "token", "key", "authorization"}


def _mask_str(value: str) -> str:
    value = _AADHAAR_RE.sub("XXXX-XXXX-XXXX", value)
    value = _PAN_RE.sub("XXXXX****X", value)
    value = _PHONE_RE.sub("XXXXXX****", value)
    value = _EMAIL_RE.sub("***@***.***", value)
    return value


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


def _mask_pii(data: dict | None) -> dict | None:
    """Deep-clone and mask Aadhaar, PAN, phone, email, sensitive keys."""
    if data is None:
        return None
    return _mask_dict(copy.deepcopy(data))


class ExternalAuditService:
    """Service for recording and querying compliance-grade external API audit trails."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        tenant_id: str,
        user_id: str | None,
        configuration_id: str,
        adapter_name: str,
        adapter_version: str,
        endpoint_path: str,
        http_method: str,
        request_body: dict | None,
        response_status: int,
        response_body: dict | None,
        response_time_ms: int,
        success: bool,
        trigger_type: str,
        trigger_id: str | None = None,
        workflow_run_id: str | None = None,
        workflow_step_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ExternalAPIAudit:
        """Record an external API interaction with PII masking and hash chaining.

        1. Mask PII in request_body and response_body
        2. Get the last record's hash for this tenant (for chain)
        3. Compute record_hash = SHA-256 of key fields
        4. Create and store the audit record
        """
        masked_request = _mask_pii(request_body)
        masked_response = _mask_pii(response_body)

        # Get previous hash for chain
        previous_hash = await self._get_last_hash(tenant_id)

        # Generate ID upfront so it can be included in the hash
        record_id = str(uuid.uuid4())

        record = ExternalAPIAudit(
            id=record_id,
            tenant_id=tenant_id,
            user_id=user_id,
            configuration_id=configuration_id,
            adapter_name=adapter_name,
            adapter_version=adapter_version,
            endpoint_path=endpoint_path,
            http_method=http_method,
            request_body_masked=json.dumps(masked_request) if masked_request is not None else None,
            response_status=response_status,
            response_body_masked=json.dumps(masked_response) if masked_response is not None else None,
            response_time_ms=response_time_ms,
            success=success,
            error_code=error_code,
            error_message=error_message,
            trigger_type=trigger_type,
            trigger_id=trigger_id,
            workflow_run_id=workflow_run_id,
            workflow_step_id=workflow_step_id,
            previous_hash=previous_hash,
            record_hash="",  # placeholder, computed below
        )

        # Compute hash from key fields
        hash_data = {
            "tenant_id": tenant_id,
            "configuration_id": configuration_id,
            "adapter_name": adapter_name,
            "endpoint_path": endpoint_path,
            "response_status": response_status,
            "record_id": record.id,
            "previous_hash": previous_hash or "",
        }
        record.record_hash = self._compute_hash(hash_data)

        self.session.add(record)
        await self.session.flush()
        return record

    async def get_records(
        self,
        tenant_id: str,
        adapter_name: str | None = None,
        adapter_version: str | None = None,
        success: bool | None = None,
        trigger_type: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ExternalAPIAudit]:
        """Query audit records with filters."""
        filters = [ExternalAPIAudit.tenant_id == tenant_id]
        if adapter_name is not None:
            filters.append(ExternalAPIAudit.adapter_name == adapter_name)
        if adapter_version is not None:
            filters.append(ExternalAPIAudit.adapter_version == adapter_version)
        if success is not None:
            filters.append(ExternalAPIAudit.success == success)
        if trigger_type is not None:
            filters.append(ExternalAPIAudit.trigger_type == trigger_type)
        if from_date is not None:
            filters.append(ExternalAPIAudit.created_at >= from_date)
        if to_date is not None:
            filters.append(ExternalAPIAudit.created_at <= to_date)

        stmt = (
            select(ExternalAPIAudit)
            .where(*filters)
            .order_by(ExternalAPIAudit.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_records(
        self,
        tenant_id: str,
        adapter_name: str | None = None,
        adapter_version: str | None = None,
        success: bool | None = None,
        trigger_type: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> int:
        """Count total matching records for pagination."""
        filters = [ExternalAPIAudit.tenant_id == tenant_id]
        if adapter_name is not None:
            filters.append(ExternalAPIAudit.adapter_name == adapter_name)
        if adapter_version is not None:
            filters.append(ExternalAPIAudit.adapter_version == adapter_version)
        if success is not None:
            filters.append(ExternalAPIAudit.success == success)
        if trigger_type is not None:
            filters.append(ExternalAPIAudit.trigger_type == trigger_type)
        if from_date is not None:
            filters.append(ExternalAPIAudit.created_at >= from_date)
        if to_date is not None:
            filters.append(ExternalAPIAudit.created_at <= to_date)

        stmt = select(func.count()).select_from(ExternalAPIAudit).where(*filters)
        return (await self.session.execute(stmt)).scalar() or 0

    async def get_record_by_id(self, tenant_id: str, record_id: str) -> ExternalAPIAudit | None:
        """Get a single audit record by ID with tenant scoping."""
        stmt = select(ExternalAPIAudit).where(
            ExternalAPIAudit.tenant_id == tenant_id,
            ExternalAPIAudit.id == record_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def verify_chain(self, tenant_id: str, limit: int = 100) -> dict:
        """Verify the hash chain is intact for recent records.

        Walks the chain from the genesis record (previous_hash IS NULL) forward
        by following previous_hash links. This is ordering-agnostic.

        Returns: {"valid": bool, "records_checked": int, "first_broken": str | None}
        """
        # Load all records for this tenant (up to limit) and index by previous_hash
        stmt = (
            select(ExternalAPIAudit)
            .where(ExternalAPIAudit.tenant_id == tenant_id)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        all_records = list(result.scalars().all())

        if not all_records:
            return {"valid": True, "records_checked": 0, "first_broken": None}

        # Build lookup: previous_hash -> record (the record that points to that hash)
        by_prev_hash: dict[str | None, ExternalAPIAudit] = {}
        for r in all_records:
            by_prev_hash[r.previous_hash] = r

        # Find genesis (previous_hash is None)
        current = by_prev_hash.get(None)
        if current is None:
            # No genesis record found within the limit window
            return {"valid": False, "records_checked": 0, "first_broken": None}

        checked = 0
        while current is not None and checked < limit:
            checked += 1

            # Verify this record's own hash
            hash_data = {
                "tenant_id": current.tenant_id,
                "configuration_id": current.configuration_id,
                "adapter_name": current.adapter_name,
                "endpoint_path": current.endpoint_path,
                "response_status": current.response_status,
                "record_id": current.id,
                "previous_hash": current.previous_hash or "",
            }
            expected_hash = self._compute_hash(hash_data)
            if current.record_hash != expected_hash:
                return {
                    "valid": False,
                    "records_checked": checked,
                    "first_broken": current.id,
                }

            # Move to the next record in the chain (the one whose previous_hash == current.record_hash)
            current = by_prev_hash.get(current.record_hash)

        return {"valid": True, "records_checked": checked, "first_broken": None}

    async def export_records(
        self,
        tenant_id: str,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        format: str = "json",
    ) -> str:
        """Export records for regulatory submission."""
        records = await self.get_records(
            tenant_id=tenant_id,
            from_date=from_date,
            to_date=to_date,
            limit=10000,
            offset=0,
        )

        if format == "csv":
            return self._export_csv(records)
        return self._export_json(records)

    def _compute_hash(self, record_data: dict) -> str:
        """SHA-256 of canonical JSON of key fields."""
        canonical = json.dumps(record_data, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()

    async def _get_last_hash(self, tenant_id: str) -> str | None:
        """Get the record_hash of the chain tail for this tenant.

        The chain tail is the record whose record_hash is not referenced as
        previous_hash by any other record in the same tenant.
        """
        # Subquery: all previous_hash values for this tenant
        used_hashes = (
            select(ExternalAPIAudit.previous_hash)
            .where(
                ExternalAPIAudit.tenant_id == tenant_id,
                ExternalAPIAudit.previous_hash.isnot(None),
            )
            .scalar_subquery()
        )
        stmt = (
            select(ExternalAPIAudit.record_hash)
            .where(
                ExternalAPIAudit.tenant_id == tenant_id,
                ExternalAPIAudit.record_hash.notin_(used_hashes),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    def _export_json(records: list[ExternalAPIAudit]) -> str:
        items = []
        for r in records:
            items.append({
                "id": r.id,
                "tenant_id": r.tenant_id,
                "configuration_id": r.configuration_id,
                "adapter_name": r.adapter_name,
                "adapter_version": r.adapter_version,
                "endpoint_path": r.endpoint_path,
                "http_method": r.http_method,
                "response_status": r.response_status,
                "response_time_ms": r.response_time_ms,
                "success": r.success,
                "trigger_type": r.trigger_type,
                "error_code": r.error_code,
                "error_message": r.error_message,
                "record_hash": r.record_hash,
                "previous_hash": r.previous_hash,
                "created_at": str(r.created_at),
            })
        return json.dumps(items, indent=2, default=str)

    @staticmethod
    def _export_csv(records: list[ExternalAPIAudit]) -> str:
        output = io.StringIO()
        fieldnames = [
            "id", "tenant_id", "configuration_id", "adapter_name",
            "adapter_version", "endpoint_path", "http_method",
            "response_status", "response_time_ms", "success",
            "trigger_type", "error_code", "error_message",
            "record_hash", "previous_hash", "created_at",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for r in records:
            writer.writerow({
                "id": r.id,
                "tenant_id": r.tenant_id,
                "configuration_id": r.configuration_id,
                "adapter_name": r.adapter_name,
                "adapter_version": r.adapter_version,
                "endpoint_path": r.endpoint_path,
                "http_method": r.http_method,
                "response_status": r.response_status,
                "response_time_ms": r.response_time_ms,
                "success": r.success,
                "trigger_type": r.trigger_type,
                "error_code": r.error_code,
                "error_message": r.error_message,
                "record_hash": r.record_hash,
                "previous_hash": r.previous_hash,
                "created_at": str(r.created_at),
            })
        return output.getvalue()
