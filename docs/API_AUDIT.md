# AdaptConfig API audit (issue #116)

This audit maps **every interactive feature** in `frontend/src/pages/*.tsx` to a
documented backend endpoint and confirms that the *Universal API* surface
described in `adaptconfig.skill.md` is sufficient to drive the whole product
through curl, the MCP server, or a Claude Skill — i.e. *without* the React UI.

The mapping is enforced by `tests/integration/test_skill_api_surface.py::test_every_ui_feature_has_a_route`,
which fails the build if any feature loses its backing route.

## Conventions

- All API routes live under `/api/v1/...` and require the `X-Tenant-ID` header
  in debug mode or a Bearer JWT in production.
- The composite endpoint `POST /api/v1/configurations/{id}/validate-and-test`
  is **new in this issue** and was previously orchestrated client-side as
  `transitionMutation` in `Configurations.tsx`.
- Routes flagged **Composite** mean a single HTTP call performs work that
  used to require multiple round trips from the UI.

## Page → endpoint matrix

### Dashboard (`frontend/src/pages/Dashboard.tsx`)

| UI element                          | HTTP method & path                          | Status        |
|-------------------------------------|---------------------------------------------|---------------|
| Top-line counter cards (adapters)   | `GET  /api/v1/adapters/`                    | OK (existing) |
| Top-line counter cards (documents)  | `GET  /api/v1/documents/`                   | OK (existing) |
| Top-line counter cards (configs)    | `GET  /api/v1/configurations/`              | OK (existing) |
| Throughput + weekly-activity charts | `GET  /api/v1/analytics/dashboard`          | OK (existing) |
| Config status donut + health score  | `GET  /api/v1/configurations/summary`       | OK (existing) |

### Documents (`frontend/src/pages/Documents.tsx`)

| UI element                  | HTTP method & path                            | Status        |
|-----------------------------|-----------------------------------------------|---------------|
| Document list / table       | `GET    /api/v1/documents/`                   | OK (existing) |
| Document detail drawer      | `GET    /api/v1/documents/{document_id}`      | OK (existing) |
| Drag-and-drop upload        | `POST   /api/v1/documents/upload`             | OK (existing) |
| Delete document             | `DELETE /api/v1/documents/{document_id}`      | OK (existing) |

### Adapters (`frontend/src/pages/Adapters.tsx`)

| UI element                          | HTTP method & path                                                          | Status        |
|-------------------------------------|-----------------------------------------------------------------------------|---------------|
| Adapter cards / catalog             | `GET /api/v1/adapters/`                                                     | OK (existing) |
| Adapter detail (modal expansion)    | `GET /api/v1/adapters/{adapter_id}`                                         | OK (existing) |
| Deprecation banner per version      | `GET /api/v1/adapters/{adapter_id}/versions/{version}/deprecation`          | OK (existing) |
| Create adapter from a document      | `POST /api/v1/adapters/from-document` (used from Configurations page form)  | OK (existing) |

### Configurations (`frontend/src/pages/Configurations.tsx`)

| UI element                                       | HTTP method & path                                                            | Status                            |
|--------------------------------------------------|-------------------------------------------------------------------------------|-----------------------------------|
| Configurations list                              | `GET    /api/v1/configurations/`                                              | OK (existing)                     |
| Config detail expansion                          | `GET    /api/v1/configurations/{config_id}`                                   | OK (existing)                     |
| Generate config form                             | `POST   /api/v1/configurations/generate`                                      | OK (existing)                     |
| "Run Validation" button                          | `POST   /api/v1/configurations/{config_id}/validate`                          | OK (existing)                     |
| Lifecycle transition buttons (single transition) | `POST   /api/v1/configurations/{config_id}/transition`                        | OK (existing)                     |
| "Start Testing" (validate + smoke pipeline)      | `POST   /api/v1/configurations/{config_id}/validate-and-test`                 | **NEW (composite, this issue)**   |
| Rollback button                                  | `POST   /api/v1/configurations/{config_id}/rollback`                          | OK (existing)                     |
| Inline field-mapping edits                       | `PATCH  /api/v1/configurations/{config_id}`                                   | OK (existing)                     |
| Delete config                                    | `DELETE /api/v1/configurations/{config_id}`                                   | OK (existing)                     |
| History panel                                    | `GET    /api/v1/configurations/{config_id}/history`                           | OK (existing)                     |
| Compare two versions                             | `GET    /api/v1/configurations/{config_id}/history/compare`                   | OK (existing)                     |
| Export JSON/YAML                                 | `GET    /api/v1/configurations/{config_id}/export`                            | OK (existing)                     |
| Compare two configs (diff modal)                 | `GET    /api/v1/configurations/{config_a_id}/diff/{config_b_id}`              | OK (existing)                     |
| Suggested templates                              | `GET    /api/v1/configurations/templates`                                     | OK (existing)                     |
| Batch validate (toolbar)                         | `POST   /api/v1/configurations/batch-validate`                                | OK (existing)                     |
| Batch simulate (toolbar)                         | `POST   /api/v1/configurations/batch-simulate`                                | OK (existing)                     |
| Security inspection (config-level)               | `POST   /api/v1/security/inspect-config/{config_id}`                          | OK (existing)                     |

### Simulations (`frontend/src/pages/Simulations.tsx`)

| UI element                  | HTTP method & path                          | Status        |
|-----------------------------|---------------------------------------------|---------------|
| Simulation list             | `GET    /api/v1/simulations/`               | OK (existing) |
| Run a simulation            | `POST   /api/v1/simulations/run`            | OK (existing) |
| Simulation detail           | `GET    /api/v1/simulations/{simulation_id}`| OK (existing) |
| Live step stream (SSE)      | `GET    /api/v1/simulations/{simulation_id}/stream` | OK (existing) |
| Delete simulation           | `DELETE /api/v1/simulations/{simulation_id}`| OK (existing) |
| Config dropdown source      | `GET    /api/v1/configurations/`            | OK (existing) |

### Webhooks (`frontend/src/pages/Webhooks.tsx`)

| UI element            | HTTP method & path                              | Status        |
|-----------------------|-------------------------------------------------|---------------|
| Webhook list          | `GET    /api/v1/webhooks/`                      | OK (existing) |
| Register webhook form | `POST   /api/v1/webhooks/`                      | OK (existing) |
| Delete webhook        | `DELETE /api/v1/webhooks/{webhook_id}`          | OK (existing) |
| Fire test event       | `POST   /api/v1/webhooks/{webhook_id}/test`     | OK (existing) |

### Search (`frontend/src/pages/Search.tsx`)

| UI element  | HTTP method & path        | Status        |
|-------------|---------------------------|---------------|
| Global search | `GET /api/v1/search/?q=` | OK (existing) |

### Audit (`frontend/src/pages/Audit.tsx`)

| UI element             | HTTP method & path                    | Status        |
|------------------------|---------------------------------------|---------------|
| Audit log (filtered)   | `GET /api/v1/audit/`                  | OK (existing) |

### Auth (`frontend/src/pages/Login.tsx`, `frontend/src/pages/Register.tsx`)

| UI element        | HTTP method & path                  | Status        |
|-------------------|-------------------------------------|---------------|
| Login form        | `POST /api/v1/auth/login`           | OK (existing) |
| Register form     | `POST /api/v1/auth/register`        | OK (existing) |
| Silent refresh    | `POST /api/v1/auth/refresh`         | OK (existing) |
| Boot-time `/me`   | `GET  /api/v1/auth/me`              | OK (existing) |

## Gap analysis

Before this issue:

- The "Start Testing" button in `Configurations.tsx` did the work of *two*
  endpoints back-to-back from React: `POST /transition` followed by
  `POST /simulations/run` in the same `transitionMutation` handler. A
  third-party caller (the Claude Skill, the MCP server, curl scripts) had
  to replicate that orchestration. **This was the only multi-call sequence
  the SPA used that the API did not expose as a single endpoint.**

After this issue:

- `POST /api/v1/configurations/{id}/validate-and-test` performs the full
  `transition -> validate -> transition -> smoke` pipeline atomically and
  returns a composite response. The React UI's `transitionMutation` now
  delegates to this endpoint when the target state is `testing`, removing
  the only piece of multi-step business logic that lived in the front-end.
- The Skill file at the repo root documents this single call as the
  canonical way to validate and smoke-test a configuration.

No other gaps were found. The Universal API now mirrors the UI 1:1.
