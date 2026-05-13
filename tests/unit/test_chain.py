"""Tests for the chain DAG: heuristics, graph construction, executor."""

from __future__ import annotations

from finspark.schemas.documents import ExtractedEndpoint, ExtractRule, InjectRule
from finspark.services.chain.executor import run_chain
from finspark.services.chain.graph import build_chain_graph
from finspark.services.chain.heuristics import (
    enrich_chain_metadata,
    normalize_endpoints_for_chain,
)


# ── Heuristic backfill ───────────────────────────────────────────────────────


class TestHeuristics:
    def test_path_placeholder_implies_inject_and_dependency(self) -> None:
        eps = [
            ExtractedEndpoint(id="a", path="/score", method="POST",
                              extract=[ExtractRule(json_path="enquiry_id", save_as="enquiry_id")]),
            ExtractedEndpoint(id="b", path="/report/{enquiry_id}", method="GET"),
        ]
        enrich_chain_metadata(eps)
        assert "a" in eps[1].depends_on
        path_injects = [i for i in eps[1].inject if i.location == "path"]
        assert len(path_injects) == 1
        assert path_injects[0].target_field == "enquiry_id"

    def test_auth_fans_out_to_every_later_call(self) -> None:
        eps = [
            ExtractedEndpoint(id="oauth", path="/oauth/token", method="POST",
                              extract=[ExtractRule(json_path="access_token", save_as="access_token")]),
            ExtractedEndpoint(id="b", path="/foo", method="GET"),
            ExtractedEndpoint(id="c", path="/bar", method="GET"),
        ]
        enrich_chain_metadata(eps)
        for ep in (eps[1], eps[2]):
            assert "oauth" in ep.depends_on
            auth = [i for i in ep.inject if i.target_field.lower() == "authorization"]
            assert len(auth) == 1
            assert "{{access_token}}" in auth[0].template

    def test_does_not_double_inject_when_already_present(self) -> None:
        eps = [
            ExtractedEndpoint(id="oauth", path="/oauth/token", method="POST",
                              extract=[ExtractRule(json_path="access_token", save_as="access_token")]),
            ExtractedEndpoint(id="b", path="/foo", method="GET",
                              inject=[InjectRule(template="Token {{access_token}}", location="header", target_field="Authorization")]),
        ]
        enrich_chain_metadata(eps)
        auth = [i for i in eps[1].inject if i.target_field == "Authorization"]
        assert len(auth) == 1
        # Preserved exactly — heuristic must not stomp on explicit metadata.
        assert auth[0].template == "Token {{access_token}}"

    def test_normalize_assigns_missing_ids(self) -> None:
        raw = [
            {"path": "/oauth/token", "method": "POST",
             "extract": [{"save_as": "access_token", "json_path": "access_token"}]},
            {"path": "/credit-report/{enquiry_id}", "method": "GET"},
        ]
        out = normalize_endpoints_for_chain(raw)
        ids = [e["id"] for e in out]
        assert all(i for i in ids)        # no empty ids
        assert len(set(ids)) == len(ids)  # unique


# ── Graph construction ──────────────────────────────────────────────────────


class TestGraph:
    def _three_step(self) -> list[dict]:
        return [
            {"id": "auth", "path": "/oauth/token", "method": "POST",
             "extract": [{"json_path": "access_token", "save_as": "access_token"}], "inject": []},
            {"id": "score", "path": "/credit-score", "method": "POST", "depends_on": ["auth"],
             "extract": [{"json_path": "enquiry_id", "save_as": "enquiry_id"}],
             "inject": [{"template": "Bearer {{access_token}}", "location": "header", "target_field": "Authorization"}]},
            {"id": "report", "path": "/credit-report/{enquiry_id}", "method": "GET", "depends_on": ["score"],
             "inject": [
                 {"template": "{{enquiry_id}}", "location": "path", "target_field": "enquiry_id"},
                 {"template": "Bearer {{access_token}}", "location": "header", "target_field": "Authorization"},
             ]},
        ]

    def test_topo_layers_are_linear_for_a_linear_chain(self) -> None:
        g = build_chain_graph(self._three_step())
        assert g.layers == [["auth"], ["score"], ["report"]]
        assert g.cycle_error is None

    def test_auth_edge_classified_separately_from_data(self) -> None:
        g = build_chain_graph(self._three_step())
        # auth -> report exists because report injects {{access_token}}.
        e_auth = [e for e in g.edges if e.source == "auth" and e.target == "report"]
        assert e_auth and e_auth[0].kind == "auth"
        # auth -> score is a data edge (declared depends_on).
        e_data = [e for e in g.edges if e.source == "auth" and e.target == "score"]
        assert e_data and e_data[0].kind == "data"

    def test_data_cycle_is_surfaced_as_config_error(self) -> None:
        eps = [
            {"id": "a", "path": "/a", "method": "GET", "depends_on": ["b"]},
            {"id": "b", "path": "/b", "method": "GET", "depends_on": ["a"]},
        ]
        g = build_chain_graph(eps)
        assert g.cycle_error is not None
        assert g.is_valid() is False

    def test_unknown_dependency_ignored(self) -> None:
        eps = [
            {"id": "a", "path": "/a", "method": "GET", "depends_on": ["does_not_exist"]},
        ]
        g = build_chain_graph(eps)
        # Graph still valid; the bogus reference is dropped, not crashed on.
        assert g.is_valid()
        assert g.layers == [["a"]]


# ── Executor (mock-backed) ──────────────────────────────────────────────────


class TestExecutor:
    def _cibil_chain(self) -> list[dict]:
        return [
            {"id": "auth", "path": "/oauth/token", "method": "POST",
             "extract": [{"json_path": "access_token", "save_as": "access_token"}]},
            {"id": "score", "path": "/credit-score", "method": "POST", "depends_on": ["auth"],
             "extract": [{"json_path": "enquiry_id", "save_as": "enquiry_id"}],
             "inject": [{"template": "Bearer {{access_token}}", "location": "header", "target_field": "Authorization"}]},
            {"id": "report", "path": "/credit-report/{enquiry_id}", "method": "GET", "depends_on": ["score"],
             "inject": [
                 {"template": "{{enquiry_id}}", "location": "path", "target_field": "enquiry_id"},
                 {"template": "Bearer {{access_token}}", "location": "header", "target_field": "Authorization"},
             ]},
        ]

    def test_happy_path_threads_state_through_chain(self) -> None:
        g = build_chain_graph(self._cibil_chain())
        result = run_chain(g, adapter_name="CIBIL Credit Bureau", base_url="https://api.cibil.com/v1")
        assert result["ok"] is True
        assert all(s["status"] == "passed" for s in result["steps"])
        # The third step's path should have been resolved with the real enquiry_id.
        report_step = next(s for s in result["steps"] if s["id"] == "report")
        assert "ENQ" in report_step["request"]["resolved_path"]
        # Context accumulated the extractions.
        assert "access_token" in result["context_final"]
        assert "enquiry_id" in result["context_final"]

    def test_mock_contract_violation_when_extract_path_misses(self) -> None:
        # Wrong json_path on the auth extract — mock won't have that key.
        chain = self._cibil_chain()
        chain[0]["extract"] = [{"json_path": "wrong_key", "save_as": "access_token"}]
        g = build_chain_graph(chain)
        result = run_chain(g, adapter_name="CIBIL Credit Bureau", base_url="https://api.cibil.com/v1")
        assert result["ok"] is False
        statuses = {s["id"]: s["status"] for s in result["steps"]}
        assert statuses["auth"] == "mock_contract_violation"
        # Downstream collapses into blocked, not multiple independent failures.
        assert statuses["score"] == "blocked_by_upstream"
        assert statuses["report"] == "blocked_by_upstream"
        assert result["blocked_root"] == "auth"

    def test_cycle_returns_invalid_chain(self) -> None:
        eps = [
            {"id": "a", "path": "/a", "method": "GET", "depends_on": ["b"]},
            {"id": "b", "path": "/b", "method": "GET", "depends_on": ["a"]},
        ]
        g = build_chain_graph(eps)
        result = run_chain(g, adapter_name="x", base_url="https://x/y")
        assert result["ok"] is False
        assert result["cycle_error"] is not None
        assert result["steps"] == []

    def test_blocked_root_is_topologically_first_failure(self) -> None:
        # Two failures in series — root cause should be the earlier one.
        chain = self._cibil_chain()
        chain[0]["extract"] = [{"json_path": "no_such_field", "save_as": "access_token"}]
        chain[1]["extract"] = [{"json_path": "also_missing", "save_as": "enquiry_id"}]
        g = build_chain_graph(chain)
        result = run_chain(g, adapter_name="CIBIL Credit Bureau", base_url="https://api.cibil.com/v1")
        assert result["blocked_root"] == "auth"

    def test_run_is_deterministic_for_same_input(self) -> None:
        g = build_chain_graph(self._cibil_chain())
        r1 = run_chain(g, adapter_name="CIBIL Credit Bureau", base_url="https://api.cibil.com/v1")
        r2 = run_chain(g, adapter_name="CIBIL Credit Bureau", base_url="https://api.cibil.com/v1")
        # Mocks are hash-seeded, so the same input should produce the same output —
        # makes diffing two chain runs in the UI meaningful.
        assert r1["context_final"] == r2["context_final"]
        assert [s["id"] for s in r1["steps"]] == [s["id"] for s in r2["steps"]]
