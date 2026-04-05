# How to Use AdaptConfig

## Live Application

| | URL |
|---|---|
| **Frontend** | https://adaptconfig-frontend-production.up.railway.app |
| **Backend API** | https://adaptconfig-api-production.up.railway.app |
| **API Documentation** | https://adaptconfig-api-production.up.railway.app/docs |

---

## Quick Start (5 minutes)

### Step 1: Upload an API Specification

1. Open the [frontend](https://adaptconfig-frontend-production.up.railway.app)
2. Click **Documents** in the sidebar
3. Drag & drop a YAML/JSON API spec file into the upload area
4. The document is parsed automatically — you'll see it appear with status **"Parsed"**

**Test files included in the repo:**
| File | Complexity | What it tests |
|---|---|---|
| `test_fixtures/01_simple_kyc_api.yaml` | Simple | 1 endpoint, 4 fields, API key auth |
| `test_fixtures/02_payment_gateway_api.yaml` | Medium | 4 endpoints, 10+ fields, JWT auth, refunds |
| `test_fixtures/cibil_bureau_api_v2.yaml` | Complex | 4 endpoints, 28 fields, OAuth2 + API key |
| `test_fixtures/03_account_aggregator_complex.yaml` | Advanced | 4 endpoints, 20+ nested fields, mutual TLS, consent lifecycle |

### Step 2: View Parsed Results

1. **Click on the document row** — a detail modal opens with 5 tabs:
   - **Summary**: Title, confidence score, services identified
   - **Endpoints**: API paths, HTTP methods, descriptions
   - **Fields**: All extracted fields with types, required flags, source section
   - **Auth**: Authentication schemes (OAuth2, API Key, mTLS)
   - **Raw JSON**: Full parsed output

### Step 3: Generate Configuration

1. Click **Configurations** in the sidebar
2. Click **"+ Generate Config"**
3. Select your uploaded document from the **Document** dropdown
4. Select an adapter (e.g., **CIBIL Credit Bureau**) and version
5. Enter a name or use the auto-suggested one
6. Click **Generate**

The system uses **Gemini 3 AI** to generate field mappings, then **augments** them with a rule-based fuzzy matcher for confidence scoring.

### Step 4: Review & Edit Mappings

After generation, click the config row to expand:
- **Field Mappings Table**: Source → Target with confidence bars
  - Green (100%): Exact match
  - Yellow (60-99%): Fuzzy match
  - Red (<60%): Low confidence — review manually
- **Edit**: Click any target field to type a new mapping
- **Transform**: Select transformations (upper, parse_date, normalize_phone, etc.)

### Step 5: Validate

Click the **"Validate"** button to check the configuration:
- **Coverage Score**: % of fields successfully mapped
- **Errors**: Missing required fields, invalid URLs
- **Warnings**: Low-confidence mappings, unmapped optional fields

### Step 6: Run Simulation

1. Click **Simulations** in the sidebar
2. Click **"Run Simulation"**
3. Select your configuration from the dropdown
4. Choose test type: **smoke** (quick), **integration**, or **full**
5. Click **Run**

The simulation tests:
- Config structure validation
- Field mapping coverage
- Each API endpoint (with mock responses)
- Authentication configuration
- Webhook/hook configuration

### Step 7: Lifecycle Transitions

Progress your config through the lifecycle:
```
Draft → Configured → Validating → Testing → Active
```
Each step has a button in the config detail view.

---

## Other Features

### Search
- Click **Search** in the sidebar
- Type any keyword (e.g., "CIBIL", "payment", "credit")
- Results grouped by type with relevance scores

### Adapters
- Click **Adapters** — see all 8 pre-built Indian fintech adapters
- Filter by category (Bureau, KYC, GST, Payment, Fraud, Notification, Open Banking)
- Click any card to see versions and endpoints

### Webhooks
- Click **Webhooks** — register webhook URLs for event notifications
- Supports: config.created, simulation.completed, etc.

### Audit Log
- Click **Audit Log** — see all actions with timestamps
- Filter by action type and resource type
- Paginated for large histories

### Dashboard
- Overview metrics: adapter count, document count, config count
- Configuration summary with confidence scores
- Activity charts and adapter status distribution

---

## API Usage (for developers)

All endpoints documented at: https://adaptconfig-api-production.up.railway.app/docs

### Example: Upload + Generate + Simulate via cURL

```bash
# 1. Upload a document
curl -X POST https://adaptconfig-api-production.up.railway.app/api/v1/documents/upload \
  -H "X-Tenant-ID: default" -H "X-Tenant-Role: admin" \
  -F "file=@your_api_spec.yaml;type=application/x-yaml" \
  -F "doc_type=api_spec"

# 2. Generate config (use document_id and adapter_version_id from responses)
curl -X POST https://adaptconfig-api-production.up.railway.app/api/v1/configurations/generate \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: default" -H "X-Tenant-Role: admin" \
  -d '{"document_id":"<DOC_ID>","adapter_version_id":"<AV_ID>","name":"My Config"}'

# 3. Run simulation
curl -X POST https://adaptconfig-api-production.up.railway.app/api/v1/simulations/run \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: default" -H "X-Tenant-Role: admin" \
  -d '{"configuration_id":"<CONFIG_ID>","test_type":"smoke"}'
```

---

## Local Development

```bash
git clone https://github.com/Akasxh/adaptconfig.git
cd adaptconfig
cp .env.example .env  # Edit with your Gemini API key
uv sync --frozen
uv run uvicorn finspark.main:app --reload --port 8000  # Backend
cd frontend && npm ci && npm run dev  # Frontend (separate terminal)
```
