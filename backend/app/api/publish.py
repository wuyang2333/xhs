from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.adapters.xhs.creator_api_adapter import XhsCreatorApiAdapter
from backend.app.core.database import get_db
from backend.app.core.deps import get_current_user
from backend.app.core.security import decrypt_text
from backend.app.core.time import shanghai_now
from backend.app.models import AccountCookieVersion, PlatformAccount, PublishAsset, PublishJob, Task, User
from backend.app.schemas.common import paginated
from backend.app.services.android_xhs_service import AndroidXhsService

router = APIRouter(prefix="/publish", tags=["publish"])


class PublishJobUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=256)
    body: Optional[str] = None
    platform_account_id: Optional[int] = None
    publish_mode: Optional[str] = Field(default=None, pattern="^(immediate|scheduled)$")
    scheduled_at: Optional[datetime] = None
    topics: Optional[list[str]] = None
    location: Optional[str] = None
    privacy_type: Optional[int] = Field(default=None, ge=0, le=1)
    is_private: Optional[bool] = None
    product_keyword: Optional[str] = Field(default=None, max_length=200)


class PublishAssetCreateRequest(BaseModel):
    asset_type: str = Field(pattern="^(image|video)$")
    file_path: str = Field(min_length=1)


def get_android_xhs_service() -> AndroidXhsService:
    return AndroidXhsService()


def get_creator_publish_adapter_factory():
    return XhsCreatorApiAdapter


def _clean_topics(topics: Optional[list[str]]) -> list[str]:
    if topics is None:
        return []
    return [topic.strip() for topic in topics if topic and topic.strip()]


def _load_publish_options(job: PublishJob) -> dict[str, Any]:
    try:
        options = json.loads(job.publish_options or "{}")
    except json.JSONDecodeError:
        return {}
    return options if isinstance(options, dict) else {}


def _store_publish_options(job: PublishJob, options: dict[str, Any]) -> None:
    job.publish_options = json.dumps(options, ensure_ascii=False, separators=(",", ":"))


def _update_publish_options(job: PublishJob, payload: PublishJobUpdateRequest) -> None:
    fields = payload.model_fields_set
    if not {"topics", "location", "privacy_type", "is_private", "product_keyword"} & fields:
        return

    options = _load_publish_options(job)
    if "topics" in fields:
        topics = _clean_topics(payload.topics)
        if topics:
            options["topics"] = topics
        else:
            options.pop("topics", None)
    if "location" in fields:
        if payload.location and payload.location.strip():
            options["location"] = payload.location.strip()
        else:
            options.pop("location", None)
    if "is_private" in fields:
        if payload.is_private is None:
            options.pop("is_private", None)
            options.pop("privacy_type", None)
        else:
            options["is_private"] = payload.is_private
            options["privacy_type"] = 1 if payload.is_private else 0
    elif "privacy_type" in fields:
        if payload.privacy_type is None:
            options.pop("privacy_type", None)
            options.pop("is_private", None)
        else:
            options["privacy_type"] = payload.privacy_type
            options["is_private"] = payload.privacy_type == 1
    if "product_keyword" in fields:
        if payload.product_keyword and payload.product_keyword.strip():
            options["product_keyword"] = payload.product_keyword.strip()
        else:
            options.pop("product_keyword", None)
    _store_publish_options(job, options)


def _apply_publish_options(note_info: dict[str, Any], options: dict[str, Any]) -> None:
    topics = _clean_topics(options.get("topics") if isinstance(options.get("topics"), list) else None)
    if not topics:
        draft_tags = options.get("draft_tags")
        if isinstance(draft_tags, list):
            topics = [str(t.get("name", "")) for t in draft_tags if isinstance(t, dict) and t.get("name")]
    if topics:
        note_info["topics"] = topics
    location = options.get("location")
    if isinstance(location, str) and location.strip():
        note_info["location"] = location.strip()
    privacy_type = options.get("privacy_type")
    if privacy_type in (0, 1):
        note_info["type"] = privacy_type


def serialize_publish_job(job: PublishJob) -> dict:
    return {
        "id": job.id,
        "platform_account_id": job.platform_account_id,
        "source_draft_id": job.source_draft_id,
        "platform": job.platform,
        "title": job.title,
        "body": job.body,
        "publish_mode": job.publish_mode,
        "publish_options": _load_publish_options(job),
        "status": job.status,
        "scheduled_at": job.scheduled_at.isoformat() if job.scheduled_at else None,
        "external_note_id": job.external_note_id,
        "publish_error": job.publish_error,
        "published_at": job.published_at.isoformat() if job.published_at else None,
        "created_at": job.created_at.isoformat(),
    }


def serialize_publish_asset(asset: PublishAsset) -> dict:
    try:
        creator_upload_info = json.loads(asset.creator_upload_info or "{}")
    except json.JSONDecodeError:
        creator_upload_info = {}
    return {
        "id": asset.id,
        "publish_job_id": asset.publish_job_id,
        "asset_type": asset.asset_type,
        "file_path": asset.file_path,
        "sort_order": asset.sort_order,
        "source_draft_asset_id": asset.source_draft_asset_id,
        "upload_status": asset.upload_status,
        "creator_media_id": asset.creator_media_id,
        "upload_error": asset.upload_error,
        "creator_upload_info": creator_upload_info,
    }


def _cookies_to_string(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return stripped
    if stripped.startswith("{"):
        cookies = json.loads(stripped)
        return "; ".join(f"{key}={cookie_value}" for key, cookie_value in cookies.items())
    return stripped


def _extract_creator_media_id(payload: dict[str, Any]) -> str:
    for key in ("creator_media_id", "fileIds", "file_id", "media_id", "video_id"):
        value = payload.get(key)
        if value:
            return str(value)
    return ""


def _extract_external_note_id(payload: dict[str, Any]) -> str:
    for key in ("note_id", "noteId", "id"):
        value = payload.get(key)
        if value:
            return str(value)
    data = payload.get("data")
    if isinstance(data, dict):
        return _extract_external_note_id(data)
    return ""


def _scheduled_post_time(job: PublishJob) -> Optional[int]:
    if job.publish_mode != "scheduled":
        return None
    if job.scheduled_at is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Scheduled publish time is required")
    if job.scheduled_at <= shanghai_now():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Scheduled publish time must be in the future")
    return int(job.scheduled_at.timestamp() * 1000)


def _asset_creator_upload_info(asset: PublishAsset) -> dict[str, Any]:
    try:
        upload_info = json.loads(asset.creator_upload_info or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded asset metadata is invalid") from exc
    if not upload_info.get("fileIds"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded asset is missing Creator upload info")
    return upload_info


def _get_owned_publish_job(db: Session, current_user: User, job_id: int) -> PublishJob:
    job = db.scalars(
        select(PublishJob)
        .where(PublishJob.id == job_id, PublishJob.user_id == current_user.id)
    ).first()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Publish job not found")
    return job


def _get_owned_publish_asset(db: Session, current_user: User, asset_id: int) -> PublishAsset:
    asset = db.scalars(
        select(PublishAsset)
        .join(PublishJob, PublishAsset.publish_job_id == PublishJob.id)
        .where(
            PublishAsset.id == asset_id,
            PublishJob.user_id == current_user.id,
        )
    ).first()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Publish asset not found")
    return asset


def _get_owned_android_account(db: Session, current_user: User, job: PublishJob) -> PlatformAccount:
    if not job.platform_account_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请先选择安卓手机账号")
    account = db.get(PlatformAccount, job.platform_account_id)
    if account is None or account.user_id != current_user.id or account.platform != "xhs" or account.sub_type != "android":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请选择已绑定的安卓手机账号")
    if account.status not in {"active", "healthy"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="安卓手机未就绪，请先在账号矩阵进行健康检查")
    return account


def _job_assets(db: Session, job: PublishJob) -> list[PublishAsset]:
    assets = db.scalars(
        select(PublishAsset).where(PublishAsset.publish_job_id == job.id).order_by(PublishAsset.sort_order.asc(), PublishAsset.id.asc())
    ).all()
    if not assets:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="至少需要一张图片")
    return assets


def _append_android_tags(job: PublishJob) -> str:
    options = _load_publish_options(job)
    topics = _clean_topics(options.get("topics") if isinstance(options.get("topics"), list) else None)
    if not topics:
        draft_tags = options.get("draft_tags")
        if isinstance(draft_tags, list):
            topics = [str(item.get("name", "")).strip() for item in draft_tags if isinstance(item, dict)]
    tags = " ".join(f"#{tag.lstrip('#')}" for tag in topics if tag)
    return f"{job.body.rstrip()}\n\n{tags}".strip() if tags else job.body


def _get_latest_account_cookies(db: Session, account_id: int) -> str:
    cookie_version = db.scalars(
        select(AccountCookieVersion)
        .where(AccountCookieVersion.platform_account_id == account_id)
        .order_by(AccountCookieVersion.created_at.desc(), AccountCookieVersion.id.desc())
    ).first()
    if cookie_version is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account has no cookies")
    return _cookies_to_string(decrypt_text(cookie_version.encrypted_cookies))


def _record_publish_control_task(db: Session, current_user: User, job: PublishJob, task_type: str) -> None:
    task = Task(
        user_id=current_user.id,
        platform=job.platform,
        task_type=task_type,
        status="pending",
        progress=0,
        payload={
            "publish_job_id": job.id,
            "platform_account_id": job.platform_account_id,
            "publish_mode": job.publish_mode,
        },
    )
    db.add(task)


@router.get("/jobs")
def get_publish_jobs(
    platform: Optional[str] = None,
    account_id: Optional[int] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    statement = (
        select(PublishJob)
        .where(PublishJob.user_id == current_user.id)
    )
    if platform:
        statement = statement.where(PublishJob.platform == platform)
    if account_id:
        statement = statement.where(PublishJob.platform_account_id == account_id)
    jobs = db.scalars(statement.order_by(PublishJob.created_at.desc(), PublishJob.id.desc())).all()
    return paginated([serialize_publish_job(job) for job in jobs], page, page_size)


@router.get("/jobs/{job_id}")
def get_publish_job(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return serialize_publish_job(_get_owned_publish_job(db, current_user, job_id))


@router.patch("/jobs/{job_id}")
def update_publish_job(
    job_id: int,
    payload: PublishJobUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_owned_publish_job(db, current_user, job_id)
    if payload.platform_account_id is not None:
        account = db.get(PlatformAccount, payload.platform_account_id)
        if account is None or account.user_id != current_user.id or account.platform != job.platform:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Platform account not found")
        job.platform_account_id = account.id
    if payload.title is not None:
        job.title = payload.title
    if payload.body is not None:
        job.body = payload.body
    if payload.publish_mode is not None:
        job.publish_mode = payload.publish_mode
    if payload.scheduled_at is not None or payload.publish_mode == "immediate":
        job.scheduled_at = payload.scheduled_at
    _update_publish_options(job, payload)

    db.commit()
    db.refresh(job)
    return serialize_publish_job(job)


@router.post("/jobs/{job_id}/android/prepare")
def prepare_android_publish(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    android_service: AndroidXhsService = Depends(get_android_xhs_service),
):
    """Copy reviewed images to a bound phone and open XHS; never clicks final publish."""
    job = _get_owned_publish_job(db, current_user, job_id)
    if job.status in {"published", "publishing", "scheduled"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前发布任务不可发送到手机")
    if job.publish_mode == "scheduled":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="安卓手机发布不支持定时，请改为立即发布")
    if not job.title.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="发布标题不能为空")
    account = _get_owned_android_account(db, current_user, job)
    try:
        result = android_service.prepare(
            serial=account.external_user_id or "",
            user_id=current_user.id,
            job_id=job.id,
            assets=_job_assets(db, job),
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    options = _load_publish_options(job)
    options["android_stage"] = {"serial": account.external_user_id, **result}
    _store_publish_options(job, options)
    _record_publish_control_task(db, current_user, job, "android_publish_prepare")
    db.commit()
    db.refresh(job)
    return {
        "job": serialize_publish_job(job),
        "message": "图片已发送至手机，小红书已打开。请在手机选择图片并进入编辑页，再点击“写入文案”。",
    }


@router.post("/jobs/{job_id}/android/publish")
def direct_android_publish(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    android_service: AndroidXhsService = Depends(get_android_xhs_service),
):
    """Publish the exact reviewed payload to the bound Android phone in one run."""
    job = _get_owned_publish_job(db, current_user, job_id)
    if job.status in {"published", "publishing", "scheduled"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前发布任务不可发布")
    if job.publish_mode == "scheduled":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="安卓手机发布不支持定时")
    if not job.title.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="发布标题不能为空")
    account = _get_owned_android_account(db, current_user, job)
    job.status = "publishing"
    job.publish_error = ""
    db.commit()
    try:
        result = android_service.publish_direct(
            serial=account.external_user_id or "",
            user_id=current_user.id,
            job_id=job.id,
            assets=_job_assets(db, job),
            title=job.title,
            body=_append_android_tags(job),
            product_keyword=str(_load_publish_options(job).get("product_keyword") or ""),
        )
    except Exception as exc:
        ambiguous = "publish result requires verification" in str(exc)
        job.status = "verification_required" if ambiguous else "failed"
        job.publish_error = "已点击手机发布按钮，但未能自动确认结果；请检查手机后确认，勿重复发布。" if ambiguous else str(exc)
        _record_publish_control_task(db, current_user, job, "android_publish_verification_required" if ambiguous else "android_publish_failed")
        db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT if ambiguous else status.HTTP_502_BAD_GATEWAY, detail=job.publish_error) from exc

    options = _load_publish_options(job)
    options["android_result"] = result
    options.pop("android_stage", None)
    options.pop("android_review_image", None)
    _store_publish_options(job, options)
    job.status = "published"
    job.published_at = shanghai_now()
    _record_publish_control_task(db, current_user, job, "android_publish_direct")
    db.commit()
    db.refresh(job)
    return serialize_publish_job(job)


@router.post("/jobs/{job_id}/android/fill")
def fill_android_publish(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    android_service: AndroidXhsService = Depends(get_android_xhs_service),
):
    """Write the reviewed copy only after the user manually reaches XHS's compose page."""
    job = _get_owned_publish_job(db, current_user, job_id)
    account = _get_owned_android_account(db, current_user, job)
    options = _load_publish_options(job)
    if not isinstance(options.get("android_stage"), dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请先发送图片到手机")
    try:
        file_name = android_service.fill_for_review(
            serial=account.external_user_id or "",
            user_id=current_user.id,
            job_id=job.id,
            title=job.title,
            body=_append_android_tags(job),
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    options["android_review_image"] = f"/api/files/media/{file_name}"
    _store_publish_options(job, options)
    job.status = "awaiting_review"
    _record_publish_control_task(db, current_user, job, "android_publish_fill")
    db.commit()
    db.refresh(job)
    return {
        "job": serialize_publish_job(job),
        "review_image_url": f"/api/files/media/{file_name}",
        "message": "文案已写入手机。请在手机核对图片顺序、标题、正文和可见性；确认无误后由你在手机点击发布。",
    }


@router.post("/jobs/{job_id}/android/confirm")
def confirm_android_publish(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Records the user's confirmation after they tap publish on their own phone."""
    job = _get_owned_publish_job(db, current_user, job_id)
    _get_owned_android_account(db, current_user, job)
    if job.status not in {"awaiting_review", "failed", "publishing", "verification_required"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="当前任务不能确认手机发布结果",
        )
    job.status = "published"
    job.published_at = shanghai_now()
    job.publish_error = ""
    _record_publish_control_task(db, current_user, job, "android_publish_confirmed")
    db.commit()
    db.refresh(job)
    return serialize_publish_job(job)


@router.post("/jobs/{job_id}/retry")
def retry_publish_job(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_owned_publish_job(db, current_user, job_id)
    if job.status not in {"failed", "cancelled"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Publish job cannot be retried")

    job.status = "pending"
    job.publish_error = ""
    job.external_note_id = ""
    job.published_at = None
    _record_publish_control_task(db, current_user, job, "creator_publish_retry")
    db.commit()
    db.refresh(job)
    return serialize_publish_job(job)


@router.post("/jobs/{job_id}/cancel")
def cancel_publish_job(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_owned_publish_job(db, current_user, job_id)
    if job.status not in {"pending", "scheduled"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Publish job cannot be cancelled")

    job.status = "cancelled"
    _record_publish_control_task(db, current_user, job, "creator_publish_cancel")
    db.commit()
    db.refresh(job)
    return serialize_publish_job(job)


@router.delete("/jobs/{job_id}")
def delete_publish_job(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_owned_publish_job(db, current_user, job_id)
    if job.status in {"publishing", "uploading"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="正在进行中的任务无法删除")

    for asset in db.scalars(
        select(PublishAsset)
        .where(PublishAsset.publish_job_id == job.id)
        .order_by(PublishAsset.sort_order.asc(), PublishAsset.id.asc())
    ).all():
        db.delete(asset)
    db.delete(job)
    db.commit()
    return {"ok": True}


@router.get("/jobs/{job_id}/assets")
def get_publish_assets(
    job_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_owned_publish_job(db, current_user, job_id)
    assets = db.scalars(
        select(PublishAsset).where(PublishAsset.publish_job_id == job.id).order_by(PublishAsset.sort_order.asc(), PublishAsset.id.asc())
    ).all()
    return paginated([serialize_publish_asset(asset) for asset in assets], page, page_size)


@router.post("/jobs/{job_id}/assets")
def create_publish_asset(
    job_id: int,
    payload: PublishAssetCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_owned_publish_job(db, current_user, job_id)
    max_order = db.scalar(
        select(func.max(PublishAsset.sort_order)).where(PublishAsset.publish_job_id == job.id)
    )
    asset = PublishAsset(
        publish_job_id=job.id,
        asset_type=payload.asset_type,
        file_path=payload.file_path,
        sort_order=(max_order + 1) if max_order is not None else 0,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return serialize_publish_asset(asset)


@router.delete("/assets/{asset_id}")
def delete_publish_asset(
    asset_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    asset = _get_owned_publish_asset(db, current_user, asset_id)
    db.delete(asset)
    db.commit()
    return {"id": asset_id, "status": "deleted"}


@router.post("/assets/{asset_id}/upload")
def upload_publish_asset(
    asset_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    adapter_factory=Depends(get_creator_publish_adapter_factory),
):
    asset = _get_owned_publish_asset(db, current_user, asset_id)
    job = db.get(PublishJob, asset.publish_job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Publish job not found")

    account = db.get(PlatformAccount, job.platform_account_id)
    if account is None or account.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    if account.platform != "xhs" or account.sub_type != "creator":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Creator account required")

    cookies = _get_latest_account_cookies(db, account.id)
    asset.upload_status = "uploading"
    asset.upload_error = ""
    db.commit()

    try:
        payload = adapter_factory(cookies).upload_media(asset.file_path, asset.asset_type)
        asset.upload_status = "uploaded"
        asset.creator_media_id = _extract_creator_media_id(payload)
        asset.creator_upload_info = json.dumps(payload, ensure_ascii=False)
        asset.upload_error = ""
        db.commit()
        db.refresh(asset)
        return serialize_publish_asset(asset)
    except Exception as exc:
        asset.upload_status = "failed"
        asset.upload_error = str(exc)
        db.commit()
        db.refresh(asset)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=asset.upload_error)


@router.post("/jobs/{job_id}/publish")
def publish_job_to_creator(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    adapter_factory=Depends(get_creator_publish_adapter_factory),
):
    job = _get_owned_publish_job(db, current_user, job_id)
    if not job.platform_account_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请先选择发布账号")
    account = db.get(PlatformAccount, job.platform_account_id)
    if account is None or account.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    if account.platform != "xhs" or account.sub_type != "creator":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Creator account required")
    if job.status in {"publishing", "published", "scheduled"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Publish job is already completed")
    if not job.title.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Publish title is required")
    if job.publish_mode == "scheduled":
        if job.scheduled_at is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Scheduled publish time is required")
        if job.scheduled_at <= shanghai_now():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Scheduled publish time must be in the future")

    assets = db.scalars(
        select(PublishAsset).where(PublishAsset.publish_job_id == job.id).order_by(PublishAsset.sort_order.asc(), PublishAsset.id.asc())
    ).all()
    if not assets:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="至少需要一个素材")

    cookies = _get_latest_account_cookies(db, account.id)
    adapter = adapter_factory(cookies)

    for asset in assets:
        if asset.upload_status in ("pending", "failed"):
            try:
                payload = adapter.upload_media(asset.file_path, asset.asset_type)
                asset.upload_status = "uploaded"
                asset.creator_media_id = _extract_creator_media_id(payload)
                asset.creator_upload_info = json.dumps(payload, ensure_ascii=False)
                asset.upload_error = ""
            except Exception as exc:
                asset.upload_status = "failed"
                asset.upload_error = str(exc)[:500]
                db.commit()
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"素材上传失败: {exc}",
                ) from exc
    db.commit()

    uploaded_images = [a for a in assets if a.asset_type == "image" and a.upload_status == "uploaded"]
    uploaded_videos = [a for a in assets if a.asset_type == "video" and a.upload_status == "uploaded"]

    if not uploaded_images and not uploaded_videos:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="没有成功上传的素材")

    if uploaded_videos:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="视频发布功能即将上线，目前仅支持图片发布")

    image_file_infos = [_asset_creator_upload_info(asset) for asset in uploaded_images]

    note_info = {
        "title": job.title,
        "desc": job.body,
        "media_type": "image",
        "image_file_infos": image_file_infos,
        "type": 1,
        "postTime": _scheduled_post_time(job),
    }
    _apply_publish_options(note_info, _load_publish_options(job))

    task = Task(
        user_id=current_user.id,
        platform=job.platform,
        task_type="creator_publish",
        status="running",
        progress=20,
        payload={
            "publish_job_id": job.id,
            "platform_account_id": account.id,
            "asset_ids": [asset.id for asset in uploaded_images],
            "publish_mode": job.publish_mode,
        },
    )
    db.add(task)
    job.status = "publishing"
    job.publish_error = ""
    db.commit()

    try:
        payload = adapter_factory(cookies).post_note(note_info)
        job.status = "scheduled" if job.publish_mode == "scheduled" else "published"
        job.external_note_id = _extract_external_note_id(payload)
        job.publish_error = ""
        job.published_at = shanghai_now()
        task.status = "completed"
        task.progress = 100
        task.payload = {
            **(task.payload or {}),
            "external_note_id": job.external_note_id,
            "published_at": job.published_at.isoformat(),
        }
        db.commit()
        db.refresh(job)
        return serialize_publish_job(job)
    except Exception as exc:
        job.status = "failed"
        job.publish_error = str(exc)
        task.status = "failed"
        task.progress = 100
        task.payload = {**(task.payload or {}), "error": str(exc)}
        db.commit()
        db.refresh(job)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=job.publish_error)
