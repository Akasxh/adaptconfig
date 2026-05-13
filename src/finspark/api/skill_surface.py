"""Skill / Universal-API surface audit helpers.

This module is the source of truth for the *minimum* HTTP surface AdaptConfig
guarantees to its skill consumers. Every interactive UI surface in
``frontend/src/pages/`` and every operation documented in
``adaptconfig.skill.md`` is expected to route to one of the entries listed in
:data:`REQUIRED_API_SURFACE` below.

The check is intentionally *additive* — extra routes are fine. Missing routes
are not. ``tests/integration/test_skill_api_surface.py`` runs the audit
against the live FastAPI app; ``tests/unit/test_skill_surface_audit.py``
exercises the pure helpers in isolation.

Issue #116 — Universal API + drop-in Claude Skill.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(frozen=True)
class RequiredRoute:
    """A single (METHOD, PATH) the AdaptConfig API must expose."""

    method: str
    path: str
    page: str  # Originating frontend page (or "skill" / "composite" for new surface).

    @property
    def key(self) -> tuple[str, str]:
        return self.method.upper(), self.path


# The minimum set of HTTP endpoints required to drive AdaptConfig from a
# Claude Agent SDK / Claude Code consumer in MVP scope. Keep paths in sync
# with the route definitions in ``src/finspark/api/routes/*.py``.
REQUIRED_API_SURFACE: tuple[RequiredRoute, ...] = (
    # Health -- Dashboard.tsx pings this on load.
    RequiredRoute("GET", "/health", "Dashboard.tsx"),

    # Auth -- Login.tsx, Register.tsx.
    RequiredRoute("POST", "/api/v1/auth/register", "Register.tsx"),
    RequiredRoute("POST", "/api/v1/auth/login", "Login.tsx"),
    RequiredRoute("POST", "/api/v1/auth/refresh", "Login.tsx"),
    RequiredRoute("GET", "/api/v1/auth/me", "Login.tsx"),

    # Documents -- Documents.tsx, Configurations.tsx (GenerateForm).
    RequiredRoute("POST", "/api/v1/documents/upload", "Documents.tsx"),
    RequiredRoute("GET", "/api/v1/documents/", "Documents.tsx"),
    RequiredRoute("GET", "/api/v1/documents/{document_id}", "Documents.tsx"),
    RequiredRoute("DELETE", "/api/v1/documents/{document_id}", "Documents.tsx"),

    # Adapters -- Adapters.tsx + Configurations.tsx adapter dropdown.
    RequiredRoute("GET", "/api/v1/adapters/", "Adapters.tsx"),
    RequiredRoute("GET", "/api/v1/adapters/{adapter_id}", "Adapters.tsx"),
    RequiredRoute("POST", "/api/v1/adapters/from-document", "Configurations.tsx"),

    # Configurations -- Configurations.tsx.
    RequiredRoute("GET", "/api/v1/configurations/", "Configurations.tsx"),
    RequiredRoute("GET", "/api/v1/configurations/{config_id}", "Configurations.tsx"),
    RequiredRoute("POST", "/api/v1/configurations/generate", "Configurations.tsx"),
    RequiredRoute("PATCH", "/api/v1/configurations/{config_id}", "Configurations.tsx"),
    RequiredRoute("DELETE", "/api/v1/configurations/{config_id}", "Configurations.tsx"),
    RequiredRoute("POST", "/api/v1/configurations/{config_id}/validate", "Configurations.tsx"),
    RequiredRoute("POST", "/api/v1/configurations/{config_id}/transition", "Configurations.tsx"),
    RequiredRoute(
        "POST",
        "/api/v1/configurations/{config_id}/validate-and-test",
        "composite",
    ),
    RequiredRoute("GET", "/api/v1/configurations/templates", "Configurations.tsx"),
    RequiredRoute("GET", "/api/v1/configurations/{config_id}/export", "Configurations.tsx"),
    RequiredRoute("GET", "/api/v1/configurations/{config_id}/history", "Configurations.tsx"),
    RequiredRoute("POST", "/api/v1/configurations/{config_id}/rollback", "Configurations.tsx"),
    RequiredRoute("GET", "/api/v1/configurations/summary", "Configurations.tsx"),
    RequiredRoute(
        "GET",
        "/api/v1/configurations/{config_a_id}/diff/{config_b_id}",
        "Configurations.tsx",
    ),

    # Simulations -- Simulations.tsx + Configurations.tsx pipeline.
    RequiredRoute("GET", "/api/v1/simulations/", "Simulations.tsx"),
    RequiredRoute("POST", "/api/v1/simulations/run", "Simulations.tsx"),
    RequiredRoute("GET", "/api/v1/simulations/{simulation_id}", "Simulations.tsx"),
    RequiredRoute("DELETE", "/api/v1/simulations/{simulation_id}", "Simulations.tsx"),

    # Audit -- Audit.tsx.
    RequiredRoute("GET", "/api/v1/audit/", "Audit.tsx"),

    # Search -- Search.tsx.
    RequiredRoute("GET", "/api/v1/search/", "Search.tsx"),

    # Webhooks -- Webhooks.tsx.
    RequiredRoute("GET", "/api/v1/webhooks/", "Webhooks.tsx"),
    RequiredRoute("POST", "/api/v1/webhooks/", "Webhooks.tsx"),
    RequiredRoute("DELETE", "/api/v1/webhooks/{webhook_id}", "Webhooks.tsx"),
    RequiredRoute("POST", "/api/v1/webhooks/{webhook_id}/test", "Webhooks.tsx"),

    # Analytics -- Dashboard.tsx.
    RequiredRoute("GET", "/api/v1/analytics/dashboard", "Dashboard.tsx"),
)


def _route_pairs(routes: Iterable[object]) -> set[tuple[str, str]]:
    """Extract ``(METHOD, PATH)`` pairs from anything that exposes ``path``
    and ``methods``-like attributes.

    Tolerates duck-typed inputs (Starlette ``Route`` / FastAPI ``APIRoute``
    or any object with the same two attributes), which is what makes
    :func:`find_missing_routes` unit-testable without spinning up the app.
    """
    pairs: set[tuple[str, str]] = set()
    for route in routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path or not methods:
            continue
        for method in methods:
            if not isinstance(method, str):
                continue
            pairs.add((method.upper(), path))
    return pairs


def find_missing_routes(
    routes: Iterable[object],
    required: Iterable[RequiredRoute] = REQUIRED_API_SURFACE,
) -> list[RequiredRoute]:
    """Return the subset of *required* routes that are not present in *routes*.

    Pure, side-effect-free, and independent of FastAPI internals. Returns an
    empty list when the API surface satisfies every required pair.
    """
    available = _route_pairs(routes)
    return [r for r in required if r.key not in available]


def required_route_paths() -> list[str]:
    """Convenience accessor for documentation/debug tooling."""
    return sorted({r.path for r in REQUIRED_API_SURFACE})
