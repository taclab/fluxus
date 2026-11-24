"""Type de résumé (`summaries.style`) et question libre (`summaries.question`).

Les résumés existants sont du type « standard », le seul qui existait.

Revision ID: 36a206f58679
Revises: 3c7e9a41d2b8
Create Date: 2026-11-24 14:21:49.568104

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '36a206f58679'
down_revision: Union[str, None] = '3c7e9a41d2b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('summaries', sa.Column('style', sa.String(length=20), server_default='standard', nullable=False))
    op.add_column('summaries', sa.Column('question', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('summaries', 'question')
    op.drop_column('summaries', 'style')
