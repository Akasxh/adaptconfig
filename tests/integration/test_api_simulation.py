"""API integration tests for simulation, configuration, and adapter routes."""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.adapter import Adapter, AdapterVersion
from finspark.models.configuration import Configuration
from finspark.models.document import Document
from finspark.models.simulation import Simulation


# ---------------------------------------------------------------------------
# Helpers — seed minimal DB rows
# ---------------------------------------------------------------------------


async def _create_adapter(db: AsyncSession) -> tuple[Adapter, AdapterVersion]:
    adapter = Adapter(
        name="CIBIL Credit Bureau",
        category="bureau",
        description="Test adapter",
        is_active=True,
        icon="credit-card",
    )
    db.add(adapter)
    await db.flush()

    version = AdapterVersion(
        adapter_id=adapter.id,
        version="v1",
        version_order=1,
        status="active",
        base_url="https://api.cibil.com/v1",
        auth_type="api_key",
        endpoints=json.dumps(
            [{"path": "/credit-score", "method": "POST", "description": "Fetch credit score"}]
        ),
        request_schema=json.dumps(
            {
                "type": "object",
                "required": ["pan_number"],
                "properties": {"pan_number": {"type": "string"}},
            }
        ),
        response_schema=json.dumps(
            {"type": "object", "properties": {"credit_score": {"type": "integer"}}}
        ),
    )
    db.add(version)
    await db.flush()
    return adapter, version


async def _create_configuration(db: AsyncSession, adapter_version_id: str) -> Configuration:
    full_config = {
        "adapter_name": "CIBIL Credit Bureau",
        "version": "v1",
        "base_url": "https://api.cibil.com/v1",
        "auth": {"type": "api_key"},
        "endpoints": [{"path": "/credit-score", "method": "POST"}],
        "field_mappings": [
            {"source_field": "pan_number", "target_field": "pan", "confidence": 0.95}
        ],
        "transformation_rules": [],
        "hooks": [],
        "retry_policy": {"max_retries": 3},
    }
    config = Configuration(
        tenant_id="test-tenant",
        name="Test Config",
        adapter_version_id=adapter_version_id,
        status="configured",
        version=1,
        field_mappings=json.dumps(full_config["field_mappings"]),
        transformation_rules=json.dumps([]),
        hooks=json.dumps([]),
        full_config=json.dumps(full_config),
    )
    db.add(config)
    await db.flush()
    return config


async def _create_document(db: AsyncSession) -> Document:
    parsed_result = {
        "doc_type": "brd",
        "confidence_score": 0.9,
        "fields": [
            {"name": "pan_number", "field_type": "string", "is_required": True, "confidence": 0.95}
        ],
        "endpoints": [{"path": "/credit-score", "method": "POST", "description": "Score"}],
        "services_identified": ["CIBIL"],
        "auth_requirements": [],
    }
    doc = Document(
        tenant_id="test-tenant",
        filename="test.txt",
        file_type="txt",
        file_size=100,
        doc_type="brd",
        status="parsed",
        raw_text="Sample BRD text",
        parsed_result=json.dumps(parsed_result),
    )
    db.add(doc)
    await db.flush()
    return doc


async def _seed_adapters(db: AsyncSession) -> list[tuple[Adapter, AdapterVersion]]:
    """Seed 8 adapters to match the expected production seed data."""
    adapter_defs = [
        ("CIBIL Credit Bureau", "bureau", "credit-card"),
        ("Aadhaar eKYC Provider", "kyc", "shield-check"),
        ("GST Verification Service", "gst", "building"),
        ("Payment Gateway", "payment", "wallet"),
        ("Fraud Detection Engine", "fraud", "alert-triangle"),
        ("SMS Gateway", "notification", "message-square"),
        ("Account Aggregator (AA Framework)", "open_banking", "link"),
        ("Email Notification Gateway", "notification", "mail"),
    ]
    results = []
    for name, category, icon in adapter_defs:
        adapter = Adapter(
            name=name,
            category=category,
            description=f"{name} integration",
            is_active=True,
            icon=icon,
        )
        db.add(adapter)
        await db.flush()

        version = AdapterVersion(
            adapter_id=adapter.id,
            version="v1",
            version_order=1,
            status="active",
            base_url=f"https://api.{name.lower().replace(' ', '-')}.com/v1",
            auth_type="api_key",
            endpoints=json.dumps([{"path": "/verify", "method": "POST", "description": "Verify"}]),
        )
        db.add(version)
        await db.flush()
        results.append((adapter, version))
    return results


# ---------------------------------------------------------------------------
# Simulation Route Tests
# ---------------------------------------------------------------------------


class TestSimulationRoutes:
    @pytest.mark.asyncio
    async def test_run_simulation_valid_config(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """POST /simulations/run with valid config_id returns 200 with simulation result."""
        _, version = await _create_adapter(db_session)
        config = await _create_configuration(db_session, version.id)

        response = await client.post(
            "/api/v1/simulations/run",
            json={"configuration_id": config.id, "test_type": "smoke"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["configuration_id"] == config.id
        assert body["data"]["status"] in ("passed", "failed")
        assert body["data"]["total_tests"] >= 0

    @pytest.mark.asyncio
    async def test_run_simulation_invalid_config_id(self, client: AsyncClient) -> None:
        """POST /simulations/run with non-existent config_id returns 404."""
        response = await client.post(
            "/api/v1/simulations/run",
            json={"configuration_id": "nonexistent-config-id", "test_type": "smoke"},
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_get_simulation_valid_id(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /simulations/{id} with valid id returns 200."""
        _, version = await _create_adapter(db_session)
        config = await _create_configuration(db_session, version.id)

        simulation = Simulation(
            tenant_id="test-tenant",
            configuration_id=config.id,
            status="passed",
            test_type="smoke",
            total_tests=3,
            passed_tests=3,
            failed_tests=0,
            duration_ms=150,
            results=json.dumps([]),
        )
        db_session.add(simulation)
        await db_session.flush()

        response = await client.get(f"/api/v1/simulations/{simulation.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["id"] == simulation.id
        assert body["data"]["status"] == "passed"

    @pytest.mark.asyncio
    async def test_get_simulation_invalid_id(self, client: AsyncClient) -> None:
        """GET /simulations/{id} with non-existent id returns 404."""
        response = await client.get("/api/v1/simulations/nonexistent-id")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_stream_simulation_returns_sse_content_type(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /simulations/{id}/stream returns text/event-stream after Simulation lookup."""
        _, version = await _create_adapter(db_session)
        config = await _create_configuration(db_session, version.id)

        # Create a Simulation record linked to the configuration
        simulation = Simulation(
            tenant_id="test-tenant",
            configuration_id=config.id,
            status="passed",
            test_type="smoke",
        )
        db_session.add(simulation)
        await db_session.flush()

        response = await client.get(f"/api/v1/simulations/{simulation.id}/stream")
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

    @pytest.mark.asyncio
    async def test_stream_simulation_invalid_id(self, client: AsyncClient) -> None:
        """GET /simulations/{id}/stream with non-existent simulation id returns 404."""
        response = await client.get("/api/v1/simulations/nonexistent-id/stream")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Configuration Route Tests
# ---------------------------------------------------------------------------


class TestConfigurationRoutes:
    @pytest.mark.asyncio
    async def test_get_configuration_valid_id(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /configurations/{id} with valid id returns 200 with config data."""
        _, version = await _create_adapter(db_session)
        config = await _create_configuration(db_session, version.id)

        response = await client.get(f"/api/v1/configurations/{config.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["id"] == config.id
        assert body["data"]["name"] == "Test Config"
        assert body["data"]["status"] == "configured"
        assert isinstance(body["data"]["field_mappings"], list)

    @pytest.mark.asyncio
    async def test_get_configuration_invalid_id(self, client: AsyncClient) -> None:
        """GET /configurations/{id} with non-existent id returns 404."""
        response = await client.get("/api/v1/configurations/nonexistent-id")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_generate_configuration_valid_data(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """POST /configurations/generate with valid doc and adapter returns 200."""
        _, version = await _create_adapter(db_session)
        doc = await _create_document(db_session)

        response = await client.post(
            "/api/v1/configurations/generate",
            json={
                "document_id": doc.id,
                "adapter_version_id": version.id,
                "name": "Generated Config",
                "auto_map": True,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["name"] == "Generated Config"
        assert body["data"]["status"] == "configured"
        assert body["data"]["adapter_version_id"] == version.id
        assert body["data"]["document_id"] == doc.id
        assert body["message"] == "Configuration generated successfully"

    @pytest.mark.asyncio
    async def test_generate_configuration_missing_document(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """POST /configurations/generate with invalid document_id returns 404."""
        _, version = await _create_adapter(db_session)

        response = await client.post(
            "/api/v1/configurations/generate",
            json={
                "document_id": "nonexistent-doc",
                "adapter_version_id": version.id,
                "name": "Bad Config",
            },
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_generate_configuration_missing_adapter(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """POST /configurations/generate with invalid adapter_version_id returns 404."""
        doc = await _create_document(db_session)

        response = await client.post(
            "/api/v1/configurations/generate",
            json={
                "document_id": doc.id,
                "adapter_version_id": "nonexistent-adapter-version",
                "name": "Bad Config",
            },
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Adapter Route Tests
# ---------------------------------------------------------------------------


class TestAdapterRoutes:
    @pytest.mark.asyncio
    async def test_list_adapters_returns_all_8(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /adapters/ returns all 8 seeded adapters."""
        await _seed_adapters(db_session)

        response = await client.get("/api/v1/adapters/")
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["total"] == 8
        assert len(body["data"]["adapters"]) == 8

    @pytest.mark.asyncio
    async def test_list_adapters_returns_correct_categories(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /adapters/ returns the expected categories."""
        await _seed_adapters(db_session)

        response = await client.get("/api/v1/adapters/")
        assert response.status_code == 200
        body = response.json()
        categories = set(body["data"]["categories"])
        expected_categories = {"bureau", "kyc", "gst", "payment", "fraud", "notification", "open_banking"}
        assert expected_categories == categories

    @pytest.mark.asyncio
    async def test_list_adapters_empty_returns_200(self, client: AsyncClient) -> None:
        """GET /adapters/ with empty DB returns 200 with empty list."""
        response = await client.get("/api/v1/adapters/")
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["total"] == 0
        assert body["data"]["adapters"] == []

    @pytest.mark.asyncio
    async def test_list_adapters_filter_by_category(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /adapters/?category=bureau returns only bureau adapters."""
        await _seed_adapters(db_session)

        response = await client.get("/api/v1/adapters/?category=bureau")
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["total"] == 1
        assert body["data"]["adapters"][0]["category"] == "bureau"
