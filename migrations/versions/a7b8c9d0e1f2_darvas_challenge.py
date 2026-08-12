"""The Darvas Challenge: fictional-money decision log and daily snapshots

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-12

Additive only, and deliberately minimal: two platform-level tables (no user_id
— there is one public challenge). Money is stored as integer cents and prices
as exact decimal strings, so nothing in the public log is a rounded float.

NOTHING HERE TOUCHES REAL FUNDS. The portfolio these tables describe is
simulated with fictional money; there is no broker, no order, no account.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'challenge_decisions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('trade_date', sa.String(length=10), nullable=False),
        sa.Column('symbol', sa.String(length=16), nullable=False),
        sa.Column('action', sa.String(length=8), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('price', sa.String(length=32), nullable=False),
        sa.Column('units', sa.String(length=32), nullable=False),
        sa.Column('box_top', sa.String(length=32), nullable=False),
        sa.Column('box_bottom', sa.String(length=32), nullable=False),
        sa.Column('stop', sa.String(length=32), nullable=False),
        sa.Column('cash_cents_after', sa.Integer(), nullable=False),
        sa.Column('equity_cents_after', sa.Integer(), nullable=False),
        sa.Column('realized_pnl_cents', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('trade_date', 'symbol', 'action'),
    )
    op.create_index('ix_challenge_decisions_trade_date', 'challenge_decisions', ['trade_date'])
    op.create_index('ix_challenge_decisions_symbol', 'challenge_decisions', ['symbol'])

    op.create_table(
        'challenge_snapshots',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('trade_date', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('equity_cents', sa.Integer(), nullable=False),
        sa.Column('cash_cents', sa.Integer(), nullable=False),
        sa.Column('positions_json', sa.JSON(), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('trade_date'),
    )
    op.create_index('ix_challenge_snapshots_trade_date', 'challenge_snapshots', ['trade_date'])


def downgrade() -> None:
    op.drop_table('challenge_snapshots')
    op.drop_table('challenge_decisions')
