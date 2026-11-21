"""batches and watched folders

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-21 15:30:31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('watch_folders',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('path', sa.String(length=1000), nullable=False),
    sa.Column('recipe', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('include_subfolders', sa.Boolean(), nullable=False),
    sa.Column('after_processing', sa.String(length=20), nullable=False),
    sa.Column('batch_id', sa.Uuid(), nullable=True),
    sa.Column('state', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('last_scan_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('path')
    )
    op.create_table('batches',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('origin', sa.String(length=20), nullable=False),
    sa.Column('watch_folder_id', sa.Uuid(), nullable=True),
    sa.Column('recipe', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('state', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['watch_folder_id'], ['watch_folders.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('batch_items',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('batch_id', sa.Uuid(), nullable=False),
    sa.Column('filename', sa.String(length=1000), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('mtime', sa.BigInteger(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=True),
    sa.Column('asset_id', sa.Uuid(), nullable=True),
    sa.Column('project_id', sa.Uuid(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['asset_id'], ['media_assets.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['batch_id'], ['batches.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('batch_id', 'filename', 'size_bytes', 'mtime')
    )
    # watch_folders <-> batches reference each other: this one is added once both tables exist.
    op.create_foreign_key('fk_watch_folder_batch', 'watch_folders', 'batches', ['batch_id'], ['id'], ondelete='SET NULL')
    op.create_index(op.f('ix_batch_items_batch_id'), 'batch_items', ['batch_id'], unique=False)
    op.create_index(op.f('ix_batch_items_project_id'), 'batch_items', ['project_id'], unique=False)
    op.create_index(op.f('ix_batch_items_sha256'), 'batch_items', ['sha256'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_batch_items_sha256'), table_name='batch_items')
    op.drop_index(op.f('ix_batch_items_project_id'), table_name='batch_items')
    op.drop_index(op.f('ix_batch_items_batch_id'), table_name='batch_items')
    op.drop_table('batch_items')
    op.drop_constraint('fk_watch_folder_batch', 'watch_folders', type_='foreignkey')
    op.drop_table('batches')
    op.drop_table('watch_folders')
