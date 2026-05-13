"""Dependency DAG construction for an API chain.

Each endpoint becomes a node; an edge `A -> B` exists when B `depends_on` A
or when one of B's inject templates references a value that A `extract`s.

The graph isn't strictly acyclic in real life — polling endpoints have a
self-edge, and "compensating" calls (refunds) point upstream. To keep the
topological sort honest, we *classify* edges into types:
  - data:         B needs a value A extracted. Counted in toposort.
  - auth:         the access-token fan-out. Counted in toposort.
  - polling:      A -> A self-edge, unrolled at execution time.
  - compensates:  reverse edge; fired only on failure. NOT in toposort.

A cycle in the (data | auth) subgraph is a config error and we surface it
before running anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_\-.]+)\s*\}\}")
_PATH_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_\-]+)\}")


@dataclass
class ChainNode:
    """One endpoint in the chain.

    Stored as plain dicts (not Pydantic models) so the graph can be JSON-
    serialized straight into simulations.results without round-tripping.
    """

    id: str
    path: str
    method: str
    description: str
    depends_on: list[str]
    extract: list[dict[str, str]]  # {json_path, save_as}
    inject: list[dict[str, str]]   # {template, location, target_field}


@dataclass
class ChainEdge:
    """Directed edge from `source` -> `target` carrying a typed reason."""

    source: str
    target: str
    kind: str       # data | auth | polling | compensates
    via: str = ""   # the context variable name that flows along this edge


@dataclass
class ChainGraph:
    nodes: dict[str, ChainNode] = field(default_factory=dict)
    edges: list[ChainEdge] = field(default_factory=list)
    cycle_error: str | None = None  # set when a data/auth cycle is detected
    layers: list[list[str]] = field(default_factory=list)  # toposort, grouped by depth

    def is_valid(self) -> bool:
        return self.cycle_error is None and bool(self.nodes)

    def edges_into(self, node_id: str) -> list[ChainEdge]:
        return [e for e in self.edges if e.target == node_id and e.kind != "compensates"]

    def topo_order(self) -> list[str]:
        """Flat list of node ids in execution order (deterministic per declaration)."""
        return [nid for layer in self.layers for nid in layer]


def build_chain_graph(endpoints: list[dict[str, Any]]) -> ChainGraph:
    """Construct a ChainGraph from a list of endpoint dicts.

    `endpoints` is the same shape stored in `Configuration.full_config["endpoints"]`
    or `ParsedDocumentResult.endpoints` after `enrich_chain_metadata` ran:
    each dict has id, path, method, depends_on, extract, inject.

    Returns a graph; if a data/auth cycle is present, graph.cycle_error is set
    and graph.is_valid() is False — caller should surface as a config error
    before attempting execution.
    """
    graph = ChainGraph()
    if not endpoints:
        return graph

    # 1. Materialize nodes. Skip entries that lack a usable id.
    for ep in endpoints:
        node_id = (ep.get("id") or "").strip()
        if not node_id:
            continue
        graph.nodes[node_id] = ChainNode(
            id=node_id,
            path=ep.get("path", ""),
            method=(ep.get("method") or "GET").upper(),
            description=ep.get("description", "") or "",
            depends_on=list(ep.get("depends_on") or []),
            extract=[dict(r) for r in (ep.get("extract") or [])],
            inject=[dict(r) for r in (ep.get("inject") or [])],
        )

    # 2. Edges from explicit depends_on. Ignore references to unknown nodes.
    saved_by = _build_saved_by_index(graph)  # save_as -> producing node id
    for node in graph.nodes.values():
        for dep in node.depends_on:
            if dep in graph.nodes and dep != node.id:
                graph.edges.append(ChainEdge(
                    source=dep, target=node.id, kind="data"
                ))

    # 3. Edges inferred from inject templates that reference save_as values.
    seen_pairs = {(e.source, e.target) for e in graph.edges}
    for node in graph.nodes.values():
        for inj in node.inject:
            tmpl = inj.get("template") or ""
            for var in _TEMPLATE_VAR_RE.findall(tmpl):
                producer = saved_by.get(var.split(".")[0])
                if not producer or producer == node.id:
                    continue
                kind = "auth" if _looks_like_auth_var(var) else "data"
                if (producer, node.id) not in seen_pairs:
                    graph.edges.append(ChainEdge(
                        source=producer, target=node.id, kind=kind, via=var
                    ))
                    seen_pairs.add((producer, node.id))

    # 4. Cycle detection on (data | auth) edges only.
    cycle = _find_cycle(graph)
    if cycle:
        graph.cycle_error = f"cycle detected in data flow: {' -> '.join(cycle)} -> {cycle[0]}"
        return graph

    # 5. Topological layering (Kahn's algorithm); preserve declaration order on ties.
    graph.layers = _topological_layers(graph)
    return graph


# ── private helpers ──────────────────────────────────────────────────────────


def _build_saved_by_index(graph: ChainGraph) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in graph.nodes.values():
        for r in node.extract:
            key = (r.get("save_as") or "").strip()
            if key and key not in out:
                out[key] = node.id
    return out


def _looks_like_auth_var(var: str) -> bool:
    v = var.lower()
    return "access_token" in v or v == "token" or "bearer" in v


def _find_cycle(graph: ChainGraph) -> list[str] | None:
    """Return a cycle (list of node ids) in the data/auth subgraph, else None.

    Uses DFS with three-color marking. Compensating edges are excluded.
    """
    color: dict[str, int] = {nid: 0 for nid in graph.nodes}  # 0=white, 1=gray, 2=black
    parent: dict[str, str | None] = {nid: None for nid in graph.nodes}
    adj: dict[str, list[str]] = {nid: [] for nid in graph.nodes}
    for e in graph.edges:
        if e.kind in ("data", "auth"):
            adj[e.source].append(e.target)

    def dfs(start: str) -> list[str] | None:
        stack: list[tuple[str, int]] = [(start, 0)]
        while stack:
            nid, idx = stack[-1]
            if idx == 0:
                color[nid] = 1
            children = adj[nid]
            if idx < len(children):
                stack[-1] = (nid, idx + 1)
                nxt = children[idx]
                if color[nxt] == 1:
                    # Found a back edge — reconstruct the cycle.
                    cyc = [nxt]
                    cur = nid
                    while cur is not None and cur != nxt:
                        cyc.append(cur)
                        cur = parent[cur]
                    cyc.reverse()
                    return cyc
                if color[nxt] == 0:
                    parent[nxt] = nid
                    stack.append((nxt, 0))
            else:
                color[nid] = 2
                stack.pop()
        return None

    for nid in graph.nodes:
        if color[nid] == 0:
            result = dfs(nid)
            if result:
                return result
    return None


def _topological_layers(graph: ChainGraph) -> list[list[str]]:
    """Group nodes by depth so the UI can lay them out top-to-bottom.

    Preserves dict-insertion order (which mirrors the source spec's endpoint
    order) within a layer so two runs of the same chain look identical.
    """
    in_deg: dict[str, int] = {nid: 0 for nid in graph.nodes}
    adj: dict[str, list[str]] = {nid: [] for nid in graph.nodes}
    for e in graph.edges:
        if e.kind in ("data", "auth"):
            adj[e.source].append(e.target)
            in_deg[e.target] += 1

    layers: list[list[str]] = []
    # Use declaration order (dict iter is insertion-ordered in Py3.7+) so layers
    # are deterministic across runs.
    ready = [nid for nid in graph.nodes if in_deg[nid] == 0]
    while ready:
        layers.append(ready)
        next_ready: list[str] = []
        for nid in ready:
            for child in adj[nid]:
                in_deg[child] -= 1
                if in_deg[child] == 0:
                    next_ready.append(child)
        ready = next_ready
    return layers
