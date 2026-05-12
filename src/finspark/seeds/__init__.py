"""Seed data loaders for initial database population."""

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select

from finspark.core.database import async_session_factory
from finspark.models.adapter import Adapter
from finspark.services.registry.adapter_registry import AdapterRegistry

logger = logging.getLogger(__name__)

_SEED_FILE = Path(__file__).parent / "adapters.json"


def _load_seed_data() -> list[dict[str, Any]]:
    """Load adapter seed data from the JSON file."""
    with open(_SEED_FILE) as f:
        return json.load(f)


async def seed_adapters() -> None:
    """Seed the database with pre-built adapters for demo."""
    async with async_session_factory() as db:
        result = await db.execute(select(Adapter).limit(1))
        if result.scalar_one_or_none():
            return

        registry = AdapterRegistry(db)
        adapters = _load_seed_data()

        for adapter_data in adapters:
            adapter = await registry.create_adapter(
                name=adapter_data["name"],
                category=adapter_data["category"],
                description=adapter_data.get("description", ""),
                icon=adapter_data.get("icon"),
            )
            for version_data in adapter_data.get("versions", []):
                await registry.add_version(
                    adapter_id=adapter.id,
                    version=version_data["version"],
                    base_url=version_data["base_url"],
                    auth_type=version_data["auth_type"],
                    endpoints=version_data["endpoints"],
                    request_schema=version_data.get("request_schema"),
                    response_schema=version_data.get("response_schema"),
                    changelog=version_data.get("changelog", ""),
                )

        await db.commit()
        logger.info("Seeded %d adapters with versions", len(adapters))


async def seed_admin_user() -> None:
    """Create the default admin user if no users exist."""
    from finspark.api.routes.auth import _hash_password
    from finspark.models.user import User

    async with async_session_factory() as db:
        result = await db.execute(select(User).limit(1))
        if result.scalar_one_or_none():
            return

        admin = User(
            email="admin@finspark.dev",
            name="Admin",
            password_hash=_hash_password("Admin1234!"),
            role="admin",
            tenant_id="default",
        )
        db.add(admin)
        await db.commit()
        logger.info("Created default admin user: admin@finspark.dev / Admin1234!")
