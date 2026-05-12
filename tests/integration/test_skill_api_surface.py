"""Skill / Universal API surface tests.

Two responsibilities:

1. **Composite endpoint**.  Exercise `POST /api/v1/configurations/{id}/validate-and-test`
   end-to-end against an in-memory backend, asserting that the transition ->
   validate -> transition -> smoke pipeline produces the same outcome the
   React UI used to orchestrate piece by piece.

2. **Page-to-route mapping**.  For every interactive feature exposed by the
   frontend pages (Dashboard, Documents, Adapters, Configurations, Simulations,
   Webhooks, Search, Audit), assert that the FastAPI route registry contains
   a matching method+path entry.  This protects us against silently dropping
   a route that the SPA — or the Claude Skill, which mirrors the SPA's
   feature set — depends on.

Pre-existing tests (`test_full_workflow`, `test_api_endpoints`) cover the
individual route happy-paths; this file is specifically about the *skill /
universal API* contract introduced for issue #116.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.main import app
from finspark.models.adapter import Adapter, AdapterVersion

FIXTURES = Path(__file__).parent.parent.parent / "test_fixtures"


@pytest.fixture(autouse=True)
def _silence_webhook_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid the cross-test webhook background-task race.

    The default DB teardown drops the `webhooks` table between tests; the
    BackgroundTasks queue for webhook delivery races that teardown and
    surfaces as a noisy `OperationalError: no such table: webhooks` on the
    next request.  These tests do not exercise webhook fan-out, so we
    replace the deliverer with a no-op for the duration of the test.
    """

    async def _noop(*_args, **_kwargs):  # noqa: ANN001
        return None

    monkeypatch.setattr(
        "finspark.api.routes.configurations.deliver_event", _noop
    )
    monkeypatch.setattr(
        "finspark.api.routes.documents.deliver_event", _noop, raising=False
    )
    monkeypatch.setattr(
        "finspark.api.routes.simulations.deliver_event", _noop, raising=False
    )


@pytest.fixture(autouse=True)
def _force_rule_based_config_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip LLM config generation and use the deterministic rule-based mapper.

    The composite endpoint must produce a 7/7 smoke result for the gold-standard
    fixture.  Rule-based generation produces flat (non-chained) endpoint configs
    that map 1-to-1 onto the simulator's per-endpoint test step.  LLM-generated
    configs sometimes introduce chaining (`depends_on`/`extract`/`inject`),
    which the simulator collapses into a single `chain_execution` step and
    whose pass/fail oracle depends on the LLM honouring the JSONPath spec —
    a flaky dependency for CI.  These tests assert the *pipeline mechanics*,
    not the LLM's behaviour, so we monkey-patch the LLM call to raise and
    let the route's rule-based fallback handle generation.
    """

    async def _force_fallback(*_args, **_kwargs):  # noqa: ANN001
        raise RuntimeError("LLM disabled in skill-surface tests")

    monkeypatch.setattr(
        "finspark.api.routes.configurations.generate_config_llm",
        _force_fallback,
        raising=False,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_three_endpoint_adapter(db_session: AsyncSession) -> str:
    """Seed an adapter with three endpoints so a smoke simulation produces 7 steps.

    Smoke step count = 1 (config_structure) + 1 (field_mappings) + N endpoints
    + 1 (auth) + 1 (hooks).  For 7 we need N = 3.

    The adapter intentionally uses a generic name and base_url so the
    simulator's mock dispatcher falls back to the deterministic
    `_default_response` (which returns ``status: success`` for every
    endpoint).  Adapter-specific mock packs (CIBIL, KYC, GST, ...) have
    bespoke response shapes that don't include the literal ``status`` field
    the simulator's pass/fail oracle checks for.
    """
    adapter = Adapter(
        name="AdaptConfig Generic Verification",
        category="custom",
        description="Three-endpoint generic adapter for skill smoke tests.",
        is_active=True,
        icon="shield-check",
    )
    db_session.add(adapter)
    await db_session.flush()

    version = AdapterVersion(
        adapter_id=adapter.id,
        version="v1",
        base_url="https://api.adaptconfig.example.com/v1",
        auth_type="api_key",
        endpoints=json.dumps(
            [
                {
                    "path": "/verify/aadhaar",
                    "method": "POST",
                    "description": "Verify Aadhaar number",
                },
                {
                    "path": "/verify/pan",
                    "method": "POST",
                    "description": "Verify PAN number",
                },
                {
                    "path": "/verify/status/{reference_id}",
                    "method": "GET",
                    "description": "Check verification status",
                },
            ]
        ),
        request_schema=json.dumps(
            {
                "type": "object",
                "required": ["aadhaar_number", "customer_name", "consent_id"],
                "properties": {
                    "aadhaar_number": {"type": "string"},
                    "pan_number": {"type": "string"},
                    "customer_name": {"type": "string"},
                    "date_of_birth": {"type": "string", "format": "date"},
                    "mobile_number": {"type": "string"},
                    "consent_id": {"type": "string"},
                },
            }
        ),
        response_schema=json.dumps(
            {
                "type": "object",
                "properties": {
                    "verified": {"type": "boolean"},
                    "reference_id": {"type": "string"},
                },
            }
        ),
    )
    db_session.add(version)
    await db_session.flush()
    return version.id


async def _upload_perfect_fixture(client: AsyncClient) -> str:
    """Upload the gold-standard 3-endpoint YAML fixture and return doc id."""
    fixture = FIXTURES / "05_perfect_kyc_api.yaml"
    assert fixture.exists(), f"missing fixture: {fixture}"
    with fixture.open("rb") as f:
        resp = await client.post(
            "/api/v1/documents/upload",
            files={"file": ("05_perfect_kyc_api.yaml", f, "application/x-yaml")},
            params={"doc_type": "api_spec"},
        )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


# ---------------------------------------------------------------------------
# Composite endpoint
# ---------------------------------------------------------------------------


class TestValidateAndTestComposite:
    """Behaviour of POST /api/v1/configurations/{id}/validate-and-test."""

    @pytest.mark.asyncio
    async def test_pipeline_runs_for_perfect_fixture(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Gold-standard 3-endpoint fixture: pipeline reports 7/7 smoke."""
        av_id = await _seed_three_endpoint_adapter(db_session)
        doc_id = await _upload_perfect_fixture(client)

        gen = await client.post(
            "/api/v1/configurations/generate",
            json={
                "document_id": doc_id,
                "adapter_version_id": av_id,
                "name": "Perfect KYC Config",
            },
        )
        assert gen.status_code == 200, gen.text
        config_id = gen.json()["data"]["id"]

        resp = await client.post(
            f"/api/v1/configurations/{config_id}/validate-and-test",
            json={"test_type": "smoke", "reason": "skill-smoke-test"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]

        assert data["overall_status"] == "passed", data
        assert data["total_tests"] == 7, data
        assert data["passed_tests"] == 7, data
        assert data["failed_tests"] == 0
        assert data["final_state"] == "testing"
        assert data["simulation_id"]

        step_names = [step["name"] for step in data["steps"]]
        assert "transition_to_validating" in step_names
        assert "validate" in step_names
        assert "transition_to_testing" in step_names
        assert "smoke_simulation" in step_names

    @pytest.mark.asyncio
    async def test_pipeline_idempotent_when_already_testing(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Replaying the pipeline on a config already in 'testing' should still pass."""
        av_id = await _seed_three_endpoint_adapter(db_session)
        doc_id = await _upload_perfect_fixture(client)

        gen = await client.post(
            "/api/v1/configurations/generate",
            json={
                "document_id": doc_id,
                "adapter_version_id": av_id,
                "name": "Idempotent KYC",
            },
        )
        config_id = gen.json()["data"]["id"]

        first = await client.post(
            f"/api/v1/configurations/{config_id}/validate-and-test",
            json={"test_type": "smoke"},
        )
        assert first.status_code == 200
        assert first.json()["data"]["final_state"] == "testing"

        second = await client.post(
            f"/api/v1/configurations/{config_id}/validate-and-test",
            json={"test_type": "smoke"},
        )
        assert second.status_code == 200
        d = second.json()["data"]
        # Transitions become skips, smoke still runs and still passes
        statuses = {step["name"]: step["status"] for step in d["steps"]}
        assert statuses.get("transition_to_validating") == "skipped"
        assert statuses.get("transition_to_testing") == "skipped"
        assert d["overall_status"] == "passed"

    @pytest.mark.asyncio
    async def test_missing_config_returns_404(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/configurations/does-not-exist/validate-and-test",
            json={"test_type": "smoke"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_empty_body_is_accepted(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Body is optional — defaults to smoke / no reason."""
        av_id = await _seed_three_endpoint_adapter(db_session)
        doc_id = await _upload_perfect_fixture(client)
        gen = await client.post(
            "/api/v1/configurations/generate",
            json={
                "document_id": doc_id,
                "adapter_version_id": av_id,
                "name": "Defaults KYC",
            },
        )
        config_id = gen.json()["data"]["id"]
        resp = await client.post(
            f"/api/v1/configurations/{config_id}/validate-and-test"
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["overall_status"] == "passed"


# ---------------------------------------------------------------------------
# Page-to-route mapping
# ---------------------------------------------------------------------------


def _registered_routes() -> set[tuple[str, str]]:
    """Build {(method, path)} for every concrete route mounted on the app."""
    items: set[tuple[str, str]] = set()
    for r in app.routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None) or set()
        if not path:
            continue
        for m in methods:
            if m == "HEAD":
                continue
            items.add((m.upper(), path))
    return items


# Each interactive UI feature must have a backing route.  Keep this list in
# lock-step with `docs/API_AUDIT.md` — both are derived from the same scan of
# `frontend/src/pages/*.tsx`.
REQUIRED_ROUTES: list[tuple[str, str, str]] = [
    # Auth
    ("POST", "/api/v1/auth/login", "Login page – submit credentials"),
    ("POST", "/api/v1/auth/register", "Register page – create account"),
    ("POST", "/api/v1/auth/refresh", "API client refresh interceptor"),
    ("GET", "/api/v1/auth/me", "Auth bootstrap on app load"),
    # Dashboard
    ("GET", "/api/v1/analytics/dashboard", "Dashboard analytics widget"),
    ("GET", "/api/v1/configurations/summary", "Dashboard configurations summary"),
    # Documents
    ("GET", "/api/v1/documents/", "Documents page – list"),
    ("GET", "/api/v1/documents/{document_id}", "Documents page – detail"),
    ("POST", "/api/v1/documents/upload", "Documents page – upload"),
    ("DELETE", "/api/v1/documents/{document_id}", "Documents page – delete"),
    # Adapters
    ("GET", "/api/v1/adapters/", "Adapters page – list"),
    ("GET", "/api/v1/adapters/{adapter_id}", "Adapters page – detail"),
    (
        "GET",
        "/api/v1/adapters/{adapter_id}/versions/{version}/deprecation",
        "Adapters page – deprecation banner",
    ),
    ("POST", "/api/v1/adapters/from-document", "Configurations page – create adapter inline"),
    # Configurations
    ("GET", "/api/v1/configurations/", "Configurations page – list"),
    ("GET", "/api/v1/configurations/{config_id}", "Configurations page – detail"),
    ("POST", "/api/v1/configurations/generate", "Configurations page – generate config"),
    ("POST", "/api/v1/configurations/{config_id}/validate", "Configurations page – validate"),
    (
        "POST",
        "/api/v1/configurations/{config_id}/transition",
        "Configurations page – lifecycle buttons",
    ),
    (
        "POST",
        "/api/v1/configurations/{config_id}/rollback",
        "Configurations page – rollback",
    ),
    (
        "PATCH",
        "/api/v1/configurations/{config_id}",
        "Configurations page – save field mapping edits",
    ),
    (
        "DELETE",
        "/api/v1/configurations/{config_id}",
        "Configurations page – delete config",
    ),
    (
        "GET",
        "/api/v1/configurations/{config_id}/history",
        "Configurations page – history panel",
    ),
    (
        "GET",
        "/api/v1/configurations/{config_id}/export",
        "Configurations page – export JSON/YAML",
    ),
    (
        "GET",
        "/api/v1/configurations/{config_a_id}/diff/{config_b_id}",
        "Configurations page – compare two configs",
    ),
    ("GET", "/api/v1/configurations/templates", "Configurations page – template suggestions"),
    (
        "POST",
        "/api/v1/configurations/batch-validate",
        "Configurations page – batch validate",
    ),
    (
        "POST",
        "/api/v1/configurations/batch-simulate",
        "Configurations page – batch simulate",
    ),
    (
        "POST",
        "/api/v1/configurations/{config_id}/validate-and-test",
        "Configurations page – composite pipeline (issue #116)",
    ),
    # Simulations
    ("GET", "/api/v1/simulations/", "Simulations page – list"),
    ("POST", "/api/v1/simulations/run", "Simulations page – run a simulation"),
    ("GET", "/api/v1/simulations/{simulation_id}", "Simulations page – detail"),
    ("DELETE", "/api/v1/simulations/{simulation_id}", "Simulations page – delete"),
    # Webhooks
    ("GET", "/api/v1/webhooks/", "Webhooks page – list"),
    ("POST", "/api/v1/webhooks/", "Webhooks page – create"),
    ("DELETE", "/api/v1/webhooks/{webhook_id}", "Webhooks page – delete"),
    ("POST", "/api/v1/webhooks/{webhook_id}/test", "Webhooks page – fire test event"),
    # Search
    ("GET", "/api/v1/search/", "Search page – global search"),
    # Audit
    ("GET", "/api/v1/audit/", "Audit page – list with filters"),
    # Security inspector (used by Configurations security button)
    ("POST", "/api/v1/security/inspect-spec", "Documents page – security inspect"),
    (
        "POST",
        "/api/v1/security/inspect-config/{config_id}",
        "Configurations page – Security button",
    ),
    # Lint (used by uploaded specs panel)
    ("POST", "/api/v1/lint/", "Documents page – ad-hoc lint"),
    # Health
    ("GET", "/health", "Header health indicator"),
]


def test_every_ui_feature_has_a_route() -> None:
    """The FastAPI route registry must back every UI feature in the SPA."""
    actual = _registered_routes()
    missing: list[tuple[str, str, str]] = []
    for method, path, label in REQUIRED_ROUTES:
        if (method, path) not in actual:
            missing.append((method, path, label))
    assert not missing, "Missing backend routes for UI features:\n" + "\n".join(
        f"  {m} {p}  ({label})" for m, p, label in missing
    )


def test_api_audit_document_lists_validate_and_test() -> None:
    """`docs/API_AUDIT.md` must mention the composite endpoint by its full path."""
    doc = Path(__file__).parent.parent.parent / "docs" / "API_AUDIT.md"
    assert doc.exists(), "docs/API_AUDIT.md missing — required by issue #116"
    text = doc.read_text()
    assert "POST /api/v1/configurations/{id}/validate-and-test" in text or (
        "POST /api/v1/configurations/{config_id}/validate-and-test" in text
    ), "API_AUDIT.md must document the composite endpoint"


def test_skill_file_documents_universal_workflow() -> None:
    """`adaptconfig.skill.md` at repo root must follow the Anthropic skill schema."""
    skill = Path(__file__).parent.parent.parent / "adaptconfig.skill.md"
    assert skill.exists(), "adaptconfig.skill.md missing — required by issue #116"
    text = skill.read_text()
    # Anthropic Skill frontmatter
    assert text.startswith("---"), "Skill must begin with YAML frontmatter"
    assert "name:" in text
    assert "description:" in text
    # Required sections
    for section in (
        "## Overview",
        "## Authentication",
        "## Workflow",
        "## API reference",
        "## End-to-end example",
    ):
        assert section in text, f"Skill missing section: {section}"
    # The composite endpoint must be documented in the API reference
    assert "validate-and-test" in text
