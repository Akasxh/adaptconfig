"""Unit tests for the LLM-driven adapter matcher.

The LLM is mocked so these tests are deterministic and free of network calls.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.services.llm.client import LLMAPIError
from finspark.services.registry.adapter_matcher import (
    SUGGEST_CUSTOM_THRESHOLD,
    suggest_adapters,
)
from finspark.services.registry.adapter_registry import AdapterRegistry


class _StubLLM:
    """Minimal async stub of the LLM client used by the matcher."""

    def __init__(self, payload: dict[str, Any] | None = None, raises: Exception | None = None) -> None:
        self.payload = payload or {"matches": []}
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    async def generate_json(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "prompt": prompt,
                "system_instruction": system_instruction,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if self.raises is not None:
            raise self.raises
        return self.payload


async def _seed_adapter(
    db: AsyncSession,
    *,
    name: str,
    category: str,
    description: str = "",
    endpoints: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    registry = AdapterRegistry(db)
    adapter = await registry.create_adapter(
        name=name,
        category=category,
        description=description,
    )
    version = await registry.add_version(
        adapter_id=adapter.id,
        version="v1",
        base_url="https://example.com",
        auth_type="api_key",
        endpoints=endpoints or [{"path": "/health", "method": "GET"}],
    )
    await db.commit()
    return adapter.id, version.id


def _parsed_doc() -> dict[str, Any]:
    """Compact parsed-document payload used by most ranking tests."""
    return {
        "title": "Simple KYC Verification API",
        "summary": "Aadhaar-based KYC verification for onboarding.",
        "services_identified": ["aadhaar", "kyc"],
        "endpoints": [
            {"path": "/verify/aadhaar", "method": "POST"},
        ],
        "fields": [
            {"name": "aadhaar_number"},
            {"name": "customer_name"},
            {"name": "date_of_birth"},
        ],
    }


class TestSuggestAdaptersRanking:
    @pytest.mark.asyncio
    async def test_returns_top_three_sorted_by_score(self, db_session: AsyncSession) -> None:
        a_id, a_ver = await _seed_adapter(db_session, name="Aadhaar eKYC", category="kyc")
        b_id, b_ver = await _seed_adapter(db_session, name="CIBIL Bureau", category="bureau")
        c_id, c_ver = await _seed_adapter(db_session, name="Razorpay", category="payment")
        d_id, d_ver = await _seed_adapter(db_session, name="GST Verify", category="gst")

        registry = AdapterRegistry(db_session)
        adapters = await registry.list_adapters()

        stub = _StubLLM(
            payload={
                "matches": [
                    {"adapter_id": a_id, "version_id": a_ver, "score": 0.92, "reason": "kyc/aadhaar match"},
                    {"adapter_id": d_id, "version_id": d_ver, "score": 0.45, "reason": "shares verify"},
                    {"adapter_id": b_id, "version_id": b_ver, "score": 0.20, "reason": "wrong domain"},
                    {"adapter_id": c_id, "version_id": c_ver, "score": 0.10, "reason": "wrong domain"},
                ]
            }
        )

        matches, suggest_custom = await suggest_adapters(
            _parsed_doc(), adapters, llm_client=stub
        )

        assert len(matches) == 3
        scores = [m["score"] for m in matches]
        assert scores == sorted(scores, reverse=True)
        assert matches[0]["adapter_id"] == a_id
        assert matches[0]["score"] == 0.92
        assert suggest_custom is False

    @pytest.mark.asyncio
    async def test_suggest_custom_true_when_best_score_below_threshold(
        self, db_session: AsyncSession
    ) -> None:
        a_id, a_ver = await _seed_adapter(db_session, name="Aadhaar eKYC", category="kyc")
        b_id, b_ver = await _seed_adapter(db_session, name="CIBIL Bureau", category="bureau")

        registry = AdapterRegistry(db_session)
        adapters = await registry.list_adapters()

        # Best score is 0.40 — below the 0.55 cutoff.
        stub = _StubLLM(
            payload={
                "matches": [
                    {"adapter_id": a_id, "version_id": a_ver, "score": 0.40, "reason": "weak"},
                    {"adapter_id": b_id, "version_id": b_ver, "score": 0.30, "reason": "weaker"},
                ]
            }
        )

        matches, suggest_custom = await suggest_adapters(
            _parsed_doc(), adapters, llm_client=stub
        )

        assert suggest_custom is True
        assert matches[0]["score"] == 0.40
        assert matches[0]["score"] < SUGGEST_CUSTOM_THRESHOLD

    @pytest.mark.asyncio
    async def test_suggest_custom_false_when_best_score_at_or_above_threshold(
        self, db_session: AsyncSession
    ) -> None:
        a_id, a_ver = await _seed_adapter(db_session, name="Aadhaar eKYC", category="kyc")
        registry = AdapterRegistry(db_session)
        adapters = await registry.list_adapters()

        stub = _StubLLM(
            payload={
                "matches": [
                    {"adapter_id": a_id, "version_id": a_ver, "score": 0.55, "reason": "borderline"},
                ]
            }
        )

        _, suggest_custom = await suggest_adapters(_parsed_doc(), adapters, llm_client=stub)
        assert suggest_custom is False

    @pytest.mark.asyncio
    async def test_unknown_adapter_id_is_dropped(self, db_session: AsyncSession) -> None:
        a_id, a_ver = await _seed_adapter(db_session, name="Aadhaar eKYC", category="kyc")
        registry = AdapterRegistry(db_session)
        adapters = await registry.list_adapters()

        stub = _StubLLM(
            payload={
                "matches": [
                    {"adapter_id": "ghost", "version_id": "ghost-v", "score": 0.95, "reason": "fake"},
                    {"adapter_id": a_id, "version_id": a_ver, "score": 0.80, "reason": "real"},
                ]
            }
        )

        matches, suggest_custom = await suggest_adapters(
            _parsed_doc(), adapters, llm_client=stub
        )

        ids = [m["adapter_id"] for m in matches]
        assert "ghost" not in ids
        assert a_id in ids
        assert suggest_custom is False

    @pytest.mark.asyncio
    async def test_hallucinated_version_id_is_pinned_to_first_version(
        self, db_session: AsyncSession
    ) -> None:
        a_id, _ = await _seed_adapter(db_session, name="Aadhaar eKYC", category="kyc")
        registry = AdapterRegistry(db_session)
        adapters = await registry.list_adapters()

        stub = _StubLLM(
            payload={
                "matches": [
                    {
                        "adapter_id": a_id,
                        "version_id": "v-does-not-exist",
                        "score": 0.85,
                        "reason": "match",
                    }
                ]
            }
        )

        matches, _ = await suggest_adapters(_parsed_doc(), adapters, llm_client=stub)
        assert matches, "Expected the adapter to survive with a pinned version_id"
        # The pinned version_id is the real one we seeded.
        real_version_id = (await registry.get_adapter(a_id)).versions[0].id
        assert matches[0]["version_id"] == real_version_id

    @pytest.mark.asyncio
    async def test_score_is_clamped_to_unit_interval(self, db_session: AsyncSession) -> None:
        a_id, a_ver = await _seed_adapter(db_session, name="Aadhaar eKYC", category="kyc")
        registry = AdapterRegistry(db_session)
        adapters = await registry.list_adapters()

        stub = _StubLLM(
            payload={
                "matches": [
                    {"adapter_id": a_id, "version_id": a_ver, "score": 5.0, "reason": "out of range"},
                ]
            }
        )

        matches, _ = await suggest_adapters(_parsed_doc(), adapters, llm_client=stub)
        assert matches[0]["score"] <= 1.0
        assert matches[0]["score"] >= 0.0


class TestSuggestAdaptersDegraded:
    @pytest.mark.asyncio
    async def test_empty_catalogue_returns_suggest_custom_true(self) -> None:
        stub = _StubLLM()
        matches, suggest_custom = await suggest_adapters(
            _parsed_doc(), [], llm_client=stub
        )
        assert matches == []
        assert suggest_custom is True
        # No LLM call needed when there is nothing to rank.
        assert stub.calls == []

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_heuristic(self, db_session: AsyncSession) -> None:
        # Aadhaar adapter should heuristically beat the unrelated one.
        a_id, _ = await _seed_adapter(
            db_session,
            name="Aadhaar eKYC Provider",
            category="kyc",
            description="Aadhaar-based KYC verification",
        )
        await _seed_adapter(
            db_session,
            name="SMS Gateway",
            category="notification",
            description="Send SMS messages",
        )
        registry = AdapterRegistry(db_session)
        adapters = await registry.list_adapters()

        stub = _StubLLM(raises=LLMAPIError("simulated outage"))

        matches, _suggest_custom = await suggest_adapters(
            _parsed_doc(), adapters, llm_client=stub
        )
        assert matches, "Heuristic fallback should still produce ranked results"
        assert matches[0]["adapter_id"] == a_id


class TestSuggestAdaptersPromptShape:
    @pytest.mark.asyncio
    async def test_prompt_uses_compact_budget(self, db_session: AsyncSession) -> None:
        a_id, a_ver = await _seed_adapter(db_session, name="Aadhaar eKYC", category="kyc")
        registry = AdapterRegistry(db_session)
        adapters = await registry.list_adapters()

        stub = _StubLLM(
            payload={
                "matches": [
                    {"adapter_id": a_id, "version_id": a_ver, "score": 0.9, "reason": "ok"}
                ]
            }
        )

        await suggest_adapters(_parsed_doc(), adapters, llm_client=stub)

        assert stub.calls, "LLM should be called exactly once"
        call = stub.calls[0]
        assert call["max_tokens"] == 1024
        assert call["temperature"] == 0.0
        assert call["system_instruction"]
        # The prompt should include the adapter name so the model can ground.
        assert "Aadhaar eKYC" in call["prompt"]
        assert "Simple KYC Verification API" in call["prompt"]
