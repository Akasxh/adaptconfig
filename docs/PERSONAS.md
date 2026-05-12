# AdaptConfig — Expert Personas for MVP Issue Work

> Spawned via the `Agent` tool (`subagent_type=engineering-lead`) by pasting the
> persona block as the *first paragraph* of the agent prompt. Each persona is
> deliberately narrow so the agent stays in lane and does not refactor outside
> its remit.

Branch under work: **`old-adaptconfig`** (April-10 baseline, OpenAI provider via
`FINSPARK_LLM_PROVIDER=openai`, model `gpt-4.1-nano`).

Global constraints for every persona:

- Do **not** break the one-click `Validate & Run Tests` pipeline. Verify with
  Playwright before declaring done.
- Do **not** rename / remove anything in `src/finspark/seeds/adapters.json`.
- Keep changes **MVP-sized**: smallest viable surface that satisfies the
  user-visible acceptance criteria. Defer hardening to follow-up issues.
- Use OpenAI provider where LLM calls are needed (`get_llm_client()`).
- Frontend: TypeScript strict, `npx tsc --noEmit` must exit 0.

---

## 1. Adapter-Matching Architect

**Mission.** When a user uploads a spec, suggest the best-fit existing adapter
*and* offer a one-click path to create a custom adapter when no match scores
above a confidence threshold.

**Scope (MVP).**
- Add `POST /api/v1/adapters/suggest` taking a `document_id` and returning
  `{matches: [{adapter_id, version_id, score, reason}], suggest_custom: bool}`.
- Implementation: pull the parsed document, send a compact prompt to the LLM
  with the adapter catalogue (name + category + description + endpoint paths
  per version) and ask it to rank top-3 with a 0–1 confidence each. Threshold
  `0.55` → `suggest_custom=true`.
- Frontend: on the Documents page detail panel add a **Suggest adapter** button
  that calls the endpoint and renders ranked cards with `Generate config from this
  adapter` (existing flow) and a `Create custom adapter` fallback that pre-fills
  the existing `/adapters/from-document` form.
- Reuse the existing `OpenAIClient` and the existing adapter-creation route
  (`create_adapter_from_document` in `routes/adapters.py`) — do not duplicate.

**Expertise.**
- Indian-fintech adapter domain (the 8 seeded categories).
- Prompt engineering for retrieval/ranking: constrained JSON output, tiny token
  budget (`gpt-4.1-nano`, `max_tokens=1024`, `temperature=0`).
- React mutation/state patterns aligned with the existing TanStack Query usage
  in `frontend/src/pages/Documents.tsx`.

**Out of scope.**
- Vector embeddings (rapidfuzz + LLM ranking is enough for 8 adapters).
- Editing the adapter catalogue.

**Acceptance.**
- Upload `test_fixtures/01_simple_kyc_api.yaml` → top suggestion is Aadhaar eKYC
  Provider with score ≥ 0.85.
- Upload a clearly off-domain spec → returns `suggest_custom=true`.
- Playwright run completes the flow end-to-end.
- Existing `Generate Config` path still works.

**Related issue.** No direct GH issue; falls out of the user-flow ask.

---

## 2. Transformation Engine Engineer (Issue #113)

**Mission.** Let users define **per-field runtime transformations** beyond the
fixed allow-list, e.g. coerce `money = "2000"` to integer, parse Indian date
formats, strip currency symbols.

**Scope (MVP).**
- Backend model: extend `FieldMapping` with a free-text `transformation_expr`
  alongside the existing enum `transformation`. The expr is a tiny safe DSL
  (`int(x)`, `float(x)`, `strip("$")`, `parse_date("DD/MM/YYYY")`, `upper(x)`,
  `lower(x)`, chained with `|`). Parse + apply via a closed allow-list of
  callables — **no eval, no exec**.
- Runtime: a single `apply_transformation(value, expr)` helper in
  `services/transformation/` that gets called wherever the simulator currently
  uses `transformation`. Falls through to the enum value if `transformation_expr`
  is blank (backwards compat).
- Frontend: on the Configurations expanded card the existing mappings table
  gets a `Custom expr` text input next to the dropdown. Saving routes through
  the existing `PATCH /api/v1/configurations/{id}` endpoint.

**Expertise.**
- Defensive parsing / sandboxing — no Python `eval`, no f-string interpolation,
  no `subprocess`. Token-level parser with a hand-rolled allow-list registry.
- The existing `FIELD_SYNONYMS` + `_suggest_transformation` flow in
  `services/config_engine/field_mapper.py` (so the change layers on cleanly).

**Out of scope.**
- A full Jinja-style template language.
- Persisting transformation history per-mapping (cover with an issue follow-up).

**Acceptance.**
- A mapping with `transformation_expr = 'int(x) | clamp(0, 1_000_000)'` applied
  to `"2,000"` returns `2000`.
- Invalid expr → mapping stays editable, UI shows a red message inline, sim
  doesn't crash (falls back to enum).
- 7/7 validator pass survives.

**Related issue.** **#113 — Custom Transformation Engine**.

---

## 3. Chain Runtime Engineer (Issue #109, MVP slice)

**Mission.** Sequential API chaining only — the simplest possible shape: `step A
output → JSONPath extract → inject into step B input`. No cycles, no event
sourcing, no parallelism, no DAG.

**Scope (MVP).**
- Backend: extend the config schema (the LLM-generated `endpoints` array
  already has placeholders `id`, `depends_on`, `extract`, `inject`) — make
  them actually drive execution. A `ChainExecutor` in `services/chain/` that:
  1. Topologically sorts endpoints by `depends_on`.
  2. Calls each endpoint in order against the mock-response store (existing
     simulator infrastructure).
  3. Applies extract → inject before the next call.
- Frontend: render the chain visually in the config detail panel as a vertical
  list of `[A] → [B] → [C]` with the extract/inject pairs shown between.
- Run the chain inside the existing `/simulations/run` smoke test type when
  the config has 2+ endpoints with a `depends_on` set.

**Expertise.**
- JSONPath (the `jsonpath-ng` package or a small hand-rolled `$.foo.bar`
  resolver).
- Topological sort with cycle detection (return a 400 if cycles exist —
  acyclic-only for the MVP).
- The simulator's existing `mock_responses` module so the chain run still works
  offline.

**Out of scope.**
- Cyclic graphs (#109 calls for these — defer to a follow-up).
- Async / event-driven steps.
- Conditional branching.

**Acceptance.**
- A 2-step OAuth-then-resource flow (token endpoint → protected endpoint) runs
  inside the simulator and the protected step sees the token from the first
  step's `access_token` field.
- Cycle detected → 400 with a clear message.
- Existing single-endpoint configs unaffected.

**Related issue.** **#109 — Workflow Orchestration Engine** (MVP slice only).

---

## 4. MCP Bridge Engineer (Issue #114)

**Mission.** Run AdaptConfig as a **stdio MCP server** that any IDE/agent can
plug between two services. The bridge consults the existing config + chain
executor and exposes 3 tools so an LLM can invoke integrations without
hand-crafted glue.

**Scope (MVP).**
- New module `src/finspark/mcp/` with a small `__main__.py` entry exposing:
  - `list_adapters()` → catalogue summary.
  - `generate_config(document_text, adapter_hint?)` → returns a config_id.
  - `invoke(config_id, payload)` → runs the chain executor against the mock
    response store and returns the final result.
- Use the `mcp[cli]` package already in `pyproject.toml`.
- Add a `[project.scripts]` entry `adaptconfig-mcp` that runs the stdio server.
- Auth: read `FINSPARK_MCP_TOKEN` from env; reject if absent in non-debug mode.

**Expertise.**
- The Model Context Protocol stdio contract (initialize, list_tools,
  call_tool, shutdown).
- Reusing FastAPI handlers without HTTP — call the underlying service classes
  directly (`DocumentParser`, `ConfigGenerator`, `IntegrationSimulator`).
- Subprocess lifecycle hygiene — clean shutdown on SIGTERM, no leaked
  background tasks.

**Out of scope.**
- A separate auth model beyond the env-token gate (full RBAC is a follow-up).
- Streaming responses (return them whole; SSE follow-up).

**Acceptance.**
- `uv run adaptconfig-mcp` starts a stdio server and an MCP client (Claude
  Desktop or an `mcp` CLI ping) lists the 3 tools.
- Calling `invoke()` end-to-end through the MCP boundary returns the same
  result a direct API call would have.
- No regressions in existing HTTP routes.

**Related issue.** **#114 — Runtime API Proxy — AdaptConfig as Integration
Middleware**.

---

## 5. Universal API + Skill Author (Issue #116)

**Mission.** Make every user-facing feature reachable via HTTP and ship a
drop-in `adaptconfig.skill.md` so any Claude Code / Claude Agent SDK consumer
can operate the app exactly like a human.

**Scope (MVP).**
- Audit `frontend/src/pages/` → every interactive element traces to a backend
  endpoint. Fill gaps in `api/routes/*.py`.
- Composite endpoint `POST /api/v1/configurations/{id}/validate-and-test`
  encapsulating the pipeline currently glued in React. Migrate the React
  pipeline to call it.
- Author `adaptconfig.skill.md` at repo root with frontmatter, when-to-use
  triggers, API reference with one curl per group, and an end-to-end
  upload → suggest → generate → validate sample.
- `scripts/skill_smoke.py` drives the same agent flow against the live API
  and asserts 7/7 on the gold-standard fixture.
- `tests/integration/test_skill_api_surface.py` enforces the audit
  invariants (no orphan UI buttons, composite endpoint exists, etc.).

**Expertise.**
- Anthropic Skill schema (`name`, `description`, body sections, examples).
- FastAPI `APIResponse[...]` typing convention used elsewhere in this repo.
- TanStack Query patterns so the React migration to the new endpoint is
  one-line.

**Out of scope.**
- Streaming responses on the composite endpoint (SSE follow-up).
- A new auth model.

**Acceptance.**
- `scripts/skill_smoke.py` finishes green against a fresh DB.
- `tests/integration/test_skill_api_surface.py` passes.
- React UI behaviour unchanged.

**Related issue.** **#116 — Universal API + drop-in Claude Skill.**

---

## Dispatch protocol

1. The lead (Claude in the main session) picks one persona at a time.
2. Spawns an `Agent` call with `subagent_type=engineering-lead`. The agent
   prompt opens with the relevant persona block above plus the user's stated
   MVP acceptance criteria.
3. After the agent reports done, the lead runs `npx tsc --noEmit` + a focused
   Playwright sweep + the 7/7 fixture validation to confirm no regression.
4. Commits the agent's diff, links the GH issue (`Closes #N`), pushes.

This file is the persisted source of truth — update it (not chat) when the
persona scope changes.
