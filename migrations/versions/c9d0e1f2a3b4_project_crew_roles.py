"""Project crew: the roles guided creation declared for a project

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-08-13

Additive, one column. Guided project creation asks the Manager which roles fit
the project and the user confirms a selection — that DECISION had nowhere to
live. The crew_roles already reported in project metrics are observed from
crew_runs (who actually worked on it), which is history, not intent: a freshly
created project has no runs and would report an empty crew forever.

Existing rows get [] — a project created before this simply has no declared
crew, which is the truth about it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('projects', sa.Column('crew_roles_json', sa.JSON(), nullable=True))
    op.execute("UPDATE projects SET crew_roles_json = '[]' WHERE crew_roles_json IS NULL")


def downgrade() -> None:
    op.drop_column('projects', 'crew_roles_json')
