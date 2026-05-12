"""Database engine and session management."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from finspark.core.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    future=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    from finspark.models.base import Base

    # Import all models so SQLAlchemy registers them on Base.metadata
    from finspark.models import adapter  # noqa: F401
    from finspark.models import audit  # noqa: F401
    from finspark.models import configuration  # noqa: F401
    from finspark.models import document  # noqa: F401
    from finspark.models import simulation  # noqa: F401
    from finspark.models import tenant  # noqa: F401
    from finspark.models import user  # noqa: F401
    from finspark.models import webhook  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
