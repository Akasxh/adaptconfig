"""Sequential chain executor for integration configurations.

This is the MVP slice of issue #109. It deliberately stays small:

* Topological sort with cycle detection (Kahn's algorithm).
* JSONPath ``extract`` from a previous step's response.
* Dotted-path ``inject`` into the next step's request payload.
* Runs against the existing ``MockAPIServer`` -- no real HTTP, no concurrency.

Out of scope for now: cyclic graphs, async / event-driven steps, conditional
branching, parallel forks. Those are explicitly deferred to follow-ups.
"""
from __future__ import annotations

import time
from collections import deque
from typing import TYPE_CHECKING, Any

from finspark.schemas.simulations import SimulationStepResult
from finspark.services.chain.errors import ChainCycleError
from finspark.services.chain.jsonpath import extract_path, set_path

if TYPE_CHECKING:  # pragma: no cover - typing only
    from finspark.services.simulation.simulator import MockAPIServer


def _has_chain_metadata(endpoint: dict[str, Any]) -> bool:
    """A single endpoint is part of a chain iff it declares ``depends_on``."""
    deps = endpoint.get("depends_on")
    if isinstance(deps, list):
        return any(bool(d) for d in deps)
    return bool(deps)


def is_chain(endpoints: list[dict[str, Any]] | None) -> bool:
    """True iff this config should be executed via the chain runtime.

    The MVP only takes over when the config has 2+ endpoints AND at least
    one of them declares ``depends_on``. Single-endpoint configs and configs
    without any ``depends_on`` go through the existing per-endpoint test
    path unchanged.
    """
    if not endpoints or len(endpoints) < 2:
        return False
    return any(_has_chain_metadata(ep) for ep in endpoints if isinstance(ep, dict))


def _normalise_dependencies(value: Any) -> list[str]:
    """``depends_on`` may be a single id or a list. Normalise to list[str]."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v not in (None, "")]
    return [str(value)] if value not in (None, "") else []


def _endpoint_id(endpoint: dict[str, Any], index: int) -> str:
    """Stable id for an endpoint -- declared ``id`` wins, otherwise positional."""
    raw = endpoint.get("id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return f"step_{index}"


def topological_sort(endpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return ``endpoints`` ordered so every step's dependencies precede it.

    Raises :class:`ChainCycleError` when:

    * the same ``id`` appears twice;
    * a ``depends_on`` references an id that does not exist;
    * the graph contains a cycle.

    Endpoints with the same dependency depth keep their original list order
    so behaviour is deterministic for chain runs that have no inter-step
    ordering constraints.
    """
    # Index endpoints by id, preserving original ordering for tie-breaking.
    indexed: list[tuple[str, dict[str, Any]]] = []
    seen_ids: set[str] = set()
    for i, ep in enumerate(endpoints):
        if not isinstance(ep, dict):
            raise ChainCycleError(f"Endpoint at position {i} is not an object")
        eid = _endpoint_id(ep, i)
        if eid in seen_ids:
            raise ChainCycleError(f"Duplicate endpoint id: {eid!r}")
        seen_ids.add(eid)
        indexed.append((eid, ep))

    id_to_pos = {eid: pos for pos, (eid, _) in enumerate(indexed)}

    # Build adjacency + in-degrees.
    in_degree: dict[str, int] = {eid: 0 for eid, _ in indexed}
    successors: dict[str, list[str]] = {eid: [] for eid, _ in indexed}

    for eid, ep in indexed:
        for dep in _normalise_dependencies(ep.get("depends_on")):
            if dep not in id_to_pos:
                raise ChainCycleError(
                    f"Endpoint {eid!r} depends on unknown id {dep!r}"
                )
            if dep == eid:
                raise ChainCycleError(f"Endpoint {eid!r} cannot depend on itself")
            successors[dep].append(eid)
            in_degree[eid] += 1

    # Kahn's algorithm with a stable queue (lowest original index wins).
    ready = deque(
        sorted([eid for eid, deg in in_degree.items() if deg == 0], key=id_to_pos.get)
    )
    ordered: list[dict[str, Any]] = []
    while ready:
        eid = ready.popleft()
        ordered.append(indexed[id_to_pos[eid]][1])
        for nxt in successors[eid]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                # Insert preserving original order to keep determinism.
                _insert_sorted(ready, nxt, id_to_pos)

    if len(ordered) != len(indexed):
        unresolved = sorted(
            (eid for eid, deg in in_degree.items() if deg > 0),
            key=id_to_pos.get,
        )
        raise ChainCycleError(
            "Cycle detected in endpoint chain involving: " + ", ".join(unresolved)
        )

    return ordered


def _insert_sorted(
    queue: deque[str], item: str, id_to_pos: dict[str, int]
) -> None:
    """Insert ``item`` into ``queue`` in original-index order."""
    target = id_to_pos[item]
    inserted = False
    for i, existing in enumerate(queue):
        if id_to_pos[existing] > target:
            queue.insert(i, item)
            inserted = True
            break
    if not inserted:
        queue.append(item)


def _normalise_extracts(value: Any) -> list[dict[str, str]]:
    """``extract`` may be a list of ``{name, path}`` dicts or a single dict.

    Returns a list of ``{"name": str, "path": str}``.
    """
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        path = item.get("path") or item.get("from") or item.get("source")
        name = item.get("name") or item.get("as") or item.get("target") or path
        if not name or not path:
            continue
        out.append({"name": str(name), "path": str(path)})
    return out


def _normalise_injects(value: Any) -> list[dict[str, Any]]:
    """``inject`` may be a list of ``{from, to, source}`` dicts or a single dict.

    Returns a list of ``{"source": str, "target": str, "value": Any | None}``
    where ``source`` references a previously-extracted name (may be empty if
    a literal ``value`` is given) and ``target`` is the dotted path inside
    the next request payload to write into.
    """
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        target = item.get("to") or item.get("target") or item.get("into") or item.get("name")
        source = item.get("from") or item.get("source") or item.get("ref")
        literal = item.get("value")
        if not target:
            continue
        out.append(
            {
                "source": str(source) if source else "",
                "target": str(target),
                "value": literal,
            }
        )
    return out


class ChainExecutor:
    """Run a sequence of endpoints in topological order.

    Designed to plug into :class:`IntegrationSimulator`: the simulator keeps
    its rule-based config-shape checks, but when a chain is detected the
    per-endpoint loop is replaced by :meth:`run`. Each step still appears as
    a :class:`SimulationStepResult` so the existing UI / DB persistence keeps
    working without changes.
    """

    def __init__(self, mock_server: "MockAPIServer") -> None:
        self.mock_server = mock_server

    @staticmethod
    def is_chain(endpoints: list[dict[str, Any]] | None) -> bool:
        return is_chain(endpoints)

    @staticmethod
    def topological_sort(
        endpoints: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return topological_sort(endpoints)

    def run(
        self,
        endpoints: list[dict[str, Any]],
        config: dict[str, Any],
        base_request: dict[str, Any] | None = None,
    ) -> list[SimulationStepResult]:
        """Execute the chain. Raises :class:`ChainCycleError` on bad input.

        ``base_request`` is the sample payload built from field mappings;
        each step starts from a copy of it so unrelated fields are still
        present, then injected values from prior steps overwrite the
        relevant slots.
        """
        ordered = topological_sort(endpoints)
        responses: dict[str, dict[str, Any]] = {}
        extracted_values: dict[str, Any] = {}
        results: list[SimulationStepResult] = []

        for index, endpoint in enumerate(ordered):
            if not endpoint.get("enabled", True):
                continue
            step_id = _endpoint_id(endpoint, index)
            payload = dict(base_request or {})

            injects = _normalise_injects(endpoint.get("inject"))
            applied_injects: list[dict[str, Any]] = []
            for inject in injects:
                source = inject["source"]
                if source and source in extracted_values:
                    value = extracted_values[source]
                elif inject["value"] is not None:
                    value = inject["value"]
                else:
                    # Source missing -- record but don't fail the whole chain.
                    applied_injects.append(
                        {"target": inject["target"], "from": source, "applied": False}
                    )
                    continue
                set_path(payload, inject["target"], value)
                applied_injects.append(
                    {"target": inject["target"], "from": source, "applied": True}
                )

            start = time.monotonic()
            response = self.mock_server.generate_response(
                endpoint, payload, config=config
            )
            duration = max(1, int((time.monotonic() - start) * 1000))
            responses[step_id] = response

            extracts = _normalise_extracts(endpoint.get("extract"))
            applied_extracts: list[dict[str, Any]] = []
            for extract in extracts:
                value = extract_path(response, extract["path"])
                extracted_values[extract["name"]] = value
                applied_extracts.append(
                    {
                        "name": extract["name"],
                        "path": extract["path"],
                        "found": value is not None,
                    }
                )

            has_status = isinstance(response, dict) and "status" in response
            path_label = endpoint.get("path", step_id)
            confidence = 0.9 if has_status else 0.4

            results.append(
                SimulationStepResult(
                    step_name=f"chain_step_{step_id}_{path_label}",
                    status="passed" if has_status else "failed",
                    request_payload=payload,
                    expected_response={"status": "success"},
                    actual_response=response if isinstance(response, dict) else {"value": response},
                    duration_ms=duration,
                    confidence_score=confidence,
                    assertions=[
                        {"type": "chain_inject", "items": applied_injects},
                        {"type": "chain_extract", "items": applied_extracts},
                    ],
                )
            )

        return results
