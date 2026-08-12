"""Inspection log and quarantine for external agent replies

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-12

Additive only. Quarantined content is held in this table rather than in
chat_messages precisely so it never becomes agent context before the user has
approved it. Per-agent strict mode rides in agent_configs.configuration_json
and needs no schema change.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'agent_inspections',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('agent_id', sa.String(length=36), nullable=True),
        sa.Column('agent_name', sa.String(length=120), nullable=False),
        sa.Column('adapter_type', sa.String(length=32), nullable=False),
        sa.Column('session_id', sa.String(length=36), nullable=True),
        sa.Column('verdict', sa.String(length=16), nullable=False),
        sa.Column('stage', sa.String(length=16), nullable=False),
        sa.Column('reasons_json', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('content_chars', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_agent_inspections_user_id', 'agent_inspections', ['user_id'])
    op.create_index('ix_agent_inspections_agent_id', 'agent_inspections', ['agent_id'])
    op.create_index('ix_agent_inspections_verdict', 'agent_inspections', ['verdict'])
    op.create_index('ix_agent_inspections_status', 'agent_inspections', ['status'])
    op.create_index('ix_agent_inspections_created_at', 'agent_inspections', ['created_at'])


def downgrade() -> None:
    op.drop_table('agent_inspections')
