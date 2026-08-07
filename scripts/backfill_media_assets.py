#!/usr/bin/env python3
"""把历史图片资产回填进统一素材库（`media_assets`）。

背景：系统里曾并存三套互不相通的图片存储——`media_assets`（素材库）、`ai_generated_assets`
（AI 生图元数据）、以及磁盘 `storage/media/` 下由 `GET /api/files/images` 直接扫目录列出的
上传文件。后两者从未进入素材库，导致草稿选图器几乎是空的。本脚本做一次性合流。

两个来源：

1. `ai_generated_assets.file_path` → `source="ai"`
2. 磁盘 `storage/media/` 下 `xhs-upload-u{user_id}-*` 图片文件 → `source="upload"`

幂等：复用 `register_media_asset()`，它按 (user_id, local_path) 或 (user_id, url) 去重，
重复运行只会命中既有行。**不删除任何原有数据，不改动磁盘文件。**

用法：

    ./.venv-py312/bin/python scripts/backfill_media_assets.py --dry-run
    ./.venv-py312/bin/python scripts/backfill_media_assets.py
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# 直接执行本文件时 sys.path[0] 是 scripts/，仓库根不在路径里，`import backend...` 会失败。
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from backend.app.core.config import get_settings  # noqa: E402
from backend.app.core.database import SessionLocal  # noqa: E402
from backend.app.models import AiGeneratedAsset, MediaAsset, User  # noqa: E402
from backend.app.services.media_asset_service import register_media_asset  # noqa: E402

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# 与 backend/app/api/files.py 的 upload_file 命名保持一致：xhs-upload-u{user_id}-{uuid}{ext}
UPLOAD_NAME_PATTERN = re.compile(r"^xhs-upload-u(\d+)-")

MIME_BY_EXTENSION = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

MEDIA_URL_PREFIX = "/api/files/media/"


def _media_dir() -> Path:
    return Path(get_settings().storage_dir) / "media"


def _local_name_from_url(url: str) -> str:
    """AI 资产的 file_path 存的是 URL（`/api/files/media/xxx.png`），取出裸文件名。

    这样回填出来的行与 upload 链路写入的行同构（url + local_path 都在），
    `register_media_asset` 的 local_path 去重才对得上，后续 AI 生图写入也不会撞出重复行。
    """
    if url.startswith(MEDIA_URL_PREFIX):
        candidate = url[len(MEDIA_URL_PREFIX) :]
        # 只接受单段文件名，外部 URL 或带路径的值一律不当本地文件处理。
        if candidate and "/" not in candidate and ".." not in candidate:
            return candidate
    return ""


def _file_size(local_name: str) -> int:
    if not local_name:
        return 0
    candidate = _media_dir() / local_name
    return candidate.stat().st_size if candidate.is_file() else 0


class Stats:
    def __init__(self) -> None:
        self.scanned = 0
        self.created = 0
        self.skipped_existing = 0
        self.skipped_invalid = 0


def backfill_ai_assets(db: Session, *, dry_run: bool, valid_user_ids: set[int]) -> Stats:
    stats = Stats()
    rows = db.scalars(select(AiGeneratedAsset).order_by(AiGeneratedAsset.id.asc())).all()
    for row in rows:
        stats.scanned += 1
        file_path = (row.file_path or "").strip()
        if not file_path:
            stats.skipped_invalid += 1
            print(f"  [skip] ai_generated_assets#{row.id}: file_path 为空")
            continue
        if row.user_id not in valid_user_ids:
            stats.skipped_invalid += 1
            print(f"  [skip] ai_generated_assets#{row.id}: user_id={row.user_id} 不存在")
            continue

        local_name = _local_name_from_url(file_path)
        if _asset_exists(db, user_id=row.user_id, local_path=local_name, url=file_path):
            stats.skipped_existing += 1
            continue

        stats.created += 1
        label = local_name or file_path
        if dry_run:
            print(f"  [would-create] ai  user={row.user_id} {label}")
            continue
        register_media_asset(
            db,
            user_id=row.user_id,
            asset_type="image",
            url=file_path,
            local_path=local_name,
            original_name=(row.prompt or "AI 生成图片").strip()[:120],
            mime_type=MIME_BY_EXTENSION.get(Path(local_name).suffix.lower(), "") if local_name else "",
            file_size=_file_size(local_name),
            source="ai",
            # folder_id 留空：回填素材一律「未归类」，由用户自己整理。
        )
        print(f"  [create] ai  user={row.user_id} {label}")
    return stats


def backfill_uploaded_files(db: Session, *, dry_run: bool, valid_user_ids: set[int]) -> Stats:
    stats = Stats()
    media_dir = _media_dir()
    if not media_dir.is_dir():
        print(f"  媒体目录不存在，跳过磁盘回填：{media_dir}")
        return stats

    for path in sorted(media_dir.iterdir()):
        if not path.is_file():
            continue
        match = UPLOAD_NAME_PATTERN.match(path.name)
        if match is None:
            continue
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue

        stats.scanned += 1
        user_id = int(match.group(1))
        if user_id not in valid_user_ids:
            stats.skipped_invalid += 1
            print(f"  [skip] {path.name}: user_id={user_id} 不存在")
            continue

        url = f"{MEDIA_URL_PREFIX}{path.name}"
        if _asset_exists(db, user_id=user_id, local_path=path.name, url=url):
            stats.skipped_existing += 1
            continue

        stats.created += 1
        if dry_run:
            print(f"  [would-create] upload  user={user_id} {path.name}")
            continue
        register_media_asset(
            db,
            user_id=user_id,
            asset_type="image",
            url=url,
            local_path=path.name,
            # 原始文件名已不可考（上传时被 uuid 重命名，且磁盘上没有留存记录），
            # 用磁盘文件名兜底，保证列表和搜索里有可读的字符串。
            original_name=path.name,
            mime_type=MIME_BY_EXTENSION.get(path.suffix.lower(), ""),
            file_size=path.stat().st_size,
            source="upload",
        )
        print(f"  [create] upload  user={user_id} {path.name}")
    return stats


def _asset_exists(db: Session, *, user_id: int, local_path: str, url: str) -> bool:
    """与 register_media_asset 相同的判重口径，用于 --dry-run 下预测结果。"""
    statement = select(MediaAsset.id).where(MediaAsset.user_id == user_id)
    if local_path:
        statement = statement.where(MediaAsset.local_path == local_path)
    elif url:
        statement = statement.where(MediaAsset.url == url)
    else:
        return False
    return db.scalars(statement).first() is not None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="回填历史图片资产到 media_assets")
    parser.add_argument("--dry-run", action="store_true", help="只报告将新增什么，不落库")
    args = parser.parse_args(argv)

    mode = "DRY-RUN（不落库）" if args.dry_run else "实际写入"
    print(f"=== 回填 media_assets：{mode} ===")
    print(f"数据库：{get_settings().database_url}")
    print(f"媒体目录：{_media_dir()}")

    db = SessionLocal()
    try:
        existing_count = len(db.scalars(select(MediaAsset.id)).all())
        valid_user_ids = set(db.scalars(select(User.id)).all())
        print(f"现有 media_assets：{existing_count} 条；用户数：{len(valid_user_ids)}")

        print("\n-- 来源一：ai_generated_assets --")
        ai_stats = backfill_ai_assets(db, dry_run=args.dry_run, valid_user_ids=valid_user_ids)

        print("\n-- 来源二：磁盘 xhs-upload-u*  --")
        upload_stats = backfill_uploaded_files(db, dry_run=args.dry_run, valid_user_ids=valid_user_ids)

        if args.dry_run:
            db.rollback()
        else:
            db.commit()

        total_created = ai_stats.created + upload_stats.created
        print("\n=== 统计 ===")
        for name, stats in (("AI 资产", ai_stats), ("磁盘上传图", upload_stats)):
            print(
                f"{name}：扫描 {stats.scanned}，"
                f"{'将新增' if args.dry_run else '新增'} {stats.created}，"
                f"已存在跳过 {stats.skipped_existing}，无效跳过 {stats.skipped_invalid}"
            )
        print(f"合计{'将新增' if args.dry_run else '新增'}：{total_created}")
        if not args.dry_run:
            final_count = len(db.scalars(select(MediaAsset.id)).all())
            print(f"回填后 media_assets：{final_count} 条（回填前 {existing_count}）")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"回填失败，已回滚：{exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
