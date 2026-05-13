"""Chain runtime: sequential API chaining for integration configurations.

MVP slice of issue #109 (Workflow Orchestration Engine).

Public surface:

* :class:`ChainExecutor` -- topo-sorts ``endpoints`` by ``depends_on`` and runs
  them sequentially against the simulator's mock-response store, applying
  ``extract`` -> ``inject`` between steps.
* :class:`ChainCycleError` -- raised when ``depends_on`` forms a cycle or
  references an unknown endpoint id. Surfaces as HTTP 400 from
  ``/api/v1/simulations/run``.
* :func:`extract_path` -- minimal JSONPath resolver used for ``extract`` rules.
* :func:`is_chain` -- "should this config run through the chain executor?"
  detector (>=2 endpoints with at least one ``depends_on``).
"""
from __future__ import annotations

from finspark.services.chain.errors import ChainCycleError, ChainError
from finspark.services.chain.executor import ChainExecutor, is_chain
from finspark.services.chain.jsonpath import extract_path

__all__ = [
    "ChainCycleError",
    "ChainError",
    "ChainExecutor",
    "extract_path",
    "is_chain",
]
