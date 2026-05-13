"""Run a chain graph against mock responses, threading state between calls.

Execution model:
  1. Walk the topo order produced by build_chain_graph.
  2. For each node:
     a. Resolve inject templates against the shared context dict.
        Missing variables cause an "inject_failed" status — that node is
        marked failed and its downstream nodes get "blocked_by_upstream".
     b. Call mock_responses.generate_mock_response with the resolved request.
     c. Apply extract rules to the response; write values into the context.
        If a JSON path resolves to None, mark extraction_warning but don't
        fail — sometimes nullable fields are legitimately empty.
  3. After execution, do a BFS from any failed node to mark all transitively
     reachable not-yet-run nodes as "blocked_by_upstream" so the report shows
     one root cause, not N failures.

Returns a serializable dict suitable for stuffing into Simulation.results.
"""

from __future__ import annotations

import re
import time
from typing import Any

from finspark.services.chain.graph import ChainGraph, ChainNode
from finspark.services.simulation.mock_responses import generate_mock_response

_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_\-.]+)\s*\}\}")


def run_chain(
    graph: ChainGraph,
    adapter_name: str,
    base_url: str,
    seed_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute the chain end-to-end with mocked upstream responses.

    Returns a dict with the full timeline + summary stats. Shape:
      {
        "ok": bool,
        "summary": "...",
        "steps": [
          {id, status, request, response, extracted, injected, latency_ms, error}
        ],
        "context_final": {...},
        "blocked_root": "<id of the step whose failure cascaded>" or None,
      }
    """
    if not graph.is_valid():
        return {
            "ok": False,
            "summary": f"Chain invalid — {graph.cycle_error or 'no nodes'}",
            "steps": [],
            "context_final": {},
            "blocked_root": None,
            "cycle_error": graph.cycle_error,
        }

    context: dict[str, Any] = dict(seed_payload or {})
    step_records: dict[str, dict[str, Any]] = {}

    for node_id in graph.topo_order():
        node = graph.nodes[node_id]
        step_records[node_id] = _execute_node(node, context, adapter_name, base_url)

    # Cascade analysis. A node that "failed" due to inject_failed AND has at
    # least one root-cause upstream (failed or mock_contract_violation) is
    # really blocked, not independently broken. Reassign its status so the
    # report shows one root cause instead of N.
    root_cause_ids = {
        nid for nid, rec in step_records.items()
        if rec["status"] in ("failed", "mock_contract_violation")
    }
    for nid, rec in step_records.items():
        if nid in root_cause_ids and rec["status"] == "failed":
            ancestors = _ancestors_of(graph, nid)
            culpable = sorted(ancestors & root_cause_ids)
            if culpable and (rec.get("error") or "").startswith("inject_failed"):
                rec["status"] = "blocked_by_upstream"
                rec["blocked_by"] = culpable

    # Recount after reassignment.
    total = len(step_records)
    passed = sum(1 for r in step_records.values() if r["status"] == "passed")
    failed = sum(1 for r in step_records.values() if r["status"] == "failed")
    contract = sum(1 for r in step_records.values() if r["status"] == "mock_contract_violation")
    blocked = sum(1 for r in step_records.values() if r["status"] == "blocked_by_upstream")

    if failed + contract + blocked == 0:
        summary = f"All {total} step(s) passed"
        root = None
    else:
        topo = graph.topo_order()
        root = next(
            (nid for nid in topo if step_records[nid]["status"] in ("failed", "mock_contract_violation")),
            None,
        )
        parts = [f"{passed}/{total} passed"]
        if failed:
            parts.append(f"{failed} failed")
        if contract:
            parts.append(f"{contract} mock-contract violation(s)")
        if blocked:
            parts.append(f"{blocked} blocked by upstream")
        summary = ", ".join(parts)

    return {
        "ok": (failed + contract + blocked) == 0,
        "summary": summary,
        "steps": [step_records[nid] for nid in graph.topo_order()],
        "context_final": context,
        "blocked_root": root,
        "counts": {
            "total": total, "passed": passed, "failed": failed,
            "mock_contract_violation": contract, "blocked_by_upstream": blocked,
        },
    }


# ── per-node execution ───────────────────────────────────────────────────────


def _execute_node(
    node: ChainNode,
    context: dict[str, Any],
    adapter_name: str,
    base_url: str,
) -> dict[str, Any]:
    """Resolve injects -> call mock -> apply extracts. Returns one step record."""
    started = time.perf_counter()

    # 1. Resolve injects from context. Missing vars -> step fails.
    request = {"headers": {}, "query": {}, "path_params": {}, "body": {}}
    injected: list[dict[str, Any]] = []
    missing_vars: list[str] = []

    for inj in node.inject:
        template = inj.get("template", "")
        location = inj.get("location", "header")
        target_field = inj.get("target_field", "")
        resolved, missing = _resolve_template(template, context)
        injected.append({
            "template": template,
            "location": location,
            "target_field": target_field,
            "resolved": resolved,
            "missing_vars": missing,
        })
        missing_vars.extend(missing)

        if missing:
            continue  # Don't write a half-resolved value into the request.

        bucket = {
            "header": request["headers"],
            "query": request["query"],
            "path": request["path_params"],
            "body": request["body"],
        }.get(location, request["headers"])
        if target_field:
            bucket[target_field] = resolved

    if missing_vars:
        latency_ms = round((time.perf_counter() - started) * 1000)
        return {
            "id": node.id,
            "path": node.path,
            "method": node.method,
            "description": node.description,
            "status": "failed",
            "error": f"inject_failed: missing context vars {missing_vars}",
            "request": request,
            "response": None,
            "extracted": [],
            "injected": injected,
            "latency_ms": latency_ms,
        }

    # 2. Resolve {placeholder} in path from request.path_params.
    resolved_path = node.path
    for k, v in request["path_params"].items():
        resolved_path = resolved_path.replace("{" + k + "}", str(v))

    # 3. Mock call.
    try:
        response = generate_mock_response(
            adapter_name=adapter_name,
            endpoint_path=resolved_path,
            request_payload=request["body"],
            base_url=base_url,
        )
    except Exception as exc:  # noqa: BLE001
        latency_ms = round((time.perf_counter() - started) * 1000)
        return {
            "id": node.id, "path": node.path, "method": node.method,
            "description": node.description,
            "status": "failed",
            "error": f"mock_error: {exc}",
            "request": request, "response": None,
            "extracted": [], "injected": injected,
            "latency_ms": latency_ms,
        }

    # 4. Apply extracts → context. Missing fields are honest signal:
    # if the mock returns 200 but doesn't contain the declared value,
    # the chain test surfaces it as `mock_contract_violation` instead of
    # pretending everything's fine. Tells the user the chain wiring is OK,
    # the mock data isn't.
    extracted_records: list[dict[str, Any]] = []
    missing_extracts: list[str] = []
    for rule in node.extract:
        json_path = rule.get("json_path", "")
        save_as = rule.get("save_as", "")
        if not save_as:
            continue
        value, found = _read_json_path(response, json_path or save_as)
        extracted_records.append({
            "json_path": json_path,
            "save_as": save_as,
            "value": value if found else None,
            "found": found,
        })
        if found:
            context[save_as] = value
        else:
            missing_extracts.append(save_as)

    latency_ms = round((time.perf_counter() - started) * 1000)
    if missing_extracts:
        status = "mock_contract_violation"
        error = (
            f"mock_response missing declared extract fields: {missing_extracts} "
            f"— chain wiring is fine, but the mock for this endpoint doesn't "
            f"return what later steps expect."
        )
    else:
        status = "passed"
        error = None

    return {
        "id": node.id,
        "path": node.path,
        "method": node.method,
        "description": node.description,
        "status": status,
        "error": error,
        "request": {**request, "resolved_path": resolved_path},
        "response": response,
        "extracted": extracted_records,
        "injected": injected,
        "latency_ms": latency_ms,
    }


# ── helpers ──────────────────────────────────────────────────────────────────


def _resolve_template(template: str, context: dict[str, Any]) -> tuple[str, list[str]]:
    """Replace {{var}} occurrences with context values; return (resolved, missing_vars)."""
    missing: list[str] = []

    def repl(m: re.Match[str]) -> str:
        var = m.group(1)
        value, found = _read_json_path(context, var)
        if not found:
            missing.append(var)
            return m.group(0)  # leave the {{var}} in place so the human sees what failed
        return str(value)

    return _TEMPLATE_VAR_RE.sub(repl, template), missing


def _read_json_path(data: Any, path: str) -> tuple[Any, bool]:
    """Walk a dotted JSON path; return (value, found). Supports list indices."""
    if not path:
        return data, True
    cur: Any = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
            continue
        if isinstance(cur, list):
            try:
                idx = int(part)
                cur = cur[idx]
                continue
            except (ValueError, IndexError):
                return None, False
        return None, False
    return cur, True


def _reachable_from(graph: ChainGraph, starts: list[str]) -> set[str]:
    """BFS over data/auth edges from a set of start nodes."""
    adj: dict[str, list[str]] = {nid: [] for nid in graph.nodes}
    for e in graph.edges:
        if e.kind in ("data", "auth"):
            adj[e.source].append(e.target)
    seen: set[str] = set(starts)
    queue: list[str] = list(starts)
    while queue:
        n = queue.pop(0)
        for child in adj[n]:
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return seen


def _ancestors_of(graph: ChainGraph, target: str) -> set[str]:
    """Return ids of all nodes that transitively feed into `target`."""
    rev: dict[str, list[str]] = {nid: [] for nid in graph.nodes}
    for e in graph.edges:
        if e.kind in ("data", "auth"):
            rev[e.target].append(e.source)
    seen: set[str] = set()
    queue: list[str] = list(rev[target])
    while queue:
        n = queue.pop(0)
        if n in seen:
            continue
        seen.add(n)
        queue.extend(rev[n])
    return seen
