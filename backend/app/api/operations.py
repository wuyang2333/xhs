from __future__ import annotations

import json
import hashlib
import re
import threading
from datetime import timedelta
from datetime import date, datetime, time
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal, get_db
from backend.app.core.deps import get_current_user
from backend.app.core.time import shanghai_now
from backend.app.core.security import decrypt_text
from backend.app.models import AccountCookieVersion, AccountOperationProfile, AiDraft, AutoTask, LearningInsight, ModelConfig, Note, NoteAsset, NoteComment, OperationPlan, OperationStrategyProposal, PlatformAccount, PublishJob, Task, User, note_tags
from backend.app.services.ai_service import OpenAICompatibleTextClient, TextAiClient

router = APIRouter(prefix="/operations", tags=["operations"])


def _loads(value: str, fallback):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _account(db: Session, user_id: int, account_id: int) -> PlatformAccount:
    account = db.get(PlatformAccount, account_id)
    if not account or account.user_id != user_id or account.platform != "xhs":
        raise HTTPException(status_code=404, detail="小红书账号不存在")
    return account


def _profile_item(row: AccountOperationProfile) -> dict:
    return {
        "id": row.id, "platform_account_id": row.platform_account_id,
        "homepage_url": row.homepage_url,
        "positioning": row.positioning, "target_audience": row.target_audience,
        "content_directions": _loads(row.content_directions_json, []),
        "daily_post_count": row.daily_post_count,
        "daily_generate_count": row.daily_generate_count,
        "daily_publish_count": row.daily_publish_count,
        "preferred_times": _loads(row.preferred_times_json, []),
        "goals": _loads(row.goals_json, []), "tone": row.tone,
        "forbidden_topics": _loads(row.forbidden_topics_json, []),
        "review_required": row.review_required, "status": row.status,
        "created_at": row.created_at, "updated_at": row.updated_at,
    }


def _plan_item(row: OperationPlan) -> dict:
    return {
        "id": row.id, "platform_account_id": row.platform_account_id,
        "plan_date": row.plan_date, "content_direction": row.content_direction,
        "topic": row.topic, "planned_at": row.planned_at, "status": row.status,
        "draft_id": row.draft_id, "publish_job_id": row.publish_job_id,
        "rationale": row.rationale, "created_at": row.created_at, "updated_at": row.updated_at,
    }


def _insight_item(row: LearningInsight) -> dict:
    return {
        "id": row.id, "platform_account_id": row.platform_account_id,
        "insight_type": row.insight_type, "title": row.title, "conclusion": row.conclusion,
        "evidence": _loads(row.evidence_json, {}), "sample_size": row.sample_size,
        "confidence": row.confidence, "status": row.status, "created_at": row.created_at,
    }


def _proposal_item(row: OperationStrategyProposal) -> dict:
    return {
        "id": row.id, "platform_account_id": row.platform_account_id,
        "status": row.status, "source_note_count": row.source_note_count,
        "strategy": _loads(row.strategy_json, {}), "analysis_error": row.analysis_error,
        "created_at": row.created_at, "confirmed_at": row.confirmed_at,
    }


def _note_metrics(note: Note) -> tuple[int, int]:
    raw = note.raw_json or {}
    views = int(raw.get("views", 0) or 0)
    engagement = sum(int(raw.get(key, 0) or 0) for key in ("likes", "collects", "comments", "shares"))
    return views, engagement


def _as_nonnegative_int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _note_published_at(note: Note) -> str | None:
    raw = note.raw_json or {}
    value = next((raw.get(key) for key in ("published_at", "publish_time", "published_time", "create_time", "time") if raw.get(key)), None)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        return value.strip()[:64]
    return None


def _note_recency_key(note: Note) -> tuple[str, int]:
    published_at = _note_published_at(note)
    created_at = getattr(note, "created_at", None)
    fallback = created_at.isoformat() if isinstance(created_at, datetime) else ""
    return published_at or fallback, int(getattr(note, "id", 0) or 0)


def _note_analysis_sample(note: Note) -> dict:
    raw = note.raw_json or {}
    metrics = {key: _as_nonnegative_int(raw.get(key)) for key in ("views", "likes", "collects", "comments", "shares")}
    engagement = sum(metrics[key] for key in ("likes", "collects", "comments", "shares"))
    views = metrics["views"]
    content = re.sub(r"\s+", " ", str(getattr(note, "content", "") or "")).strip()
    return {
        "note_id": str(getattr(note, "note_id", "") or getattr(note, "id", "")),
        "title": str(getattr(note, "title", "") or "")[:512],
        "body_summary": content[:500],
        "source": str(raw.get("source") or "xiaohongshu")[:64],
        **metrics,
        "engagement_rate": round(engagement / views, 6) if views > 0 else None,
        "published_at": _note_published_at(note),
    }


def _select_analysis_samples(notes: list[Note], limit: int = 80) -> list[dict]:
    """Choose a balanced sample instead of showing the model only winners."""
    if limit <= 0:
        return []
    rows = list(notes)
    by_performance = sorted(rows, key=lambda row: (_note_metrics(row)[1], _note_metrics(row)[0]), reverse=True)
    by_recent = sorted(rows, key=_note_recency_key, reverse=True)
    bucket_size = max(1, limit // 3)
    candidates = [
        *((row, "high_performance") for row in by_performance[:bucket_size]),
        *((row, "recent") for row in by_recent[:bucket_size]),
        *((row, "low_performance") for row in reversed(by_performance[-bucket_size:])),
        *((row, "balanced_fill") for row in by_recent),
    ]
    result: list[dict] = []
    seen: dict[str, set[str]] = {}
    for row, group in candidates:
        sample = _note_analysis_sample(row)
        identity = sample["note_id"] or f"row-{getattr(row, 'id', id(row))}"
        if identity in seen:
            seen[identity].add(group)
            for existing in result:
                if existing["note_id"] == identity:
                    existing["sample_group"] = "+".join(sorted(seen[identity]))
                    break
            continue
        seen[identity] = {group}
        sample["sample_group"] = group
        result.append(sample)
        if len(result) >= min(limit, len(rows)):
            break
    return result


def _clean_strategy_output(generated: dict, fallback: dict, valid_note_ids: set[str]) -> dict:
    """Normalize enriched AI output while accepting the legacy JSON shape."""
    result = {**fallback, **generated, "analysis_source": "ai"}
    cleaned_insights = []
    for raw_item in result.get("learning_insights", []):
        if not isinstance(raw_item, dict) or not str(raw_item.get("title", "")).strip() or not str(raw_item.get("conclusion", "")).strip():
            continue
        evidence_ids = raw_item.get("evidence_note_ids", [])
        if not isinstance(evidence_ids, list):
            evidence_ids = []
        evidence_ids = list(dict.fromkeys(
            str(value).strip() for value in evidence_ids
            if str(value).strip() and str(value).strip() in valid_note_ids
        ))[:20]
        try:
            confidence = max(0.0, min(float(raw_item.get("confidence", 0.5)), 1.0))
        except (TypeError, ValueError):
            confidence = 0.5
        cleaned_insights.append({
            **raw_item,
            "type": str(raw_item.get("type", "content"))[:32],
            "title": str(raw_item["title"])[:200],
            "conclusion": str(raw_item["conclusion"]),
            "confidence": confidence,
            "evidence_note_ids": evidence_ids,
            "evidence_summary": str(raw_item.get("evidence_summary", "数据来源于本次账号样本，具体证据待补充")),
            "recommended_action": str(raw_item.get("recommended_action", raw_item["conclusion"])),
        })
    if not cleaned_insights:
        raise ValueError("AI策略缺少有效学习结论")
    result["learning_insights"] = cleaned_insights

    cleaned_topics = []
    seen_topics: set[str] = set()
    for raw_item in result.get("seven_day_topics", []):
        if not isinstance(raw_item, dict):
            continue
        topic = str(raw_item.get("topic", "")).strip()
        if not topic or topic.casefold() in seen_topics:
            continue
        seen_topics.add(topic.casefold())
        direction = str(raw_item.get("direction", "日常内容")).strip() or "日常内容"
        cleaned_topics.append({
            **raw_item,
            "direction": direction,
            "topic": topic[:256],
            "angle": str(raw_item.get("angle", "从真实场景提供具体信息")),
            "title_idea": str(raw_item.get("title_idea", topic))[:40],
            "product_relevance": str(raw_item.get("product_relevance", "待人工判断")),
            "review_required": bool(raw_item.get("review_required", True)),
        })
    if len(cleaned_topics) != 7:
        raise ValueError("AI策略必须包含7个不重复的具体选题")
    result["seven_day_topics"] = cleaned_topics
    return result


def _fallback_strategy(account: PlatformAccount, notes: list[Note], profile: AccountOperationProfile | None = None) -> dict:
    """Build a deliberately product-neutral proposal when AI is unavailable.

    Do not guess a product category from a small or unrelated sample.  A manual,
    active profile remains authoritative and is copied into the proposal below.
    """
    directions = ["高表现内容复盘", "产品或服务特点分享", "使用场景与真实体验", "选购与对比参考"]
    top = sorted(notes, key=lambda row: _note_metrics(row), reverse=True)[:5]
    top_titles = [row.title for row in top if row.title]
    profile_directions = _loads(profile.content_directions_json, []) if profile else []
    has_profile = bool(profile and profile.status == "active")
    if has_profile and profile_directions:
        directions = [str(item).strip() for item in profile_directions if str(item).strip()][:4] or directions
    positioning = (profile.positioning or "").strip() if has_profile and profile else ""
    target_audience = (profile.target_audience or "").strip() if has_profile and profile else ""
    preferred_times = _loads(profile.preferred_times_json, []) if has_profile and profile else []
    goals = _loads(profile.goals_json, []) if has_profile and profile else []
    daily_generate_count = max(1, min(profile.daily_generate_count, 3)) if has_profile and profile else 1
    daily_publish_count = max(1, min(profile.daily_publish_count, daily_generate_count)) if has_profile and profile else 1
    return {
        "analysis_source": "rules",
        "summary": f"基于当前账号同步的{len(notes)}篇内容生成。暂未可靠识别具体产品或赛道，请人工确认账号定位后再应用。",
        "diagnosis": [
            f"已积累{len(notes)}篇可分析内容",
            "当前仅生成通用内容方向，未根据关键词猜测具体产品",
            "手机端公开页只能读取阅读与点赞等可见指标，结论需随新数据持续校正",
        ],
        "positioning": positioning or f"{account.nickname or '该账号'}｜围绕当前内容的实用分享",
        "target_audience": target_audience or "对该主题感兴趣、正在了解或准备购买的用户",
        "content_directions": directions,
        "daily_post_count": daily_publish_count,
        "daily_generate_count": daily_generate_count,
        "daily_publish_count": daily_publish_count,
        "preferred_times": preferred_times or ["12:30", "20:30"],
        "goals": goals or ["建立清晰账号标签", "提高有效阅读与互动", "形成可复制的内容栏目"],
        "tone": (profile.tone or "").strip() if has_profile and profile and profile.tone else "真实、实用、具体；避免未经证实的效果承诺",
        "forbidden_topics": _loads(profile.forbidden_topics_json, []) if has_profile and profile else ["虚构产品或使用体验", "绝对化效果承诺", "未经证实的专业结论"],
        "content_ratio": {"内容复盘": 30, "特点分享": 30, "场景体验": 25, "选购对比": 15},
        "title_rules": ["标题不超过20字", "优先使用真实可观察的细节", "避免连续使用同一标题模板"],
        "learning_insights": [
            {"type": "content", "title": "优先复用高表现场景", "conclusion": f"优先围绕历史高表现标题延伸：{'；'.join(top_titles[:3]) or '暂无高表现样本'}", "confidence": 0.7, "evidence_note_ids": [str(getattr(row, "note_id", "")) for row in top[:3] if getattr(row, "note_id", "")], "evidence_summary": "来自当前账号的高表现样本。", "recommended_action": "围绕这些场景制作不同角度的后续选题。"},
            {"type": "strategy", "title": "先确认账号方向", "conclusion": "在确认具体产品或主题前，先用通用内容结构测试用户反馈，避免凭空扩展品类。", "confidence": 0.9, "evidence_note_ids": [], "evidence_summary": "当前样本不足以可靠识别具体品类。", "recommended_action": "人工确认账号定位和禁用边界后再应用。"},
            {"type": "title", "title": "标题保持具体且简短", "conclusion": "标题限制20字内，优先表达明确人群、场景或问题，不堆叠夸张词。", "confidence": 0.9, "evidence_note_ids": [], "evidence_summary": "规则建议，不是统计结论。", "recommended_action": "审核草稿标题，确保具体、简短、可验证。"},
        ],
        "seven_day_topics": [
            {"direction": directions[index % len(directions)], "topic": f"{directions[index % len(directions)]}实用内容第{index + 1}篇", "angle": "从真实场景提供具体信息", "title_idea": f"{directions[index % len(directions)]}怎么做", "product_relevance": "待人工判断", "review_required": True}
            for index in range(7)
        ],
    }


def _confirmed_profile_context(profile: AccountOperationProfile | None) -> dict:
    if not profile or profile.status != "active":
        return {}
    return {
        "positioning": profile.positioning,
        "target_audience": profile.target_audience,
        "content_directions": _loads(profile.content_directions_json, []),
        "tone": profile.tone,
        "forbidden_topics": _loads(profile.forbidden_topics_json, []),
    }


def _extract_json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("AI策略没有返回JSON对象")
    payload = json.loads(cleaned[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("AI策略格式异常")
    return payload


def _create_strategy_proposal(db: Session, user: User, account: PlatformAccount) -> OperationStrategyProposal:
    notes = db.scalars(select(Note).where(
        Note.user_id == user.id, Note.platform_account_id == account.id
    )).all()
    if not notes:
        raise HTTPException(status_code=400, detail="请先同步账号内容")
    profile = db.scalar(select(AccountOperationProfile).where(
        AccountOperationProfile.user_id == user.id,
        AccountOperationProfile.platform_account_id == account.id,
    ))
    fallback = _fallback_strategy(account, notes, profile)
    strategy = fallback
    analysis_error = ""
    config = db.scalar(select(ModelConfig).where(
        ModelConfig.user_id == user.id, ModelConfig.model_type == "text", ModelConfig.is_default.is_(True)
    ))
    if config and config.encrypted_api_key:
        samples = _select_analysis_samples(notes, limit=80)
        valid_note_ids = {str(item.get("note_id")) for item in samples if item.get("note_id")}
        profile_snapshot = _loads(account.profile_json, {}).get("mobile_profile_snapshot", {})
        try:
            content = OpenAICompatibleTextClient()._complete(
                model_config=config,
                api_key=decrypt_text(config.encrypted_api_key),
                system_prompt=(
                    "你是小红书多账号运营策略分析师。只分析用户提供的当前账号数据，"
                    "不得引用、虚构或套用其他账号的数据；不得根据示例或常识猜测具体产品。"
                    "如果无法从数据可靠识别品类，必须使用中性表达并提示人工确认。医疗健康内容不得承诺疗效。只输出合法JSON。"
                ),
                user_prompt=(
                    "参考专业运营报告的结构，为当前账号生成一份待人工确认的策略。"
                    "必须输出JSON字段：summary字符串；diagnosis字符串数组；positioning；target_audience；"
                    "content_directions字符串数组；daily_generate_count整数1-3；daily_publish_count整数1-3且不得大于生成数；"
                    "daily_post_count等于daily_publish_count；preferred_times字符串数组；"
                    "goals字符串数组；tone；forbidden_topics字符串数组；content_ratio对象；"
                    "title_rules字符串数组（必须含标题不超过20字）；learning_insights数组，元素含type/title/conclusion/confidence/evidence_note_ids/evidence_summary/recommended_action；"
                    "evidence_note_ids只能引用样本中的note_id。seven_day_topics数组必须7项且完全不同，每项含direction/topic/angle/title_idea/product_relevance/review_required；"
                    "选题不得只是替换第几篇，不得重复历史标题，不得虚构体验或承诺疗效；结论要区分事实和建议，数据不足要明确说明。"
                    f"\n当前账号：{account.nickname}\n主页数据：{json.dumps(profile_snapshot, ensure_ascii=False)}"
                    f"\n已确认运营档案（如有，定位、受众和内容方向必须优先保留）：{json.dumps(_confirmed_profile_context(profile), ensure_ascii=False)}"
                    f"\n同步笔记总数：{len(notes)}\n按表现排序的样本：{json.dumps(samples, ensure_ascii=False)}"
                ),
                temperature=0.35,
                timeout=180,
            )
            generated = _extract_json_object(content)
            required = ("positioning", "target_audience", "content_directions", "learning_insights", "seven_day_topics")
            if not all(generated.get(key) for key in required):
                raise ValueError("AI策略缺少必要字段")
            strategy = _clean_strategy_output(generated, fallback, valid_note_ids)
            if profile and profile.status == "active":
                # User-confirmed settings are authoritative over a fresh AI guess.
                for key, value in _confirmed_profile_context(profile).items():
                    if value:
                        strategy[key] = value
            generate_count = max(1, min(int(strategy.get("daily_generate_count", strategy.get("daily_post_count", 1))), 3))
            publish_count = max(1, min(int(strategy.get("daily_publish_count", strategy.get("daily_post_count", generate_count))), generate_count))
            strategy["daily_generate_count"] = generate_count
            strategy["daily_publish_count"] = publish_count
            strategy["daily_post_count"] = publish_count
        except Exception as exc:
            analysis_error = f"AI分析失败，已改用通用规则建议，请人工确认后再应用：{exc}"
    elif not config or not config.encrypted_api_key:
        analysis_error = "未配置可用的文本模型，已生成通用规则建议，请人工确认后再应用。"
    db.query(OperationStrategyProposal).filter(
        OperationStrategyProposal.user_id == user.id,
        OperationStrategyProposal.platform_account_id == account.id,
        OperationStrategyProposal.status == "pending",
    ).update({"status": "superseded"})
    row = OperationStrategyProposal(
        user_id=user.id, platform_account_id=account.id, source_note_count=len(notes),
        strategy_json=json.dumps(strategy, ensure_ascii=False), analysis_error=analysis_error,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


class ProfilePayload(BaseModel):
    homepage_url: str = ""
    positioning: str = ""
    target_audience: str = ""
    content_directions: list[str] = []
    daily_post_count: int = Field(default=1, ge=0, le=10)
    daily_generate_count: int = Field(default=1, ge=0, le=10)
    daily_publish_count: int = Field(default=1, ge=0, le=10)
    preferred_times: list[str] = []
    goals: list[str] = []
    tone: str = ""
    forbidden_topics: list[str] = []
    review_required: bool = True
    status: str = Field(default="active", pattern="^(active|paused)$")


class GeneratePayload(BaseModel):
    plan_date: date = Field(default_factory=date.today)
    plan_date_to: date | None = None
    account_ids: list[int] = []


class SyncContentPayload(BaseModel):
    mode: str = Field(default="full", pattern="^(quick|full|rebuild)$")
    analyze_after_sync: bool = False


class PlanUpdatePayload(BaseModel):
    content_direction: str | None = None
    topic: str | None = None
    planned_at: datetime | None = None
    status: str | None = Field(default=None, pattern="^(pending|drafted|scheduled|published|skipped)$")


class PlanGenerateDraftPayload(BaseModel):
    create_publish_job: bool = False


class InsightPayload(BaseModel):
    platform_account_id: int | None = None
    insight_type: str = "content"
    title: str = Field(min_length=1, max_length=200)
    conclusion: str = Field(min_length=1)
    evidence: dict = {}
    sample_size: int = Field(default=0, ge=0)
    confidence: float = Field(default=0, ge=0, le=1)


class ProposalDecisionPayload(BaseModel):
    strategy: dict | None = None
    # Applying a strategy remains backwards compatible (profile/plan creation),
    # but automatic tasks are paused unless the caller explicitly opts in.
    create_auto_tasks: bool = True
    enable_auto_tasks: bool = False


@router.get("/profiles")
def list_profiles(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(AccountOperationProfile).where(AccountOperationProfile.user_id == current_user.id)).all()
    return {"items": [_profile_item(row) for row in rows]}


@router.put("/profiles/{account_id}")
def save_profile(account_id: int, payload: ProfilePayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _account(db, current_user.id, account_id)
    row = db.scalar(select(AccountOperationProfile).where(
        AccountOperationProfile.user_id == current_user.id,
        AccountOperationProfile.platform_account_id == account_id,
    ))
    if row is None:
        row = AccountOperationProfile(user_id=current_user.id, platform_account_id=account_id)
        db.add(row)
    for key in ("homepage_url", "positioning", "target_audience", "daily_post_count", "daily_generate_count", "daily_publish_count", "tone", "review_required", "status"):
        setattr(row, key, getattr(payload, key))
    row.content_directions_json = json.dumps(payload.content_directions, ensure_ascii=False)
    row.preferred_times_json = json.dumps(payload.preferred_times, ensure_ascii=False)
    row.goals_json = json.dumps(payload.goals, ensure_ascii=False)
    row.forbidden_topics_json = json.dumps(payload.forbidden_topics, ensure_ascii=False)
    row.updated_at = shanghai_now()
    db.commit()
    db.refresh(row)
    return _profile_item(row)


def _sync_account_content_now(
    account_id: int,
    current_user: User,
    db: Session,
    progress: Callable[[int, str, str, dict[str, Any] | None], None] | None = None,
    sync_mode: str = "full",
    analyze_after_sync: bool = False,
):
    def report(value: int, phase: str, message: str, details: dict[str, Any] | None = None) -> None:
        if progress:
            progress(value, phase, message, details)

    target = _account(db, current_user.id, account_id)
    max_notes = {"quick": 100, "full": 500, "rebuild": 500}.get(sync_mode, 500)
    if sync_mode == "rebuild":
        old_notes = db.scalars(select(Note).where(Note.user_id == current_user.id, Note.platform_account_id == account_id)).all()
        old_ids = [note.id for note in old_notes]
        if old_ids:
            db.execute(delete(note_tags).where(note_tags.c.note_id.in_(old_ids)))
            db.execute(delete(NoteAsset).where(NoteAsset.note_id.in_(old_ids)))
            db.execute(delete(NoteComment).where(NoteComment.note_id.in_(old_ids)))
            for draft in db.scalars(select(AiDraft).where(AiDraft.source_note_id.in_(old_ids))).all():
                draft.source_note_id = None
            db.flush()
            db.execute(delete(Note).where(Note.id.in_(old_ids)))
            db.commit()
    # Keep a lightweight, account-scoped status snapshot so the UI can explain
    # when data was last synced even after the background Task is gone.
    sync_meta = _operation_sync_metadata(target)
    target.profile_json = json.dumps(
        {**_loads(target.profile_json, {}), "operation_sync": {
            **sync_meta, "status": "running", "started_at": shanghai_now().isoformat(),
            "finished_at": None, "error": "", "total": 0,
        }}, ensure_ascii=False,
    )
    db.commit()
    report(10, "syncing_local", "正在整理该账号的工作台发布记录")
    profile = db.scalar(select(AccountOperationProfile).where(
        AccountOperationProfile.user_id == current_user.id,
        AccountOperationProfile.platform_account_id == account_id,
    ))
    system_jobs = db.scalars(select(PublishJob).where(
        PublishJob.user_id == current_user.id,
        PublishJob.platform_account_id == account_id,
        PublishJob.status == "published",
    )).all()
    system_saved = 0
    for job in system_jobs:
        note_id = f"system-publish-{job.id}"
        note = db.scalar(select(Note).where(Note.user_id == current_user.id, Note.note_id == note_id))
        if note is None:
            note = Note(user_id=current_user.id, platform_account_id=account_id, platform="xhs", note_id=note_id)
            db.add(note)
            system_saved += 1
        note.title, note.content, note.author_name = job.title, job.body, target.nickname
        note.raw_json = {"source": "publish_job", "publish_job_id": job.id, "likes": 0, "collects": 0, "comments": 0, "shares": 0}
    db.commit()
    report(25, "syncing_remote", "工作台记录已整理，正在读取小红书账号内容")

    remote_saved = 0
    remote_error = ""
    mobile_profile = None
    scan: dict[str, Any] = {}
    if target.sub_type == "android":
        from backend.app.api.accounts import _android_user_from_account
        from backend.app.services.android_xhs_service import AndroidXhsService, android_device_publish_lock
        try:
            serial = target.external_user_id or ""
            with android_device_publish_lock(serial):
                def persist_scan(checkpoint: dict[str, Any]) -> None:
                    nonlocal remote_saved
                    items = checkpoint.get("notes", []) if isinstance(checkpoint.get("notes"), list) else []
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        title = str(item.get("title") or "").strip()
                        if not title:
                            continue
                        # Keep the legacy title fingerprint so a metric change
                        # on an existing note updates the row instead of
                        # creating a duplicate note on every sync.
                        stable = title
                        fingerprint = hashlib.sha1(f"{account_id}:{stable}".encode("utf-8")).hexdigest()[:20]
                        note_id = f"mobile-ui-{fingerprint}"
                        note = db.scalar(select(Note).where(Note.user_id == current_user.id, Note.note_id == note_id))
                        if note is None:
                            note = Note(user_id=current_user.id, platform_account_id=account_id, platform="xhs", note_id=note_id)
                            db.add(note)
                            remote_saved += 1
                        note.title = title
                        note.author_name = target.nickname
                        note.raw_json = {
                            "source": "android_ui", "likes": int(item.get("likes", 0) or 0),
                            "views": int(item.get("views", 0) or 0), "media_type": item.get("media_type", "image"),
                            "collects": 0, "comments": 0, "shares": 0,
                            "accessibility_text": item.get("accessibility_text", ""),
                        }
                    db.commit()
                    scanned = int(checkpoint.get("visible_note_count", len(items)) or 0)
                    scrolls = int(checkpoint.get("scroll_count", 0) or 0)
                    target.profile_json = json.dumps({
                        **_loads(target.profile_json, {}), "operation_sync": {
                            **_operation_sync_metadata(target), "status": "running",
                            "total": scanned + system_saved, "remote_saved": remote_saved,
                            "scroll_count": scrolls, "target_count": max_notes,
                        },
                    }, ensure_ascii=False)
                    db.commit()
                    report(
                        min(64, 25 + int(min(scanned / max_notes, 1) * 39)),
                        "syncing_remote",
                        f"正在读取内容：{min(scanned, max_notes)} / {max_notes} 篇（已翻页 {scrolls} 次）",
                        {"scanned_count": min(scanned, max_notes), "target_count": max_notes, "scroll_count": scrolls, "remote_saved": remote_saved},
                    )

                scan = AndroidXhsService().scan_profile(
                    serial,
                    _android_user_from_account(target),
                    max_scrolls=260 if max_notes == 500 else 90,
                    max_notes=max_notes,
                    resume=sync_mode != "rebuild",
                    progress=persist_scan,
                )
            mobile_profile = scan.get("profile") if isinstance(scan.get("profile"), dict) else {}
            existing_profile = _loads(target.profile_json, {})
            target.profile_json = json.dumps(
                {**existing_profile, "mobile_profile_snapshot": mobile_profile, "mobile_visible_note_count": scan.get("visible_note_count", 0), "mobile_expected_public_count": scan.get("expected_public_count", 0), "mobile_sync_end_reason": scan.get("end_reason", ""), "mobile_last_synced_at": shanghai_now().isoformat()},
                ensure_ascii=False,
            )
            if mobile_profile.get("nickname"):
                target.nickname = str(mobile_profile["nickname"])
            persist_scan(scan)
            remote_error = str(scan.get("scan_error") or "")
            db.commit()
            report(65, "organizing", f"手机内容读取完成，正在整理 {len(scan.get('notes', []))} 篇可见笔记")
        except Exception as exc:
            remote_error = str(exc)

    homepage_url = (profile.homepage_url if profile else "").strip()
    if homepage_url and target.sub_type != "android":
        from backend.app.adapters.xhs.pc_api_adapter import XhsPcApiAdapter
        from backend.app.api.platforms.xhs.crawl import _data_items, _save_normalized_notes
        from backend.app.api.platforms.xhs.pc import _normalize_search_item
        from backend.app.core.security import decrypt_text
        from backend.app.services.account_service import cookie_header_from_text

        pc_account = db.scalar(select(PlatformAccount).where(
            PlatformAccount.user_id == current_user.id,
            PlatformAccount.platform == "xhs",
            PlatformAccount.sub_type == "pc",
            PlatformAccount.status == "active",
        ))
        if pc_account is None:
            remote_error = "未绑定有效 PC 账号，暂时只同步了工作台发布记录"
        else:
            cookie_version = db.scalar(select(AccountCookieVersion).where(
                AccountCookieVersion.platform_account_id == pc_account.id
            ).order_by(AccountCookieVersion.created_at.desc()))
            if cookie_version is None:
                remote_error = "PC 账号没有可用登录凭证"
            else:
                try:
                    cookies = cookie_header_from_text(decrypt_text(cookie_version.encrypted_cookies))
                    success, message, raw = XhsPcApiAdapter(cookies).get_user_notes(homepage_url)
                    if not success:
                        raise RuntimeError(message or "主页笔记读取失败")
                    items = raw if isinstance(raw, list) else _data_items(raw)
                    normalized = [_normalize_search_item(item) for item in items]
                    remote_saved = len(_save_normalized_notes(db, target, normalized))
                    report(65, "organizing", f"主页内容读取完成，正在整理 {len(normalized)} 篇笔记")
                except Exception as exc:
                    remote_error = str(exc)
    elif target.sub_type != "android" and system_saved == 0:
        remote_error = "请先在运营档案填写小红书主页链接"

    notes = db.scalars(select(Note).where(
        Note.user_id == current_user.id, Note.platform_account_id == account_id
    ).order_by(Note.created_at.desc())).all()
    proposal = None
    if notes and not remote_error and analyze_after_sync:
        try:
            report(75, "analyzing", f"已同步 {len(notes)} 篇内容，正在生成 AI 运营方案")
            proposal = _create_strategy_proposal(db, current_user, target)
            report(95, "saving", "运营方案已生成，正在保存分析结果")
        except Exception as exc:
            remote_error = f"内容同步成功，但策略分析失败：{exc}"
    # Persist the final state independently of the transient Task row.
    target.profile_json = json.dumps(
        {**_loads(target.profile_json, {}), "operation_sync": {
            "status": "partial" if remote_error and notes else "failed" if remote_error else "completed",
            "started_at": _operation_sync_metadata(target).get("started_at"),
            "finished_at": shanghai_now().isoformat(), "error": remote_error,
            "total": len(notes), "remote_saved": remote_saved,
            "target_count": max_notes, "sync_mode": sync_mode,
            "scanned_count": int((scan.get("visible_note_count", 0) if isinstance(scan, dict) else 0) or 0),
            "scroll_count": int((scan.get("scroll_count", 0) if isinstance(scan, dict) else 0) or 0),
            "end_reason": str(scan.get("end_reason", "") if isinstance(scan, dict) else ""),
            "system_saved": system_saved,
            "analysis_proposal_id": (proposal.id if proposal else None),
        }}, ensure_ascii=False,
    )
    db.commit()
    return {
        "account_id": account_id, "system_saved": system_saved, "remote_saved": remote_saved,
        "total": len(notes), "remote_error": remote_error, "mobile_profile": mobile_profile,
        "sync_mode": sync_mode, "analyze_after_sync": analyze_after_sync,
        "scanned_count": int((scan.get("visible_note_count", 0) if isinstance(scan, dict) else 0) or 0),
        "target_count": max_notes,
        "scroll_count": int((scan.get("scroll_count", 0) if isinstance(scan, dict) else 0) or 0),
        "end_reason": str(scan.get("end_reason", "") if isinstance(scan, dict) else ""),
        "strategy_proposal": _proposal_item(proposal) if proposal else None,
    }


def _operation_task_item(task: Task) -> dict:
    return {
        "id": task.id,
        "platform": task.platform,
        "task_type": task.task_type,
        "status": task.status,
        "progress": task.progress,
        "payload": task.payload or {},
        "created_at": task.created_at.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "error_type": task.error_type,
    }


def _operation_sync_metadata(account: PlatformAccount) -> dict:
    """Read persisted sync metadata without exposing credentials."""
    profile = _loads(account.profile_json, {})
    value = profile.get("operation_sync")
    return value if isinstance(value, dict) else {}


def _active_operation_task(db: Session, user_id: int, account_id: int) -> Task | None:
    candidates = db.scalars(select(Task).where(
        Task.user_id == user_id,
        Task.task_type.in_(("account_content_sync", "account_strategy_analysis")),
        Task.status.in_(("pending", "running")),
    ).order_by(Task.id.desc())).all()
    now = shanghai_now()
    for task in candidates:
        if int((task.payload or {}).get("account_id", 0)) != account_id:
            continue
        # Background threads do not survive a process restart.  A running task
        # with no heartbeat for 30 minutes is therefore unrecoverable and must
        # not permanently block the account from starting a new operation.
        reference = task.started_at or task.created_at
        if reference and reference < now - timedelta(minutes=30):
            task.status = "failed"
            task.finished_at = now
            task.error_type = "operation_task_stale"
            task.payload = {
                **(task.payload or {}), "phase": "failed",
                "message": "任务运行超时或服务已重启，请重新执行",
            }
            account = db.get(PlatformAccount, account_id)
            if account is not None and account.user_id == user_id and task.task_type == "account_content_sync":
                previous = _operation_sync_metadata(account)
                account.profile_json = json.dumps({
                    **_loads(account.profile_json, {}),
                    "operation_sync": {
                        **previous, "status": "failed", "finished_at": now.isoformat(),
                        "error": "同步任务运行超时或服务已重启，请重新执行",
                    },
                }, ensure_ascii=False)
            db.commit()
            continue
        return task
    return None


def _run_operation_task(task_id: int, mode: str) -> None:
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return
        user = db.get(User, task.user_id)
        account_id = int((task.payload or {}).get("account_id", 0))
        if user is None or not account_id:
            raise RuntimeError("运营任务缺少账号信息")
        task.status = "running"
        task.started_at = shanghai_now()
        task.progress = 5
        task.payload = {**(task.payload or {}), "phase": "starting", "message": "任务已开始"}
        db.commit()

        def report(value: int, phase: str, message: str, details: dict[str, Any] | None = None) -> None:
            task.progress = value
            task.payload = {**(task.payload or {}), "phase": phase, "message": message, **(details or {})}
            db.commit()

        if mode == "sync":
            task_options = task.payload or {}
            result = _sync_account_content_now(
                account_id, user, db, report,
                sync_mode=str(task_options.get("sync_mode", "full")),
                analyze_after_sync=bool(task_options.get("analyze_after_sync", False)),
            )
            summary = {
                "total": result["total"],
                "remote_saved": result["remote_saved"],
                "remote_error": result["remote_error"],
                "proposal_id": (result.get("strategy_proposal") or {}).get("id"),
                "sync_mode": result.get("sync_mode"),
                "analyze_after_sync": result.get("analyze_after_sync"),
                "scanned_count": result.get("scanned_count", 0),
                "target_count": result.get("target_count", 0),
                "scroll_count": result.get("scroll_count", 0),
                "end_reason": result.get("end_reason", ""),
            }
        else:
            account = _account(db, user.id, account_id)
            report(35, "analyzing", "正在读取已同步内容并调用默认文本模型")
            proposal = _create_strategy_proposal(db, user, account)
            report(90, "saving", "运营方案已生成，正在保存分析结果")
            summary = {"proposal_id": proposal.id, "source_note_count": proposal.source_note_count, "analysis_error": proposal.analysis_error}
        task.status = "completed"
        task.progress = 100
        task.finished_at = shanghai_now()
        task.payload = {
            **(task.payload or {}), **summary, "phase": "completed",
            "message": (
                "同步完成（部分结果已保存）" if mode == "sync" and summary.get("remote_error")
                else "内容同步已完成" if mode == "sync" and not summary.get("analyze_after_sync")
                else "同步与分析已完成" if mode == "sync"
                else "重新分析已完成"
            ),
        }
        db.commit()
    except Exception as exc:
        db.rollback()
        task = db.get(Task, task_id)
        if task is not None:
            task.status = "failed"
            task.finished_at = shanghai_now()
            task.error_type = "operation_sync_failed" if mode == "sync" else "strategy_analysis_failed"
            task.payload = {**(task.payload or {}), "phase": "failed", "message": str(exc)}
            if mode == "sync":
                account_id = int((task.payload or {}).get("account_id", 0))
                account = db.get(PlatformAccount, account_id) if account_id else None
                if account is not None and account.user_id == task.user_id:
                    previous = _operation_sync_metadata(account)
                    account.profile_json = json.dumps({
                        **_loads(account.profile_json, {}),
                        "operation_sync": {
                            **previous, "status": "failed",
                            "finished_at": shanghai_now().isoformat(), "error": str(exc),
                        },
                    }, ensure_ascii=False)
            db.commit()
    finally:
        db.close()


def _start_operation_task(account_id: int, mode: str, current_user: User, db: Session, options: dict[str, Any] | None = None) -> dict:
    _account(db, current_user.id, account_id)
    active = _active_operation_task(db, current_user.id, account_id)
    if active is not None:
        return _operation_task_item(active)
    task = Task(
        user_id=current_user.id,
        platform="xhs",
        task_type="account_content_sync" if mode == "sync" else "account_strategy_analysis",
        status="pending",
        progress=0,
        payload={"account_id": account_id, "phase": "queued", "message": "任务已加入队列", **(options or {})},
        max_retries=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    threading.Thread(target=_run_operation_task, args=(task.id, mode), name=f"operation-{mode}-{task.id}", daemon=True).start()
    return _operation_task_item(task)


@router.post("/accounts/{account_id}/sync-content")
def sync_account_content(account_id: int, payload: SyncContentPayload | None = None, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    options = payload.model_dump() if payload else {"mode": "full", "analyze_after_sync": False}
    return _start_operation_task(account_id, "sync", current_user, db, {"sync_mode": options["mode"], "analyze_after_sync": options["analyze_after_sync"]})


@router.delete("/accounts/{account_id}/content")
def clear_account_content(account_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = _account(db, current_user.id, account_id)
    notes = db.scalars(select(Note).where(
        Note.user_id == current_user.id,
        Note.platform_account_id == target.id,
    )).all()
    note_ids = [note.id for note in notes]
    if note_ids:
        db.execute(delete(note_tags).where(note_tags.c.note_id.in_(note_ids)))
        db.execute(delete(NoteAsset).where(NoteAsset.note_id.in_(note_ids)))
        db.execute(delete(NoteComment).where(NoteComment.note_id.in_(note_ids)))
        for draft in db.scalars(select(AiDraft).where(AiDraft.source_note_id.in_(note_ids))).all():
            draft.source_note_id = None
        db.flush()
        db.execute(delete(Note).where(Note.id.in_(note_ids)))
        db.commit()
    return {"account_id": account_id, "deleted": len(note_ids)}


@router.get("/strategy-proposals")
def list_strategy_proposals(account_id: int | None = None, status: str | None = None, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    statement = select(OperationStrategyProposal).where(OperationStrategyProposal.user_id == current_user.id)
    if account_id:
        statement = statement.where(OperationStrategyProposal.platform_account_id == account_id)
    if status:
        statement = statement.where(OperationStrategyProposal.status == status)
    rows = db.scalars(statement.order_by(OperationStrategyProposal.created_at.desc())).all()
    return {"items": [_proposal_item(row) for row in rows]}


@router.get("/accounts/{account_id}/operation-task")
def latest_operation_task(account_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _account(db, current_user.id, account_id)
    candidates = db.scalars(select(Task).where(
        Task.user_id == current_user.id,
        Task.task_type.in_(("account_content_sync", "account_strategy_analysis")),
    ).order_by(Task.id.desc()).limit(50)).all()
    task = next((row for row in candidates if int((row.payload or {}).get("account_id", 0)) == account_id), None)
    return {"task": _operation_task_item(task) if task else None}


@router.get("/accounts/{account_id}/summary")
def account_operation_summary(account_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return a compact, account-scoped snapshot for the operations console.

    This endpoint intentionally combines persisted sync metadata with the
    latest proposal/profile and execution counters.  It is read-only and does
    not trigger a crawl or an AI request, so it is safe to poll from the UI.
    """
    account = _account(db, current_user.id, account_id)
    profile = db.scalar(select(AccountOperationProfile).where(
        AccountOperationProfile.user_id == current_user.id,
        AccountOperationProfile.platform_account_id == account_id,
    ))
    latest_proposal = db.scalar(select(OperationStrategyProposal).where(
        OperationStrategyProposal.user_id == current_user.id,
        OperationStrategyProposal.platform_account_id == account_id,
    ).order_by(OperationStrategyProposal.created_at.desc()))
    note_count = db.scalar(select(func.count(Note.id)).where(
        Note.user_id == current_user.id, Note.platform_account_id == account_id,
    )) or 0
    today_plan_count = db.scalar(select(func.count(OperationPlan.id)).where(
        OperationPlan.user_id == current_user.id,
        OperationPlan.platform_account_id == account_id,
        OperationPlan.plan_date == date.today(),
    )) or 0
    auto_tasks = db.scalars(select(AutoTask).where(
        AutoTask.user_id == current_user.id, AutoTask.creator_account_id == account_id,
    )).all()
    task_candidates = db.scalars(select(Task).where(
        Task.user_id == current_user.id,
        Task.task_type.in_(("account_content_sync", "account_strategy_analysis")),
    ).order_by(Task.id.desc()).limit(50)).all()
    # account_id lives in the JSON payload on legacy Task rows, so account
    # filtering remains in Python until that shared model is migrated.
    task = next((row for row in task_candidates if int((row.payload or {}).get("account_id", 0)) == account_id), None)
    sync_meta = _operation_sync_metadata(account)
    default_text = db.scalar(select(ModelConfig).where(
        ModelConfig.user_id == current_user.id, ModelConfig.model_type == "text", ModelConfig.is_default.is_(True)
    ))
    return {
        "account": {"id": account.id, "nickname": account.nickname, "sub_type": account.sub_type, "status": account.status},
        "sync": {"note_count": int(note_count), **sync_meta},
        "analysis": {
            "latest_proposal": _proposal_item(latest_proposal) if latest_proposal else None,
            "model": {"id": default_text.id, "name": default_text.name, "model_name": default_text.model_name} if default_text else None,
        },
        "profile": _profile_item(profile) if profile else None,
        "execution": {
            "today_plan_count": int(today_plan_count),
            "auto_task_count": len(auto_tasks),
            "auto_task_active_count": sum(1 for item in auto_tasks if item.status == "active"),
            "auto_task_paused_count": sum(1 for item in auto_tasks if item.status == "paused"),
        },
        "task": _operation_task_item(task) if task else None,
    }


@router.post("/accounts/{account_id}/analyze-strategy")
def analyze_strategy(account_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _start_operation_task(account_id, "analyze", current_user, db)


@router.post("/strategy-proposals/{proposal_id}/approve")
def approve_strategy(proposal_id: int, payload: ProposalDecisionPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    proposal = db.get(OperationStrategyProposal, proposal_id)
    if not proposal or proposal.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="策略方案不存在")
    if proposal.status != "pending":
        raise HTTPException(status_code=400, detail="该策略方案已处理")
    strategy = payload.strategy or _loads(proposal.strategy_json, {})
    account = _account(db, current_user.id, proposal.platform_account_id)
    profile = db.scalar(select(AccountOperationProfile).where(
        AccountOperationProfile.user_id == current_user.id,
        AccountOperationProfile.platform_account_id == account.id,
    ))
    if profile is None:
        profile = AccountOperationProfile(user_id=current_user.id, platform_account_id=account.id)
        db.add(profile)
    profile.positioning = str(strategy.get("positioning", ""))
    profile.target_audience = str(strategy.get("target_audience", ""))
    profile.content_directions_json = json.dumps(strategy.get("content_directions", []), ensure_ascii=False)
    generate_count = max(1, min(int(strategy.get("daily_generate_count", strategy.get("daily_post_count", 1))), 3))
    publish_count = max(1, min(int(strategy.get("daily_publish_count", strategy.get("daily_post_count", generate_count))), generate_count))
    profile.daily_generate_count = generate_count
    profile.daily_publish_count = publish_count
    profile.daily_post_count = publish_count
    profile.preferred_times_json = json.dumps(strategy.get("preferred_times", ["20:30"]), ensure_ascii=False)
    profile.goals_json = json.dumps(strategy.get("goals", []), ensure_ascii=False)
    profile.tone = str(strategy.get("tone", ""))
    profile.forbidden_topics_json = json.dumps(strategy.get("forbidden_topics", []), ensure_ascii=False)
    profile.review_required = True
    profile.status = "active"
    profile.updated_at = shanghai_now()

    for old in db.scalars(select(LearningInsight).where(
        LearningInsight.user_id == current_user.id,
        LearningInsight.platform_account_id == account.id,
        LearningInsight.status == "active",
    )).all():
        old.status = "archived"
    insights_created = 0
    for item in strategy.get("learning_insights", []):
        if not isinstance(item, dict) or not item.get("title") or not item.get("conclusion"):
            continue
        db.add(LearningInsight(
            user_id=current_user.id, platform_account_id=account.id,
            insight_type=str(item.get("type", "content"))[:32],
            title=str(item["title"])[:200], conclusion=str(item["conclusion"]),
            evidence_json=json.dumps({
                "proposal_id": proposal.id,
                "source_note_count": proposal.source_note_count,
                "evidence_note_ids": item.get("evidence_note_ids", []),
                "evidence_summary": item.get("evidence_summary", ""),
                "recommended_action": item.get("recommended_action", ""),
            }, ensure_ascii=False),
            sample_size=proposal.source_note_count,
            confidence=max(0, min(float(item.get("confidence", 0.7)), 1)),
        ))
        insights_created += 1

    topics = [item for item in strategy.get("seven_day_topics", []) if isinstance(item, dict)]
    times = strategy.get("preferred_times", ["20:30"]) or ["20:30"]
    plans_created = 0
    for day_index in range(7):
        plan_date = date.today() + timedelta(days=day_index)
        existing_count = db.scalar(select(func.count(OperationPlan.id)).where(
            OperationPlan.user_id == current_user.id,
            OperationPlan.platform_account_id == account.id,
            OperationPlan.plan_date == plan_date,
        )) or 0
        for slot_index in range(existing_count, generate_count):
            topic_item = topics[(day_index * generate_count + slot_index) % len(topics)] if topics else {}
            time_text = str(times[slot_index % len(times)])
            try:
                hour, minute = [int(part) for part in time_text.split(":", 1)]
            except (ValueError, TypeError):
                hour, minute = 20, 30
            db.add(OperationPlan(
                user_id=current_user.id, platform_account_id=account.id, plan_date=plan_date,
                content_direction=str(topic_item.get("direction", "日常内容"))[:128],
                topic=str(topic_item.get("topic", f"第{day_index + 1}天第{slot_index + 1}篇"))[:256],
                planned_at=datetime.combine(plan_date, time(hour, minute)),
                rationale=f"依据已确认策略方案 #{proposal.id} 生成；每日生成{generate_count}篇、发布{publish_count}篇",
            ))
            plans_created += 1

    auto_tasks_created = 0
    auto_tasks_updated = 0
    if payload.create_auto_tasks:
        # 一个账号一套自动运营策略；每个发布时间对应一个独立任务，便于单独暂停和追踪。
        # 安全默认值是 paused：确认运营方案不等于授权系统开始自动执行。
        from backend.app.api.auto_tasks import _calculate_next_run_at
        task_prefix = f"策略自动运营｜{account.id}｜"
        existing_tasks = db.scalars(select(AutoTask).where(
            AutoTask.user_id == current_user.id,
            AutoTask.creator_account_id == account.id,
            AutoTask.name.like(f"{task_prefix}%"),
        ).order_by(AutoTask.id)).all()
        directions = [str(item) for item in strategy.get("content_directions", []) if str(item).strip()] or ["日常内容"]
        instruction = (
            f"账号定位：{profile.positioning}。目标人群：{profile.target_audience}。"
            f"语气：{profile.tone}。标题严格不超过20字。"
            f"禁用内容：{'、'.join(_loads(profile.forbidden_topics_json, []))}。"
            "生成原创小红书图文草稿并配图，进入发布中心；不同账号内容不得混用。"
        )
        for index in range(generate_count):
            task = existing_tasks[index] if index < len(existing_tasks) else AutoTask(
                user_id=current_user.id, pc_account_id=account.id, creator_account_id=account.id,
            )
            if index >= len(existing_tasks):
                db.add(task)
                auto_tasks_created += 1
            else:
                auto_tasks_updated += 1
            task.name = f"{task_prefix}{index + 1}" + ("｜仅草稿" if index >= publish_count else "")
            task.keywords = directions
            task.ai_instruction = instruction
            task.schedule_type = "daily"
            task.schedule_time = str(times[index % len(times)])
            task.schedule_days = ""
            task.schedule_interval_hours = 24
            task.status = "active" if payload.enable_auto_tasks else "paused"
            if payload.enable_auto_tasks:
                _calculate_next_run_at(task)
            else:
                task.next_run_at = None
        for task in existing_tasks[generate_count:]:
            task.status = "paused"
            task.next_run_at = None
    proposal.strategy_json = json.dumps(strategy, ensure_ascii=False)
    proposal.status = "approved"
    proposal.confirmed_at = shanghai_now()
    db.commit()
    db.refresh(profile)
    db.refresh(proposal)
    return {
        "proposal": _proposal_item(proposal), "profile": _profile_item(profile),
        "insights_created": insights_created, "plans_created": plans_created,
        "auto_tasks_created": auto_tasks_created, "auto_tasks_updated": auto_tasks_updated,
        "auto_tasks_enabled": bool(payload.create_auto_tasks and payload.enable_auto_tasks),
    }


@router.post("/strategy-proposals/{proposal_id}/reject")
def reject_strategy(proposal_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    proposal = db.get(OperationStrategyProposal, proposal_id)
    if not proposal or proposal.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="策略方案不存在")
    if proposal.status != "pending":
        raise HTTPException(status_code=400, detail="该策略方案已处理")
    proposal.status = "rejected"
    proposal.confirmed_at = shanghai_now()
    db.commit()
    db.refresh(proposal)
    return _proposal_item(proposal)


@router.get("/accounts/{account_id}/content")
def account_content(account_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _account(db, current_user.id, account_id)
    rows = db.scalars(select(Note).where(
        Note.user_id == current_user.id, Note.platform_account_id == account_id
    ).order_by(Note.created_at.desc())).all()
    items = []
    for row in rows:
        raw = row.raw_json or {}
        items.append({
            "id": row.id, "note_id": row.note_id, "title": row.title, "content": row.content,
            "likes": int(raw.get("likes", 0) or 0), "collects": int(raw.get("collects", 0) or 0),
            "views": int(raw.get("views", 0) or 0),
            "comments": int(raw.get("comments", 0) or 0), "shares": int(raw.get("shares", 0) or 0),
            "source": raw.get("source", "xiaohongshu"), "created_at": row.created_at,
        })
    return {"items": items, "total": len(items)}


@router.get("/plans")
def list_plans(
    plan_date: date | None = None,
    plan_date_to: date | None = None,
    account_id: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    statement = select(OperationPlan).where(OperationPlan.user_id == current_user.id)
    if plan_date and plan_date_to:
        statement = statement.where(OperationPlan.plan_date >= plan_date, OperationPlan.plan_date <= plan_date_to)
    elif plan_date:
        statement = statement.where(OperationPlan.plan_date == plan_date)
    elif plan_date_to:
        statement = statement.where(OperationPlan.plan_date <= plan_date_to)
    if account_id:
        statement = statement.where(OperationPlan.platform_account_id == account_id)
    rows = db.scalars(statement.order_by(OperationPlan.planned_at, OperationPlan.id)).all()
    return {"items": [_plan_item(row) for row in rows]}


@router.post("/plans/generate")
def generate_plans(payload: GeneratePayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    statement = select(AccountOperationProfile).where(
        AccountOperationProfile.user_id == current_user.id, AccountOperationProfile.status == "active"
    )
    if payload.account_ids:
        statement = statement.where(AccountOperationProfile.platform_account_id.in_(payload.account_ids))
    profiles = db.scalars(statement).all()
    end_date = payload.plan_date_to or payload.plan_date
    if end_date < payload.plan_date:
        raise HTTPException(status_code=400, detail="结束日期不能早于开始日期")
    if (end_date - payload.plan_date).days > 30:
        raise HTTPException(status_code=400, detail="单次最多生成未来 31 天排期")
    created = []
    existing_total = 0
    daily_summary: list[dict[str, Any]] = []
    for plan_date in (payload.plan_date + timedelta(days=offset) for offset in range((end_date - payload.plan_date).days + 1)):
        day_created = 0
        day_existing = 0
        for profile in profiles:
            existing = db.scalars(select(OperationPlan).where(
                OperationPlan.user_id == current_user.id,
                OperationPlan.platform_account_id == profile.platform_account_id,
                OperationPlan.plan_date == plan_date,
            )).all()
            day_existing += len(existing)
            existing_total += len(existing)
            directions = _loads(profile.content_directions_json, []) or ["日常内容"]
            preferred_times = _loads(profile.preferred_times_json, []) or ["10:00", "18:00", "20:30"]
            for index in range(len(existing), profile.daily_generate_count):
                time_text = preferred_times[index % len(preferred_times)]
                try:
                    hour, minute = [int(part) for part in time_text.split(":", 1)]
                    planned_at = datetime.combine(plan_date, time(hour, minute))
                except (ValueError, TypeError):
                    planned_at = datetime.combine(plan_date, time(10, 0))
                direction = directions[index % len(directions)]
                row = OperationPlan(
                    user_id=current_user.id, platform_account_id=profile.platform_account_id,
                    plan_date=plan_date, content_direction=direction,
                    topic=f"{direction}｜{plan_date.month}月{plan_date.day}日" + (f"·第{index + 1}篇" if profile.daily_generate_count > 1 else ""), planned_at=planned_at,
                    rationale="依据账号运营档案中的内容方向、频次和发布时间自动生成",
                )
                db.add(row)
                created.append(row)
                day_created += 1
        daily_summary.append({"plan_date": plan_date.isoformat(), "created": day_created, "existing": day_existing})
    db.commit()
    for row in created:
        db.refresh(row)
    return {
        "created": len(created), "existing": existing_total,
        "days_requested": len(daily_summary),
        "days_covered": sum(1 for item in daily_summary if item["created"] or item["existing"]),
        "daily": daily_summary,
        "items": [_plan_item(row) for row in created],
    }


@router.patch("/plans/{plan_id}")
def update_plan(plan_id: int, payload: PlanUpdatePayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.get(OperationPlan, plan_id)
    if not row or row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="运营计划不存在")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    row.updated_at = shanghai_now()
    db.commit()
    db.refresh(row)
    return _plan_item(row)


def _draft_item(row: AiDraft) -> dict:
    return {
        "id": row.id,
        "platform": row.platform,
        "platform_account_id": row.platform_account_id,
        "title": row.title,
        "body": row.body,
        "created_at": row.created_at.isoformat(),
    }


def _publish_job_item(row: PublishJob) -> dict:
    return {
        "id": row.id,
        "platform_account_id": row.platform_account_id,
        "source_draft_id": row.source_draft_id,
        "title": row.title,
        "body": row.body,
        "publish_mode": row.publish_mode,
        "status": row.status,
        "scheduled_at": row.scheduled_at.isoformat() if row.scheduled_at else None,
    }


@router.post("/plans/{plan_id}/generate-draft")
def generate_plan_draft(
    plan_id: int,
    payload: PlanGenerateDraftPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    text_ai_client: TextAiClient = Depends(lambda: OpenAICompatibleTextClient()),
):
    """Generate one draft for a plan, optionally staging a publish job.

    A created PublishJob always remains non-due: status is pending but
    publish_mode is immediate and this endpoint never invokes a publisher or
    scheduler.  The user must explicitly start publishing from Publish Center.
    """
    plan = db.get(OperationPlan, plan_id)
    if plan is None or plan.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="运营计划不存在")
    if plan.draft_id is not None:
        draft = db.get(AiDraft, plan.draft_id)
        if draft is not None and draft.user_id == current_user.id:
            job = db.get(PublishJob, plan.publish_job_id) if plan.publish_job_id else None
            return {
                "plan": _plan_item(plan), "draft": _draft_item(draft),
                "publish_job": _publish_job_item(job) if job else None,
                "created": False,
            }
        raise HTTPException(status_code=409, detail="计划已关联不可用的草稿，请先修复关联")

    account = _account(db, current_user.id, plan.platform_account_id)
    profile = db.scalar(select(AccountOperationProfile).where(
        AccountOperationProfile.user_id == current_user.id,
        AccountOperationProfile.platform_account_id == account.id,
    ))
    config = db.scalar(select(ModelConfig).where(
        ModelConfig.user_id == current_user.id,
        ModelConfig.model_type == "text",
        ModelConfig.is_default.is_(True),
    ))
    if config is None or not config.encrypted_api_key:
        raise HTTPException(status_code=400, detail="请先配置可用的默认文本模型")
    positioning = (profile.positioning if profile else "") or "未确认，请使用中性表达"
    audience = (profile.target_audience if profile else "") or "当前账号受众"
    tone = (profile.tone if profile else "") or "真实、自然、具体"
    forbidden = _loads(profile.forbidden_topics_json, []) if profile else []
    instruction = (
        f"账号定位：{positioning}。目标人群：{audience}。内容方向：{plan.content_direction or '日常内容'}。"
        f"语气：{tone}。禁用内容：{'、'.join(str(item) for item in forbidden) or '虚构体验、绝对化承诺'}。"
        "必须原创，不得虚构个人经历、产品参数或效果；标题不超过20字；只生成可人工审核的草稿。"
    )
    try:
        result = text_ai_client.generate_note(
            model_config=config,
            api_key=decrypt_text(config.encrypted_api_key),
            topic=plan.topic,
            reference=f"运营计划日期：{plan.plan_date.isoformat()}；规划依据：{plan.rationale or '无'}",
            instruction=instruction,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI草稿生成失败：{exc}") from exc
    title = str(result.get("title") or plan.topic).strip()[:20]
    body = str(result.get("body") or "").strip()
    if not title or not body:
        raise HTTPException(status_code=502, detail="AI没有返回有效的标题和正文")

    draft = AiDraft(
        user_id=current_user.id,
        platform_account_id=account.id,
        platform="xhs",
        title=title,
        body=body,
    )
    db.add(draft)
    db.flush()
    plan.draft_id = draft.id
    plan.status = "drafted"
    plan.updated_at = shanghai_now()
    publish_job = None
    if payload.create_publish_job:
        publish_job = PublishJob(
            user_id=current_user.id,
            platform_account_id=account.id,
            source_draft_id=draft.id,
            platform="xhs",
            title=draft.title,
            body=draft.body,
            # Immediate+pending is inert until the explicit Publish Center API
            # is called; scheduled jobs could be picked up automatically.
            publish_mode="immediate",
            publish_options=json.dumps({"source": "operation_plan", "plan_id": plan.id}, ensure_ascii=False),
            status="pending",
            scheduled_at=None,
        )
        db.add(publish_job)
        db.flush()
        plan.publish_job_id = publish_job.id
        plan.status = "scheduled"
    db.commit()
    db.refresh(plan)
    db.refresh(draft)
    if publish_job:
        db.refresh(publish_job)
    return {
        "plan": _plan_item(plan), "draft": _draft_item(draft),
        "publish_job": _publish_job_item(publish_job) if publish_job else None,
        "created": True,
    }


@router.get("/learning/insights")
def list_insights(account_id: int | None = None, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    statement = select(LearningInsight).where(LearningInsight.user_id == current_user.id)
    if account_id:
        statement = statement.where(LearningInsight.platform_account_id == account_id)
    rows = db.scalars(statement.order_by(LearningInsight.created_at.desc())).all()
    return {"items": [_insight_item(row) for row in rows]}


@router.post("/learning/insights")
def create_insight(payload: InsightPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if payload.platform_account_id:
        _account(db, current_user.id, payload.platform_account_id)
    row = LearningInsight(
        user_id=current_user.id, platform_account_id=payload.platform_account_id,
        insight_type=payload.insight_type, title=payload.title, conclusion=payload.conclusion,
        evidence_json=json.dumps(payload.evidence, ensure_ascii=False),
        sample_size=payload.sample_size, confidence=payload.confidence,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _insight_item(row)


@router.get("/learning/overview")
def learning_overview(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile_count = db.scalar(select(func.count()).select_from(AccountOperationProfile).where(AccountOperationProfile.user_id == current_user.id)) or 0
    insight_count = db.scalar(select(func.count()).select_from(LearningInsight).where(LearningInsight.user_id == current_user.id, LearningInsight.status == "active")) or 0
    today_plans = db.scalar(select(func.count()).select_from(OperationPlan).where(OperationPlan.user_id == current_user.id, OperationPlan.plan_date == date.today())) or 0
    published = db.scalar(select(func.count()).select_from(PublishJob).where(PublishJob.user_id == current_user.id, PublishJob.status == "published")) or 0
    synced_notes = db.scalar(select(func.count()).select_from(Note).where(Note.user_id == current_user.id, Note.platform == "xhs")) or 0
    return {"profile_count": profile_count, "active_insight_count": insight_count, "today_plan_count": today_plans, "published_count": published, "synced_note_count": synced_notes}


@router.get("/learning/content-performance")
def content_performance(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Note).where(
        Note.user_id == current_user.id, Note.platform == "xhs"
    ).order_by(Note.created_at.desc())).all()
    items = []
    for row in rows:
        raw = row.raw_json or {}
        likes = int(raw.get("likes", 0) or 0)
        collects = int(raw.get("collects", 0) or 0)
        comments = int(raw.get("comments", 0) or 0)
        shares = int(raw.get("shares", 0) or 0)
        items.append({
            "id": row.id, "platform_account_id": row.platform_account_id, "title": row.title,
            "views": int(raw.get("views", 0) or 0),
            "likes": likes, "collects": collects, "comments": comments, "shares": shares,
            "engagement": likes + collects + comments + shares,
            "source": raw.get("source", "xiaohongshu"), "created_at": row.created_at,
        })
    items.sort(key=lambda item: item["engagement"], reverse=True)
    return {"items": items, "total": len(items)}
