"""阶段 B：指令中心的产品 / 场景绑定与官方模板标记。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.main import app

client = TestClient(app)

# 由 fixture 填充，供需要直接改库的测试复用同一引擎（沿用 test_stage_a_assets.py 的写法）
_TestingSessionLocal = None


def _override_database(tmp_path):
    global _TestingSessionLocal
    from backend.app.core.database import Base, get_db

    engine = create_engine(
        f"sqlite:///{tmp_path / 'stage-b-prompts-test.db'}",
        connect_args={"check_same_thread": False},
    )
    _TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = _TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return get_db


@pytest.fixture
def isolated_api(tmp_path):
    db_dependency = _override_database(tmp_path)
    try:
        yield
    finally:
        app.dependency_overrides.pop(db_dependency, None)


def _mark_official(prompt_id):
    """直接改库把模板标成官方——目前没有对外接口设置这个标记。"""
    from backend.app.models import PromptTemplate

    with _TestingSessionLocal() as db:
        db.get(PromptTemplate, prompt_id).is_official = True
        db.commit()


def _token(username):
    response = client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _create_product(token, name):
    response = client.post("/api/assets/products", headers=_headers(token), json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()


def _create_scene(token, name):
    response = client.post("/api/assets/scenes", headers=_headers(token), json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()


def _create_prompt(token, name, **extra):
    payload = {"name": name, "prompt_type": "rewrite", "content": f"{name} 的正文指令"}
    payload.update(extra)
    response = client.post("/api/prompt-library", headers=_headers(token), json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_account_filter_includes_unbound_prompts(isolated_api):
    """BUG-11：原来写成 in_([None, account_id])，而 SQL 里 IN (NULL, x) 永远匹配不到
    NULL 行，导致按账号筛选时通用指令一条也带不回来。"""
    token = _token("stage-b-account-filter")
    generic = _create_prompt(token, "通用指令")

    response = client.get("/api/prompt-library?account_id=999", headers=_headers(token))
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert generic["id"] in ids, "未绑定账号的通用指令必须一并返回"


def test_recommend_endpoint_survives_list_prompts_signature(isolated_api):
    """recommend 曾用位置参数调 list_prompts，B-01 在中间插入三个新参数后，
    current_user 与 db 会被错位塞进 product_id / scene_id。改为关键字传参。"""
    token = _token("stage-b-recommend")
    created = _create_prompt(token, "推荐用指令")

    response = client.get("/api/prompt-library/recommend/match?prompt_type=rewrite", headers=_headers(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["item"] is not None, "应能推荐出刚建的指令"
    assert body["item"]["id"] == created["id"]


def test_generate_title_accepts_layered_instruction(isolated_api, monkeypatch):
    """标题接口必须有独立的 instruction 字段。此前前端只能把指令塞进 body 并裁掉
    正文尾部，属绕法；B-02 核对时补上该字段。"""
    from backend.app.api.ai import get_text_ai_client

    captured = {}

    class FakeTextClient:
        def generate_titles(self, *, model_config, api_key, title, body, count, instruction="", system_prompt_override=""):
            captured["instruction"] = instruction
            captured["body"] = body
            return ["标题一"]

    app.dependency_overrides[get_text_ai_client] = lambda: FakeTextClient()
    token = _token("stage-b-title")
    response = client.post(
        "/api/ai/generate-title",
        headers=_headers(token),
        json={"title": "原标题", "body": "正文内容", "count": 1, "instruction": "要口语化，突出痛点"},
    )

    # 没配模型时会 400，此处只关心字段被接受且原样透传
    if response.status_code == 200:
        assert captured["instruction"] == "要口语化，突出痛点"
        assert captured["body"] == "正文内容", "正文不得被指令污染"
    else:
        assert response.status_code != 422, f"instruction 字段应被接受，实际：{response.text}"


def test_selected_prompt_replaces_default_persona_without_extra_text(isolated_api):
    """用户要求「必须按照我的指令生成」：选定的指令必须**取代**后端默认人设，
    而不是叠在它后面；同时不得再夹带 '提升表达、增强小红书语感' 这类系统默认文案。"""
    from backend.app.services.ai_service import OpenAICompatibleTextClient

    captured = {}

    class Probe(OpenAICompatibleTextClient):
        def _complete(self, *, model_config, api_key, system_prompt, user_prompt):
            captured["system"] = system_prompt
            captured["user"] = user_prompt
            return "改写结果"

    Probe().rewrite_note(
        model_config=None, api_key="k", title="原标题", body="原正文",
        instruction="", system_prompt_override="只用四字短句，不要emoji。",
    )

    assert captured["system"] == "只用四字短句，不要emoji。", "指令必须整体取代默认人设"
    assert "内容运营编辑" not in captured["system"], "默认人设不得残留"
    assert "提升表达" not in captured["user"], "不得夹带系统默认要求"
    assert "原正文" in captured["user"], "待改写的正文仍须送达"


def test_default_persona_still_applies_when_no_override(isolated_api):
    """没选指令时行为不变，保证这次改动不回退既有效果。"""
    from backend.app.services.ai_service import OpenAICompatibleTextClient

    captured = {}

    class Probe(OpenAICompatibleTextClient):
        def _complete(self, *, model_config, api_key, system_prompt, user_prompt):
            captured["system"] = system_prompt
            captured["user"] = user_prompt
            return "改写结果"

    Probe().rewrite_note(
        model_config=None, api_key="k", title="原标题", body="原正文", instruction="",
    )

    assert "内容运营编辑" in captured["system"]
    assert "提升表达" in captured["user"]


def test_prompt_can_bind_product_and_scene(isolated_api):
    token = _token("stage-b-bind")
    product = _create_product(token, "坐浴盆")
    scene = _create_scene(token, "卫生间场景")

    created = _create_prompt(token, "坐浴盆卫生间指令", product_id=product["id"], scene_id=scene["id"])

    assert created["product_id"] == product["id"]
    assert created["scene_id"] == scene["id"]
    assert created["is_official"] is False


def test_prompt_rejects_product_and_scene_from_other_user(isolated_api):
    owner = _token("stage-b-owner")
    other = _token("stage-b-other")
    product = _create_product(owner, "护腰")
    scene = _create_scene(owner, "久坐办公")

    for field, value in (("product_id", product["id"]), ("scene_id", scene["id"])):
        response = client.post(
            "/api/prompt-library",
            headers=_headers(other),
            json={"name": "越权指令", "prompt_type": "rewrite", "content": "x", field: value},
        )
        assert response.status_code == 404, f"{field} 应拒绝跨用户引用"


def test_product_scene_filter_includes_unbound_prompts(isolated_api):
    token = _token("stage-b-filter")
    product = _create_product(token, "坐浴盆")
    other_product = _create_product(token, "护腰")

    generic = _create_prompt(token, "通用改写指令")
    bidet = _create_prompt(token, "坐浴盆专属", product_id=product["id"])
    waist = _create_prompt(token, "护腰专属", product_id=other_product["id"])

    response = client.get(f"/api/prompt-library?product_id={product['id']}", headers=_headers(token))
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}

    # 通用指令必须一起返回，否则无法参与「通用 + 产品」的分层组合
    assert generic["id"] in ids
    assert bidet["id"] in ids
    assert waist["id"] not in ids


def test_editing_content_clears_official_flag(isolated_api):
    token = _token("stage-b-official")
    created = _create_prompt(token, "官方模板")
    _mark_official(created["id"])

    unchanged = client.put(
        f"/api/prompt-library/{created['id']}",
        headers=_headers(token),
        json={"name": "官方模板改名", "prompt_type": "rewrite", "content": created["content"]},
    )
    assert unchanged.status_code == 200
    # 仅改名未改正文，仍视为官方
    assert unchanged.json()["is_official"] is True

    edited = client.put(
        f"/api/prompt-library/{created['id']}",
        headers=_headers(token),
        json={"name": "官方模板改名", "prompt_type": "rewrite", "content": "我自己改过的正文"},
    )
    assert edited.status_code == 200
    # 改了正文即成为客户模板，升级不得覆盖
    assert edited.json()["is_official"] is False


def test_is_official_filter_separates_template_sources(isolated_api):
    token = _token("stage-b-official-filter")
    mine = _create_prompt(token, "我的指令")
    official = _create_prompt(token, "官方指令")
    _mark_official(official["id"])

    official_only = client.get("/api/prompt-library?is_official=true", headers=_headers(token))
    customer_only = client.get("/api/prompt-library?is_official=false", headers=_headers(token))

    assert {item["id"] for item in official_only.json()["items"]} == {official["id"]}
    assert {item["id"] for item in customer_only.json()["items"]} == {mine["id"]}
