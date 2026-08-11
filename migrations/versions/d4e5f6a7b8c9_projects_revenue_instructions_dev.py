"""third pass: project assets, revenue events, instruction control layer, market reports, dev proposals

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-11

Additive only — no data destroyed, existing tables preserved (owner directive
third pass §56). SQLite-safe batch mode for column additions.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # projects: real-world asset links + capital allocation
    with op.batch_alter_table('projects') as batch:
        batch.add_column(sa.Column('urls_json', sa.JSON(), nullable=False, server_default='{}'))
        batch.add_column(sa.Column('capital_allocated_cents', sa.Integer(), nullable=False, server_default='0'))

    # crew_runs / llm_usage: project attribution
    with op.batch_alter_table('crew_runs') as batch:
        batch.add_column(sa.Column('project_id', sa.String(length=36), nullable=True))
    op.create_index('ix_crew_runs_project_id', 'crew_runs', ['project_id'])
    with op.batch_alter_table('llm_usage') as batch:
        batch.add_column(sa.Column('project_id', sa.String(length=36), nullable=True))
    op.create_index('ix_llm_usage_project_id', 'llm_usage', ['project_id'])

    op.create_table(
        'revenue_events',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=True),
        sa.Column('source', sa.String(length=32), nullable=False),
        sa.Column('source_ref', sa.String(length=255), nullable=True),
        sa.Column('description', sa.String(length=500), nullable=False),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('recurring', sa.Boolean(), nullable=False),
        sa.Column('recurrence_interval', sa.String(length=16), nullable=True),
        sa.Column('verification_status', sa.String(length=16), nullable=False),
        sa.Column('reversal_of', sa.String(length=36), nullable=True),
        sa.Column('evidence_json', sa.JSON(), nullable=False),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_revenue_events_user_id', 'revenue_events', ['user_id'])
    op.create_index('ix_revenue_events_project_id', 'revenue_events', ['project_id'])

    op.create_table(
        'instructions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=True),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('assigned_role', sa.String(length=32), nullable=True),
        sa.Column('provider', sa.String(length=48), nullable=True),
        sa.Column('model', sa.String(length=128), nullable=True),
        sa.Column('config_json', sa.JSON(), nullable=False),
        sa.Column('schedule_json', sa.JSON(), nullable=False),
        sa.Column('delivery_json', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.String(length=16), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_result_json', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_instructions_user_id', 'instructions', ['user_id'])
    op.create_index('ix_instructions_kind', 'instructions', ['kind'])
    op.create_index('ix_instructions_project_id', 'instructions', ['project_id'])

    op.create_table(
        'instruction_versions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('instruction_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('snapshot_json', sa.JSON(), nullable=False),
        sa.Column('changed_by', sa.String(length=16), nullable=False),
        sa.Column('reason', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['instruction_id'], ['instructions.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_instruction_versions_instruction_id', 'instruction_versions', ['instruction_id'])
    op.create_index('ix_instruction_versions_user_id', 'instruction_versions', ['user_id'])

    op.create_table(
        'market_reports',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('instruction_id', sa.String(length=36), nullable=True),
        sa.Column('crew_run_id', sa.String(length=36), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('sentiment', sa.String(length=24), nullable=True),
        sa.Column('summary_json', sa.JSON(), nullable=False),
        sa.Column('sources_json', sa.JSON(), nullable=False),
        sa.Column('sample_json', sa.JSON(), nullable=False),
        sa.Column('query_json', sa.JSON(), nullable=False),
        sa.Column('delivered_json', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['instruction_id'], ['instructions.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_market_reports_user_id', 'market_reports', ['user_id'])
    op.create_index('ix_market_reports_instruction_id', 'market_reports', ['instruction_id'])

    op.create_table(
        'dev_proposals',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('why', sa.Text(), nullable=False),
        sa.Column('expected_benefit', sa.Text(), nullable=False),
        sa.Column('evidence_json', sa.JSON(), nullable=False),
        sa.Column('plan_md', sa.Text(), nullable=False),
        sa.Column('files_affected_json', sa.JSON(), nullable=False),
        sa.Column('schema_impact', sa.String(length=500), nullable=False),
        sa.Column('risk', sa.String(length=16), nullable=False),
        sa.Column('test_plan', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('branch_name', sa.String(length=128), nullable=True),
        sa.Column('patch_hash', sa.String(length=64), nullable=True),
        sa.Column('patch_stats_json', sa.JSON(), nullable=False),
        sa.Column('test_results_json', sa.JSON(), nullable=False),
        sa.Column('approval_id', sa.String(length=36), nullable=True),
        sa.Column('approved_patch_hash', sa.String(length=64), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('merged_commit', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_dev_proposals_user_id', 'dev_proposals', ['user_id'])
    op.create_index('ix_dev_proposals_status', 'dev_proposals', ['status'])


def downgrade() -> None:
    op.drop_table('dev_proposals')
    op.drop_table('market_reports')
    op.drop_table('instruction_versions')
    op.drop_table('instructions')
    op.drop_table('revenue_events')
    op.drop_index('ix_llm_usage_project_id', table_name='llm_usage')
    with op.batch_alter_table('llm_usage') as batch:
        batch.drop_column('project_id')
    op.drop_index('ix_crew_runs_project_id', table_name='crew_runs')
    with op.batch_alter_table('crew_runs') as batch:
        batch.drop_column('project_id')
    with op.batch_alter_table('projects') as batch:
        batch.drop_column('capital_allocated_cents')
        batch.drop_column('urls_json')
