"""Unit tests for the AdapterSuggester ranking + threshold + fallback logic."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from finspark.schemas.adapters import AdapterSuggestResponse
from finspark.services.llm.client import GeminiAPIError
from finspark.services.registry.adapter_suggester import (
    SUGGEST_THRESHOLD,
    AdapterSuggester,
)


class _FakeVersion:
    def __init__(self, vid: str, version: str, endpoints: list[dict[str, Any]]) -> None:
        self.id = vid
        self.version = version
        self.endpoints = json.dumps(endpoints)


class _FakeAdapter:
    def __init__(
        self,
        aid: str,
        name: str,
        category: str,
        description: str,
        versions: list[_FakeVersion],
    ) -> None:
        self.id = aid
        self.name = name
        self.category = category
        self.description = description
        self.versions = versions


def _kyc_adapter() -> _FakeAdapter:
    return _FakeAdapter(
        "adp-kyc-1",
        "Aadhaar eKYC Provider",
        "kyc",
        "Aadhaar-based electronic KYC verification",
        [_FakeVersion("ver-kyc-v1", "v1", [{"path": "/verify/aadhaar"}])],
    )


def _payment_adapter() -> _FakeAdapter:
    return _FakeAdapter(
        "adp-pay-1",
        "Payment Gateway",
        "payment",
        "UPI / cards / netbanking aggregator",
        [_FakeVersion("ver-pay-v1", "v1", [{"path": "/payments/charge"}])],
    )


def _bureau_adapter() -> _FakeAdapter:
    return _FakeAdapter(
        "adp-cibil-1",
        "CIBIL Credit Bureau",
        "bureau",
        "TransUnion CIBIL credit score and report integration",
        [_FakeVersion("ver-cibil-v1", "v1", [{"path": "/credit-score"}])],
    )


def _kyc_doc() -> dict[str, Any]:
    return {
        "title": "Simple KYC Verification API",
        "summary": "Basic Aadhaar-based KYC verification for customer onboarding.",
        "services_identified": ["aadhaar", "kyc", "verification"],
        "endpoints": [{"path": "/verify/aadhaar", "method": "POST"}],
        "fields": [
            {"name": "aadhaar_number"},
            {"name": "customer_name"},
            {"name": "date_of_birth"},
        ],
        "sections": {"base_urls": "https://api.kyc-provider.in/v1"},
    }


def _offdomain_doc() -> dict[str, Any]:
    return {
        "title": "Cosmic Weather Telemetry API",
        "summary": "Streams ionospheric scintillation indices from polar ground stations.",
        "services_identified": ["telemetry", "weather"],
        "endpoints": [{"path": "/telemetry/scintillation", "method": "GET"}],
        "fields": [{"name": "station_id"}, {"name": "scint_index"}],
        "sections": {"base_urls": "https://api.cosmic-weather.example/v1"},
    }


# ---------------------------------------------------------------------------
# Pure-logic / fallback tests (no LLM)
# ---------------------------------------------------------------------------


class TestKeywordFallback:
    @pytest.mark.asyncio
    async def test_kyc_doc_ranks_kyc_adapter_first(self) -> None:
        suggester = AdapterSuggester(llm_client=None)
        adapters = [_payment_adapter(), _bureau_adapter(), _kyc_adapter()]
        resp = await suggester.suggest(_kyc_doc(), adapters)

        assert isinstance(resp, AdapterSuggestResponse)
        assert resp.matches, "fallback should produce at least one match"
        assert resp.matches[0].adapter_id == "adp-kyc-1"

    @pytest.mark.asyncio
    async def test_off_domain_doc_flags_suggest_custom(self) -> None:
        suggester = AdapterSuggester(llm_client=None)
        adapters = [_payment_adapter(), _bureau_adapter(), _kyc_adapter()]
        resp = await suggester.suggest(_offdomain_doc(), adapters)

        assert resp.suggest_custom is True
        if resp.matches:
            assert resp.matches[0].score < SUGGEST_THRESHOLD

    @pytest.mark.asyncio
    async def test_empty_catalogue_returns_suggest_custom(self) -> None:
        suggester = AdapterSuggester(llm_client=None)
        resp = await suggester.suggest(_kyc_doc(), [])
        assert resp.matches == []
        assert resp.suggest_custom is True


# ---------------------------------------------------------------------------
# LLM ranking + validation
# ---------------------------------------------------------------------------


class TestLLMRanking:
    @pytest.mark.asyncio
    async def test_top_match_score_clamped_and_threshold_respected(self) -> None:
        client = AsyncMock()
        client.generate_json = AsyncMock(
            return_value={
                "matches": [
                    {
                        "adapter_id": "adp-kyc-1",
                        "version_id": "ver-kyc-v1",
                        "score": 1.7,  # out of range — must clamp to 1.0
                        "reason": "Direct fit on Aadhaar eKYC verification.",
                    },
                    {
                        "adapter_id": "adp-pay-1",
                        "version_id": "ver-pay-v1",
                        "score": -0.3,  # out of range — must clamp to 0.0
                        "reason": "No payment paths in document.",
                    },
                ]
            }
        )
        suggester = AdapterSuggester(llm_client=client)
        adapters = [_kyc_adapter(), _payment_adapter()]
        resp = await suggester.suggest(_kyc_doc(), adapters)

        assert resp.matches[0].adapter_id == "adp-kyc-1"
        assert resp.matches[0].score == 1.0
        assert resp.matches[-1].score == 0.0
        assert resp.suggest_custom is False  # top score 1.0 >= 0.55

    @pytest.mark.asyncio
    async def test_unknown_ids_dropped_then_fallback_runs(self) -> None:
        client = AsyncMock()
        client.generate_json = AsyncMock(
            return_value={
                "matches": [
                    {
                        "adapter_id": "ghost",
                        "version_id": "phantom",
                        "score": 0.99,
                        "reason": "hallucinated",
                    }
                ]
            }
        )
        suggester = AdapterSuggester(llm_client=client)
        adapters = [_kyc_adapter(), _payment_adapter(), _bureau_adapter()]
        resp = await suggester.suggest(_kyc_doc(), adapters)

        assert resp.matches, "should fall through to keyword fallback"
        assert resp.matches[0].adapter_id == "adp-kyc-1"

    @pytest.mark.asyncio
    async def test_llm_error_falls_back_to_keywords(self) -> None:
        client = AsyncMock()
        client.generate_json = AsyncMock(side_effect=GeminiAPIError("boom"))
        suggester = AdapterSuggester(llm_client=client)
        adapters = [_kyc_adapter(), _payment_adapter()]
        resp = await suggester.suggest(_kyc_doc(), adapters)

        assert resp.matches[0].adapter_id == "adp-kyc-1"

    @pytest.mark.asyncio
    async def test_acceptance_kyc_top_score_above_0_85(self) -> None:
        """Acceptance: the persona requires top suggestion for the KYC fixture
        scores >= 0.85. We assert the suggester surfaces the LLM's score
        verbatim (post-clamp) for the matching candidate."""
        client = AsyncMock()
        client.generate_json = AsyncMock(
            return_value={
                "matches": [
                    {
                        "adapter_id": "adp-kyc-1",
                        "version_id": "ver-kyc-v1",
                        "score": 0.92,
                        "reason": "Document verifies Aadhaar — exact category fit.",
                    }
                ]
            }
        )
        suggester = AdapterSuggester(llm_client=client)
        adapters = [_kyc_adapter(), _payment_adapter(), _bureau_adapter()]
        resp = await suggester.suggest(_kyc_doc(), adapters)

        assert resp.matches[0].adapter_id == "adp-kyc-1"
        assert resp.matches[0].score >= 0.85
        assert resp.suggest_custom is False

    @pytest.mark.asyncio
    async def test_top_n_capped_at_three(self) -> None:
        client = AsyncMock()
        client.generate_json = AsyncMock(
            return_value={
                "matches": [
                    {"adapter_id": "adp-kyc-1", "version_id": "ver-kyc-v1", "score": 0.9, "reason": "k"},
                    {"adapter_id": "adp-pay-1", "version_id": "ver-pay-v1", "score": 0.7, "reason": "p"},
                    {"adapter_id": "adp-cibil-1", "version_id": "ver-cibil-v1", "score": 0.6, "reason": "c"},
                    {"adapter_id": "adp-cibil-1", "version_id": "ver-cibil-v1", "score": 0.5, "reason": "dup"},
                ]
            }
        )
        suggester = AdapterSuggester(
            llm_client=client,
        )
        adapters = [_kyc_adapter(), _payment_adapter(), _bureau_adapter()]
        resp = await suggester.suggest(_kyc_doc(), adapters)

        assert len(resp.matches) <= 3
        ids = [m.adapter_id for m in resp.matches]
        assert len(ids) == len(set(ids))  # de-duplicated
