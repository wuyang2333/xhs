"""add operation homepage url

Revision ID: 9dd05c28f21a
Revises: 7ab84c9d1230
"""
from alembic import op
import sqlalchemy as sa

revision = "9dd05c28f21a"
down_revision = "7ab84c9d1230"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("account_operation_profiles", sa.Column("homepage_url", sa.Text(), nullable=False, server_default=""))


def downgrade():
    op.drop_column("account_operation_profiles", "homepage_url")
