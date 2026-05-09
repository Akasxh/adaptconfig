# AdaptConfig — Parallel Feature Implementation Plan

## Strategy Overview

```
                          main (LIVE — always deployable)
                            │
         ┌──────────────────┼──────────────────┐
         │                  │                  │
    feature/transform  feature/observability   │
    (worktree-1)       (worktree-2)            │
         │                  │                  │
         └──────┬───────────┘                  │
                │                              │
           feature/proxy                       │
           (worktree-3)                        │
                │                              │
         ┌──────┼──────────────┐               │
         │      │              │               │
    feature/    feature/       feature/         │
    contract    confidence     audit-trail      │
    (wt-4)      (wt-5)        (wt-6)           │
         │      │              │               │
         └──────┴──────┬───────┘               │
                       │                       │
                  feature/workflow-engine       │
                  (worktree-7)                  │
                       │                       │
                  integration/all-features ─────┘
                  (final merge to main)
```

## Ground Rules

### 1. Main is ALWAYS live
- `main` = production. Railway deploys from `main`.
- No direct commits to `main`. Everything via PR.
- Every PR must pass: `pytest` (899+ tests), `ruff check`, and manual review.

### 2. Git Worktrees for Parallel Development
Each feature gets its own worktree — an independent working directory on its own branch. Developers can work on multiple features simultaneously without `git stash` or branch switching.

### 3. Feature Branches Follow a Dependency Chain
Features merge in phase order. A later-phase feature rebases onto its dependencies before merging.

### 4. Continuous Integration Gate
Every feature branch runs the FULL existing test suite + its own new tests. A feature that breaks existing tests cannot merge.

---

## Phase 1: Foundation (No Dependencies — Start Immediately)

### Feature A: Custom Transformation Engine
- **Branch:** `feature/transformation-engine`
- **Worktree:** `../adaptconfig-wt-transform`
- **GitHub Issue:** [#113](https://github.com/Akasxh/adaptconfig/issues/113)
- **Assignees:** Swayam + Akash

**What to build:**
```
src/finspark/services/transformation/
├── engine.py          # TransformationEngine.transform(payload, mappings) → payload
├── builtins.py        # 12 built-in transforms (upper, parse_number, normalize_phone, etc.)
├── sandbox.py         # Safe eval for custom expressions
└── __init__.py

tests/test_transformation/
├── test_engine.py
├── test_builtins.py
└── test_sandbox.py
```

**Acceptance criteria:**
- [ ] `TransformationEngine.transform(source_payload, field_mappings) → target_payload` works
- [ ] All 12 built-in transforms have unit tests
- [ ] Custom expression sandbox blocks `import`, `exec`, `eval`, file I/O
- [ ] Chained transforms work: `upper` → `trim` → `validate_pattern`
- [ ] Error handling: bad transform returns error per-field, doesn't crash the whole payload
- [ ] All 899 existing tests still pass

**Integration surface:** Zero — this is a pure library with no routes or models. Safest to build first.

---

### Feature B: 3rd Party API Observability
- **Branch:** `feature/api-observability`
- **Worktree:** `../adaptconfig-wt-observability`
- **GitHub Issue:** [#112](https://github.com/Akasxh/adaptconfig/issues/112)
- **Assignees:** Swayam + Akash

**What to build:**
```
src/finspark/models/api_call_log.py          # APICallLog SQLAlchemy model
src/finspark/services/observability/
├── call_logger.py                           # log_api_call() — async, PII-masked
└── __init__.py
src/finspark/api/routes/observability.py     # GET /api/v1/observability/calls
alembic/versions/xxxx_add_api_call_log.py    # Migration

tests/test_observability/
├── test_call_logger.py
└── test_observability_routes.py
```

**Acceptance criteria:**
- [ ] `APICallLog` model with all fields (see Issue #112)
- [ ] `log_api_call()` masks PII (reuse existing `_mask_pii`) and redacts auth headers
- [ ] Query endpoint with filters: adapter, version, status, date range
- [ ] Version comparison view: diff responses across adapter versions
- [ ] Alembic migration runs cleanly on fresh DB and on existing DB
- [ ] All 899 existing tests still pass

**Integration surface:** New model + new route. Does NOT modify existing routes. Low risk.

---

### How Phase 1 Runs in Parallel

```bash
# Set up worktrees (one-time)
cd /home/akash/PROJECTS/finspark
git worktree add ../adaptconfig-wt-transform feature/transformation-engine
git worktree add ../adaptconfig-wt-observability feature/api-observability

# Developer 1 works in ../adaptconfig-wt-transform
# Developer 2 works in ../adaptconfig-wt-observability
# Main repo stays on main — live app unaffected

# Each developer runs tests in their worktree:
cd ../adaptconfig-wt-transform && pytest
cd ../adaptconfig-wt-observability && pytest
```

**No file conflicts possible** — Feature A touches `services/transformation/` (new directory), Feature B touches `models/api_call_log.py` + `services/observability/` (new directory). Zero overlap.

---

## Phase 2: Runtime (Depends on Phase 1)

### Feature C: Runtime API Proxy
- **Branch:** `feature/runtime-proxy`
- **Worktree:** `../adaptconfig-wt-proxy`
- **GitHub Issue:** [#114](https://github.com/Akasxh/adaptconfig/issues/114)
- **Prerequisite:** Merge Phase 1 (transformation engine + observability) into `main` first.

**What to build:**
```
src/finspark/services/proxy/
├── router.py              # Core proxy: load config → transform → forward → validate → log
├── request_builder.py     # Apply field mappings to outgoing request
├── response_handler.py    # Validate response against schema, reverse-map if needed
├── auth_injector.py       # Inject auth from encrypted vault
├── circuit_breaker.py     # Track failures, open circuit after threshold
└── __init__.py
src/finspark/api/routes/proxy.py   # POST /api/v1/proxy/{config_id}/{endpoint}

tests/test_proxy/
├── test_router.py
├── test_request_builder.py
├── test_response_handler.py
├── test_auth_injector.py
└── test_circuit_breaker.py
```

**Acceptance criteria:**
- [ ] `POST /api/v1/proxy/{config_id}/{endpoint}` routes through the full pipeline
- [ ] Transformation engine (Phase 1A) applied to request body
- [ ] Auth injected from encrypted config
- [ ] Response validated against adapter schema
- [ ] Full cycle logged to APICallLog (Phase 1B)
- [ ] Retry logic from config `retry_policy`
- [ ] Circuit breaker opens after N consecutive failures
- [ ] All existing tests + Phase 1 tests pass

**Sequence to merge:**
1. Merge Phase 1A (`feature/transformation-engine`) → `main`
2. Merge Phase 1B (`feature/api-observability`) → `main`
3. Create `feature/runtime-proxy` from updated `main`
4. Build Phase 2 using Phase 1 as foundation

---

## Phase 3: Intelligence (Depends on Phase 2)

### Feature D: Live Contract Testing
- **Branch:** `feature/contract-testing`
- **GitHub Issue:** [#110](https://github.com/Akasxh/adaptconfig/issues/110)
- **Prerequisite:** Runtime proxy merged.

### Feature E: Confidence-Driven Validation
- **Branch:** `feature/confidence-validation`
- **GitHub Issue:** [#111](https://github.com/Akasxh/adaptconfig/issues/111)
- **Prerequisite:** None (can start in Phase 1, but lower priority)

**Phase 3 can run in parallel** — D and E have no overlap:
- D modifies `simulation/` + new `testing/contract_tester.py`
- E modifies `config_engine/validator.py` + `schemas/configurations.py`

---

## Phase 4: Orchestration (Depends on Phase 2)

### Feature F: Workflow Engine
- **Branch:** `feature/workflow-engine`
- **GitHub Issue:** [#109](https://github.com/Akasxh/adaptconfig/issues/109)
- **Prerequisite:** Runtime proxy + transformation engine merged.
- **This is the largest feature.** Consider splitting into sub-PRs:
  - PR 4a: Models + schema validation + graph validator (Tarjan)
  - PR 4b: Engine core + node executor + fuel tracker
  - PR 4c: Parallel executor + event router
  - PR 4d: API routes + workflow run state machine
  - PR 4e: Saga compensation

### Feature G: 3rd Party Audit Trail
- **Branch:** `feature/audit-trail`
- **GitHub Issue:** [#115](https://github.com/Akasxh/adaptconfig/issues/115)
- **Can run in parallel with F.** Touches `models/audit.py` + new `services/audit/external_audit.py`.

---

## Review & QA Process

### Continuous Review Team

Every PR goes through a 3-gate review process before merge:

```
Developer writes code
        │
        ▼
  ┌─────────────┐
  │  Gate 1:     │  Automated
  │  CI Pipeline │  - pytest (all tests)
  │              │  - ruff check (linting)
  │              │  - ruff format --check
  └──────┬──────┘
         │ PASS
         ▼
  ┌─────────────┐
  │  Gate 2:     │  Peer Review
  │  Code Review │  - At least 1 reviewer (Swayam or Akash)
  │              │  - Check: does it match the issue spec?
  │              │  - Check: no regressions in existing features
  │              │  - Check: new tests cover the feature
  └──────┬──────┘
         │ APPROVED
         ▼
  ┌─────────────┐
  │  Gate 3:     │  Integration Test
  │  Live Test   │  - Start dev server from the PR branch
  │              │  - Manually test the new feature E2E
  │              │  - Test existing features (documents, configs,
  │              │    simulations, webhooks) still work
  │              │  - Frontend still loads and functions
  └──────┬──────┘
         │ PASS
         ▼
    Merge to main
```

### What Reviewers Check

| Check | Why |
|-------|-----|
| All existing 899 tests pass | No regressions |
| New feature has >80% test coverage | Quality bar |
| No new `# type: ignore` or `noqa` | Maintain strictness |
| Alembic migration is reversible (`downgrade`) | Safe rollback |
| PII masking on any new logging | Compliance |
| New routes have auth + tenant scoping | Security |
| No hardcoded secrets or test data | Security |
| API response schemas documented | Maintainability |

---

## CI/CD Pipeline (To Be Created)

Currently there's no CI. We need to create `.github/workflows/ci.yml`:

```yaml
name: CI
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install uv && uv pip install -e ".[dev]" --system
      - run: ruff check src/ tests/
      - run: ruff format --check src/ tests/
      - run: pytest --tb=short -q

  frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
      - run: npm ci
      - run: npm run build
      - run: npm run test -- --run
```

---

## Merge Strategy: How Features Combine

### The Integration Branch Pattern

After all features in a phase merge to `main`, we do an **integration smoke test** before starting the next phase:

```
Phase 1A merges → main
Phase 1B merges → main
        │
        ▼
  Integration Test: start app, test documents + configs +
  simulations + webhooks + NEW transformation + NEW observability
        │
        ▼ PASS
Phase 2 starts from updated main
```

### Handling Merge Conflicts

| Conflict Area | Resolution |
|--------------|------------|
| `main.py` (route registration) | Each feature adds ONE line. Merge both. |
| `alembic/env.py` | Import both new models. |
| `alembic/versions/` | Multiple migrations — run `alembic merge heads` to create merge migration. |
| `models/__init__.py` | Each feature adds its export. Merge both. |

### Rollback Plan

If a feature merge breaks production:
1. `git revert <merge-commit>` on `main` — instant rollback
2. Fix the issue on the feature branch
3. Re-merge

Because each feature is its own merge commit, reverting is surgical — you undo one feature without touching others.

---

## Worktree Setup Commands

```bash
# Phase 1 — run now
cd /home/akash/PROJECTS/finspark

git checkout -b feature/transformation-engine main
git push -u origin feature/transformation-engine
git checkout main

git checkout -b feature/api-observability main
git push -u origin feature/api-observability
git checkout main

git worktree add ../adaptconfig-wt-transform feature/transformation-engine
git worktree add ../adaptconfig-wt-observability feature/api-observability

# Verify
git worktree list
# /home/akash/PROJECTS/finspark                     main
# /home/akash/PROJECTS/adaptconfig-wt-transform     feature/transformation-engine
# /home/akash/PROJECTS/adaptconfig-wt-observability  feature/api-observability
```

```bash
# Phase 2 — after Phase 1 merges
git checkout -b feature/runtime-proxy main
git push -u origin feature/runtime-proxy
git worktree add ../adaptconfig-wt-proxy feature/runtime-proxy
```

```bash
# Phase 3 — after Phase 2 merges
git checkout -b feature/contract-testing main
git checkout -b feature/confidence-validation main
git worktree add ../adaptconfig-wt-contract feature/contract-testing
git worktree add ../adaptconfig-wt-confidence feature/confidence-validation
```

```bash
# Phase 4 — after Phase 2 merges
git checkout -b feature/workflow-engine main
git checkout -b feature/audit-trail main
git worktree add ../adaptconfig-wt-workflow feature/workflow-engine
git worktree add ../adaptconfig-wt-audit feature/audit-trail
```

---

## Timeline Estimate

| Phase | Features | Parallel? | Est. Duration | Cumulative |
|-------|----------|-----------|---------------|------------|
| 1 | Transformation + Observability | Yes (2 worktrees) | 1-2 weeks | Week 2 |
| 2 | Runtime Proxy | Solo (depends on P1) | 2-3 weeks | Week 5 |
| 3 | Contract Testing + Confidence Validation | Yes (2 worktrees) | 1-2 weeks | Week 7 |
| 4 | Workflow Engine + Audit Trail | Yes (2 worktrees) | 3-5 weeks | Week 12 |

**Total: ~10-12 weeks** with 2 developers working in parallel.

### Accelerators
- Phase 3E (Confidence Validation) has no dependencies — can start during Phase 1
- Phase 4G (Audit Trail) is small — can start during Phase 2
- Workflow Engine (Phase 4F) can be split into 5 sub-PRs for parallel review

---

## File Ownership Map (Prevents Conflicts)

| Directory/File | Owner Feature | Phase |
|---|---|---|
| `services/transformation/` | Transformation Engine | 1 |
| `models/api_call_log.py` | Observability | 1 |
| `services/observability/` | Observability | 1 |
| `api/routes/observability.py` | Observability | 1 |
| `services/proxy/` | Runtime Proxy | 2 |
| `api/routes/proxy.py` | Runtime Proxy | 2 |
| `services/testing/` | Contract Testing | 3 |
| `services/config_engine/validator.py` | Confidence Validation | 3 |
| `services/orchestration/` | Workflow Engine | 4 |
| `models/workflow.py` | Workflow Engine | 4 |
| `api/routes/workflows.py` | Workflow Engine | 4 |
| `services/audit/external_audit.py` | Audit Trail | 4 |

**Zero overlap between parallel features in the same phase.** This is by design.

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Alembic migration conflicts | Use `alembic merge heads` — well-supported pattern |
| Feature breaks existing tests | Gate 1 CI catches this before merge |
| Feature works alone but breaks with others | Integration smoke test after each phase merge |
| Main goes down during merge | Revert the merge commit — surgical, instant |
| Worktree gets stale (behind main) | Rebase feature branch onto main weekly |
| Workflow Engine is too large | Split into 5 sub-PRs with independent reviewability |
| Railway deploy fails | Keep previous deploy; Railway supports instant rollback |

---

## Summary

```
1. Work in git worktrees (parallel, isolated, no conflicts)
2. Every PR goes through 3 gates (CI → review → live test)
3. Merge in phase order (foundation first, capstone last)
4. Integration smoke test after each phase
5. Main is always deployable — if anything breaks, revert the merge commit
6. File ownership prevents conflicts between parallel features
7. Workflow Engine (the big one) splits into 5 sub-PRs
```
