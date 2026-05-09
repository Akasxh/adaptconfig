"""add external_api_audits table

Revision ID: a1b2c3d4e5f6
Revises: f8af5a676619
Create Date: 2026-05-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f8af5a676619'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('external_api_audits',
    sa.Column('user_id', sa.String(length=36), nullable=True),
    sa.Column('configuration_id', sa.String(length=36), nullable=False),
    sa.Column('adapter_name', sa.String(length=255), nullable=False),
    sa.Column('adapter_version', sa.String(length=50), nullable=False),
    sa.Column('endpoint_path', sa.String(length=500), nullable=False),
    sa.Column('http_method', sa.String(length=10), nullable=False),
    sa.Column('request_body_masked', sa.Text(), nullable=True),
    sa.Column('response_status', sa.Integer(), nullable=False),
    sa.Column('response_body_masked', sa.Text(), nullable=True),
    sa.Column('response_time_ms', sa.Integer(), nullable=False),
    sa.Column('success', sa.Boolean(), nullable=False),
    sa.Column('error_code', sa.String(length=100), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('trigger_type', sa.String(length=50), nullable=False),
    sa.Column('trigger_id', sa.String(length=36), nullable=True),
    sa.Column('workflow_run_id', sa.String(length=36), nullable=True),
    sa.Column('workflow_step_id', sa.String(length=36), nullable=True),
    sa.Column('previous_hash', sa.String(length=64), nullable=True),
    sa.Column('record_hash', sa.String(length=64), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_external_api_audits_adapter_name'), 'external_api_audits', ['adapter_name'], unique=False)
    op.create_index(op.f('ix_external_api_audits_adapter_version'), 'external_api_audits', ['adapter_version'], unique=False)
    op.create_index(op.f('ix_external_api_audits_configuration_id'), 'external_api_audits', ['configuration_id'], unique=False)
    op.create_index(op.f('ix_external_api_audits_tenant_id'), 'external_api_audits', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_external_api_audits_trigger_type'), 'external_api_audits', ['trigger_type'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_external_api_audits_trigger_type'), table_name='external_api_audits')
    op.drop_index(op.f('ix_external_api_audits_tenant_id'), table_name='external_api_audits')
    op.drop_index(op.f('ix_external_api_audits_configuration_id'), table_name='external_api_audits')
    op.drop_index(op.f('ix_external_api_audits_adapter_version'), table_name='external_api_audits')
    op.drop_index(op.f('ix_external_api_audits_adapter_name'), table_name='external_api_audits')
    op.drop_table('external_api_audits')
