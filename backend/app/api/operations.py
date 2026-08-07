from __future__ import annotations

import json
import hashlib
import re
from collections import Counter
from datetime import timedelta
from datetime import date, datetime, time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.deps import get_current_user
from backend.app.core.time import shanghai_now
from backend.app.core.security import decrypt_text
from backend.app.models import AccountCookieVersion, AccountOperationProfile, AutoTask, LearningInsight, ModelConfig, Note, OperationPlan, OperationStrategyProposal, PlatformAccount, PublishJob, User
from backend.app.services.ai_service import OpenAICompatibleTextClient

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


def _fallback_strategy(account: PlatformAccount, notes: list[Note]) -> dict:
    categories = {
        "护腰与腰部场景": ("护腰", "腰酸", "腰痛", "久坐", "搬重"),
        "手腕防护": ("手腕", "护腕", "腱鞘"),
        "踝足防护": ("护踝", "崴脚", "脚踝"),
        "呼吸防护": ("口罩", "空气", "鼻炎"),
        "健康护具选购": ("选购", "佩戴", "怎么选", "避坑"),
    }
    scores = Counter()
    for note in notes:
        for category, words in categories.items():
            scores[category] += sum(note.title.count(word) for word in words)
    directions = [name for name, _ in scores.most_common(4)] or ["健康护具选购", "使用场景科普", "真实体验分享"]
    top = sorted(notes, key=lambda row: _note_metrics(row), reverse=True)[:5]
    top_titles = [row.title for row in top if row.title]
    return {
        "summary": f"基于当前账号同步的{len(notes)}篇内容生成。历史内容以{directions[0]}为主，需要减少同质化并建立稳定栏目。",
        "diagnosis": [
            f"已积累{len(notes)}篇可分析内容",
            f"当前主要内容方向：{'、'.join(directions)}",
            "手机端公开页只能读取阅读与点赞等可见指标，结论需随新数据持续校正",
        ],
        "positioning": f"{account.nickname or '该账号'}｜实用健康护具选购与使用指南",
        "target_audience": "久坐办公、体力劳动、运动防护及有日常支撑需求的人群",
        "content_directions": directions,
        "daily_post_count": 1,
        "daily_generate_count": 1,
        "daily_publish_count": 1,
        "preferred_times": ["12:30", "20:30"],
        "goals": ["建立清晰账号标签", "提高有效阅读与互动", "形成可复制的内容栏目"],
        "tone": "真实、实用、专业但不制造焦虑；避免医疗保证和夸大疗效",
        "forbidden_topics": ["治愈或根治承诺", "伪造使用体验", "绝对化效果", "未经证实的医疗结论"],
        "content_ratio": {"痛点场景": 40, "选购科普": 35, "真实体验": 20, "品牌产品": 5},
        "title_rules": ["标题不超过20字", "优先使用具体场景与人群", "避免连续使用同一标题模板"],
        "learning_insights": [
            {"type": "content", "title": "优先复用高表现场景", "conclusion": f"优先围绕历史高表现标题延伸：{'；'.join(top_titles[:3]) or '暂无高表现样本'}", "confidence": 0.7},
            {"type": "strategy", "title": "降低硬广密度", "conclusion": "用场景问题和选购方法建立价值，产品信息作为解决方案的一部分自然出现。", "confidence": 0.75},
            {"type": "title", "title": "标题保持具体且简短", "conclusion": "标题限制20字内，优先表达明确人群、场景或问题，不堆叠夸张词。", "confidence": 0.9},
        ],
        "seven_day_topics": [
            {"direction": directions[index % len(directions)], "topic": f"{directions[index % len(directions)]}实用内容第{index + 1}篇"}
            for index in range(7)
        ],
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
    fallback = _fallback_strategy(account, notes)
    strategy = fallback
    analysis_error = ""
    config = db.scalar(select(ModelConfig).where(
        ModelConfig.user_id == user.id, ModelConfig.model_type == "text", ModelConfig.is_default.is_(True)
    ))
    if config and config.encrypted_api_key:
        ranked = sorted(notes, key=lambda row: _note_metrics(row), reverse=True)
        samples = [
            {"title": row.title, "views": _note_metrics(row)[0], "engagement": _note_metrics(row)[1]}
            for row in ranked[:80]
        ]
        profile_snapshot = _loads(account.profile_json, {}).get("mobile_profile_snapshot", {})
        try:
            content = OpenAICompatibleTextClient()._complete(
                model_config=config,
                api_key=decrypt_text(config.encrypted_api_key),
                system_prompt=(
                    "你是小红书多账号运营策略分析师。只分析用户提供的当前账号数据，"
                    "不得引用、虚构或套用其他账号的数据。医疗健康内容不得承诺疗效。只输出合法JSON。"
                ),
                user_prompt=(
                    "参考专业运营报告的结构，为当前账号生成一份待人工确认的策略。"
                    "必须输出JSON字段：summary字符串；diagnosis字符串数组；positioning；target_audience；"
                    "content_directions字符串数组；daily_generate_count整数1-3；daily_publish_count整数1-3且不得大于生成数；"
                    "daily_post_count等于daily_publish_count；preferred_times字符串数组；"
                    "goals字符串数组；tone；forbidden_topics字符串数组；content_ratio对象；"
                    "title_rules字符串数组（必须含标题不超过20字）；learning_insights数组，元素含type/title/conclusion/confidence；"
                    "seven_day_topics数组，必须7项，每项含direction/topic。"
                    f"\n当前账号：{account.nickname}\n主页数据：{json.dumps(profile_snapshot, ensure_ascii=False)}"
                    f"\n同步笔记总数：{len(notes)}\n按表现排序的样本：{json.dumps(samples, ensure_ascii=False)}"
                ),
                temperature=0.35,
                timeout=180,
            )
            generated = _extract_json_object(content)
            required = ("positioning", "target_audience", "content_directions", "learning_insights", "seven_day_topics")
            if not all(generated.get(key) for key in required):
                raise ValueError("AI策略缺少必要字段")
            strategy = {**fallback, **generated}
            generate_count = max(1, min(int(strategy.get("daily_generate_count", strategy.get("daily_post_count", 1))), 3))
            publish_count = max(1, min(int(strategy.get("daily_publish_count", strategy.get("daily_post_count", generate_count))), generate_count))
            strategy["daily_generate_count"] = generate_count
            strategy["daily_publish_count"] = publish_count
            strategy["daily_post_count"] = publish_count
        except Exception as exc:
            analysis_error = f"AI分析失败，已生成规则分析方案：{exc}"
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
    account_ids: list[int] = []


class PlanUpdatePayload(BaseModel):
    content_direction: str | None = None
    topic: str | None = None
    planned_at: datetime | None = None
    status: str | None = Field(default=None, pattern="^(pending|drafted|scheduled|published|skipped)$")


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


@router.post("/accounts/{account_id}/sync-content")
def sync_account_content(account_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = _account(db, current_user.id, account_id)
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

    remote_saved = 0
    remote_error = ""
    mobile_profile = None
    if target.sub_type == "android":
        from backend.app.services.android_xhs_service import AndroidXhsService
        try:
            scan = AndroidXhsService().scan_profile(target.external_user_id or "", max_scrolls=120)
            mobile_profile = scan.get("profile") if isinstance(scan.get("profile"), dict) else {}
            existing_profile = _loads(target.profile_json, {})
            target.profile_json = json.dumps(
                {**existing_profile, "mobile_profile_snapshot": mobile_profile, "mobile_visible_note_count": scan.get("visible_note_count", 0), "mobile_expected_public_count": scan.get("expected_public_count", 0), "mobile_sync_end_reason": scan.get("end_reason", ""), "mobile_last_synced_at": shanghai_now().isoformat()},
                ensure_ascii=False,
            )
            if mobile_profile.get("nickname"):
                target.nickname = str(mobile_profile["nickname"])
            for item in scan.get("notes", []):
                title = str(item.get("title") or "").strip()
                if not title:
                    continue
                fingerprint = hashlib.sha1(f"{account_id}:{title}".encode("utf-8")).hexdigest()[:20]
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
                    normalized = [_normalize_search_item(item) for item in _data_items(raw)]
                    remote_saved = len(_save_normalized_notes(db, target, normalized))
                except Exception as exc:
                    remote_error = str(exc)
    elif target.sub_type != "android" and system_saved == 0:
        remote_error = "请先在运营档案填写小红书主页链接"

    notes = db.scalars(select(Note).where(
        Note.user_id == current_user.id, Note.platform_account_id == account_id
    ).order_by(Note.created_at.desc())).all()
    proposal = None
    if notes and not remote_error:
        try:
            proposal = _create_strategy_proposal(db, current_user, target)
        except Exception as exc:
            remote_error = f"内容同步成功，但策略分析失败：{exc}"
    return {
        "account_id": account_id, "system_saved": system_saved, "remote_saved": remote_saved,
        "total": len(notes), "remote_error": remote_error, "mobile_profile": mobile_profile,
        "strategy_proposal": _proposal_item(proposal) if proposal else None,
    }


@router.get("/strategy-proposals")
def list_strategy_proposals(account_id: int | None = None, status: str | None = None, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    statement = select(OperationStrategyProposal).where(OperationStrategyProposal.user_id == current_user.id)
    if account_id:
        statement = statement.where(OperationStrategyProposal.platform_account_id == account_id)
    if status:
        statement = statement.where(OperationStrategyProposal.status == status)
    rows = db.scalars(statement.order_by(OperationStrategyProposal.created_at.desc())).all()
    return {"items": [_proposal_item(row) for row in rows]}


@router.post("/accounts/{account_id}/analyze-strategy")
def analyze_strategy(account_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account = _account(db, current_user.id, account_id)
    return _proposal_item(_create_strategy_proposal(db, current_user, account))


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
            evidence_json=json.dumps({"proposal_id": proposal.id, "source_note_count": proposal.source_note_count}, ensure_ascii=False),
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

    # 一个账号一套自动运营策略；每个发布时间对应一个独立任务，便于单独暂停和追踪。
    from backend.app.api.auto_tasks import _calculate_next_run_at
    task_prefix = f"策略自动运营｜{account.id}｜"
    existing_tasks = db.scalars(select(AutoTask).where(
        AutoTask.user_id == current_user.id,
        AutoTask.creator_account_id == account.id,
        AutoTask.name.like(f"{task_prefix}%"),
    ).order_by(AutoTask.id)).all()
    directions = [str(item) for item in strategy.get("content_directions", []) if str(item).strip()] or ["日常内容"]
    auto_tasks_created = 0
    auto_tasks_updated = 0
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
        task.status = "active"
        _calculate_next_run_at(task)
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
def list_plans(plan_date: date | None = None, account_id: int | None = None, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    statement = select(OperationPlan).where(OperationPlan.user_id == current_user.id)
    if plan_date:
        statement = statement.where(OperationPlan.plan_date == plan_date)
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
    created = []
    for profile in profiles:
        existing = db.scalars(select(OperationPlan).where(
            OperationPlan.user_id == current_user.id,
            OperationPlan.platform_account_id == profile.platform_account_id,
            OperationPlan.plan_date == payload.plan_date,
        )).all()
        directions = _loads(profile.content_directions_json, []) or ["日常内容"]
        preferred_times = _loads(profile.preferred_times_json, []) or ["10:00", "18:00", "20:30"]
        for index in range(len(existing), profile.daily_generate_count):
            time_text = preferred_times[index % len(preferred_times)]
            try:
                hour, minute = [int(part) for part in time_text.split(":", 1)]
                planned_at = datetime.combine(payload.plan_date, time(hour, minute))
            except (ValueError, TypeError):
                planned_at = datetime.combine(payload.plan_date, time(10, 0))
            direction = directions[index % len(directions)]
            row = OperationPlan(
                user_id=current_user.id, platform_account_id=profile.platform_account_id,
                plan_date=payload.plan_date, content_direction=direction,
                topic=f"{direction}｜第{index + 1}篇", planned_at=planned_at,
                rationale="依据账号运营档案中的内容方向、频次和发布时间自动生成",
            )
            db.add(row)
            created.append(row)
    db.commit()
    for row in created:
        db.refresh(row)
    return {"created": len(created), "items": [_plan_item(row) for row in created]}


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
