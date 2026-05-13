"""add last_transform_run to configurations

Revision ID: a1b2c3d4e5f6
Revises: f98c690eeb83
Create Date: 2026-05-13 06:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f98c690eeb83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add last_transform_run column for persisting the most recent
    transformation run against a configuration (issue #113)."""
    op.add_column(
        "configurations",
        sa.Column("last_transform_run", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("configurations", "last_transform_run")
