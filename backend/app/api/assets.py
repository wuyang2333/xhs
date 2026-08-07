from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, insert, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.deps import get_current_user
from backend.app.core.time import shanghai_now
from backend.app.models import AssetFolder, AssetScene, AssetTag, DraftAsset, MediaAsset, Product, User, media_asset_tags
from backend.app.schemas.common import paginated
from backend.app.services.media_asset_service import register_media_asset, resolve_folder_path

router = APIRouter(prefix="/assets", tags=["assets"])


class NamedCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class NamedUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class FolderCreateRequest(NamedCreateRequest):
    parent_id: Optional[int] = None


class FolderUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    parent_id: Optional[int] = None
    cover_asset_id: Optional[int] = None


class ProductCreateRequest(NamedCreateRequest):
    description: str = ""


class ProductUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = None


class TagCreateRequest(NamedCreateRequest):
    color: str = Field(default="", max_length=24)


class TagUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    color: Optional[str] = Field(default=None, max_length=24)


class AssetCreateRequest(BaseModel):
    asset_type: str = Field(pattern="^(image|video)$")
    # "坐浴盆/卫生间场景" —— 缺失的层级会自动建出来。与 folder_id 同时传时 folder_id 优先。
    folder_path: str = Field(default="", max_length=1024)
    url: str = Field(default="", max_length=4096)
    local_path: str = Field(default="", max_length=2048)
    original_name: str = Field(default="", max_length=256)
    mime_type: str = Field(default="", max_length=128)
    file_size: int = Field(default=0, ge=0)
    source: str = Field(default="manual", max_length=32)
    folder_id: Optional[int] = None
    product_id: Optional[int] = None
    scene_id: Optional[int] = None
    tag_ids: list[int] = Field(default_factory=list)


class AssetBulkUpdateRequest(BaseModel):
    asset_ids: list[int] = Field(min_length=1)
    folder_id: Optional[int] = None
    product_id: Optional[int] = None
    scene_id: Optional[int] = None
    tag_ids: Optional[list[int]] = None


class AssetReorderRequest(BaseModel):
    asset_ids: list[int] = Field(min_length=1)


def _clean_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Name must not be blank")
    return name


def _serialize_folder(folder: AssetFolder) -> dict:
    return {
        "id": folder.id,
        "name": folder.name,
        "parent_id": folder.parent_id,
        "cover_asset_id": folder.cover_asset_id,
        "created_at": folder.created_at.isoformat(),
    }


def _serialize_named(item) -> dict:
    result = {"id": item.id, "name": item.name, "created_at": item.created_at.isoformat()}
    if isinstance(item, Product):
        result["description"] = item.description
    if isinstance(item, AssetTag):
        result["color"] = item.color
    return result


def _asset_tag_items(db: Session, asset_ids: list[int]) -> dict[int, list[dict]]:
    if not asset_ids:
        return {}
    rows = db.execute(
        select(media_asset_tags.c.media_asset_id, AssetTag)
        .join(AssetTag, AssetTag.id == media_asset_tags.c.asset_tag_id)
        .where(media_asset_tags.c.media_asset_id.in_(asset_ids))
        .order_by(AssetTag.name.asc(), AssetTag.id.asc())
    ).all()
    result: dict[int, list[dict]] = {}
    for asset_id, tag in rows:
        result.setdefault(asset_id, []).append({"id": tag.id, "name": tag.name, "color": tag.color})
    return result


def _serialize_asset(asset: MediaAsset, tags: Optional[list[dict]] = None) -> dict:
    return {
        "id": asset.id,
        "asset_type": asset.asset_type,
        "url": asset.url,
        "local_path": asset.local_path,
        "original_name": asset.original_name,
        "mime_type": asset.mime_type,
        "file_size": asset.file_size,
        "source": asset.source,
        "folder_id": asset.folder_id,
        "product_id": asset.product_id,
        "scene_id": asset.scene_id,
        "tag_ids": [tag["id"] for tag in tags or []],
        "tags": tags or [],
        "sort_order": asset.sort_order,
        "created_at": asset.created_at.isoformat(),
        "last_used_at": asset.last_used_at.isoformat() if asset.last_used_at else None,
    }


def _owned(db: Session, model, user_id: int, object_id: int, detail: str):
    item = db.scalars(select(model).where(model.id == object_id, model.user_id == user_id)).first()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return item


def _optional_owned(db: Session, model, user_id: int, object_id: Optional[int], detail: str):
    if object_id is None:
        return None
    return _owned(db, model, user_id, object_id, detail)


def _owned_tags(db: Session, user_id: int, tag_ids: list[int]) -> list[AssetTag]:
    if len(tag_ids) != len(set(tag_ids)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tag IDs must not contain duplicates")
    tags = db.scalars(select(AssetTag).where(AssetTag.user_id == user_id, AssetTag.id.in_(tag_ids))).all() if tag_ids else []
    if len(tags) != len(tag_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset tag not found")
    return tags


def _commit_unique(db: Session, detail: str) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from exc


def _validate_folder_parent(db: Session, user_id: int, folder: Optional[AssetFolder], parent_id: Optional[int]) -> None:
    if parent_id is None:
        return
    parent = _owned(db, AssetFolder, user_id, parent_id, "Parent folder not found")
    if folder is None:
        return
    if parent.id == folder.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Folder cannot be its own parent")
    while parent.parent_id is not None:
        if parent.parent_id == folder.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Folder move would create a cycle")
        parent = _owned(db, AssetFolder, user_id, parent.parent_id, "Parent folder not found")


@router.get("/folders")
def list_folders(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    folders = db.scalars(
        select(AssetFolder)
        .where(AssetFolder.user_id == current_user.id)
        .order_by(AssetFolder.parent_id.asc(), AssetFolder.name.asc(), AssetFolder.id.asc())
    ).all()
    return {"items": [_serialize_folder(folder) for folder in folders]}


@router.post("/folders")
def create_folder(payload: FolderCreateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _validate_folder_parent(db, current_user.id, None, payload.parent_id)
    folder = AssetFolder(user_id=current_user.id, name=_clean_name(payload.name), parent_id=payload.parent_id)
    db.add(folder)
    _commit_unique(db, "Folder name already exists under this parent")
    db.refresh(folder)
    return _serialize_folder(folder)


@router.patch("/folders/{folder_id}")
def update_folder(folder_id: int, payload: FolderUpdateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    folder = _owned(db, AssetFolder, current_user.id, folder_id, "Folder not found")
    fields = payload.model_fields_set
    if "parent_id" in fields:
        _validate_folder_parent(db, current_user.id, folder, payload.parent_id)
        folder.parent_id = payload.parent_id
    if payload.name is not None:
        folder.name = _clean_name(payload.name)
    if "cover_asset_id" in fields:
        if payload.cover_asset_id is not None:
            cover = _owned(db, MediaAsset, current_user.id, payload.cover_asset_id, "Cover asset not found")
            if cover.folder_id != folder.id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cover asset must belong to the folder")
        folder.cover_asset_id = payload.cover_asset_id
    _commit_unique(db, "Folder name already exists under this parent")
    db.refresh(folder)
    return _serialize_folder(folder)


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    folder = _owned(db, AssetFolder, current_user.id, folder_id, "Folder not found")
    has_child = db.scalar(select(func.count()).select_from(AssetFolder).where(AssetFolder.user_id == current_user.id, AssetFolder.parent_id == folder.id))
    has_asset = db.scalar(select(func.count()).select_from(MediaAsset).where(MediaAsset.user_id == current_user.id, MediaAsset.folder_id == folder.id))
    if has_child or has_asset:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Folder must be empty before deletion")
    db.delete(folder)
    db.commit()
    return {"id": folder_id, "status": "deleted"}


@router.get("/products")
def list_products(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(select(Product).where(Product.user_id == current_user.id).order_by(Product.name.asc(), Product.id.asc())).all()
    return {"items": [_serialize_named(item) for item in items]}


@router.post("/products")
def create_product(payload: ProductCreateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = Product(user_id=current_user.id, name=_clean_name(payload.name), description=payload.description)
    db.add(item)
    _commit_unique(db, "Product name already exists")
    db.refresh(item)
    return _serialize_named(item)


@router.patch("/products/{item_id}")
def update_product(item_id: int, payload: ProductUpdateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = _owned(db, Product, current_user.id, item_id, "Product not found")
    if payload.name is not None:
        item.name = _clean_name(payload.name)
    if payload.description is not None:
        item.description = payload.description
    _commit_unique(db, "Product name already exists")
    db.refresh(item)
    return _serialize_named(item)


@router.delete("/products/{item_id}")
def delete_product(item_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = _owned(db, Product, current_user.id, item_id, "Product not found")
    db.execute(
        MediaAsset.__table__.update()
        .where(MediaAsset.user_id == current_user.id, MediaAsset.product_id == item.id)
        .values(product_id=None)
    )
    db.delete(item)
    db.commit()
    return {"id": item_id, "status": "deleted"}


@router.get("/scenes")
def list_scenes(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(select(AssetScene).where(AssetScene.user_id == current_user.id).order_by(AssetScene.name.asc(), AssetScene.id.asc())).all()
    return {"items": [_serialize_named(item) for item in items]}


@router.post("/scenes")
def create_scene(payload: NamedCreateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = AssetScene(user_id=current_user.id, name=_clean_name(payload.name))
    db.add(item)
    _commit_unique(db, "Scene name already exists")
    db.refresh(item)
    return _serialize_named(item)


@router.patch("/scenes/{item_id}")
def update_scene(item_id: int, payload: NamedUpdateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = _owned(db, AssetScene, current_user.id, item_id, "Scene not found")
    item.name = _clean_name(payload.name)
    _commit_unique(db, "Scene name already exists")
    db.refresh(item)
    return _serialize_named(item)


@router.delete("/scenes/{item_id}")
def delete_scene(item_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = _owned(db, AssetScene, current_user.id, item_id, "Scene not found")
    db.execute(
        MediaAsset.__table__.update()
        .where(MediaAsset.user_id == current_user.id, MediaAsset.scene_id == item.id)
        .values(scene_id=None)
    )
    db.delete(item)
    db.commit()
    return {"id": item_id, "status": "deleted"}


@router.get("/tags")
def list_tags(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(select(AssetTag).where(AssetTag.user_id == current_user.id).order_by(AssetTag.name.asc(), AssetTag.id.asc())).all()
    return {"items": [_serialize_named(item) for item in items]}


@router.post("/tags")
def create_tag(payload: TagCreateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = AssetTag(user_id=current_user.id, name=_clean_name(payload.name), color=payload.color)
    db.add(item)
    _commit_unique(db, "Tag name already exists")
    db.refresh(item)
    return _serialize_named(item)


@router.patch("/tags/{item_id}")
def update_tag(item_id: int, payload: TagUpdateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = _owned(db, AssetTag, current_user.id, item_id, "Asset tag not found")
    if payload.name is not None:
        item.name = _clean_name(payload.name)
    if payload.color is not None:
        item.color = payload.color
    _commit_unique(db, "Tag name already exists")
    db.refresh(item)
    return _serialize_named(item)


@router.delete("/tags/{item_id}")
def delete_tag(item_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = _owned(db, AssetTag, current_user.id, item_id, "Asset tag not found")
    db.execute(delete(media_asset_tags).where(media_asset_tags.c.asset_tag_id == item.id))
    db.delete(item)
    db.commit()
    return {"id": item_id, "status": "deleted"}


@router.get("")
def list_assets(
    folder_id: Optional[int] = None,
    product_id: Optional[int] = None,
    scene_id: Optional[int] = None,
    tag_id: Optional[int] = None,
    search: Optional[str] = None,
    recent: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    statement = select(MediaAsset).where(MediaAsset.user_id == current_user.id)
    if folder_id is not None:
        statement = statement.where(MediaAsset.folder_id == folder_id)
    if product_id is not None:
        statement = statement.where(MediaAsset.product_id == product_id)
    if scene_id is not None:
        statement = statement.where(MediaAsset.scene_id == scene_id)
    if tag_id is not None:
        statement = statement.join(media_asset_tags).where(media_asset_tags.c.asset_tag_id == tag_id)
    if search and search.strip():
        needle = f"%{search.strip()}%"
        statement = statement.where(or_(MediaAsset.original_name.ilike(needle), MediaAsset.url.ilike(needle), MediaAsset.local_path.ilike(needle)))
    if recent:
        statement = statement.where(or_(MediaAsset.last_used_at >= shanghai_now() - timedelta(days=30), MediaAsset.created_at >= shanghai_now() - timedelta(days=30)))
    assets = db.scalars(statement.order_by(MediaAsset.sort_order.asc(), MediaAsset.id.asc())).all()
    tag_map = _asset_tag_items(db, [asset.id for asset in assets])
    return paginated([_serialize_asset(asset, tag_map.get(asset.id)) for asset in assets], page, page_size)


@router.post("")
def create_asset(payload: AssetCreateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not payload.url and not payload.local_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Asset URL or local path is required")
    _optional_owned(db, AssetFolder, current_user.id, payload.folder_id, "Folder not found")
    _optional_owned(db, Product, current_user.id, payload.product_id, "Product not found")
    _optional_owned(db, AssetScene, current_user.id, payload.scene_id, "Scene not found")
    tags = _owned_tags(db, current_user.id, payload.tag_ids)
    folder_id = payload.folder_id
    if folder_id is None and payload.folder_path.strip():
        try:
            resolved = resolve_folder_path(db, user_id=current_user.id, path=payload.folder_path)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        folder_id = resolved.id if resolved else None
    asset = register_media_asset(
        db,
        user_id=current_user.id,
        asset_type=payload.asset_type,
        url=payload.url,
        local_path=payload.local_path,
        original_name=payload.original_name,
        mime_type=payload.mime_type,
        file_size=payload.file_size,
        source=payload.source,
        folder_id=folder_id,
        product_id=payload.product_id,
        scene_id=payload.scene_id,
    )
    db.execute(delete(media_asset_tags).where(media_asset_tags.c.media_asset_id == asset.id))
    if tags:
        db.execute(insert(media_asset_tags), [{"media_asset_id": asset.id, "asset_tag_id": tag.id} for tag in tags])
    db.commit()
    db.refresh(asset)
    return _serialize_asset(asset, [{"id": tag.id, "name": tag.name, "color": tag.color} for tag in tags])


@router.patch("/batch")
def update_assets(payload: AssetBulkUpdateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if len(payload.asset_ids) != len(set(payload.asset_ids)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Asset IDs must not contain duplicates")
    assets = db.scalars(select(MediaAsset).where(MediaAsset.user_id == current_user.id, MediaAsset.id.in_(payload.asset_ids))).all()
    if len(assets) != len(payload.asset_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media asset not found")
    fields = payload.model_fields_set
    if "folder_id" in fields:
        _optional_owned(db, AssetFolder, current_user.id, payload.folder_id, "Folder not found")
    if "product_id" in fields:
        _optional_owned(db, Product, current_user.id, payload.product_id, "Product not found")
    if "scene_id" in fields:
        _optional_owned(db, AssetScene, current_user.id, payload.scene_id, "Scene not found")
    tags = _owned_tags(db, current_user.id, payload.tag_ids or []) if "tag_ids" in fields else None
    for asset in assets:
        if "folder_id" in fields:
            asset.folder_id = payload.folder_id
        if "product_id" in fields:
            asset.product_id = payload.product_id
        if "scene_id" in fields:
            asset.scene_id = payload.scene_id
    if tags is not None:
        db.execute(delete(media_asset_tags).where(media_asset_tags.c.media_asset_id.in_(payload.asset_ids)))
        values = [{"media_asset_id": asset.id, "asset_tag_id": tag.id} for asset in assets for tag in tags]
        if values:
            db.execute(insert(media_asset_tags), values)
    db.commit()
    return {"updated_count": len(assets)}


@router.put("/reorder")
def reorder_assets(payload: AssetReorderRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if len(payload.asset_ids) != len(set(payload.asset_ids)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Asset IDs must not contain duplicates")
    assets = db.scalars(select(MediaAsset).where(MediaAsset.user_id == current_user.id)).all()
    existing_ids = {asset.id for asset in assets}
    if set(payload.asset_ids) != existing_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Asset IDs must include every media asset exactly once")
    asset_map = {asset.id: asset for asset in assets}
    for index, asset_id in enumerate(payload.asset_ids):
        asset_map[asset_id].sort_order = index
    db.commit()
    return {"ok": True}


@router.delete("/{asset_id}")
def delete_asset(asset_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    asset = _owned(db, MediaAsset, current_user.id, asset_id, "Media asset not found")
    if db.scalar(select(func.count()).select_from(DraftAsset).where(DraftAsset.media_asset_id == asset.id)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Media asset is still used by a draft")
    db.execute(delete(media_asset_tags).where(media_asset_tags.c.media_asset_id == asset.id))
    db.execute(
        AssetFolder.__table__.update()
        .where(AssetFolder.user_id == current_user.id, AssetFolder.cover_asset_id == asset.id)
        .values(cover_asset_id=None)
    )
    db.delete(asset)
    db.commit()
    return {"id": asset_id, "status": "deleted"}
