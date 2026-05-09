"""Static analysis of workflow graph definitions.

Validates structural correctness at workflow creation time
using Tarjan's SCC algorithm for cycle detection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GraphValidationResult:
    valid: bool
    errors: list[str]
    warnings: list[str]
    cycles_detected: list[list[str]]
    unreachable_nodes: list[str]


class GraphValidator:
    """Validate a workflow definition dict for structural correctness."""

    def validate(self, definition: dict[str, Any]) -> GraphValidationResult:
        """Run all validations on the workflow definition."""
        errors: list[str] = []
        warnings: list[str] = []

        nodes = definition.get("nodes", {})
        initial_state = definition.get("initial_state")
        node_ids = set(nodes.keys())

        # 1. Verify initial_state exists
        if not initial_state:
            errors.append("Missing initial_state in workflow definition")
        elif initial_state not in node_ids:
            errors.append(
                f"initial_state '{initial_state}' does not exist in nodes"
            )

        # 2. Verify at least one terminal node
        terminals = self._find_terminals(nodes)
        if not terminals:
            errors.append("No terminal node found in workflow definition")

        # 3. Verify all transitions reference existing nodes
        bad_refs = self._check_transition_refs(nodes, node_ids)
        errors.extend(bad_refs)

        # 4. Detect cycles with Tarjan's SCC
        adjacency = self._build_adjacency(nodes)
        sccs = _tarjan_scc(node_ids, adjacency)
        cycles = [scc for scc in sccs if len(scc) > 1]

        # 5. For each cycle, verify at least one node has max_visits
        cycle_errors = self._check_cycle_safety(cycles, nodes)
        errors.extend(cycle_errors)

        # 6. Check reachability from initial_state
        unreachable: list[str] = []
        if initial_state and initial_state in node_ids:
            reachable = self._reachable_from(initial_state, adjacency)
            unreachable = sorted(node_ids - reachable)
            if unreachable:
                warnings.append(
                    f"Unreachable nodes: {', '.join(unreachable)}"
                )

        return GraphValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            cycles_detected=cycles,
            unreachable_nodes=unreachable,
        )

    # -- helpers --

    @staticmethod
    def _find_terminals(nodes: dict[str, Any]) -> list[str]:
        """Find nodes marked as terminal."""
        return [
            nid for nid, ndef in nodes.items()
            if ndef.get("terminal", False)
        ]

    @staticmethod
    def _check_transition_refs(
        nodes: dict[str, Any], node_ids: set[str],
    ) -> list[str]:
        """Check that all transition targets reference existing nodes."""
        errors: list[str] = []
        for nid, ndef in nodes.items():
            for tr in ndef.get("transitions", []):
                target = tr.get("target")
                if target and target not in node_ids:
                    errors.append(
                        f"Node '{nid}' transitions to "
                        f"non-existent node '{target}'"
                    )
            # Also check on_max_visits target
            on_max = ndef.get("on_max_visits")
            if on_max and on_max not in node_ids:
                errors.append(
                    f"Node '{nid}' on_max_visits target "
                    f"'{on_max}' does not exist"
                )
        return errors

    @staticmethod
    def _build_adjacency(
        nodes: dict[str, Any],
    ) -> dict[str, list[str]]:
        """Build adjacency list from node transitions."""
        adj: dict[str, list[str]] = {}
        for nid, ndef in nodes.items():
            targets: list[str] = []
            for tr in ndef.get("transitions", []):
                t = tr.get("target")
                if t:
                    targets.append(t)
            on_max = ndef.get("on_max_visits")
            if on_max:
                targets.append(on_max)
            adj[nid] = targets
        return adj

    @staticmethod
    def _check_cycle_safety(
        cycles: list[list[str]], nodes: dict[str, Any],
    ) -> list[str]:
        """For each cycle, verify at least one node has max_visits."""
        errors: list[str] = []
        for cycle in cycles:
            has_guard = any(
                nodes.get(nid, {}).get("max_visits") is not None
                for nid in cycle
            )
            if not has_guard:
                errors.append(
                    f"Cycle {cycle} has no node with max_visits set "
                    f"(infinite loop risk)"
                )
        return errors

    @staticmethod
    def _reachable_from(
        start: str, adjacency: dict[str, list[str]],
    ) -> set[str]:
        """BFS to find all reachable nodes from start."""
        visited: set[str] = set()
        queue = [start]
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            for neighbor in adjacency.get(node, []):
                if neighbor not in visited:
                    queue.append(neighbor)
        return visited


def _tarjan_scc(
    node_ids: set[str],
    adjacency: dict[str, list[str]],
) -> list[list[str]]:
    """Tarjan's strongly connected components algorithm.

    Returns list of SCCs (each SCC is a list of node IDs).
    Single-node SCCs are included.
    """
    index_counter = [0]
    stack: list[str] = []
    on_stack: set[str] = set()
    index_map: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    result: list[list[str]] = []

    def strongconnect(v: str) -> None:
        index_map[v] = index_counter[0]
        lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)

        for w in adjacency.get(v, []):
            if w not in index_map:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], index_map[w])

        if lowlink[v] == index_map[v]:
            component: list[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                component.append(w)
                if w == v:
                    break
            result.append(component)

    for node in sorted(node_ids):
        if node not in index_map:
            strongconnect(node)

    return result
