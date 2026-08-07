from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.models import AssetFolder, MediaAsset

FOLDER_PATH_MAX_DEPTH = 8
FOLDER_NAME_MAX_LENGTH = 128


def resolve_folder_path(db: Session, *, user_id: int, path: str) -> Optional[AssetFolder]:
    """Resolve a "产品/场景" style path to a folder, creating missing levels.

    Segments are matched case-sensitively against existing folders at the same
    level, so uploading twice into "坐浴盆/卫生间场景" reuses the same folder
    instead of creating a duplicate. Returns None for an empty path.
    """
    segments = [segment.strip() for segment in path.replace("\\", "/").split("/")]
    segments = [segment for segment in segments if segment]
    if not segments:
        return None
    if len(segments) > FOLDER_PATH_MAX_DEPTH:
        raise ValueError(f"文件夹层级不能超过 {FOLDER_PATH_MAX_DEPTH} 层")
    for segment in segments:
        if len(segment) > FOLDER_NAME_MAX_LENGTH:
            raise ValueError(f"文件夹名称不能超过 {FOLDER_NAME_MAX_LENGTH} 个字符：{segment}")

    parent_id: Optional[int] = None
    folder: Optional[AssetFolder] = None
    for segment in segments:
        folder = db.scalars(
            select(AssetFolder).where(
                AssetFolder.user_id == user_id,
                AssetFolder.parent_id == parent_id,
                AssetFolder.name == segment,
            )
        ).first()
        if folder is None:
            folder = AssetFolder(user_id=user_id, name=segment, parent_id=parent_id)
            db.add(folder)
            db.flush()
        parent_id = folder.id
    return folder


def register_media_asset(
    db: Session,
    *,
    user_id: int,
    asset_type: str,
    url: str = "",
    local_path: str = "",
    original_name: str = "",
    mime_type: str = "",
    file_size: int = 0,
    source: str = "upload",
    folder_id: Optional[int] = None,
    product_id: Optional[int] = None,
    scene_id: Optional[int] = None,
) -> MediaAsset:
    statement = select(MediaAsset).where(MediaAsset.user_id == user_id)
    if local_path:
        statement = statement.where(MediaAsset.local_path == local_path)
    elif url:
        statement = statement.where(MediaAsset.url == url)
    else:
        statement = statement.where(MediaAsset.id == -1)
    asset = db.scalars(statement).first()
    if asset is None:
        # 排在末尾。若沿用模型默认的 0，则手动排序过之后新上传的素材会插到最前面，
        # 因为列表按 (sort_order, id) 排序。
        next_sort_order = db.scalar(
            select(func.coalesce(func.max(MediaAsset.sort_order), -1) + 1).where(MediaAsset.user_id == user_id)
        )
        asset = MediaAsset(
            user_id=user_id,
            asset_type=asset_type,
            url=url,
            local_path=local_path,
            original_name=original_name,
            mime_type=mime_type,
            file_size=file_size,
            source=source,
            folder_id=folder_id,
            product_id=product_id,
            scene_id=scene_id,
            sort_order=next_sort_order or 0,
        )
        db.add(asset)
    else:
        asset.asset_type = asset_type
        asset.url = url or asset.url
        asset.local_path = local_path or asset.local_path
        asset.original_name = original_name or asset.original_name
        asset.mime_type = mime_type or asset.mime_type
        asset.file_size = file_size or asset.file_size
        asset.source = source or asset.source
        if folder_id is not None:
            asset.folder_id = folder_id
        if product_id is not None:
            asset.product_id = product_id
        if scene_id is not None:
            asset.scene_id = scene_id
    db.flush()
    return asset
