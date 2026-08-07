from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.app.core.database import get_db
from backend.app.core.deps import get_current_user
from backend.app.core.time import shanghai_now
from backend.app.models import AiDraft, DraftAsset, MediaAsset, Note, NoteAsset, PlatformAccount, PublishAsset, PublishJob, User
from backend.app.schemas.common import paginated

router = APIRouter(prefix="/drafts", tags=["drafts"])


class DraftCreateRequest(BaseModel):
    platform: str = Field(pattern="^xhs$")
    platform_account_id: Optional[int] = None
    source_note_id: Optional[int] = None
    title: str = Field(default="", max_length=20)
    body: str = ""
    intent: str = Field(default="publish", max_length=32)


class DraftUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=20)
    body: Optional[str] = None
    tags: Optional[list[dict]] = None


class DraftSendToPublishRequest(BaseModel):
    platform_account_id: Optional[int] = None
    publish_mode: str = Field(default="immediate", pattern="^(immediate|scheduled)$")
    scheduled_at: Optional[datetime] = None
    topics: Optional[list[str]] = None
    location: Optional[str] = None
    privacy_type: Optional[int] = Field(default=None, ge=0, le=1)
    is_private: Optional[bool] = None


def _clean_topics(topics: Optional[list[str]]) -> list[str]:
    if topics is None:
        return []
    return [topic.strip() for topic in topics if topic and topic.strip()]


def _build_publish_options(payload: DraftSendToPublishRequest) -> dict[str, Any]:
    options: dict[str, Any] = {}
    topics = _clean_topics(payload.topics)
    if topics:
        options["topics"] = topics
    if payload.location and payload.location.strip():
        options["location"] = payload.location.strip()
    if payload.is_private is not None:
        options["is_private"] = payload.is_private
        options["privacy_type"] = 1 if payload.is_private else 0
    elif payload.privacy_type is not None:
        options["privacy_type"] = payload.privacy_type
        options["is_private"] = payload.privacy_type == 1
    return options


def _serialize_draft(draft: AiDraft) -> dict:
    return {
        "id": draft.id,
        "platform": draft.platform,
        "platform_account_id": draft.platform_account_id,
        "title": draft.title,
        "body": draft.body,
        "tags": draft.tags or [],
        "source_note_id": draft.source_note_id,
        "created_at": draft.created_at.isoformat(),
    }


def _serialize_publish_job(job: PublishJob) -> dict:
    try:
        publish_options = json.loads(job.publish_options or "{}")
    except json.JSONDecodeError:
        publish_options = {}
    return {
        "id": job.id,
        "platform_account_id": job.platform_account_id,
        "source_draft_id": job.source_draft_id,
        "platform": job.platform,
        "title": job.title,
        "body": job.body,
        "publish_mode": job.publish_mode,
        "publish_options": publish_options,
        "status": job.status,
        "scheduled_at": job.scheduled_at.isoformat() if job.scheduled_at else None,
        "created_at": job.created_at.isoformat(),
    }


def _sync_draft_to_unpublished_jobs(db: Session, draft: AiDraft) -> None:
    jobs = db.scalars(
        select(PublishJob).where(
            PublishJob.source_draft_id == draft.id,
            PublishJob.user_id == draft.user_id,
            PublishJob.status.notin_(("published", "publishing", "uploading")),
        )
    ).all()
    for job in jobs:
        job.title = draft.title
        job.body = draft.body
        try:
            options = json.loads(job.publish_options or "{}")
        except json.JSONDecodeError:
            options = {}
        if draft.tags:
            options["draft_tags"] = draft.tags
        else:
            options.pop("draft_tags", None)
        job.publish_options = json.dumps(
            options,
            ensure_ascii=False,
            separators=(",", ":"),
        )


def _sync_draft_assets_to_unpublished_jobs(db: Session, draft: AiDraft) -> None:
    """Keep pending publish jobs in the exact reviewed draft asset order."""
    jobs = db.scalars(
        select(PublishJob).where(
            PublishJob.source_draft_id == draft.id,
            PublishJob.user_id == draft.user_id,
            PublishJob.status.notin_(("published", "publishing", "uploading")),
        )
    ).all()
    if not jobs:
        return
    draft_assets = db.scalars(
        select(DraftAsset)
        .where(DraftAsset.draft_id == draft.id)
        .order_by(DraftAsset.sort_order.asc(), DraftAsset.id.asc())
    ).all()
    for job in jobs:
        old_assets = db.scalars(
            select(PublishAsset)
            .where(PublishAsset.publish_job_id == job.id)
            .order_by(PublishAsset.sort_order.asc(), PublishAsset.id.asc())
        ).all()
        for old_asset in old_assets:
            db.delete(old_asset)
        for draft_asset in draft_assets:
            file_path = (
                f"/api/files/media/{draft_asset.local_path}"
                if draft_asset.local_path
                else draft_asset.url
            )
            db.add(PublishAsset(
                publish_job_id=job.id,
                source_draft_asset_id=draft_asset.id,
                asset_type=draft_asset.asset_type,
                file_path=file_path,
                sort_order=draft_asset.sort_order,
                upload_status="pending",
            ))


def _get_owned_source_note(db: Session, current_user: User, note_id: int) -> Note:
    note = db.get(Note, note_id)
    if note is None or note.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source note not found")
    return note


@router.get("")
def get_drafts(
    platform: Optional[str] = None,
    account_id: Optional[int] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    statement = select(AiDraft).where(AiDraft.user_id == current_user.id)
    if platform:
        statement = statement.where(AiDraft.platform == platform)
    if account_id:
        statement = statement.where(AiDraft.platform_account_id == account_id)
    drafts = db.scalars(statement.order_by(AiDraft.created_at.desc())).all()
    return paginated([_serialize_draft(draft) for draft in drafts], page, page_size)


@router.post("")
def create_draft(
    payload: DraftCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source_note = None
    if payload.platform_account_id is not None:
        account = db.get(PlatformAccount, payload.platform_account_id)
        if account is None or account.user_id != current_user.id or account.platform != payload.platform:
            raise HTTPException(status_code=404, detail="Platform account not found")
    if payload.source_note_id is not None:
        source_note = _get_owned_source_note(db, current_user, payload.source_note_id)

    # Extract tags from source note
    tags = None
    if source_note and source_note.raw_json:
        raw = source_note.raw_json if isinstance(source_note.raw_json, dict) else {}
        tag_list = raw.get("tags") or raw.get("tag_list")
        if not tag_list:
            data = raw.get("data")
            if isinstance(data, dict):
                items = data.get("items") or []
                if items and isinstance(items[0], dict):
                    card = items[0].get("note_card") or {}
                    if isinstance(card, dict):
                        tag_list = card.get("tag_list")
        if isinstance(tag_list, list):
            tags = []
            for t in tag_list:
                if isinstance(t, str):
                    tags.append({"name": t})
                elif isinstance(t, dict) and t.get("name"):
                    tags.append({"id": str(t.get("id", "")), "name": str(t["name"])})

    draft = AiDraft(
        user_id=current_user.id,
        platform_account_id=payload.platform_account_id,
        platform=payload.platform,
        title=payload.title or (source_note.title if source_note else ""),
        body=payload.body or (source_note.content if source_note else ""),
        tags=tags,
        source_note_id=source_note.id if source_note else None,
    )
    db.add(draft)
    db.flush()

    if source_note:
        source_assets = db.scalars(
            select(NoteAsset).where(NoteAsset.note_id == source_note.id).order_by(NoteAsset.sort_order.asc(), NoteAsset.id.asc())
        ).all()
        for idx, na in enumerate(source_assets):
            db.add(DraftAsset(
                draft_id=draft.id,
                asset_type=na.asset_type,
                url=na.url,
                local_path=na.local_path,
                sort_order=idx,
            ))

    db.commit()
    db.refresh(draft)
    return _serialize_draft(draft)


@router.post("/{draft_id}/send-to-publish")
def send_draft_to_publish(
    draft_id: int,
    payload: DraftSendToPublishRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft = db.get(AiDraft, draft_id)
    if draft is None or draft.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")
    if len(draft.title.strip()) > 20:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="小红书标题不能超过20个字符",
        )

    account_id: Optional[int] = draft.platform_account_id
    if payload.platform_account_id is not None:
        account = db.get(PlatformAccount, payload.platform_account_id)
        if account is None or account.user_id != current_user.id or account.platform != draft.platform:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Platform account not found")
        account_id = account.id

    options = _build_publish_options(payload)
    if draft.tags:
        options["draft_tags"] = draft.tags

    job = PublishJob(
        user_id=current_user.id,
        platform_account_id=account_id,
        source_draft_id=draft.id,
        platform=draft.platform,
        title=draft.title,
        body=draft.body,
        publish_mode=payload.publish_mode,
        publish_options=json.dumps(options, ensure_ascii=False, separators=(",", ":")),
        scheduled_at=payload.scheduled_at,
        status="pending",
    )
    db.add(job)
    db.flush()

    draft_assets = db.scalars(
        select(DraftAsset).where(DraftAsset.draft_id == draft.id).order_by(DraftAsset.sort_order.asc(), DraftAsset.id.asc())
    ).all()
    for da in draft_assets:
        if da.local_path:
            file_path = f"/api/files/media/{da.local_path}"
        else:
            file_path = da.url
        pa = PublishAsset(
            publish_job_id=job.id,
            source_draft_asset_id=da.id,
            asset_type=da.asset_type,
            file_path=file_path,
            sort_order=da.sort_order,
            upload_status="pending",
        )
        db.add(pa)

    db.commit()
    db.refresh(job)
    return _serialize_publish_job(job)


@router.patch("/{draft_id}")
def update_draft(
    draft_id: int,
    payload: DraftUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft = db.get(AiDraft, draft_id)
    if draft is None or draft.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")

    if payload.title is not None:
        draft.title = payload.title
    if payload.body is not None:
        draft.body = payload.body
    if payload.tags is not None:
        draft.tags = list(payload.tags)
        flag_modified(draft, "tags")

    _sync_draft_to_unpublished_jobs(db, draft)
    db.commit()
    db.refresh(draft)
    return _serialize_draft(draft)


@router.delete("/{draft_id}")
def delete_draft(
    draft_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft = db.get(AiDraft, draft_id)
    if draft is None or draft.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")
    for asset in db.scalars(select(DraftAsset).where(DraftAsset.draft_id == draft.id)).all():
        db.execute(
            PublishAsset.__table__.update()
            .where(PublishAsset.source_draft_asset_id == asset.id)
            .values(source_draft_asset_id=None)
        )
        db.delete(asset)
    db.delete(draft)
    db.commit()
    return {"id": draft_id, "status": "deleted"}


# ---------------------------------------------------------------------------
# Draft assets
# ---------------------------------------------------------------------------

def _serialize_draft_asset(asset: DraftAsset) -> dict:
    display_url = f"/api/files/media/{asset.local_path}" if asset.local_path else asset.url
    return {
        "id": asset.id,
        "draft_id": asset.draft_id,
        "asset_type": asset.asset_type,
        "url": display_url,
        "local_path": asset.local_path,
        "media_asset_id": asset.media_asset_id,
        "sort_order": asset.sort_order,
        "is_cover": asset.is_cover,
    }


class DraftAssetCreateRequest(BaseModel):
    media_asset_id: Optional[int] = None
    asset_type: Optional[str] = Field(default=None, pattern="^(image|video)$")
    url: str = Field(default="", max_length=2048)
    local_path: str = Field(default="", max_length=512)
    is_cover: bool = False


class DraftAssetReorderRequest(BaseModel):
    asset_ids: list[int] = Field(min_length=1)


@router.get("/{draft_id}/assets")
def get_draft_assets(
    draft_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft = db.get(AiDraft, draft_id)
    if draft is None or draft.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Draft not found")
    assets = db.scalars(
        select(DraftAsset).where(DraftAsset.draft_id == draft.id).order_by(DraftAsset.sort_order.asc(), DraftAsset.id.asc())
    ).all()
    return {"items": [_serialize_draft_asset(a) for a in assets]}


@router.post("/{draft_id}/assets")
def add_draft_asset(
    draft_id: int,
    payload: DraftAssetCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft = db.get(AiDraft, draft_id)
    if draft is None or draft.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Draft not found")
    media_asset = None
    if payload.media_asset_id is not None:
        media_asset = db.scalars(
            select(MediaAsset).where(MediaAsset.id == payload.media_asset_id, MediaAsset.user_id == current_user.id)
        ).first()
        if media_asset is None:
            raise HTTPException(status_code=404, detail="Media asset not found")
    elif not payload.url and not payload.local_path:
        raise HTTPException(status_code=400, detail="Asset URL, local path, or media_asset_id is required")

    asset_url = media_asset.url if media_asset else payload.url
    asset_local_path = media_asset.local_path if media_asset else payload.local_path
    identity_checks = []
    if media_asset is not None:
        identity_checks.append(DraftAsset.media_asset_id == media_asset.id)
    if asset_url:
        identity_checks.append(DraftAsset.url == asset_url)
    if asset_local_path:
        identity_checks.append(DraftAsset.local_path == asset_local_path)
    duplicate = db.scalars(
        select(DraftAsset).where(
            DraftAsset.draft_id == draft.id,
            or_(*identity_checks),
        )
    ).first()
    if duplicate is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Asset already exists in draft")

    max_order = db.scalar(select(func.max(DraftAsset.sort_order)).where(DraftAsset.draft_id == draft.id))
    asset = DraftAsset(
        draft_id=draft.id,
        media_asset_id=media_asset.id if media_asset else None,
        asset_type=media_asset.asset_type if media_asset else (payload.asset_type or "image"),
        url=asset_url,
        local_path=asset_local_path,
        sort_order=(max_order + 1) if max_order is not None else 0,
        is_cover=payload.is_cover,
    )
    if payload.is_cover:
        db.execute(
            update(DraftAsset)
            .where(DraftAsset.draft_id == draft.id, DraftAsset.is_cover.is_(True))
            .values(is_cover=False)
        )
    if media_asset:
        media_asset.last_used_at = shanghai_now()
    db.add(asset)
    db.flush()
    _sync_draft_assets_to_unpublished_jobs(db, draft)
    db.commit()
    db.refresh(asset)
    return _serialize_draft_asset(asset)


@router.delete("/{draft_id}/assets/{asset_id}")
def delete_draft_asset(
    draft_id: int,
    asset_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft = db.get(AiDraft, draft_id)
    if draft is None or draft.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Draft not found")
    asset = db.scalars(select(DraftAsset).where(DraftAsset.id == asset_id, DraftAsset.draft_id == draft.id)).first()
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    db.execute(
        PublishAsset.__table__.update()
        .where(PublishAsset.source_draft_asset_id == asset.id)
        .values(source_draft_asset_id=None)
    )
    db.delete(asset)
    db.flush()
    _sync_draft_assets_to_unpublished_jobs(db, draft)
    db.commit()
    return {"id": asset_id, "status": "deleted"}


class DraftAssetUpdateRequest(BaseModel):
    url: Optional[str] = Field(default=None, max_length=2048)
    local_path: Optional[str] = Field(default=None, max_length=512)
    is_cover: Optional[bool] = None


@router.patch("/{draft_id}/assets/{asset_id}")
def update_draft_asset(
    draft_id: int,
    asset_id: int,
    payload: DraftAssetUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft = db.get(AiDraft, draft_id)
    if draft is None or draft.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Draft not found")
    asset = db.scalars(select(DraftAsset).where(DraftAsset.id == asset_id, DraftAsset.draft_id == draft.id)).first()
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    next_url = payload.url if payload.url is not None else asset.url
    next_local_path = payload.local_path if payload.local_path is not None else asset.local_path
    identity_checks = []
    if next_url:
        identity_checks.append(DraftAsset.url == next_url)
    if next_local_path:
        identity_checks.append(DraftAsset.local_path == next_local_path)
    if identity_checks and db.scalars(
        select(DraftAsset).where(
            DraftAsset.draft_id == draft.id,
            DraftAsset.id != asset.id,
            or_(*identity_checks),
        )
    ).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Asset already exists in draft")
    asset.url = next_url
    asset.local_path = next_local_path
    if payload.is_cover is True:
        db.execute(
            update(DraftAsset)
            .where(DraftAsset.draft_id == draft.id, DraftAsset.id != asset.id, DraftAsset.is_cover.is_(True))
            .values(is_cover=False)
        )
        asset.is_cover = True
    elif payload.is_cover is False:
        asset.is_cover = False
    _sync_draft_assets_to_unpublished_jobs(db, draft)
    db.commit()
    db.refresh(asset)
    return _serialize_draft_asset(asset)


@router.put("/{draft_id}/assets/{asset_id}/cover")
def set_draft_cover(
    draft_id: int,
    asset_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft = db.get(AiDraft, draft_id)
    if draft is None or draft.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Draft not found")
    selected = db.scalars(
        select(DraftAsset).where(DraftAsset.draft_id == draft.id, DraftAsset.id == asset_id)
    ).first()
    if selected is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    db.execute(
        update(DraftAsset)
        .where(DraftAsset.draft_id == draft.id, DraftAsset.id != selected.id, DraftAsset.is_cover.is_(True))
        .values(is_cover=False)
    )
    selected.is_cover = True
    db.commit()
    db.refresh(selected)
    return _serialize_draft_asset(selected)


@router.put("/{draft_id}/assets/reorder")
def reorder_draft_assets(
    draft_id: int,
    payload: DraftAssetReorderRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft = db.get(AiDraft, draft_id)
    if draft is None or draft.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Draft not found")
    assets = db.scalars(select(DraftAsset).where(DraftAsset.draft_id == draft.id)).all()
    existing_ids = {asset.id for asset in assets}
    submitted_ids = payload.asset_ids
    if len(submitted_ids) != len(set(submitted_ids)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Asset IDs must not contain duplicates")
    if set(submitted_ids) != existing_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Asset IDs must include every draft asset exactly once")
    asset_map = {asset.id: asset for asset in assets}
    for idx, aid in enumerate(submitted_ids):
        asset_map[aid].sort_order = idx
    _sync_draft_assets_to_unpublished_jobs(db, draft)
    db.commit()
    return {"ok": True}
