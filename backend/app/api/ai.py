from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.deps import get_current_user
from backend.app.core.security import decrypt_text
from backend.app.models import AiDraft, AiGeneratedAsset, DraftAsset, ModelConfig, Task, User
from backend.app.schemas.common import paginated
from backend.app.services.ai_service import ImageAiClient, OpenAICompatibleImageClient, OpenAICompatibleTextClient, TextAiClient
from backend.app.services.media_asset_service import register_media_asset

router = APIRouter(prefix="/ai", tags=["ai"])


class RewriteNoteRequest(BaseModel):
    draft_id: int
    instruction: str = Field(default="", max_length=6000)
    # 用户从指令库选定或自填的指令，直接取代默认人设，不叠加。
    system_prompt_override: str = Field(default="", max_length=6000)


class GenerateNoteRequest(BaseModel):
    platform: Literal["xhs", "douyin", "kuaishou", "weibo", "xianyu", "taobao"] = "xhs"
    platform_account_id: int | None = None
    topic: str = Field(min_length=1, max_length=300)
    reference: str = Field(default="", max_length=4000)
    instruction: str = Field(default="", max_length=6000)
    generate_images: bool = True
    image_count: int = Field(default=1, ge=1, le=3)
    image_prompt: str = Field(default="", max_length=6000)
    existing_image_urls: list[str] = Field(default_factory=list, max_length=9)
    # 用户从指令库选定或自填的指令，直接取代默认人设，不叠加。
    system_prompt_override: str = Field(default="", max_length=6000)


class GenerateTitleRequest(BaseModel):
    title: str = Field(default="", max_length=300)
    body: str = Field(min_length=1, max_length=6000)
    count: int = Field(default=5, ge=1, le=10)
    instruction: str = Field(default="", max_length=6000)
    # 用户从指令库选定或自填的指令，直接取代默认人设，不叠加。
    system_prompt_override: str = Field(default="", max_length=6000)


class GenerateTagsRequest(BaseModel):
    title: str = Field(default="", max_length=300)
    body: str = Field(min_length=1, max_length=6000)
    count: int = Field(default=8, ge=1, le=20)
    # 用户从指令库选定或自填的指令，直接取代默认人设，不叠加。
    system_prompt_override: str = Field(default="", max_length=6000)


class PolishTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=6000)
    instruction: str = Field(default="", max_length=6000)
    # 用户从指令库选定或自填的指令，直接取代默认人设，不叠加。
    system_prompt_override: str = Field(default="", max_length=6000)


class GenerateCoverRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=6000)
    draft_id: Optional[int] = None
    size: str = Field(default="1024x1024", max_length=32)
    style: str = Field(default="clean", max_length=120)


class GenerateImageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=6000)
    model_config_id: int | None = Field(default=None, ge=1)
    reference_images: list[str] = Field(default_factory=list)
    size: str | None = Field(default=None, pattern=r"^\d{3,5}x\d{3,5}$", max_length=32)
    save_to_assets: bool = True


class DescribeImageRequest(BaseModel):
    image_url: str = Field(min_length=1, max_length=4000)
    instruction: str = Field(default="", max_length=6000)


def get_text_ai_client() -> TextAiClient:
    return OpenAICompatibleTextClient()


def get_image_ai_client() -> ImageAiClient:
    return OpenAICompatibleImageClient()


def _serialize_draft(draft: AiDraft) -> dict:
    return {
        "id": draft.id,
        "platform": draft.platform,
        "platform_account_id": draft.platform_account_id,
        "title": draft.title,
        "body": draft.body,
        "source_note_id": draft.source_note_id,
        "created_at": draft.created_at.isoformat(),
    }


def _get_default_text_model(db: Session, current_user: User) -> ModelConfig:
    config = db.scalars(
        select(ModelConfig).where(
            ModelConfig.user_id == current_user.id,
            ModelConfig.model_type == "text",
            ModelConfig.is_default.is_(True),
        )
    ).first()
    if config is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Default text model is not configured")
    return config


def _get_default_image_model(db: Session, current_user: User) -> ModelConfig:
    config = db.scalars(
        select(ModelConfig).where(
            ModelConfig.user_id == current_user.id,
            ModelConfig.model_type == "image",
            ModelConfig.is_default.is_(True),
        )
    ).first()
    if config is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Default image model is not configured")
    return config


def _text_model_context(db: Session, current_user: User) -> tuple[ModelConfig, str]:
    model_config = _get_default_text_model(db, current_user)
    api_key = decrypt_text(model_config.encrypted_api_key) if model_config.encrypted_api_key else ""
    return model_config, api_key


def _get_default_vision_model(db: Session, current_user: User) -> ModelConfig:
    config = db.scalars(
        select(ModelConfig).where(
            ModelConfig.user_id == current_user.id,
            ModelConfig.model_type == "vision",
            ModelConfig.is_default.is_(True),
        )
    ).first()
    if config is None:
        return _get_default_text_model(db, current_user)
    return config


def _vision_model_context(db: Session, current_user: User) -> tuple[ModelConfig, str]:
    model_config = _get_default_vision_model(db, current_user)
    api_key = decrypt_text(model_config.encrypted_api_key) if model_config.encrypted_api_key else ""
    return model_config, api_key


def _image_model_context(db: Session, current_user: User) -> tuple[ModelConfig, str]:
    model_config = _get_default_image_model(db, current_user)
    api_key = decrypt_text(model_config.encrypted_api_key) if model_config.encrypted_api_key else ""
    return model_config, api_key


def _selected_image_model_context(
    db: Session,
    current_user: User,
    model_config_id: int | None,
) -> tuple[ModelConfig, str]:
    if model_config_id is None:
        return _image_model_context(db, current_user)
    model_config = db.get(ModelConfig, model_config_id)
    if model_config is None or model_config.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model config not found")
    if model_config.model_type != "image":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected model is not an image model")
    api_key = decrypt_text(model_config.encrypted_api_key) if model_config.encrypted_api_key else ""
    return model_config, api_key


def _serialize_generated_asset(asset: AiGeneratedAsset) -> dict[str, Any]:
    return {
        "id": asset.id,
        "draft_id": asset.draft_id,
        "prompt": asset.prompt,
        "model_name": asset.model_name,
        "params": asset.params or {},
        "file_path": asset.file_path,
        "created_at": asset.created_at.isoformat(),
    }


def _register_ai_media_asset(
    db: Session,
    *,
    user_id: int,
    image_url: str,
    local_file_name: str,
    prompt: str,
) -> None:
    """把 AI 生成的图片同时登记进素材库（`media_assets`）。

    `ai_generated_assets` 仍然是 prompt / model_name / params 的落点，这里只是让同一张图
    在统一素材库里也可见，草稿选图器与发布选图器才能筛到它。source 固定 "ai"。

    `folder_id` 留空（未归类），由用户自行整理。登记失败不能影响生图主流程——图已经生成
    并落盘了，为了一条索引记录把整个请求判失败是更坏的结果，因此这里吞掉异常。
    """
    if not image_url and not local_file_name:
        return
    file_size = 0
    mime_type = ""
    if local_file_name:
        from pathlib import Path

        from backend.app.core.config import get_settings

        candidate = Path(get_settings().storage_dir) / "media" / local_file_name
        if candidate.is_file():
            file_size = candidate.stat().st_size
        mime_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }.get(Path(local_file_name).suffix.lower(), "")
    # SAVEPOINT：登记失败只回滚这一步，同一事务里已 add 的 AiGeneratedAsset / AiDraft 不受影响。
    # 直接 db.rollback() 会把它们一起丢掉，那比少一条素材库索引更糟。
    try:
        with db.begin_nested():
            register_media_asset(
                db,
                user_id=user_id,
                asset_type="image",
                url=image_url,
                local_path=local_file_name,
                # original_name 列限长 256，prompt 可以很长，这里截断只为列表可读。
                original_name=(prompt or "AI 生成图片").strip()[:120],
                mime_type=mime_type,
                file_size=file_size,
                source="ai",
            )
    except Exception:
        pass


def _remove_ai_watermark(file_path: Path) -> None:
    """Remove AI watermarks from a generated image.

    Tries remove_all (visible + invisible + metadata) first — needs GPU.
    Falls back to remove_visible (visible + metadata) — no GPU needed.
    Falls back to nothing if the tool isn't installed.
    Failures never block the image; the user gets the original if all else fails.
    """
    import sys

    # Try remove_all first (covers invisible watermarks via diffusion).
    # This needs torch + CUDA; if not installed or no GPU, it will fail and
    # we fall back to visible-only.
    try:
        from remove_ai_watermarks.api import remove_all
        try:
            remove_all(str(file_path), str(file_path))
            return
        except Exception as exc:
            print(f"[去水印] all 模式失败，降级到 visible：{exc}", file=sys.stderr, flush=True)
    except ImportError:
        pass  # all deps not installed, try visible

    # Fallback: visible marks + metadata only (no GPU needed).
    try:
        from remove_ai_watermarks.api import remove_visible
        try:
            remove_visible(str(file_path), str(file_path))
        except Exception as exc:
            print(f"[去水印] visible 模式失败，保留原图：{exc}", file=sys.stderr, flush=True)
    except ImportError:
        pass  # tool not installed at all, skip silently


def _persist_base64_image(result: dict[str, Any], current_user: User) -> tuple[str, str]:
    image_ref = result.get("url") or ""
    if not isinstance(image_ref, str) or not image_ref.startswith("data:image/"):
        return image_ref, ""

    import base64
    from pathlib import Path
    from uuid import uuid4

    from backend.app.core.config import get_settings

    try:
        header, encoded = image_ref.split(",", 1)
        mime_type = header.split(";", 1)[0].replace("data:", "")
        extension = {
            "image/jpeg": "jpg",
            "image/webp": "webp",
            "image/gif": "gif",
        }.get(mime_type, "png")
        file_name = f"xhs-image-u{current_user.id}-{uuid4().hex}.{extension}"
        media_dir = Path(get_settings().storage_dir) / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        saved_path = media_dir / file_name
        saved_path.write_bytes(base64.b64decode(encoded))
        # 去除 AI 暗水印（可见标记 + 元数据）。三处生图入口都走这里：
        # 草稿工坊、图片工坊、自动运营。失败不阻断，原图保留。
        _remove_ai_watermark(saved_path)
        return f"/api/files/media/{file_name}", file_name
    except Exception as exc:
        raise ValueError(f"图片已生成，但 Base64 文件保存失败: {exc}") from exc


def _recorded_text_task(
    *,
    db: Session,
    current_user: User,
    platform: str,
    task_type: str,
    payload: dict[str, Any],
    action: Callable[[], Any],
):
    task = Task(
        user_id=current_user.id,
        platform=platform,
        task_type=task_type,
        status="running",
        progress=10,
        payload=payload,
    )
    db.add(task)
    db.flush()
    try:
        result = action()
    except ValueError as exc:
        task.status = "failed"
        task.progress = 100
        task.payload = {**(task.payload or {}), "error": str(exc)}
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        task.status = "failed"
        task.progress = 100
        task.payload = {**(task.payload or {}), "error": str(exc)}
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"AI text generation failed: {exc}") from exc

    task.status = "completed"
    task.progress = 100
    return task, result


def _recorded_image_task(
    *,
    db: Session,
    current_user: User,
    task_type: str,
    payload: dict[str, Any],
    action: Callable[[], Any],
):
    task = Task(
        user_id=current_user.id,
        platform="xhs",
        task_type=task_type,
        status="running",
        progress=10,
        payload=payload,
    )
    db.add(task)
    db.flush()
    try:
        result = action()
    except ValueError as exc:
        task.status = "failed"
        task.progress = 100
        task.payload = {**(task.payload or {}), "error": str(exc)}
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        task.status = "failed"
        task.progress = 100
        task.payload = {**(task.payload or {}), "error": str(exc)}
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"AI image generation failed: {exc}") from exc

    task.status = "completed"
    task.progress = 100
    return task, result


@router.post("/rewrite-note")
def rewrite_note(
    payload: RewriteNoteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    text_ai_client: TextAiClient = Depends(get_text_ai_client),
):
    draft = db.get(AiDraft, payload.draft_id)
    if draft is None or draft.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")

    model_config, api_key = _text_model_context(db, current_user)
    task, rewritten_body = _recorded_text_task(
        db=db,
        current_user=current_user,
        platform=draft.platform,
        task_type="ai_rewrite",
        payload={"draft_id": draft.id, "model_config_id": model_config.id, "instruction": payload.instruction},
        action=lambda: text_ai_client.rewrite_note(
            model_config=model_config,
            api_key=api_key,
            title=draft.title,
            body=draft.body,
            instruction=payload.instruction,
            system_prompt_override=payload.system_prompt_override,
        ),
    )
    draft.body = rewritten_body
    task.payload = {**(task.payload or {}), "result_draft_id": draft.id, "result_length": len(rewritten_body)}
    db.commit()
    db.refresh(draft)
    return _serialize_draft(draft)


@router.post("/generate-note")
def generate_note(
    payload: GenerateNoteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    text_ai_client: TextAiClient = Depends(get_text_ai_client),
    image_ai_client: ImageAiClient = Depends(get_image_ai_client),
):
    model_config, api_key = _text_model_context(db, current_user)
    task, result = _recorded_text_task(
        db=db,
        current_user=current_user,
        platform=payload.platform,
        task_type="ai_generate_note",
        payload={"model_config_id": model_config.id, "topic": payload.topic},
        action=lambda: text_ai_client.generate_note(
            model_config=model_config,
            api_key=api_key,
            topic=payload.topic,
            reference=payload.reference,
            instruction=payload.instruction,
            system_prompt_override=payload.system_prompt_override,
        ),
    )

    generated_images: list[tuple[ModelConfig, dict[str, Any], str]] = []
    if payload.generate_images:
        image_model_config, image_api_key = _image_model_context(db, current_user)
        title = result.get("title") or payload.topic
        body = result.get("body") or ""
        image_prompt = payload.image_prompt.strip() or (
            "生成一张适合小红书图文笔记的竖版首图，画面真实自然、有生活感，"
            "主体清晰、构图简洁、留出标题排版空间，不添加水印和无法确认的品牌标识。"
            f"\n选题：{payload.topic}\n标题：{title}\n正文摘要：{body[:800]}"
        )
        for index in range(payload.image_count):
            prompt = image_prompt
            if payload.image_count > 1:
                prompt += f"\n这是第 {index + 1} 张配图，请与同组其他图片保持主题一致但构图不同。"
            image_task, image_result = _recorded_image_task(
                db=db,
                current_user=current_user,
                task_type="ai_generate_note_image",
                payload={
                    "model_config_id": image_model_config.id,
                    "topic": payload.topic,
                    "image_index": index,
                },
                action=lambda current_prompt=prompt: image_ai_client.generate_image(
                    model_config=image_model_config,
                    api_key=image_api_key,
                    prompt=current_prompt,
                ),
            )
            generated_images.append((image_model_config, image_result, prompt))
            image_task.payload = {
                **(image_task.payload or {}),
                "generated_url": image_result.get("url") or "",
            }

    draft = AiDraft(
        user_id=current_user.id,
        platform_account_id=payload.platform_account_id,
        platform=payload.platform,
        title=result.get("title") or payload.topic,
        body=result.get("body") or "",
    )
    db.add(draft)
    db.flush()

    sort_order = 0
    for image_model_config, image_result, prompt in generated_images:
        image_url, local_path = _persist_base64_image(image_result, current_user)
        generated_asset = AiGeneratedAsset(
            user_id=current_user.id,
            draft_id=draft.id,
            prompt=prompt,
            model_name=image_model_config.model_name,
            params={"source": "generate_note", "raw": image_result.get("raw")},
            file_path=image_url,
        )
        db.add(generated_asset)
        _register_ai_media_asset(
            db,
            user_id=current_user.id,
            image_url=image_url,
            local_file_name=local_path,
            prompt=prompt,
        )
        db.add(
            DraftAsset(
                draft_id=draft.id,
                asset_type="image",
                url=image_url,
                local_path=local_path,
                sort_order=sort_order,
            )
        )
        sort_order += 1

    for image_url in payload.existing_image_urls:
        db.add(
            DraftAsset(
                draft_id=draft.id,
                asset_type="image",
                url=image_url,
                local_path="",
                sort_order=sort_order,
            )
        )
        sort_order += 1

    task.payload = {
        **(task.payload or {}),
        "result_draft_id": draft.id,
        "generated_image_count": len(generated_images),
        "reused_image_count": len(payload.existing_image_urls),
    }
    db.commit()
    db.refresh(draft)
    return _serialize_draft(draft)


@router.post("/generate-title")
def generate_title(
    payload: GenerateTitleRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    text_ai_client: TextAiClient = Depends(get_text_ai_client),
):
    model_config, api_key = _text_model_context(db, current_user)
    task, items = _recorded_text_task(
        db=db,
        current_user=current_user,
        platform="xhs",
        task_type="ai_generate_title",
        payload={"model_config_id": model_config.id, "count": payload.count},
        action=lambda: text_ai_client.generate_titles(
            model_config=model_config,
            api_key=api_key,
            title=payload.title,
            body=payload.body,
            count=payload.count,
            instruction=payload.instruction,
            system_prompt_override=payload.system_prompt_override,
        ),
    )
    task.payload = {**(task.payload or {}), "result_count": len(items)}
    db.commit()
    return {"items": items}


@router.post("/generate-tags")
def generate_tags(
    payload: GenerateTagsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    text_ai_client: TextAiClient = Depends(get_text_ai_client),
):
    model_config, api_key = _text_model_context(db, current_user)
    task, items = _recorded_text_task(
        db=db,
        current_user=current_user,
        platform="xhs",
        task_type="ai_generate_tags",
        payload={"model_config_id": model_config.id, "count": payload.count},
        action=lambda: text_ai_client.generate_tags(
            model_config=model_config,
            api_key=api_key,
            title=payload.title,
            body=payload.body,
            count=payload.count,
            system_prompt_override=payload.system_prompt_override,
        ),
    )
    task.payload = {**(task.payload or {}), "result_count": len(items)}
    db.commit()
    return {"items": items}


@router.post("/polish-text")
def polish_text(
    payload: PolishTextRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    text_ai_client: TextAiClient = Depends(get_text_ai_client),
):
    model_config, api_key = _text_model_context(db, current_user)
    task, text = _recorded_text_task(
        db=db,
        current_user=current_user,
        platform="xhs",
        task_type="ai_polish_text",
        payload={"model_config_id": model_config.id, "instruction": payload.instruction},
        action=lambda: text_ai_client.polish_text(
            model_config=model_config,
            api_key=api_key,
            text=payload.text,
            instruction=payload.instruction,
            system_prompt_override=payload.system_prompt_override,
        ),
    )
    task.payload = {**(task.payload or {}), "result_length": len(text)}
    db.commit()
    return {"text": text}


@router.get("/images/assets")
def generated_image_assets(
    page: int = 1,
    page_size: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assets = db.scalars(
        select(AiGeneratedAsset)
        .where(AiGeneratedAsset.user_id == current_user.id)
        .order_by(AiGeneratedAsset.created_at.desc(), AiGeneratedAsset.id.desc())
    ).all()
    return paginated([_serialize_generated_asset(asset) for asset in assets], page, page_size)


@router.delete("/images/assets/{asset_id}")
def delete_generated_image_asset(
    asset_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    asset = db.get(AiGeneratedAsset, asset_id)
    if asset is None or asset.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    db.delete(asset)
    db.commit()
    return {"id": asset_id, "status": "deleted"}


@router.post("/images/generate-cover")
def generate_cover(
    payload: GenerateCoverRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    image_ai_client: ImageAiClient = Depends(get_image_ai_client),
):
    if payload.draft_id is not None:
        draft = db.get(AiDraft, payload.draft_id)
        if draft is None or draft.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")

    model_config, api_key = _image_model_context(db, current_user)
    task, result = _recorded_image_task(
        db=db,
        current_user=current_user,
        task_type="ai_image_generate_cover",
        payload={"model_config_id": model_config.id, "prompt": payload.prompt, "size": payload.size, "style": payload.style},
        action=lambda: image_ai_client.generate_cover(
            model_config=model_config,
            api_key=api_key,
            prompt=payload.prompt,
            size=payload.size,
            style=payload.style,
        ),
    )
    image_url, local_path = _persist_base64_image(result, current_user)
    asset = AiGeneratedAsset(
        user_id=current_user.id,
        draft_id=payload.draft_id,
        prompt=payload.prompt,
        model_name=model_config.model_name,
        params={"size": payload.size, "style": payload.style, "raw": result.get("raw")},
        file_path=image_url,
    )
    db.add(asset)
    _register_ai_media_asset(
        db,
        user_id=current_user.id,
        image_url=image_url,
        local_file_name=local_path,
        prompt=payload.prompt,
    )
    db.flush()
    task.payload = {**(task.payload or {}), "asset_id": asset.id}
    db.commit()
    db.refresh(asset)
    return _serialize_generated_asset(asset)


@router.post("/images/generate")
def generate_image(
    payload: GenerateImageRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    image_ai_client: ImageAiClient = Depends(get_image_ai_client),
):
    model_config, api_key = _selected_image_model_context(db, current_user, payload.model_config_id)
    generation_kwargs: dict[str, Any] = {
        "model_config": model_config,
        "api_key": api_key,
        "prompt": payload.prompt,
        "reference_images": payload.reference_images or None,
    }
    # Keep omitted size compatible with providers and older test/dummy clients;
    # the image client supplies its own provider default in that case.
    if payload.size:
        generation_kwargs["size"] = payload.size
    task, result = _recorded_image_task(
        db=db,
        current_user=current_user,
        task_type="ai_image_generate",
        payload={"model_config_id": model_config.id, "prompt": payload.prompt, "reference_images": payload.reference_images, "size": payload.size},
        action=lambda: image_ai_client.generate_image(**generation_kwargs),
    )
    image_url, local_path = _persist_base64_image(result, current_user)
    response_data: dict = {"url": image_url, "raw": result.get("raw")}
    if payload.save_to_assets:
        asset = AiGeneratedAsset(
            user_id=current_user.id,
            prompt=payload.prompt,
            model_name=model_config.model_name,
            params={"reference_images": payload.reference_images, "size": payload.size, "raw": result.get("raw")},
            file_path=image_url,
        )
        db.add(asset)
        # 只在用户选择存图时登记素材库，与 AiGeneratedAsset 的落库条件保持一致。
        _register_ai_media_asset(
            db,
            user_id=current_user.id,
            image_url=image_url,
            local_file_name=local_path,
            prompt=payload.prompt,
        )
        db.flush()
        task.payload = {**(task.payload or {}), "asset_id": asset.id}
        db.commit()
        db.refresh(asset)
        response_data["asset"] = _serialize_generated_asset(asset)
    else:
        db.commit()
    return response_data


@router.post("/images/describe")
def describe_image(
    payload: DescribeImageRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    image_ai_client: ImageAiClient = Depends(get_image_ai_client),
):
    model_config, api_key = _vision_model_context(db, current_user)
    task, text = _recorded_image_task(
        db=db,
        current_user=current_user,
        task_type="ai_image_describe",
        payload={"model_config_id": model_config.id, "image_url": payload.image_url, "instruction": payload.instruction},
        action=lambda: image_ai_client.describe_image(
            model_config=model_config,
            api_key=api_key,
            image_url=payload.image_url,
            instruction=payload.instruction,
        ),
    )
    task.payload = {**(task.payload or {}), "result_length": len(text)}
    db.commit()
    return {"text": text}
