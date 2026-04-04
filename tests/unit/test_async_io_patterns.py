"""Tests verifying async I/O patterns (issue #73).

Confirms that blocking operations (parse, simulate, generate) use
asyncio.to_thread so they do not block the event loop.
"""

import ast
import inspect
from pathlib import Path

import pytest


def _get_source(module_path: str) -> str:
    """Read the source of a Python module file."""
    return Path(module_path).read_text()


def _function_uses_to_thread(source: str, func_name: str) -> bool:
    """Check if a function body contains an asyncio.to_thread call."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == func_name:
            for child in ast.walk(node):
                if isinstance(child, ast.Attribute) and child.attr == "to_thread":
                    return True
    return False


class TestAsyncToThreadUsage:
    """Verify that blocking operations are wrapped in asyncio.to_thread."""

    def test_simulation_run_uses_to_thread(self) -> None:
        """The run_simulation endpoint wraps simulator.run_simulation in to_thread."""
        source = _get_source("src/finspark/api/routes/simulations.py")
        assert _function_uses_to_thread(source, "run_simulation"), (
            "run_simulation endpoint should use asyncio.to_thread"
        )

    def test_document_upload_uses_to_thread(self) -> None:
        """The document upload endpoint wraps parser.parse in to_thread."""
        source = _get_source("src/finspark/api/routes/documents.py")
        assert _function_uses_to_thread(source, "upload_document"), (
            "upload_document endpoint should use asyncio.to_thread for parsing"
        )

    def test_config_generate_uses_to_thread(self) -> None:
        """The config generation endpoint wraps generator.generate in to_thread."""
        source = _get_source("src/finspark/api/routes/configurations.py")
        assert _function_uses_to_thread(source, "generate_configuration"), (
            "generate_configuration endpoint should use asyncio.to_thread"
        )

    def test_simulator_stream_uses_to_thread_per_step(self) -> None:
        """The async streaming simulator uses to_thread for each step."""
        source = _get_source("src/finspark/services/simulation/simulator.py")
        assert _function_uses_to_thread(source, "_execute_step_with_timeout"), (
            "_execute_step_with_timeout should use asyncio.to_thread"
        )


class TestAsyncEndpointsRespond:
    """Integration-level smoke tests verifying endpoints still work e2e."""

    @pytest.mark.asyncio
    async def test_health_endpoint_responds(self, client) -> None:
        response = await client.get("/health")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_simulations_list_endpoint_responds(self, client) -> None:
        response = await client.get("/api/v1/simulations/")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_documents_list_endpoint_responds(self, client) -> None:
        response = await client.get("/api/v1/documents/")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_configurations_list_endpoint_responds(self, client) -> None:
        response = await client.get("/api/v1/configurations/")
        assert response.status_code == 200
