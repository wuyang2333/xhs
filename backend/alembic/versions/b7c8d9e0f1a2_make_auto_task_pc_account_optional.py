"""make auto_tasks.pc_account_id nullable

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6

采集账号改为可选：不填表示跳过抓取，直接按关键词让 AI 生成内容。

此前该字段 NOT NULL，被当成模式开关使用（填 PC 号才抓取，填安卓号走纯生成），
表单上是必填，用户即使不需要抓取也得随便选一个账号。

SQLite 无法直接 ALTER COLUMN 改可空性，必须重建表；batch_alter_table 会处理。
已确认没有其他表外键指向 auto_tasks，重建安全。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "b7c8d9e0f1a2"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("auto_tasks") as batch_op:
        batch_op.alter_column(
            "pc_account_id",
            existing_type=sa.Integer(),
            nullable=True,
        )


def downgrade() -> None:
    # 回退前把空值填成 creator_account_id，否则 NOT NULL 加不回去。
    op.execute(
        "UPDATE auto_tasks SET pc_account_id = creator_account_id "
        "WHERE pc_account_id IS NULL"
    )
    with op.batch_alter_table("auto_tasks") as batch_op:
        batch_op.alter_column(
            "pc_account_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
