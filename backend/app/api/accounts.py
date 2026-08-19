from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.adapters.xhs.creator_login_adapter import XhsCreatorLoginAdapter
from backend.app.adapters.xhs.pc_api_adapter import XhsPcApiAdapter
from backend.app.adapters.xhs.pc_login_adapter import XhsPcLoginAdapter
from backend.app.core.database import get_db
from backend.app.core.deps import get_current_user
from backend.app.core.security import decrypt_text
from backend.app.core.time import shanghai_now
from backend.app.models import AccountCookieVersion, PlatformAccount, User
from backend.app.schemas.common import paginated
from backend.app.services.account_service import (
    account_profile_from_user_info,
    cookie_header_from_text,
    decode_cookie_text,
    enrich_user_info_with_xhs_self_profile,
    serialize_account,
    upsert_platform_account_from_login,
)
from backend.app.services.android_xhs_service import AndroidXhsService
from xhs_utils.cookie_util import trans_cookies

router = APIRouter(prefix="/accounts", tags=["accounts"])


class CookieImportRequest(BaseModel):
    platform: str = Field(pattern="^xhs$")
    sub_type: str = Field(pattern="^(pc|creator)$")
    cookie_string: str = Field(min_length=3)
    sync_creator: bool = False


class AndroidBindRequest(BaseModel):
    serial: str = Field(min_length=2, max_length=160)
    nickname: str = Field(default="", max_length=128)


class AndroidSerialRequest(BaseModel):
    serial: str = Field(min_length=2, max_length=160)


def get_pc_account_adapter() -> XhsPcLoginAdapter:
    return XhsPcLoginAdapter()


def get_creator_account_adapter() -> XhsCreatorLoginAdapter:
    return XhsCreatorLoginAdapter()


class XhsSelfProfileAdapter:
    def get_self_profile(self, cookies_text: str):
        return XhsPcApiAdapter(cookies_text).get_self_info()


def get_xhs_self_profile_adapter() -> XhsSelfProfileAdapter:
    return XhsSelfProfileAdapter()


def get_android_xhs_service() -> AndroidXhsService:
    return AndroidXhsService()


def _select_adapter(sub_type: str, pc_adapter: XhsPcLoginAdapter, creator_adapter: XhsCreatorLoginAdapter):
    return creator_adapter if sub_type == "creator" else pc_adapter


def _sync_creator_account_from_pc_cookie(
    *,
    db: Session,
    user_id: int,
    platform: str,
    cookie_string: str,
    creator_adapter: XhsCreatorLoginAdapter,
):
    try:
        creator_result = creator_adapter.exchange_from_user_cookies(trans_cookies(cookie_string))
        creator_cookies_text = json.dumps(creator_result["cookies"], ensure_ascii=False, separators=(",", ":"))
        creator_user_info = creator_adapter.get_user_info(creator_result["cookies"])
        upsert_platform_account_from_login(
            db=db,
            user_id=user_id,
            platform=platform,
            sub_type="creator",
            user_info=creator_user_info,
            cookies_text=creator_cookies_text,
        )
    except Exception:
        return None
    return True


@router.get("")
def get_accounts(
    platform: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    statement = select(PlatformAccount).where(PlatformAccount.user_id == current_user.id)
    if platform:
        statement = statement.where(PlatformAccount.platform == platform)
    accounts = db.scalars(statement.order_by(PlatformAccount.created_at.desc())).all()
    return paginated(
        [serialize_account(account) for account in accounts],
        page,
        page_size,
    )


@router.get("/android/devices")
def android_devices(
    current_user: User = Depends(get_current_user),
    android_service: AndroidXhsService = Depends(get_android_xhs_service),
):
    del current_user
    try:
        return {"items": android_service.devices()}
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.get("/android/setup-status")
def android_setup_status(
    serial: Optional[str] = Query(default=None, max_length=160),
    current_user: User = Depends(get_current_user),
    android_service: AndroidXhsService = Depends(get_android_xhs_service),
):
    del current_user
    return android_service.setup_status(serial)


@router.post("/android/setup-adb")
def android_setup_adb(
    current_user: User = Depends(get_current_user),
    android_service: AndroidXhsService = Depends(get_android_xhs_service),
):
    """一键安装 platform-tools。下载几十 MB，走长任务，前端轮询任务状态。"""
    del current_user
    try:
        return android_service.start_adb_install()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.get("/android/setup-adb/{task_id}")
def android_setup_adb_status(
    task_id: str,
    current_user: User = Depends(get_current_user),
    android_service: AndroidXhsService = Depends(get_android_xhs_service),
):
    del current_user
    try:
        return android_service.adb_install_status(task_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/android/install-ime")
def android_install_ime(
    payload: AndroidSerialRequest,
    current_user: User = Depends(get_current_user),
    android_service: AndroidXhsService = Depends(get_android_xhs_service),
):
    """装中文输入法。不装的话标题正文打不了中文。"""
    del current_user
    try:
        return android_service.install_ime(payload.serial)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/android/bind")
def bind_android_account(
    payload: AndroidBindRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    android_service: AndroidXhsService = Depends(get_android_xhs_service),
):
    try:
        device = android_service.doctor(payload.serial)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not device.get("xiaohongshu_installed"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="目标手机未安装小红书")

    account = db.scalar(
        select(PlatformAccount).where(
            PlatformAccount.user_id == current_user.id,
            PlatformAccount.platform == "xhs",
            PlatformAccount.sub_type == "android",
            PlatformAccount.external_user_id == payload.serial,
        )
    )
    action = "updated" if account else "created"
    now = shanghai_now()
    if account is None:
        account = PlatformAccount(
            user_id=current_user.id,
            platform="xhs",
            sub_type="android",
            external_user_id=payload.serial,
            created_at=now,
        )
        db.add(account)
    account.nickname = payload.nickname.strip() or account.nickname or f"{device.get('manufacturer', 'Android')} {device.get('model', '手机')}"
    account.status = "active"
    account.status_message = "已连接：在手机小红书中确认登录后可用于发布"
    account.profile_json = json.dumps(device, ensure_ascii=False, separators=(",", ":"))
    account.updated_at = now
    db.commit()
    db.refresh(account)
    return serialize_account(account, action)


@router.post("/import-cookie")
def import_cookie(
    payload: CookieImportRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    pc_adapter: XhsPcLoginAdapter = Depends(get_pc_account_adapter),
    creator_adapter: XhsCreatorLoginAdapter = Depends(get_creator_account_adapter),
    self_profile_adapter: XhsSelfProfileAdapter = Depends(get_xhs_self_profile_adapter),
):
    adapter = _select_adapter(payload.sub_type, pc_adapter, creator_adapter)
    try:
        user_info = adapter.get_user_info(trans_cookies(payload.cookie_string))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cookie is invalid or expired") from exc

    if payload.sub_type == "pc":
        try:
            self_profile = self_profile_adapter.get_self_profile(cookie_header_from_text(payload.cookie_string))
            user_info = enrich_user_info_with_xhs_self_profile(user_info, self_profile)
        except Exception:
            pass

    account, action = upsert_platform_account_from_login(
        db=db,
        user_id=current_user.id,
        platform=payload.platform,
        sub_type=payload.sub_type,
        user_info=user_info,
        cookies_text=payload.cookie_string,
    )
    if payload.sub_type == "pc" and payload.sync_creator:
        _sync_creator_account_from_pc_cookie(
            db=db,
            user_id=current_user.id,
            platform=payload.platform,
            cookie_string=payload.cookie_string,
            creator_adapter=creator_adapter,
        )
    db.commit()
    db.refresh(account)
    return serialize_account(account, action)


@router.post("/{account_id}/check")
def check_account(
    account_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    pc_adapter: XhsPcLoginAdapter = Depends(get_pc_account_adapter),
    creator_adapter: XhsCreatorLoginAdapter = Depends(get_creator_account_adapter),
    self_profile_adapter: XhsSelfProfileAdapter = Depends(get_xhs_self_profile_adapter),
):
    account = db.get(PlatformAccount, account_id)
    if account is None or account.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    if account.sub_type == "android":
        try:
            device = AndroidXhsService().doctor(account.external_user_id or "")
            account.status = "active" if device.get("xiaohongshu_installed") else "risk"
            account.status_message = "设备已连接" if account.status == "active" else "手机未安装小红书"
            account.profile_json = json.dumps(device, ensure_ascii=False, separators=(",", ":"))
        except Exception as exc:
            account.status = "expired"
            account.status_message = str(exc)
        account.updated_at = shanghai_now()
        db.commit()
        db.refresh(account)
        return serialize_account(account)

    cookie_version = db.scalars(
        select(AccountCookieVersion)
        .where(AccountCookieVersion.platform_account_id == account.id)
        .order_by(AccountCookieVersion.created_at.desc())
    ).first()
    if cookie_version is None:
        account.status = "expired"
        account.status_message = "No stored cookie version"
        db.commit()
        db.refresh(account)
        return serialize_account(account)

    adapter = _select_adapter(account.sub_type or "pc", pc_adapter, creator_adapter)
    try:
        cookies_text = decrypt_text(cookie_version.encrypted_cookies)
        user_info = adapter.get_user_info(decode_cookie_text(cookies_text))
        try:
            self_profile = self_profile_adapter.get_self_profile(cookie_header_from_text(cookies_text))
            user_info = enrich_user_info_with_xhs_self_profile(user_info, self_profile)
        except Exception:
            pass
        account.status = "active"
        account.status_message = ""
        account.nickname = user_info.get("nickname", account.nickname)
        account.avatar_url = user_info.get("avatar_url", account.avatar_url)
        account.external_user_id = user_info.get("external_user_id", account.external_user_id)
        account.profile_json = json.dumps(account_profile_from_user_info(user_info), ensure_ascii=False, separators=(",", ":"))
        account.updated_at = shanghai_now()

        if account.sub_type == "creator":
            try:
                from apis.xhs_creator_apis import XHS_Creator_Apis
                creator_cookies = decode_cookie_text(cookies_text)
                api = XHS_Creator_Apis()
                success, msg, _ = api.get_fileIds("image", creator_cookies)
                if not success:
                    account.status = "expired"
                    account.status_message = f"上传凭证获取失败: {msg}"
            except Exception as upload_exc:
                account.status = "expired"
                account.status_message = f"上传凭证验证异常: {upload_exc}"
    except Exception as exc:
        account.status = "expired"
        account.status_message = str(exc)
        account.updated_at = shanghai_now()

    db.commit()
    db.refresh(account)
    return serialize_account(account)


@router.patch("/{account_id}")
def update_account(account_id: int):
    return {"id": account_id, "status": "updated"}


@router.delete("/{account_id}")
def delete_account(
    account_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = db.get(PlatformAccount, account_id)
    if account is None or account.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    # 清理所有外键引用，否则 SQLite 外键约束阻止删除。
    # 可空的列置 NULL，NOT NULL 的列必须删整行。
    for cookie_version in db.scalars(
        select(AccountCookieVersion).where(AccountCookieVersion.platform_account_id == account.id)
    ).all():
        db.delete(cookie_version)
    db.flush()
    from sqlalchemy import text
    # 可空列：置 NULL
    for table, col in [
        ("monitoring_targets", "platform_account_id"),
        ("account_operation_profiles", "platform_account_id"),
        ("operation_plans", "platform_account_id"),
        ("learning_insights", "platform_account_id"),
        ("operation_strategy_proposals", "platform_account_id"),
        ("ai_drafts", "platform_account_id"),
        ("prompt_templates", "platform_account_id"),
        ("prompt_usages", "platform_account_id"),
        ("auto_tasks", "pc_account_id"),
        ("publish_jobs", "platform_account_id"),
    ]:
        db.execute(text(f"UPDATE {table} SET {col} = NULL WHERE {col} = :aid"), {"aid": account.id})
    db.flush()
    # NOT NULL 列：只能删整行（notes.platform_account_id 和 auto_tasks.creator_account_id）
    db.execute(text("DELETE FROM notes WHERE platform_account_id = :aid"), {"aid": account.id})
    db.execute(text("DELETE FROM auto_tasks WHERE creator_account_id = :aid"), {"aid": account.id})
    db.flush()
    db.delete(account)
    db.commit()
    return {"id": account_id, "status": "deleted"}
