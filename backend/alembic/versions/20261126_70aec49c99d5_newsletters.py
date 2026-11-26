"""Newsletters reçues par e-mail : adresses de réception (`newsletter_addresses`) et origine des flux
(`feeds.source` : rss | email). Les flux existants sont des flux RSS.

Revision ID: 70aec49c99d5
Revises: 36a206f58679
Create Date: 2026-11-26 15:42:21.180696

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '70aec49c99d5'
down_revision: Union[str, None] = '36a206f58679'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('newsletter_addresses',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('token', sa.String(length=64), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=False),
    sa.Column('feed_id', sa.Integer(), nullable=True),
    sa.Column('category_id', sa.Integer(), nullable=True),
    sa.Column('received_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('last_received_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['category_id'], ['categories.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['feed_id'], ['feeds.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('feed_id'),
    sa.UniqueConstraint('token')
    )
    op.add_column('feeds', sa.Column('source', sa.String(length=10), server_default='rss', nullable=False))


def downgrade() -> None:
    op.drop_column('feeds', 'source')
    op.drop_table('newsletter_addresses')
