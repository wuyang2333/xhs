from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.api import accounts, admin_users, ai, assets, auth, auto_tasks, backups, drafts, files, keyword_groups, login_sessions, model_configs, notes, notifications, operations, prompt_library, publish, tags, tasks
from backend.app.api.platforms import registry
from backend.app.api.platforms.xhs import analytics, crawl, creator, monitoring, pc
from backend.app.core.config import get_settings
from backend.app.core.database import init_db, SessionLocal
from backend.app.services.scheduler_service import run_due_auto_tasks, shutdown_due_publish_scheduler, start_due_publish_scheduler


def _sync_model_configs() -> None:
    """启动时从 config/model_configs.json 导入缺失的模型配置。

    两台机器的数据库独立，用户在 Mac 上配的模型 Windows 看不到。
    把配置导出到 git 里的 JSON，启动时自动导入——按 name 去重，已存在的不覆盖。
    API Key 是加密的，两台机器用同一个 secret_key（config/default.yaml）所以密文通用。
    """
    config_path = Path("config/model_configs.json")
    if not config_path.is_file():
        return
    try:
        configs = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return
    from backend.app.models import ModelConfig

    db = SessionLocal()
    try:
        imported = 0
        for cfg in configs:
            existing = db.query(ModelConfig).filter(
                ModelConfig.name == cfg["name"],
                ModelConfig.user_id == 1,
            ).first()
            if existing:
                # 已存在的更新密钥和默认标记（用户可能在另一台机器上改过）
                existing.encrypted_api_key = cfg["encrypted_api_key"]
                # 配置文件中的地址是可迁移配置；已存在记录也要跟随更新，
                # 否则页面和实际运行时数据库会继续使用旧地址。
                if "base_url" in cfg:
                    existing.base_url = cfg.get("base_url", "")
                if cfg.get("is_default"):
                    db.query(ModelConfig).filter(
                        ModelConfig.user_id == 1,
                        ModelConfig.model_type == cfg["model_type"],
                    ).update({ModelConfig.is_default: False})
                    existing.is_default = True
                continue
            row = ModelConfig(
                user_id=1,
                name=cfg["name"],
                model_type=cfg["model_type"],
                provider=cfg.get("provider", ""),
                model_name=cfg.get("model_name", ""),
                base_url=cfg.get("base_url", ""),
                encrypted_api_key=cfg.get("encrypted_api_key", ""),
                is_default=cfg.get("is_default", False),
            )
            if row.is_default:
                db.query(ModelConfig).filter(
                    ModelConfig.user_id == 1,
                    ModelConfig.model_type == cfg["model_type"],
                ).update({ModelConfig.is_default: False})
            db.add(row)
            imported += 1
        db.commit()
        if imported:
            print(f"[模型同步] 从 config/model_configs.json 导入了 {imported} 条新配置")
    except Exception as exc:
        print(f"[模型同步] 失败（不阻断启动）: {exc}")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _sync_model_configs()
    settings = get_settings()
    scheduler = None
    if settings.scheduler_enabled:
        scheduler = start_due_publish_scheduler(settings.scheduler_interval_seconds)
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        shutdown_due_publish_scheduler(scheduler)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.api_title, lifespan=lifespan)

    origins = [origin.strip() for origin in settings.backend_cors_origins.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health", tags=["health"])
    def health() -> dict:
        return {"status": "ok", "service": "spider-xhs"}

    app.include_router(registry.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(accounts.router, prefix="/api")
    app.include_router(login_sessions.router, prefix="/api")
    app.include_router(notes.router, prefix="/api")
    app.include_router(files.router, prefix="/api")
    app.include_router(assets.router, prefix="/api")
    app.include_router(drafts.router, prefix="/api")
    app.include_router(ai.router, prefix="/api")
    app.include_router(tasks.router, prefix="/api")
    app.include_router(model_configs.router, prefix="/api")
    app.include_router(tags.router, prefix="/api")
    app.include_router(notifications.router, prefix="/api")
    app.include_router(keyword_groups.router, prefix="/api")
    app.include_router(publish.router, prefix="/api")
    app.include_router(analytics.router, prefix="/api")
    app.include_router(pc.router, prefix="/api")
    app.include_router(creator.router, prefix="/api")
    app.include_router(crawl.router, prefix="/api")
    app.include_router(monitoring.router, prefix="/api")
    app.include_router(auto_tasks.router, prefix="/api")
    app.include_router(admin_users.router, prefix="/api")
    app.include_router(operations.router, prefix="/api")
    app.include_router(prompt_library.router, prefix="/api")
    app.include_router(backups.router, prefix="/api")

    # Serve pre-built frontend in production / Docker
    if settings.frontend_serve_static:
        frontend_dist = Path(settings.frontend_build_dir)
        if frontend_dist.is_dir():
            from starlette.responses import FileResponse

            # Serve index.html for SPA client-side routing (non-API, non-file paths)
            @app.middleware("http")
            async def _spa_fallback(request, call_next):
                response = await call_next(request)
                path = request.url.path
                if (
                    response.status_code == 404
                    and not path.startswith("/api")
                    and "." not in path.split("/")[-1]
                ):
                    return FileResponse(str(frontend_dist / "index.html"))
                return response

            app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    return app


app = create_app()
