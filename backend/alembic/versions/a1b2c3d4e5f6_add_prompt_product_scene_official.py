"""add product/scene binding and official flag to prompt templates

Revision ID: a1b2c3d4e5f6
Revises: f41a0e9d2c73

注意：这里用原生 op.add_column 而非 batch_alter_table。SQLite 下 batch 模式靠
「重建表」实现，而 prompt_versions 与 prompt_usages 都有外键指向 prompt_templates，
DROP TABLE 会被外键约束拒绝（实测 IntegrityError）。原生 ALTER TABLE ADD COLUMN
不重建表，SQLite 原生支持。外键约束在 SQLite 上无法事后添加，故仅在其他方言上建。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "f41a0e9d2c73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dialect_name = op.get_bind().dialect.name

    op.add_column("prompt_templates", sa.Column("product_id", sa.Integer(), nullable=True))
    op.add_column("prompt_templates", sa.Column("scene_id", sa.Integer(), nullable=True))
    # 官方模板由我方随程序更新；客户自建或复制后修改的永不被升级覆盖。
    op.add_column(
        "prompt_templates",
        sa.Column("is_official", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_index("ix_prompt_templates_product_id", "prompt_templates", ["product_id"])
    op.create_index("ix_prompt_templates_scene_id", "prompt_templates", ["scene_id"])
    op.create_index("ix_prompt_templates_is_official", "prompt_templates", ["is_official"])

    if dialect_name != "sqlite":
        op.create_foreign_key(
            "fk_prompt_templates_product_id", "prompt_templates", "products", ["product_id"], ["id"]
        )
        op.create_foreign_key(
            "fk_prompt_templates_scene_id", "prompt_templates", "asset_scenes", ["scene_id"], ["id"]
        )


def downgrade() -> None:
    dialect_name = op.get_bind().dialect.name

    if dialect_name != "sqlite":
        op.drop_constraint("fk_prompt_templates_scene_id", "prompt_templates", type_="foreignkey")
        op.drop_constraint("fk_prompt_templates_product_id", "prompt_templates", type_="foreignkey")

    op.drop_index("ix_prompt_templates_is_official", table_name="prompt_templates")
    op.drop_index("ix_prompt_templates_scene_id", table_name="prompt_templates")
    op.drop_index("ix_prompt_templates_product_id", table_name="prompt_templates")

    op.drop_column("prompt_templates", "is_official")
    op.drop_column("prompt_templates", "scene_id")
    op.drop_column("prompt_templates", "product_id")
