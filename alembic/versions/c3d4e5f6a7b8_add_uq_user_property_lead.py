"""add uq_user_property_lead constraint

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-06
"""
from alembic import op

revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Remove duplicate leads first (keep earliest created)
    op.execute("""
        DELETE FROM leads
        WHERE id NOT IN (
            SELECT DISTINCT ON (user_id, property_id) id
            FROM leads
            ORDER BY user_id, property_id, created_at ASC
        )
    """)
    op.create_unique_constraint(
        "uq_user_property_lead", "leads", ["user_id", "property_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_user_property_lead", "leads", type_="unique")
