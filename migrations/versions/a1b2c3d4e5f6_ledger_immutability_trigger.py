"""Ledger immutability trigger (PostgreSQL only).

On PostgreSQL, UPDATE/DELETE on the append-only `events` table is blocked at the
database level (§17, §141). On SQLite (dev/test) the application-layer ORM guard
in backend/core/models.py provides the same protection.

Revision ID: a1b2c3d4e5f6
Revises: 86508ac4e60e
Create Date: 2026-08-10

"""
from typing import Sequence, Union

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "000000000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
    op.execute(
        """
        CREATE TRIGGER events_append_only
        BEFORE UPDATE OR DELETE ON events
        FOR EACH ROW EXECUTE FUNCTION forbid_event_mutation();
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER IF EXISTS events_append_only ON events;")
    op.execute("DROP FUNCTION IF EXISTS forbid_event_mutation();")
