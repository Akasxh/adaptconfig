"""Pure-Python unit tests for the skill API surface audit helpers.

Exercises :mod:`finspark.api.skill_surface` in isolation -- no FastAPI, no
DB, no HTTP. The matching integration test
(``tests/integration/test_skill_api_surface.py``) runs the same audit against
the live FastAPI app.

Issue #116 -- Universal API + drop-in Claude Skill.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from finspark.api.skill_surface import (
    REQUIRED_API_SURFACE,
    RequiredRoute,
    find_missing_routes,
    required_route_paths,
)


@dataclass
class _FakeRoute:
    """Tiny duck-typed stand-in for a starlette Route."""

    path: str
    methods: set[str]


class TestRequiredAPISurface:
    def test_validate_and_test_composite_endpoint_is_required(self) -> None:
        """The composite endpoint introduced for Issue #116 must be in the
        required surface -- it is the single point of contact for the
        validate-then-test pipeline."""
        keys = {r.key for r in REQUIRED_API_SURFACE}
        assert (
            "POST",
            "/api/v1/configurations/{config_id}/validate-and-test",
        ) in keys

    def test_required_routes_have_no_duplicates(self) -> None:
        """Each (method, path) pair appears at most once in the required list."""
        keys = [r.key for r in REQUIRED_API_SURFACE]
        assert len(keys) == len(set(keys)), f"duplicates: {keys}"

    def test_required_routes_cover_every_frontend_page(self) -> None:
        """Every frontend page under ``frontend/src/pages/`` must claim at
        least one required route. Prevents orphan UI pages."""
        pages_with_routes = {
            r.page
            for r in REQUIRED_API_SURFACE
            if r.page.endswith(".tsx")
        }
        expected_pages = {
            "Adapters.tsx",
            "Audit.tsx",
            "Configurations.tsx",
            "Dashboard.tsx",
            "Documents.tsx",
            "Login.tsx",
            "Register.tsx",
            "Search.tsx",
            "Simulations.tsx",
            "Webhooks.tsx",
        }
        missing = expected_pages - pages_with_routes
        assert not missing, f"frontend pages without a required route: {sorted(missing)}"

    def test_required_route_paths_is_sorted_unique(self) -> None:
        paths = required_route_paths()
        assert paths == sorted(set(paths))


class TestFindMissingRoutes:
    def test_returns_empty_when_all_routes_present(self) -> None:
        target = RequiredRoute("POST", "/api/v1/foo", "Foo.tsx")
        routes = [_FakeRoute(path="/api/v1/foo", methods={"POST", "OPTIONS"})]
        assert find_missing_routes(routes, required=[target]) == []

    def test_returns_missing_when_method_absent(self) -> None:
        target = RequiredRoute("POST", "/api/v1/foo", "Foo.tsx")
        routes = [_FakeRoute(path="/api/v1/foo", methods={"GET"})]
        missing = find_missing_routes(routes, required=[target])
        assert missing == [target]

    def test_returns_missing_when_path_absent(self) -> None:
        target = RequiredRoute("GET", "/api/v1/foo", "Foo.tsx")
        routes = [_FakeRoute(path="/api/v1/bar", methods={"GET"})]
        missing = find_missing_routes(routes, required=[target])
        assert missing == [target]

    def test_method_comparison_is_case_insensitive(self) -> None:
        """FastAPI exposes HTTP methods in upper case but the helper normalises
        anyway so callers can supply 'get' or 'GET' interchangeably."""
        target = RequiredRoute("get", "/api/v1/foo", "Foo.tsx")
        routes = [_FakeRoute(path="/api/v1/foo", methods={"GET"})]
        assert find_missing_routes(routes, required=[target]) == []

    def test_tolerates_routes_without_methods_or_path(self) -> None:
        @dataclass
        class _Mount:
            pass  # No path / methods at all -- e.g. starlette Mount.

        target = RequiredRoute("GET", "/api/v1/foo", "Foo.tsx")
        routes = [_FakeRoute(path="/api/v1/foo", methods={"GET"}), _Mount()]
        assert find_missing_routes(routes, required=[target]) == []

    def test_ignores_non_string_method_entries(self) -> None:
        """Some Starlette routes expose ``methods`` as a frozenset of strings,
        others omit it. Non-string entries must be silently skipped."""

        @dataclass
        class _WeirdRoute:
            path: str
            methods: set

        target = RequiredRoute("GET", "/api/v1/foo", "Foo.tsx")
        routes = [_WeirdRoute(path="/api/v1/foo", methods={None, 200, "GET"})]
        assert find_missing_routes(routes, required=[target]) == []


class TestRequiredRouteKey:
    def test_key_upcases_method(self) -> None:
        rr = RequiredRoute("post", "/x", "Page.tsx")
        assert rr.key == ("POST", "/x")

    def test_required_routes_are_immutable(self) -> None:
        """Frozen dataclass -- mutation is a programmer error caught at runtime."""
        rr = RequiredRoute("GET", "/x", "Page.tsx")
        with pytest.raises(Exception):  # noqa: PT011 -- FrozenInstanceError is dataclass-specific
            rr.method = "POST"  # type: ignore[misc]
