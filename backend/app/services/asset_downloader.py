from __future__ import annotations

import io
from pathlib import Path
from uuid import uuid4

from loguru import logger

from backend.app.core.config import get_settings


def fetch_asset_content(url: str, asset_type: str) -> tuple[bytes, str]:
    if not url or not url.startswith(("http://", "https://")):
        raise ValueError("Unsupported asset URL")

    import requests

    response = requests.get(url, timeout=30, headers={"Referer": ""})
    response.raise_for_status()
    content = response.content
    if len(content) < 100:
        raise ValueError("Downloaded asset is empty")
    extension = _guess_extension(url, response.headers.get("content-type", ""), asset_type)
    return content, extension


def convert_image_content_to_jpeg(content: bytes, quality: int = 95) -> bytes:
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(content)) as source:
        image = ImageOps.exif_transpose(source)
        if image.mode in {"RGBA", "LA"} or "transparency" in source.info:
            rgba = image.convert("RGBA")
            converted = Image.new("RGB", rgba.size, "white")
            converted.paste(rgba, mask=rgba.getchannel("A"))
        else:
            converted = image.convert("RGB")
        output = io.BytesIO()
        converted.save(output, format="JPEG", quality=quality, optimize=True, subsampling=0)
        return output.getvalue()


def download_asset_to_local(url: str, user_id: int, asset_type: str) -> str | None:
    if not url or not url.startswith(("http://", "https://")):
        return None
    try:
        content, ext = fetch_asset_content(url, asset_type)
        file_name = f"xhs-asset-u{user_id}-{uuid4().hex}{ext}"
        media_dir = Path(get_settings().storage_dir) / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        (media_dir / file_name).write_bytes(content)
        return file_name
    except Exception as exc:
        logger.warning(f"Asset download failed for {url[:80]}: {exc}")
        return None


def _guess_extension(url: str, content_type: str, asset_type: str) -> str:
    ct = content_type.lower()
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "png" in ct:
        return ".png"
    if "gif" in ct:
        return ".gif"
    if "webp" in ct:
        return ".webp"
    if "mp4" in ct:
        return ".mp4"
    if "quicktime" in ct or "mov" in ct:
        return ".mov"
    lower_url = url.lower().split("?")[0]
    for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov"):
        if lower_url.endswith(ext):
            return ext
    return ".mp4" if asset_type == "video" else ".jpg"
