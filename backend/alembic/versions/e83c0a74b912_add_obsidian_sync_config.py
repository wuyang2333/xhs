"""add obsidian sync config

Revision ID: e83c0a74b912
Revises: d71c0f08a942
"""
from alembic import op
import sqlalchemy as sa


revision = "e83c0a74b912"
down_revision = "d71c0f08a942"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "obsidian_sync_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("vault_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_scanned_at", sa.DateTime(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("user_id", name="uq_obsidian_sync_configs_user_id"),
    )
    op.create_index("ix_obsidian_sync_configs_user_id", "obsidian_sync_configs", ["user_id"], unique=True)


def downgrade() -> None:
    op.drop_table("obsidian_sync_configs")
