# AdaptConfig — Project Overview

> **Team Nucleolus** | S Akash, Swayam Jain, Yash Kamdar | IIT Patna
> **Hackathon:** FinSpark Hackathon, April 2026

---

## 1. Problem Statement

Indian lending platforms (NBFCs, digital lenders, banks) must integrate with **8+ external APIs** to operate — credit bureaus (CIBIL), identity verification (eKYC/Aadhaar), GST verification, payment gateways (Razorpay, Paytm), fraud detection, SMS/email notifications, and account aggregators.

**The pain points:**

- **Manual field mapping** — Each API has 20–50+ fields that must be manually read from documentation and mapped to the internal system. A developer reads the CIBIL spec, figures out which field maps to what, writes the transforms.
- **Authentication complexity** — Each provider uses different auth: OAuth2, mTLS, API keys, JWT bearer tokens. Configuring these correctly is error-prone.
- **No test environment** — Developers can't test integrations without hitting live APIs (which often require paid sandbox access or production credentials).
- **Multi-version hell** — APIs have multiple coexisting versions (CIBIL v1, v2). Configs must handle version differences, deprecation, and rollback.
- **Compliance & audit** — Every configuration change must be tracked for regulatory compliance. Manual processes lack auditability.
- **Time cost** — **2–4 weeks per integration**, repeated across every new API partner. For a platform integrating 8 APIs, that's months of engineering time just on integration plumbing.

**In short:** Integration configuration is a repetitive, error-prone, time-consuming bottleneck that slows down fintech lending operations.

---

## 2. How AdaptConfig Solves It

AdaptConfig automates the entire integration configuration lifecycle in 4 steps:

### Upload → Parse → Generate → Simulate

```
Upload API Spec (YAML/JSON/PDF/DOCX)
        │
        ▼
   AI-Powered Parsing ──── GPT-5 extracts endpoints, fields, auth schemes
        │                   with 95% confidence on structured specs
        ▼
   Auto-Configuration ──── GPT-5 reasoning model generates field mappings,
        │                   suggests transforms, detects API chains
        ▼
   Simulation & Validation ── Mock API server runs 8-step tests without
        │                      hitting real APIs
        ▼
   Deploy (Draft → Active lifecycle with rollback support)
```

### Core Modules (mapped to PS requirements)

| PS Requirement | Module | What It Does |
|---|---|---|
| **Requirement Parsing Engine** | `DocumentParser` | Parses YAML, JSON, PDF, DOCX API specs. Extracts endpoints, fields (with types, required flags), auth schemes. Uses LLM for unstructured docs, regex+OpenAPI walker for structured specs. |
| **Integration Registry & Hook Library** | `AdapterRegistry` | 8 pre-built Indian fintech adapters (CIBIL, eKYC, GST, Payment, Fraud, SMS, Email, Account Aggregator) with multi-version support and lifecycle hooks (`pre_request`, `post_response`, `on_error`, `on_timeout`). |
| **Auto-Configuration Engine** | `ConfigGenerator` + `FieldMapper` | 3-strategy field matching (synonym dictionary with 100+ entries, fuzzy matching via `rapidfuzz`, token Jaccard). LLM generates mappings + auto-suggests transforms (`parse_number`, `normalize_phone`, `parse_date`). Includes diff engine and rollback. |
| **Simulation & Testing Framework** | `IntegrationSimulator` + `MockAPIServer` | 8 adapter-specific mock response generators. Runs smoke/integration/full test suites. Parallel version testing (v1 vs v2). No real API calls needed. |

### Enterprise Features

| Feature | How |
|---|---|
| **Multi-tenant isolation** | `TenantMixin` on all models, row-level filtering via `tenant_id` |
| **Full audit trail** | Immutable `AuditLog` table — every action tracked |
| **Config versioning** | `ConfigurationHistory` with snapshot/restore/rollback |
| **API chaining** | Endpoints with `depends_on`, `extract` (JSONPath), `inject` (template strings) — supports OAuth → resource → status flows |
| **Security inspection** | 10 OWASP API Top 10 rule-based checks + LLM semantic analysis |
| **Spectral linting** | Auto-runs on uploaded OpenAPI/AsyncAPI specs for quality checks |
| **Webhook delivery** | HMAC-SHA256 signed, fires on real events (`document.uploaded`, `config.created`, `simulation.completed`) |
| **MCP server** | 6 tools exposed via Model Context Protocol for LLM-driven automation (Claude Desktop, IDE agents) |

---

## 3. Unique Selling Points (USP)

### 1. AI-First, Not AI-Bolted
The LLM isn't a chatbot overlay — it's embedded in the core pipeline. GPT-5 does the actual work: parsing specs, generating field mappings, detecting API chains, validating configs. The AI understands Indian fintech API semantics (CIBIL score fields, Aadhaar patterns, UPI VPAs).

### 2. Zero-API-Call Simulation
Full integration testing without hitting real APIs. The mock server generates deterministic, adapter-specific responses that mirror real Indian fintech API schemas (CIBIL credit reports, eKYC verification results, GST returns). Developers can validate configs before spending on sandbox access.

### 3. API Chain Detection & Execution
Automatically detects multi-step API flows (e.g., OAuth token → credit pull → status check) and generates chained configurations with dependency resolution via topological sort and JSONPath extraction/injection.

### 4. India-Specific Adapter Catalog
8 pre-built adapters for the Indian fintech ecosystem — not generic API connectors, but adapters that understand CIBIL bureau formats, Aadhaar XML responses, GST return structures, UPI payment flows, and Account Aggregator consent lifecycles.

### 5. Weeks to Minutes
Demonstrated end-to-end: CIBIL Bureau API v2 spec (28 fields, 4 endpoints, OAuth2 + API Key auth) → fully mapped and tested config in under 2 minutes, with 100% field mapping confidence and 8/8 simulation tests passing.

### 6. Multi-LLM Provider Support
Not locked to one AI vendor. Supports OpenAI GPT-5 (default), OpenRouter (Claude/Gemini gateway), and Google Gemini — all behind a unified `get_llm_client()` interface. Switch providers with one env var.

### 7. MCP Server for Agentic Workflows
Exposes core capabilities via Model Context Protocol, so LLM agents (Claude Desktop, IDE copilots) can parse docs, generate configs, and run simulations programmatically — enabling fully autonomous integration setup.

---

## 4. How to Run

### Prerequisites
- Python 3.11+ with `uv` package manager
- Node.js 18+ with npm
- An OpenAI API key (or OpenRouter/Gemini key)

### Backend Setup

```bash
# Clone the repo
git clone https://github.com/Akasxh/adaptconfig.git
cd adaptconfig

# Create .env file
cp .env.example .env
# Edit .env and fill in:
#   FINSPARK_LLM_PROVIDER=openai
#   FINSPARK_OPENAI_API_KEY=your-key-here
#   FINSPARK_AI_ENABLED=true
#   FINSPARK_DEBUG=true
#   FINSPARK_DATABASE_URL=sqlite+aiosqlite:///./adaptconfig.db
#   FINSPARK_SECRET_KEY=<generate with: openssl rand -hex 32>
#   FINSPARK_ENCRYPTION_KEY=<generate with: openssl rand -hex 32>

# Install dependencies
uv sync --extra dev

# Start the backend (port 8000)
uv run uvicorn finspark.main:app --reload --port 8000
```

### Frontend Setup

```bash
# In a separate terminal
cd frontend
npm install
npm run dev
# Frontend runs on http://localhost:5173
```

### Docker (one command)

```bash
docker compose up --build
# Frontend: http://localhost:3000
# API Docs: http://localhost:8000/docs
```

### Default Login
- **Email:** admin@finspark.dev
- **Password:** Whatever you set in `FINSPARK_ADMIN_PASSWORD` (e.g. `Admin1234!`)
- The admin user is auto-seeded on first startup if `FINSPARK_ADMIN_PASSWORD` is set and no users exist yet
- In debug mode, if the env var is missing, seeding is silently skipped

### Running Tests

```bash
# Backend (1297 tests)
uv run python -m pytest tests/ --no-cov

# Frontend type-check + build
cd frontend && npx tsc --noEmit && npm run build
```

### MCP Server (for Claude Desktop / IDE agents)

```bash
uv run adaptconfig-mcp
```

---

## 5. Architecture at a Glance

```
User → Frontend (React 18 / TypeScript / Tailwind)
            │
            ▼
       FastAPI Backend (34 endpoints, async)
            │
    ┌───────┼───────────────────────────────────┐
    │       │                                   │
    ▼       ▼                                   ▼
Document  Config       Simulation          Security
Parser    Generator    Engine              Inspector
(LLM +    (LLM +      (Mock APIs +        (OWASP rules
 regex)    fuzzy        chain executor)      + LLM)
           match)
    │       │               │                   │
    └───────┴───────┬───────┘                   │
                    ▼                           │
              SQLite (dev) /                    │
              PostgreSQL (prod)                 │
                    │                           │
                    ▼                           ▼
              Audit Log +              Spectral Linter +
              Webhooks +               MCP Server
              Event System

LLM Providers: OpenAI GPT-5 (default) | OpenRouter Claude | Gemini
Deployment: Railway (Docker + nginx)
```

---

## 6. Live URLs

| Resource | URL |
|---|---|
| Frontend | https://adaptconfig-frontend-production.up.railway.app |
| Backend API | https://adaptconfig-api-production.up.railway.app |
| Swagger Docs | https://adaptconfig-api-production.up.railway.app/docs |
| GitHub | https://github.com/Akasxh/adaptconfig |

---

## 7. Project Stats

| Metric | Value |
|---|---|
| API Endpoints | 34 |
| Pre-built Adapters | 8 (Indian fintech specific) |
| Automated Tests | 1297 |
| Frontend Pages | 8 |
| LLM Tasks | 5 (parsing, config gen, field mapping, simulation validation, search) |
| LLM Providers | 3 (OpenAI, OpenRouter, Gemini) |
| Cost per Full Workflow | ~$0.10 |

---

## 8. Test Fixtures (for demos)

| File | Complexity | Endpoints | Fields | Auth |
|---|---|---|---|---|
| `test_fixtures/01_simple_kyc_api.yaml` | Simple | 1 | 4 | API Key |
| `test_fixtures/02_payment_gateway_api.yaml` | Medium | 4 | 10+ | JWT Bearer |
| `test_fixtures/cibil_bureau_api_v2.yaml` | Complex | 4 | 28 | OAuth2 + API Key |
| `test_fixtures/03_account_aggregator_complex.yaml` | Advanced | 4 | 20+ | Mutual TLS + JWT |
