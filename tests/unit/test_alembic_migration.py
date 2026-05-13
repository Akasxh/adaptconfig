"""Tests for Alembic migration setup."""

from pathlib import Path


class TestAlembicSetup:
    def test_alembic_ini_exists(self) -> None:
        ini_path = Path(__file__).parents[2] / "alembic.ini"
        assert ini_path.exists(), "alembic.ini not found at project root"

    def test_alembic_env_exists(self) -> None:
        env_path = Path(__file__).parents[2] / "alembic" / "env.py"
        assert env_path.exists(), "alembic/env.py not found"

    def test_versions_directory_has_migration(self) -> None:
        versions_dir = Path(__file__).parents[2] / "alembic" / "versions"
        assert versions_dir.exists(), "alembic/versions/ directory not found"
        py_files = [f for f in versions_dir.iterdir() if f.suffix == ".py"]
        assert len(py_files) >= 1, "No migration files found in alembic/versions/"

    def test_alembic_ini_has_async_url(self) -> None:
        ini_path = Path(__file__).parents[2] / "alembic.ini"
        content = ini_path.read_text()
        assert "sqlite+aiosqlite" in content, "alembic.ini missing async SQLite URL"

    def test_env_imports_base_metadata(self) -> None:
        env_path = Path(__file__).parents[2] / "alembic" / "env.py"
        content = env_path.read_text()
        assert "from finspark.models.base import Base" in content
        assert "target_metadata = Base.metadata" in content
