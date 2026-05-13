#!/usr/bin/env python3
"""Drive the same upload → suggest → generate → validate flow that
``adaptconfig.skill.md`` documents, then assert 7/7 dimensions pass on the
gold-standard fixture.

Hits the live HTTP API (default ``http://localhost:8000``). Run against a
fresh DB to reproduce the published acceptance criterion for Issue #116.

Usage:
    uv run python scripts/skill_smoke.py
    uv run python scripts/skill_smoke.py --base-url https://adaptconfig-api-production.up.railway.app
    uv run python scripts/skill_smoke.py --fixture test_fixtures/05_perfect_kyc_api.yaml

Exit codes:
    0  -- 7/7 dimensions passed.
    1  -- pipeline ran but fewer than 7 dimensions passed.
    2  -- prerequisite failure (upload/generate did not complete).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURE = REPO_ROOT / "test_fixtures" / "05_perfect_kyc_api.yaml"
DEFAULT_BASE_URL = os.environ.get("ADAPTCONFIG_BASE_URL", "http://localhost:8000")

# Validator dimensions returned by IntegrationSimulator.validate_config_llm.
# Kept in sync with services/simulation/simulator.py.
EXPECTED_DIMENSIONS = (
    "config_structure_validation",
    "field_mapping_quality",
    "auth_configuration_adequacy",
    "error_handling_robustness",
    "retry_logic_appropriateness",
    "endpoint_configuration_validity",
    "security_best_practices",
)


def _headers(tenant: str, role: str, name: str) -> dict[str, str]:
    return {
        "X-Tenant-ID": tenant,
        "X-Tenant-Role": role,
        "X-Tenant-Name": name,
    }


def _envelope(resp: httpx.Response, context: str) -> dict[str, Any]:
    try:
        body = resp.json()
    except ValueError as exc:
        raise SystemExit(f"[{context}] non-JSON response (status={resp.status_code}): {resp.text[:200]}") from exc
    if resp.status_code >= 400:
        raise SystemExit(
            f"[{context}] HTTP {resp.status_code}: {body.get('detail') or body.get('message') or body}"
        )
    return body


def upload_document(client: httpx.Client, fixture_path: Path) -> str:
    """Upload an OpenAPI fixture and return the parsed document id."""
    if not fixture_path.exists():
        raise SystemExit(f"Fixture not found: {fixture_path}")

    with fixture_path.open("rb") as fp:
        files = {"file": (fixture_path.name, fp, "application/x-yaml")}
        resp = client.post("/api/v1/documents/upload?doc_type=api_spec", files=files)
    body = _envelope(resp, "upload_document")
    doc = body.get("data") or {}
    doc_id = doc.get("id")
    if not doc_id:
        raise SystemExit(f"upload_document: missing data.id in response: {body}")
    if doc.get("status") != "parsed":
        raise SystemExit(
            f"upload_document: document did not reach parsed status (got {doc.get('status')!r})"
        )
    return doc_id


def pick_adapter_version(client: httpx.Client, category: str = "kyc") -> str:
    """List adapters for *category* and return the first version id."""
    resp = client.get("/api/v1/adapters/", params={"category": category})
    body = _envelope(resp, "list_adapters")
    adapters = (body.get("data") or {}).get("adapters") or []
    for adapter in adapters:
        for version in adapter.get("versions") or []:
            if version.get("id"):
                return version["id"]
    raise SystemExit(f"pick_adapter_version: no adapter version found for category={category!r}")


def generate_configuration(client: httpx.Client, document_id: str, av_id: str, name: str) -> str:
    payload = {
        "document_id": document_id,
        "adapter_version_id": av_id,
        "name": name,
        "auto_map": True,
    }
    resp = client.post("/api/v1/configurations/generate", json=payload)
    body = _envelope(resp, "generate_configuration")
    config = body.get("data") or {}
    config_id = config.get("id")
    if not config_id:
        raise SystemExit(f"generate_configuration: missing data.id in response: {body}")
    return config_id


def run_integration_simulation(client: httpx.Client, config_id: str) -> dict[str, Any]:
    """Trigger /simulations/run with test_type=integration (the 7-dim LLM validator)."""
    resp = client.post(
        "/api/v1/simulations/run",
        json={"configuration_id": config_id, "test_type": "integration"},
    )
    body = _envelope(resp, "run_simulation")
    return body.get("data") or {}


def assert_seven_of_seven(simulation: dict[str, Any]) -> tuple[int, int]:
    """Return (passed, total). Raises SystemExit if the validator dimensions
    don't match the documented gold-standard set or fewer than 7 passed."""
    steps = simulation.get("steps") or []
    seen = {s.get("step_name"): s.get("status") for s in steps}
    missing = [d for d in EXPECTED_DIMENSIONS if d not in seen]
    if missing:
        raise SystemExit(
            "Simulation did not return the 7 validator dimensions. "
            f"Missing: {missing}. Got: {sorted(seen.keys())}"
        )
    passed = sum(1 for d in EXPECTED_DIMENSIONS if seen.get(d) == "passed")
    total = len(EXPECTED_DIMENSIONS)
    return passed, total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--tenant", default="default")
    parser.add_argument("--role", default="admin")
    parser.add_argument("--name", default="skill-smoke-agent")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--config-name",
        default="Gold-Standard eKYC (skill smoke)",
        help="Name to give the generated configuration row.",
    )
    args = parser.parse_args()

    fixture = Path(args.fixture).resolve()

    print(f"--> base_url={args.base_url} fixture={fixture.name}", flush=True)
    started = time.monotonic()
    with httpx.Client(
        base_url=args.base_url.rstrip("/"),
        timeout=args.timeout,
        headers=_headers(args.tenant, args.role, args.name),
    ) as client:
        print("[1/4] Uploading document...", flush=True)
        doc_id = upload_document(client, fixture)
        print(f"        document_id={doc_id}", flush=True)

        print("[2/4] Picking adapter version (category=kyc)...", flush=True)
        av_id = pick_adapter_version(client)
        print(f"        adapter_version_id={av_id}", flush=True)

        print("[3/4] Generating configuration...", flush=True)
        config_id = generate_configuration(client, doc_id, av_id, args.config_name)
        print(f"        config_id={config_id}", flush=True)

        print("[4/4] Running 7-dimension integration validator...", flush=True)
        sim = run_integration_simulation(client, config_id)

    passed, total = assert_seven_of_seven(sim)
    elapsed = time.monotonic() - started
    status = sim.get("status")
    print(f"--> sim_status={status} passed={passed}/{total} duration_total_s={elapsed:.1f}", flush=True)

    if passed == total:
        print("OK: gold-standard fixture path scored 7/7 dimensions.", flush=True)
        return 0

    failing = [
        s.get("step_name")
        for s in sim.get("steps") or []
        if s.get("status") != "passed"
    ]
    print(f"FAIL: only {passed}/{total} dimensions passed. Failing dimensions: {failing}", flush=True)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001 -- CLI top-level
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
