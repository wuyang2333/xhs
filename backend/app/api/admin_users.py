from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.deps import get_current_user
from backend.app.core.security import hash_password
from backend.app.models import User

router = APIRouter(prefix="/admin/users", tags=["admin"])


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return current_user


class ResetPasswordRequest(BaseModel):
    password: str = Field(min_length=6, max_length=128)


class SetAdminRequest(BaseModel):
    is_admin: bool


class CreateSubAdminRequest(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=6, max_length=128)




def _resolve_owner(db: Session, admin: User) -> int:
    """找到根主账号（子管理员不再嵌套）。"""
    owner_id = admin.id
    seen = 0
    while admin.shares_user_id is not None and seen < 10:
        owner_id = admin.shares_user_id
        admin = db.get(User, owner_id)
        if admin is None:
            break
        seen += 1
    return owner_id


@router.post("/sub-admin")
def create_sub_admin(
    payload: CreateSubAdminRequest,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """创建一个子管理员：登录后共享主管理员（根账号）的全部数据。"""
    username = payload.username.strip()
    if db.scalar(select(User).where(User.username == username)) is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户名已存在")
    owner_id = _resolve_owner(db, _admin)
    user = User(
        username=username,
        password_hash=hash_password(payload.password),
        shares_user_id=owner_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "username": user.username, "shares_user_id": user.shares_user_id}


@router.get("")
def list_users(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    users = db.scalars(select(User).order_by(User.id)).all()
    return {
        "items": [
            {
                "id": user.id,
                "username": user.username,
                "is_admin": bool(user.is_admin),
                "created_at": user.created_at.isoformat() if user.created_at else None,
            }
            for user in users
        ]
    }


@router.post("/{user_id}/reset-password")
def reset_password(
    user_id: int,
    payload: ResetPasswordRequest,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    user.password_hash = hash_password(payload.password)
    db.commit()
    return {"id": user.id, "username": user.username, "status": "password_reset"}


@router.post("/{user_id}/admin")
def set_admin(
    user_id: int,
    payload: SetAdminRequest,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    user.is_admin = payload.is_admin
    db.commit()
    return {"id": user.id, "username": user.username, "is_admin": bool(user.is_admin)}