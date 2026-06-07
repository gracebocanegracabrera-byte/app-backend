"""add image_url to properties

Revision ID: a1c2d3e4f5a6
Revises: b28d06678b52
Create Date: 2026-06-04 18:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'a1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'b28d06678b52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('properties', sa.Column('image_url', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('properties', 'image_url')
