from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Table, Text, UniqueConstraint, Column
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base
from backend.app.core.time import shanghai_now


media_asset_tags = Table(
    "media_asset_tags",
    Base.metadata,
    Column("media_asset_id", ForeignKey("media_assets.id"), primary_key=True),
    Column("asset_tag_id", ForeignKey("asset_tags.id"), primary_key=True),
)


class AssetFolder(Base):
    __tablename__ = "asset_folders"
    __table_args__ = (UniqueConstraint("user_id", "parent_id", "name", name="uq_asset_folders_owner_parent_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("asset_folders.id"), nullable=True, index=True)
    cover_asset_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL", name="fk_asset_folders_cover_asset_id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=shanghai_now)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_products_owner_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=shanghai_now)


class AssetScene(Base):
    __tablename__ = "asset_scenes"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_asset_scenes_owner_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=shanghai_now)


class AssetTag(Base):
    __tablename__ = "asset_tags"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_asset_tags_owner_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    color: Mapped[str] = mapped_column(String(24), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=shanghai_now)


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    asset_type: Mapped[str] = mapped_column(String(32), index=True)
    url: Mapped[str] = mapped_column(Text, default="")
    local_path: Mapped[str] = mapped_column(Text, default="")
    original_name: Mapped[str] = mapped_column(String(256), default="")
    mime_type: Mapped[str] = mapped_column(String(128), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(32), default="upload", index=True)
    folder_id: Mapped[Optional[int]] = mapped_column(ForeignKey("asset_folders.id"), nullable=True, index=True)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    scene_id: Mapped[Optional[int]] = mapped_column(ForeignKey("asset_scenes.id"), nullable=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=shanghai_now, index=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
