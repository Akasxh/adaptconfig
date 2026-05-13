"""Best-effort backfill of dependency metadata when the LLM under-extracts.

The LLM-based parser is asked to produce `depends_on`, `extract`, and `inject`
on every endpoint, but it forgets two common patterns:
  1. Path templates ({enquiry_id}) almost always imply a dependency on
     whichever earlier endpoint extracted that value.
  2. OAuth/token endpoints feed every subsequent call's Authorization header.

This module patches those gaps without touching the LLM output's intentional
choices. It only adds; it never removes.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from finspark.schemas.documents import ExtractedEndpoint

from finspark.schemas.documents import InjectRule

_PATH_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_\-]+)\}")


def normalize_endpoints_for_chain(raw: list[dict]) -> list[dict]:
    """Take a list of raw endpoint dicts (possibly missing chain metadata)
    and return chain-ready dicts: every entry has a unique id, depends_on,
    extract, inject populated either from the source or by heuristic.

    Used at chain-run time to make the executor work on legacy documents
    that haven't been re-analyzed yet, AND on documents that have been
    re-analyzed (idempotent in the second case).
    """
    from finspark.schemas.documents import ExtractedEndpoint, ExtractRule, InjectRule

    eps: list[ExtractedEndpoint] = []
    for ep in raw:
        if not ep.get("path"):
            continue
        eps.append(ExtractedEndpoint(
            id=str(ep.get("id") or "").strip(),
            path=ep.get("path", ""),
            method=str(ep.get("method") or "GET").upper(),
            description=ep.get("description", "") or "",
            depends_on=list(ep.get("depends_on") or []),
            extract=[
                ExtractRule(
                    json_path=str(r.get("json_path") or r.get("save_as", "")),
                    save_as=str(r.get("save_as", "")),
                )
                for r in (ep.get("extract") or [])
                if isinstance(r, dict) and r.get("save_as")
            ],
            inject=[
                InjectRule(
                    template=str(r.get("template", "")),
                    location=str(r.get("location") or "header").lower(),
                    target_field=str(r.get("target_field") or ""),
                )
                for r in (ep.get("inject") or [])
                if isinstance(r, dict) and r.get("template")
            ],
        ))

    _ensure_unique_ids(eps)
    enrich_chain_metadata(eps)

    return [
        {
            "id": e.id, "path": e.path, "method": e.method,
            "description": e.description,
            "depends_on": e.depends_on,
            "extract": [r.model_dump() for r in e.extract],
            "inject": [r.model_dump() for r in e.inject],
        }
        for e in eps
    ]


def _ensure_unique_ids(endpoints: list) -> None:
    """Fill missing ids + de-dup collisions in place. See document_parser version."""
    seen: set[str] = set()
    for idx, ep in enumerate(endpoints):
        proposed = ep.id.strip() if ep.id else _slug_from_path(ep.path, ep.method)
        candidate = proposed
        n = 2
        while candidate in seen or not candidate:
            candidate = f"{proposed}_{n}" if proposed else f"endpoint_{idx}"
            n += 1
        ep.id = candidate
        seen.add(candidate)


def _slug_from_path(path: str, method: str) -> str:
    if not path:
        return "endpoint"
    parts = [p for p in path.strip("/").split("/") if p and not p.startswith("{")]
    base = parts[-1] if parts else "endpoint"
    base = re.sub(r"[^a-zA-Z0-9_]+", "_", base).strip("_").lower() or "endpoint"
    if method.upper() not in ("GET", ""):
        base = f"{method.lower()}_{base}"
    return base

# Endpoint ids/paths that look like auth — used to detect "fan out the token to every later call" pattern.
_AUTH_HINTS = ("oauth", "token", "auth", "login", "signin")


def enrich_chain_metadata(endpoints: list["ExtractedEndpoint"]) -> None:
    """Mutate `endpoints` in place, adding missing depends_on/inject entries.

    Heuristic precedence:
      1. Path placeholder rule — for every {placeholder} in an endpoint's path,
         find an earlier endpoint that extracts a value named the same
         (or whose save_as matches a casing/underscore variant) and wire up
         the dependency + a path-inject if neither exists yet.
      2. Auth fan-out — if any earlier endpoint extracts `access_token`
         (or similar) and the current endpoint has no Authorization inject,
         add one with `Bearer {{access_token}}` plus the dependency.
    """
    # Build a lookup: save_as -> source endpoint id (first wins; preserves declared order).
    saved_by: dict[str, str] = {}
    auth_source_id: str | None = None
    for ep in endpoints:
        for rule in ep.extract:
            key = rule.save_as.strip()
            if not key:
                continue
            saved_by.setdefault(_norm(key), ep.id)
            # Detect an auth source: endpoint id or save_as looks like a token field.
            if auth_source_id is None and (
                _looks_like_auth(ep.id)
                or _looks_like_auth(rule.save_as)
                or _looks_like_token_key(rule.save_as)
            ):
                if _looks_like_token_key(rule.save_as):
                    auth_source_id = ep.id

    # Walk endpoints in declared order — heuristics only borrow from earlier ones.
    seen_ids: set[str] = set()
    for ep in endpoints:
        # 1. Path-template injects
        for placeholder in _PATH_PLACEHOLDER_RE.findall(ep.path or ""):
            source_id = saved_by.get(_norm(placeholder))
            if not source_id or source_id == ep.id or source_id not in seen_ids:
                continue
            _add_dependency(ep, source_id)
            if not _has_path_inject(ep, placeholder):
                ep.inject.append(InjectRule(
                    template=f"{{{{{placeholder}}}}}",
                    location="path",
                    target_field=placeholder,
                ))

        # 2. Auth fan-out
        if (
            auth_source_id
            and auth_source_id != ep.id
            and auth_source_id in seen_ids
            and not _has_auth_inject(ep)
        ):
            _add_dependency(ep, auth_source_id)
            ep.inject.append(InjectRule(
                template="Bearer {{access_token}}",
                location="header",
                target_field="Authorization",
            ))

        seen_ids.add(ep.id)


# ── private helpers ──────────────────────────────────────────────────────────


def _norm(s: str) -> str:
    """Normalize a key so enquiry_id / enquiryId / EnquiryID collide."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _looks_like_auth(s: str) -> bool:
    s_low = (s or "").lower()
    return any(hint in s_low for hint in _AUTH_HINTS)


def _looks_like_token_key(s: str) -> bool:
    s_low = (s or "").lower()
    return "access_token" in s_low or s_low == "token" or s_low == "accesstoken"


def _add_dependency(ep: "ExtractedEndpoint", source_id: str) -> None:
    if source_id not in ep.depends_on:
        ep.depends_on.append(source_id)


def _has_path_inject(ep: "ExtractedEndpoint", placeholder: str) -> bool:
    return any(
        r.location == "path" and r.target_field == placeholder
        for r in ep.inject
    )


def _has_auth_inject(ep: "ExtractedEndpoint") -> bool:
    return any(
        r.location == "header" and r.target_field.lower() == "authorization"
        for r in ep.inject
    )
