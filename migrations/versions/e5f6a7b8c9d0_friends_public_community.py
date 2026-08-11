"""Friends of the Cantina: public profiles, public projects, public updates

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-11

Additive only. Public Friends entities are deliberately separate tables from
the internal operational `projects` table — no internal data is exposed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'public_profiles',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('handle', sa.String(length=30), nullable=False),
        sa.Column('display_name', sa.String(length=80), nullable=False),
        sa.Column('bio', sa.String(length=300), nullable=False),
        sa.Column('avatar_url', sa.String(length=2048), nullable=True),
        sa.Column('location', sa.String(length=120), nullable=True),
        sa.Column('links_json', sa.JSON(), nullable=False),
        sa.Column('is_published', sa.Boolean(), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_active_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('moderation_status', sa.String(length=16), nullable=False),
        sa.Column('moderation_reason', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id'),
        sa.UniqueConstraint('handle'),
    )
    op.create_index('ix_public_profiles_user_id', 'public_profiles', ['user_id'])
    op.create_index('ix_public_profiles_handle', 'public_profiles', ['handle'])
    op.create_index('ix_public_profiles_is_published', 'public_profiles', ['is_published'])
    op.create_index('ix_public_profiles_last_active_at', 'public_profiles', ['last_active_at'])

    op.create_table(
        'public_projects',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('tagline', sa.String(length=160), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('url', sa.String(length=2048), nullable=True),
        sa.Column('image_url', sa.String(length=2048), nullable=True),
        sa.Column('category', sa.String(length=24), nullable=False),
        sa.Column('tags_json', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('is_public', sa.Boolean(), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('source_internal_project_id', sa.String(length=36), nullable=True),
        sa.Column('moderation_status', sa.String(length=16), nullable=False),
        sa.Column('moderation_reason', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_public_projects_user_id', 'public_projects', ['user_id'])
    op.create_index('ix_public_projects_category', 'public_projects', ['category'])
    op.create_index('ix_public_projects_is_public', 'public_projects', ['is_public'])
    op.create_index('ix_public_projects_created_at', 'public_projects', ['created_at'])

    op.create_table(
        'public_updates',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=True),
        sa.Column('text', sa.String(length=500), nullable=False),
        sa.Column('url', sa.String(length=2048), nullable=True),
        sa.Column('image_url', sa.String(length=2048), nullable=True),
        sa.Column('is_public', sa.Boolean(), nullable=False),
        sa.Column('edited', sa.Boolean(), nullable=False),
        sa.Column('moderation_status', sa.String(length=16), nullable=False),
        sa.Column('moderation_reason', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['project_id'], ['public_projects.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_public_updates_user_id', 'public_updates', ['user_id'])
    op.create_index('ix_public_updates_project_id', 'public_updates', ['project_id'])
    op.create_index('ix_public_updates_is_public', 'public_updates', ['is_public'])
    op.create_index('ix_public_updates_created_at', 'public_updates', ['created_at'])


def downgrade() -> None:
    op.drop_table('public_updates')
    op.drop_table('public_projects')
    op.drop_table('public_profiles')
