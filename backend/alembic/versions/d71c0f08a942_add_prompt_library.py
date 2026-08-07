"""add prompt library

Revision ID: d71c0f08a942
Revises: c4a81f29d731
"""
from alembic import op
import sqlalchemy as sa

revision = "d71c0f08a942"
down_revision = "c4a81f29d731"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("prompt_templates",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False), sa.Column("prompt_type", sa.String(32), nullable=False),
        sa.Column("platform_account_id", sa.Integer(), nullable=True), sa.Column("scenes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("tags_json", sa.Text(), nullable=False, server_default="[]"), sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"), sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"), sa.Column("source_type", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("source_ref", sa.Text(), nullable=False, server_default=""), sa.Column("source_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]), sa.ForeignKeyConstraint(["platform_account_id"], ["platform_accounts.id"]))
    for column in ("user_id", "prompt_type", "platform_account_id", "status"):
        op.create_index(f"ix_prompt_templates_{column}", "prompt_templates", [column])
    op.create_table("prompt_versions",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("prompt_template_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("content", sa.Text(), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=False, server_default=""), sa.Column("created_by", sa.String(32), nullable=False, server_default="human"),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["prompt_template_id"], ["prompt_templates.id"]),
        sa.UniqueConstraint("prompt_template_id", "version", name="uq_prompt_template_version"))
    op.create_index("ix_prompt_versions_prompt_template_id", "prompt_versions", ["prompt_template_id"])
    op.create_table("prompt_usages",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("prompt_template_id", sa.Integer(), nullable=False), sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("platform_account_id", sa.Integer(), nullable=True), sa.Column("module", sa.String(32), nullable=False),
        sa.Column("scene", sa.String(128), nullable=False, server_default=""), sa.Column("target_type", sa.String(32), nullable=False, server_default=""),
        sa.Column("target_id", sa.Integer(), nullable=True), sa.Column("outcome_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["prompt_template_id"], ["prompt_templates.id"]), sa.ForeignKeyConstraint(["platform_account_id"], ["platform_accounts.id"]))
    for column in ("user_id", "prompt_template_id", "platform_account_id", "module"):
        op.create_index(f"ix_prompt_usages_{column}", "prompt_usages", [column])


def downgrade() -> None:
    op.drop_table("prompt_usages")
    op.drop_table("prompt_versions")
    op.drop_table("prompt_templates")
