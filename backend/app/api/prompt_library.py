from __future__ import annotations
import hashlib, json
from pathlib import Path
import yaml
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session
from backend.app.core.database import get_db
from backend.app.core.deps import get_current_user
from backend.app.core.time import shanghai_now
from backend.app.models import AssetScene, ObsidianSyncConfig, PlatformAccount, Product, PromptTemplate, PromptUsage, PromptVersion, User
from backend.app.services.backup_service import create_backup
from backend.app.services.obsidian_prompt_service import parse_markdown, resolve_selected_file, resolve_vault_path, scan_vault

router = APIRouter(prefix="/prompt-library", tags=["prompt-library"])


class PromptPayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    prompt_type: str = Field(pattern="^(image|note|title|rewrite|auto_ops)$")
    platform_account_id: int | None = None
    # 与图片素材共用的产品 / 场景实体，用于指令分层组合。
    product_id: int | None = None
    scene_id: int | None = None
    scenes: list[str] = []
    tags: list[str] = []
    content: str = Field(min_length=1)
    priority: int = Field(default=0, ge=-100, le=100)
    status: str = Field(default="active", pattern="^(active|paused|archived)$")
    change_note: str = ""


class UsagePayload(BaseModel):
    prompt_template_id: int
    platform_account_id: int | None = None
    module: str = Field(pattern="^(image_studio|draft_studio|auto_ops)$")
    scene: str = ""
    target_type: str = ""
    target_id: int | None = None
    outcome: dict = {}


class RestoreVersionPayload(BaseModel):
    change_note: str = Field(default="", max_length=500)


class ObsidianConfigPayload(BaseModel):
    vault_path: str = Field(min_length=1, max_length=2000)


class ObsidianImportPayload(BaseModel):
    relative_paths: list[str] = Field(min_length=1, max_length=500)


def _owned_account(db: Session, user_id: int, account_id: int | None) -> None:
    if account_id is None:
        return
    row = db.get(PlatformAccount, account_id)
    if not row or row.user_id != user_id:
        raise HTTPException(404, "账号不存在")


def _item(row: PromptTemplate) -> dict:
    return {"id": row.id, "name": row.name, "prompt_type": row.prompt_type,
        "platform_account_id": row.platform_account_id, "product_id": row.product_id,
        "scene_id": row.scene_id, "is_official": row.is_official,
        "scenes": json.loads(row.scenes_json or "[]"),
        "tags": json.loads(row.tags_json or "[]"), "content": row.content, "status": row.status,
        "priority": row.priority, "current_version": row.current_version, "source_type": row.source_type,
        "source_ref": row.source_ref, "created_at": row.created_at, "updated_at": row.updated_at}


def _match_item(row: PromptTemplate, *, account_id: int | None = None,
                product_id: int | None = None, scene_id: int | None = None,
                scene: str = "", product_name: str = "", scene_name: str = "") -> dict:
    """Serialize a prompt and explain why it matches the supplied context.

    Exact entity bindings outrank generic prompts. Free-text scenes are a softer
    signal, while priority remains useful inside the same specificity tier.
    """
    item = _item(row)
    reasons: list[str] = []
    specificity = 0
    if account_id is not None and row.platform_account_id == account_id:
        reasons.append("该账号专属")
        specificity += 4
    if product_id is not None and row.product_id == product_id:
        reasons.append(f"匹配产品：{product_name}" if product_name else "匹配产品")
        specificity += 3
    if scene_id is not None and row.scene_id == scene_id:
        reasons.append(f"匹配场景：{scene_name}" if scene_name else "匹配场景")
        specificity += 2

    needle = scene.strip().casefold()
    if needle:
        matched_scene = next((value for value in item["scenes"]
                              if needle in str(value).casefold() or str(value).casefold() in needle), None)
        if matched_scene is not None:
            reasons.append(f"匹配关键词：{matched_scene}")
            specificity += 1
    if not reasons and row.platform_account_id is None and row.product_id is None and row.scene_id is None:
        reasons.append("全局通用")

    # specificity is intentionally dominant over the bounded [-100, 100] priority.
    item["match_reasons"] = reasons
    item["match_score"] = specificity * 1000 + row.priority
    return item


def _context_names(db: Session, user_id: int, product_id: int | None,
                   scene_id: int | None) -> tuple[str, str]:
    product = db.get(Product, product_id) if product_id is not None else None
    scene = db.get(AssetScene, scene_id) if scene_id is not None else None
    product_name = product.name if product and product.user_id == user_id else ""
    scene_name = scene.name if scene and scene.user_id == user_id else ""
    return product_name, scene_name


def _owned_product(db: Session, user_id: int, product_id: int | None) -> None:
    if product_id is None:
        return
    row = db.get(Product, product_id)
    if not row or row.user_id != user_id:
        raise HTTPException(404, "产品不存在")


def _owned_scene(db: Session, user_id: int, scene_id: int | None) -> None:
    if scene_id is None:
        return
    row = db.get(AssetScene, scene_id)
    if not row or row.user_id != user_id:
        raise HTTPException(404, "场景不存在")


def _obsidian_config(db: Session, user_id: int) -> ObsidianSyncConfig | None:
    return db.scalar(select(ObsidianSyncConfig).where(ObsidianSyncConfig.user_id == user_id))


def _config_item(row: ObsidianSyncConfig | None) -> dict:
    return {
        "configured": bool(row and row.vault_path),
        "vault_path": row.vault_path if row else "",
        "last_scanned_at": row.last_scanned_at if row else None,
        "last_synced_at": row.last_synced_at if row else None,
    }


def _configured_vault(db: Session, user_id: int) -> tuple[ObsidianSyncConfig, Path]:
    config = _obsidian_config(db, user_id)
    if not config or not config.vault_path:
        raise HTTPException(400, "请先配置 Obsidian 仓库路径")
    try:
        return config, resolve_vault_path(config.vault_path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _obsidian_metadata_changed(row: PromptTemplate, item: dict) -> bool:
    return any((
        row.name != item["name"],
        row.prompt_type != item["prompt_type"],
        row.platform_account_id != item["platform_account_id"],
        json.loads(row.scenes_json or "[]") != item["scenes"],
        json.loads(row.tags_json or "[]") != item["tags"],
        row.priority != item["priority"],
        row.status != "active",
    ))


def _obsidian_needs_update(row: PromptTemplate, item: dict) -> bool:
    return row.source_hash != item["source_hash"] or _obsidian_metadata_changed(row, item)


@router.get("")
def list_prompts(prompt_type: str | None = None, account_id: int | None = None, status: str | None = "active",
                 product_id: int | None = None, scene_id: int | None = None, is_official: bool | None = None,
                 current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stmt = select(PromptTemplate).where(PromptTemplate.user_id == current_user.id)
    if prompt_type: stmt = stmt.where(PromptTemplate.prompt_type == prompt_type)
    if status: stmt = stmt.where(PromptTemplate.status == status)
    # 同 product_id / scene_id：IN (NULL, x) 匹配不到 NULL 行，通用指令会被漏掉（BUG-11）。
    if account_id: stmt = stmt.where(or_(PromptTemplate.platform_account_id.is_(None), PromptTemplate.platform_account_id == account_id))
    # 产品 / 场景筛选要包含未绑定的（NULL），因为通用指令要能与专属指令一起参与组合。
    # 注意不能写 in_([None, x])——SQL 里 IN (NULL, x) 永远匹配不到 NULL 行。
    if product_id: stmt = stmt.where(or_(PromptTemplate.product_id.is_(None), PromptTemplate.product_id == product_id))
    if scene_id: stmt = stmt.where(or_(PromptTemplate.scene_id.is_(None), PromptTemplate.scene_id == scene_id))
    if is_official is not None: stmt = stmt.where(PromptTemplate.is_official.is_(is_official))
    rows = db.scalars(stmt).all()
    product_name, scene_name = _context_names(db, current_user.id, product_id, scene_id)
    items = [_match_item(row, account_id=account_id, product_id=product_id, scene_id=scene_id,
                         product_name=product_name, scene_name=scene_name) for row in rows]
    # Context-specific prompts appear before generic prompts; priority and recency
    # break ties. With no context this preserves the prior priority-first behavior.
    items.sort(key=lambda item: (item["match_score"], item["updated_at"]), reverse=True)
    return {"items": items}


@router.get("/recommend/match")
def recommend(prompt_type: str, account_id: int | None = None, product_id: int | None = None,
              scene_id: int | None = None, scene: str = "", current_user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    # Keep this static route above every /{prompt_id}/... GET route. Otherwise
    # FastAPI may try to parse "recommend" as an integer prompt id and return 422.
    rows = list_prompts(
        prompt_type=prompt_type, account_id=account_id, status="active",
        current_user=current_user, db=db,
    )["items"]
    product_name, scene_name = _context_names(db, current_user.id, product_id, scene_id)
    # 产品/场景是推荐条件，不是权限或硬筛选条件；保留其他产品/场景的指令供用户手动选择。
    # 账号仍是适用范围，所以 list_prompts 继续按账号排除其他账号的专属指令。
    model_rows = {row.id: row for row in db.scalars(select(PromptTemplate).where(
        PromptTemplate.user_id == current_user.id,
        PromptTemplate.id.in_([item["id"] for item in rows]) if rows else False,
    )).all()}
    rows = [_match_item(model_rows[item["id"]], account_id=account_id, product_id=product_id,
                        scene_id=scene_id, scene=scene, product_name=product_name,
                        scene_name=scene_name) for item in rows]
    rows.sort(key=lambda item: (item["match_score"], item["updated_at"]), reverse=True)
    # 选择器需要保留全部可用指令；只返回前 10 条会让其余指令无法被手动选择。
    return {"item": rows[0] if rows else None, "candidates": rows}


@router.post("")
def create_prompt(payload: PromptPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _owned_account(db, current_user.id, payload.platform_account_id)
    _owned_product(db, current_user.id, payload.product_id)
    _owned_scene(db, current_user.id, payload.scene_id)
    row = PromptTemplate(user_id=current_user.id, name=payload.name, prompt_type=payload.prompt_type,
        platform_account_id=payload.platform_account_id, product_id=payload.product_id, scene_id=payload.scene_id,
        scenes_json=json.dumps(payload.scenes, ensure_ascii=False),
        tags_json=json.dumps(payload.tags, ensure_ascii=False), content=payload.content, status=payload.status,
        priority=payload.priority, source_hash=hashlib.sha256(payload.content.encode()).hexdigest())
    db.add(row); db.flush()
    db.add(PromptVersion(prompt_template_id=row.id, version=1, content=row.content, change_note=payload.change_note or "初始版本"))
    db.commit(); db.refresh(row)
    return _item(row)


@router.put("/{prompt_id}")
def update_prompt(prompt_id: int, payload: PromptPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.get(PromptTemplate, prompt_id)
    if not row or row.user_id != current_user.id: raise HTTPException(404, "指令不存在")
    _owned_account(db, current_user.id, payload.platform_account_id)
    _owned_product(db, current_user.id, payload.product_id)
    _owned_scene(db, current_user.id, payload.scene_id)
    changed = row.content != payload.content
    row.name, row.prompt_type, row.platform_account_id = payload.name, payload.prompt_type, payload.platform_account_id
    row.product_id, row.scene_id = payload.product_id, payload.scene_id
    # 客户改过的模板一律置为非官方，升级时不会被覆盖（总纲第八节）。
    if changed:
        row.is_official = False
    row.scenes_json, row.tags_json = json.dumps(payload.scenes, ensure_ascii=False), json.dumps(payload.tags, ensure_ascii=False)
    row.status, row.priority, row.updated_at = payload.status, payload.priority, shanghai_now()
    if changed:
        row.current_version += 1; row.content = payload.content
        row.source_hash = hashlib.sha256(payload.content.encode()).hexdigest()
        db.add(PromptVersion(prompt_template_id=row.id, version=row.current_version, content=row.content,
            change_note=payload.change_note or "人工更新"))
    db.commit(); db.refresh(row)
    return _item(row)


@router.get("/{prompt_id}/versions")
def versions(prompt_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.get(PromptTemplate, prompt_id)
    if not row or row.user_id != current_user.id: raise HTTPException(404, "指令不存在")
    items = db.scalars(select(PromptVersion).where(PromptVersion.prompt_template_id == row.id).order_by(PromptVersion.version.desc())).all()
    return {"current_version": row.current_version,
            "items": [{"id": x.id, "version": x.version, "content": x.content, "change_note": x.change_note,
                       "created_by": x.created_by, "approved": x.approved, "created_at": x.created_at,
                       "is_current": x.version == row.current_version} for x in items]}


@router.get("/{prompt_id}/versions/{version}")
def version_detail(prompt_id: int, version: int, current_user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    row = db.get(PromptTemplate, prompt_id)
    if not row or row.user_id != current_user.id:
        raise HTTPException(404, "指令不存在")
    item = db.scalar(select(PromptVersion).where(
        PromptVersion.prompt_template_id == row.id, PromptVersion.version == version,
    ))
    if item is None:
        raise HTTPException(404, "版本不存在")
    return {"id": item.id, "version": item.version, "content": item.content,
            "change_note": item.change_note, "created_by": item.created_by,
            "approved": item.approved, "created_at": item.created_at,
            "is_current": item.version == row.current_version}


@router.post("/{prompt_id}/versions/{version}/restore")
def restore_version(prompt_id: int, version: int, payload: RestoreVersionPayload | None = None,
                    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.get(PromptTemplate, prompt_id)
    if not row or row.user_id != current_user.id:
        raise HTTPException(404, "指令不存在")
    old = db.scalar(select(PromptVersion).where(
        PromptVersion.prompt_template_id == row.id, PromptVersion.version == version,
    ))
    if old is None:
        raise HTTPException(404, "版本不存在")
    new_version = row.current_version + 1
    row.current_version = new_version
    row.content = old.content
    row.source_hash = hashlib.sha256(old.content.encode()).hexdigest()
    row.is_official = False
    row.updated_at = shanghai_now()
    note = payload.change_note.strip() if payload and payload.change_note.strip() else f"恢复自 v{version}"
    db.add(PromptVersion(prompt_template_id=row.id, version=new_version, content=old.content,
                         change_note=note, created_by="human", approved=True))
    db.commit(); db.refresh(row)
    return {**_item(row), "restored_from_version": version}


@router.post("/{prompt_id}/archive")
def archive_prompt(prompt_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.get(PromptTemplate, prompt_id)
    if not row or row.user_id != current_user.id:
        raise HTTPException(404, "指令不存在")
    row.status = "archived"
    row.updated_at = shanghai_now()
    db.commit(); db.refresh(row)
    return _item(row)


@router.post("/{prompt_id}/activate")
def activate_prompt(prompt_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.get(PromptTemplate, prompt_id)
    if not row or row.user_id != current_user.id:
        raise HTTPException(404, "指令不存在")
    row.status = "active"
    row.updated_at = shanghai_now()
    db.commit(); db.refresh(row)
    return _item(row)


@router.delete("/{prompt_id}")
def delete_prompt(prompt_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Permanently delete only prompts that have never been used.

    Usage records are audit data and retain the exact version used. A used prompt
    must therefore be archived instead of deleting that history.
    """
    row = db.get(PromptTemplate, prompt_id)
    if not row or row.user_id != current_user.id:
        raise HTTPException(404, "指令不存在")
    usage_id = db.scalar(select(PromptUsage.id).where(PromptUsage.prompt_template_id == row.id).limit(1))
    if usage_id is not None:
        raise HTTPException(409, "该指令已有使用记录，不能永久删除，请改为归档")
    db.execute(delete(PromptVersion).where(PromptVersion.prompt_template_id == row.id))
    db.delete(row)
    db.commit()
    return {"id": prompt_id, "status": "deleted"}


@router.post("/usages")
def record_usage(payload: UsagePayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    prompt = db.get(PromptTemplate, payload.prompt_template_id)
    if not prompt or prompt.user_id != current_user.id: raise HTTPException(404, "指令不存在")
    _owned_account(db, current_user.id, payload.platform_account_id)
    row = PromptUsage(user_id=current_user.id, prompt_template_id=prompt.id, prompt_version=prompt.current_version,
        platform_account_id=payload.platform_account_id, module=payload.module, scene=payload.scene,
        target_type=payload.target_type, target_id=payload.target_id, outcome_json=json.dumps(payload.outcome, ensure_ascii=False))
    db.add(row); db.commit()
    return {"id": row.id, "status": "recorded"}


@router.get("/sync/obsidian/config")
def get_obsidian_config(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _config_item(_obsidian_config(db, current_user.id))


@router.put("/sync/obsidian/config")
def save_obsidian_config(payload: ObsidianConfigPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        vault = resolve_vault_path(payload.vault_path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    row = _obsidian_config(db, current_user.id)
    if row is None:
        row = ObsidianSyncConfig(user_id=current_user.id, vault_path=str(vault))
        db.add(row)
    else:
        row.vault_path = str(vault)
        row.updated_at = shanghai_now()
    db.commit(); db.refresh(row)
    return _config_item(row)


@router.post("/sync/obsidian/scan")
def scan_obsidian(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    config, vault = _configured_vault(db, current_user.id)
    scanned, errors = scan_vault(vault)
    refs = [item["source_ref"] for item in scanned]
    existing = {}
    if refs:
        rows = db.scalars(select(PromptTemplate).where(
            PromptTemplate.user_id == current_user.id,
            PromptTemplate.source_type == "obsidian",
            PromptTemplate.source_ref.in_(refs),
        )).all()
        existing = {row.source_ref: row for row in rows}
    items = []
    for item in scanned:
        row = existing.get(item["source_ref"])
        action = "new" if row is None else ("update" if _obsidian_needs_update(row, item) else "unchanged")
        items.append({
            **{key: value for key, value in item.items() if key not in {"content", "source_ref"}},
            "action": action,
            "existing_prompt_id": row.id if row else None,
            "current_version": row.current_version if row else 0,
            "content_preview": item["content"][:500],
            "content_length": len(item["content"]),
        })
    config.last_scanned_at = shanghai_now(); db.commit()
    return {"config": _config_item(config), "items": items, "errors": errors, "total": len(items)}


@router.post("/sync/obsidian/import")
def import_obsidian(payload: ObsidianImportPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    config, vault = _configured_vault(db, current_user.id)
    selected = list(dict.fromkeys(payload.relative_paths))
    parsed = []
    try:
        for relative_path in selected:
            path = resolve_selected_file(vault, relative_path)
            if path.stat().st_size > 1024 * 1024:
                raise ValueError(f"文件超过 1 MB：{relative_path}")
            item = parse_markdown(path, vault)
            if not item["content"]:
                raise ValueError(f"指令正文为空：{relative_path}")
            _owned_account(db, current_user.id, item["platform_account_id"])
            parsed.append(item)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
        raise HTTPException(400, str(exc)) from exc

    refs = [item["source_ref"] for item in parsed]
    rows = db.scalars(select(PromptTemplate).where(
        PromptTemplate.user_id == current_user.id,
        PromptTemplate.source_type == "obsidian",
        PromptTemplate.source_ref.in_(refs),
    )).all()
    existing = {row.source_ref: row for row in rows}
    changed_items = [item for item in parsed if item["source_ref"] not in existing or _obsidian_needs_update(existing[item["source_ref"]], item)]
    backup_id = None
    if changed_items:
        backup_id = create_backup("before_obsidian_prompt_sync", include_source=False)["id"]

    created = updated = unchanged = 0
    for item in parsed:
        row = existing.get(item["source_ref"])
        scenes_json = json.dumps(item["scenes"], ensure_ascii=False)
        tags_json = json.dumps(item["tags"], ensure_ascii=False)
        if row is None:
            row = PromptTemplate(
                user_id=current_user.id, name=item["name"], prompt_type=item["prompt_type"],
                platform_account_id=item["platform_account_id"], scenes_json=scenes_json, tags_json=tags_json,
                content=item["content"], status="active", priority=item["priority"], source_type="obsidian",
                source_ref=item["source_ref"], source_hash=item["source_hash"],
            )
            db.add(row); db.flush()
            db.add(PromptVersion(prompt_template_id=row.id, version=1, content=row.content,
                change_note=f"从 Obsidian 导入：{item['relative_path']}", created_by="obsidian", approved=True))
            created += 1
            continue
        metadata_changed = _obsidian_metadata_changed(row, item)
        row.name, row.prompt_type, row.platform_account_id = item["name"], item["prompt_type"], item["platform_account_id"]
        row.scenes_json, row.tags_json, row.priority = scenes_json, tags_json, item["priority"]
        row.status, row.updated_at = "active", shanghai_now()
        if row.source_hash == item["source_hash"]:
            if metadata_changed:
                updated += 1
                continue
            unchanged += 1
            continue
        row.current_version += 1
        row.content, row.source_hash = item["content"], item["source_hash"]
        db.add(PromptVersion(prompt_template_id=row.id, version=row.current_version, content=row.content,
            change_note=f"从 Obsidian 同步：{item['relative_path']}", created_by="obsidian", approved=True))
        updated += 1
    config.last_synced_at = shanghai_now(); db.commit()
    return {"created": created, "updated": updated, "unchanged": unchanged, "backup_id": backup_id}


@router.get("/sync/adapters")
def sync_adapters(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    config = _obsidian_config(db, current_user.id)
    status = "configured" if config and config.vault_path else "not_configured"
    return {"items": [{"type": "obsidian", "status": status, "capabilities": ["preview", "selective_import", "incremental_sync"],
                       "message": "已配置，可扫描并勾选同步" if status == "configured" else "配置仓库路径后可扫描、预览并勾选同步"}]}
