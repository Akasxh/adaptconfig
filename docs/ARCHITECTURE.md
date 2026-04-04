# FinSpark Architecture & Internals

## Table of Contents

- [1. System Overview](#1-system-overview)
- [2. Backend Architecture](#2-backend-architecture)
- [3. Frontend Architecture](#3-frontend-architecture)
- [4. Key Workflows](#4-key-workflows)
- [5. Data Model](#5-data-model)
- [6. Configuration](#6-configuration)
- [7. Testing Strategy](#7-testing-strategy)

---

## 1. System Overview

FinSpark is an AI-assisted integration configuration and orchestration engine for enterprise lending platforms. It automates the process of configuring integrations with Indian fintech services (credit bureaus, KYC providers, payment gateways, GST verification, fraud detection, Account Aggregators) by parsing business requirement documents and generating adapter configurations with intelligent field mapping.

### High-Level Architecture

```
                          +---------------------+
                          |   React Frontend    |
                          |  (Vite + React 18)  |
                          |  Port 5173          |
                          +----------+----------+
                                     |
                            Axios / REST API
                                     |
                          +----------v----------+
                          |   FastAPI Backend    |
                          |   (Uvicorn, async)   |
                          |   Port 8000          |
                          +----------+----------+
                                     |
         +---------------------------+---------------------------+
         |                           |                           |
+--------v--------+     +----------v---------+     +-----------v----------+
|  Middleware      |     |  Service Layer     |     |  Event System        |
|  Stack           |     |                    |     |  (events.py)         |
|  - CORS          |     |  - DocumentParser  |     |  - Webhook delivery  |
|  - TenantAuth    |     |  - ConfigGenerator |     |  - Async listeners   |
|  - RateLimiter   |     |  - FieldMapper     |     +----------------------+
|  - SecurityHdrs  |     |  - Simulator       |
|  - Deprecation   |     |  - LLM Client      |
|  - RequestLog    |     |  - SearchService   |
+-----------------+     +----------+---------+
                                    |
                          +---------v---------+
                          |  SQLAlchemy Async  |
                          |  (aiosqlite)       |
                          +---------+---------+
                                    |
                          +---------v---------+
                          |  SQLite Database   |
                          |  (finspark.db)     |
                          +-------------------+
```

### Core Capabilities

| Capability | Description |
|---|---|
| Document Parsing | Extract integration requirements from DOCX, PDF, YAML, JSON (OpenAPI) |
| Config Generation | Rule-based field mapping with optional LLM augmentation (Gemini) |
| Adapter Registry | 8 pre-built adapters for Indian fintech services with versioning |
| Simulation | Mock-based integration testing with step-by-step results |
| Lifecycle Management | State machine governing config status (draft -> active) |
| Webhook Events | Event-driven notifications with HMAC-signed delivery |
| Multi-Tenant | Row-level tenant isolation with JWT auth in production |
| Audit Trail | Immutable logging of all configuration changes |

---

## 2. Backend Architecture

### 2.1 FastAPI App Structure

**Entry point:** `src/finspark/main.py`

The application uses FastAPI's `lifespan` context manager for startup/shutdown:

**Startup sequence:**
1. Initialize database (Alembic migrations in production, `create_all` in debug)
2. Seed 8 pre-built adapters with versioned schemas
3. Create upload directory
4. Register event handlers for webhook delivery

**Shutdown:**
1. Close the shared LLM client connection pool (if created)

**Route registration:**
```
/health                    -> health.router        (no prefix)
/api/v1/documents/...      -> documents.router
/api/v1/adapters/...       -> adapters.router
/api/v1/configurations/... -> configurations.router
/api/v1/simulations/...    -> simulations.router
/api/v1/audit/...          -> audit.router
/api/v1/search/...         -> search.router
/api/v1/webhooks/...       -> webhooks.router
/api/v1/analytics/...      -> analytics.router     (no prefix)
/metrics                   -> inline handler
```

### 2.2 Middleware Stack

Middleware executes in reverse registration order (last added = first executed). The effective execution order for an inbound request:

```
Request
  -> CORSMiddleware          (CORS headers, preflight handling)
  -> TrustedHostMiddleware   (production only, validates Host header)
  -> TenantMiddleware        (JWT auth in production, X-Tenant-* headers in debug)
  -> RateLimiterMiddleware   (per-tenant sliding window, 100 req/60s default)
  -> DeprecationHeaderMiddleware (Sunset/Deprecation headers for deprecated versions)
  -> RequestLoggingMiddleware (timing, structured log output)
  -> SecurityHeadersMiddleware (X-Content-Type-Options, CSP, X-Frame-Options)
  -> Route handler
```

**TenantMiddleware** (`core/middleware.py`):
- Production mode: requires `Authorization: Bearer <JWT>` with `tenant_id`, `tenant_name`, `role` claims
- Debug mode: reads from `X-Tenant-ID`, `X-Tenant-Name`, `X-Tenant-Role` headers
- Auth bypass paths: `/health`, `/docs`, `/redoc`, `/openapi.json`, `/metrics`

**RateLimiterMiddleware** (`core/rate_limiter.py`):
- Per-tenant sliding window counter using `OrderedDict`
- Bounded tenant set (max 10,000 entries) with LRU eviction
- Returns `429` with `Retry-After` header when limit exceeded
- Also collects in-memory metrics (request counts per endpoint, avg response time)

**DeprecationHeaderMiddleware** (`core/middleware.py`):
- Matches URL pattern `/api/v1/adapters/{id}/versions/{version}/...`
- Adds `Sunset`, `Deprecation: true`, and `Link` (successor-version) headers

### 2.3 Database Layer

**Engine:** SQLAlchemy 2.0 async with `aiosqlite` (SQLite). Configurable via `FINSPARK_DATABASE_URL`.

**Session management** (`core/database.py`):
- `async_session_factory`: `async_sessionmaker` with `expire_on_commit=False`
- `get_db()`: async generator dependency that auto-commits on success, rolls back on exception
- `init_db()`: imports all models and runs `Base.metadata.create_all` for development

**Base mixins** (`models/base.py`):
- `UUIDMixin`: UUID v4 primary key as `String(36)`
- `TenantMixin`: `tenant_id` column with index for row-level isolation
- `TimestampMixin`: `created_at` and `updated_at` with `server_default=func.now()`

### 2.4 API Routes

All API routes follow the same pattern:

```
Request -> Middleware (tenant context injected into request.state)
        -> Route handler (receives dependencies via FastAPI Depends)
        -> Service layer (business logic)
        -> Database (SQLAlchemy async queries)
        -> APIResponse[T] wrapper returned
```

**Dependency injection** (`api/dependencies.py`):
- `get_tenant_context(request)`: extracts `TenantContext` from `request.state`
- `require_role(*roles)`: factory returning a `Depends` that enforces RBAC
- Service factories: `get_document_parser()`, `get_config_generator()`, `get_simulator()`, `get_diff_engine()`, `get_rollback_manager()`, `get_adapter_registry(db)`, `get_audit_service(db)`

**Standard response wrapper:**
```python
class APIResponse(BaseModel, Generic[T]):
    success: bool = True
    data: T | None = None
    message: str = ""
    errors: list[str] = []
```

**Endpoint inventory (34 endpoints):**

| Route Group | Endpoints | Auth |
|---|---|---|
| Health | `GET /health` | None |
| Documents | `POST /upload`, `GET /`, `GET /{id}`, `DELETE /{id}` | admin/editor for writes |
| Adapters | `GET /`, `GET /{id}`, `GET /{id}/versions/{v}/deprecation`, `GET /{id}/match` | viewer |
| Configurations | `POST /generate`, `GET /`, `GET /{id}`, `PATCH /{id}`, `POST /{id}/validate`, `POST /{id}/transition`, `GET /{id}/diff/{id}`, `GET /templates`, `GET /summary`, `GET /{id}/history`, `POST /{id}/rollback`, `GET /{id}/history/compare`, `GET /{id}/export`, `POST /batch-validate`, `POST /batch-simulate` | admin/editor for mutations |
| Simulations | `POST /run`, `GET /`, `GET /{id}`, `GET /{id}/stream` | admin/editor for run |
| Audit | `GET /` (paginated with filters) | viewer |
| Search | `GET /?q=...` | viewer |
| Webhooks | `POST /`, `GET /`, `DELETE /{id}`, `POST /{id}/test` | admin for mutations |
| Analytics | `GET /dashboard`, `GET /health` | viewer |
| Metrics | `GET /metrics` | None |

### 2.5 Service Layer

#### DocumentParser (`services/parsing/document_parser.py`)

Parses uploaded files and extracts structured integration requirements:

- **DOCX**: Uses `python-docx` to extract paragraphs and table data
- **PDF**: Uses `pypdf` to extract text from all pages
- **YAML/JSON**: Detects OpenAPI/Swagger specs; resolves `$ref` references against the spec
- **Text extraction**: Regex-based extraction of endpoints, fields, auth requirements, services, sections, security/SLA requirements

Returns `ParsedDocumentResult` containing: `endpoints`, `fields`, `auth_requirements`, `services_identified`, `sections`, `security_requirements`, `sla_requirements`, `confidence_score`.

#### FieldMapper + ConfigGenerator (`services/config_engine/field_mapper.py`)

**FieldMapper** maps source document fields to target adapter fields using three strategies:

1. **Exact synonym match**: Lookup table of 17 canonical Indian fintech field groups (PAN, Aadhaar, GSTIN, mobile, etc.) with 100+ synonyms
2. **Fuzzy string matching**: `rapidfuzz` library with `token_sort_ratio` scorer, threshold 0.6
3. **Partial token matching**: Jaccard similarity on underscore-split tokens

**ConfigGenerator** orchestrates the full config generation:
- Separates request fields from response fields using `source_section` metadata
- Maps both against adapter request/response schemas
- Generates default hooks (audit logger, PII masker, schema validator)
- Includes retry policy, timeout, and metadata with unmapped field tracking

#### LLM Client (`services/llm/client.py`)

Async Gemini REST API client using `httpx` (no SDK dependency):
- Endpoint: `generativelanguage.googleapis.com/v1beta`
- Supports `generate()` for text and `generate_json()` for structured JSON output
- API key passed via `x-goog-api-key` header
- Module-level shared client with lazy initialization; closed during app shutdown

**Config generation pipeline** (`services/llm/config_generator.py`):
- System instruction establishes FinSpark's role as an integration configuration engine
- Prompt template includes adapter info, parsed document content, and output schema
- Temperature 0.1 for deterministic output

**Hybrid generation** (in `routes/configurations.py`):
1. If AI enabled + Gemini key present: attempt LLM generation
2. Always augment with rule-based field mapper for confidence scores
3. Rule-based confidence overrides LLM self-assessment
4. Fallback to pure rule-based if LLM fails

#### ConfigDiffEngine (`services/config_engine/diff_engine.py`)

Recursive structural diff between two configuration dicts:
- **Dict diffing**: key-by-key comparison with path tracking
- **List diffing**: Identity-based matching using `source_field`, `path`, `name`, `id` keys; falls back to positional matching for primitive lists
- **Breaking change detection**: Changes to `auth.type`, `base_url`, `version`, `endpoints` are flagged as breaking

#### RollbackManager (`services/config_engine/rollback.py`)

Version history and rollback for configurations:
- `snapshot()`: creates a `ConfigurationHistory` entry with serialized state
- `rollback()`: restores config to a target version, creates pre-rollback snapshot, bumps version number
- `list_versions()`: returns full version history
- `compare_versions()`: diffs two historical versions using `ConfigDiffEngine`

#### ConfigValidator (`services/config_engine/validator.py`)

Rule-based validation producing a `ValidationReport`:
- `required_fields_mapped`: checks top-level keys and field mapping coverage
- `auth_configured`: validates auth type against known types
- `endpoints_reachable`: validates path strings and HTTP methods
- `hooks_valid`: validates hook types and handler presence
- `retry_policy_valid`: bounds checking on max_retries and backoff_factor
- `timeout_reasonable`: validates timeout within 100ms-120,000ms range

#### IntegrationSimulator (`services/simulation/simulator.py`)

Mock-based integration testing framework:

**Test steps (full run):**
1. Config structure validation
2. Field mapping validation (coverage >= 30%, low confidence <= 50%)
3. Per-endpoint mock testing (generates realistic responses per adapter)
4. Auth config validation
5. Hooks validation
6. Error handling validation
7. Retry logic validation

**Execution modes:**
- `run_simulation()`: synchronous, returns all steps
- `run_simulation_stream()`: generator yielding steps one at a time
- `run_simulation_stream_async()`: async generator with per-step timeout (30s default)
- `run_parallel_version_test()`: tests same request against two API versions

**MockAPIServer** (`services/simulation/mock_responses.py`):
- 8 adapter-specific mock generators producing deterministic, hash-seeded responses
- Covers: CIBIL credit scores/reports, Aadhaar eKYC, GST verification, Payment Gateway, Fraud Detection, SMS/Email Gateway, Account Aggregator
- Routes by adapter display name first, then by base_url pattern matching

#### IntegrationLifecycle (`services/lifecycle.py`)

State machine with defined transition graph:

```
draft -> configured -> validating -> testing -> active -> deprecated -> draft
                ^          |            |                      ^
                +----------+            +--- configured        |
                                                               |
                                        active -> rollback -> configured/draft
```

Maintains an in-memory audit trail of transitions. Raises `InvalidTransitionError` for illegal state changes.

#### IntegrationSearch (`services/search.py`)

Keyword-based natural language search across three entity types:

- **Query parsing**: tokenizes input, maps keywords to categories (kyc, bureau, payment...), statuses, auth types
- **Adapter scoring**: category match (10pts), token-in-name (3pts), token-in-description (1pt), auth type match (5pts)
- **Configuration scoring**: status match (10pts), token-in-name (3pts)
- **Simulation scoring**: status match (10pts), "simulation" keyword boost (5pts)
- Results sorted by score within each group

#### WebhookDelivery (`services/webhook_delivery.py`)

Event-driven webhook delivery system:
- Retrieves all active webhooks for the tenant matching the event type (or `*` wildcard)
- SSRF protection via `is_safe_url()` check before delivery
- HMAC-SHA256 signature using decrypted webhook secret in `X-Webhook-Signature` header
- 3 delivery attempts with exponential backoff (2s, 4s)
- Records `WebhookDelivery` entries with status, response code, attempt count

#### AnalyticsService (`services/analytics.py`)

Dashboard metrics aggregated per tenant:
- Configuration stats: total, by_status breakdown, active/draft counts
- Simulation stats: total, pass rate, average duration
- Document stats: total, by_status
- Audit entry count
- Health score (0-100): weighted composite of config activity, active configs, simulation pass rate

### 2.6 Security

#### JWT Authentication

- **Production mode**: `TenantMiddleware` requires `Authorization: Bearer <JWT>`
- Token payload contains: `tenant_id`, `tenant_name`, `role`, `exp`
- Signed with `HS256` using `FINSPARK_SECRET_KEY`
- Expiry configurable via `FINSPARK_JWT_EXPIRY_MINUTES` (default: 60)
- `create_tenant_token()` helper for test/dev tooling

#### Tenant Isolation

- All tenant-scoped models include `TenantMixin` adding indexed `tenant_id` column
- Every query filters by `tenant_id` from the authenticated context
- Adapters are global (not tenant-scoped); configurations, documents, simulations, webhooks, and audit logs are tenant-scoped

#### Role-Based Access Control

Three roles with the `require_role()` dependency:

| Role | Permissions |
|---|---|
| `admin` | Full access: create, read, update, delete, deploy, rollback |
| `editor` | Create and modify: upload docs, generate configs, run simulations |
| `viewer` | Read-only: list and view all resources |

#### PII Masking

- `core/security.py`: regex patterns for Aadhaar, PAN, phone, email, account numbers
- `mask_pii()` replaces matches with masked values (e.g., `XXXX-XXXX-XXXX`)
- `PIIMaskingFilter` (`core/logging_filter.py`): logging filter applied globally, masks PII in log messages and arguments before emission

#### Encryption

- Fernet symmetric encryption using key derived from `FINSPARK_ENCRYPTION_KEY` via SHA-256
- Used for webhook secrets (`encrypt_value()`/`decrypt_value()`)
- Production validator rejects insecure default keys and keys shorter than 32 characters

#### SSRF Protection

`core/url_validator.py`: validates webhook URLs against blocked networks:
- `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`, `::1/128`
- DNS resolution check before HTTP delivery

#### Security Headers

Every response includes:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Content-Security-Policy: default-src 'self'; ...`

### 2.7 Event System

`core/events.py`: simple pub/sub event bus:

```python
events.on(event_type, handler)   # Register handler
await events.emit(event_type, data)  # Emit to all handlers (sync or async)
events.clear()                   # Clear all handlers (testing)
```

**Standard event types:**
- `config.created`, `config.updated`, `config.deployed`, `config.rolled_back`
- `simulation.started`, `simulation.completed`
- `document.parsed`
- `adapter.deprecated`

Events are wired to `deliver_event()` during app lifespan startup. Handlers fire via `asyncio.create_task()` for non-blocking delivery.

---

## 3. Frontend Architecture

### 3.1 React App Structure

**Stack:** React 18, TypeScript, Vite, Tailwind CSS v4, React Router v6, TanStack React Query, Recharts, Lucide icons, Axios.

**Entry point:** `frontend/src/main.tsx` -> `App.tsx`

**Component hierarchy:**
```
<ErrorBoundary>
  <QueryClientProvider>
    <ToastProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>     -- sidebar + header + <Outlet>
            <Route path="/" element={<Dashboard />} />
            <Route path="/adapters" ... />
            <Route path="/documents" ... />
            <Route path="/configurations" ... />
            <Route path="/simulations" ... />
            <Route path="/audit" ... />
            <Route path="/search" ... />
            <Route path="/webhooks" ... />
          </Route>
          <Route path="*" element={<NotFound />} />
        </Routes>
      </BrowserRouter>
    </ToastProvider>
  </QueryClientProvider>
</ErrorBoundary>
```

**Error boundaries:**
- `ErrorBoundary`: top-level catch-all
- `PageErrorBoundary`: wraps each page route for isolated error recovery

**Lazy loading:** Dashboard page is lazy-loaded with `Suspense` fallback.

### 3.2 Design System

**Palette:** Deep space background (`#08090e`) with glassmorphism surfaces:

| Token | Value | Usage |
|---|---|---|
| `--color-bg-base` | `#08090e` | Page background |
| `--color-glass` | `rgba(255,255,255,0.05)` | Card backgrounds |
| `--color-brand` | `#38e5cd` | Primary teal/cyan |
| `--color-accent` | `#fbbf24` | Warm amber highlights |
| `--color-text-primary` | `#f1f5f9` | Headings, body text |
| `--color-text-secondary` | `#94a3b8` | Labels, secondary text |
| `--color-border` | `rgba(255,255,255,0.06)` | Card/section borders |

**Typography:** Plus Jakarta Sans (body), JetBrains Mono (code/data).

**Glass card utility:** `card` class applies border-radius, border, glass background, and backdrop blur.

**Layout:** 220px sidebar with grouped navigation (Core / Integrations / Governance), 56px top header bar with system health indicator.

### 3.3 Data Flow

**API layer** (`lib/api.ts`):
- Axios instance with 30s timeout
- Default headers: `X-Tenant-ID: default`, `X-Tenant-Role: admin`
- Error interceptor with timeout and status logging
- Typed API modules: `healthApi`, `adaptersApi`, `documentsApi`, `configurationsApi`, `simulationsApi`, `auditApi`, `analyticsApi`, `searchApi`, `webhooksApi`

**React Query configuration:**
```typescript
{
  retry: 1,
  refetchOnWindowFocus: false,
  staleTime: 30_000   // 30 second cache
}
```

**Type system** (`types/index.ts`):
- TypeScript interfaces matching all Pydantic response schemas
- `APIResponse<T>`, `PaginatedResponse<T>` generics
- Entity types: `Adapter`, `Document`, `Configuration`, `Simulation`, `AuditEntry`
- Sub-types: `FieldMapping`, `SimulationStepResult`, `ConfigDiffItem`, `SearchResult`

### 3.4 Page Components

| Page | Path | Key Features |
|---|---|---|
| **Dashboard** | `/` | Metric cards (configs, docs, simulations, health), area chart (weekly activity), bar chart (throughput), config summary with status breakdown, adapter category pie chart |
| **Documents** | `/documents` | Drag-and-drop upload (DOCX, PDF, YAML, JSON), document list with status badges, 5-tab detail modal (overview, fields, endpoints, auth, raw), delete with confirmation |
| **Search** | `/search` | Debounced natural language search, grouped results (adapters, configurations, simulations), relevance score bars |
| **Adapters** | `/adapters` | 8 adapter cards with icons, category filter pills (bureau, kyc, gst, payment, fraud, notification, open_banking), detail modal with version list, deprecation info |
| **Configurations** | `/configurations` | Generate config (document + adapter version dropdowns), editable field mappings with confidence scores, lifecycle stepper (draft -> active), export (JSON/YAML), version history, rollback, validate |
| **Webhooks** | `/webhooks` | Create webhook (URL, events, secret), list active webhooks, delete, test delivery with response display |
| **Simulations** | `/simulations` | Run simulation from configuration, step-by-step results with pass/fail indicators, request/response payload inspection |
| **Audit Log** | `/audit` | Filter by action type and resource type, paginated results, expandable detail rows |

**Shared components:**
- `Pagination`: page navigation with page size control
- `EmptyState`: placeholder for empty data states
- `Skeleton`: loading shimmer placeholders
- `Toast`: notification system with provider context

---

## 4. Key Workflows

### 4.1 Document Upload and Parsing

```
User uploads file (DOCX/PDF/YAML/JSON)
  |
  v
Route: POST /api/v1/documents/upload?doc_type=brd
  |
  +-- Validate extension (.docx, .pdf, .yaml, .yml, .json)
  +-- Validate doc_type against DocType enum (brd, sow, api_spec, other)
  +-- Validate file size against max_upload_size_mb
  +-- Sanitize filename (PurePosixPath.name to prevent traversal)
  +-- Save to uploads/{tenant_id}/{filename}
  +-- Create Document record (status=parsing)
  |
  v
DocumentParser.parse(file_path, doc_type)
  |
  +-- DOCX: python-docx -> paragraphs + table text
  +-- PDF: pypdf -> page text extraction
  +-- YAML/JSON: detect OpenAPI -> resolve $ref -> extract endpoints + fields
  +-- Text: regex extraction of endpoints, fields, auth, services, sections
  |
  v
ParsedDocumentResult stored as JSON in document.parsed_result
Document status -> "parsed" (or "failed" with error_message)
Audit log entry created
```

### 4.2 Configuration Generation

```
User selects document + adapter version
  |
  v
Route: POST /api/v1/configurations/generate
  {document_id, adapter_version_id, name}
  |
  v
Fetch Document.parsed_result and AdapterVersion schema
  |
  v
[If AI enabled + Gemini key]
  |
  +-- LLM generation via Gemini REST API
  |     Prompt: adapter info + parsed document + output schema
  |     Temperature: 0.1, JSON response mode
  |
  +-- Rule-based augmentation:
  |     - Index LLM mappings by source_field
  |     - Override confidence scores with rule-based values
  |     - Backfill any source fields LLM missed
  |
  +-- generation_path = "llm_with_rule_augment"
  |
[Else or if LLM fails]
  |
  +-- Pure rule-based generation via ConfigGenerator:
  |     - Extract source fields from parsed document
  |     - Extract target fields from adapter request/response schemas
  |     - FieldMapper.map_fields() using synonym + fuzzy + token matching
  |     - Build endpoint configs, transformation rules, hooks
  |     - Include retry policy and timeout defaults
  |
  +-- generation_path = "rule_based" or "rule_based_fallback"
  |
  v
Ensure minimum confidence (0.6) for mapped fields
Save Configuration (status=configured, version=1)
Create ConfigurationHistory entry (version=1, change_type=created)
Audit log entry created
```

### 4.3 Simulation Execution

```
User triggers simulation for a configuration
  |
  v
Route: POST /api/v1/simulations/run
  {configuration_id, test_type: "full"|"smoke"|"schema_only"}
  |
  v
Create Simulation record (status=running)
  |
  v
IntegrationSimulator.run_simulation(full_config, test_type)
  |
  +-- Step 1: Config structure validation (required keys present)
  +-- Step 2: Field mapping validation (coverage >= 30%)
  +-- Step 3: Per-endpoint mock testing
  |     MockAPIServer routes to adapter-specific generator
  |     Generates deterministic responses using hash-seeded data
  +-- Step 4: Auth config validation
  +-- Step 5: Hooks validation
  +-- [full only] Step 6: Error handling validation
  +-- [full only] Step 7: Retry logic validation
  |
  v
Each step produces SimulationStepResult:
  {step_name, status, request_payload, expected_response,
   actual_response, duration_ms, confidence_score, error_message}
  |
  v
Save Simulation results + individual SimulationStep records
Update Configuration status -> "testing" (if passed) or keep "configured" (if failed)
Audit log entry created
Events emitted: simulation.completed -> webhook delivery
```

**SSE streaming** (`GET /simulations/{id}/stream`):
- If simulation already complete: replays stored steps from DB
- If pending: runs fresh simulation with async step generator, persists results after all steps complete

### 4.4 Lifecycle Transitions

```
State Machine:
  draft -> configured -> validating -> testing -> active -> deprecated -> draft
                                                        \-> rollback -> configured/draft

User triggers: POST /api/v1/configurations/{id}/transition
  {target_state, reason}
  |
  v
IntegrationLifecycle validates transition against TRANSITIONS graph
  |
  +-- Valid: update config.status, create ConfigurationHistory entry
  +-- Invalid: raise InvalidTransitionError -> 400 response
  |
  v
Response includes available_transitions from new state
Audit log entry created
```

---

## 5. Data Model

### Entity Relationships

```
Tenant (1) ----< (*) Document
Tenant (1) ----< (*) Configuration
Tenant (1) ----< (*) Simulation
Tenant (1) ----< (*) AuditLog
Tenant (1) ----< (*) Webhook

Adapter (1) ----< (*) AdapterVersion
AdapterVersion (1) ----< (*) Configuration
Document (1) ----< (*) Configuration     (optional, SET NULL on delete)
Configuration (1) ----< (*) ConfigurationHistory
Configuration (1) ----< (*) Simulation
Simulation (1) ----< (*) SimulationStep
Webhook (1) ----< (*) WebhookDelivery
```

### Table Details

| Table | Key Columns | Mixins |
|---|---|---|
| `tenants` | name, slug (unique), is_active, settings (JSON) | UUID, Timestamp |
| `documents` | filename, file_type, doc_type, status, raw_text, parsed_result (JSON), error_message | UUID, Tenant, Timestamp |
| `adapters` | name, category, description, is_active, icon | UUID, Timestamp |
| `adapter_versions` | adapter_id (FK), version, version_order, status, base_url, auth_type, request_schema (JSON), response_schema (JSON), endpoints (JSON), config_template (JSON), changelog | UUID, Timestamp |
| `configurations` | name, adapter_version_id (FK), document_id (FK, nullable), status, version (int), field_mappings (JSON), transformation_rules (JSON), hooks (JSON), auth_config (JSON, encrypted), full_config (JSON), notes | UUID, Tenant, Timestamp |
| `configuration_history` | configuration_id (FK), version (int), change_type, previous_value (JSON), new_value (JSON), changed_by | UUID, Tenant, Timestamp |
| `simulations` | configuration_id (FK), status, test_type, total_tests, passed_tests, failed_tests, duration_ms, results (JSON), error_log | UUID, Tenant, Timestamp |
| `simulation_steps` | simulation_id (FK), step_name, step_order, status, request_payload (JSON), expected_response (JSON), actual_response (JSON), duration_ms, confidence_score (float), error_message | UUID, Timestamp |
| `audit_logs` | actor, action (indexed), resource_type (indexed), resource_id (indexed), details (JSON), ip_address, user_agent | UUID, Tenant, Timestamp |
| `webhooks` | url, secret (Fernet-encrypted), events (JSON list), is_active | UUID, Tenant, Timestamp |
| `webhook_deliveries` | webhook_id (FK), event_type, payload (JSON), status, response_code, attempts | UUID, Timestamp |

### Status Enums

**Document status:** `uploaded` -> `parsing` -> `parsed` | `failed`

**Configuration status:** `draft` -> `configured` -> `validating` -> `testing` -> `active` -> `deprecated` | `rollback`

**Simulation status:** `pending` -> `running` -> `passed` | `failed` | `error`

**Adapter version status:** `active` | `deprecated` | `beta`

---

## 6. Configuration

All settings are managed via `core/config.py` using `pydantic-settings`. Environment variables use the `FINSPARK_` prefix.

| Variable | Default | Description |
|---|---|---|
| `FINSPARK_APP_NAME` | `FinSpark Integration Engine` | Application display name |
| `FINSPARK_APP_VERSION` | `0.1.0` | Application version |
| `FINSPARK_DEBUG` | `false` | Debug mode (relaxes auth, enables SQL echo, uses create_all instead of Alembic) |
| `FINSPARK_DATABASE_URL` | `sqlite+aiosqlite:///./finspark.db` | SQLAlchemy async database URL |
| `FINSPARK_SECRET_KEY` | `change-me-in-production-...` | JWT signing key. Must be >= 32 chars and not contain "change-me" or "insecure" when debug=false |
| `FINSPARK_JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `FINSPARK_JWT_EXPIRY_MINUTES` | `60` | JWT token expiry in minutes |
| `FINSPARK_ENCRYPTION_KEY` | `change-me-in-production` | Fernet encryption key for webhook secrets. Same production validation as secret_key |
| `FINSPARK_ALLOWED_HOSTS` | `["localhost", "127.0.0.1"]` | Trusted host whitelist (production only) |
| `FINSPARK_RATE_LIMIT_MAX_REQUESTS` | `100` | Max requests per tenant per window |
| `FINSPARK_RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate limit sliding window duration |
| `FINSPARK_UPLOAD_DIR` | `./uploads` | File upload storage directory |
| `FINSPARK_MAX_UPLOAD_SIZE_MB` | `50` | Maximum upload file size in MB |
| `FINSPARK_AI_ENABLED` | `false` | Enable LLM-augmented config generation |
| `FINSPARK_GEMINI_API_KEY` | (empty) | Google Gemini API key |
| `FINSPARK_GEMINI_MODEL` | `gemini-3-flash-preview` | Gemini model identifier |
| `FINSPARK_LLM_API_KEY` | (empty) | Generic LLM API key (unused, reserved) |
| `FINSPARK_OPENAI_API_KEY` | (empty) | OpenAI API key (unused, reserved) |

Settings are loaded from `.env` file and environment variables. A `model_validator` enforces that `secret_key` and `encryption_key` are strong (>= 32 chars, no insecure defaults) when `debug=false`.

---

## 7. Testing Strategy

### Test Organization

Tests are split across two root directories with identical structure:

```
tests/                      # Primary test suite
  conftest.py               # Shared fixtures (async DB session, test client, tenant headers)
  unit/
    conftest.py
    test_field_mapper.py
    test_diff_engine.py
    test_security.py
    test_lifecycle.py
    test_simulator.py
    test_mock_responses.py
    test_config_validator.py
    test_pii_masking.py
    test_rate_limiter.py
    test_events.py
    test_rollback.py
    test_analytics.py
    test_jwt_token.py
    test_rbac.py
    ... (50+ unit test files)
  integration/
    test_full_workflow.py
    test_e2e_flow.py
    test_api_endpoints.py
    test_webhooks.py
    test_search_api.py
    test_config_export.py
    test_batch_operations.py
    test_simulation_all_adapters.py
    ... (10+ integration test files)

backend/tests/              # Secondary test suite
  conftest.py
  unit/
    test_document_parser.py
    test_config_generation.py
    test_simulation.py
    test_llm_client.py
    ... (20+ unit test files)
  integration/
    test_api_documents.py
    test_api_config.py
    test_full_workflow.py
    ... (8+ integration test files)

frontend/src/components/__tests__/
  Pagination.test.tsx
  EmptyState.test.tsx
  Layout.test.tsx
  Toast.test.tsx
  PageErrorBoundary.test.tsx
```

### Test Categories

**Unit tests** cover:
- Service logic: document parsing, field mapping, diff engine, config validation, lifecycle state machine, simulator, mock responses, search scoring
- Security: PII masking, JWT creation/verification, encryption, RBAC enforcement, security headers, SSRF URL validation
- Infrastructure: rate limiter (sliding window, bounded tenants), event system, health monitor, database session management
- Production hardening: insecure key rejection, model index verification, UTC timestamp usage, Alembic migration

**Integration tests** cover:
- Full API workflow: document upload -> config generation -> simulation -> lifecycle transitions
- API endpoint testing: all 34 endpoints with proper tenant context
- Webhook registration, delivery, and test sending
- Search API with real database queries
- Configuration export (JSON/YAML), batch validation/simulation
- All 8 adapter simulation scenarios
- Tenant mutation isolation

**Frontend tests** (Vitest + React Testing Library):
- Component rendering: Layout sidebar navigation, Pagination controls, Toast notifications, EmptyState display, PageErrorBoundary error recovery

### Coverage

850+ tests passing with 82% code coverage.
