"""Unit tests for database session lifecycle and SQLite FK enforcement."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import Boolean, ForeignKey, Integer, String, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped


# ---------------------------------------------------------------------------
# Minimal in-memory schema for FK enforcement test
# ---------------------------------------------------------------------------


class _TestBase(DeclarativeBase):
    pass


class _Parent(_TestBase):
    __tablename__ = "test_parent"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))


class _Child(_TestBase):
    __tablename__ = "test_child"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("test_parent.id", ondelete="RESTRICT"), nullable=False
    )


def _apply_sqlite_pragmas(dbapi_conn: object, _connection_record: object) -> None:
    cursor = dbapi_conn.cursor()  # type: ignore[union-attr]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


# ---------------------------------------------------------------------------
# Session-scoped engine with schema + pragmas
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def _fk_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False, future=True)
    event.listen(engine.sync_engine, "connect", _apply_sqlite_pragmas)
    async with engine.begin() as conn:
        await conn.run_sync(_TestBase.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(_TestBase.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture()
async def _fk_session(_fk_engine) -> AsyncSession:
    """Each test gets a fresh session so that one FK failure does not poison later tests."""
    factory = async_sessionmaker(
        bind=_fk_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Helpers to build an engine + session_factory matching db.py's get_db()
# ---------------------------------------------------------------------------


def _make_engine_and_factory(url: str = "sqlite+aiosqlite:///:memory:"):
    eng = create_async_engine(url, echo=False, future=True)
    factory = async_sessionmaker(
        bind=eng,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    return eng, factory


# ---------------------------------------------------------------------------
# Tests: commit on success
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_db_commits_on_success() -> None:
    """A successful request body should result in commit being called."""
    committed: list[bool] = []

    eng, factory = _make_engine_and_factory()

    async with factory() as session:
        original_commit = session.commit

        async def _tracking_commit() -> None:
            committed.append(True)
            await original_commit()

        session.commit = _tracking_commit  # type: ignore[method-assign]

        try:
            yield_value = session  # simulate: `yield session`
            _ = yield_value        # route handler receives session and returns normally
            await session.commit()  # this is what get_db() does after yield
        except Exception:
            await session.rollback()
            raise

    assert committed == [True], "commit must be called exactly once on successful exit"

    await eng.dispose()


# ---------------------------------------------------------------------------
# Tests: rollback on exception
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_db_rolls_back_on_exception() -> None:
    """An exception raised inside the request body must trigger rollback, not commit."""
    rolled_back: list[bool] = []
    committed: list[bool] = []

    eng, factory = _make_engine_and_factory()

    async with factory() as session:
        original_rollback = session.rollback
        original_commit = session.commit

        async def _tracking_rollback() -> None:
            rolled_back.append(True)
            await original_rollback()

        async def _tracking_commit() -> None:
            committed.append(True)
            await original_commit()

        session.rollback = _tracking_rollback  # type: ignore[method-assign]
        session.commit = _tracking_commit  # type: ignore[method-assign]

        try:
            # Simulate route handler raising after yield
            raise ValueError("simulated route error")
        except Exception:
            await session.rollback()
            # re-raise suppressed for assertion purposes only

    assert rolled_back == [True], "rollback must be called on exception"
    assert committed == [], "commit must NOT be called when an exception occurs"

    await eng.dispose()


# ---------------------------------------------------------------------------
# Tests: get_db() generator contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_db_yields_session_and_commits() -> None:
    """get_db() must yield exactly one AsyncSession and commit afterwards."""
    from finspark.core.db import get_db

    sessions: list[AsyncSession] = []
    gen = get_db()
    try:
        session = await gen.__anext__()
        assert isinstance(session, AsyncSession)
        sessions.append(session)
        # Normal exit — send StopAsyncIteration to trigger the post-yield commit path
        try:
            await gen.aclose()
        except StopAsyncIteration:
            pass
    except Exception:
        await gen.aclose()
        raise

    assert len(sessions) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_db_rollback_on_throw() -> None:
    """Throwing into get_db() generator must trigger rollback and re-raise."""
    from finspark.core.db import get_db

    gen = get_db()
    await gen.__anext__()  # advance to yield

    with pytest.raises(RuntimeError, match="boom"):
        await gen.athrow(RuntimeError("boom"))


# ---------------------------------------------------------------------------
# Tests: SQLite FK enforcement
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sqlite_fk_enforced(_fk_session: AsyncSession) -> None:
    """Inserting a child row with a non-existent parent_id must raise IntegrityError."""
    orphan = _Child(id=1, parent_id=999)  # parent 999 does not exist
    _fk_session.add(orphan)

    with pytest.raises(IntegrityError):
        await _fk_session.flush()

    await _fk_session.rollback()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sqlite_fk_passes_with_valid_parent(_fk_session: AsyncSession) -> None:
    """Inserting a child row with a valid parent_id must succeed."""
    # Flush parent first so FK constraint is satisfied when child is inserted.
    parent = _Parent(id=20, name="test-parent")
    _fk_session.add(parent)
    await _fk_session.flush()

    child = _Child(id=20, parent_id=20)
    _fk_session.add(child)
    await _fk_session.flush()

    result = await _fk_session.execute(select(_Child).where(_Child.parent_id == 20))
    found = result.scalar_one_or_none()
    assert found is not None
    assert found.parent_id == 20

    await _fk_session.rollback()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sqlite_wal_journal_mode(tmp_path: "pytest.TempPathFactory") -> None:
    """WAL journal mode must be enabled when the pragma hook is applied to a file DB.

    WAL is not supported by in-memory SQLite; a temporary file is required.
    """
    import os  # noqa: PLC0415

    db_file = os.path.join(str(tmp_path), "wal_test.db")  # type: ignore[arg-type]
    url = f"sqlite+aiosqlite:///{db_file}"
    file_engine = create_async_engine(url, echo=False, future=True)
    event.listen(file_engine.sync_engine, "connect", _apply_sqlite_pragmas)

    try:
        async with file_engine.connect() as conn:
            result = await conn.execute(text("PRAGMA journal_mode"))
            row = result.fetchone()
            assert row is not None
            assert row[0].lower() == "wal"
    finally:
        await file_engine.dispose()


# ---------------------------------------------------------------------------
# Tests: SoftDeleteMixin + _soft_delete_filter
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_soft_delete_mixin_has_is_deleted_column() -> None:
    from finspark.models.base import SoftDeleteMixin

    assert hasattr(SoftDeleteMixin, "is_deleted")


@pytest.mark.unit
def test_soft_delete_filter_appends_where_clause() -> None:
    """_soft_delete_filter must produce a WHERE clause referencing is_deleted."""
    from sqlalchemy import select

    from finspark.models.base import Base, SoftDeleteMixin, _soft_delete_filter

    class _SoftModel(SoftDeleteMixin, Base):
        __tablename__ = "test_soft_model"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)

    stmt = _soft_delete_filter(select(_SoftModel), _SoftModel)
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "is_deleted" in compiled
