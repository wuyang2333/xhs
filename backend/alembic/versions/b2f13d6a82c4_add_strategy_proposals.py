"""add strategy proposals

Revision ID: b2f13d6a82c4
Revises: 9dd05c28f21a
"""
from alembic import op
import sqlalchemy as sa

revision = "b2f13d6a82c4"
down_revision = "9dd05c28f21a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "operation_strategy_proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("platform_account_id", sa.Integer(), sa.ForeignKey("platform_accounts.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("source_note_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("strategy_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("analysis_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_operation_strategy_proposals_user_id", "operation_strategy_proposals", ["user_id"])
    op.create_index("ix_operation_strategy_proposals_platform_account_id", "operation_strategy_proposals", ["platform_account_id"])
    op.create_index("ix_operation_strategy_proposals_status", "operation_strategy_proposals", ["status"])


def downgrade():
    op.drop_table("operation_strategy_proposals")
