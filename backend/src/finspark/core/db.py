"""Async SQLAlchemy engine and session factory."""

from collections.abc import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from finspark.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.APP_DEBUG,
    future=True,
    # pool_pre_ping keeps connections fresh; not supported by aiosqlite but harmless
    pool_pre_ping=True,
)


# ---------------------------------------------------------------------------
# SQLite-specific connection setup
# ---------------------------------------------------------------------------


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_conn: object, connection_record: object) -> None:  # noqa: ARG001
    """Enable FK enforcement and WAL journal mode for every new SQLite connection."""
    if "sqlite" not in settings.DATABASE_URL:
        return
    cursor = dbapi_conn.cursor()  # type: ignore[union-attr]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------


async def init_db() -> None:
    """Create all tables.  Import every model module first so metadata is populated."""
    # These imports register each model's metadata with Base before create_all.
    import finspark.models  # noqa: F401, PLC0415 — side-effect import

    from finspark.models.base import Base  # noqa: PLC0415

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
