"""package schedule: the exact time a post should go live

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-21 09:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('output_packages', sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_output_packages_scheduled_at', 'output_packages', ['scheduled_at'])


def downgrade() -> None:
    op.drop_index('ix_output_packages_scheduled_at', table_name='output_packages')
    op.drop_column('output_packages', 'scheduled_at')
