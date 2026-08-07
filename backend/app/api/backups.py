from fastapi import APIRouter, Depends
from backend.app.core.deps import get_current_user
from backend.app.models import User
from backend.app.services.backup_service import create_backup, list_backups

router = APIRouter(prefix="/backups", tags=["backups"])


@router.get("")
def backups(current_user: User = Depends(get_current_user)):
    return {"items": list_backups()}


@router.post("")
def backup(current_user: User = Depends(get_current_user)):
    return create_backup(f"manual_user_{current_user.id}", include_source=True)
