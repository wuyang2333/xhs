"""add media asset domain

Revision ID: f41a0e9d2c73
Revises: e83c0a74b912
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "f41a0e9d2c73"
down_revision = "e83c0a74b912"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    cover_foreign_key = [
        sa.ForeignKeyConstraint(
            ["cover_asset_id"],
            ["media_assets.id"],
            name="fk_asset_folders_cover_asset_id",
            ondelete="SET NULL",
        )
    ] if dialect_name == "sqlite" else []
    op.create_table(
        "asset_folders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("cover_asset_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["asset_folders.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        *cover_foreign_key,
        sa.UniqueConstraint("user_id", "parent_id", "name", name="uq_asset_folders_owner_parent_name"),
    )
    op.create_index("ix_asset_folders_user_id", "asset_folders", ["user_id"])
    op.create_index("ix_asset_folders_parent_id", "asset_folders", ["parent_id"])

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("user_id", "name", name="uq_products_owner_name"),
    )
    op.create_index("ix_products_user_id", "products", ["user_id"])

    op.create_table(
        "asset_scenes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("user_id", "name", name="uq_asset_scenes_owner_name"),
    )
    op.create_index("ix_asset_scenes_user_id", "asset_scenes", ["user_id"])

    op.create_table(
        "asset_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("color", sa.String(length=24), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("user_id", "name", name="uq_asset_tags_owner_name"),
    )
    op.create_index("ix_asset_tags_user_id", "asset_tags", ["user_id"])

    op.create_table(
        "media_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("asset_type", sa.String(length=32), nullable=False),
        sa.Column("url", sa.Text(), nullable=False, server_default=""),
        sa.Column("local_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("original_name", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("mime_type", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("file_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="upload"),
        sa.Column("folder_id", sa.Integer(), nullable=True),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("scene_id", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["folder_id"], ["asset_folders.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["scene_id"], ["asset_scenes.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    for column in ("user_id", "asset_type", "source", "folder_id", "product_id", "scene_id", "created_at", "last_used_at"):
        op.create_index(f"ix_media_assets_{column}", "media_assets", [column])

    if dialect_name != "sqlite":
        with op.batch_alter_table("asset_folders") as batch_op:
            batch_op.create_foreign_key(
                "fk_asset_folders_cover_asset_id",
                "media_assets",
                ["cover_asset_id"],
                ["id"],
                ondelete="SET NULL",
            )

    op.create_table(
        "media_asset_tags",
        sa.Column("media_asset_id", sa.Integer(), nullable=False),
        sa.Column("asset_tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["asset_tag_id"], ["asset_tags.id"]),
        sa.ForeignKeyConstraint(["media_asset_id"], ["media_assets.id"]),
        sa.PrimaryKeyConstraint("media_asset_id", "asset_tag_id"),
    )

    with op.batch_alter_table("draft_assets") as batch_op:
        batch_op.add_column(sa.Column("media_asset_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("is_cover", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.create_foreign_key("fk_draft_assets_media_asset_id", "media_assets", ["media_asset_id"], ["id"])
        batch_op.create_index("ix_draft_assets_media_asset_id", ["media_asset_id"])
        batch_op.create_unique_constraint(
            "uq_draft_assets_draft_media_asset",
            ["draft_id", "media_asset_id"],
        )
    if dialect_name in {"sqlite", "postgresql"}:
        op.create_index(
            "uq_draft_assets_one_cover",
            "draft_assets",
            ["draft_id"],
            unique=True,
            sqlite_where=sa.text("is_cover = 1"),
            postgresql_where=sa.text("is_cover"),
        )

    with op.batch_alter_table("publish_assets") as batch_op:
        batch_op.add_column(sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("source_draft_asset_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_publish_assets_source_draft_asset_id",
            "draft_assets",
            ["source_draft_asset_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_publish_assets_source_draft_asset_id", ["source_draft_asset_id"])

    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, publish_job_id FROM publish_assets ORDER BY publish_job_id, id")
    ).fetchall()
    current_job_id = None
    sort_order = 0
    for asset_id, publish_job_id in rows:
        if publish_job_id != current_job_id:
            current_job_id = publish_job_id
            sort_order = 0
        connection.execute(
            sa.text("UPDATE publish_assets SET sort_order = :sort_order WHERE id = :asset_id"),
            {"sort_order": sort_order, "asset_id": asset_id},
        )
        sort_order += 1


def downgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    with op.batch_alter_table("publish_assets") as batch_op:
        batch_op.drop_index("ix_publish_assets_source_draft_asset_id")
        batch_op.drop_constraint("fk_publish_assets_source_draft_asset_id", type_="foreignkey")
        batch_op.drop_column("source_draft_asset_id")
        batch_op.drop_column("sort_order")
    if dialect_name in {"sqlite", "postgresql"}:
        op.drop_index("uq_draft_assets_one_cover", table_name="draft_assets")
    with op.batch_alter_table("draft_assets") as batch_op:
        batch_op.drop_constraint("uq_draft_assets_draft_media_asset", type_="unique")
        batch_op.drop_index("ix_draft_assets_media_asset_id")
        batch_op.drop_constraint("fk_draft_assets_media_asset_id", type_="foreignkey")
        batch_op.drop_column("is_cover")
        batch_op.drop_column("media_asset_id")
    op.drop_table("media_asset_tags")
    with op.batch_alter_table("asset_folders") as batch_op:
        batch_op.drop_constraint("fk_asset_folders_cover_asset_id", type_="foreignkey")
    op.drop_table("media_assets")
    op.drop_table("asset_tags")
    op.drop_table("asset_scenes")
    op.drop_table("products")
    op.drop_table("asset_folders")
