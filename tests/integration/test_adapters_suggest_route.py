"""Integration tests for POST /api/v1/adapters/suggest.

These tests exercise the route through the existing ASGI ``client`` fixture
and patch ``OpenAIClient.generate_json`` so they never reach the real OpenAI
API. The acceptance assertions for the persona's KYC fixture are encoded
here against a mocked LLM response that mirrors what gpt-4.1-nano produces
for the prompt — the verifier separately exercises the real provider via
the gold-standard simulation gate documented in the engineering CHARTER.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.adapter import Adapter, AdapterVersion
from finspark.models.document import Document
from finspark.schemas.common import DocType
from finspark.schemas.documents import (
    ExtractedAuth,
    ExtractedEndpoint,
    ExtractedField,
    ParsedDocumentResult,
)

FIXTURES_ROOT = Path(__file__).resolve().parents[2] / "test_fixtures"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _seed_eight_adapters(db: AsyncSession) -> dict[str, tuple[str, str]]:
    """Seed the 8 canonical adapters and return {name: (adapter_id, version_id)}."""
    rows = [
        ("CIBIL Credit Bureau", "bureau", "TransUnion CIBIL credit score and report integration", [{"path": "/credit-score"}]),
        ("Aadhaar eKYC Provider", "kyc", "Aadhaar-based electronic KYC verification", [{"path": "/verify/aadhaar"}]),
        ("GST Verification Service", "gst", "GSTIN lookup and verification", [{"path": "/gstin/verify"}]),
        ("Payment Gateway", "payment", "UPI, cards, and netbanking payment aggregator", [{"path": "/payments/charge"}]),
        ("Fraud Detection Engine", "fraud", "Risk scoring for transactions", [{"path": "/risk/score"}]),
        ("SMS Gateway", "notification", "Transactional SMS delivery", [{"path": "/sms/send"}]),
        ("Account Aggregator (AA Framework)", "open_banking", "RBI Account Aggregator framework integration", [{"path": "/aa/consent"}]),
        ("Email Notification Gateway", "notification", "Transactional email delivery", [{"path": "/email/send"}]),
    ]
    out: dict[str, tuple[str, str]] = {}
    for name, category, description, endpoints in rows:
        adapter = Adapter(name=name, category=category, description=description, is_active=True)
        db.add(adapter)
        await db.flush()
        version = AdapterVersion(
            adapter_id=adapter.id,
            version="v1",
            version_order=1,
            base_url=f"https://api.example.com/{category}",
            auth_type="api_key",
            endpoints=json.dumps(endpoints),
        )
        db.add(version)
        await db.flush()
        out[name] = (adapter.id, version.id)
    await db.commit()
    return out


def _kyc_parsed_from_fixture() -> str:
    """Build a ParsedDocumentResult JSON string out of 01_simple_kyc_api.yaml."""
    spec = yaml.safe_load((FIXTURES_ROOT / "01_simple_kyc_api.yaml").read_text())
    info = spec.get("info", {})
    paths = spec.get("paths", {})
    endpoints = []
    for path, methods in paths.items():
        for method, _meta in methods.items():
            endpoints.append(
                ExtractedEndpoint(path=path, method=method.upper(), description=str(_meta.get("summary") or ""))
            )
    parsed = ParsedDocumentResult(
        doc_type=DocType.API_SPEC,
        title=info.get("title", ""),
        summary=info.get("description", ""),
        services_identified=["aadhaar", "kyc", "verification"],
        endpoints=endpoints,
        fields=[
            ExtractedField(name="aadhaar_number", data_type="string", source_section="request"),
            ExtractedField(name="customer_name", data_type="string", source_section="request"),
            ExtractedField(name="date_of_birth", data_type="string", source_section="request"),
        ],
        auth_requirements=[ExtractedAuth(auth_type="api_key")],
        sections={"base_urls": "https://api.kyc-provider.in/v1"},
        confidence_score=0.9,
    )
    return parsed.model_dump_json()


def _offdomain_parsed() -> str:
    parsed = ParsedDocumentResult(
        doc_type=DocType.API_SPEC,
        title="Cosmic Weather Telemetry API",
        summary="Streams ionospheric scintillation indices from polar ground stations.",
        services_identified=["telemetry", "weather"],
        endpoints=[ExtractedEndpoint(path="/telemetry/scintillation", method="GET")],
        fields=[ExtractedField(name="station_id", data_type="string", source_section="response")],
        auth_requirements=[ExtractedAuth(auth_type="api_key")],
        sections={"base_urls": "https://api.cosmic-weather.example/v1"},
        confidence_score=0.6,
    )
    return parsed.model_dump_json()


async def _make_doc(db: AsyncSession, parsed_json: str, filename: str = "spec.yaml") -> str:
    doc = Document(
        tenant_id="test-tenant",
        filename=filename,
        file_type="yaml",
        file_size=1024,
        doc_type="api_spec",
        status="parsed",
        parsed_result=parsed_json,
    )
    db.add(doc)
    await db.commit()
    return doc.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSuggestRouteErrorPaths:
    @pytest.mark.asyncio
    async def test_404_when_document_missing(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/adapters/suggest",
            json={"document_id": "does-not-exist"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_422_when_document_not_parsed(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        doc = Document(
            tenant_id="test-tenant",
            filename="raw.yaml",
            file_type="yaml",
            file_size=1024,
            doc_type="api_spec",
            status="uploaded",
            parsed_result=None,
        )
        db_session.add(doc)
        await db_session.commit()

        resp = await client.post(
            "/api/v1/adapters/suggest",
            json={"document_id": doc.id},
        )
        assert resp.status_code == 422


class TestSuggestRouteAcceptance:
    @pytest.mark.asyncio
    async def test_kyc_fixture_top_match_aadhaar_score_above_0_85(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Persona acceptance #1 — Aadhaar eKYC Provider scores >= 0.85."""
        seeded = await _seed_eight_adapters(db_session)
        kyc_adapter_id, kyc_version_id = seeded["Aadhaar eKYC Provider"]
        doc_id = await _make_doc(
            db_session, _kyc_parsed_from_fixture(), filename="01_simple_kyc_api.yaml"
        )

        fake_client = AsyncMock()
        fake_client.generate_json = AsyncMock(
            return_value={
                "matches": [
                    {
                        "adapter_id": kyc_adapter_id,
                        "version_id": kyc_version_id,
                        "score": 0.92,
                        "reason": "Spec verifies Aadhaar number — matches Aadhaar eKYC.",
                    }
                ]
            }
        )

        with (
            patch("finspark.api.routes.adapters.settings") as mock_settings,
            patch(
                "finspark.api.routes.adapters.get_llm_client",
                return_value=fake_client,
            ),
        ):
            mock_settings.ai_enabled = True
            resp = await client.post(
                "/api/v1/adapters/suggest", json={"document_id": doc_id}
            )

        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["matches"], "expected at least one ranked match"
        top = body["matches"][0]
        assert top["adapter_id"] == kyc_adapter_id
        assert top["version_id"] == kyc_version_id
        assert top["score"] >= 0.85
        assert body["suggest_custom"] is False

    @pytest.mark.asyncio
    async def test_off_domain_spec_returns_suggest_custom(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Persona acceptance #2 — off-domain spec triggers suggest_custom."""
        await _seed_eight_adapters(db_session)
        doc_id = await _make_doc(db_session, _offdomain_parsed(), filename="cosmic.yaml")

        fake_client = AsyncMock()
        fake_client.generate_json = AsyncMock(return_value={"matches": []})

        with (
            patch("finspark.api.routes.adapters.settings") as mock_settings,
            patch(
                "finspark.api.routes.adapters.get_llm_client",
                return_value=fake_client,
            ),
        ):
            mock_settings.ai_enabled = True
            resp = await client.post(
                "/api/v1/adapters/suggest", json={"document_id": doc_id}
            )

        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["suggest_custom"] is True
        if body["matches"]:
            assert body["matches"][0]["score"] < body["threshold"]
