# FinSpark Bugfixes & Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 6 issues — webhook event delivery, editor role permissions, config/simulation delete, rollback button visibility, LLM-powered BRD parsing, credential vaulting — then update the presentation slides with architecture details.

**Architecture:** Backend is FastAPI + SQLAlchemy async + SQLite/PostgreSQL. Frontend is React 18 + TypeScript + TanStack Query + Tailwind. Event system uses in-process pub/sub (`core/events.py`) with webhook delivery (`services/webhook_delivery.py`). Auth uses JWT with RBAC (`admin`/`editor`/`viewer`). New users default to `editor` role with per-user tenant isolation.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, React 18, TypeScript, Vite, Google Gemini Flash (LLM), LaTeX Beamer (presentation)

---

## File Structure

### Files to Modify

| File | Responsibility |
|------|---------------|
| `src/finspark/api/routes/configurations.py` | Add config delete endpoint, emit events on generate/transition/rollback |
| `src/finspark/api/routes/simulations.py` | Add simulation delete endpoint, emit events on simulation complete |
| `src/finspark/api/routes/documents.py` | Emit events on document parsed, relax delete to editor role |
| `src/finspark/api/routes/webhooks.py` | Relax register/delete to editor role |
| `src/finspark/services/parsing/document_parser.py` | Add LLM-powered BRD/SOW entity extraction |
| `src/finspark/services/parsing/llm_parser.py` | **New** — LLM entity extraction for free-text documents |
| `src/finspark/core/credentials.py` | **New** — Credential vault abstraction (env-var backed) |
| `frontend/src/lib/api.ts` | Add config delete, simulation delete API methods |
| `frontend/src/pages/Configurations.tsx` | Add delete button, fix rollback visibility |
| `frontend/src/pages/Simulations.tsx` | Add delete button |
| `docs/presentation/adaptconfig_slides.tex` | Add architecture deep-dive slides |

---

## Task 1: Fix Webhook Event Delivery (Issue #1 — HIGH PRIORITY)

**Root Cause:** The event system (`core/events.py`) defines event types and `main.py` registers webhook delivery handlers on those events, BUT no route ever calls `events.emit()`. The pub/sub wiring exists but nobody publishes events.

**Files:**
- Modify: `src/finspark/api/routes/configurations.py` (add emit calls after generate, transition, rollback)
- Modify: `src/finspark/api/routes/simulations.py` (add emit call after simulation completes)
- Modify: `src/finspark/api/routes/documents.py` (add emit call after document parsed)

- [ ] **Step 1: Add event emission to configuration generation**

In `src/finspark/api/routes/configurations.py`, add the import at the top (near line 27):

```python
from finspark.core import events
```

Then in `generate_configuration()` (around line 620, after the audit.log call and before the return statement), add:

```python
    await events.emit(events.CONFIG_CREATED, {
        "tenant_id": tenant.tenant_id,
        "config_id": configuration.id,
        "config_name": configuration.name,
        "generation_path": generation_path,
        "adapter_version_id": request.adapter_version_id,
    })
```

- [ ] **Step 2: Add event emission to configuration transition**

In the `transition_configuration()` function (around line 809, after `await db.flush()` and before the return), add:

```python
    # Emit event for active deployments
    if body.target_state.value == "active":
        await events.emit(events.CONFIG_DEPLOYED, {
            "tenant_id": tenant.tenant_id,
            "config_id": config.id,
            "config_name": config.name,
            "previous_state": previous_state.value,
            "new_state": body.target_state.value,
        })
    else:
        await events.emit(events.CONFIG_UPDATED, {
            "tenant_id": tenant.tenant_id,
            "config_id": config.id,
            "config_name": config.name,
            "previous_state": previous_state.value,
            "new_state": body.target_state.value,
        })
```

- [ ] **Step 3: Add event emission to configuration rollback**

In the `rollback_configuration()` function (around line 346, after `await db.flush()` and before the return), add:

```python
    await events.emit(events.CONFIG_ROLLED_BACK, {
        "tenant_id": tenant.tenant_id,
        "config_id": config_id,
        "config_name": config.name,
        "previous_version": previous_version,
        "restored_version": restored.version,
    })
```

- [ ] **Step 4: Add event emission to simulation completion**

In `src/finspark/api/routes/simulations.py`, add the import:

```python
from finspark.core import events
```

Then in `run_simulation()`, after the simulation record is updated and audit is logged (after the `await audit.log(...)` call), add:

```python
    await events.emit(events.SIMULATION_COMPLETED, {
        "tenant_id": tenant.tenant_id,
        "simulation_id": simulation.id,
        "configuration_id": request.configuration_id,
        "status": simulation.status,
        "total_tests": total,
        "passed_tests": passed,
        "failed_tests": total - passed,
    })
```

- [ ] **Step 5: Add event emission to document parsing**

In `src/finspark/api/routes/documents.py`, add the import:

```python
from finspark.core import events
```

Then in the `upload_document()` function, after the document status is updated to `"parsed"` and the db is flushed (find where `doc.status = "parsed"` is set), add:

```python
    await events.emit(events.DOCUMENT_PARSED, {
        "tenant_id": tenant.tenant_id,
        "document_id": doc.id,
        "filename": doc.filename,
        "doc_type": doc_type,
        "confidence_score": parsed.confidence_score,
    })
```

- [ ] **Step 6: Run tests to verify event emission**

Run: `cd /home/akash/PROJECTS/finspark && python -m pytest tests/ -x -q --tb=short 2>&1 | tail -20`
Expected: All existing tests pass (events are fire-and-forget, shouldn't break anything)

- [ ] **Step 7: Commit**

```bash
git add src/finspark/api/routes/configurations.py src/finspark/api/routes/simulations.py src/finspark/api/routes/documents.py
git commit -m "fix: emit events from routes so webhooks fire on real actions"
```

---

## Task 2: Fix Editor Role Permissions (Issue #2 — HIGH PRIORITY)

**Root Cause:** Several endpoints use `require_role("admin")` when they should allow `editor` too. Since every user gets their own `tenant_id` and data is tenant-isolated, an editor deleting their own documents is safe.

**Files:**
- Modify: `src/finspark/api/routes/webhooks.py:44,105` (change admin → admin+editor for register and delete)
- Modify: `src/finspark/api/routes/documents.py:176` (change admin → admin+editor for delete)

- [ ] **Step 1: Allow editors to register webhooks**

In `src/finspark/api/routes/webhooks.py`, line 44, change:

```python
    tenant: TenantContext = require_role("admin"),
```

to:

```python
    tenant: TenantContext = require_role("admin", "editor"),
```

- [ ] **Step 2: Allow editors to delete webhooks**

In the same file, line 105, change:

```python
    tenant: TenantContext = require_role("admin"),
```

to:

```python
    tenant: TenantContext = require_role("admin", "editor"),
```

- [ ] **Step 3: Allow editors to delete their own documents**

In `src/finspark/api/routes/documents.py`, line 176, change:

```python
    tenant: TenantContext = require_role("admin"),
```

to:

```python
    tenant: TenantContext = require_role("admin", "editor"),
```

- [ ] **Step 4: Run tests**

Run: `cd /home/akash/PROJECTS/finspark && python -m pytest tests/ -x -q --tb=short 2>&1 | tail -20`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/finspark/api/routes/webhooks.py src/finspark/api/routes/documents.py
git commit -m "fix: allow editor role to register/delete webhooks and delete own documents"
```

---

## Task 3: Add Delete for Configurations and Simulations (Issue #3)

**Files:**
- Modify: `src/finspark/api/routes/configurations.py` (add DELETE endpoint)
- Modify: `src/finspark/api/routes/simulations.py` (add DELETE endpoint)
- Modify: `frontend/src/lib/api.ts` (add delete API methods)
- Modify: `frontend/src/pages/Configurations.tsx` (add delete button)
- Modify: `frontend/src/pages/Simulations.tsx` (add delete button)

### Backend

- [ ] **Step 1: Add configuration delete endpoint**

In `src/finspark/api/routes/configurations.py`, add this endpoint after the `list_configurations` function (end of file, around line 887):

```python
@router.delete("/{config_id}", response_model=APIResponse[dict])
async def delete_configuration(
    config_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = require_role("admin", "editor"),
    audit: AuditService = Depends(get_audit_service),
) -> APIResponse[dict]:
    """Delete a configuration and its history."""
    stmt = select(Configuration).where(
        Configuration.id == config_id,
        Configuration.tenant_id == tenant.tenant_id,
    )
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")

    config_name = config.name
    await db.delete(config)
    await db.flush()

    await audit.log(
        tenant_id=tenant.tenant_id,
        actor=tenant.tenant_name,
        action="delete_configuration",
        resource_type="configuration",
        resource_id=config_id,
        details={"name": config_name},
    )

    return APIResponse(
        data={"id": config_id, "deleted": True},
        message=f"Configuration '{config_name}' deleted",
    )
```

- [ ] **Step 2: Add simulation delete endpoint**

In `src/finspark/api/routes/simulations.py`, read the file first to find the right location. Add this endpoint after the existing endpoints:

```python
@router.delete("/{simulation_id}", response_model=APIResponse[dict])
async def delete_simulation(
    simulation_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = require_role("admin", "editor"),
    audit: AuditService = Depends(get_audit_service),
) -> APIResponse[dict]:
    """Delete a simulation and its steps."""
    stmt = select(Simulation).where(
        Simulation.id == simulation_id,
        Simulation.tenant_id == tenant.tenant_id,
    )
    result = await db.execute(stmt)
    sim = result.scalar_one_or_none()
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")

    await db.delete(sim)
    await db.flush()

    await audit.log(
        tenant_id=tenant.tenant_id,
        actor=tenant.tenant_name,
        action="delete_simulation",
        resource_type="simulation",
        resource_id=simulation_id,
        details={},
    )

    return APIResponse(
        data={"id": simulation_id, "deleted": True},
        message="Simulation deleted",
    )
```

Note: You'll need to add imports for `require_role` and `get_audit_service` if not already imported. Check the existing imports first.

- [ ] **Step 3: Run backend tests**

Run: `cd /home/akash/PROJECTS/finspark && python -m pytest tests/ -x -q --tb=short 2>&1 | tail -20`
Expected: PASS

- [ ] **Step 4: Commit backend changes**

```bash
git add src/finspark/api/routes/configurations.py src/finspark/api/routes/simulations.py
git commit -m "feat: add delete endpoints for configurations and simulations"
```

### Frontend

- [ ] **Step 5: Add delete API methods to frontend**

In `frontend/src/lib/api.ts`, add to `configurationsApi` (after the `update` method around line 262):

```typescript
  delete: (id: string) =>
    api.delete<APIResponse<{ id: string; deleted: boolean }>>(`/api/v1/configurations/${id}`).then((r) => r.data),
```

Add to `simulationsApi` (after the `get` method around line 270):

```typescript
  delete: (id: string) =>
    api.delete<APIResponse<{ id: string; deleted: boolean }>>(`/api/v1/simulations/${id}`).then((r) => r.data),
```

- [ ] **Step 6: Add delete button to Configurations page**

In `frontend/src/pages/Configurations.tsx`:

1. Add `Trash2` to the lucide-react import
2. In the table row for each configuration (the row that shows config name, status, etc.), add a delete button. Find the row rendering and add a delete button with confirm dialog, similar to how Documents.tsx does it.

The implementation should:
- Add a `confirmDelete` state: `const [confirmDelete, setConfirmDelete] = useState<Configuration | null>(null);`
- Add a `deleteMutation` using `useMutation` calling `configurationsApi.delete(id)`, with `onSuccess` invalidating `["configurations"]` query
- Add a trash icon button in each row that sets `confirmDelete`
- Add a confirmation modal (copy pattern from Documents.tsx)

- [ ] **Step 7: Add delete button to Simulations page**

In `frontend/src/pages/Simulations.tsx`:

Same pattern as above:
1. Add `Trash2` to imports
2. Add `confirmDelete` state and `deleteMutation`
3. Add trash button per simulation row
4. Add confirmation modal

- [ ] **Step 8: Run frontend build to verify**

Run: `cd /home/akash/PROJECTS/finspark/frontend && npx tsc --noEmit 2>&1 | tail -20`
Expected: No type errors

- [ ] **Step 9: Commit frontend changes**

```bash
git add frontend/src/lib/api.ts frontend/src/pages/Configurations.tsx frontend/src/pages/Simulations.tsx
git commit -m "feat: add delete buttons for configurations and simulations in UI"
```

---

## Task 4: Fix Rollback Button Visibility (Issue #4 — HIGH PRIORITY)

**Root Cause:** After investigation, the rollback button IS present in the UI (`Configurations.tsx:319-328`) and appears in the History tab for non-current versions. The issue is likely that:
1. For a newly generated config (version 1), there's only one history entry, so no rollback target exists
2. The History tab must be manually clicked — it's not the default tab

The actual problem is more subtle: the `HistoryPanel` shows rollback for ALL non-current versions. But if a config has only been through `created` (version 1) with no edits, there's nothing to rollback to. The button IS there but only visible when you:
1. Click on a configuration to expand it
2. Click the "History" tab
3. Have at least 2 versions (i.e., the config was edited/updated at least once)

**Fix needed:** Make rollback more discoverable by showing a rollback indicator in the config detail header when history exists, and ensure the config's current state after transitions still shows history. Also verify the rollback endpoint itself works correctly.

**Files:**
- Modify: `frontend/src/pages/Configurations.tsx` (add rollback shortcut in detail header)

- [ ] **Step 1: Read the full Configurations.tsx to understand the detail layout**

Read: `frontend/src/pages/Configurations.tsx` — specifically the `ConfigDetail` component and the area showing the status/lifecycle stepper, to understand where to add a rollback indicator.

- [ ] **Step 2: Add a "Rollback Available" badge and quick-action in the config detail header**

In the `ConfigDetail` component, after the lifecycle stepper section and before the tabs, add a section that shows when history has more than 1 entry. This section should:

1. Fetch history using a `useQuery` for `["config-history", cfg.id]`
2. If history has entries with versions != current version, show a badge: "Version history available — click History tab to rollback"
3. Or better: add a dedicated "Rollback" button that auto-switches to the History tab

```tsx
// Inside ConfigDetail, after StatusStepper, before tabs
const { data: historyData } = useQuery({
  queryKey: ["config-history", cfg.id],
  queryFn: () => configurationsApi.history(cfg.id),
});
const historyEntries = historyData?.data ?? [];
const hasRollbackTargets = historyEntries.some((e) => e.version !== cfg.version);
```

Then in the JSX, before the tab bar:

```tsx
{hasRollbackTargets && (
  <div style={{
    display: "flex", alignItems: "center", gap: 8,
    padding: "8px 12px", borderRadius: 6,
    background: "rgba(56,229,205,0.06)", border: "1px solid rgba(56,229,205,0.15)",
  }}>
    <RotateCcw style={{ width: 14, height: 14, color: "var(--color-teal)" }} />
    <span style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>
      {historyEntries.length} version(s) available
    </span>
    <button
      type="button"
      className="btn-secondary"
      style={{ fontSize: 11, padding: "3px 8px", marginLeft: "auto" }}
      onClick={() => setActiveTab("history")}
    >
      View History
    </button>
  </div>
)}
```

- [ ] **Step 3: Verify rollback works end-to-end**

Manually test or write a quick API test:
1. Generate a config
2. Transition it to a new state (this creates a history entry)
3. Check that history shows version entries
4. Confirm rollback button appears in the History tab
5. Click rollback and verify the config reverts

- [ ] **Step 4: Run frontend build**

Run: `cd /home/akash/PROJECTS/finspark/frontend && npx tsc --noEmit 2>&1 | tail -20`
Expected: No type errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Configurations.tsx
git commit -m "fix: make rollback feature more discoverable with version history banner"
```

---

## Task 5: LLM-Powered BRD/SOW Parsing (Issue #5)

**Root Cause:** `DocumentParser.parse_text()` uses only regex patterns for entity extraction from free-text documents (DOCX/PDF). Structured specs (YAML/JSON/OpenAPI) work well at 95% confidence, but BRDs/SOWs produce low-quality results.

**Files:**
- Create: `src/finspark/services/parsing/llm_parser.py`
- Modify: `src/finspark/services/parsing/document_parser.py`

- [ ] **Step 1: Create the LLM parser module**

Create `src/finspark/services/parsing/llm_parser.py`:

```python
"""LLM-powered entity extraction for free-text documents (BRD/SOW)."""

import json
import logging
from typing import Any

from finspark.core.config import settings
from finspark.services.llm.client import GeminiClient, get_llm_client

logger = logging.getLogger(__name__)

_SYSTEM_INSTRUCTION = (
    "You are an expert at extracting structured integration requirements from "
    "enterprise Business Requirement Documents (BRDs) and Statements of Work (SOWs) "
    "for Indian financial services. Extract API endpoints, field definitions, "
    "authentication requirements, and service identifiers."
)

_EXTRACTION_PROMPT = """Analyze this document text and extract structured information.

Document text:
---
{text}
---

Return a JSON object with these keys:
{{
  "title": "Document title or best guess",
  "summary": "One-line summary of the document's purpose",
  "services_identified": ["List of service/API names mentioned"],
  "endpoints": [
    {{"path": "/api/path", "method": "POST", "description": "What it does", "is_mandatory": true}}
  ],
  "fields": [
    {{"name": "field_name", "data_type": "string|number|boolean|date", "is_required": true, "source_section": "Which section mentions this field"}}
  ],
  "auth_requirements": [
    {{"auth_type": "api_key|oauth2|bearer|mtls", "details": {{"description": "How auth works"}}}}
  ],
  "security_requirements": ["List of security requirements mentioned"],
  "sla_requirements": {{"response_time_ms": null, "availability_percent": null}}
}}

Focus on Indian fintech terms: CIBIL, PAN, Aadhaar, GSTIN, UPI, NEFT, IMPS, eKYC, etc.
Only include fields and endpoints explicitly mentioned or strongly implied. Do not hallucinate.
Return ONLY valid JSON, no markdown fences."""


async def extract_entities_llm(text: str) -> dict[str, Any] | None:
    """Use Gemini to extract structured entities from free-text.

    Returns parsed dict on success, None on failure (caller should fall back to regex).
    """
    if not settings.ai_enabled or not settings.gemini_api_key:
        return None

    # Truncate to avoid token limits (Gemini Flash handles ~128k but be safe)
    truncated = text[:15000]

    try:
        client = get_llm_client()
        prompt = _EXTRACTION_PROMPT.format(text=truncated)

        response = await client.generate(
            prompt=prompt,
            system_instruction=_SYSTEM_INSTRUCTION,
            temperature=0.1,
            response_mime_type="application/json",
        )

        if not response:
            return None

        result = json.loads(response)
        logger.info("llm_entity_extraction_succeeded entities=%d", _count_entities(result))
        return result

    except Exception:
        logger.warning("llm_entity_extraction_failed", exc_info=True)
        return None


def _count_entities(result: dict[str, Any]) -> int:
    """Count total extracted entities for logging."""
    return (
        len(result.get("endpoints", []))
        + len(result.get("fields", []))
        + len(result.get("auth_requirements", []))
        + len(result.get("services_identified", []))
    )
```

- [ ] **Step 2: Integrate LLM parser into DocumentParser**

In `src/finspark/services/parsing/document_parser.py`, modify the `parse_text()` method to attempt LLM extraction first for BRD/SOW documents, falling back to regex.

Add import at top:

```python
import asyncio
```

Modify `parse_text()` (around line 61) to:

```python
    def parse_text(self, text: str, doc_type: str = "brd") -> ParsedDocumentResult:
        """Parse raw text content and extract structured information.

        For BRD/SOW documents, attempts LLM-powered extraction first,
        falling back to regex if LLM is unavailable or fails.
        """
        doc_type = self._normalize_doc_type(doc_type)

        # Try LLM extraction for free-text documents
        llm_result = None
        if doc_type in ("brd", "sow"):
            try:
                from finspark.services.parsing.llm_parser import extract_entities_llm
                llm_result = asyncio.get_event_loop().run_until_complete(
                    extract_entities_llm(text)
                )
            except RuntimeError:
                # No event loop (called from sync context or thread)
                pass

        if llm_result:
            return self._build_result_from_llm(llm_result, doc_type, text)

        # Fallback to regex-based extraction
        endpoints = self._extract_endpoints(text)
        fields = self._extract_fields(text)
        auth = self._extract_auth_requirements(text)
        services = self._extract_services(text)
        sections = self._extract_sections(text)
        security_reqs = self._extract_security_requirements(text)
        sla_reqs = self._extract_sla_requirements(text)

        total_entities = len(endpoints) + len(fields) + len(auth) + len(services)
        confidence = min(1.0, total_entities / 20.0)

        return ParsedDocumentResult(
            doc_type=doc_type,
            title=self._extract_title(text),
            summary=self._extract_summary(text),
            services_identified=services,
            endpoints=endpoints,
            fields=fields,
            auth_requirements=auth,
            security_requirements=security_reqs,
            sla_requirements=sla_reqs,
            sections=sections,
            confidence_score=round(confidence, 2),
            raw_entities=self._extract_all_entities(text),
        )
```

Add the helper method to the `DocumentParser` class:

```python
    def _build_result_from_llm(
        self, llm_data: dict, doc_type: str, original_text: str
    ) -> ParsedDocumentResult:
        """Build ParsedDocumentResult from LLM extraction output."""
        endpoints = []
        for ep in llm_data.get("endpoints", []):
            endpoints.append({
                "path": ep.get("path", ""),
                "method": ep.get("method", "GET"),
                "description": ep.get("description", ""),
                "parameters": [],
                "is_mandatory": ep.get("is_mandatory", True),
            })

        fields = []
        for f in llm_data.get("fields", []):
            fields.append({
                "name": f.get("name", ""),
                "data_type": f.get("data_type", "string"),
                "is_required": f.get("is_required", False),
                "source_section": f.get("source_section", ""),
            })

        auth = []
        for a in llm_data.get("auth_requirements", []):
            auth.append({
                "auth_type": a.get("auth_type", "api_key"),
                "details": a.get("details", {}),
            })

        # Augment with regex results for anything LLM might have missed
        regex_fields = self._extract_fields(original_text)
        llm_field_names = {f["name"] for f in fields}
        for rf in regex_fields:
            if rf["name"] not in llm_field_names:
                fields.append(rf)

        total_entities = len(endpoints) + len(fields) + len(auth) + len(llm_data.get("services_identified", []))
        confidence = min(1.0, max(0.7, total_entities / 20.0))  # LLM results get a floor of 0.7

        sla_reqs = llm_data.get("sla_requirements", {})
        sla_list = []
        if isinstance(sla_reqs, dict):
            if sla_reqs.get("response_time_ms"):
                sla_list.append(f"Response time: {sla_reqs['response_time_ms']}ms")
            if sla_reqs.get("availability_percent"):
                sla_list.append(f"Availability: {sla_reqs['availability_percent']}%")

        return ParsedDocumentResult(
            doc_type=doc_type,
            title=llm_data.get("title", self._extract_title(original_text)),
            summary=llm_data.get("summary", self._extract_summary(original_text)),
            services_identified=llm_data.get("services_identified", []),
            endpoints=endpoints,
            fields=fields,
            auth_requirements=auth,
            security_requirements=llm_data.get("security_requirements", []),
            sla_requirements=sla_list,
            sections=self._extract_sections(original_text),
            confidence_score=round(confidence, 2),
            raw_entities=self._extract_all_entities(original_text),
        )
```

**Important:** Since `parse_text` is called from `asyncio.to_thread` in the upload route, we can't just `await` inside it. The approach above uses `asyncio.get_event_loop().run_until_complete()` which won't work from a thread. Better approach — make the LLM call BEFORE calling `parse_text`, in the route itself.

**Revised approach:** Instead of modifying `parse_text`, modify the upload route in `documents.py` to call LLM extraction BEFORE falling back to the synchronous parser for BRD/SOW documents. This keeps the parser sync-compatible.

In `src/finspark/api/routes/documents.py`, after the file is saved and before parsing, add:

```python
    # For BRD/SOW documents, try LLM-powered extraction first
    llm_parsed = None
    if doc_type in ("brd", "sow"):
        try:
            from finspark.services.parsing.llm_parser import extract_entities_llm
            llm_parsed = await extract_entities_llm(raw_text)
        except Exception:
            logger.warning("LLM extraction failed, falling back to regex", exc_info=True)

    if llm_parsed:
        parsed = parser._build_result_from_llm(llm_parsed, doc_type, raw_text)
    else:
        parsed = await asyncio.to_thread(parser.parse_text, raw_text, doc_type)
```

The `_build_result_from_llm` method stays on the parser class as defined above, but remove the `asyncio.get_event_loop()` hack from `parse_text()` — keep `parse_text()` unchanged. The LLM integration happens only in the route.

- [ ] **Step 3: Run tests**

Run: `cd /home/akash/PROJECTS/finspark && python -m pytest tests/ -x -q --tb=short 2>&1 | tail -20`
Expected: PASS (LLM path is only taken when API key is present, existing tests use fixtures)

- [ ] **Step 4: Commit**

```bash
git add src/finspark/services/parsing/llm_parser.py src/finspark/services/parsing/document_parser.py src/finspark/api/routes/documents.py
git commit -m "feat: add LLM-powered entity extraction for BRD/SOW documents"
```

---

## Task 6: Credential Vault Abstraction (Issue #6)

**Root Cause:** Generated configs have `"credentials": {}`. No actual credential management exists.

**Files:**
- Create: `src/finspark/core/credentials.py`
- Modify: `src/finspark/api/routes/configurations.py` (integrate credential references)

- [ ] **Step 1: Create credential vault abstraction**

Create `src/finspark/core/credentials.py`:

```python
"""Credential vault abstraction for adapter authentication.

Supports environment-variable-based credential storage for development
and can be extended with HashiCorp Vault or AWS Secrets Manager.
"""

import logging
import os
from typing import Any

from finspark.core.security import decrypt_value, encrypt_value

logger = logging.getLogger(__name__)


class CredentialVault:
    """Manages adapter credentials with encryption at rest.

    Storage backends:
    - env: Read from environment variables (default, suitable for dev/Docker)
    - encrypted: Store encrypted in database (uses Fernet from core.security)
    """

    def store(self, tenant_id: str, adapter_name: str, credentials: dict[str, str]) -> dict[str, str]:
        """Encrypt and store credentials, returning references (not plaintext).

        Returns a dict of {key: "vault:encrypted_value"} references that can be
        safely stored in configuration JSON.
        """
        refs: dict[str, str] = {}
        for key, value in credentials.items():
            if value:
                refs[key] = f"vault:{encrypt_value(value)}"
            else:
                refs[key] = ""
        return refs

    def resolve(self, credential_refs: dict[str, str]) -> dict[str, str]:
        """Resolve credential references back to plaintext values.

        Handles three formats:
        - "vault:encrypted_data" → decrypt using Fernet
        - "env:VAR_NAME" → read from environment variable
        - plain string → return as-is (backward compat)
        """
        resolved: dict[str, str] = {}
        for key, ref in credential_refs.items():
            if not ref:
                resolved[key] = ""
            elif ref.startswith("vault:"):
                try:
                    resolved[key] = decrypt_value(ref[6:])
                except Exception:
                    logger.warning("Failed to decrypt credential %s", key)
                    resolved[key] = ""
            elif ref.startswith("env:"):
                var_name = ref[4:]
                resolved[key] = os.environ.get(var_name, "")
            else:
                resolved[key] = ref
        return resolved

    def redact(self, credential_refs: dict[str, str]) -> dict[str, str]:
        """Return redacted view of credentials for API responses."""
        return {key: "••••••••" if ref else "" for key, ref in credential_refs.items()}
```

- [ ] **Step 2: Integrate vault into config generation**

In `src/finspark/api/routes/configurations.py`, in the `generate_configuration` function, after the auth config is set (around line 573 where `config.setdefault("auth", ...)`), add credential vault references:

```python
    # Add credential vault references (placeholder for user to fill)
    auth_config = config.get("auth", {})
    auth_config.setdefault("credentials", {
        "api_key": "env:ADAPTER_API_KEY",
        "api_secret": "env:ADAPTER_API_SECRET",
    })
    config["auth"] = auth_config
```

This replaces the empty `{}` with meaningful references that point to environment variables, making it clear how credentials should be configured.

- [ ] **Step 3: Run tests**

Run: `cd /home/akash/PROJECTS/finspark && python -m pytest tests/ -x -q --tb=short 2>&1 | tail -20`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/finspark/core/credentials.py src/finspark/api/routes/configurations.py
git commit -m "feat: add credential vault abstraction with env-var and encrypted storage"
```

---

## Task 7: Update Presentation with Architecture Details

**Files:**
- Modify: `docs/presentation/adaptconfig_slides.tex`

- [ ] **Step 1: Read current slides to understand structure**

Read: `docs/presentation/adaptconfig_slides.tex` — full file (already done in planning phase)

- [ ] **Step 2: Add detailed architecture slide after the current architecture slide (slide 4)**

After the current Architecture slide (line 164, before `% ── 5. AUTH`), add a new slide:

```latex
% ── 4b. ARCHITECTURE DEEP-DIVE ──────────────────────────────────────────────
\begin{frame}{Architecture Deep-Dive}
\begin{center}
{\scriptsize
\begin{tikzpicture}[
  box/.style={rectangle, draw=teal, fill=surface, minimum width=2.8cm, minimum height=0.7cm, text=tp, font=\scriptsize\bfseries, rounded corners=2pt},
  arrow/.style={->, thick, teal!60},
  label/.style={font=\tiny, text=ts},
]
  % Frontend
  \node[box, minimum width=10cm] (fe) at (0,3.5) {React 18 + TypeScript + TanStack Query};

  % Backend
  \node[box, minimum width=10cm] (be) at (0,2.2) {FastAPI — 34 Endpoints, JWT Auth, RBAC, Rate Limiter};

  % Services row
  \node[box] (parser) at (-4,0.8) {Document Parser};
  \node[box] (config) at (-0.7,0.8) {Config Engine};
  \node[box] (sim) at (2.5,0.8) {Simulator};
  \node[box] (events) at (5.5,0.8) {Event System};

  % Bottom row
  \node[box] (db) at (-2.5,-0.5) {SQLite / PostgreSQL};
  \node[box] (gemini) at (1.5,-0.5) {Gemini Flash LLM};
  \node[box] (webhooks) at (5.5,-0.5) {Webhook Delivery};

  % Arrows
  \draw[arrow] (fe) -- (be);
  \draw[arrow] (be) -- (parser);
  \draw[arrow] (be) -- (config);
  \draw[arrow] (be) -- (sim);
  \draw[arrow] (be) -- (events);
  \draw[arrow] (parser) -- (db);
  \draw[arrow] (config) -- (db);
  \draw[arrow] (config) -- (gemini);
  \draw[arrow] (parser) -- (gemini);
  \draw[arrow] (events) -- (webhooks);
\end{tikzpicture}
}
\end{center}
\end{frame}
```

- [ ] **Step 3: Add a "Config Generation Pipeline" slide**

After the new architecture slide, add:

```latex
% ── 4c. CONFIG PIPELINE ─────────────────────────────────────────────────────
\begin{frame}{Config Generation Pipeline}
\begin{columns}[T]
\begin{column}{0.48\textwidth}
\textbf{Hybrid AI + Rule Engine:}
\begin{enumerate}
\item \textcolor{teal}{Gemini Flash} generates config from document + adapter schema
\item \textbf{Rule engine} augments with:
  \begin{itemize}
  \item 100+ Indian fintech synonyms
  \item Fuzzy string matching (rapidfuzz)
  \item Jaccard token overlap
  \end{itemize}
\item Confidence scores from rule engine \textbf{override} LLM self-assessment
\item Unmapped fields \textbf{backfilled} from rule engine
\end{enumerate}
\end{column}
\begin{column}{0.48\textwidth}
\textbf{Graceful degradation:}\\[0.3em]
{\small
\begin{tabular}{ll}
\toprule
\textbf{Path} & \textbf{When} \\
\midrule
LLM + Rules & API key present \\
Rule fallback & LLM fails \\
Pure rules & AI disabled \\
\bottomrule
\end{tabular}
}

\vspace{0.5em}
\textbf{Field matching tiers:}\\[0.2em]
{\small
1. Exact synonym → \textcolor{ok}{1.0}\\
2. Fuzzy match → \textcolor{warn}{0.6--0.99}\\
3. Token Jaccard → \textcolor{warn}{0.6+}\\
}
\end{column}
\end{columns}
\end{frame}
```

- [ ] **Step 4: Add a "Rollback \& Version Control" slide**

```latex
% ── 4d. ROLLBACK ────────────────────────────────────────────────────────────
\begin{frame}{Rollback \& Version Control}
\begin{columns}[T]
\begin{column}{0.48\textwidth}
\textbf{Configuration Lifecycle:}
\begin{center}
{\small Draft $\to$ Configured $\to$ Validating $\to$ Testing $\to$ Active}
\end{center}

\vspace{0.3em}
\textbf{Version history:}
\begin{itemize}
\item Every change creates a \texttt{ConfigurationHistory} snapshot
\item Full state serialization (mappings, hooks, auth, rules)
\item One-click rollback to any previous version
\item Pre-rollback snapshot for safety
\end{itemize}
\end{column}
\begin{column}{0.48\textwidth}
\textbf{Diff engine:}
\begin{itemize}
\item Recursive dict/list comparison
\item Breaking change detection
\item Side-by-side version comparison
\item Export as JSON or YAML
\end{itemize}

\vspace{0.3em}
\textbf{Audit trail:}
\begin{itemize}
\item Immutable log of every action
\item Actor, timestamp, resource details
\item Per-user data isolation
\end{itemize}
\end{column}
\end{columns}
\end{frame}
```

- [ ] **Step 5: Add a "Database Schema" slide**

```latex
% ── 4e. DATABASE ────────────────────────────────────────────────────────────
\begin{frame}{Database Schema (11 Tables)}
\begin{center}
{\small
\begin{tabular}{llr}
\toprule
\textbf{Table} & \textbf{Purpose} & \textbf{Key Relations} \\
\midrule
\texttt{users} & Auth, RBAC (admin/editor/viewer) & tenant owner \\
\texttt{documents} & Uploaded specs (PDF/DOCX/YAML) & tenant-scoped \\
\texttt{adapters} & 9 pre-built adapters & global catalog \\
\texttt{adapter\_versions} & Multi-version per adapter & FK → adapters \\
\texttt{configurations} & Generated integration configs & FK → adapter\_ver, doc \\
\texttt{config\_history} & Versioned snapshots for rollback & FK → configurations \\
\texttt{simulations} & Test run results & FK → configurations \\
\texttt{simulation\_steps} & Per-step test details & FK → simulations \\
\texttt{audit\_logs} & Immutable action log & tenant-scoped \\
\texttt{webhooks} & Registered callback URLs & tenant-scoped \\
\texttt{webhook\_deliveries} & Delivery attempts \& status & FK → webhooks \\
\bottomrule
\end{tabular}
}
\end{center}
\vspace{0.3em}
{\small\centering UUID primary keys $\cdot$ Row-level tenant isolation $\cdot$ Alembic migrations $\cdot$ CASCADE deletes\par}
\end{frame}
```

- [ ] **Step 6: Compile presentation to verify**

Run: `cd /home/akash/PROJECTS/finspark/docs/presentation && pdflatex -interaction=nonstopmode adaptconfig_slides.tex 2>&1 | tail -10`
Expected: PDF generated successfully

- [ ] **Step 7: Commit**

```bash
git add docs/presentation/adaptconfig_slides.tex
git commit -m "docs: add architecture deep-dive, pipeline, rollback, and schema slides"
```

---

## Execution Order Summary

| Priority | Task | Issue | Effort |
|----------|------|-------|--------|
| 1 | Task 1: Webhook Event Delivery | #1 | ~15 min |
| 2 | Task 2: Editor Role Permissions | #2 | ~5 min |
| 3 | Task 4: Rollback Button Visibility | #4 | ~15 min |
| 4 | Task 3: Config/Simulation Delete | #3 | ~25 min |
| 5 | Task 5: LLM BRD Parsing | #5 | ~20 min |
| 6 | Task 6: Credential Vault | #6 | ~10 min |
| 7 | Task 7: Update Presentation | Pres | ~20 min |

Tasks 1-4 are the high-priority frontend-visible fixes. Tasks 5-6 are medium-priority backend enhancements. Task 7 is the presentation update.

Tasks 1, 2, and 6 are backend-only and can be parallelized. Task 3 spans backend + frontend. Task 4 is frontend-only. Tasks 5 and 7 are independent.
