from __future__ import annotations

import random
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.ai import _recorded_text_task, _text_model_context, get_text_ai_client
from backend.app.api.platforms.xhs.crawl import _data_items, _normalize_search_item
from backend.app.api.platforms.xhs.pc import (
    _get_owned_pc_account_cookies,
    get_xhs_pc_api_adapter_factory,
)
from backend.app.core.database import get_db
from backend.app.core.deps import get_current_user
from backend.app.core.security import decrypt_text
from backend.app.core.time import shanghai_now
from backend.app.models import (
    AccountCookieVersion,
    AiDraft,
    AutoTask,
    ModelConfig,
    PlatformAccount,
    PromptTemplate,
    PublishAsset,
    PublishJob,
    Task,
    User,
)
from backend.app.schemas.common import paginated
from backend.app.services.ai_service import (
    OpenAICompatibleTextClient,
    TextAiClient,
    normalize_xhs_title,
)

router = APIRouter(prefix="/auto-tasks", tags=["auto-tasks"])


class AutoTaskCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    keywords: list[str] = Field(min_length=1)
    pc_account_id: int
    creator_account_id: int
    ai_instruction: str = Field(default="", max_length=2000)
    schedule_type: str = Field(default="manual", pattern="^(manual|daily|weekly|interval)$")
    schedule_time: str = Field(default="09:00", max_length=5)
    schedule_days: str = Field(default="", max_length=64)
    schedule_interval_hours: float = Field(default=24, ge=0.5, le=168)


class AutoTaskParseDescriptionRequest(BaseModel):
    description: str = Field(min_length=1, max_length=1000)
    account_id: Optional[int] = None


# 与 AutoTaskCreateRequest.schedule_type 的正则保持同一份真相。改这里必须同时改那边。
SCHEDULE_TYPES = ("manual", "daily", "weekly", "interval")
# AI 容易一次吐几十个关键词。上限在这里卡住，超出部分直接丢并告警。
MAX_PARSED_KEYWORDS = 10
SOURCE_SUB_TYPES = ("pc", "android")
PUBLISH_SUB_TYPES = ("creator", "android")


class AutoTaskUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    keywords: Optional[list[str]] = None
    ai_instruction: Optional[str] = Field(default=None, max_length=2000)
    status: Optional[str] = Field(default=None, pattern="^(active|paused|completed)$")
    schedule_type: Optional[str] = Field(default=None, pattern="^(manual|daily|weekly|interval)$")
    schedule_time: Optional[str] = Field(default=None, max_length=5)
    schedule_days: Optional[str] = Field(default=None, max_length=64)
    schedule_interval_hours: Optional[float] = Field(default=None, ge=0.5, le=168)


def _serialize_auto_task(task: AutoTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "user_id": task.user_id,
        "name": task.name,
        "keywords": task.keywords or [],
        "pc_account_id": task.pc_account_id,
        "creator_account_id": task.creator_account_id,
        "ai_instruction": task.ai_instruction,
        "status": task.status,
        "schedule_type": task.schedule_type,
        "schedule_time": task.schedule_time,
        "schedule_days": task.schedule_days,
        "schedule_interval_hours": task.schedule_interval_hours,
        "last_run_at": task.last_run_at.isoformat() if task.last_run_at else None,
        "next_run_at": task.next_run_at.isoformat() if task.next_run_at else None,
        "total_published": task.total_published,
        "created_at": task.created_at.isoformat(),
    }


_HHMM_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _resolve_proposed_account(
    db: Session,
    current_user: User,
    raw_value: Any,
    *,
    allowed_sub_types: tuple[str, ...],
    role: str,
    role_label: str,
    warnings: list[str],
) -> tuple[Optional[int], Optional[dict[str, Any]]]:
    """把 AI 给的账号 id 复核一遍归属，通不过就留空 + 告警。

    发错账号是不可逆后果（笔记直接进别人主页），所以这里绝不放行「看起来像 id 的东西」：
    非整数、不存在、不属于当前用户、平台或子类型不对，全部当作未匹配处理。
    """
    if raw_value is None or isinstance(raw_value, bool):
        warnings.append(f"AI 未能确定{role_label}，请手动选择。")
        return None, None
    try:
        account_id = int(raw_value)
    except (TypeError, ValueError):
        warnings.append(f"AI 返回的{role_label} id 不是有效数字，已留空，请手动选择。")
        return None, None

    account = db.get(PlatformAccount, account_id)
    if (
        account is None
        or account.user_id != current_user.id
        or account.platform != "xhs"
        or account.sub_type not in allowed_sub_types
    ):
        # 跨用户 id 与不存在的 id 走同一条路径，且提示里不回显该 id 归属谁。
        warnings.append(f"AI 匹配的{role_label}（id={account_id}）不属于你或类型不符，已留空，请手动选择。")
        return None, None

    return account.id, {
        "id": account.id,
        "nickname": account.nickname or "",
        "sub_type": account.sub_type,
        "role": role,
        "role_label": role_label,
    }


def _build_auto_task_proposal(
    db: Session,
    current_user: User,
    parsed: dict[str, Any],
) -> dict[str, Any]:
    """把 AI 的原始输出收敛成一份可信提案。

    原则：每个字段要么通过校验，要么留空并进 `warnings`。绝不填猜测值——用户看到空字段会去填，
    看不到的错值会被直接提交。
    """
    warnings: list[str] = []

    raw_name = parsed.get("name")
    name = str(raw_name).strip()[:128] if isinstance(raw_name, (str, int, float)) and not isinstance(raw_name, bool) else ""
    if not name:
        warnings.append("AI 未能确定任务名称，请手动填写。")

    keywords: list[str] = []
    raw_keywords = parsed.get("keywords")
    if isinstance(raw_keywords, str):
        raw_keywords = [part for part in re.split(r"[,，\n]", raw_keywords)]
    if isinstance(raw_keywords, list):
        for item in raw_keywords:
            if not isinstance(item, str):
                continue
            keyword = item.strip().lstrip("#")
            if keyword and keyword not in keywords:
                keywords.append(keyword)
        if len(keywords) > MAX_PARSED_KEYWORDS:
            warnings.append(
                f"AI 返回了 {len(keywords)} 个关键词，已截取前 {MAX_PARSED_KEYWORDS} 个。"
            )
            keywords = keywords[:MAX_PARSED_KEYWORDS]
    if not keywords:
        warnings.append("AI 未能确定搜索关键词，请手动填写至少一个。")

    raw_instruction = parsed.get("ai_instruction")
    ai_instruction = str(raw_instruction).strip()[:2000] if isinstance(raw_instruction, str) else ""

    raw_schedule_type = parsed.get("schedule_type")
    schedule_type = ""
    if isinstance(raw_schedule_type, str) and raw_schedule_type.strip() in SCHEDULE_TYPES:
        schedule_type = raw_schedule_type.strip()
    elif raw_schedule_type not in (None, ""):
        warnings.append(
            f"AI 返回的调度方式「{raw_schedule_type}」不是合法值（{'/'.join(SCHEDULE_TYPES)}），已留空，请手动选择。"
        )
    else:
        warnings.append("AI 未能确定调度方式，请手动选择。")

    raw_schedule_time = parsed.get("schedule_time")
    schedule_time = ""
    if isinstance(raw_schedule_time, str) and _HHMM_PATTERN.match(raw_schedule_time.strip()):
        schedule_time = raw_schedule_time.strip()
    elif raw_schedule_time not in (None, ""):
        warnings.append(
            f"AI 返回的执行时间「{raw_schedule_time}」不是 HH:MM 格式，已留空，请手动填写。"
        )

    raw_schedule_days = parsed.get("schedule_days")
    schedule_days = ""
    if isinstance(raw_schedule_days, list):
        raw_schedule_days = ",".join(str(item) for item in raw_schedule_days)
    if isinstance(raw_schedule_days, str) and raw_schedule_days.strip():
        days = [part.strip() for part in raw_schedule_days.split(",") if part.strip()]
        valid_days = [day for day in days if day.isdigit() and 1 <= int(day) <= 7]
        if len(valid_days) != len(days):
            warnings.append("AI 返回的执行日期含非法值（应为 1-7），已留空，请手动选择。")
        else:
            schedule_days = ",".join(dict.fromkeys(valid_days))
    if schedule_type == "weekly" and not schedule_days:
        warnings.append("每周调度需要指定执行日期，请手动勾选。")

    raw_interval = parsed.get("schedule_interval_hours")
    schedule_interval_hours: Optional[float] = None
    if isinstance(raw_interval, (int, float)) and not isinstance(raw_interval, bool):
        if 0.5 <= float(raw_interval) <= 168:
            schedule_interval_hours = float(raw_interval)
        else:
            warnings.append(f"AI 返回的间隔小时数 {raw_interval} 超出 0.5-168 范围，已留空。")
    elif raw_interval not in (None, ""):
        warnings.append("AI 返回的间隔小时数不是数字，已留空。")
    if schedule_type == "interval" and schedule_interval_hours is None:
        warnings.append("自定义间隔调度需要指定间隔小时数，请手动填写。")

    matched_accounts: list[dict[str, Any]] = []
    pc_account_id, pc_match = _resolve_proposed_account(
        db,
        current_user,
        parsed.get("pc_account_id"),
        allowed_sub_types=SOURCE_SUB_TYPES,
        role="pc_account_id",
        role_label="采集账号",
        warnings=warnings,
    )
    if pc_match:
        matched_accounts.append(pc_match)
    creator_account_id, creator_match = _resolve_proposed_account(
        db,
        current_user,
        parsed.get("creator_account_id"),
        allowed_sub_types=PUBLISH_SUB_TYPES,
        role="creator_account_id",
        role_label="发布账号",
        warnings=warnings,
    )
    if creator_match:
        matched_accounts.append(creator_match)

    if creator_account_id is not None:
        # 匹配上了也要提醒——这是发布去向，总纲第十二节要求人工复核。
        label = creator_match["nickname"] if creator_match else str(creator_account_id)
        warnings.append(f"发布账号将使用「{label}」，发布不可撤回，请确认无误。")

    raw_notes = parsed.get("notes")
    if isinstance(raw_notes, str) and raw_notes.strip():
        warnings.append(f"AI 备注：{raw_notes.strip()[:300]}")

    return {
        "name": name,
        "keywords": keywords,
        "pc_account_id": pc_account_id,
        "creator_account_id": creator_account_id,
        "ai_instruction": ai_instruction,
        "schedule_type": schedule_type,
        "schedule_time": schedule_time,
        "schedule_days": schedule_days,
        "schedule_interval_hours": schedule_interval_hours,
        "matched_accounts": matched_accounts,
        "warnings": warnings,
    }


def _get_owned_auto_task(db: Session, current_user: User, task_id: int) -> AutoTask:
    auto_task = db.scalars(
        select(AutoTask).where(AutoTask.id == task_id, AutoTask.user_id == current_user.id)
    ).first()
    if auto_task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Auto task not found")
    return auto_task


def _verify_account_ownership(db: Session, current_user: User, account_id: int, expected_sub_type: str) -> PlatformAccount:
    account = db.get(PlatformAccount, account_id)
    if (
        account is None
        or account.user_id != current_user.id
        or account.platform != "xhs"
        or account.sub_type != expected_sub_type
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"XHS {expected_sub_type} account not found",
        )
    return account


def _get_account_cookies(db: Session, account_id: int) -> str:
    from backend.app.api.publish import _cookies_to_string

    cookie_version = db.scalars(
        select(AccountCookieVersion)
        .where(AccountCookieVersion.platform_account_id == account_id)
        .order_by(AccountCookieVersion.created_at.desc(), AccountCookieVersion.id.desc())
    ).first()
    if cookie_version is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account has no cookies")
    return _cookies_to_string(decrypt_text(cookie_version.encrypted_cookies))


def _calculate_next_run_at(task: AutoTask) -> None:
    from datetime import timedelta
    now = shanghai_now()
    if task.schedule_type == "manual":
        task.next_run_at = None
    elif task.schedule_type == "daily":
        h, m = (task.schedule_time or "09:00").split(":")
        next_time = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
        if next_time <= now:
            next_time += timedelta(days=1)
        task.next_run_at = next_time
    elif task.schedule_type == "weekly":
        h, m = (task.schedule_time or "09:00").split(":")
        days = [int(d) for d in (task.schedule_days or "").split(",") if d.strip().isdigit()]
        if not days:
            task.next_run_at = None
            return
        for offset in range(1, 8):
            candidate = now + timedelta(days=offset)
            if candidate.isoweekday() in days:
                task.next_run_at = candidate.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
                return
    elif task.schedule_type == "interval":
        task.next_run_at = now + timedelta(hours=task.schedule_interval_hours)


@router.get("")
def list_auto_tasks(
    account_id: Optional[int] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    statement = select(AutoTask).where(AutoTask.user_id == current_user.id)
    if account_id:
        statement = statement.where(AutoTask.creator_account_id == account_id)
    tasks = db.scalars(
        statement
        .order_by(AutoTask.created_at.desc(), AutoTask.id.desc())
    ).all()
    return paginated([_serialize_auto_task(t) for t in tasks], page, page_size)


@router.post("/parse-description")
def parse_auto_task_description(
    payload: AutoTaskParseDescriptionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    text_ai_client: TextAiClient = Depends(get_text_ai_client),
):
    """把一句话需求解析成任务配置**提案**。

    这个端点只读不写：不创建 AutoTask，不排任何调度，不触发发布。落库由用户在前端复核后
    走 `POST /auto-tasks` 完成。自动运营任务会自动发布到真实账号且不可撤回，因此人工确认门
    必须留在这里（总纲第十二节）。

    唯一的写入是 `_recorded_text_task` 记的那条 AI 任务流水，与任务配置无关。
    """
    model_config, api_key = _text_model_context(db, current_user)

    accounts = db.scalars(
        select(PlatformAccount).where(
            PlatformAccount.user_id == current_user.id,
            PlatformAccount.platform == "xhs",
        )
    ).all()
    account_payload = [
        {"id": account.id, "nickname": account.nickname or "", "sub_type": account.sub_type}
        for account in accounts
        if account.sub_type in set(SOURCE_SUB_TYPES) | set(PUBLISH_SUB_TYPES)
    ]

    # 指令中心里的 auto_ops 指令作为 ai_instruction 的候选来源，一并喂给模型并回传给前端。
    instruction_templates = db.scalars(
        select(PromptTemplate)
        .where(
            PromptTemplate.user_id == current_user.id,
            PromptTemplate.prompt_type == "auto_ops",
            PromptTemplate.status == "active",
        )
        .order_by(PromptTemplate.priority.desc(), PromptTemplate.id.desc())
    ).all()
    instruction_candidates = [
        {"id": template.id, "name": template.name, "content": template.content}
        for template in instruction_templates[:10]
    ]

    task, parsed = _recorded_text_task(
        db=db,
        current_user=current_user,
        platform="xhs",
        task_type="ai_parse_auto_task",
        payload={
            "model_config_id": model_config.id,
            "description": payload.description,
            "account_id": payload.account_id,
        },
        action=lambda: text_ai_client.parse_auto_task_description(
            model_config=model_config,
            api_key=api_key,
            description=payload.description,
            accounts=account_payload,
            instruction_candidates=instruction_candidates,
        ),
    )

    if not isinstance(parsed, dict):
        task.status = "failed"
        task.payload = {**(task.payload or {}), "error": "AI 返回的配置不是对象"}
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI 返回的配置不是对象",
        )

    proposal = _build_auto_task_proposal(db, current_user, parsed)

    # 调用方显式指定了账号时以它为准——用户的选择优先于 AI 的猜测，但仍要过归属校验。
    if payload.account_id is not None:
        override_warnings: list[str] = []
        override_id, override_match = _resolve_proposed_account(
            db,
            current_user,
            payload.account_id,
            allowed_sub_types=PUBLISH_SUB_TYPES,
            role="creator_account_id",
            role_label="发布账号",
            warnings=override_warnings,
        )
        if override_id is not None and override_match is not None:
            proposal["creator_account_id"] = override_id
            proposal["matched_accounts"] = [
                item for item in proposal["matched_accounts"]
                if item["role"] != "creator_account_id"
            ] + [override_match]
        else:
            proposal["warnings"] = proposal["warnings"] + override_warnings

    task.payload = {
        **(task.payload or {}),
        "warning_count": len(proposal["warnings"]),
        "keyword_count": len(proposal["keywords"]),
    }
    db.commit()

    return {
        "description": payload.description,
        "proposal": {key: value for key, value in proposal.items() if key not in {"matched_accounts", "warnings"}},
        "matched_accounts": proposal["matched_accounts"],
        "warnings": proposal["warnings"],
        "instruction_candidates": instruction_candidates,
        # 提案永远不落库，前端必须走创建接口。这个标记让前端无从误判。
        "requires_confirmation": True,
    }


@router.post("")
def create_auto_task(
    payload: AutoTaskCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source_account = db.get(PlatformAccount, payload.pc_account_id)
    if source_account is None or source_account.user_id != current_user.id or source_account.platform != "xhs" or source_account.sub_type not in {"pc", "android"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="XHS source account not found")
    publish_account = db.get(PlatformAccount, payload.creator_account_id)
    if publish_account is None or publish_account.user_id != current_user.id or publish_account.platform != "xhs" or publish_account.sub_type not in {"creator", "android"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="XHS publish account not found")

    auto_task = AutoTask(
        user_id=current_user.id,
        name=payload.name,
        keywords=payload.keywords,
        pc_account_id=payload.pc_account_id,
        creator_account_id=payload.creator_account_id,
        ai_instruction=payload.ai_instruction,
        schedule_type=payload.schedule_type,
        schedule_time=payload.schedule_time,
        schedule_days=payload.schedule_days,
        schedule_interval_hours=payload.schedule_interval_hours,
        status="active",
    )
    _calculate_next_run_at(auto_task)
    db.add(auto_task)
    db.commit()
    db.refresh(auto_task)
    return _serialize_auto_task(auto_task)


@router.patch("/{task_id}")
def update_auto_task(
    task_id: int,
    payload: AutoTaskUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    auto_task = _get_owned_auto_task(db, current_user, task_id)

    if payload.name is not None:
        auto_task.name = payload.name
    if payload.keywords is not None:
        auto_task.keywords = payload.keywords
    if payload.ai_instruction is not None:
        auto_task.ai_instruction = payload.ai_instruction
    if payload.status is not None:
        auto_task.status = payload.status

    schedule_changed = False
    if payload.schedule_type is not None:
        auto_task.schedule_type = payload.schedule_type
        schedule_changed = True
    if payload.schedule_time is not None:
        auto_task.schedule_time = payload.schedule_time
        schedule_changed = True
    if payload.schedule_days is not None:
        auto_task.schedule_days = payload.schedule_days
        schedule_changed = True
    if payload.schedule_interval_hours is not None:
        auto_task.schedule_interval_hours = payload.schedule_interval_hours
        schedule_changed = True
    if schedule_changed:
        _calculate_next_run_at(auto_task)

    db.commit()
    db.refresh(auto_task)
    return _serialize_auto_task(auto_task)


@router.delete("/{task_id}")
def delete_auto_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    auto_task = _get_owned_auto_task(db, current_user, task_id)
    db.delete(auto_task)
    db.commit()
    return {"id": task_id, "status": "deleted"}


@router.post("/{task_id}/run")
def run_auto_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    adapter_factory=Depends(get_xhs_pc_api_adapter_factory),
):
    auto_task = _get_owned_auto_task(db, current_user, task_id)

    source_account = db.get(PlatformAccount, auto_task.pc_account_id)
    publish_account = db.get(PlatformAccount, auto_task.creator_account_id)
    if publish_account is None or publish_account.user_id != current_user.id or publish_account.platform != "xhs" or publish_account.sub_type not in {"creator", "android"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="XHS publish account not found")

    # Pure-AI Android tasks do not need a PC account or XHS search cookies. They
    # generate an original illustrated draft and leave a pending mobile publish
    # job for review.
    if (
        source_account is not None
        and source_account.user_id == current_user.id
        and source_account.platform == "xhs"
        and source_account.sub_type == "android"
    ):
        from backend.app.services.scheduler_service import _execute_auto_task_background

        try:
            _execute_auto_task_background(db, auto_task)
            _calculate_next_run_at(auto_task)
            db.commit()
        except Exception as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"AI draft generation failed: {exc}",
            ) from exc

        draft = db.scalars(
            select(AiDraft)
            .where(AiDraft.user_id == current_user.id, AiDraft.platform == "xhs")
            .order_by(AiDraft.id.desc())
        ).first()
        if draft is None:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="AI draft generation did not create a draft",
            )
        publish_job = db.scalars(
            select(PublishJob)
            .where(
                PublishJob.user_id == current_user.id,
                PublishJob.source_draft_id == draft.id,
            )
            .order_by(PublishJob.id.desc())
        ).first()
        if publish_job is None:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="AI draft generation did not create a publish job",
            )
        db.refresh(auto_task)
        return {
            "auto_task": _serialize_auto_task(auto_task),
            "keyword": (auto_task.keywords or ["护腰怎么选"])[0],
            "source_note": {
                "note_id": "",
                "title": "AI 原创护腰选题",
                "likes": 0,
                "collects": 0,
                "comments": 0,
            },
            "draft": {
                "id": draft.id,
                "title": draft.title,
                "body": draft.body,
                "created_at": draft.created_at.isoformat(),
            },
            "publish_job": {
                "id": publish_job.id,
                "status": publish_job.status,
                "platform_account_id": publish_job.platform_account_id,
            },
        }

    # Research-based tasks still require a PC account and cookies.
    _verify_account_ownership(db, current_user, auto_task.pc_account_id, "pc")

    # Create a tracking task
    tracking_task = Task(
        user_id=current_user.id,
        platform="xhs",
        task_type="auto_ops_run",
        status="running",
        progress=10,
        payload={"auto_task_id": auto_task.id, "auto_task_name": auto_task.name},
    )
    db.add(tracking_task)
    db.flush()

    # 1. Pick a random keyword
    keywords = auto_task.keywords or []
    if not keywords:
        tracking_task.status = "failed"
        tracking_task.progress = 100
        tracking_task.payload = {**(tracking_task.payload or {}), "error": "No keywords configured"}
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No keywords configured")

    keyword = random.choice(keywords)
    tracking_task.payload = {**(tracking_task.payload or {}), "keyword": keyword}
    db.flush()

    # 2. Search notes using PC adapter
    pc_cookies = _get_owned_pc_account_cookies(db, current_user, auto_task.pc_account_id)
    adapter = adapter_factory(pc_cookies)
    success, message, raw_payload = adapter.search_note(keyword, page=1)
    if not success:
        tracking_task.status = "failed"
        tracking_task.progress = 100
        tracking_task.payload = {**(tracking_task.payload or {}), "error": message or "Search failed"}
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=message or "XHS search failed")

    tracking_task.progress = 30
    db.flush()

    # 3. Normalize and pick top-engagement note
    items = _data_items(raw_payload)
    normalized_items = [_normalize_search_item(item) for item in items]
    if not normalized_items:
        tracking_task.status = "failed"
        tracking_task.progress = 100
        tracking_task.payload = {**(tracking_task.payload or {}), "error": "No notes found for keyword"}
        db.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No notes found for keyword")

    # Limit to crawl_count and pick best by engagement
    candidates = normalized_items[:10]
    best_note = max(
        candidates,
        key=lambda n: (n.get("likes", 0) + n.get("collects", 0) + n.get("comments", 0) + n.get("shares", 0)),
    )

    tracking_task.progress = 50
    tracking_task.payload = {
        **(tracking_task.payload or {}),
        "source_note_id": best_note.get("note_id"),
        "source_title": best_note.get("title"),
        "candidates_count": len(candidates),
    }
    db.flush()

    # 4. Create an AiDraft from the best note
    draft = AiDraft(
        user_id=current_user.id,
        platform_account_id=auto_task.creator_account_id,
        platform="xhs",
        title=normalize_xhs_title(str(best_note.get("title") or "")),
        body=str(best_note.get("content") or ""),
    )
    db.add(draft)
    db.flush()

    tracking_task.progress = 60
    tracking_task.payload = {**(tracking_task.payload or {}), "draft_id": draft.id}
    db.flush()

    # 5. AI rewrite using the task's instruction
    model_config = db.scalars(
        select(ModelConfig).where(
            ModelConfig.user_id == current_user.id,
            ModelConfig.model_type == "text",
            ModelConfig.is_default.is_(True),
        )
    ).first()
    if model_config is None:
        tracking_task.status = "failed"
        tracking_task.progress = 100
        tracking_task.payload = {**(tracking_task.payload or {}), "error": "Default text model not configured"}
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Default text model is not configured")

    api_key = decrypt_text(model_config.encrypted_api_key) if model_config.encrypted_api_key else ""
    text_client = OpenAICompatibleTextClient()

    try:
        rewritten_body = text_client.rewrite_note(
            model_config=model_config,
            api_key=api_key,
            title=draft.title,
            body=draft.body,
            instruction=auto_task.ai_instruction or "改写为原创小红书笔记，保持核心信息，提升表达和语感",
        )
        draft.body = rewritten_body
    except Exception as exc:
        tracking_task.status = "failed"
        tracking_task.progress = 100
        tracking_task.payload = {**(tracking_task.payload or {}), "error": f"AI rewrite failed: {exc}"}
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"AI rewrite failed: {exc}") from exc

    # 5b. Title rewrite (non-fatal)
    try:
        rewritten_title = text_client._complete(
            model_config=model_config,
            api_key=api_key,
            system_prompt="你是小红书标题创作专家。",
            user_prompt=f"为以下小红书笔记改写一个吸引人的标题（15字以内）：\n\n原标题：{draft.title}\n\n正文：{draft.body[:200]}",
            temperature=0.8,
        )
        draft.title = normalize_xhs_title(rewritten_title, draft.title)
    except Exception:
        pass  # title rewrite failure is not fatal

    tracking_task.progress = 80
    db.flush()

    # 6. Create a PublishJob with the Creator account
    publish_job = PublishJob(
        user_id=current_user.id,
        platform_account_id=auto_task.creator_account_id,
        source_draft_id=draft.id,
        platform="xhs",
        title=draft.title,
        body=draft.body,
        publish_mode="immediate",
        status="pending",
    )
    db.add(publish_job)
    db.flush()

    # 6b. Copy image assets from source note
    image_urls = best_note.get("image_urls", [])
    if isinstance(image_urls, list):
        for url in image_urls[:9]:  # max 9 images
            if isinstance(url, str) and url:
                db.add(PublishAsset(
                    publish_job_id=publish_job.id,
                    asset_type="image",
                    file_path=url,
                    upload_status="pending",
                ))

    # 7. Update auto task counters
    auto_task.total_published = (auto_task.total_published or 0) + 1
    auto_task.last_run_at = shanghai_now()
    _calculate_next_run_at(auto_task)

    tracking_task.status = "completed"
    tracking_task.progress = 100
    tracking_task.payload = {
        **(tracking_task.payload or {}),
        "publish_job_id": publish_job.id,
        "rewritten_length": len(rewritten_body),
    }

    db.commit()
    db.refresh(auto_task)
    db.refresh(draft)
    db.refresh(publish_job)

    return {
        "auto_task": _serialize_auto_task(auto_task),
        "keyword": keyword,
        "source_note": {
            "note_id": best_note.get("note_id"),
            "title": best_note.get("title"),
            "likes": best_note.get("likes", 0),
            "collects": best_note.get("collects", 0),
            "comments": best_note.get("comments", 0),
        },
        "draft": {
            "id": draft.id,
            "title": draft.title,
            "body": draft.body,
            "created_at": draft.created_at.isoformat(),
        },
        "publish_job": {
            "id": publish_job.id,
            "status": publish_job.status,
            "platform_account_id": publish_job.platform_account_id,
        },
    }
