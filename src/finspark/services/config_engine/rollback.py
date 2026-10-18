"""Configuration rollback manager - snapshot, restore, and compare versions."""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.configuration import Configuration, ConfigurationHistory
from finspark.schemas.configurations import (
    ConfigHistoryEntry,
    VersionComparisonResponse,
)
from finspark.services.config_engine.diff_engine import ConfigDiffEngine


class RollbackManager:
    """Manages configuration version snapshots and rollbacks."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._diff_engine = ConfigDiffEngine()

    async def snapshot(
        self,
        config_id: str,
        tenant_id: str,
        *,
        change_type: str = "updated",
        changed_by: str | None = None,
    ) -> ConfigurationHistory:
        """Create a ConfigurationHistory entry capturing the current state."""
        config = await self._get_config(config_id, tenant_id)

        current_state = self._serialise_config(config)

        # Fetch the latest history entry to record previous_value
        prev_stmt = (
            select(ConfigurationHistory)
            .where(
                ConfigurationHistory.configuration_id == config_id,
                ConfigurationHistory.tenant_id == tenant_id,
            )
            .order_by(ConfigurationHistory.version.desc())
            .limit(1)
        )
        prev_result = await self._db.execute(prev_stmt)
        prev_entry = prev_result.scalar_one_or_none()

        previous_value = prev_entry.new_value if prev_entry else None

        new_version = (prev_entry.version + 1) if prev_entry else 1

        history = ConfigurationHistory(
            tenant_id=tenant_id,
            configuration_id=config_id,
            version=new_version,
            change_type=change_type,
            previous_value=previous_value,
            new_value=json.dumps(current_state),
            changed_by=changed_by,
        )
        self._db.add(history)
        await self._db.flush()
        return history

    async def rollback(
        self,
        config_id: str,
        target_version: int,
        tenant_id: str,
        *,
        changed_by: str | None = None,
    ) -> Configuration:
        """Restore a configuration to a specific historical version."""
        config = await self._get_config(config_id, tenant_id, for_update=True)

        # Fetch the target history entry
        target_stmt = select(ConfigurationHistory).where(
            ConfigurationHistory.configuration_id == config_id,
            ConfigurationHistory.tenant_id == tenant_id,
            ConfigurationHistory.version == target_version,
        )
        target_result = await self._db.execute(target_stmt)
        target_entry = target_result.scalar_one_or_none()
        if not target_entry:
            raise ValueError(f"Version {target_version} not found for configuration {config_id}")

        if not target_entry.new_value:
            raise ValueError(f"Version {target_version} has no saved state to restore")

        try:
            snapshot_data: dict[str, Any] = json.loads(target_entry.new_value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Version {target_version} has corrupt state data: {exc}"
            ) from exc

        # Save current state as a snapshot before overwriting.
        # snapshot() assigns the next available version number — the rollback
        # history entry must follow it with snapshot_version + 1 to avoid
        # duplicate version numbers.
        pre_rollback_snapshot = await self.snapshot(
            config_id,
            tenant_id,
            change_type="pre_rollback",
            changed_by=changed_by,
        )
        rollback_version = pre_rollback_snapshot.version + 1

        # Apply restored state
        config.field_mappings = json.dumps(snapshot_data.get("field_mappings"))
        config.transformation_rules = json.dumps(snapshot_data.get("transformation_rules"))
        config.hooks = json.dumps(snapshot_data.get("hooks"))
        config.auth_config = (
            json.dumps(snapshot_data.get("auth_config"))
            if snapshot_data.get("auth_config")
            else None
        )
        config.full_config = json.dumps(snapshot_data.get("full_config"))
        config.status = "rollback"
        config.version = rollback_version

        # Record rollback in history
        rollback_history = ConfigurationHistory(
            tenant_id=tenant_id,
            configuration_id=config_id,
            version=rollback_version,
            change_type="rollback",
            previous_value=None,
            new_value=json.dumps(self._serialise_config(config)),
            changed_by=changed_by,
        )
        self._db.add(rollback_history)
        await self._db.flush()

        return config

    async def list_versions(
        self,
        config_id: str,
        tenant_id: str,
    ) -> list[ConfigHistoryEntry]:
        """Return the full version history for a configuration."""
        stmt = (
            select(ConfigurationHistory)
            .where(
                ConfigurationHistory.configuration_id == config_id,
                ConfigurationHistory.tenant_id == tenant_id,
            )
            .order_by(ConfigurationHistory.version.asc())
        )
        result = await self._db.execute(stmt)
        rows = result.scalars().all()

        entries: list[ConfigHistoryEntry] = []
        for row in rows:
            try:
                previous_value = json.loads(row.previous_value) if row.previous_value else None
            except json.JSONDecodeError:
                previous_value = None
            try:
                new_value = json.loads(row.new_value) if row.new_value else None
            except json.JSONDecodeError:
                new_value = None
            entries.append(
                ConfigHistoryEntry(
                    id=row.id,
                    configuration_id=row.configuration_id,
                    version=row.version,
                    change_type=row.change_type,
                    previous_value=previous_value,
                    new_value=new_value,
                    changed_by=row.changed_by,
                    created_at=row.created_at,
                )
            )
        return entries

    async def compare_versions(
        self,
        config_id: str,
        version_a: int,
        version_b: int,
        tenant_id: str,
    ) -> VersionComparisonResponse:
        """Diff two versions of the same configuration."""
        stmt = select(ConfigurationHistory).where(
            ConfigurationHistory.configuration_id == config_id,
            ConfigurationHistory.tenant_id == tenant_id,
            ConfigurationHistory.version.in_([version_a, version_b]),
        )
        result = await self._db.execute(stmt)
        entries = {row.version: row for row in result.scalars().all()}

        if version_a not in entries:
            raise ValueError(f"Version {version_a} not found for configuration {config_id}")
        if version_b not in entries:
            raise ValueError(f"Version {version_b} not found for configuration {config_id}")

        try:
            state_a: dict[str, Any] = (
                json.loads(entries[version_a].new_value) if entries[version_a].new_value else {}
            )
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Version {version_a} has corrupt state data: {exc}"
            ) from exc
        try:
            state_b: dict[str, Any] = (
                json.loads(entries[version_b].new_value) if entries[version_b].new_value else {}
            )
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Version {version_b} has corrupt state data: {exc}"
            ) from exc

        diff_result = self._diff_engine.compare(
            state_a,
            state_b,
            config_a_id=f"v{version_a}",
            config_b_id=f"v{version_b}",
        )

        return VersionComparisonResponse(
            configuration_id=config_id,
            version_a=version_a,
            version_b=version_b,
            total_changes=diff_result.total_changes,
            breaking_changes=diff_result.breaking_changes,
            diffs=diff_result.diffs,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get_config(
        self,
        config_id: str,
        tenant_id: str,
        *,
        for_update: bool = False,
    ) -> Configuration:
        stmt = select(Configuration).where(
            Configuration.id == config_id,
            Configuration.tenant_id == tenant_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        result = await self._db.execute(stmt)
        config = result.scalar_one_or_none()
        if not config:
            raise ValueError(f"Configuration {config_id} not found")
        return config

    @staticmethod
    def _safe_json_loads(value: str | None) -> Any:
        if not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _serialise_config(config: Configuration) -> dict[str, Any]:
        _load = RollbackManager._safe_json_loads
        return {
            "field_mappings": _load(config.field_mappings),
            "transformation_rules": _load(config.transformation_rules),
            "hooks": _load(config.hooks),
            "auth_config": _load(config.auth_config),
            "full_config": _load(config.full_config),
            "status": config.status,
            "version": config.version,
        }

    @staticmethod
    def serialise_config(config: Configuration) -> dict[str, Any]:
        """Public alias for the snapshot shape ``rollback`` knows how to apply.

        Routes that create the initial ``ConfigurationHistory`` row (generate /
        MCP create) must store this exact shape. Storing the bare full-config
        dict caused rollback to overwrite ``full_config`` with ``null`` because
        ``snapshot.get("full_config")`` returned ``None``.
        """
        return RollbackManager._serialise_config(config)
