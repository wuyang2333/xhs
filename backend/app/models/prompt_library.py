from __future__ import annotations

from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.core.database import Base
from backend.app.core.time import shanghai_now


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    prompt_type: Mapped[str] = mapped_column(String(32), index=True)
    platform_account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("platform_accounts.id"), nullable=True, index=True)
    # 与图片素材共用同一套产品 / 场景实体，使选图与选指令的分类口径一致。
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    scene_id: Mapped[Optional[int]] = mapped_column(ForeignKey("asset_scenes.id"), nullable=True, index=True)
    # 官方模板随程序更新；客户自建或复制后修改的置 False，永不被升级覆盖。
    is_official: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # 自由文本场景名列表，供 /recommend/match 宽松匹配。与 scene_id 是两个概念，并存。
    scenes_json: Mapped[str] = mapped_column(Text, default="[]")
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    source_type: Mapped[str] = mapped_column(String(32), default="manual")
    source_ref: Mapped[str] = mapped_column(Text, default="")
    source_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=shanghai_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=shanghai_now, onupdate=shanghai_now)


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("prompt_template_id", "version", name="uq_prompt_template_version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prompt_template_id: Mapped[int] = mapped_column(ForeignKey("prompt_templates.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    change_note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(32), default="human")
    approved: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=shanghai_now)


class PromptUsage(Base):
    __tablename__ = "prompt_usages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    prompt_template_id: Mapped[int] = mapped_column(ForeignKey("prompt_templates.id"), index=True)
    prompt_version: Mapped[int] = mapped_column(Integer)
    platform_account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("platform_accounts.id"), nullable=True, index=True)
    module: Mapped[str] = mapped_column(String(32), index=True)
    scene: Mapped[str] = mapped_column(String(128), default="")
    target_type: Mapped[str] = mapped_column(String(32), default="")
    target_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    outcome_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=shanghai_now)


class ObsidianSyncConfig(Base):
    __tablename__ = "obsidian_sync_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    vault_path: Mapped[str] = mapped_column(Text, default="")
    last_scanned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=shanghai_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=shanghai_now, onupdate=shanghai_now)
