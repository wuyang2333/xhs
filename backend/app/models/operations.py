from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base
from backend.app.core.time import shanghai_now


class AccountOperationProfile(Base):
    __tablename__ = "account_operation_profiles"
    __table_args__ = (UniqueConstraint("user_id", "platform_account_id", name="uq_operation_profile_account"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    platform_account_id: Mapped[int] = mapped_column(ForeignKey("platform_accounts.id"), index=True)
    homepage_url: Mapped[str] = mapped_column(Text, default="")
    positioning: Mapped[str] = mapped_column(Text, default="")
    target_audience: Mapped[str] = mapped_column(Text, default="")
    content_directions_json: Mapped[str] = mapped_column(Text, default="[]")
    daily_post_count: Mapped[int] = mapped_column(Integer, default=1)
    daily_generate_count: Mapped[int] = mapped_column(Integer, default=1)
    daily_publish_count: Mapped[int] = mapped_column(Integer, default=1)
    preferred_times_json: Mapped[str] = mapped_column(Text, default="[]")
    goals_json: Mapped[str] = mapped_column(Text, default="[]")
    tone: Mapped[str] = mapped_column(Text, default="")
    forbidden_topics_json: Mapped[str] = mapped_column(Text, default="[]")
    review_required: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=shanghai_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=shanghai_now, onupdate=shanghai_now)


class OperationPlan(Base):
    __tablename__ = "operation_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    platform_account_id: Mapped[int] = mapped_column(ForeignKey("platform_accounts.id"), index=True)
    plan_date: Mapped[date] = mapped_column(Date, index=True)
    content_direction: Mapped[str] = mapped_column(String(128), default="")
    topic: Mapped[str] = mapped_column(String(256), default="")
    planned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    draft_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ai_drafts.id"), nullable=True)
    publish_job_id: Mapped[Optional[int]] = mapped_column(ForeignKey("publish_jobs.id"), nullable=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=shanghai_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=shanghai_now, onupdate=shanghai_now)


class LearningInsight(Base):
    __tablename__ = "learning_insights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    platform_account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("platform_accounts.id"), nullable=True, index=True)
    insight_type: Mapped[str] = mapped_column(String(32), default="content")
    title: Mapped[str] = mapped_column(String(200))
    conclusion: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=shanghai_now)


class OperationStrategyProposal(Base):
    __tablename__ = "operation_strategy_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    platform_account_id: Mapped[int] = mapped_column(ForeignKey("platform_accounts.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    source_note_count: Mapped[int] = mapped_column(Integer, default=0)
    strategy_json: Mapped[str] = mapped_column(Text, default="{}")
    analysis_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=shanghai_now)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
