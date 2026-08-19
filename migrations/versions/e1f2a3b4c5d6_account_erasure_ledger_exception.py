"""Allow GDPR account erasure to delete a user's Ledger events (PostgreSQL).

The `events` table is append-only: UPDATE and DELETE are blocked by the
`events_append_only` trigger. GDPR erasure (backend/ops/account.delete_account)
is the one sanctioned exception. This migration teaches the trigger to permit
DELETE — never UPDATE — when the erasure transaction sets
`app.ledger_erase = 'on'` via `SET LOCAL`. Normal operation is unchanged: without
that per-transaction GUC, every UPDATE/DELETE on `events` still raises.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-08-16
"""
from typing import Sequence, Union

from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE OR REPLACE FUNCTION forbid_event_mutation() RETURNS trigger AS $$
        BEGIN
            -- UPDATE is never allowed. DELETE is allowed only inside an explicit
            -- account-erasure transaction (GDPR Art. 17).
            IF (TG_OP = 'DELETE'
                AND current_setting('app.ledger_erase', true) = 'on') THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'Ledger events are append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE OR REPLACE FUNCTION forbid_event_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Ledger events are append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
