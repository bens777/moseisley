"""Trader Assistant: per-user TradingView webhook + signals journal

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-08-12

Additive only. Two tables:

  trading_webhooks — the per-user inbound endpoint. Only a SHA-256 of the
    token's verifier is stored; the selector is indexed for lookup.
  trading_signals  — the journal of alerts the user's own TradingView
    strategies sent, plus what the assistant suggested for each.

NO ORDERS, NO FUNDS. This feature receives signals and gives advice the user
executes themselves. Nothing here connects to a broker.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'trading_webhooks',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('selector', sa.String(length=32), nullable=False),
        sa.Column('verifier_hash', sa.String(length=64), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('signal_count', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('selector'),
    )
    op.create_index('ix_trading_webhooks_user_id', 'trading_webhooks', ['user_id'])
    op.create_index('ix_trading_webhooks_selector', 'trading_webhooks', ['selector'])

    op.create_table(
        'trading_signals',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ticker', sa.String(length=24), nullable=False),
        sa.Column('action', sa.String(length=8), nullable=False),
        sa.Column('price', sa.String(length=32), nullable=False),
        sa.Column('stop', sa.String(length=32), nullable=True),
        sa.Column('strategy', sa.String(length=64), nullable=False),
        sa.Column('note', sa.String(length=200), nullable=False),
        sa.Column('raw_payload', sa.JSON(), nullable=False),
        sa.Column('screening', sa.JSON(), nullable=False),
        sa.Column('recommendation', sa.JSON(), nullable=False),
        sa.Column('idempotency_key', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
    )
    op.create_index('ix_trading_signals_user_id', 'trading_signals', ['user_id'])
    op.create_index('ix_trading_signals_received_at', 'trading_signals', ['received_at'])
    op.create_index('ix_trading_signals_ticker', 'trading_signals', ['ticker'])
    op.create_index('ix_trading_signals_idempotency_key', 'trading_signals',
                    ['idempotency_key'])


def downgrade() -> None:
    op.drop_table('trading_signals')
    op.drop_table('trading_webhooks')
