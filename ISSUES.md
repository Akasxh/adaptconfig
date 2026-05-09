# AdaptConfig — Feature Issues & Enhancements

> Sourced from API expert feedback session. Each issue is reasoned through against the current codebase state. Issues tagged `[needs discussion]` require further clarification before implementation.

---

## OPEN — Issue #0: Workflow Orchestration Engine (Arbitrary Graphs: Cyclic, Acyclic, Sync, Async, Event-Driven)

**Status:** OPEN — Highest priority new feature
**Priority:** Critical
**Tags:** `core-feature`, `architecture`, `paradigm-shift`, `orchestration`
**Depends on:** Issue #4 (Transformation Engine), Issue #6 (Runtime Proxy)
**Effort:** XXL — this IS the product

---

### The One-Line Pitch

AdaptConfig needs a **workflow engine that can express any integration topology** — sequential chains, parallel fan-outs, polling loops, event-driven pauses, saga rollbacks, approval cycles, and nested sub-workflows — running in sync or async mode, with hard termination guarantees on every cycle.

---

### Why This Exists

Real fintech integrations are NOT pipelines. They are **state machines with cycles**.

Today, a Configuration has a flat `endpoints: []` list. Each endpoint is independent. There is no way to express:
- "Use the output of API call A as input to API call B"
- "Poll this status endpoint every 30 seconds until it returns `active`"
- "If this step fails, undo the previous two steps (saga compensation)"
- "Pause the workflow and wait for a webhook from the payment gateway"
- "If the underwriter rejects, go back to document collection and restart"

These are all **cycles** — steps that revisit previous steps. A DAG (Directed Acyclic Graph) cannot represent them. Airflow, simple task queues, and `depends_on` schemas all fail here.

---

### What We Are NOT Building

We are NOT building a DAG executor. Here's why:

| DAG | What We Need (State Machine) |
|-----|------------------------------|
| No cycles — every step runs at most once | Steps can be revisited (polling, retry, re-verification) |
| Execution order determined statically before run | Execution path determined at runtime by conditions + external events |
| All data available upfront or from upstream steps | Data accumulates across loop iterations — state evolves |
| Binary outcome: succeeds or fails | Can pause indefinitely waiting for external input, then resume |
| Completion is guaranteed (finite acyclic graph) | Needs explicit termination guarantees (fuel budget, max visits, timeouts) |

---

### 5 Real Fintech Patterns That Require Cycles

**Pattern 1: Polling Loop (e-NACH Mandate Activation)**

A mandate is submitted to the bank. The bank processes it asynchronously. We must poll until it activates or times out. This is a **cycle** — `check_status` is visited 1 to N times.

```
                    ┌─────────────────────────────┐
                    │                              │
submit_mandate ──► check_status ──► pending? ──YES─┘ (wait 30s, up to 20 times)
                                       │
                                      NO
                                       │
                                  ┌────┴────┐
                                  │         │
                               active    failed
                                  │         │
                              continue   escalate
```

**Pattern 2: Approval Loop with Document Re-submission**

Application is borderline. Underwriter requests additional documents. Customer uploads. Application re-enters review. This can happen multiple times — a **cycle back to the start**.

```
submit_application ──► auto_check ──► approved? ──YES──► disburse
                            │              │
                           NO         BORDERLINE
                            │              │
                        reject     send_to_underwriter
                                           │
                                    ┌──────┴──────┐
                                    │              │
                                 approved      need_docs
                                    │              │
                                 disburse    request_docs
                                                   │
                                           wait_for_upload ◄── (external event: customer uploads)
                                                   │
                                           submit_application ◄── CYCLE BACK TO START
```

**Pattern 3: Saga with Backward Compensation (Loan Disbursement)**

Disbursement fails after funds were reserved and mandate was created. We must **undo** steps in reverse order. The compensation chain goes backwards through nodes already visited.

```
reserve_funds ──► create_mandate ──► disburse ──► notify_borrower
                       │                │
                    FAILED           FAILED
                       │                │
                release_funds    cancel_mandate ──► release_funds
                (compensate)     (compensate)       (compensate)
```

**Pattern 4: Multi-API Reconciliation with External Resolution**

Settlement reconciliation finds mismatches. Ops team resolves them externally. Workflow resumes and re-checks. This loops until all mismatches are resolved.

```
fetch_settlements ──► compare ──► all_matched? ──YES──► done
                                       │
                                      NO
                                       │
                                 fetch_mismatch_details
                                       │
                                 attempt_auto_reconcile
                                       │
                                 still_mismatched? ──NO──► done
                                       │
                                      YES
                                       │
                                 escalate_to_ops
                                       │
                                 wait_for_resolution ◄── (external event: ops marks resolved)
                                       │
                                 compare ◄── CYCLE BACK
```

**Pattern 5: Payment Collection with Reminder Cycles**

Payment link sent to customer. If no payment within 24h, send reminder. Up to 3 reminders. Each reminder restarts the wait-for-payment timer. This is a **bounded cycle with counter**.

```
create_payment_link ──► send_to_customer ──► wait_for_payment ◄── (webhook: payment.captured)
                                                    │
                                              timed_out? (24h)
                                                    │
                                               YES: send_reminder ──► wait_for_payment ◄── CYCLE
                                               (max_visits: 3)
                                                    │
                                               exceeded: escalate_to_collections
```

---

### Architecture: Nodes + Transitions (Not `depends_on`)

The workflow is defined as a directed graph of **nodes** with explicit **transitions**. Each node declares where execution can go next — including backwards to a previously visited node.

#### Node Types

| Type | What It Does | Sync/Async |
|------|-------------|------------|
| `api_call` | Call an external API via the runtime proxy (Issue #6). Store response in workflow context. Apply transformations (Issue #4). | Either — declared per node |
| `parallel` | Fork into N branches. Execute concurrently. Join when all/any/n_of_m complete. | Inherently async internally |
| `wait` | Pause for a fixed duration (timer). Used between polling iterations. | Always async |
| `wait_for_event` | Pause until a specific external event arrives (webhook: `payment.captured`, `mandate.activated`, `docs.uploaded`). | Always async |
| `transform` | Apply data transformations to workflow context. No external call. | Sync |
| `condition` | Pure branching — evaluate expression, route to one of N target nodes. | Sync |
| `sub_workflow` | Invoke another workflow definition entirely. Pass context subset, receive result. | Either |
| `compensate` | Undo/rollback a previous step (saga pattern). | Sync |
| `terminal` | End state. Workflow is done: `completed`, `failed`, `rejected`, `escalated`, `timed_out`. | N/A |

#### Full Example: Loan Disbursement with Mandate Polling

```json
{
  "workflow": {
    "name": "loan_disbursement_with_mandate",
    "version": "1.0",
    "timeout_seconds": 86400,
    "max_total_steps": 500,
    "fuel_budget": 1000,
    "initial_state": "kyc_verify",

    "context_schema": {
      "application": { "type": "object" },
      "kyc_result": { "type": "object" },
      "credit_result": { "type": "object" },
      "fraud_result": { "type": "object" },
      "mandate_result": { "type": "object" },
      "mandate_status": { "type": "object" },
      "payment_result": { "type": "object" }
    },

    "nodes": {
      "kyc_verify": {
        "type": "api_call",
        "adapter": "eKYC Verification",
        "endpoint": "/verify/aadhaar",
        "execution": "sync",
        "input_mapping": {
          "aadhaar_number": "$.context.application.aadhaar"
        },
        "output_key": "kyc_result",
        "transitions": [
          { "condition": "$.kyc_result.verified == true", "target": "credit_and_fraud" },
          { "condition": "$.kyc_result.verified == false", "target": "kyc_failed" }
        ]
      },

      "credit_and_fraud": {
        "type": "parallel",
        "branches": [
          { "id": "credit", "node": "credit_pull" },
          { "id": "fraud", "node": "fraud_check" }
        ],
        "join": "all",
        "transitions": [
          {
            "condition": "$.context.credit_result.score >= 650 AND $.context.fraud_result.risk != 'high'",
            "target": "create_mandate"
          },
          { "condition": "$.context.credit_result.score < 650", "target": "rejected" },
          { "condition": "$.context.fraud_result.risk == 'high'", "target": "escalate_fraud" }
        ]
      },

      "credit_pull": {
        "type": "api_call",
        "adapter": "CIBIL Bureau",
        "endpoint": "/v2/credit-pull",
        "execution": "sync",
        "input_mapping": {
          "pan": "$.context.application.pan_number",
          "full_name": "$.context.kyc_result.verified_name"
        },
        "output_key": "credit_result"
      },

      "fraud_check": {
        "type": "api_call",
        "adapter": "Fraud Detection",
        "endpoint": "/v1/check",
        "execution": "async",
        "input_mapping": { "pan": "$.context.application.pan_number" },
        "output_key": "fraud_result"
      },

      "create_mandate": {
        "type": "api_call",
        "adapter": "Payment Gateway",
        "endpoint": "/v1/mandates/create",
        "execution": "sync",
        "input_mapping": {
          "amount": "$.context.application.emi_amount",
          "frequency": "monthly"
        },
        "output_key": "mandate_result",
        "on_failure": "rejected",
        "transitions": [
          { "target": "poll_mandate" }
        ]
      },

      "poll_mandate": {
        "type": "api_call",
        "adapter": "Payment Gateway",
        "endpoint": "/v1/mandates/{$.context.mandate_result.mandate_id}",
        "execution": "sync",
        "output_key": "mandate_status",
        "max_visits": 20,
        "on_max_visits": "mandate_timed_out",
        "transitions": [
          { "condition": "$.context.mandate_status.status == 'active'", "target": "disburse" },
          { "condition": "$.context.mandate_status.status == 'failed'", "target": "mandate_failed" },
          { "condition": "$.context.mandate_status.status == 'pending'", "target": "wait_before_poll" }
        ]
      },

      "wait_before_poll": {
        "type": "wait",
        "wait_type": "timer",
        "duration_seconds": 30,
        "transitions": [
          { "target": "poll_mandate" }
        ]
      },

      "disburse": {
        "type": "api_call",
        "adapter": "Payment Gateway",
        "endpoint": "/v1/payments/create",
        "execution": "sync",
        "input_mapping": {
          "amount": "$.context.application.loan_amount",
          "account_number": "$.context.application.bank_account"
        },
        "output_key": "payment_result",
        "on_failure": "compensate_mandate",
        "transitions": [
          { "target": "completed" }
        ]
      },

      "compensate_mandate": {
        "type": "compensate",
        "adapter": "Payment Gateway",
        "endpoint": "/v1/mandates/{$.context.mandate_result.mandate_id}/cancel",
        "execution": "sync",
        "transitions": [
          { "target": "disbursement_failed" }
        ]
      },

      "completed":           { "type": "terminal", "status": "completed" },
      "rejected":            { "type": "terminal", "status": "rejected", "reason": "low_credit_score" },
      "kyc_failed":          { "type": "terminal", "status": "rejected", "reason": "kyc_failed" },
      "escalate_fraud":      { "type": "terminal", "status": "escalated", "reason": "high_fraud_risk" },
      "mandate_failed":      { "type": "terminal", "status": "failed", "reason": "mandate_activation_failed" },
      "mandate_timed_out":   { "type": "terminal", "status": "failed", "reason": "mandate_poll_exceeded_20_attempts" },
      "disbursement_failed": { "type": "terminal", "status": "failed", "reason": "disbursement_failed_mandate_cancelled" }
    }
  }
}
```

Note the **cycle**: `poll_mandate → wait_before_poll → poll_mandate`. This loop runs up to 20 times (10 minutes of polling at 30s intervals). A DAG cannot represent this.

---

### Cycle Safety — Termination Guarantees

Every cycle MUST be bounded. The engine enforces this at **two levels**:

#### Level 1: Per-Node (prevents individual loops from running forever)

```json
{
  "max_visits": 20,
  "on_max_visits": "fallback_terminal_node"
}
```

Any node involved in a cycle MUST declare `max_visits`. The engine forces a transition to `on_max_visits` when exceeded.

#### Level 2: Per-Workflow (prevents the whole workflow from running forever)

```json
{
  "timeout_seconds": 86400,
  "max_total_steps": 500,
  "fuel_budget": 1000
}
```

| Guard | What It Does |
|-------|-------------|
| `timeout_seconds` | Wall-clock hard limit. Workflow killed unconditionally after this. |
| `max_total_steps` | Total node transitions across ALL nodes. Catches runaway combinations of multiple loops. |
| `fuel_budget` | Abstract cost unit. `api_call` costs 10 fuel, `wait` costs 1, `transform` costs 1. When fuel hits 0, workflow enters emergency terminal. Prevents expensive API call loops from burning budget. |

#### Level 3: Static Analysis at Definition Time

**The engine MUST reject workflow definitions** at creation time (not just at runtime) if:
- A cycle exists where no node in the cycle has `max_visits` set
- A node in a cycle has no reachable terminal state from its `on_max_visits` target
- The workflow has no `timeout_seconds`
- Any node has transitions that all point to itself with no exit condition (trivial infinite loop)

This is a graph analysis pass — detect all strongly connected components (Tarjan's algorithm), verify each has bounded exit.

---

### Execution Modes

The workflow itself doesn't have a single mode. **Each node declares its own execution style**, and the **caller** chooses how to interact with the workflow as a whole:

| Caller Mode | API | Behavior | Use Case |
|------------|-----|----------|----------|
| **Sync (blocking)** | `POST /api/v1/workflows/run` | Blocks until terminal state. Returns final context. | Simple 2-3 step chains completing in <30s |
| **Async (poll)** | `POST /api/v1/workflows/start` → `GET /api/v1/workflows/runs/{run_id}` | Returns `run_id` immediately. Caller polls for status. | Long workflows (mandate activation, reconciliation) |
| **Async (callback)** | `POST /api/v1/workflows/start` with `callback_url` | AdaptConfig POSTs final result to callback URL when done. | System-to-system integration |
| **Event-driven** | `POST /api/v1/workflows/runs/{run_id}/events/{event_type}` | External system pushes event. Workflow resumes from `PAUSED`. | Payment webhooks, document uploads, manual approvals |

A single workflow can use ALL of these. The loan disbursement example starts sync (KYC call takes 2s), goes async when it hits the mandate polling loop, and uses event-driven resume if it's waiting for a webhook.

---

### Workflow Run State Machine

```
              ┌─────── PAUSED (wait / wait_for_event node)
              │            │
              │        event arrives / timer fires
              │            │
              ▼            ▼
CREATED → RUNNING ──────────────────────► COMPLETED
              │                            REJECTED
              │                            ESCALATED
              │                            TIMED_OUT
              │
              └── node fails with on_failure ──► COMPENSATING ──► FAILED
                                                      │
                                                      └── compensation fails ──► CRITICAL_FAILURE
```

`PAUSED` is a **first-class durable state**. A workflow waiting for a mandate webhook is PAUSED — not running, not failed. On server restart, paused workflows rehydrate their wait.

---

### Durability & Persistence

Workflow state MUST survive process restarts:

- **After every node transition:** persist current node, full context, visit counts, timestamps, fuel remaining → DB
- **On startup:** rehydrate all `RUNNING` and `PAUSED` workflows. `RUNNING` workflows resume from last persisted node. `PAUSED` workflows re-register their event listeners.
- **Idempotency:** Every transition has a unique `transition_id`. Replaying a transition (after crash recovery) does NOT re-execute the API call if the `transition_id` already exists in the log.

---

### Parallel Execution (Fork/Join)

`parallel` nodes fork into N branches. The engine must handle:

| Join Mode | Behavior |
|-----------|----------|
| `all` | Wait for every branch to reach terminal or return. All results merged into context. |
| `any` | First branch to succeed wins. Cancel remaining branches. |
| `n_of_m` | Wait for N of M branches to succeed (e.g., 2 of 3 KYC providers agree). Cancel rest. |

**Branch isolation:** Branches cannot see each other's in-progress context mutations. Each branch gets a snapshot. Results are merged into parent context only at join.

**Branch failure:** Configurable per parallel node:
- `fail_fast`: First branch failure fails the whole parallel block
- `ignore_failures`: Failed branches are skipped at join, surviving results merged
- `compensate`: Failed branch triggers compensation for completed branches

**Dynamic fan-out:** Branch count determined at runtime:
```json
{
  "type": "parallel",
  "dynamic_branches": {
    "source": "$.context.applicants",
    "node_template": "process_single_applicant"
  },
  "join": "all"
}
```
If `$.context.applicants` has 50 entries, 50 branches are spawned.

---

### `[needs discussion]`

- **Storage:** Postgres for state persistence, or do we need Redis for fast transitions at high throughput? For v1, Postgres is likely fine. At 1000s concurrent workflows, revisit.
- **Workflow versioning:** If a definition changes while runs are in-flight, do running instances pin to the old version, or hot-swap? Recommendation: pin — running workflows use the version that existed when they started.
- **Visual editor:** JSON schema is sufficient for v1. Graph visualization (read-only, from JSON) is a nice-to-have. Full drag-and-drop editor is Phase 2+.
- **Expression language:** JSONPath for data access. What about conditions? Simple `==`, `!=`, `>=`, `AND`, `OR`? Or a real expression language (CEL, Jexl)?
- **Per-tenant isolation:** One tenant's runaway workflow must not starve another. Need per-tenant limits: max concurrent workflows, max fuel per hour, max paused workflows.
- **Workflow templates:** Pre-built workflow templates for common fintech patterns (KYC→Credit→Disburse, Mandate Activation, Reconciliation)? LLM-generated workflow from BRD document?

---

### How This Connects to Other Issues

```
Issue #4 (Transformation Engine) ──► used by api_call nodes to transform request/response
Issue #6 (Runtime Proxy)         ──► used by api_call nodes to actually hit external APIs
Issue #3 (Observability)         ──► every node transition emits to the API Call Ledger
Issue #7 (Audit Trail)           ──► every workflow run is an auditable event chain
Issue #1 (Contract Testing)      ──► can be implemented as a workflow (loop through endpoints, check schemas)
```

This issue is the **capstone**. Issues #3, #4, #6, #7 are building blocks. This is where they come together into a product.

---

### Files To Create

| File | Purpose |
|------|---------|
| `services/orchestration/engine.py` | Core state machine executor — load workflow, execute transitions, persist state |
| `services/orchestration/graph_validator.py` | Static analysis at definition time — cycle detection (Tarjan), termination proof, reachability |
| `services/orchestration/node_executor.py` | Dispatch by node type (api_call, wait, parallel, etc.) |
| `services/orchestration/context_store.py` | Workflow context read/write with branch isolation for parallel nodes |
| `services/orchestration/event_router.py` | Match incoming external events to paused workflow runs |
| `services/orchestration/parallel_executor.py` | Fork/join with branch isolation, cancellation, and merge |
| `services/orchestration/compensation.py` | Saga rollback — walk compensation chain on failure |
| `services/orchestration/fuel_tracker.py` | Termination budget enforcement (fuel, max_steps, timeout) |
| `services/orchestration/expression_eval.py` | Evaluate transition conditions against workflow context |
| `models/workflow.py` | `Workflow`, `WorkflowRun`, `WorkflowNodeVisit`, `WorkflowEvent`, `WorkflowContext` |
| `api/routes/workflows.py` | CRUD, run (sync), start (async), events (push), runs (poll) |
| `schemas/workflows.py` | Pydantic validation of workflow definitions including cycle safety |

---

---

## Issue #1: Live API Contract Testing (Schema Drift Detection)

**Priority:** High
**Tags:** `testing`, `core-feature`

### Problem

Simulations today are 100% mocked (`MockAPIServer.generate_response()`). They validate config *structure* — "does this config have the right fields?" — but never answer the real question: **"does this 3rd party API still behave the way our config expects?"**

A fintech adapter version (say CIBIL Bureau v2) was built against a known response schema. Six months later, CIBIL silently adds a field, changes a type from `int` to `string`, or deprecates an endpoint. Our config still passes all simulations because mocks never change. The integration breaks silently in production.

### What We Need

**API Contract Testing** — a mode that hits real (or sandbox) endpoints and compares actual responses against the expected schema stored in `AdapterVersion.response_schema`.

**Detection targets:**
- **Schema drift:** New fields appeared, fields removed, type changes (`credit_score` was `int`, now returns `string`)
- **Behavioral drift:** Status codes changed, error format changed, new required headers
- **Deprecation signals:** `Sunset` / `Deprecation` headers in responses
- **Latency drift:** Response time significantly different from SLA baseline

### Proposed Design

```
┌─────────────────────────────────────────────────────┐
│  Contract Test Runner                                │
│                                                      │
│  Input: Configuration + AdapterVersion               │
│                                                      │
│  1. Build request from config field_mappings         │
│     (use test/sandbox credentials from vault)        │
│  2. Hit real endpoint (sandbox URL from adapter)     │
│  3. Capture: status, headers, body, latency          │
│  4. Compare response body against response_schema    │
│     - JSON Schema validation (structural)            │
│     - Deep diff against last known good response     │
│  5. Generate ContractTestResult:                     │
│     - schema_valid: bool                             │
│     - drift_report: [{field, expected, actual}]      │
│     - deprecation_warnings: []                       │
│     - latency_ms vs sla_ms                           │
│  6. Store result, fire webhook: contract.drift       │
└─────────────────────────────────────────────────────┘
```

### Key Decisions Needed

- Do we require users to provide sandbox credentials per adapter, or do we maintain a shared sandbox key pool?
- How often do we run contract tests? On-demand only, or scheduled (cron)?
- Do we block config deployment if contract test fails, or just warn?

### Files Likely Affected

- New: `services/testing/contract_tester.py`
- New: `models/contract_test.py` (ContractTestResult, ContractTestRun)
- Modify: `services/simulation/simulator.py` (add `mode="live"` option)
- Modify: `api/routes/simulations.py` (new endpoint or param)
- Modify: `models/adapter.py` (add `sandbox_url` to AdapterVersion)

---

## Issue #2: Rethink Post-LLM Validation — Confidence-Driven Validation Strategy

**Priority:** Medium
**Tags:** `architecture`, `llm`

### Problem

Current flow: LLM generates config (with per-field confidence scores) → static validator runs the same checks regardless of confidence. The API expert's question is valid: **why run the same validation on a 0.99-confidence config as on a 0.4-confidence one?**

Today's validator (`validator.py`) does 7 static checks: required fields, auth type whitelist, HTTP method whitelist, retry bounds, timeout bounds, hook types, endpoint format. These are the same checks whether the LLM was highly confident or guessing.

### What the Expert Likely Means

The validation layer should be **confidence-aware**:

1. **High confidence (>0.85):** Light validation — structural checks only. Trust the LLM's output. Fast-track to `CONFIGURED` state.
2. **Medium confidence (0.5–0.85):** Full validation + flag uncertain mappings for human review. Show which specific fields the LLM was unsure about.
3. **Low confidence (<0.5):** Deep validation + LLM self-critique pass. Re-prompt Gemini: "Here's your generated config. Here are the fields with low confidence. Reconsider these mappings and explain your reasoning."
4. **Per-field granularity:** Don't score the whole config — highlight individual field mappings that need human attention.

### Proposed Changes

```python
# Current: binary pass/fail
validation_result = validator.validate(config)  # always same checks

# Proposed: tiered validation
validation_result = validator.validate(
    config,
    strategy=infer_strategy(config.avg_confidence),
    # "fast_track" | "standard" | "deep_review"
)
# Returns: {
#   "passed": bool,
#   "strategy_used": "standard",
#   "flags": [
#     {"field": "pan_number→pan", "confidence": 0.45, "reason": "ambiguous source field name", "action": "needs_human_review"},
#     {"field": "base_url", "confidence": 0.99, "action": "auto_approved"}
#   ]
# }
```

### Why This Matters

- Reduces friction for high-quality configs (don't make users click through obvious validations)
- Focuses human attention where it's actually needed (low-confidence mappings)
- Makes the LLM confidence score *actionable* instead of just decorative

### `[needs discussion]`

- Should low-confidence configs trigger a Gemini self-critique pass automatically, or is that too expensive?
- Do we want a "human-in-the-loop" approval step for medium-confidence configs, or just warnings?
- How does this interact with the config lifecycle state machine? Does a partially-approved config get a new state like `REVIEW_NEEDED`?

### Files Likely Affected

- Modify: `services/config_engine/validator.py` (add strategy param, tiered checks)
- Modify: `schemas/configurations.py` (add per-field review flags to response)
- Modify: `api/routes/configurations.py` (surface flags in generate response)
- Possibly modify: `services/llm/client.py` (add self-critique prompt)

---

## Issue #3: 3rd Party API Observability — Request/Response Logging & Version Tracking

**Priority:** High
**Tags:** `observability`, `core-feature`

### Problem

The current audit log (`AuditLog` model) tracks **user actions**: "user X created config Y", "user X ran simulation Z". It does NOT track:

- What a 3rd party API actually responded when we called it
- How the request was constructed (which field mappings were applied)
- Which adapter version was used for the call
- Latency, status codes, error responses from the external API
- How responses differ across adapter versions

This means we have zero visibility into the runtime behavior of integrations. If something breaks, there's no trail to debug from.

### What We Need

**API Call Ledger** — an immutable, append-only log of every outbound API call made through AdaptConfig.

### Proposed Schema

```
APICallLog:
  id:                UUID
  tenant_id:         UUID
  configuration_id:  UUID
  adapter_name:      str
  adapter_version:   str       # "v2.1" — critical for version comparison
  endpoint_path:     str       # "/v1/credit-pull"
  http_method:       str

  # Request
  request_headers:   JSON      # (secrets masked)
  request_body:      JSON      # (PII masked via existing PII masker)

  # Response
  response_status:   int
  response_headers:  JSON
  response_body:     JSON      # (PII masked)
  response_time_ms:  int

  # Analysis
  schema_match:      bool      # did response match expected schema?
  drift_fields:      JSON      # fields that differed from expected
  error_code:        str?      # if call failed
  error_message:     str?

  created_at:        datetime
```

### Version Comparison View

With this data, we can answer: "How does CIBIL v2 respond differently from CIBIL v1 for the same request?" Group by `adapter_version`, diff `response_body` structures.

### Privacy / Security Considerations

- PII masking MUST apply to logged request/response bodies (existing `_mask_pii()` from middleware)
- Auth headers (API keys, Bearer tokens) MUST be fully redacted
- Consider retention policy — how long do we keep raw API call logs?
- Tenant isolation — logs scoped by `tenant_id`, no cross-tenant access

### `[needs discussion]`

- Storage: These logs will be high-volume. SQLite/Postgres for now, but may need to consider a time-series store (ClickHouse, TimescaleDB) later?
- Do we log calls from contract tests (Issue #1) separately, or in the same ledger?
- Sampling: Log 100% of calls, or configurable sampling rate for high-throughput tenants?

### Files Likely Affected

- New: `models/api_call_log.py`
- New: `services/observability/call_logger.py`
- Modify: `core/middleware.py` (PII masking reuse)
- New: `api/routes/observability.py` (query logs, version comparison)
- Alembic migration for new table

---

## Issue #4: Custom Transformation Engine — Runtime Field Transformation

**Priority:** High
**Tags:** `core-feature`, `runtime`

### Problem

Field mappings currently declare transformations like `"upper"`, `"parse_number"`, `"normalize_phone"` but **they are never executed**. The transformation is metadata — stored in config JSON, shown in UI, but no code actually runs `upper()` on a PAN number at request time.

The expert wants System A → AdaptConfig → System B with real data transformation happening in the middle.

### What We Need

A **transformation engine** that takes a source payload, applies declared transformations, and produces the target payload.

### Proposed Transformation Pipeline

```
Source Payload (System A)          Transformed Payload (System B)
┌──────────────────────┐           ┌──────────────────────┐
│ {                    │           │ {                    │
│   "pan_number":      │  ──────► │   "pan": "ABCDE1234F"│
│     "abcde1234f",    │  upper   │                      │
│   "dob": "15/05/90", │  ──────► │   "date_of_birth":   │
│   "amount": "50000"  │  parse   │     "1990-05-15",    │
│ }                    │  number  │   "amount": 50000    │
└──────────────────────┘           └──────────────────────┘
```

### Transformation Types

**Built-in (type-safe, validated):**

| Transform | Input | Output | Example |
|-----------|-------|--------|---------|
| `upper` | str | str | `"abc"` → `"ABC"` |
| `lower` | str | str | `"ABC"` → `"abc"` |
| `parse_number` | str | int/float | `"50000"` → `50000` |
| `to_string` | any | str | `50000` → `"50000"` |
| `parse_date` | str | ISO date | `"15/05/90"` → `"1990-05-15"` |
| `format_date` | ISO date | str | configurable format pattern |
| `normalize_phone` | str | E.164 | `"9876543210"` → `"+919876543210"` |
| `validate_email` | str | str | validates format, passes through |
| `parse_boolean` | str | bool | `"true"` / `"1"` / `"yes"` → `true` |
| `mask_aadhaar` | str | str | `"123456789012"` → `"XXXX-XXXX-9012"` |
| `paise_to_rupees` | int | float | `5000000` → `50000.00` |
| `rupees_to_paise` | float | int | `50000.00` → `5000000` |

**Custom (user-defined, sandboxed):**

```json
{
  "source_field": "full_address",
  "target_field": "address_line_1",
  "transformation": "custom",
  "custom_expression": "value.split(',')[0].strip()"
}
```

Custom expressions should be sandboxed (no imports, no file access, no network). Consider using a restricted Python eval or a simple expression DSL.

### `[needs discussion]`

- How complex can custom transformations get? Simple expressions only, or do we need a full scripting layer (Lua, JSONata, Jinja2)?
- Should transformations be testable in isolation before deploying a config? (Transformation playground?)
- How do we handle transformation errors at runtime? Fail the whole request, skip the field, use a default?
- Should we support chained transformations? (`upper` then `trim` then `validate_pattern`)

### Files Likely Affected

- New: `services/transformation/engine.py` (core transformer)
- New: `services/transformation/builtins.py` (built-in transforms)
- New: `services/transformation/sandbox.py` (custom expression eval)
- Modify: `schemas/configurations.py` (enrich transformation schema)
- Modify: `services/config_engine/field_mapper.py` (generate richer transformation rules)

---

## ~~Issue #5~~ — PROMOTED TO ISSUE #0

The workflow orchestration engine (arbitrary graphs, cycles, async/sync, event-driven) has been promoted to **Issue #0** at the top of this document as the highest-priority feature. All content is there. This section is kept as a redirect.

---

## Issue #6: Runtime API Proxy — AdaptConfig as Integration Middleware

**Priority:** Critical
**Tags:** `architecture`, `core-feature`, `paradigm-shift`

### Problem

This is the biggest conceptual gap. Today, AdaptConfig is a **config generator** — it produces JSON configs that describe how an integration *should* work. But it never actually *executes* those configs. The expert wants AdaptConfig to sit in the middle:

```
Currently:
  System A ──────────────────────────────► System B (3rd party API)
  AdaptConfig just generates a JSON config that System A reads

Proposed:
  System A ──► AdaptConfig Proxy ──► System B (3rd party API)
                    │
                    ├── applies field mappings
                    ├── executes transformations
                    ├── handles auth (injects API keys, rotates tokens)
                    ├── retries on failure
                    ├── logs everything (Issue #3)
                    └── validates response schema (Issue #1)
```

### What This Means

AdaptConfig becomes an **API Gateway / Integration Middleware** — not just a tool that generates configs, but one that **uses** them to route, transform, and monitor real API traffic.

### Proposed Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  AdaptConfig Runtime Proxy                                      │
│                                                                  │
│  POST /api/v1/proxy/{configuration_id}/{endpoint_path}          │
│                                                                  │
│  1. Load Configuration by ID                                     │
│  2. Resolve endpoint from config's endpoints[]                  │
│  3. Transform request body (Issue #4 engine)                    │
│  4. Inject auth (read encrypted creds from vault)               │
│  5. Forward to 3rd party API (base_url + endpoint_path)         │
│  6. Validate response against expected schema                   │
│  7. Transform response back (reverse mappings if needed)        │
│  8. Log full request/response cycle (Issue #3)                  │
│  9. Return to caller                                            │
│                                                                  │
│  + Retry logic (from config retry_policy)                       │
│  + Timeout enforcement (from config timeout_ms)                 │
│  + Circuit breaker (if consecutive failures > threshold)        │
└─────────────────────────────────────────────────────────────────┘
```

### Example Flow

```
System A (LendFlow) calls:
  POST /api/v1/proxy/config-uuid-123/credit-pull
  Body: { "pan_number": "ABCDE1234F", "applicant_name": "Rahul Sharma" }

AdaptConfig Proxy:
  1. Loads config → adapter: CIBIL Bureau v2, base_url: https://api.crif.in/v2
  2. Resolves endpoint → POST /credit-pull
  3. Transforms: { "pan_number" → "pan" (upper), "applicant_name" → "full_name" }
  4. Injects: Authorization: Bearer <token from vault>
  5. Forwards: POST https://api.crif.in/v2/credit-pull { "pan": "ABCDE1234F", "full_name": "Rahul Sharma" }
  6. Receives: { "credit_score": 745, "enquiry_id": "ENQ-2026-001" }
  7. Validates: response matches expected schema ✓
  8. Logs: full cycle to APICallLog
  9. Returns to System A: { "credit_score": 745, "enquiry_id": "ENQ-2026-001" }
```

### Key Design Decisions `[needs discussion]`

- **Latency budget:** Adding a proxy hop adds latency. Is this acceptable for real-time payment flows?
- **Scaling:** Proxy handles all API traffic. Need to think about horizontal scaling, connection pooling.
- **Failure mode:** If AdaptConfig proxy is down, all integrations are down. Need circuit breaker, health checks, maybe a "passthrough" mode.
- **SDK vs Proxy:** Alternative to HTTP proxy — ship a lightweight SDK that systems embed. SDK reads configs from AdaptConfig API and applies transformations locally. Lower latency, but harder to update.
- **Config hot-reload:** When a config is updated, does the proxy pick it up immediately, or does it need a deployment cycle?

### Files Likely Affected

- New: `services/proxy/router.py` (core proxy logic)
- New: `services/proxy/request_builder.py` (apply field mappings to request)
- New: `services/proxy/response_handler.py` (validate + optionally reverse-map response)
- New: `services/proxy/auth_injector.py` (resolve auth from encrypted vault)
- New: `services/proxy/circuit_breaker.py`
- New: `api/routes/proxy.py` (the proxy endpoint)
- Modify: `services/transformation/engine.py` (Issue #4 — used here)
- Modify: `services/observability/call_logger.py` (Issue #3 — used here)

---

## Issue #7: 3rd Party API Audit Trail

**Priority:** High
**Tags:** `compliance`, `observability`

### Problem

Current `AuditLog` records user actions: "created config", "ran simulation", "rolled back". It does NOT record:

- "Called CIBIL API at 14:32:01, got 200 in 1.2s"
- "Payment to RazorPay failed with 502, retried 3 times, succeeded on attempt 3"
- "eKYC response schema drifted — field `verified` changed from bool to string"

For fintech compliance (RBI guidelines, PCI-DSS), we need an **immutable audit trail of every interaction with external APIs**.

### Difference from Issue #3

Issue #3 (Observability) is about **operational logging** — debugging, monitoring, dashboards.
Issue #7 (Audit Trail) is about **compliance logging** — tamper-proof, legally admissible, with retention policies.

### What We Need

```
3rd Party API Audit Record:
  - WHO:    which tenant, which user triggered it, which config
  - WHAT:   which adapter, version, endpoint was called
  - WHEN:   timestamp (server + external API response timestamp)
  - INPUT:  request sent (PII masked)
  - OUTPUT: response received (PII masked)
  - RESULT: success/failure, status code, latency
  - WHY:    what triggered the call (user action, workflow step, scheduled test)
  - CHAIN:  if part of a workflow (Issue #5), link to workflow_run_id and step_id
```

### Compliance Requirements

- **Immutability:** Append-only. No updates, no deletes. Consider cryptographic chaining (hash of previous record in current record) for tamper detection.
- **Retention:** Configurable per tenant. Fintech default: 7 years (RBI).
- **Access control:** Only compliance role can export full audit trail. Regular users see summary only.
- **Export:** CSV/JSON export for regulatory submissions.

### Files Likely Affected

- Modify: `models/audit.py` (add 3rd-party-specific fields, or new model `ExternalAPIAudit`)
- New: `services/audit/external_audit.py`
- Modify: `api/routes/audit.py` (new query filters: by adapter, by status, by date range)

---

## ~~Issue #8~~ — MERGED INTO ISSUE #5

Async/sync execution modes, cyclic graphs, event-driven workflows, and polling patterns are all covered comprehensively in Issue #5 (General-Purpose Workflow Orchestration Engine). Issue #8 no longer exists as a separate item.

---

## Summary & Priority Matrix

| # | Issue | Priority | Depends On | Effort |
|---|-------|----------|------------|--------|
| **0** | **Workflow Orchestration Engine (arbitrary graphs, cycles, async/sync, event-driven)** | **Critical** | #4, #6 | **XXL — this IS the product** |
| 6 | **Runtime API Proxy (Middleware)** | Critical | #4, #3 | XL — architectural shift |
| 1 | **Live Contract Testing** | High | #6 (partial) | L |
| 3 | **3rd Party API Observability** | High | — | M |
| 4 | **Custom Transformation Engine** | High | — | M |
| 7 | **3rd Party API Audit Trail** | High | #3 | M |
| 2 | **Confidence-Driven Validation** | Medium | — | S |
| ~~5~~ | ~~Workflow Engine~~ | — | — | Promoted to #0 |
| ~~8~~ | ~~Async/Sync Execution~~ | — | — | Merged into #0 |

### Suggested Implementation Order

```
Phase 1 (Foundation):    #4 Transformation Engine  +  #3 Observability
                         (zero dependencies, can start now)

Phase 2 (Runtime):       #6 API Proxy (uses #4 + #3)
                         (the architectural shift — single API calls through AdaptConfig)

Phase 3 (Intelligence):  #2 Confidence Validation  +  #1 Contract Testing
                         (uses #6 to hit real APIs)

Phase 4 (Orchestration): #0 Workflow Engine  +  #7 Compliance Audit
                         (the product — multi-API workflows with cycles,
                          async/sync, event-driven, saga compensation)
```

Phase 1 has zero dependencies and can start immediately. Phase 2 is the paradigm shift — single API calls routed through AdaptConfig. Phase 4 is the **real product** — this is where AdaptConfig becomes a workflow orchestration platform, not just a config tool.

### The Big Picture

```
Today:     Config Generator (generate JSON, hope it works)
Phase 2:   Integration Middleware (route single API calls, transform, log)
Phase 4:   Workflow Orchestration Platform (arbitrary multi-API workflows
           with cycles, async/sync, events, compensation, and full observability)
```
