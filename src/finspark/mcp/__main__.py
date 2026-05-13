"""Entry point for the ``adaptconfig-mcp`` stdio server.

Reads ``FINSPARK_MCP_TOKEN`` from the environment. The token's presence is the
authorisation gate -- a stdio process is already isolated by OS-level
permissions, so per-call validation is not part of the MVP (full RBAC is a
documented follow-up). In debug mode the gate is bypassed so local development
does not require a token.

Logs go to stderr; stdout is reserved for the MCP JSON-RPC protocol.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from typing import NoReturn

from finspark.core.config import settings
from finspark.core.database import init_db
from finspark.mcp.server import build_server

logger = logging.getLogger("finspark.mcp")

MCP_TOKEN_ENV = "FINSPARK_MCP_TOKEN"


class StartupError(RuntimeError):
    """Raised when the MCP server cannot start (missing auth, missing deps)."""


def check_auth(env: dict[str, str] | None = None) -> str | None:
    """Validate the ``FINSPARK_MCP_TOKEN`` environment guard.

    Returns the token when present (possibly ``None`` in debug mode). Raises
    :class:`StartupError` when the token is missing in non-debug mode.
    """
    env = env if env is not None else dict(os.environ)
    token = env.get(MCP_TOKEN_ENV) or None
    if token:
        return token
    if settings.debug:
        logger.warning(
            "%s not set; starting in debug mode without auth gate", MCP_TOKEN_ENV
        )
        return None
    raise StartupError(
        f"{MCP_TOKEN_ENV} environment variable is required when debug is False"
    )


def _configure_logging() -> None:
    """Route logs to stderr so they do not corrupt the stdio JSON-RPC stream."""
    root = logging.getLogger()
    # FastMCP sets up its own handlers on import; replace any stdout handlers
    # with a stderr handler to keep stdout clean.
    for handler in list(root.handlers):
        if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stdout:
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(logging.INFO)


async def _prepare() -> None:
    """Ensure the schema exists before tools execute."""
    await init_db()


def main(argv: list[str] | None = None) -> int:
    """Run the stdio MCP server. Returns a process exit code."""
    del argv  # FastMCP owns argument parsing on the wire
    _configure_logging()

    try:
        check_auth()
    except StartupError as exc:
        logger.error("mcp_startup_failed reason=%s", exc)
        sys.stderr.write(f"adaptconfig-mcp: {exc}\n")
        return 2

    try:
        asyncio.run(_prepare())
    except Exception as exc:  # noqa: BLE001
        logger.error("mcp_db_init_failed error=%s", exc)
        sys.stderr.write(f"adaptconfig-mcp: database init failed: {exc}\n")
        return 3

    server = build_server()

    # FastMCP installs its own SIGINT handler; we add SIGTERM so container
    # orchestrators get a clean shutdown without forcing a kill.
    def _on_sigterm(signum: int, _frame: object) -> NoReturn:
        logger.info("mcp_signal_received signum=%s shutting_down", signum)
        raise SystemExit(0)

    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except ValueError:
        # Not on the main thread (e.g. inside a test harness) -- skip.
        pass

    logger.info("mcp_server_starting transport=stdio name=%s", server.name)
    try:
        server.run(transport="stdio")
    except (KeyboardInterrupt, SystemExit):
        logger.info("mcp_server_stopped")
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover - executed via uv run
    raise SystemExit(main(sys.argv[1:]))
