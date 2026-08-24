import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

from backend.app.main import app


client = TestClient(app)


def _override_database(tmp_path):
    from backend.app.core.database import Base, get_db

    engine = create_engine(
        f"sqlite:///{tmp_path / 'stage-a-assets-test.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
    )
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return get_db


def _register_and_get_access_token(username):
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "secret123"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _create_folder(token, name):
    response = client.post(
        "/api/assets/folders",
        headers=_headers(token),
        json={"name": name},
    )
    assert response.status_code == 200
    return response.json()


def _create_media_asset(token, name, folder_id=None):
    response = client.post(
        "/api/assets",
        headers=_headers(token),
        json={
            "asset_type": "image",
            "url": f"https://example.test/{name}.png",
            "original_name": f"{name}.png",
            "folder_id": folder_id,
        },
    )
    assert response.status_code == 200
    return response.json()


def _create_draft(token, title="阶段A草稿"):
    response = client.post(
        "/api/drafts",
        headers=_headers(token),
        json={"platform": "xhs", "title": title, "body": "阶段 A 测试正文"},
    )
    assert response.status_code == 200
    return response.json()


def _add_media_asset_to_draft(token, draft_id, media_asset_id):
    response = client.post(
        f"/api/drafts/{draft_id}/assets",
        headers=_headers(token),
        json={"media_asset_id": media_asset_id},
    )
    assert response.status_code == 200
    return response.json()


@pytest.fixture
def isolated_api(tmp_path):
    db_dependency = _override_database(tmp_path)
    try:
        yield
    finally:
        app.dependency_overrides.pop(db_dependency, None)


def test_alembic_upgrade_from_empty_sqlite_creates_stage_a_schema(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'stage-a-alembic.db'}"
    config_path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "backend", "alembic.ini")
    )
    config = Config(config_path)
    config.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(config, "head")

    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    schema = inspect(engine)
    table_names = set(schema.get_table_names())
    assert {"asset_folders", "media_assets", "media_asset_tags"}.issubset(table_names)

    assert {"cover_asset_id"}.issubset(
        {column["name"] for column in schema.get_columns("asset_folders")}
    )
    assert {
        "user_id",
        "folder_id",
        "product_id",
        "scene_id",
        "sort_order",
        "last_used_at",
    }.issubset({column["name"] for column in schema.get_columns("media_assets")})
    assert {"media_asset_id", "is_cover"}.issubset(
        {column["name"] for column in schema.get_columns("draft_assets")}
    )
    assert {"sort_order", "source_draft_asset_id"}.issubset(
        {column["name"] for column in schema.get_columns("publish_assets")}
    )
    engine.dispose()


def test_media_assets_and_folders_are_isolated_between_users(isolated_api):
    owner_token = _register_and_get_access_token("stage-a-owner")
    other_token = _register_and_get_access_token("stage-a-other")

    owner_folder = _create_folder(owner_token, "owner-folder")
    owner_asset = _create_media_asset(owner_token, "owner-asset", owner_folder["id"])

    other_folders = client.get("/api/assets/folders", headers=_headers(other_token))
    other_assets = client.get("/api/assets", headers=_headers(other_token))
    assert other_folders.status_code == 200
    assert other_folders.json()["items"] == []
    assert other_assets.status_code == 200
    assert other_assets.json()["items"] == []

    cross_folder_response = client.post(
        "/api/assets",
        headers=_headers(other_token),
        json={
            "asset_type": "image",
            "url": "https://example.test/cross-owner-folder.png",
            "folder_id": owner_folder["id"],
        },
    )
    assert cross_folder_response.status_code == 404

    other_folder = _create_folder(other_token, "other-folder")
    other_asset = _create_media_asset(other_token, "other-asset", other_folder["id"])

    owner_folders = client.get("/api/assets/folders", headers=_headers(owner_token))
    owner_assets = client.get("/api/assets", headers=_headers(owner_token))
    assert [item["id"] for item in owner_folders.json()["items"]] == [owner_folder["id"]]
    assert [item["id"] for item in owner_assets.json()["items"]] == [owner_asset["id"]]
    assert other_asset["id"] not in {item["id"] for item in owner_assets.json()["items"]}


def test_nonempty_folder_delete_returns_conflict(isolated_api):
    token = _register_and_get_access_token("stage-a-folder-owner")
    folder = _create_folder(token, "nonempty-folder")
    _create_media_asset(token, "folder-asset", folder["id"])

    response = client.delete(
        f"/api/assets/folders/{folder['id']}",
        headers=_headers(token),
    )

    assert response.status_code == 409


def test_same_media_asset_cannot_be_added_to_same_draft_twice(isolated_api):
    token = _register_and_get_access_token("stage-a-duplicate-owner")
    media_asset = _create_media_asset(token, "duplicate-asset")
    draft = _create_draft(token)
    _add_media_asset_to_draft(token, draft["id"], media_asset["id"])

    duplicate_response = client.post(
        f"/api/drafts/{draft['id']}/assets",
        headers=_headers(token),
        json={"media_asset_id": media_asset["id"]},
    )

    assert duplicate_response.status_code == 409


def test_draft_asset_reorder_validates_exact_complete_set(isolated_api):
    token = _register_and_get_access_token("stage-a-reorder-owner")
    draft = _create_draft(token)
    draft_assets = [
        _add_media_asset_to_draft(
            token,
            draft["id"],
            _create_media_asset(token, f"reorder-{index}")["id"],
        )
        for index in range(3)
    ]
    asset_ids = [asset["id"] for asset in draft_assets]
    reorder_url = f"/api/drafts/{draft['id']}/assets/reorder"

    duplicate_response = client.put(
        reorder_url,
        headers=_headers(token),
        json={"asset_ids": [asset_ids[0], asset_ids[0], asset_ids[2]]},
    )
    missing_response = client.put(
        reorder_url,
        headers=_headers(token),
        json={"asset_ids": asset_ids[:2]},
    )
    unknown_response = client.put(
        reorder_url,
        headers=_headers(token),
        json={"asset_ids": [asset_ids[0], asset_ids[1], 999999]},
    )

    assert duplicate_response.status_code == 400
    assert missing_response.status_code == 400
    assert unknown_response.status_code == 400

    expected_order = list(reversed(asset_ids))
    success_response = client.put(
        reorder_url,
        headers=_headers(token),
        json={"asset_ids": expected_order},
    )
    assert success_response.status_code == 200

    list_response = client.get(
        f"/api/drafts/{draft['id']}/assets",
        headers=_headers(token),
    )
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()["items"]] == expected_order
    assert [item["sort_order"] for item in list_response.json()["items"]] == [0, 1, 2]


def test_setting_cover_leaves_exactly_one_cover_in_draft(isolated_api):
    token = _register_and_get_access_token("stage-a-cover-owner")
    draft = _create_draft(token)
    first = _add_media_asset_to_draft(
        token,
        draft["id"],
        _create_media_asset(token, "cover-first")["id"],
    )
    second = _add_media_asset_to_draft(
        token,
        draft["id"],
        _create_media_asset(token, "cover-second")["id"],
    )

    first_response = client.put(
        f"/api/drafts/{draft['id']}/assets/{first['id']}/cover",
        headers=_headers(token),
    )
    second_response = client.put(
        f"/api/drafts/{draft['id']}/assets/{second['id']}/cover",
        headers=_headers(token),
    )
    assert first_response.status_code == 200
    assert second_response.status_code == 200

    assets_response = client.get(
        f"/api/drafts/{draft['id']}/assets",
        headers=_headers(token),
    )
    covers = [item for item in assets_response.json()["items"] if item["is_cover"]]
    assert len(covers) == 1
    assert covers[0]["id"] == second["id"]


def test_send_draft_to_publish_preserves_asset_order_and_source_ids(isolated_api):
    token = _register_and_get_access_token("stage-a-publish-owner")
    draft = _create_draft(token, title="发布中心草稿")
    draft_assets = [
        _add_media_asset_to_draft(
            token,
            draft["id"],
            _create_media_asset(token, f"publish-{index}")["id"],
        )
        for index in range(3)
    ]
    expected_ids = [draft_assets[2]["id"], draft_assets[0]["id"], draft_assets[1]["id"]]
    reorder_response = client.put(
        f"/api/drafts/{draft['id']}/assets/reorder",
        headers=_headers(token),
        json={"asset_ids": expected_ids},
    )
    assert reorder_response.status_code == 200

    create_job_response = client.post(
        f"/api/drafts/{draft['id']}/send-to-publish",
        headers=_headers(token),
        json={"publish_mode": "immediate"},
    )
    assert create_job_response.status_code == 200
    job_id = create_job_response.json()["id"]

    get_job_response = client.get(
        f"/api/publish/jobs/{job_id}",
        headers=_headers(token),
    )
    assets_response = client.get(
        f"/api/publish/jobs/{job_id}/assets",
        headers=_headers(token),
    )
    assert get_job_response.status_code == 200
    assert get_job_response.json()["source_draft_id"] == draft["id"]
    assert assets_response.status_code == 200

    publish_assets = assets_response.json()["items"]
    assert [item["sort_order"] for item in publish_assets] == [0, 1, 2]
    assert [item["source_draft_asset_id"] for item in publish_assets] == expected_ids


def _upload_with_path(token, name, folder_path):
    response = client.post(
        "/api/assets",
        headers=_headers(token),
        json={
            "asset_type": "image",
            "url": f"https://example.test/{name}.png",
            "original_name": f"{name}.png",
            "folder_path": folder_path,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _folders_by_name(token):
    response = client.get("/api/assets/folders", headers=_headers(token))
    assert response.status_code == 200
    return {item["name"]: item for item in response.json()["items"]}


def test_folder_path_upload_creates_missing_levels(isolated_api):
    token = _register_and_get_access_token("stage-a-path-create")

    asset = _upload_with_path(token, "bidet-white", "坐浴盆/白底产品图")

    folders = _folders_by_name(token)
    assert set(folders) == {"坐浴盆", "白底产品图"}
    assert folders["坐浴盆"]["parent_id"] is None
    assert folders["白底产品图"]["parent_id"] == folders["坐浴盆"]["id"]
    assert asset["folder_id"] == folders["白底产品图"]["id"]


def test_folder_path_upload_reuses_existing_folders(isolated_api):
    token = _register_and_get_access_token("stage-a-path-reuse")

    first = _upload_with_path(token, "bidet-bathroom-1", "坐浴盆/卫生间场景")
    second = _upload_with_path(token, "bidet-bathroom-2", "坐浴盆/卫生间场景")
    third = _upload_with_path(token, "bidet-pregnancy", "坐浴盆/孕期场景")

    folders = _folders_by_name(token)
    assert set(folders) == {"坐浴盆", "卫生间场景", "孕期场景"}
    assert first["folder_id"] == second["folder_id"]
    assert third["folder_id"] != first["folder_id"]
    assert folders["卫生间场景"]["parent_id"] == folders["坐浴盆"]["id"]
    assert folders["孕期场景"]["parent_id"] == folders["坐浴盆"]["id"]


def test_explicit_folder_id_takes_precedence_over_folder_path(isolated_api):
    token = _register_and_get_access_token("stage-a-path-precedence")

    explicit = _create_folder(token, "explicit-folder")
    response = client.post(
        "/api/assets",
        headers=_headers(token),
        json={
            "asset_type": "image",
            "url": "https://example.test/precedence.png",
            "folder_id": explicit["id"],
            "folder_path": "护腰/久坐办公",
        },
    )
    assert response.status_code == 200
    assert response.json()["folder_id"] == explicit["id"]
    assert set(_folders_by_name(token)) == {"explicit-folder"}


def test_folder_paths_are_isolated_between_users(isolated_api):
    owner_token = _register_and_get_access_token("stage-a-path-owner")
    other_token = _register_and_get_access_token("stage-a-path-other")

    owner_asset = _upload_with_path(owner_token, "owner-waist", "护腰/开车")
    other_asset = _upload_with_path(other_token, "other-waist", "护腰/开车")

    assert owner_asset["folder_id"] != other_asset["folder_id"]
    assert set(_folders_by_name(owner_token)) == {"护腰", "开车"}
    assert set(_folders_by_name(other_token)) == {"护腰", "开车"}


def test_tests_never_write_into_the_real_storage_directory():
    """守住 BUG-09：storage_dir 曾写死为 backend/app/storage，导致每次全量测试
    往用户真实媒体目录泄漏 2 个图片文件。tests/conftest.py 现将其指向临时目录。"""
    from backend.app.core.config import get_settings

    storage_dir = get_settings().storage_dir.resolve()
    real_storage = (Path(__file__).resolve().parents[2] / "backend" / "app" / "storage").resolve()
    assert storage_dir != real_storage, (
        f"测试的 storage_dir 指向了真实媒体目录 {storage_dir}，"
        "会污染用户素材。检查 tests/conftest.py 是否被跳过。"
    )


def test_new_uploads_are_appended_after_reordered_assets(isolated_api):
    token = _register_and_get_access_token("stage-a-append-order")

    first = _create_media_asset(token, "append-first")
    second = _create_media_asset(token, "append-second")

    reorder = client.put(
        "/api/assets/reorder",
        headers=_headers(token),
        json={"asset_ids": [second["id"], first["id"]]},
    )
    assert reorder.status_code == 200

    third = _create_media_asset(token, "append-third")

    listing = client.get("/api/assets", headers=_headers(token))
    assert listing.status_code == 200
    ordered_ids = [item["id"] for item in listing.json()["items"]]
    # 新上传的必须在末尾，不能因为 sort_order 默认 0 而插到最前面
    assert ordered_ids == [second["id"], first["id"], third["id"]]


def test_folder_path_rejects_excessive_depth(isolated_api):
    token = _register_and_get_access_token("stage-a-path-depth")

    response = client.post(
        "/api/assets",
        headers=_headers(token),
        json={
            "asset_type": "image",
            "url": "https://example.test/too-deep.png",
            "folder_path": "/".join(f"level{index}" for index in range(9)),
        },
    )
    assert response.status_code == 400
    assert _folders_by_name(token) == {}


# --- A-06：三套图片存储合流到 media_assets ---


class _FakeImageAiClient:
    """够用的替身：只覆盖 A-06 需要的两条生图路径，不发真实请求。"""

    def generate_cover(self, *, model_config, api_key, prompt, size, style):
        return {"url": "https://cdn.example.test/ai-cover.png", "raw": {"seed": 1}}

    def generate_image(self, *, model_config, api_key, prompt, reference_images=None):
        return {"url": "https://cdn.example.test/ai-image.png", "raw": {"seed": 2}}


def _create_default_image_model(token):
    response = client.post(
        "/api/model-configs",
        headers=_headers(token),
        json={
            "name": "Default Image",
            "model_type": "image",
            "provider": "openai-compatible",
            "model_name": "image-generate-test",
            "base_url": "https://api.example.test/v1",
            "api_key": "sk-image-secret",
            "is_default": True,
        },
    )
    assert response.status_code == 200
    return response.json()


def _media_assets(token):
    response = client.get("/api/assets", headers=_headers(token))
    assert response.status_code == 200
    return response.json()["items"]


def test_ai_generated_cover_is_also_registered_in_media_assets_with_ai_source(isolated_api):
    from backend.app.api.ai import get_image_ai_client

    token = _register_and_get_access_token("stage-a-ai-cover")
    app.dependency_overrides[get_image_ai_client] = lambda: _FakeImageAiClient()
    try:
        _create_default_image_model(token)
        assert _media_assets(token) == []

        response = client.post(
            "/api/ai/images/generate-cover",
            headers=_headers(token),
            json={"prompt": "低卡早餐封面", "size": "1024x1024", "style": "clean"},
        )
        assert response.status_code == 200

        assets = _media_assets(token)
        assert len(assets) == 1
        assert assets[0]["source"] == "ai"
        assert assets[0]["url"] == "https://cdn.example.test/ai-cover.png"
        assert assets[0]["asset_type"] == "image"
        # 未归类，等用户自己整理
        assert assets[0]["folder_id"] is None

        # ai_generated_assets 列表功能必须仍然正常（元数据不能丢）
        ai_list = client.get("/api/ai/images/assets", headers=_headers(token))
        assert ai_list.status_code == 200
        assert ai_list.json()["total"] == 1
        assert ai_list.json()["items"][0]["prompt"] == "低卡早餐封面"
    finally:
        app.dependency_overrides.pop(get_image_ai_client, None)


def test_repeated_ai_generation_of_same_image_url_keeps_single_media_asset(isolated_api):
    """替身每次返回同一个 URL，素材库应按 url 判重，不能堆出多条。"""
    from backend.app.api.ai import get_image_ai_client

    token = _register_and_get_access_token("stage-a-ai-dedupe")
    app.dependency_overrides[get_image_ai_client] = lambda: _FakeImageAiClient()
    try:
        _create_default_image_model(token)
        for _ in range(3):
            response = client.post(
                "/api/ai/images/generate-cover",
                headers=_headers(token),
                json={"prompt": "重复生成", "size": "1024x1024", "style": "clean"},
            )
            assert response.status_code == 200

        assert len(_media_assets(token)) == 1
        # 元数据表仍逐次记录，三次生成三条
        assert client.get("/api/ai/images/assets", headers=_headers(token)).json()["total"] == 3
    finally:
        app.dependency_overrides.pop(get_image_ai_client, None)


def test_ai_image_generation_without_save_to_assets_creates_no_media_asset(isolated_api):
    from backend.app.api.ai import get_image_ai_client

    token = _register_and_get_access_token("stage-a-ai-nosave")
    app.dependency_overrides[get_image_ai_client] = lambda: _FakeImageAiClient()
    try:
        _create_default_image_model(token)
        response = client.post(
            "/api/ai/images/generate",
            headers=_headers(token),
            json={"prompt": "不入库", "save_to_assets": False},
        )
        assert response.status_code == 200
        assert _media_assets(token) == []
    finally:
        app.dependency_overrides.pop(get_image_ai_client, None)


def test_ai_image_generation_uses_selected_image_model(isolated_api):
    from backend.app.api.ai import get_image_ai_client

    class RecordingImageClient:
        selected_model = ""

        def generate_image(self, *, model_config, api_key, prompt, reference_images=None):
            self.selected_model = model_config.model_name
            return {"url": "https://cdn.example.test/selected-model.png", "raw": {}}

    token = _register_and_get_access_token("stage-a-ai-selected-model")
    fake_client = RecordingImageClient()
    app.dependency_overrides[get_image_ai_client] = lambda: fake_client
    try:
        _create_default_image_model(token)
        selected = client.post(
            "/api/model-configs",
            headers=_headers(token),
            json={
                "name": "Selected Image",
                "model_type": "image",
                "provider": "openai-compatible",
                "model_name": "image-selected-test",
                "base_url": "https://api.example.test/v1",
                "api_key": "sk-image-selected",
                "is_default": False,
            },
        )
        assert selected.status_code == 200

        response = client.post(
            "/api/ai/images/generate",
            headers=_headers(token),
            json={
                "prompt": "使用本次选择的模型",
                "model_config_id": selected.json()["id"],
                "save_to_assets": False,
            },
        )

        assert response.status_code == 200
        assert fake_client.selected_model == "image-selected-test"
    finally:
        app.dependency_overrides.pop(get_image_ai_client, None)


def _backfill_session(tmp_path):
    """给回填脚本一个独立库：脚本函数都接收 db，测试直接驱动，不碰真实库。"""
    from backend.app.core.database import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'backfill-test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(autocommit=False, autoflush=False, bind=engine)()


def test_backfill_media_assets_is_idempotent_across_repeated_runs(tmp_path, monkeypatch):
    from backend.app.models import AiGeneratedAsset, MediaAsset, User
    from scripts import backfill_media_assets as backfill

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    monkeypatch.setattr(backfill, "_media_dir", lambda: media_dir)

    engine, db = _backfill_session(tmp_path)
    try:
        user = User(username="backfill-owner", password_hash="x")
        db.add(user)
        db.flush()

        # 来源一：两条 AI 资产
        for name in ("ai-one.png", "ai-two.png"):
            db.add(
                AiGeneratedAsset(
                    user_id=user.id,
                    prompt=f"prompt for {name}",
                    model_name="image-test",
                    params={},
                    file_path=f"/api/files/media/{name}",
                )
            )
        # 来源二：三个磁盘上传图 + 一个不符合前缀的干扰文件 + 一个非图片扩展名
        for name in (
            "xhs-upload-u{uid}-a.jpg",
            "xhs-upload-u{uid}-b.png",
            "xhs-upload-u{uid}-c.webp",
        ):
            (media_dir / name.format(uid=user.id)).write_bytes(b"fake-image-bytes")
        (media_dir / "unrelated-file.jpg").write_bytes(b"x")
        (media_dir / f"xhs-upload-u{user.id}-notes.txt").write_bytes(b"x")
        db.commit()

        valid_user_ids = {user.id}

        first_ai = backfill.backfill_ai_assets(db, dry_run=False, valid_user_ids=valid_user_ids)
        first_upload = backfill.backfill_uploaded_files(db, dry_run=False, valid_user_ids=valid_user_ids)
        db.commit()

        assert (first_ai.scanned, first_ai.created) == (2, 2)
        # 干扰文件与 .txt 都不该被扫进来
        assert (first_upload.scanned, first_upload.created) == (3, 3)
        assert len(db.scalars(select(MediaAsset.id)).all()) == 5

        second_ai = backfill.backfill_ai_assets(db, dry_run=False, valid_user_ids=valid_user_ids)
        second_upload = backfill.backfill_uploaded_files(db, dry_run=False, valid_user_ids=valid_user_ids)
        db.commit()

        assert (second_ai.created, second_ai.skipped_existing) == (0, 2)
        assert (second_upload.created, second_upload.skipped_existing) == (0, 3)
        assert len(db.scalars(select(MediaAsset.id)).all()) == 5

        sources = sorted(row for row in db.scalars(select(MediaAsset.source)).all())
        assert sources == ["ai", "ai", "upload", "upload", "upload"]
        # 回填素材一律未归类
        assert all(row is None for row in db.scalars(select(MediaAsset.folder_id)).all())
        # AI 资产同时带上 url 与裸文件名，后续 AI 生图写入才判得出重
        ai_rows = db.scalars(select(MediaAsset).where(MediaAsset.source == "ai")).all()
        assert {row.local_path for row in ai_rows} == {"ai-one.png", "ai-two.png"}
        assert {row.url for row in ai_rows} == {
            "/api/files/media/ai-one.png",
            "/api/files/media/ai-two.png",
        }
        # 原始 AI 元数据一条都不能少
        assert len(db.scalars(select(AiGeneratedAsset.id)).all()) == 2
    finally:
        db.close()
        engine.dispose()


def test_backfill_dry_run_reports_counts_without_writing_rows(tmp_path, monkeypatch):
    from backend.app.models import AiGeneratedAsset, MediaAsset, User
    from scripts import backfill_media_assets as backfill

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    monkeypatch.setattr(backfill, "_media_dir", lambda: media_dir)

    engine, db = _backfill_session(tmp_path)
    try:
        user = User(username="backfill-dry", password_hash="x")
        db.add(user)
        db.flush()
        db.add(
            AiGeneratedAsset(
                user_id=user.id,
                prompt="dry run",
                model_name="image-test",
                params={},
                file_path="/api/files/media/dry-ai.png",
            )
        )
        (media_dir / f"xhs-upload-u{user.id}-dry.jpg").write_bytes(b"bytes")
        db.commit()

        ai_stats = backfill.backfill_ai_assets(db, dry_run=True, valid_user_ids={user.id})
        upload_stats = backfill.backfill_uploaded_files(db, dry_run=True, valid_user_ids={user.id})
        db.rollback()

        assert ai_stats.created == 1
        assert upload_stats.created == 1
        assert db.scalars(select(MediaAsset.id)).all() == []
    finally:
        db.close()
        engine.dispose()


def test_backfill_skips_assets_whose_user_no_longer_exists(tmp_path, monkeypatch):
    from backend.app.models import AiGeneratedAsset, MediaAsset, User
    from scripts import backfill_media_assets as backfill

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    monkeypatch.setattr(backfill, "_media_dir", lambda: media_dir)

    engine, db = _backfill_session(tmp_path)
    try:
        user = User(username="backfill-scope", password_hash="x")
        db.add(user)
        db.flush()
        # 属于不存在用户的资产：外键会拒绝，必须在回填前就跳过
        db.add(
            AiGeneratedAsset(
                user_id=user.id,
                prompt="orphan check",
                model_name="image-test",
                params={},
                file_path="/api/files/media/orphan.png",
            )
        )
        (media_dir / "xhs-upload-u9999-ghost.jpg").write_bytes(b"bytes")
        db.commit()

        upload_stats = backfill.backfill_uploaded_files(db, dry_run=False, valid_user_ids={user.id})
        db.commit()

        assert upload_stats.scanned == 1
        assert upload_stats.created == 0
        assert upload_stats.skipped_invalid == 1
        assert db.scalars(select(MediaAsset.id)).all() == []
    finally:
        db.close()
        engine.dispose()


def test_generate_note_with_images_registers_each_image_in_media_assets(isolated_api):
    """generate_note 的登记发生在 AiDraft / DraftAsset 同一个事务里，单独覆盖这条路径。"""
    from backend.app.api.ai import get_image_ai_client, get_text_ai_client

    class FakeTextClient:
        def generate_note(self, **_kwargs):
            return {"title": "护腰怎么选", "body": "五个选购要点"}

    class FakeImageClient:
        def __init__(self):
            self.index = 0

        def generate_image(self, **_kwargs):
            self.index += 1
            return {"url": f"https://cdn.example.test/note-{self.index}.png", "raw": {}}

    token = _register_and_get_access_token("stage-a-note-images")
    app.dependency_overrides[get_text_ai_client] = FakeTextClient
    app.dependency_overrides[get_image_ai_client] = FakeImageClient
    try:
        for model_type, model_name, api_key in (
            ("text", "text-test", "sk-text"),
            ("image", "image-test", "sk-image"),
        ):
            response = client.post(
                "/api/model-configs",
                headers=_headers(token),
                json={
                    "name": f"Default {model_type}",
                    "model_type": model_type,
                    "provider": "openai-compatible",
                    "model_name": model_name,
                    "base_url": "https://api.example.test/v1",
                    "api_key": api_key,
                    "is_default": True,
                },
            )
            assert response.status_code == 200

        generated = client.post(
            "/api/ai/generate-note",
            headers=_headers(token),
            json={"topic": "护腰选购", "generate_images": True, "image_count": 2},
        )
        assert generated.status_code == 200
        draft_id = generated.json()["id"]

        assets = _media_assets(token)
        assert [asset["url"] for asset in assets] == [
            "https://cdn.example.test/note-1.png",
            "https://cdn.example.test/note-2.png",
        ]
        assert {asset["source"] for asset in assets} == {"ai"}

        # 草稿本身与 AI 元数据不受 SAVEPOINT 影响，仍然完整落库
        draft_assets = client.get(f"/api/drafts/{draft_id}/assets", headers=_headers(token))
        assert draft_assets.status_code == 200
        assert len(draft_assets.json()["items"]) == 2
        assert client.get("/api/ai/images/assets", headers=_headers(token)).json()["total"] == 2
    finally:
        app.dependency_overrides.pop(get_text_ai_client, None)
        app.dependency_overrides.pop(get_image_ai_client, None)
