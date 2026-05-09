# AdaptConfig (FinSpark) — Interview Quick Reference

## What Is It?
Multi-tenant SaaS that automates Indian fintech API integrations using AI. Upload an API spec → AI generates a ready-to-deploy integration config → test it with simulations → deploy with full audit trail.

---

## Tech Stack
- **Backend:** FastAPI (async) + SQLAlchemy 2.0 + PostgreSQL
- **Frontend:** React 18 + TypeScript + Vite + Tailwind + TanStack Query
- **LLM:** Google Gemini (gemini-2.5-flash) via REST API
- **Deploy:** Railway (backend + frontend + PostgreSQL)

---

## Core Flow
```
Upload Document → Parse (LLM) → Select Adapter → Generate Config (LLM) → Validate → Simulate → Deploy
```

---

## Feature Breakdown

### 1. Document Parsing
- Accepts DOCX, PDF, YAML, JSON (BRDs, API specs, SOWs)
- **LLM extracts:** endpoints (URL + method), fields (PAN, Aadhaar, etc.), auth schemes (OAuth2, API key, Bearer, HMAC), SLA/security requirements
- Returns structured JSON with confidence scores
- Files stored tenant-scoped on filesystem, metadata in DB

### 2. Configuration Generation
- **Input:** parsed document + selected adapter (e.g., CIBIL Bureau v2)
- **LLM generates:** base_url, endpoints, auth config, field_mappings (source→target with transformations), retry policy, timeout
- Each mapping has a confidence score

**Lifecycle State Machine:**
```
DRAFT → CONFIGURED → VALIDATING → TESTING → ACTIVE → DEPRECATED
                                                ↓
                                            ROLLBACK
```

**Validation checks:** required fields, auth type correctness, timeout bounds, retry policy, endpoint reachability

### 3. Rollback & History
- Every config update snapshots to `ConfigurationHistory` table
- Rollback restores field_mappings, auth_config, full_config from any previous version
- **Diff engine** compares two versions showing changed fields, added/removed mappings

### 4. Simulations (Mock Testing)
- Generates realistic mock API responses based on adapter response schemas
- Indian fintech mock data generators (CIBIL scores, KYC results, payment statuses)
- **Test types:** full, smoke, schema_only, parallel_version
- **Pass rate** = average of individual step confidence scores (not binary)

### 5. Webhooks
- Register URL + secret + event filter (e.g., `["configuration.deployed", "simulation.completed"]`)
- **Delivery:** async via BackgroundTasks, HMAC-SHA256 signed (`X-Webhook-Signature`)
- **Retry:** 3 attempts, exponential backoff. 4xx → no retry (client error). 5xx → retry
- **Security:** SSRF protection (blocks private IPs), signing failure → abort immediately
- Secret encrypted at rest with Fernet

### 6. Audit Log
- Immutable record of every action: actor, action, resource, IP, timestamp, details JSON
- Searchable by actor, action, resource_type, date range

### 7. Adapters
- 8 pre-built Indian fintech adapters: Bureau (CIBIL), KYC, GST, Payment, Fraud, Notification, Open Banking
- Each has versioned schemas (request/response JSON Schema), auth types, endpoints, changelogs

---

## Auth & Multi-Tenancy
- **Registration:** email + password → PBKDF2-HMAC-SHA256 (260k iterations)
- **Login:** returns JWT (HS256, 60min expiry) with `{user_id, email, tenant_id, role}`
- **Refresh token** flow for seamless re-auth
- **TenantMiddleware** extracts tenant from JWT → all queries scoped by `tenant_id`
- **Role-based access:** `require_role("admin", "editor")` dependency

---

## Security Highlights
- PII masking in logs (Aadhaar, PAN, phone, email, account numbers)
- Encrypted secrets (Fernet with SHA-256 derived key)
- SSRF protection on webhook URLs
- Rate limiting: 100 req/60s per user
- Security headers: CSP, X-Frame-Options, etc.

---

## LLM Integration (Gemini)
- **Client:** direct REST via httpx (no SDK)
- **Two uses:**
  1. **Document parsing** — extract structured entities from raw text
  2. **Config generation** — generate complete integration config from adapter + document
- System instructions define the extraction/generation schema
- Responses parsed as JSON with error handling for network/timeout/API failures

---

## Database Models (9 core)
| Model | Purpose |
|---|---|
| User | Auth, role, tenant binding |
| Tenant | Multi-tenancy isolation |
| Adapter / AdapterVersion | Pre-built integration templates with schemas |
| Document | Uploaded files + parsed results |
| Configuration | Generated integration config + lifecycle status |
| ConfigurationHistory | Version snapshots for rollback |
| Simulation / SimulationStep | Test runs + individual step results |
| Webhook / WebhookDelivery | Event subscriptions + delivery tracking |
| AuditLog | Immutable action trail |

All models use UUID PKs, tenant scoping, and server-side timestamps.

---

## Key Architecture Decisions
1. **LLM over rules** — Gemini handles parsing/generation; rule-based as fallback
2. **Async everywhere** — FastAPI + asyncpg + aiosqlite for non-blocking I/O
3. **Immutable audit** — append-only log, no deletes
4. **Webhook hardening** — abort on signing failure, skip 4xx retries, SSRF protection
5. **Average pass rate** — simulation scores average step confidence, not binary pass/fail
6. **Tenant isolation** — middleware + DB-level tenant_id on every row

---

## Testing
- **899 backend tests** (pytest, async fixtures) — 82% coverage
- Frontend: vitest + React Testing Library
