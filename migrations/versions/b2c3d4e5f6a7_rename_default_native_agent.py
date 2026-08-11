"""Rebrand data correction: rename system-default native agents.

Targets ONLY rows created by the pre-rebrand default
(adapter_type='native' AND display_name='MyChief Native').
User-customized agent names are never touched.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE agent_configs SET display_name = 'Native Agent' "
            "WHERE adapter_type = 'native' AND display_name = 'MyChief Native'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE agent_configs SET display_name = 'MyChief Native' "
            "WHERE adapter_type = 'native' AND display_name = 'Native Agent'"
        )
    )
