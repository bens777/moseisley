"""Project spec: the JSON the Manager's conversational creation flow compiles

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-08-13

Additive, one nullable column. The guided conversation gathers identity, input
data, a web-researched benchmark (with its sources), a validated objective and
a method — that compiled spec needs a durable, editable home on the project
row. NULL means the project predates the flow or was created without one,
which is the truth about it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, None] = 'c9d0e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('projects', sa.Column('spec_json', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('projects', 'spec_json')
