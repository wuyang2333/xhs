"""add operations planning and learning

Revision ID: 7ab84c9d1230
Revises: 60cd5c95fde1
"""
from alembic import op
import sqlalchemy as sa

revision = "7ab84c9d1230"
down_revision = "60cd5c95fde1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("account_operation_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("platform_account_id", sa.Integer(), sa.ForeignKey("platform_accounts.id"), nullable=False),
        sa.Column("positioning", sa.Text(), nullable=False, server_default=""),
        sa.Column("target_audience", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_directions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("daily_post_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("preferred_times_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("goals_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("tone", sa.Text(), nullable=False, server_default=""),
        sa.Column("forbidden_topics_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("review_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "platform_account_id", name="uq_operation_profile_account"))
    op.create_index("ix_account_operation_profiles_user_id", "account_operation_profiles", ["user_id"])
    op.create_index("ix_account_operation_profiles_platform_account_id", "account_operation_profiles", ["platform_account_id"])
    op.create_table("operation_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("platform_account_id", sa.Integer(), sa.ForeignKey("platform_accounts.id"), nullable=False),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("content_direction", sa.String(128), nullable=False, server_default=""),
        sa.Column("topic", sa.String(256), nullable=False, server_default=""),
        sa.Column("planned_at", sa.DateTime()),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("draft_id", sa.Integer(), sa.ForeignKey("ai_drafts.id")),
        sa.Column("publish_job_id", sa.Integer(), sa.ForeignKey("publish_jobs.id")),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_index("ix_operation_plans_user_id", "operation_plans", ["user_id"])
    op.create_index("ix_operation_plans_platform_account_id", "operation_plans", ["platform_account_id"])
    op.create_index("ix_operation_plans_plan_date", "operation_plans", ["plan_date"])
    op.create_table("learning_insights",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("platform_account_id", sa.Integer(), sa.ForeignKey("platform_accounts.id")),
        sa.Column("insight_type", sa.String(32), nullable=False, server_default="content"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("conclusion", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_learning_insights_user_id", "learning_insights", ["user_id"])
    op.create_index("ix_learning_insights_platform_account_id", "learning_insights", ["platform_account_id"])


def downgrade():
    op.drop_table("learning_insights")
    op.drop_table("operation_plans")
    op.drop_table("account_operation_profiles")
