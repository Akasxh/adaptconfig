# Chain Testing — Dependency DAG Execution

This document explains the chain-testing feature added for issue #113.
It's the missing piece between "each endpoint works in isolation" and
"the integration as a whole works."

---

## 1. The bug we're fixing

The simulator's main loop, before this feature, was:

```python
for endpoint in endpoints:
    steps.append(self._test_endpoint(endpoint, config))
```

It tested each endpoint independently — confirming the URL and method
were defined, the mock returned *something*. What it **never** checked:

- Did the OAuth token returned by step 1 actually get attached to step 2's
  `Authorization` header?
- Did the `enquiry_id` returned by step 2 actually flow into step 3's
  URL?
- If step 1 fails, are we honest about steps 2 and 3 being **blocked**
  (not independently broken)?

Real integration bugs live in that glue between calls. Chain testing
makes the glue first-class.

---

## 2. The data model

Every endpoint extracted from a document now carries four chain fields
in addition to its path/method/description:

| Field         | Type                 | Meaning                                                          |
|---------------|----------------------|------------------------------------------------------------------|
| `id`          | `str`                | Stable identifier — used to reference this step from others      |
| `depends_on`  | `list[str]`          | IDs of endpoints whose output this one needs                     |
| `extract`     | `list[ExtractRule]`  | Values to pull from this endpoint's response into chain context  |
| `inject`      | `list[InjectRule]`   | Values to plug from chain context into this endpoint's request   |

An `ExtractRule` is `{json_path, save_as}` — pull `data.access_token`
out of the response, save it as `access_token` in the context dict.

An `InjectRule` is `{template, location, target_field}` — render
`"Bearer {{access_token}}"` against the context and write it to the
request's `header.Authorization`.

`location` is one of `header / query / path / body`.

---

## 3. Where the metadata comes from

Three sources, in priority order:

1. **LLM extraction.** The document parser's prompt asks Gemini/GPT to
   produce all four fields directly. Reliable when it works; sometimes
   the model under-extracts (especially `inject`).
2. **Heuristic backfill.** Runs after the LLM. Two rules:
    - **Path-template rule.** Any `{placeholder}` in a path almost
      always means "I need that value from an earlier step." If some
      earlier step `extract`s a value with the matching `save_as`, we
      add a `depends_on` and a path-`inject` for it.
    - **Auth fan-out.** If any earlier step extracts `access_token` (or
      a similar token), every subsequent step gets a `depends_on` on
      that step plus an `Authorization: Bearer {{access_token}}` header
      inject — unless it already has one.
3. **Re-analyze button.** Existing documents parsed before this feature
   landed have no chain metadata. The Documents page exposes a
   per-document **Re-analyze** button (refresh icon) that re-runs the
   LLM parser on the original file. Lazy on-demand backfill, no
   surprise LLM bills on documents the user doesn't care about.

For documents that still have no chain metadata at simulation time (e.g.
upload-and-test before re-analyze), the chain executor normalizes the
adapter's bare endpoint list through the same heuristic backfill — so
the user always gets at least heuristic-driven dependencies.

---

## 4. The graph

The package lives at `src/finspark/services/chain/`.

### 4.1 Edge types

Endpoints are nodes. Edges are typed:

| Kind          | Meaning                                                | Counted in toposort? |
|---------------|--------------------------------------------------------|----------------------|
| `data`        | B's `depends_on` references A, or B injects a value A produced | **Yes** |
| `auth`        | B injects an auth token (`access_token`) that A extracts | **Yes** |
| `polling`     | A → A self-edge for "wait and check" endpoints (e.g. Account Aggregator) | **No** |
| `compensates` | Reverse edge — fired only on chain failure (refund, void) | **No** |

The split matters because the **happy-path DAG must be acyclic**, but
polling self-loops and compensation reverse-edges are legitimate. They
exist in the graph (and the UI renders them with distinct colors), but
they're skipped by both the cycle detector and the toposort.

Today only `data` and `auth` edges are actually constructed from
metadata. The `polling` and `compensates` kinds are wired through the
graph and UI but no parser writes them yet — they're plumbed so the
addition is purely additive when the feature lands.

### 4.2 Cycle detection

Implemented in `graph._find_cycle` — iterative three-color DFS:

- White (0): not yet visited
- Gray (1): on the current DFS stack
- Black (2): fully explored

A back-edge to a gray node means a cycle. The cycle is reconstructed by
walking parent pointers from the offending node back to the gray
ancestor.

**The cycle finder only considers `data` and `auth` edges.** Polling
self-loops and compensation reverse-edges are excluded by construction.

#### What's tested

| Case                                              | Detected? |
|---------------------------------------------------|-----------|
| Two-node back-reference: A → B → A                | ✅        |
| Three-node ring: A → B → C → A                    | ✅        |
| Self-loop: A → A (almost always a typo)           | ✅        |
| Diamond: A → B, A → C, B → D, C → D (not a cycle) | ✅ (correctly NOT flagged) |
| Cycle via inferred auth edge                      | ✅        |
| Polling self-loop (`kind=polling`)                | ✅ (correctly NOT flagged) |
| Compensates reverse edge                          | ✅ (correctly NOT flagged) |
| Reference to non-existent node                    | ✅ (silently dropped, graph stays valid) |

Coverage is in `tests/unit/test_chain.py::TestGraph` — 8 cycle-related
test cases.

When a cycle is found, `graph.cycle_error` gets the human-readable
description (`"cycle detected in data flow: b -> c -> a -> b"`), and
the executor refuses to run — surfacing the config error instead of
producing mystery failures at runtime.

### 4.3 Topological sort

`graph._topological_layers` uses Kahn's algorithm but groups nodes by
**depth**, not into a flat order. This lets the UI render columns
left-to-right with siblings stacked in the same column.

Tie-breaking inside a layer preserves the order endpoints were declared
in the source spec — same input always produces the same layer
arrangement, so diffing two chain runs is meaningful.

---

## 5. Execution

`executor.run_chain(graph, adapter_name, base_url)` walks the
topological order, threading a single `context: dict` through every
step. For each node:

1. **Resolve injects.** For each `inject` rule, render `{{var}}`
   substitutions against `context`. If any variable is missing:
    - record the missing names on the step's `injected` log entry
    - mark the step as `failed` with `error: "inject_failed: missing
      context vars [...]"`
    - skip the mock call
2. **Resolve path placeholders.** Any `{name}` in the URL is replaced
   from `request.path_params`.
3. **Call the mock.** `simulation/mock_responses.generate_mock_response`
   routes by adapter name and returns a realistic adapter-specific fake
   payload.
4. **Apply extracts.** For each `extract` rule, walk the JSON path on
   the response, write into `context` under `save_as`. If the path
   doesn't resolve:
    - record `found: false` on the step's `extracted` log entry
    - mark the step as **`mock_contract_violation`** (see §6)

### 5.1 Result record per step

Each step emits a dict with:

- `id`, `path`, `method`, `description`
- `status` — `passed | failed | blocked_by_upstream | mock_contract_violation`
- `request` — `{headers, query, path_params, body, resolved_path}`
  showing the **actually-sent** request after injection + path
  resolution
- `response` — the raw mock response
- `extracted` — list of `{json_path, save_as, value, found}`
- `injected` — list of `{template, location, target_field, resolved, missing_vars}`
- `latency_ms`
- `error` (when applicable)
- `blocked_by` (only on blocked steps — list of upstream node IDs at
  fault)

The whole structure is JSON-serializable and is persisted into
`simulations.results` as a single column. No new tables.

---

## 6. The mock-contract-violation status

The single biggest risk in chain testing with fake responses is:

> *Mock returns shape X, downstream extract expects shape Y, chain
> "fails" but the chain wiring is correct — the mock is wrong.*

If we marked these as plain `failed`, the user would chase a wiring bug
that doesn't exist. So when an extract path resolves nothing despite
the mock returning a response, we explicitly mark the step as
`mock_contract_violation` and surface it in the UI with a distinct
amber color and the label "MOCK MISMATCH."

The error message reads:

> *"mock_response missing declared extract fields: [...] — chain
> wiring is fine, but the mock for this endpoint doesn't return what
> later steps expect."*

That tells the operator exactly which side of the contract to fix.

---

## 7. Cascade analysis (the "one root cause" rule)

Without cascade analysis, three failures show as three problems. With
it, downstream steps that couldn't run because of an upstream failure
are reclassified.

After execution:

1. Find every step in `root_cause_ids` — anything with status `failed`
   or `mock_contract_violation`.
2. For each step still marked `failed` whose error starts with
   `inject_failed:`:
    - find all ancestors in the graph
    - if any ancestor is in `root_cause_ids`, reclassify this step as
      `blocked_by_upstream` with `blocked_by: [list of culpable ancestor IDs]`
3. The chain's overall `blocked_root` is the **topologically earliest**
   step in `root_cause_ids` — i.e. the one that started the cascade.

The summary line distinguishes these honestly:

> `"0/3 passed, 1 mock-contract violation(s), 2 blocked by upstream"`

One root cause to investigate, not three.

---

## 8. The UI

A new **Chain Test** tab on each Configuration card.

### Header
Summary banner — green when everything passes, red otherwise, with the
`blocked_root` step ID prominently displayed.

### SVG DAG
Hand-rolled SVG (no graph library, no new dependency). Layout:

- Nodes laid out by **topological layer**, columns left-to-right
- Each node is a rounded rect, 230×84 px, showing method/path/id/latency
- Status pill in the lower-right of each node, colored by step status
- Edges are bezier curves from the right edge of the source to the left
  edge of the target
- Edge color encodes type: grey=data, blue=auth, dashed-purple=polling,
  orange=compensates
- Edges carry the variable name they're transporting (`access_token`,
  `enquiry_id`) as a small label at the midpoint
- Arrow markers per edge type

### Click-to-inspect
Clicking a node opens an inline details panel below the graph showing:

- Step status pill + resolved request URL
- Every **injected** value with the template and the rendered output
  (red if any variables were missing)
- Every **extracted** value with the JSON path and the value found
  (green if found, red if not)
- The raw mock response (collapsible JSON)
- Any `blocked_by` list

---

## 9. Lifecycle of one chain run

End-to-end, on clicking **Run Chain Test**:

```
UI                         Route                         Engine
─────────────────────      ──────────────────────────    ──────────────────────────────
POST /simulations/run      load Configuration           build_chain_graph(endpoints)
{test_type: "chain"}       load linked Document           ├── cycle check
                           extract endpoints from         │   (data + auth edges only)
                           parsed_result OR full_config   └── topological layers
                           normalize_endpoints_for_chain
                           (heuristics fill gaps)        run_chain(graph, adapter, base_url)
                                                          ├── for each node in topo order:
                                                          │   ├── resolve injects from context
                                                          │   ├── path placeholder substitution
                                                          │   ├── mock_responses.generate_*
                                                          │   └── apply extracts → context
                                                          ├── cascade analysis
                                                          │   (reclassify failed→blocked)
                                                          └── return chain_run dict

                           persist chain_run to
                           simulations.results JSON       (NDJSON; no new tables)

                           return SimulationResponse
                           with chain_run field
←──── 200 OK ──────────────
render SVG DAG
+ per-step click panels
```

---

## 10. Files

| Layer       | File                                                       | Lines (approx) |
|-------------|------------------------------------------------------------|----------------|
| Schema      | `src/finspark/schemas/documents.py`                        | +30            |
| Schema      | `src/finspark/schemas/simulations.py`                      | +1             |
| Engine      | `src/finspark/services/chain/__init__.py`                  | 10             |
| Engine      | `src/finspark/services/chain/graph.py`                     | 200            |
| Engine      | `src/finspark/services/chain/executor.py`                  | 290            |
| Engine      | `src/finspark/services/chain/heuristics.py`                | 200            |
| Mocks       | `src/finspark/services/simulation/mock_responses.py`       | +15            |
| Parser      | `src/finspark/services/parsing/document_parser.py`         | +140           |
| Routes      | `src/finspark/api/routes/documents.py` (reanalyze)         | +100           |
| Routes      | `src/finspark/api/routes/simulations.py` (chain branch)    | +170           |
| Types       | `frontend/src/types/index.ts`                              | +50            |
| Client      | `frontend/src/lib/api.ts`                                  | +4             |
| UI          | `frontend/src/pages/Documents.tsx` (re-analyze button)     | +25            |
| UI          | `frontend/src/pages/Configurations.tsx` (Chain Test tab)   | +330           |
| Tests       | `tests/unit/test_chain.py`                                 | 230            |

---

## 11. What's deliberately not in v1

These earned their cut during scoping; they're cleanly additive.

- **Polling unrolling.** The graph supports `kind=polling` self-edges
  but no parser produces them and the executor doesn't yet loop. To
  add: detect `polling: true` in endpoint metadata, emit a self-edge,
  in the executor wrap the step in a `while not terminal_condition`
  with a max-iterations cap.
- **Compensation execution.** The graph supports `kind=compensates`
  reverse edges; the executor doesn't yet fire them on chain failure.
  To add: after cascade analysis, walk failed steps' compensating
  edges and execute the inverse step.
- **LLM-as-reviewer.** Considered, cut. The rule engine already
  catches the schema-mismatch and forgotten-auth bug classes (proven
  in tests); the LLM reviewer's incremental value (missing
  idempotency, missing rollback) doesn't justify the per-run cost.
- **Per-chain-run database tables (`chain_runs`, `chain_edges`).**
  The `simulations.results` JSON column already holds the whole run.
  Tables earn their existence when someone needs to query *across*
  chain runs ("which edges have ever failed with type-mismatch"), and
  nobody is yet.
- **Drift detection vs real APIs.** Periodic comparison of chain runs
  against the live sandbox. Sensible — but out of scope until users
  ask, and overlaps with the contract-testing routes that exist on
  other branches.

---

## 12. Quick reference — how to add a new chain-aware endpoint

1. Upload the spec (YAML/OpenAPI/PDF/DOCX) through the Documents page.
   The parser will populate `extract` and `inject` automatically for
   common patterns.
2. If the chain test shows missing dependencies, open the document
   detail or hit the **Re-analyze** button to re-run the LLM parser.
3. If the LLM still misses something, the heuristic backfill should
   catch path placeholders (`{enquiry_id}` → auto-inject) and auth
   tokens (`access_token` → fan-out as `Authorization`).
4. If the chain test surfaces `mock_contract_violation`, the chain
   wiring is fine — the issue is the mock generator in
   `simulation/mock_responses.py` not returning the field shape the
   chain expects. Edit the relevant `_AdapterMock.respond` branch to
   include the missing field.
