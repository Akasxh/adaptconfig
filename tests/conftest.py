"""Pytest configuration and shared fixtures."""

import os

# Must be set before any finspark imports so Settings loads with debug=True.
os.environ.setdefault("FINSPARK_DEBUG", "true")

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# Import all models so Base.metadata has complete table definitions
import finspark.models.adapter  # noqa: F401
import finspark.models.audit  # noqa: F401
import finspark.models.configuration  # noqa: F401
import finspark.models.document  # noqa: F401
import finspark.models.simulation  # noqa: F401
import finspark.models.tenant  # noqa: F401
import finspark.models.webhook  # noqa: F401
from finspark.core.database import get_db
from finspark.main import app
from finspark.models.base import Base

# In-memory SQLite with StaticPool -- avoids file corruption and is fast
TEST_DB_URL = "sqlite+aiosqlite://"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

engine = create_async_engine(
    TEST_DB_URL,
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
test_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
def enable_debug_mode_for_tests():
    """Enable debug mode for all tests so header-based tenant auth is active."""
    with patch("finspark.core.middleware.settings") as mock_settings:
        mock_settings.debug = True
        yield mock_settings


@pytest_asyncio.fixture(autouse=True)
async def setup_database():
    """Drop and recreate tables before each test for a clean slate."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture(autouse=True)
async def reset_rate_limiter():
    """Reset the global rate limiter before each test to avoid 429 errors."""
    from finspark.core.rate_limiter import rate_limiter

    await rate_limiter.reset()
    yield


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with test_session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client for testing API endpoints."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    # Patch async_session_factory in the simulations route so SSE generators
    # (which open fresh sessions) use the same test in-memory DB.
    with patch("finspark.api.routes.simulations.async_session_factory", test_session_factory):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            ac.headers["X-Tenant-ID"] = "test-tenant"
            ac.headers["X-Tenant-Name"] = "Test Tenant"
            ac.headers["X-Tenant-Role"] = "admin"
            yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def sample_brd_path() -> Path:
    return FIXTURES_DIR / "sample_brd.txt"


@pytest.fixture
def sample_openapi_path() -> Path:
    return FIXTURES_DIR / "sample_openapi.yaml"


@pytest.fixture
def sample_brd_text() -> str:
    return (FIXTURES_DIR / "sample_brd.txt").read_text()
