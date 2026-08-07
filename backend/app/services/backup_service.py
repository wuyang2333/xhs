from __future__ import annotations
import hashlib, json, sqlite3, tarfile
from datetime import datetime
from pathlib import Path
from backend.app.core.config import get_settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKUP_ROOT = PROJECT_ROOT / "data" / "backups"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""): digest.update(chunk)
    return digest.hexdigest()


def create_backup(reason: str = "manual", include_source: bool = True) -> dict:
    settings = get_settings()
    source_db = Path(settings.database_url.replace("sqlite:///", "")).resolve()
    if not source_db.exists(): raise FileNotFoundError(source_db)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    folder = BACKUP_ROOT / f"{stamp}-{reason.replace(' ', '_')[:40]}"
    folder.mkdir(parents=True, exist_ok=False)
    db_copy = folder / "spider_xhs.sqlite"
    with sqlite3.connect(f"file:{source_db}?mode=ro", uri=True) as src, sqlite3.connect(db_copy) as dst:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok": raise RuntimeError("备份数据库完整性检查失败")
    files = [{"name": db_copy.name, "sha256": _sha256(db_copy), "size": db_copy.stat().st_size}]
    if include_source:
        archive = folder / "project-source.tar.gz"
        excluded = {".git", "node_modules", ".venv", ".venv-py312", "__pycache__", "dist", "backups"}
        with tarfile.open(archive, "w:gz") as tar:
            for path in PROJECT_ROOT.rglob("*"):
                if any(part in excluded for part in path.parts) or path == source_db or not path.is_file(): continue
                tar.add(path, arcname=path.relative_to(PROJECT_ROOT), recursive=False)
        files.append({"name": archive.name, "sha256": _sha256(archive), "size": archive.stat().st_size})
    manifest = {"id": folder.name, "created_at": datetime.now().isoformat(), "reason": reason,
                "database_source": str(source_db), "files": files, "format_version": 1}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def list_backups() -> list[dict]:
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    result = []
    for manifest_path in sorted(BACKUP_ROOT.glob("*/manifest.json"), reverse=True):
        try:
            item = json.loads(manifest_path.read_text(encoding="utf-8"))
            item["valid"] = all((manifest_path.parent / f["name"]).exists() and _sha256(manifest_path.parent / f["name"]) == f["sha256"] for f in item["files"])
            result.append(item)
        except Exception:
            result.append({"id": manifest_path.parent.name, "valid": False, "reason": "manifest_error"})
    return result


def restore_backup(backup_id: str) -> dict:
    if "/" in backup_id or ".." in backup_id: raise ValueError("非法备份 ID")
    folder = BACKUP_ROOT / backup_id
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if not all((folder / f["name"]).exists() and _sha256(folder / f["name"]) == f["sha256"] for f in manifest["files"]):
        raise RuntimeError("备份校验失败，拒绝恢复")
    safety = create_backup("before_restore", include_source=True)
    source_db = folder / "spider_xhs.sqlite"
    target_db = Path(get_settings().database_url.replace("sqlite:///", "")).resolve()
    with sqlite3.connect(source_db) as src, sqlite3.connect(target_db) as dst:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok": raise RuntimeError("恢复后完整性检查失败")
    return {"restored": backup_id, "safety_backup": safety["id"]}
