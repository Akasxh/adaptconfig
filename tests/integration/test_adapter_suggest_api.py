"""Integration tests for ``POST /api/v1/adapters/suggest``.

Uploads the simple KYC fixture via the existing API, seeds the eight
catalogue adapters, and confirms the LLM-stubbed ranking surfaces the
Aadhaar eKYC Provider as the top match.

The LLM client is patched module-locally so the route runs through its
real plumbing (request validation, tenant scoping, registry lookup, schema
serialisation) without making a network call.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.adapter import Adapter, AdapterVersion

# The fixture lives at the repo root, not under tests/fixtures/.
FIXTURES_DIR = Path(__file__).parent.parent.parent / "test_fixtures"


class _StubLLM:
    """LLM stub that returns a canned ranking payload for each call."""

    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response
        self.calls = 0

    async def generate_json(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        self.calls += 1
        return self._response


async def _seed_catalogue(db: AsyncSession) -> dict[str, dict[str, str]]:
    """Seed the eight catalogue adapters.

    Returns ``{adapter_name: {"adapter_id": ..., "version_id": ...}}`` for
    the first version of each, which is what the test asserts against.
    """
    catalogue_path = (
        Path(__file__).parent.parent.parent
        / "src"
        / "finspark"
        / "seeds"
        / "adapters.json"
    )
    seeded: dict[str, dict[str, str]] = {}
    with catalogue_path.open() as f:
        catalogue = json.load(f)

    for entry in catalogue:
        adapter = Adapter(
            name=entry["name"],
            category=entry["category"],
            description=entry.get("description", ""),
            is_active=True,
            icon=entry.get("icon"),
        )
        db.add(adapter)
        await db.flush()
        first_version: AdapterVersion | None = None
        for idx, ver in enumerate(entry.get("versions", [])):
            av = AdapterVersion(
                adapter_id=adapter.id,
                version=ver["version"],
                version_order=idx + 1,
                base_url=ver.get("base_url"),
                auth_type=ver.get("auth_type", "api_key"),
                endpoints=json.dumps(ver.get("endpoints", [])),
                request_schema=(
                    json.dumps(ver["request_schema"]) if ver.get("request_schema") else None
                ),
                response_schema=(
                    json.dumps(ver["response_schema"]) if ver.get("response_schema") else None
                ),
            )
            db.add(av)
            await db.flush()
            if first_version is None:
                first_version = av
        assert first_version is not None, f"Adapter {entry['name']} has no versions"
        seeded[entry["name"]] = {
            "adapter_id": adapter.id,
            "version_id": first_version.id,
        }
    await db.commit()
    return seeded


async def _upload_kyc_fixture(client: AsyncClient) -> str:
    """Upload ``test_fixtures/01_simple_kyc_api.yaml`` and return the doc ID.

    The upload route runs an inline regex parse when ``settings.ai_enabled``
    is False, so the document arrives with ``status=parsed`` and a populated
    ``parsed_result`` ready for the suggest endpoint.
    """
    fixture = FIXTURES_DIR / "01_simple_kyc_api.yaml"
    assert fixture.exists(), f"Missing fixture: {fixture}"
    with fixture.open("rb") as fh:
        resp = await client.post(
            "/api/v1/documents/upload",
            files={"file": (fixture.name, fh, "application/x-yaml")},
            params={"doc_type": "api_spec"},
        )
    assert resp.status_code == 200, f"Upload failed: {resp.text}"
    return resp.json()["data"]["id"]


class TestAdapterSuggestEndpoint:
    @pytest.mark.asyncio
    async def test_top_match_is_aadhaar_ekyc(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Upload the KYC fixture, suggest adapters, expect Aadhaar eKYC at the top."""
        # Disable the LLM-driven upload background path so parsing finishes inline.
        with patch("finspark.api.routes.documents.settings") as mock_settings:
            mock_settings.ai_enabled = False
            mock_settings.openai_api_key = ""
            mock_settings.openrouter_api_key = ""
            mock_settings.gemini_api_key = ""
            mock_settings.upload_dir = Path("./uploads")
            mock_settings.max_upload_size_mb = 50

            seeded = await _seed_catalogue(db_session)
            doc_id = await _upload_kyc_fixture(client)

            # Confirm the upload completed parsing.
            detail = await client.get(f"/api/v1/documents/{doc_id}")
            assert detail.status_code == 200, detail.text
            assert detail.json()["data"]["status"] == "parsed"

        # Stub the LLM client used inside the suggest route. The stub returns a
        # ranking where the Aadhaar eKYC Provider wins.
        aadhaar = seeded["Aadhaar eKYC Provider"]
        cibil = seeded["CIBIL Credit Bureau"]
        gst = seeded["GST Verification Service"]
        stub = _StubLLM(
            {
                "matches": [
                    {
                        "adapter_id": aadhaar["adapter_id"],
                        "version_id": aadhaar["version_id"],
                        "score": 0.92,
                        "reason": "Document targets Aadhaar-based KYC verification.",
                    },
                    {
                        "adapter_id": gst["adapter_id"],
                        "version_id": gst["version_id"],
                        "score": 0.32,
                        "reason": "Related identity verification but for GST, not KYC.",
                    },
                    {
                        "adapter_id": cibil["adapter_id"],
                        "version_id": cibil["version_id"],
                        "score": 0.18,
                        "reason": "Different domain (credit bureau).",
                    },
                ]
            }
        )

        with patch(
            "finspark.api.routes.adapters.get_llm_client", return_value=stub
        ):
            resp = await client.post(
                "/api/v1/adapters/suggest", json={"document_id": doc_id}
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert isinstance(data["matches"], list)
        assert len(data["matches"]) == 3
        assert data["suggest_custom"] is False
        top = data["matches"][0]
        assert top["adapter_name"] == "Aadhaar eKYC Provider"
        assert top["score"] == 0.92
        assert top["adapter_id"] == aadhaar["adapter_id"]
        assert top["version_id"] == aadhaar["version_id"]
        # Scores stay sorted desc.
        scores = [m["score"] for m in data["matches"]]
        assert scores == sorted(scores, reverse=True)
        assert stub.calls == 1, "LLM stub should be hit exactly once"

    @pytest.mark.asyncio
    async def test_suggest_custom_true_when_no_match_passes_threshold(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        with patch("finspark.api.routes.documents.settings") as mock_settings:
            mock_settings.ai_enabled = False
            mock_settings.openai_api_key = ""
            mock_settings.openrouter_api_key = ""
            mock_settings.gemini_api_key = ""
            mock_settings.upload_dir = Path("./uploads")
            mock_settings.max_upload_size_mb = 50

            seeded = await _seed_catalogue(db_session)
            doc_id = await _upload_kyc_fixture(client)

        # All scores below the 0.55 threshold → suggest_custom should be True.
        aadhaar = seeded["Aadhaar eKYC Provider"]
        gst = seeded["GST Verification Service"]
        stub = _StubLLM(
            {
                "matches": [
                    {
                        "adapter_id": aadhaar["adapter_id"],
                        "version_id": aadhaar["version_id"],
                        "score": 0.40,
                        "reason": "Weak.",
                    },
                    {
                        "adapter_id": gst["adapter_id"],
                        "version_id": gst["version_id"],
                        "score": 0.20,
                        "reason": "Weaker.",
                    },
                ]
            }
        )

        with patch(
            "finspark.api.routes.adapters.get_llm_client", return_value=stub
        ):
            resp = await client.post(
                "/api/v1/adapters/suggest", json={"document_id": doc_id}
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["data"]["suggest_custom"] is True
        assert body["data"]["matches"][0]["score"] == 0.40

    @pytest.mark.asyncio
    async def test_missing_document_returns_404(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/adapters/suggest", json={"document_id": "does-not-exist"}
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_unparsed_document_returns_422(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from finspark.models.document import Document

        doc = Document(
            tenant_id="test-tenant",
            filename="empty.yaml",
            file_type="yaml",
            file_size=0,
            doc_type="api_spec",
            status="uploaded",
        )
        db_session.add(doc)
        await db_session.commit()

        resp = await client.post(
            "/api/v1/adapters/suggest", json={"document_id": doc.id}
        )
        assert resp.status_code == 422
