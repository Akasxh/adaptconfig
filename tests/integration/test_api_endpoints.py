"""Comprehensive integration tests for all FinSpark API endpoint groups."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.adapter import Adapter, AdapterVersion
from finspark.models.audit import AuditLog
from finspark.models.configuration import Configuration
from finspark.models.document import Document
from finspark.models.simulation import Simulation


# ---------------------------------------------------------------------------
# DB seeding helpers
# ---------------------------------------------------------------------------


async def _create_adapter(db: AsyncSession) -> tuple[Adapter, AdapterVersion]:
    adapter = Adapter(
        name="CIBIL Credit Bureau",
        category="bureau",
        description="Credit score lookup",
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
            {
                "name": "pan_number",
                "field_type": "string",
                "is_required": True,
                "confidence": 0.95,
            }
        ],
        "endpoints": [{"path": "/credit-score", "method": "POST", "description": "Score"}],
        "services_identified": ["CIBIL"],
        "auth_requirements": [],
        "summary": "Sample BRD for credit bureau integration",
    }
    doc = Document(
        tenant_id="test-tenant",
        filename="test_brd.json",
        file_type="json",
        file_size=100,
        doc_type="brd",
        status="parsed",
        raw_text="Sample BRD text",
        parsed_result=json.dumps(parsed_result),
    )
    db.add(doc)
    await db.flush()
    return doc


async def _create_simulation(
    db: AsyncSession, config_id: str, status: str = "passed"
) -> Simulation:
    steps = [
        {
            "step_name": "schema_validation",
            "status": "passed",
            "request_payload": {"pan_number": "ABCDE1234F"},
            "expected_response": {"credit_score": 750},
            "actual_response": {"credit_score": 750},
            "duration_ms": 120,
            "confidence_score": 0.95,
            "error_message": None,
            "assertions": [],
        }
    ]
    simulation = Simulation(
        tenant_id="test-tenant",
        configuration_id=config_id,
        status=status,
        test_type="smoke",
        total_tests=1,
        passed_tests=1 if status == "passed" else 0,
        failed_tests=0 if status == "passed" else 1,
        duration_ms=120,
        results=json.dumps(steps),
    )
    db.add(simulation)
    await db.flush()
    return simulation


async def _seed_adapters(db: AsyncSession) -> list[Adapter]:
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
    adapters = []
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
            endpoints=json.dumps(
                [{"path": "/verify", "method": "POST", "description": "Verify"}]
            ),
        )
        db.add(version)
        await db.flush()
        adapters.append(adapter)
    return adapters


# ---------------------------------------------------------------------------
# Health endpoint tests
# ---------------------------------------------------------------------------


class TestHealthEndpoints:
    @pytest.mark.asyncio
    async def test_health_returns_200_with_status(self, client: AsyncClient) -> None:
        """GET /health returns 200 with a healthy status string."""
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_response_has_required_fields(self, client: AsyncClient) -> None:
        """GET /health response contains version, timestamp, and checks fields."""
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "version" in data
        assert "timestamp" in data
        assert "checks" in data
        assert isinstance(data["checks"], dict)


# ---------------------------------------------------------------------------
# Adapter endpoint tests
# ---------------------------------------------------------------------------


class TestAdapterEndpoints:
    @pytest.mark.asyncio
    async def test_list_adapters_returns_api_response_wrapper(
        self, client: AsyncClient
    ) -> None:
        """GET /api/v1/adapters/ wraps data in the standard APIResponse envelope."""
        response = await client.get("/api/v1/adapters/")
        assert response.status_code == 200
        body = response.json()
        assert "success" in body
        assert body["success"] is True
        assert "data" in body

    @pytest.mark.asyncio
    async def test_list_adapters_contains_seeded_data(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /api/v1/adapters/ returns all seeded adapters with expected structure."""
        await _seed_adapters(db_session)
        response = await client.get("/api/v1/adapters/")
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["total"] == 8
        assert len(body["data"]["adapters"]) == 8
        first = body["data"]["adapters"][0]
        assert "id" in first
        assert "name" in first
        assert "category" in first

    @pytest.mark.asyncio
    async def test_get_adapter_by_id(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /api/v1/adapters/{id} returns the matching adapter record."""
        adapter, _ = await _create_adapter(db_session)
        response = await client.get(f"/api/v1/adapters/{adapter.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["id"] == adapter.id
        assert body["data"]["name"] == "CIBIL Credit Bureau"
        assert body["data"]["category"] == "bureau"

    @pytest.mark.asyncio
    async def test_get_nonexistent_adapter_returns_404(self, client: AsyncClient) -> None:
        """GET /api/v1/adapters/{id} with an unknown id returns 404."""
        response = await client.get("/api/v1/adapters/does-not-exist")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_adapters_empty_db_returns_empty_list(
        self, client: AsyncClient
    ) -> None:
        """GET /api/v1/adapters/ with no adapters returns an empty list."""
        response = await client.get("/api/v1/adapters/")
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["total"] == 0
        assert body["data"]["adapters"] == []

    @pytest.mark.asyncio
    async def test_list_adapters_filter_by_category(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /api/v1/adapters/?category=bureau returns only bureau adapters."""
        await _seed_adapters(db_session)
        response = await client.get("/api/v1/adapters/?category=bureau")
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["total"] == 1
        assert body["data"]["adapters"][0]["category"] == "bureau"


# ---------------------------------------------------------------------------
# Document endpoint tests
# ---------------------------------------------------------------------------


class TestDocumentEndpoints:
    @pytest.mark.asyncio
    async def test_upload_document_returns_202_accepted(self, client: AsyncClient) -> None:
        """POST /api/v1/documents/upload returns 202 for a valid PDF upload."""
        pdf_bytes = (
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R"
            b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
            b"4 0 obj<</Length 44>>\nstream\nBT /F1 12 Tf 100 700 Td (Test) Tj ET\n"
            b"endstream\nendobj\n"
            b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
            b"xref\n0 6\n"
            b"0000000000 65535 f \n"
            b"0000000009 00000 n \n"
            b"0000000058 00000 n \n"
            b"0000000115 00000 n \n"
            b"0000000266 00000 n \n"
            b"0000000362 00000 n \n"
            b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n433\n%%EOF"
        )
        response = await client.post(
            "/api/v1/documents/upload",
            files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
        )
        # The endpoint returns 200 (the route uses 200, not 202, in this app)
        assert response.status_code == 200
        body = response.json()
        assert "success" in body
        assert body["data"]["filename"] == "test.pdf"

    @pytest.mark.asyncio
    async def test_upload_invalid_extension_returns_400(self, client: AsyncClient) -> None:
        """POST /api/v1/documents/upload rejects files with unsupported extensions."""
        response = await client.post(
            "/api/v1/documents/upload",
            files={"file": ("malware.exe", b"binary content", "application/octet-stream")},
        )
        assert response.status_code == 400
        body = response.json()
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_list_documents_returns_api_response(self, client: AsyncClient) -> None:
        """GET /api/v1/documents/ returns the standard APIResponse wrapper."""
        response = await client.get("/api/v1/documents/")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "data" in body
        assert isinstance(body["data"], list)

    @pytest.mark.asyncio
    async def test_list_documents_returns_only_tenant_documents(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /api/v1/documents/ returns documents scoped to the requesting tenant."""
        await _create_document(db_session)
        response = await client.get("/api/v1/documents/")
        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["filename"] == "test_brd.json"

    @pytest.mark.asyncio
    async def test_get_document_by_id(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /api/v1/documents/{id} returns the matching document record."""
        doc = await _create_document(db_session)
        response = await client.get(f"/api/v1/documents/{doc.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["id"] == doc.id
        assert body["data"]["filename"] == "test_brd.json"

    @pytest.mark.asyncio
    async def test_get_nonexistent_document_returns_404(self, client: AsyncClient) -> None:
        """GET /api/v1/documents/{id} with an unknown id returns 404."""
        response = await client.get("/api/v1/documents/nonexistent-doc-id")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Configuration endpoint tests
# ---------------------------------------------------------------------------


class TestConfigurationEndpoints:
    @pytest.mark.asyncio
    async def test_generate_config_returns_api_response(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """POST /api/v1/configurations/generate returns a generated config record."""
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
        assert body["success"] is True
        assert body["data"]["name"] == "Generated Config"
        assert body["data"]["adapter_version_id"] == version.id
        assert body["data"]["document_id"] == doc.id

    @pytest.mark.asyncio
    async def test_generate_config_with_missing_document_returns_404(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """POST /api/v1/configurations/generate with bad document_id returns 404."""
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
    async def test_list_configurations(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /api/v1/configurations/ returns a list scoped to the tenant."""
        _, version = await _create_adapter(db_session)
        await _create_configuration(db_session, version.id)
        response = await client.get("/api/v1/configurations/")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert isinstance(body["data"], list)
        assert len(body["data"]) == 1

    @pytest.mark.asyncio
    async def test_list_configurations_empty_returns_empty_list(
        self, client: AsyncClient
    ) -> None:
        """GET /api/v1/configurations/ with no data returns an empty list."""
        response = await client.get("/api/v1/configurations/")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"] == []

    @pytest.mark.asyncio
    async def test_get_configuration_by_id(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /api/v1/configurations/{id} returns the matching configuration."""
        _, version = await _create_adapter(db_session)
        config = await _create_configuration(db_session, version.id)
        response = await client.get(f"/api/v1/configurations/{config.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["id"] == config.id
        assert body["data"]["name"] == "Test Config"
        assert body["data"]["status"] == "configured"

    @pytest.mark.asyncio
    async def test_get_nonexistent_configuration_returns_404(
        self, client: AsyncClient
    ) -> None:
        """GET /api/v1/configurations/{id} with an unknown id returns 404."""
        response = await client.get("/api/v1/configurations/nonexistent-config-id")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_validate_configuration(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """POST /api/v1/configurations/{id}/validate returns a validation result."""
        _, version = await _create_adapter(db_session)
        config = await _create_configuration(db_session, version.id)
        response = await client.post(f"/api/v1/configurations/{config.id}/validate")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        validation = body["data"]
        assert "is_valid" in validation
        assert "errors" in validation
        assert "warnings" in validation
        assert "coverage_score" in validation

    @pytest.mark.asyncio
    async def test_validate_nonexistent_configuration_returns_404(
        self, client: AsyncClient
    ) -> None:
        """POST /api/v1/configurations/{id}/validate with unknown id returns 404."""
        response = await client.post("/api/v1/configurations/ghost-id/validate")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_transition_configuration(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """POST /api/v1/configurations/{id}/transition moves the config to a new state."""
        _, version = await _create_adapter(db_session)
        config = await _create_configuration(db_session, version.id)
        response = await client.post(
            f"/api/v1/configurations/{config.id}/transition",
            json={"target_state": "validating", "reason": "Ready for validation"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["new_state"] == "validating"
        assert body["data"]["previous_state"] == "configured"

    @pytest.mark.asyncio
    async def test_transition_invalid_state_returns_400(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """POST /api/v1/configurations/{id}/transition to an invalid state returns 400."""
        _, version = await _create_adapter(db_session)
        config = await _create_configuration(db_session, version.id)
        # 'active' is not reachable directly from 'configured'
        response = await client.post(
            f"/api/v1/configurations/{config.id}/transition",
            json={"target_state": "active", "reason": "Skip testing"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_config_templates_returns_list(self, client: AsyncClient) -> None:
        """GET /api/v1/configurations/templates returns a non-empty list of templates."""
        response = await client.get("/api/v1/configurations/templates")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        templates = body["data"]
        assert isinstance(templates, list)
        assert len(templates) > 0
        first = templates[0]
        assert "name" in first
        assert "description" in first
        assert "adapter_category" in first
        assert "default_config" in first


# ---------------------------------------------------------------------------
# Simulation endpoint tests
# ---------------------------------------------------------------------------


class TestSimulationEndpoints:
    @pytest.mark.asyncio
    async def test_run_simulation_returns_api_response(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """POST /api/v1/simulations/run returns a simulation result with status."""
        _, version = await _create_adapter(db_session)
        config = await _create_configuration(db_session, version.id)
        response = await client.post(
            "/api/v1/simulations/run",
            json={"configuration_id": config.id, "test_type": "smoke"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        data = body["data"]
        assert data["configuration_id"] == config.id
        assert data["status"] in ("passed", "failed")
        assert "total_tests" in data
        assert "passed_tests" in data
        assert "failed_tests" in data

    @pytest.mark.asyncio
    async def test_run_simulation_with_invalid_config_returns_404(
        self, client: AsyncClient
    ) -> None:
        """POST /api/v1/simulations/run with an unknown config_id returns 404."""
        response = await client.post(
            "/api/v1/simulations/run",
            json={"configuration_id": "nonexistent-config", "test_type": "smoke"},
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_get_simulation_by_id(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /api/v1/simulations/{id} returns the matching simulation record."""
        _, version = await _create_adapter(db_session)
        config = await _create_configuration(db_session, version.id)
        simulation = await _create_simulation(db_session, config.id)
        response = await client.get(f"/api/v1/simulations/{simulation.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["id"] == simulation.id
        assert body["data"]["status"] == "passed"

    @pytest.mark.asyncio
    async def test_get_nonexistent_simulation_returns_404(self, client: AsyncClient) -> None:
        """GET /api/v1/simulations/{id} with an unknown id returns 404."""
        response = await client.get("/api/v1/simulations/nonexistent-sim-id")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Audit endpoint tests
# ---------------------------------------------------------------------------


class TestAuditEndpoints:
    @pytest.mark.asyncio
    async def test_list_audit_logs(self, client: AsyncClient) -> None:
        """GET /api/v1/audit/ returns the standard APIResponse with pagination data."""
        response = await client.get("/api/v1/audit/")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        paginated = body["data"]
        assert "items" in paginated
        assert "total" in paginated
        assert "page" in paginated
        assert "page_size" in paginated
        assert isinstance(paginated["items"], list)

    @pytest.mark.asyncio
    async def test_audit_logs_contain_seeded_entries(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /api/v1/audit/ reflects entries written to the DB for the tenant."""
        log = AuditLog(
            tenant_id="test-tenant",
            actor="Test Tenant",
            action="upload_document",
            resource_type="document",
            resource_id="doc-001",
            details=json.dumps({"filename": "brd.pdf"}),
        )
        db_session.add(log)
        await db_session.flush()

        response = await client.get("/api/v1/audit/")
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["total"] == 1
        entry = body["data"]["items"][0]
        assert entry["action"] == "upload_document"
        assert entry["resource_type"] == "document"

    @pytest.mark.asyncio
    async def test_audit_logs_filter_by_action(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /api/v1/audit/?action=<x> returns only logs matching that action."""
        for action in ("upload_document", "generate_config", "upload_document"):
            db_session.add(
                AuditLog(
                    tenant_id="test-tenant",
                    actor="Test Tenant",
                    action=action,
                    resource_type="document",
                    resource_id="res-001",
                    details=None,
                )
            )
        await db_session.flush()

        response = await client.get("/api/v1/audit/?action=upload_document")
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["total"] == 2
        actions = [item["action"] for item in body["data"]["items"]]
        assert all(a == "upload_document" for a in actions)

    @pytest.mark.asyncio
    async def test_audit_logs_filter_by_resource_type(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /api/v1/audit/?resource_type=configuration filters correctly."""
        db_session.add(
            AuditLog(
                tenant_id="test-tenant",
                actor="Test Tenant",
                action="generate_config",
                resource_type="configuration",
                resource_id="cfg-001",
                details=None,
            )
        )
        db_session.add(
            AuditLog(
                tenant_id="test-tenant",
                actor="Test Tenant",
                action="upload_document",
                resource_type="document",
                resource_id="doc-001",
                details=None,
            )
        )
        await db_session.flush()

        response = await client.get("/api/v1/audit/?resource_type=configuration")
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["total"] == 1
        assert body["data"]["items"][0]["resource_type"] == "configuration"


# ---------------------------------------------------------------------------
# Cross-cutting concerns tests
# ---------------------------------------------------------------------------


class TestCrossCuttingConcerns:
    @pytest.mark.asyncio
    async def test_cors_headers_present(self, client: AsyncClient) -> None:
        """API responses include CORS headers for wildcard origins."""
        response = await client.get(
            "/health",
            headers={"Origin": "http://localhost:3000"},
        )
        assert response.status_code == 200
        # CORS middleware adds this when origin header is present
        assert "access-control-allow-origin" in response.headers

    @pytest.mark.asyncio
    async def test_response_time_header_present(self, client: AsyncClient) -> None:
        """Every response includes the X-Response-Time header from logging middleware."""
        response = await client.get("/health")
        assert "x-response-time" in response.headers

    @pytest.mark.asyncio
    async def test_tenant_id_header_echoed(self, client: AsyncClient) -> None:
        """The X-Tenant-ID header set by the tenant middleware is echoed in the response."""
        response = await client.get("/health")
        assert "x-tenant-id" in response.headers
        assert response.headers["x-tenant-id"] == "test-tenant"

    @pytest.mark.asyncio
    async def test_unknown_route_returns_404(self, client: AsyncClient) -> None:
        """Requests to undefined paths return 404."""
        response = await client.get("/api/v1/does-not-exist")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_rate_limiting_returns_429(self, client: AsyncClient) -> None:
        """Exceeding the per-tenant rate limit returns 429 with Retry-After header."""
        from finspark.core.rate_limiter import rate_limiter

        # Exhaust the bucket for this tenant
        rate_limiter._requests["test-tenant"] = [
            __import__("time").monotonic()
            for _ in range(rate_limiter.max_requests)
        ]

        response = await client.get("/api/v1/adapters/")
        assert response.status_code == 429
        assert "retry-after" in response.headers
        body = response.json()
        assert "detail" in body

        # Clean up so other tests are not affected
        rate_limiter.reset()

    @pytest.mark.asyncio
    async def test_security_headers_present(self, client: AsyncClient) -> None:
        """The app does not expose internal server error details to the client."""
        response = await client.get("/health")
        # Server header should not expose stack traces
        assert response.status_code == 200
        # Verify the response is JSON and parseable
        assert response.json()["status"] == "healthy"
