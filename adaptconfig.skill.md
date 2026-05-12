---
name: adaptconfig
description: |
  Drive the AdaptConfig integration platform end to end through its
  Universal API. Use this skill IMMEDIATELY when the user asks to
  "generate an integration config", "validate and test a config",
  "upload an API spec", "create an adapter from a YAML/PDF", or any
  other AdaptConfig workflow. Mirrors the SPA feature set 1:1 so an
  LLM agent can replace the UI for any operation.
when_to_use:
  - User uploads an OpenAPI/YAML/JSON spec or a BRD PDF and wants a
    fintech integration config (CIBIL, KYC, GST, UPI, Paytm, ...)
  - User asks to validate or smoke-test an existing configuration
  - User wants to deploy a config to active or roll back to a prior
    version
  - User asks for the audit trail of a tenant's recent activity
  - User wants to register a webhook so an external system can be
    notified when configs move through their lifecycle
  - User wants to compare two configs or two versions of the same
    config
allowed_tools: [bash, curl, python]
---

## Overview

AdaptConfig is a multi-tenant FastAPI + React platform that turns
API specs (or business-requirement documents) into runnable
integration configurations for Indian fintech adapters. Every
interactive feature exposed by the React SPA is also reachable
through the same HTTP API documented here, so an LLM agent (or
plain curl) can execute the full workflow:

1. Upload an API spec or BRD.
2. Generate an integration configuration against a known adapter.
3. Validate and smoke-test the configuration with a single composite
   call.
4. Deploy, roll back, or fire test webhooks.

The canonical happy path is **upload -> suggest adapter -> generate
config -> validate-and-test**, all driven from this skill. The
`POST /api/v1/configurations/{id}/validate-and-test` endpoint is new
in issue #116 and replaces the multi-step orchestration the SPA used
to perform client-side.

## Authentication

All endpoints live under `/api/v1` on the AdaptConfig backend
(default base URL `http://localhost:8000`).

- **Debug / local dev**: send the `X-Tenant-ID` header on every
  request. Optional companions: `X-Tenant-Name`, `X-Tenant-Role`
  (`admin` or `editor` is required for write endpoints).
- **Production**: obtain a JWT from `POST /api/v1/auth/login` and
  send `Authorization: Bearer <token>` on every request. Refresh
  with `POST /api/v1/auth/refresh`.

```bash
# Local dev — header-based tenant
export ADAPT_BASE_URL="${ADAPT_BASE_URL:-http://localhost:8000}"
export ADAPT_TENANT="default"
curl -sS "$ADAPT_BASE_URL/health"
```

## Workflow

The recommended workflow for any net-new integration:

1. **Upload the source document**  
   `POST /api/v1/documents/upload?doc_type=api_spec` (multipart file)
   - Accepts `.yaml`, `.yml`, `.json`, `.pdf`, `.docx`, `.txt`.
   - For business requirement docs, send `doc_type=brd`.
   - The backend parses the document asynchronously; poll
     `GET /api/v1/documents/{id}` until `status == "parsed"`.

2. **Pick (or create) an adapter version**  
   `GET /api/v1/adapters/?category=kyc` to list catalog adapters,
   or `POST /api/v1/adapters/from-document` to mint a new adapter
   from the just-uploaded document. Capture the `versions[0].id`
   from the response. The catalog ships with nine seeded adapters
   (CIBIL, eKYC, GST, Payment, Fraud, SMS, Account Aggregator,
   Email, ...) — picking a seeded adapter is preferable when the
   document's base URL cannot be reliably extracted, since
   adapters minted from a document inherit only the
   `parsed_result.sections.base_urls` field.

3. **Generate a configuration**  
   `POST /api/v1/configurations/generate` with body
   `{"document_id": "...", "adapter_version_id": "...", "name": "..."}`.
   The backend runs the LLM-augmented field mapper and persists a
   Configuration in `configured` state.

4. **Validate AND smoke-test in one call** (composite endpoint)  
   `POST /api/v1/configurations/{config_id}/validate-and-test`. This
   single call performs:
   - `transition configured -> validating`
   - `validate` (same logic as `POST /validate`)
   - `transition validating -> testing`
   - `simulate test_type=smoke`

   Returns a JSON document containing the per-step results, the
   overall status (`passed` | `failed`), and the simulation id.

5. **Deploy** (when satisfied):
   `POST /api/v1/configurations/{config_id}/transition` with body
   `{"target_state": "active"}`.

## API reference

All examples assume `$ADAPT_BASE_URL` and `$ADAPT_TENANT` are
exported as shown in the Authentication section.

### Documents

```bash
# Upload an OpenAPI spec
curl -sS -X POST "$ADAPT_BASE_URL/api/v1/documents/upload?doc_type=api_spec" \
  -H "X-Tenant-ID: $ADAPT_TENANT" \
  -F "file=@./test_fixtures/05_perfect_kyc_api.yaml"

# Inspect a parsed document
curl -sS "$ADAPT_BASE_URL/api/v1/documents/<doc_id>" \
  -H "X-Tenant-ID: $ADAPT_TENANT"
```

### Adapters

```bash
# List adapters (optionally filter by category)
curl -sS "$ADAPT_BASE_URL/api/v1/adapters/?category=kyc" \
  -H "X-Tenant-ID: $ADAPT_TENANT"

# Mint a new adapter from a parsed document
curl -sS -X POST "$ADAPT_BASE_URL/api/v1/adapters/from-document?document_id=<doc_id>&name=My%20KYC&category=kyc" \
  -H "X-Tenant-ID: $ADAPT_TENANT"
```

### Configurations

```bash
# Generate a new config
curl -sS -X POST "$ADAPT_BASE_URL/api/v1/configurations/generate" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: $ADAPT_TENANT" -H "X-Tenant-Role: admin" \
  -d '{"document_id":"<doc_id>","adapter_version_id":"<av_id>","name":"My KYC Config"}'

# Composite: validate + smoke-test in one call (issue #116)
curl -sS -X POST "$ADAPT_BASE_URL/api/v1/configurations/<config_id>/validate-and-test" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: $ADAPT_TENANT" -H "X-Tenant-Role: admin" \
  -d '{"test_type":"smoke","reason":"first run"}'

# Deploy the config to production
curl -sS -X POST "$ADAPT_BASE_URL/api/v1/configurations/<config_id>/transition" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: $ADAPT_TENANT" -H "X-Tenant-Role: admin" \
  -d '{"target_state":"active","reason":"smoke 7/7 green"}'

# History and rollback
curl -sS "$ADAPT_BASE_URL/api/v1/configurations/<config_id>/history" \
  -H "X-Tenant-ID: $ADAPT_TENANT"

curl -sS -X POST "$ADAPT_BASE_URL/api/v1/configurations/<config_id>/rollback" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: $ADAPT_TENANT" -H "X-Tenant-Role: admin" \
  -d '{"target_version":1}'

# Batch validation (toolbar feature)
curl -sS -X POST "$ADAPT_BASE_URL/api/v1/configurations/batch-validate" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: $ADAPT_TENANT" \
  -d '{"config_ids":["<id1>","<id2>"]}'
```

### Simulations

```bash
# Ad-hoc simulation against a config (alternative to the composite endpoint)
curl -sS -X POST "$ADAPT_BASE_URL/api/v1/simulations/run" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: $ADAPT_TENANT" -H "X-Tenant-Role: admin" \
  -d '{"configuration_id":"<config_id>","test_type":"smoke"}'

# Retrieve the simulation
curl -sS "$ADAPT_BASE_URL/api/v1/simulations/<sim_id>" \
  -H "X-Tenant-ID: $ADAPT_TENANT"
```

### Webhooks

```bash
# Register a webhook receiver
curl -sS -X POST "$ADAPT_BASE_URL/api/v1/webhooks/" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: $ADAPT_TENANT" -H "X-Tenant-Role: admin" \
  -d '{"url":"https://hooks.example.com/in","events":["simulation.completed"],"secret":"shh"}'

# Fire a test event without waiting for a real one
curl -sS -X POST "$ADAPT_BASE_URL/api/v1/webhooks/<webhook_id>/test" \
  -H "X-Tenant-ID: $ADAPT_TENANT" -H "X-Tenant-Role: admin"
```

### Search and audit

```bash
# Global search across documents, configs, adapters
curl -sS "$ADAPT_BASE_URL/api/v1/search/?q=aadhaar" \
  -H "X-Tenant-ID: $ADAPT_TENANT"

# Audit log (filterable by action, resource_type, resource_id)
curl -sS "$ADAPT_BASE_URL/api/v1/audit/?action=run_simulation" \
  -H "X-Tenant-ID: $ADAPT_TENANT"
```

### Security inspection and lint

```bash
# Lint an uploaded OpenAPI spec
curl -sS -X POST "$ADAPT_BASE_URL/api/v1/lint/" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: $ADAPT_TENANT" \
  -d '{"spec_text":"<yaml>","format":"yaml"}'

# Inspect a generated config against OWASP API Top 10
curl -sS -X POST "$ADAPT_BASE_URL/api/v1/security/inspect-config/<config_id>" \
  -H "X-Tenant-ID: $ADAPT_TENANT" -H "X-Tenant-Role: admin"
```

## End-to-end example

A complete validate-and-test run against the gold-standard 3-endpoint
fixture. Produces 7/7 passing tests via the composite endpoint.

```bash
#!/usr/bin/env bash
set -euo pipefail

export ADAPT_BASE_URL="${ADAPT_BASE_URL:-http://localhost:8000}"
export ADAPT_TENANT="${ADAPT_TENANT:-default}"
H=(-H "X-Tenant-ID: $ADAPT_TENANT" -H "X-Tenant-Name: Smoke" -H "X-Tenant-Role: admin")

# 1. Upload the fixture
DOC_ID=$(curl -sS -X POST "$ADAPT_BASE_URL/api/v1/documents/upload?doc_type=api_spec" \
  "${H[@]}" \
  -F "file=@test_fixtures/05_perfect_kyc_api.yaml" | jq -r '.data.id')

# 2. Pick a seeded 3-endpoint adapter version (CIBIL Credit Bureau v1).
# Alternative: POST /api/v1/adapters/from-document — but base_url
# extraction from OpenAPI specs is best-effort and may be empty, in
# which case the composite endpoint correctly fails validation.
AV_ID=$(curl -sS "$ADAPT_BASE_URL/api/v1/adapters/" "${H[@]}" \
  | jq -r '.data.adapters[] | select(.name=="CIBIL Credit Bureau") | .versions[] | select(.version=="v1") | .id')

# 3. Generate a configuration
CFG_ID=$(curl -sS -X POST "$ADAPT_BASE_URL/api/v1/configurations/generate" \
  "${H[@]}" -H "Content-Type: application/json" \
  -d "{\"document_id\":\"$DOC_ID\",\"adapter_version_id\":\"$AV_ID\",\"name\":\"Skill Smoke Config\"}" \
  | jq -r '.data.id')

# 4. Validate + smoke-test in a single composite call
curl -sS -X POST "$ADAPT_BASE_URL/api/v1/configurations/$CFG_ID/validate-and-test" \
  "${H[@]}" -H "Content-Type: application/json" \
  -d '{"test_type":"smoke","reason":"skill end-to-end"}' \
  | jq '{overall_status, total_tests, passed_tests, failed_tests, final_state, steps:[.data.steps[]?.name // .steps[]?.name]}'
```

The expected `data` block:

```json
{
  "configuration_id": "<uuid>",
  "final_state": "testing",
  "overall_status": "passed",
  "total_tests": 7,
  "passed_tests": 7,
  "failed_tests": 0,
  "simulation_id": "<uuid>",
  "steps": [
    {"name": "transition_to_validating", "status": "passed"},
    {"name": "validate",                  "status": "passed"},
    {"name": "transition_to_testing",     "status": "passed"},
    {"name": "smoke_simulation",          "status": "passed"}
  ]
}
```

The `scripts/skill_smoke.py` driver shipped in this repo runs the
above sequence against a live backend and asserts 7/7. CI exercises
the same logic in-process via `tests/integration/test_skill_smoke.py`.

**Note on simulator step counts.** A "smoke" simulation produces
`2 + N + 2` test steps for a configuration with N enabled endpoints
(config-structure, field-mappings, N endpoint tests, auth, hooks).
When the LLM-augmented config generator emits chained endpoints
(non-null `depends_on`/`extract`/`inject` fields), the simulator
collapses the per-endpoint tests into a single `chain_execution`
step. To produce a deterministic 7/7 result for the gold-standard
3-endpoint fixture, run the smoke driver against a backend started
with `FINSPARK_AI_ENABLED=false` (or simply omit the LLM API key).
