"""DAG-based API chain testing (issue #ChainTesting).

The simulator's `_test_endpoint` checks each endpoint in isolation. Real
integrations are chains where one endpoint's output is the next one's input.
This package builds the dependency DAG, runs the chain with mocked responses
in topological order, propagates state through a shared context, and surfaces
broken wiring (missing inject values, type mismatches, blocked downstream
calls) instead of just per-endpoint pass/fail.
"""
