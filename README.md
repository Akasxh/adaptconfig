<div align="center">

# AdaptConfig

### AI-Powered Integration Configuration Platform

*Automate API integration setup for Indian lending platforms — from weeks to minutes*

[![Live App](https://img.shields.io/badge/Live_App-Railway-blueviolet?style=for-the-badge)](https://adaptconfig-frontend-production.up.railway.app)
[![API Docs](https://img.shields.io/badge/API_Docs-Swagger-green?style=for-the-badge)](https://adaptconfig-api-production.up.railway.app/docs)
[![Tests](https://img.shields.io/badge/Tests-899_Passing-brightgreen?style=for-the-badge)]()

---

**FinSpark Hackathon** · IIT Patna · April 2026

**Team Nucleolus**

| Name | Department |
|------|-----------|
| **S Akash** (Team Lead) | EE, IIT Patna |
| **Swayam Jain** | EE, IIT Patna |
| **Yash Kamdar** | AI&ML, IIT Patna |

</div>

---

## The Problem

Indian lending platforms must integrate with **8+ external APIs** — credit bureaus, KYC providers, payment gateways, fraud detection, and more. Each integration requires:

- Manually reading API documentation and mapping 20-50+ fields
- Configuring authentication (OAuth2, mTLS, API keys)
- Writing test harnesses for each endpoint
- **2–4 weeks per integration**

## The Solution

**Upload an API spec → AI generates the config → Simulate & validate → Deploy**

AdaptConfig uses **Google Gemini 3** to parse API specifications, automatically map fields with confidence scores, and run simulation tests — reducing integration setup from weeks to minutes.

### Live Results (Production)

| Step | Input | Output |
|------|-------|--------|
| **Parse** | CIBIL Bureau API v2 (YAML) | 4 endpoints, 28 fields, 2 auth schemes @ **95% confidence** |
| **Generate** | Document + Adapter selection | 28/28 fields mapped @ **100% confidence** |
| **Simulate** | Smoke test (8 steps) | **8/8 PASSED** ✓ |

---

## Screenshots

### Dashboard
<img src="docs/images/01-dashboard.png" alt="Dashboard" width="100%">

> Real-time metrics — 8 adapters, configuration summary with confidence scores, activity charts

### Document Parsing
<img src="docs/images/04-document-detail-modal.png" alt="Document Detail" width="100%">

> Uploaded CIBIL API spec parsed into 5 tabs: Summary (95% confidence), Endpoints, Fields (28 extracted), Auth (OAuth2 + API Key), Raw JSON

### Field Mapping Table
<img src="docs/images/09-config-generated.png" alt="Config Detail" width="100%">

> AI-generated field mappings with editable targets, transform dropdowns, confidence bars. Lifecycle: Draft → Configured → Validating → Testing → Active

### Simulation Results
<img src="docs/images/12-simulation-results.png" alt="Simulation" width="100%">

> 8/8 tests pass — config structure, field coverage, each API endpoint, auth validation, hooks

### Adapter Catalog
<img src="docs/images/15-adapters-page.png" alt="Adapters" width="100%">

> 8 pre-built Indian fintech adapters with category filtering (Bureau, KYC, GST, Payment, Fraud, Notification, Open Banking)

### Audit Trail
<img src="docs/images/14-audit-log.png" alt="Audit" width="100%">

> Immutable audit log — every action tracked with filters and pagination

---

## Architecture

```
┌────────────────┐      ┌─────────────────────────┐      ┌────────────┐
│                │      │                         │      │            │
│  React 18 +    │─────▶│   FastAPI Backend        │─────▶│ PostgreSQL │
│  TypeScript    │      │   34 API endpoints       │      │ (Railway)  │
│  Tailwind CSS  │      │                         │      │            │
│                │      │  ┌───────────────────┐  │      └────────────┘
└────────────────┘      │  │ Document Parser    │  │
                        │  │ PDF · DOCX · YAML  │  │      ┌────────────┐
                        │  └───────────────────┘  │      │            │
                        │  ┌───────────────────┐  │─────▶│  Gemini 3  │
                        │  │ Config Engine      │  │      │  Flash AI  │
                        │  │ AI + Rule Mapper   │  │      │            │
                        │  └───────────────────┘  │      └────────────┘
                        │  ┌───────────────────┐  │
                        │  │ Simulation Engine  │  │
                        │  │ 8 Mock Adapters    │  │
                        │  └───────────────────┘  │
                        └─────────────────────────┘
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 18 · TypeScript · Tailwind CSS · Recharts |
| Backend | FastAPI · SQLAlchemy (async) · Pydantic v2 |
| AI | Google Gemini 3 Flash via REST API |
| Database | PostgreSQL (prod) · SQLite (dev) |
| Deployment | Railway · Docker · nginx |
| Testing | pytest (899 tests, 82% coverage) · vitest |

## Features

| Category | Features |
|----------|----------|
| **Parsing** | PDF, DOCX, YAML, JSON intake · endpoint/field/auth extraction · confidence scoring |
| **AI Config** | Gemini 3 generation · fuzzy field matching · synonym lookup · transform suggestions |
| **Adapters** | CIBIL · eKYC · GST · Payment · Fraud · SMS · Account Aggregator · Email |
| **Simulation** | Mock API responses per adapter · 8-step validation · step-by-step results |
| **Lifecycle** | Draft → Configured → Validating → Testing → Active · rollback · history |
| **Security** | JWT auth · RBAC · rate limiting · PII masking · CORS · path traversal protection |
| **UX** | Glassmorphism dark theme · search · webhooks · audit log · pagination · filters |

## Quick Start

```bash
# Clone
git clone https://github.com/Akasxh/adaptconfig.git && cd adaptconfig

# Backend
cp .env.example .env          # Add your FINSPARK_GEMINI_API_KEY
uv sync --frozen
uv run uvicorn finspark.main:app --reload --port 8000

# Frontend (new terminal)
cd frontend && npm ci && npm run dev
```

Open **http://localhost:5173** · API docs at **http://localhost:8000/docs**

## Test Documents

Four API specs with increasing complexity for testing:

| File | Difficulty | Endpoints | Fields | Auth |
|------|-----------|-----------|--------|------|
| `test_fixtures/01_simple_kyc_api.yaml` | ⭐ Simple | 1 | 4 | API Key |
| `test_fixtures/02_payment_gateway_api.yaml` | ⭐⭐ Medium | 4 | 10+ | JWT Bearer |
| `test_fixtures/cibil_bureau_api_v2.yaml` | ⭐⭐⭐ Complex | 4 | 28 | OAuth2 + API Key |
| `test_fixtures/03_account_aggregator_complex.yaml` | ⭐⭐⭐⭐ Advanced | 4 | 20+ | Mutual TLS + JWT |

## Project Stats

| Metric | Value |
|--------|-------|
| API Endpoints | 34 |
| Pre-built Adapters | 8 |
| Automated Tests | 899 |
| Code Coverage | 82% |
| Frontend Pages | 8 |
| Lines of Code | ~15,000 |

---

<div align="center">

📖 **[How to Use](HOW_TO_USE.md)** · 🚀 **[Live App](https://adaptconfig-frontend-production.up.railway.app)** · 📡 **[API Docs](https://adaptconfig-api-production.up.railway.app/docs)**

</div>
