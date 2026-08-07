from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml


PROMPT_TYPES = {"image", "note", "title", "rewrite", "auto_ops"}
PROMPT_TYPE_ALIASES = {
    "生图": "image", "图片": "image", "生文": "note", "正文": "note", "笔记": "note",
    "标题": "title", "改写": "rewrite", "自动运营": "auto_ops", "自动化": "auto_ops",
}
MAX_MARKDOWN_BYTES = 1024 * 1024
MAX_FILES = 2000


def resolve_vault_path(raw_path: str) -> Path:
    if not raw_path.strip():
        raise ValueError("请先填写 Obsidian 仓库路径")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise ValueError("Obsidian 仓库路径必须是绝对路径")
    path = path.resolve()
    if path == Path(path.anchor):
        raise ValueError("不能把磁盘根目录作为 Obsidian 仓库")
    if not path.is_dir():
        raise ValueError("Obsidian 仓库路径不存在或不是文件夹")
    return path


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).replace("，", ",").split(",") if item.strip()]


def parse_markdown(path: Path, vault: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8-sig")
    metadata: dict[str, Any] = {}
    content = raw.strip()
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) == 3:
            loaded = yaml.safe_load(parts[1]) or {}
            if isinstance(loaded, dict):
                metadata = loaded
                content = parts[2].strip()

    relative_path = path.relative_to(vault).as_posix()
    raw_type = str(metadata.get("prompt_type") or metadata.get("type") or "note").strip()
    prompt_type = PROMPT_TYPE_ALIASES.get(raw_type, raw_type)
    if prompt_type not in PROMPT_TYPES:
        prompt_type = "note"
    name = str(metadata.get("name") or metadata.get("title") or path.stem).strip() or path.stem
    account_id = metadata.get("platform_account_id") or metadata.get("account_id")
    try:
        account_id = int(account_id) if account_id not in (None, "") else None
    except (TypeError, ValueError):
        account_id = None
    try:
        priority = max(-100, min(100, int(metadata.get("priority") or 0)))
    except (TypeError, ValueError):
        priority = 0
    source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return {
        "relative_path": relative_path,
        "name": name,
        "prompt_type": prompt_type,
        "platform_account_id": account_id,
        "scenes": _as_list(metadata.get("scenes") or metadata.get("scene")),
        "tags": _as_list(metadata.get("tags")),
        "priority": priority,
        "content": content,
        "source_ref": f"obsidian:{relative_path}",
        "source_hash": source_hash,
        "modified_at": path.stat().st_mtime,
    }


def scan_vault(vault: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    candidates = sorted(
        path for path in vault.rglob("*.md")
        if ".obsidian" not in path.parts and not any(part.startswith(".") for part in path.relative_to(vault).parts)
    )[:MAX_FILES]
    for path in candidates:
        relative = path.relative_to(vault).as_posix()
        try:
            if path.is_symlink() or not path.is_file():
                continue
            if path.stat().st_size > MAX_MARKDOWN_BYTES:
                errors.append({"relative_path": relative, "message": "文件超过 1 MB，已跳过"})
                continue
            item = parse_markdown(path, vault)
            if not item["content"]:
                errors.append({"relative_path": relative, "message": "正文为空，已跳过"})
                continue
            items.append(item)
        except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
            errors.append({"relative_path": relative, "message": str(exc)})
    return items, errors


def resolve_selected_file(vault: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".md":
        raise ValueError(f"非法 Obsidian 文件路径：{relative_path}")
    candidate = (vault / relative).resolve()
    try:
        candidate.relative_to(vault)
    except ValueError as exc:
        raise ValueError(f"文件不在 Obsidian 仓库中：{relative_path}") from exc
    if not candidate.is_file() or candidate.is_symlink():
        raise ValueError(f"Obsidian 文件不存在：{relative_path}")
    return candidate
