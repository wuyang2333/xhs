"""add account scoped operations

Revision ID: c4a81f29d731
Revises: b2f13d6a82c4
"""

from alembic import op
import sqlalchemy as sa


revision = "c4a81f29d731"
down_revision = "b2f13d6a82c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ai_drafts") as batch:
        batch.add_column(sa.Column("platform_account_id", sa.Integer(), nullable=True))
        batch.create_index("ix_ai_drafts_platform_account_id", ["platform_account_id"])
        batch.create_foreign_key("fk_ai_drafts_platform_account_id", "platform_accounts", ["platform_account_id"], ["id"])
    with op.batch_alter_table("account_operation_profiles") as batch:
        batch.add_column(sa.Column("daily_generate_count", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("daily_publish_count", sa.Integer(), nullable=False, server_default="1"))
    op.execute(
        "UPDATE ai_drafts SET platform_account_id = ("
        "SELECT platform_account_id FROM publish_jobs "
        "WHERE publish_jobs.source_draft_id = ai_drafts.id AND publish_jobs.platform_account_id IS NOT NULL "
        "ORDER BY publish_jobs.id DESC LIMIT 1"
        ") WHERE platform_account_id IS NULL AND EXISTS ("
        "SELECT 1 FROM publish_jobs WHERE publish_jobs.source_draft_id = ai_drafts.id "
        "AND publish_jobs.platform_account_id IS NOT NULL)"
    )


def downgrade() -> None:
    with op.batch_alter_table("account_operation_profiles") as batch:
        batch.drop_column("daily_publish_count")
        batch.drop_column("daily_generate_count")
    with op.batch_alter_table("ai_drafts") as batch:
        batch.drop_constraint("fk_ai_drafts_platform_account_id", type_="foreignkey")
        batch.drop_index("ix_ai_drafts_platform_account_id")
        batch.drop_column("platform_account_id")
