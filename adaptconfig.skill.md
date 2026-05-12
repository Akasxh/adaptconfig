---
name: adaptconfig
description: Operate AdaptConfig, the AI-assisted integration configuration platform for Indian fintech APIs (CIBIL, eKYC, GST, UPI, payment gateways, account aggregator). Use when the user wants to upload an API spec or BRD, pick an adapter, generate an integration configuration, validate it with the 7-dimension LLM validator, run smoke tests, or inspect simulation, audit, history, and webhook resources via HTTP.
license: MIT
---

# AdaptConfig — Universal API Skill

This skill teaches an agent to drive AdaptConfig the same way a human does in
the web UI, but entirely over HTTP. Every interactive surface in
`frontend/src/pages/` traces to one of the endpoints listed below.

## When to use this skill

Trigger this skill when the user asks for any of the following:

- "Upload this API spec / BRD / OpenAPI file to AdaptConfig"
- "Generate a configuration for adapter X from document Y"
- "Validate this configuration", "run smoke tests", "run the full pipeline",
  or "validate and test"
- "Score this config against the 7 validator dimensions"
- "Roll back configuration X to version N", "compare two configs", "diff
  versions"
- "List adapters / documents / configurations / simulations / webhooks /
  audit entries"
- "Register / test / delete a webhook subscription"
- "Search across documents, adapters, configurations"
- "Show me dashboard analytics for the AdaptConfig deployment"

If the user is operating in the AdaptConfig repo or against an AdaptConfig
deployment, prefer the API endpoints below over scraping the React UI.

## Conventions

- **Base URL.** `http://localhost:8000` for local dev, or the deployed host
  (e.g. `https://adaptconfig-api-production.up.railway.app`). Substitute as
  needed; examples below use `$ADAPTCONFIG_BASE` as a placeholder.
- **Tenant headers.** Every request requires
  `X-Tenant-ID: <tenant>` and `X-Tenant-Role: <admin|editor|viewer>`. Add
  `X-Tenant-Name` for cleaner audit trails. JWT bearer auth is also
  supported via `Authorization: Bearer <token>` after `/api/v1/auth/login`.
- **Response envelope.** All `/api/v1/*` responses use the `APIResponse[T]`
  wrapper: `{ "success": bool, "data": T, "message": str, "errors": [str] }`.
- **LLM provider.** `FINSPARK_LLM_PROVIDER=openai` (model `gpt-4.1-nano`) is
  the canonical LLM path. Gemini is fallback-only.

## API reference (one curl per group)

### Health
```bash
curl -s "$ADAPTCONFIG_BASE/health"
```

### Auth
```bash
curl -s -X POST "$ADAPTCONFIG_BASE/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@finspark.local","password":"<password>"}'
# Use the returned access_token as Bearer auth for subsequent requests if
# you prefer JWT over X-Tenant-* headers.
```

### Adapters
```bash
# List all 8 pre-seeded Indian fintech adapters (filter by category optional).
curl -s "$ADAPTCONFIG_BASE/api/v1/adapters/?category=kyc" \
  -H "X-Tenant-ID: default" -H "X-Tenant-Role: admin"
```

### Documents
```bash
# Upload an OpenAPI spec or BRD; YAML/JSON specs auto-classify as api_spec.
curl -s -X POST "$ADAPTCONFIG_BASE/api/v1/documents/upload?doc_type=api_spec" \
  -H "X-Tenant-ID: default" -H "X-Tenant-Role: admin" \
  -F "file=@test_fixtures/05_perfect_kyc_api.yaml;type=application/x-yaml"
```

### Configurations
```bash
# Generate a configuration from a parsed document + adapter version.
curl -s -X POST "$ADAPTCONFIG_BASE/api/v1/configurations/generate" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: default" -H "X-Tenant-Role: admin" \
  -d '{"document_id":"<DOC_ID>","adapter_version_id":"<AV_ID>","name":"My eKYC"}'

# One-shot composite pipeline: LLM validate → smoke test, all server-side.
# This is the preferred way to run the configured → testing → done flow.
curl -s -X POST "$ADAPTCONFIG_BASE/api/v1/configurations/<CONFIG_ID>/validate-and-test" \
  -H "X-Tenant-ID: default" -H "X-Tenant-Role: admin"
```

### Simulations
```bash
# Run a standalone simulation. test_type=integration triggers the 7-dimension
# LLM validator; test_type=smoke triggers rule-based mock tests.
curl -s -X POST "$ADAPTCONFIG_BASE/api/v1/simulations/run" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: default" -H "X-Tenant-Role: admin" \
  -d '{"configuration_id":"<CONFIG_ID>","test_type":"integration"}'
```

### Audit, search, webhooks, analytics
```bash
# Audit log (paginated):
curl -s "$ADAPTCONFIG_BASE/api/v1/audit/?page=1&page_size=20" \
  -H "X-Tenant-ID: default" -H "X-Tenant-Role: admin"

# Search across documents/adapters/configs:
curl -s "$ADAPTCONFIG_BASE/api/v1/search/?q=KYC" \
  -H "X-Tenant-ID: default" -H "X-Tenant-Role: admin"

# Register a webhook (events: document.parsed, config.created, simulation.passed, ...):
curl -s -X POST "$ADAPTCONFIG_BASE/api/v1/webhooks/" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: default" -H "X-Tenant-Role: admin" \
  -d '{"url":"https://example.test/hook","events":["simulation.passed"],"secret":"changeme"}'

# Dashboard analytics:
curl -s "$ADAPTCONFIG_BASE/api/v1/analytics/dashboard" \
  -H "X-Tenant-ID: default" -H "X-Tenant-Role: admin"
```

## End-to-end sample: upload → suggest → generate → validate

This mirrors `scripts/skill_smoke.py` exactly and is the canonical happy
path. Run against a clean DB to reproduce the gold-standard 7/7 score on
the OpenAI provider.

```bash
BASE="$ADAPTCONFIG_BASE"
HDR=(-H "X-Tenant-ID: default" -H "X-Tenant-Role: admin" -H "X-Tenant-Name: agent")

# 1. Upload the gold-standard eKYC OpenAPI spec.
DOC_ID=$(curl -s -X POST "$BASE/api/v1/documents/upload?doc_type=api_spec" \
  "${HDR[@]}" \
  -F "file=@test_fixtures/05_perfect_kyc_api.yaml;type=application/x-yaml" \
  | jq -r '.data.id')

# 2. Suggest an adapter: list the catalogue and pick the eKYC one. (A
#    dedicated /adapters/suggest endpoint is on the roadmap; in the meantime
#    pick by category.)
AV_ID=$(curl -s "$BASE/api/v1/adapters/?category=kyc" "${HDR[@]}" \
  | jq -r '.data.adapters[0].versions[0].id')

# 3. Generate a configuration.
CONFIG_ID=$(curl -s -X POST "$BASE/api/v1/configurations/generate" \
  -H "Content-Type: application/json" "${HDR[@]}" \
  -d "{\"document_id\":\"$DOC_ID\",\"adapter_version_id\":\"$AV_ID\",\"name\":\"Gold eKYC\"}" \
  | jq -r '.data.id')

# 4. Validate with the 7-dimension LLM validator. Expect passed_tests==7.
curl -s -X POST "$BASE/api/v1/simulations/run" \
  -H "Content-Type: application/json" "${HDR[@]}" \
  -d "{\"configuration_id\":\"$CONFIG_ID\",\"test_type\":\"integration\"}" \
  | jq '{status:.data.status, passed:.data.passed_tests, total:.data.total_tests}'

# 5. Optional: run the composite validate→test pipeline in one call.
curl -s -X POST "$BASE/api/v1/configurations/$CONFIG_ID/validate-and-test" "${HDR[@]}" \
  | jq '{phase, val_pass:.data.validation.passed_tests, val_total:.data.validation.total_tests}'
```

## Operational notes for agents

- **Sequencing.** Configuration → simulation requires the document to be in
  `parsed` status. The upload endpoint blocks until parsing finishes (no SSE
  in MVP); if `status="failed"`, inspect `error_message`.
- **Idempotency.** `POST /configurations/{id}/validate-and-test` is safe to
  re-run; the server best-efforts lifecycle transitions and swallows
  `InvalidTransitionError` when the config is already past `configured`.
- **Pagination.** Listing endpoints accept `page` + `page_size` (omit both to
  fetch all rows for a tenant — fine for small datasets, paginate at scale).
- **Out of scope for MVP.** Streaming responses on `validate-and-test`,
  vector-embedding-based adapter suggestion, and a separate API auth model
  are deferred to follow-up issues.

## Examples (when to invoke this skill)

- User: "Upload the file at `test_fixtures/05_perfect_kyc_api.yaml` and run
  the full pipeline." → upload via `/documents/upload`, generate via
  `/configurations/generate`, then `POST /configurations/{id}/validate-and-test`.
- User: "Check what configurations are in `testing` state." → `GET
  /configurations/summary` then filter `by_status.testing`.
- User: "Roll back the eKYC config to v3." → `POST
  /configurations/{id}/rollback` with `{"target_version": 3}`.
- User: "List the last 20 audit entries for resource type
  `configuration`." → `GET /audit/?resource_type=configuration&page=1&page_size=20`.
