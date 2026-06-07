"""add listing_type to properties

Revision ID: b2c3d4e5f6a7
Revises: a1c2d3e4f5a6
Create Date: 2026-06-04 19:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE TYPE listing_type AS ENUM ('sale', 'rent')")
    op.add_column('properties',
        sa.Column('listing_type', sa.Enum('sale', 'rent', name='listing_type'),
                  nullable=False, server_default='sale')
    )


def downgrade() -> None:
    op.drop_column('properties', 'listing_type')
    op.execute("DROP TYPE listing_type")
