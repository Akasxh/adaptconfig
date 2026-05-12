"""Sequential API chain runtime (MVP slice of #109).

Drives the LLM-generated ``endpoints`` array (with ``id``, ``depends_on``,
``extract``, ``inject``) through topological execution against the
simulator's mock response store.

Scope of the MVP slice:
  * sequential / acyclic only (cycles raise :class:`ChainCycleError`)
  * synchronous step ordering (no parallelism, no event sourcing)
  * hand-rolled JSONPath subset (``$.foo.bar``, ``$.items[0].id``)
  * template substitution via ``{{step_id.field}}``

Public surface:
  * :class:`ChainExecutor`            -- the executor class
  * :class:`ChainCycleError`          -- raised on cycle detection (-> HTTP 400)
  * :class:`ChainExecutionError`      -- base for other chain failures
  * :func:`topological_sort`          -- standalone helper for tests
  * :func:`extract_jsonpath`          -- standalone helper for tests
  * :func:`apply_inject`              -- standalone helper for tests
  * :func:`substitute_template`       -- standalone helper for tests
"""

from __future__ import annotations

import copy
import logging
import re
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

__all__ = [
    "ChainCycleError",
    "ChainExecutionError",
    "ChainExecutor",
    "apply_inject",
    "extract_jsonpath",
    "substitute_template",
    "topological_sort",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ChainExecutionError(Exception):
    """Base error for chain runtime failures (unknown dep, bad template, ...)."""


class ChainCycleError(ChainExecutionError):
    """Raised when the endpoint dependency graph contains a cycle.

    The simulations route catches this and surfaces it as HTTP 400 so the
    user sees a clear "your config has a dependency cycle" message instead
    of a 500.
    """


# ---------------------------------------------------------------------------
# Topological sort (Kahn's algorithm)
# ---------------------------------------------------------------------------


def topological_sort(endpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order *endpoints* by their ``depends_on`` relations.

    Each endpoint must carry a unique string ``id``.  ``depends_on`` may be
    a string (single dep), a list of strings (multi dep), or absent / None
    (no deps -- root of the chain).

    Raises:
        ChainCycleError: if the graph has at least one cycle.
        ChainExecutionError: on duplicate ids, missing ``id``, or
            ``depends_on`` pointing at an id that doesn't exist.
    """
    id_map: dict[str, dict[str, Any]] = {}
    for ep in endpoints:
        ep_id = ep.get("id")
        if ep_id is None:
            raise ChainExecutionError(
                f"Endpoint missing 'id': path={ep.get('path', '<unknown>')!r}"
            )
        if not isinstance(ep_id, str):
            raise ChainExecutionError(f"Endpoint id must be a string, got {type(ep_id).__name__}")
        if ep_id in id_map:
            raise ChainExecutionError(f"Duplicate endpoint id: {ep_id!r}")
        id_map[ep_id] = ep

    in_degree: dict[str, int] = {eid: 0 for eid in id_map}
    dependents: dict[str, list[str]] = defaultdict(list)

    for ep in endpoints:
        deps = _normalize_deps(ep.get("depends_on"))
        for dep in deps:
            if dep not in id_map:
                raise ChainExecutionError(
                    f"Endpoint {ep['id']!r} depends on unknown id {dep!r}"
                )
            dependents[dep].append(ep["id"])
            in_degree[ep["id"]] += 1

    queue: deque[str] = deque(eid for eid, deg in in_degree.items() if deg == 0)
    ordered: list[str] = []

    while queue:
        current = queue.popleft()
        ordered.append(current)
        for nxt in dependents[current]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)

    if len(ordered) != len(id_map):
        remaining = sorted(set(id_map) - set(ordered))
        raise ChainCycleError(
            f"Cycle detected among endpoints: {remaining}"
        )

    return [id_map[eid] for eid in ordered]


def _normalize_deps(raw: Any) -> list[str]:
    """Coerce ``depends_on`` into a ``list[str]``.

    Accepts ``None``, ``str``, or ``list[str]``.  Anything else is rejected.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, str):
                raise ChainExecutionError(
                    f"depends_on must be string or list[str], got element {type(item).__name__}"
                )
        return list(raw)
    raise ChainExecutionError(
        f"depends_on must be string or list[str], got {type(raw).__name__}"
    )


# ---------------------------------------------------------------------------
# Template substitution -- {{step_id.field}} -> value
# ---------------------------------------------------------------------------


_TEMPLATE_RE = re.compile(r"\{\{([\w]+(?:\.[\w]+)*)\}\}")


def substitute_template(template: str, context: dict[str, dict[str, Any]]) -> str:
    """Replace ``{{step.field}}`` tokens in *template* with values from *context*.

    *context* is keyed by upstream step id.  The value is the dict of
    extracted fields for that step (whatever ``extract`` produced) plus the
    private ``_response`` key holding the full response.

    Supports dotted nesting: ``{{auth.data.access_token}}`` walks down the
    dict per part.

    Raises:
        ChainExecutionError: if the referenced step or any path segment
            is missing.
    """

    def _replacer(match: re.Match[str]) -> str:
        parts = match.group(1).split(".")
        step_id = parts[0]
        if step_id not in context:
            raise ChainExecutionError(
                f"Template references unknown step {step_id!r}"
            )
        value: Any = context[step_id]
        for part in parts[1:]:
            if isinstance(value, dict):
                if part not in value:
                    raise ChainExecutionError(
                        f"Field {'.'.join(parts)} not found in step {step_id!r} context"
                    )
                value = value[part]
            else:
                raise ChainExecutionError(
                    f"Field {'.'.join(parts)} not found in step {step_id!r} context"
                )
        return str(value)

    return _TEMPLATE_RE.sub(_replacer, template)


# ---------------------------------------------------------------------------
# Inject -- write into request template at a dotted path
# ---------------------------------------------------------------------------


def apply_inject(
    request_template: dict[str, Any],
    inject_rules: dict[str, str],
    context: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Return a NEW request dict with *inject_rules* applied.

    Each ``inject_rules`` key is a dotted path into the request dict
    (``headers.Authorization``, ``body.payment.parent_txn_id``).  The value
    is a template string passed through :func:`substitute_template`.

    Intermediate dicts are auto-created so callers can target deeply nested
    locations without pre-populating the template.

    Never mutates *request_template*.
    """
    result = copy.deepcopy(request_template)

    for dotted_path, tpl in inject_rules.items():
        if not dotted_path:
            raise ChainExecutionError("inject rule has empty target path")
        value = substitute_template(tpl, context)
        parts = dotted_path.split(".")
        target: Any = result
        for part in parts[:-1]:
            if not isinstance(target, dict):
                raise ChainExecutionError(
                    f"inject path {dotted_path!r} traverses non-dict at segment {part!r}"
                )
            if part not in target or not isinstance(target[part], dict):
                target[part] = {}
            target = target[part]
        if not isinstance(target, dict):
            raise ChainExecutionError(
                f"inject path {dotted_path!r} terminal target is not a dict"
            )
        target[parts[-1]] = value

    return result


# ---------------------------------------------------------------------------
# JSONPath subset -- $.foo, $.foo.bar, $.items[0].sub
# ---------------------------------------------------------------------------


_BRACKET_RE = re.compile(r"^(\w+)\[(\d+)\]$")


class _MissingSentinel:
    """Internal sentinel returned when a JSONPath does not resolve."""


_MISSING = _MissingSentinel()


def _resolve_path(data: Any, path_parts: list[str]) -> Any:
    """Walk *data* following dot/bracket segments.

    Returns the resolved value or :data:`_MISSING` on any failure (missing
    key, wrong type, out-of-range index, ...).  Missing values are silent
    at this layer; the caller decides how to react.
    """
    current = data
    for part in path_parts:
        if not part:
            return _MISSING
        bracket_match = _BRACKET_RE.match(part)
        if bracket_match:
            field, idx = bracket_match.group(1), int(bracket_match.group(2))
            if not isinstance(current, dict) or field not in current:
                return _MISSING
            arr = current[field]
            if not isinstance(arr, list) or idx >= len(arr):
                return _MISSING
            current = arr[idx]
        else:
            if not isinstance(current, dict) or part not in current:
                return _MISSING
            current = current[part]
    return current


def extract_jsonpath(
    response: dict[str, Any],
    extract_rules: dict[str, str],
) -> dict[str, Any]:
    """Extract values from *response* using JSONPath-subset rules.

    Supported patterns:
      * ``$.field``                    -- top-level scalar
      * ``$.nested.field``             -- dotted descent
      * ``$.items[0]``                 -- array index
      * ``$.items[0].sub``             -- index then descent

    Missing fields are silently omitted from the result dict (logged at
    WARNING).  The caller (typically the executor) treats absence as
    "skip this binding" rather than failure -- matches typical OAuth /
    pagination flows where the upstream may legitimately not return a
    field on the error path.
    """
    extracted: dict[str, Any] = {}

    for key, jsonpath in extract_rules.items():
        if not isinstance(jsonpath, str):
            raise ChainExecutionError(
                f"extract rule for key {key!r} must be a string, got {type(jsonpath).__name__}"
            )
        path = jsonpath.lstrip("$").lstrip(".")
        if not path:
            logger.warning("Empty JSONPath for key %r -- skipping", key)
            continue
        parts = path.split(".")
        value = _resolve_path(response, parts)
        if isinstance(value, _MissingSentinel):
            logger.warning(
                "JSONPath %r resolved nothing in response -- skipping key %r",
                jsonpath,
                key,
            )
            continue
        extracted[key] = value

    return extracted


# ---------------------------------------------------------------------------
# ChainExecutor
# ---------------------------------------------------------------------------


class ChainExecutor:
    """Executes a sequential, acyclic endpoint chain.

    The executor is injected with a *call_fn* that performs the actual
    request -- the simulator passes its mock-response wrapper, real
    runtimes would pass an httpx-backed dispatcher.  Decoupling here
    keeps the executor pure (no I/O, no module-level mock coupling) and
    trivially testable with a fake callable.

    Usage
    -----
    >>> async def fake(endpoint, request):
    ...     return {"access_token": "tok", "status": "success"}
    >>> executor = ChainExecutor(fake)
    >>> results = await executor.run([
    ...     {"id": "auth", "path": "/oauth/token", "method": "POST",
    ...      "extract": {"token": "$.access_token"}},
    ...     {"id": "resource", "path": "/v1/resource", "method": "GET",
    ...      "depends_on": "auth",
    ...      "inject": {"headers.Authorization": "Bearer {{auth.token}}"}},
    ... ])
    """

    def __init__(
        self,
        call_fn: Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> None:
        self._call_fn = call_fn

    async def run(
        self,
        endpoints: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Sort endpoints, then run them in dependency order.

        Returns a list of step result dicts, one per endpoint, in execution
        order:

            {
                "endpoint_id": str,
                "request": dict,        # post-inject request that was sent
                "response": dict,       # full response from call_fn
                "extracted": dict,      # values extracted by JSONPath rules
            }
        """
        if not endpoints:
            return []

        sorted_eps = topological_sort(endpoints)
        context: dict[str, dict[str, Any]] = {}
        results: list[dict[str, Any]] = []

        for ep in sorted_eps:
            ep_id = ep["id"]
            inject_rules = ep.get("inject") or {}
            extract_rules = ep.get("extract") or {}

            request_template = self._build_request_template(ep)

            prepared_request = (
                apply_inject(request_template, inject_rules, context)
                if inject_rules
                else copy.deepcopy(request_template)
            )

            response = await self._call_fn(ep, prepared_request)

            extracted = (
                extract_jsonpath(response, extract_rules) if extract_rules else {}
            )

            # Context entry: extracted fields are first-class; the full
            # response lives under ``_response`` so templates can fall
            # back to ``{{step._response.x}}`` if needed.
            context[ep_id] = {**extracted, "_response": response}

            results.append(
                {
                    "endpoint_id": ep_id,
                    "request": prepared_request,
                    "response": response,
                    "extracted": extracted,
                }
            )

        return results

    @staticmethod
    def _build_request_template(endpoint: dict[str, Any]) -> dict[str, Any]:
        """Snapshot the request-shaped portions of *endpoint* into a fresh dict."""
        template: dict[str, Any] = {}
        for section in ("body", "headers", "path_params", "query_params"):
            if section in endpoint:
                template[section] = copy.deepcopy(endpoint[section])
        return template
