"""clip hook and loop scores, shorts metadata

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-22 22:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('clips', sa.Column('hook_score', sa.Float(), nullable=True))
    op.add_column('clips', sa.Column('loop_score', sa.Float(), nullable=True))
    # hook, loop, retention_plan, packaging_variants, originality_flags, publish_gate_result, analytics
    op.add_column('clips', sa.Column('shorts', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                                     server_default=sa.text("'{}'::jsonb")))


def downgrade() -> None:
    op.drop_column('clips', 'shorts')
    op.drop_column('clips', 'loop_score')
    op.drop_column('clips', 'hook_score')
