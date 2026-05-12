"""One-off verification script for the gold-standard 7/7 simulation path.

Uploads ``test_fixtures/05_perfect_kyc_api.yaml`` through the FastAPI app
using the real OpenAI provider (``FINSPARK_LLM_PROVIDER=openai``,
``FINSPARK_LLM_MODEL=gpt-4.1-nano`` per .env), generates a configuration
against the seeded Aadhaar eKYC adapter, runs the simulation, and asserts
``status: passed`` with 7/7 passed steps.

This is NOT part of ``pytest tests/`` — it talks to the live OpenAI API
and is intended as a one-shot pre-merge smoke test.

Run with::

    uv run python scripts/verify_perfect_kyc_simulation.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

# Test conftest sets this so the in-memory sqlite path works
os.environ.setdefault("FINSPARK_DEBUG", "true")

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "test_fixtures" / "05_perfect_kyc_api.yaml"
CATALOGUE = ROOT / "src" / "finspark" / "seeds" / "adapters.json"

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import finspark.models.adapter  # noqa: E402, F401
import finspark.models.api_call_log  # noqa: E402, F401
import finspark.models.audit  # noqa: E402, F401
import finspark.models.configuration  # noqa: E402, F401
import finspark.models.contract_test  # noqa: E402, F401
import finspark.models.document  # noqa: E402, F401
import finspark.models.external_api_audit  # noqa: E402, F401
import finspark.models.simulation  # noqa: E402, F401
import finspark.models.tenant  # noqa: E402, F401
import finspark.models.webhook  # noqa: E402, F401
import finspark.models.workflow  # noqa: E402, F401
from finspark.core.database import get_db  # noqa: E402
from finspark.main import app  # noqa: E402
from finspark.models.adapter import Adapter, AdapterVersion  # noqa: E402
from finspark.models.base import Base  # noqa: E402


async def _seed_catalogue(session_factory) -> dict[str, dict[str, str]]:
    seeded: dict[str, dict[str, str]] = {}
    with CATALOGUE.open() as f:
        catalogue = json.load(f)

    async with session_factory() as db:
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
            assert first_version is not None
            seeded[entry["name"]] = {
                "adapter_id": adapter.id,
                "version_id": first_version.id,
            }
        await db.commit()
    return seeded


async def _wait_until_parsed(client: AsyncClient, doc_id: str, *, deadline_s: float = 120) -> dict:
    loop = asyncio.get_event_loop()
    end = loop.time() + deadline_s
    delay = 1.0
    while loop.time() < end:
        detail = await client.get(f"/api/v1/documents/{doc_id}")
        if detail.status_code != 200:
            await asyncio.sleep(delay)
            continue
        data = detail.json().get("data", {})
        if data.get("status") == "parsed":
            return data
        if data.get("status") == "failed":
            raise RuntimeError(f"Document parsing failed: {data}")
        await asyncio.sleep(delay)
        delay = min(delay * 1.4, 5.0)
    raise TimeoutError(f"Document {doc_id} did not reach parsed status in {deadline_s}s")


async def main() -> int:
    if not FIXTURE.exists():
        print(f"[fail] Missing fixture: {FIXTURE}", file=sys.stderr)
        return 1

    # Use an in-memory db so we don't pollute the dev sqlite file. The conftest
    # uses the same trick and the existing flows are designed for it.
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Pin app's get_db to a shared session so route + background tasks see the
    # same in-memory database.
    shared_session: AsyncSession | None = None

    async def _override_get_db():
        nonlocal shared_session
        if shared_session is None:
            shared_session = session_factory()
        try:
            yield shared_session
        finally:
            # Don't close — keep the same connection across requests so the
            # in-memory db survives.
            pass

    app.dependency_overrides[get_db] = _override_get_db

    # The upload route's background parse task imports async_session_factory
    # at call-time via ``from finspark.core.database import async_session_factory``,
    # so patching the module attribute is sufficient. The simulations route
    # imports it at module load time so we also patch that copy.
    patches = [
        patch("finspark.core.database.async_session_factory", session_factory),
        patch("finspark.api.routes.simulations.async_session_factory", session_factory),
    ]

    @contextlib.contextmanager
    def _apply_all_patches():
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            yield

    seeded = await _seed_catalogue(session_factory)
    aadhaar = seeded["Aadhaar eKYC Provider"]

    transport = ASGITransport(app=app)
    with _apply_all_patches():
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            client.headers["X-Tenant-ID"] = "test-tenant"
            client.headers["X-Tenant-Name"] = "Test Tenant"
            client.headers["X-Tenant-Role"] = "admin"

            # Adapter catalogue is now seeded; verify via the API.
            adapters_resp = await client.get("/api/v1/adapters/")
            adapters_resp.raise_for_status()
            adapters = adapters_resp.json()["data"]["adapters"]
            print(f"[ok] adapter catalogue listed: {len(adapters)} adapters")
            assert any(a["name"] == "Aadhaar eKYC Provider" for a in adapters), adapters

            # Upload the fixture
            with FIXTURE.open("rb") as fh:
                up = await client.post(
                    "/api/v1/documents/upload",
                    files={"file": (FIXTURE.name, fh, "application/x-yaml")},
                    params={"doc_type": "api_spec"},
                )
            up.raise_for_status()
            doc_id = up.json()["data"]["id"]
            print(f"[ok] uploaded fixture: doc_id={doc_id}")

            # Wait for parsing (LLM runs as background task)
            await _wait_until_parsed(client, doc_id)
            print("[ok] document parsed via OpenAI")

            # Generate configuration
            gen = await client.post(
                "/api/v1/configurations/generate",
                json={
                    "document_id": doc_id,
                    "adapter_version_id": aadhaar["version_id"],
                    "name": "Aadhaar eKYC Gold Integration",
                    "auto_map": True,
                },
            )
            gen.raise_for_status()
            cfg_id = gen.json()["data"]["id"]
            print(f"[ok] configuration generated: cfg_id={cfg_id}")

            # Run simulation
            sim = await client.post(
                "/api/v1/simulations/run",
                json={"configuration_id": cfg_id, "test_type": "full"},
            )
            sim.raise_for_status()
            sim_data = sim.json()["data"]
            total = sim_data.get("total_tests", 0)
            passed = sim_data.get("passed_tests", 0)
            status = sim_data.get("status", "")
            print(f"[result] status={status} total={total} passed={passed}")
            for step in sim_data.get("steps", []) or []:
                print(
                    f"   - {step.get('step_name'):40s} {step.get('status'):10s}"
                    f" conf={step.get('confidence_score')}"
                    f" err={step.get('error_message') or ''}"
                )
            if shared_session is not None:
                await shared_session.close()
            return 0 if status == "passed" and total == 7 and passed == 7 else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
