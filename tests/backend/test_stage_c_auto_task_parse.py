"""C-03：一句话生成自动运营任务。

这些测试守的是一条不可逆边界：自动运营任务会自动发布到真实小红书账号，发出去撤不回来。
所以解析端点只能产出提案——不落库、不排调度、不触发发布，且任何拿不准的字段必须留空并告警，
绝不填猜测值（尤其是账号 id，填错就是发到别人主页）。

AI 一律用 dependency_overrides 假客户端，绝不真调模型。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.main import app

client = TestClient(app)

_TestingSessionLocal = None


def _override_database(tmp_path):
    global _TestingSessionLocal
    from backend.app.core.database import Base, get_db

    engine = create_engine(
        f"sqlite:///{tmp_path / 'stage-c-auto-parse-test.db'}",
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


class FakeParsingTextClient:
    """只实现解析方法的假客户端。`parsed` 就是「AI 返回了什么」。"""

    def __init__(self, parsed):
        self._parsed = parsed
        self.calls = []

    def parse_auto_task_description(
        self, *, model_config, api_key, description, accounts, instruction_candidates
    ):
        self.calls.append(
            {
                "description": description,
                "accounts": accounts,
                "instruction_candidates": instruction_candidates,
            }
        )
        if isinstance(self._parsed, Exception):
            raise self._parsed
        return self._parsed


def _token(username):
    response = client.post(
        "/api/auth/register", json={"username": username, "password": "secret123"}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _seed_account(user_id, sub_type, nickname):
    from backend.app.models import PlatformAccount

    with _TestingSessionLocal() as db:
        account = PlatformAccount(
            user_id=user_id,
            platform="xhs",
            sub_type=sub_type,
            external_user_id=f"{sub_type}-{nickname}",
            nickname=nickname,
            status="active",
        )
        db.add(account)
        db.commit()
        return account.id


def _seed_default_text_model(user_id):
    from backend.app.core.security import encrypt_text
    from backend.app.models import ModelConfig

    with _TestingSessionLocal() as db:
        config = ModelConfig(
            user_id=user_id,
            name="测试文本模型",
            model_type="text",
            provider="openai",
            base_url="https://example.invalid/v1",
            model_name="fake-model",
            encrypted_api_key=encrypt_text("fake-key"),
            is_default=True,
        )
        db.add(config)
        db.commit()
        return config.id


@pytest.fixture
def parse_env(tmp_path):
    """一个用户 + 采集账号 + 发布账号 + 默认文本模型。返回后由测试挂假 AI。"""
    db_dependency = _override_database(tmp_path)
    token = _token("auto-parse-owner")
    pc_id = _seed_account(1, "pc", "采集号")
    creator_id = _seed_account(1, "creator", "医麦德护具店")
    _seed_default_text_model(1)
    try:
        yield {
            "headers": {"Authorization": f"Bearer {token}"},
            "pc_account_id": pc_id,
            "creator_account_id": creator_id,
        }
    finally:
        app.dependency_overrides.pop(db_dependency, None)


def _install_fake_ai(parsed):
    from backend.app.api.ai import get_text_ai_client

    fake = FakeParsingTextClient(parsed)
    app.dependency_overrides[get_text_ai_client] = lambda: fake
    return get_text_ai_client, fake


def _parse(headers, parsed, **extra):
    dependency, fake = _install_fake_ai(parsed)
    try:
        response = client.post(
            "/api/auto-tasks/parse-description",
            json={"description": "每天早上九点找护腰相关的热门笔记，改写成口语化种草文", **extra},
            headers=headers,
        )
    finally:
        app.dependency_overrides.pop(dependency, None)
    return response, fake


def _count_auto_tasks():
    from backend.app.models import AutoTask

    with _TestingSessionLocal() as db:
        return len(db.scalars(select(AutoTask)).all())


def test_parse_description_returns_structured_proposal(parse_env):
    response, fake = _parse(
        parse_env["headers"],
        {
            "name": "护腰口语化种草",
            "keywords": ["护腰", "久坐腰酸"],
            "ai_instruction": "改写成口语化种草文",
            "schedule_type": "daily",
            "schedule_time": "09:00",
            "schedule_days": "",
            "schedule_interval_hours": None,
            "pc_account_id": parse_env["pc_account_id"],
            "creator_account_id": parse_env["creator_account_id"],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    proposal = payload["proposal"]
    assert proposal["name"] == "护腰口语化种草"
    assert proposal["keywords"] == ["护腰", "久坐腰酸"]
    assert proposal["schedule_type"] == "daily"
    assert proposal["schedule_time"] == "09:00"
    assert proposal["pc_account_id"] == parse_env["pc_account_id"]
    assert proposal["creator_account_id"] == parse_env["creator_account_id"]
    assert payload["requires_confirmation"] is True
    assert len(fake.calls) == 1
    # 账号候选必须喂进 prompt，否则模型只能凭空编 id
    assert {item["id"] for item in fake.calls[0]["accounts"]} == {
        parse_env["pc_account_id"],
        parse_env["creator_account_id"],
    }


def test_parse_description_proposal_is_never_persisted_as_auto_task(parse_env):
    """人工确认门的核心断言：解析成功也绝不产生 AutoTask 行。"""
    assert _count_auto_tasks() == 0

    response, _ = _parse(
        parse_env["headers"],
        {
            "name": "护腰口语化种草",
            "keywords": ["护腰"],
            "ai_instruction": "口语化",
            "schedule_type": "daily",
            "schedule_time": "09:00",
            "pc_account_id": parse_env["pc_account_id"],
            "creator_account_id": parse_env["creator_account_id"],
        },
    )

    assert response.status_code == 200, response.text
    assert _count_auto_tasks() == 0, "解析端点不得落库，必须由用户确认后走创建接口"


def test_parse_description_matched_account_is_returned_for_human_review(parse_env):
    response, _ = _parse(
        parse_env["headers"],
        {
            "name": "护腰种草",
            "keywords": ["护腰"],
            "schedule_type": "daily",
            "schedule_time": "09:00",
            "creator_account_id": parse_env["creator_account_id"],
        },
    )

    payload = response.json()
    publish_matches = [
        item for item in payload["matched_accounts"] if item["role"] == "creator_account_id"
    ]
    assert len(publish_matches) == 1
    assert publish_matches[0]["nickname"] == "医麦德护具店"
    # 匹配上了也必须提醒复核——发布不可撤回
    assert any("医麦德护具店" in warning for warning in payload["warnings"])


def test_parse_description_rejects_invalid_schedule_type_into_warnings(parse_env):
    response, _ = _parse(
        parse_env["headers"],
        {
            "name": "护腰种草",
            "keywords": ["护腰"],
            "schedule_type": "every_morning",
            "schedule_time": "09:00",
        },
    )

    payload = response.json()
    assert payload["proposal"]["schedule_type"] == "", "非法调度方式必须留空"
    assert any("every_morning" in warning for warning in payload["warnings"])


def test_parse_description_rejects_malformed_schedule_time_into_warnings(parse_env):
    response, _ = _parse(
        parse_env["headers"],
        {
            "name": "护腰种草",
            "keywords": ["护腰"],
            "schedule_type": "daily",
            "schedule_time": "早上九点",
        },
    )

    payload = response.json()
    assert payload["proposal"]["schedule_time"] == "", "非 HH:MM 的时间必须留空"
    assert any("早上九点" in warning for warning in payload["warnings"])


def test_parse_description_rejects_out_of_range_schedule_time(parse_env):
    """25:00 是合法的 HH:MM 形状但不是合法时刻，正则必须卡住。"""
    response, _ = _parse(
        parse_env["headers"],
        {"name": "护腰种草", "keywords": ["护腰"], "schedule_type": "daily", "schedule_time": "25:00"},
    )

    assert response.json()["proposal"]["schedule_time"] == ""


def test_parse_description_does_not_adopt_account_id_of_another_user(parse_env):
    """跨用户账号 id 绝不能被采纳——采纳即等于把笔记发到别人的账号。"""
    other_creator_id = _seed_account(2, "creator", "别人的账号")

    response, _ = _parse(
        parse_env["headers"],
        {
            "name": "护腰种草",
            "keywords": ["护腰"],
            "schedule_type": "daily",
            "schedule_time": "09:00",
            "creator_account_id": other_creator_id,
        },
    )

    payload = response.json()
    assert payload["proposal"]["creator_account_id"] is None, "他人账号 id 必须留空"
    assert all(
        item["id"] != other_creator_id for item in payload["matched_accounts"]
    ), "他人账号不得出现在匹配结果里"
    assert any(str(other_creator_id) in warning for warning in payload["warnings"])


def test_parse_description_leaves_nonexistent_account_empty(parse_env):
    response, _ = _parse(
        parse_env["headers"],
        {
            "name": "护腰种草",
            "keywords": ["护腰"],
            "schedule_type": "daily",
            "schedule_time": "09:00",
            "creator_account_id": 999999,
        },
    )

    assert response.json()["proposal"]["creator_account_id"] is None


def test_parse_description_rejects_account_with_wrong_sub_type(parse_env):
    """采集账号槽位不接受 creator 号，反之亦然——沿用创建接口的子类型约束。"""
    response, _ = _parse(
        parse_env["headers"],
        {
            "name": "护腰种草",
            "keywords": ["护腰"],
            "schedule_type": "daily",
            "schedule_time": "09:00",
            "pc_account_id": parse_env["creator_account_id"],
        },
    )

    assert response.json()["proposal"]["pc_account_id"] is None


def test_parse_description_caps_keywords_at_ten(parse_env):
    response, _ = _parse(
        parse_env["headers"],
        {
            "name": "护腰种草",
            "keywords": [f"关键词{index}" for index in range(25)],
            "schedule_type": "daily",
            "schedule_time": "09:00",
        },
    )

    payload = response.json()
    assert len(payload["proposal"]["keywords"]) == 10
    assert any("10" in warning for warning in payload["warnings"])


def test_parse_description_warns_when_publish_account_missing(parse_env):
    """发布账号缺失必须告警——发错账号不可逆。

    采集账号缺失**不**告警：它是可选的，不填即跳过抓取、纯 AI 生成。
    """
    response, _ = _parse(
        parse_env["headers"],
        {"name": "护腰种草", "keywords": ["护腰"], "schedule_type": "manual"},
    )

    payload = response.json()
    assert payload["proposal"]["creator_account_id"] is None
    assert any("发布账号" in warning for warning in payload["warnings"])
    # 采集账号可选，留空不该告警，否则用户会以为漏填了东西
    assert not any("采集账号" in warning for warning in payload["warnings"]), (
        f"采集账号是可选字段，不应告警。实际告警：{payload['warnings']}"
    )
    assert payload["proposal"]["pc_account_id"] is None


def test_parse_description_offers_auto_ops_prompts_as_instruction_candidates(parse_env):
    from backend.app.models import PromptTemplate

    with _TestingSessionLocal() as db:
        db.add(
            PromptTemplate(
                user_id=1,
                name="护具口语化改写",
                prompt_type="auto_ops",
                content="改写成口语化种草文，加入真实体验",
                status="active",
            )
        )
        db.commit()

    response, fake = _parse(
        parse_env["headers"],
        {"name": "护腰种草", "keywords": ["护腰"], "schedule_type": "manual"},
    )

    payload = response.json()
    assert [item["name"] for item in payload["instruction_candidates"]] == ["护具口语化改写"]
    assert fake.calls[0]["instruction_candidates"][0]["name"] == "护具口语化改写"


def test_parse_description_requires_authentication(parse_env):
    response = client.post(
        "/api/auto-tasks/parse-description", json={"description": "每天九点发护腰笔记"}
    )
    assert response.status_code == 401


def test_parse_description_requires_default_text_model(tmp_path):
    db_dependency = _override_database(tmp_path)
    token = _token("no-model-owner")
    try:
        dependency, _ = _install_fake_ai({"name": "x"})
        try:
            response = client.post(
                "/api/auto-tasks/parse-description",
                json={"description": "每天九点发护腰笔记"},
                headers={"Authorization": f"Bearer {token}"},
            )
        finally:
            app.dependency_overrides.pop(dependency, None)
        assert response.status_code == 400
    finally:
        app.dependency_overrides.pop(db_dependency, None)


def test_parse_description_ai_failure_does_not_create_auto_task(parse_env):
    response, _ = _parse(parse_env["headers"], RuntimeError("model exploded"))

    assert response.status_code == 502
    assert _count_auto_tasks() == 0


def test_parse_description_explicit_account_id_overrides_ai_guess(parse_env):
    """用户显式指定的账号优先于 AI 猜测，但仍要过归属校验。"""
    response, _ = _parse(
        parse_env["headers"],
        {
            "name": "护腰种草",
            "keywords": ["护腰"],
            "schedule_type": "manual",
            "creator_account_id": 999999,
        },
        account_id=parse_env["creator_account_id"],
    )

    payload = response.json()
    assert payload["proposal"]["creator_account_id"] == parse_env["creator_account_id"]


def test_parse_description_explicit_account_id_of_another_user_is_refused(parse_env):
    other_creator_id = _seed_account(2, "creator", "别人的账号")

    response, _ = _parse(
        parse_env["headers"],
        {"name": "护腰种草", "keywords": ["护腰"], "schedule_type": "manual"},
        account_id=other_creator_id,
    )

    payload = response.json()
    assert payload["proposal"]["creator_account_id"] is None
    assert _count_auto_tasks() == 0


def test_parse_description_weekly_without_days_warns(parse_env):
    response, _ = _parse(
        parse_env["headers"],
        {
            "name": "护腰种草",
            "keywords": ["护腰"],
            "schedule_type": "weekly",
            "schedule_time": "09:00",
            "schedule_days": "",
        },
    )

    payload = response.json()
    assert payload["proposal"]["schedule_days"] == ""
    assert any("执行日期" in warning for warning in payload["warnings"])


def test_parse_description_rejects_invalid_weekday_values(parse_env):
    response, _ = _parse(
        parse_env["headers"],
        {
            "name": "护腰种草",
            "keywords": ["护腰"],
            "schedule_type": "weekly",
            "schedule_time": "09:00",
            "schedule_days": "1,9",
        },
    )

    assert response.json()["proposal"]["schedule_days"] == ""


def test_parse_description_rejects_out_of_range_interval_hours(parse_env):
    response, _ = _parse(
        parse_env["headers"],
        {
            "name": "护腰种草",
            "keywords": ["护腰"],
            "schedule_type": "interval",
            "schedule_interval_hours": 5000,
        },
    )

    payload = response.json()
    assert payload["proposal"]["schedule_interval_hours"] is None
    assert any("5000" in warning for warning in payload["warnings"])


def test_create_auto_task_without_collection_account(parse_env):
    """不填采集账号也能建任务——纯生成场景不需要抓取。

    此前 pc_account_id 是 NOT NULL 且表单必填，用户即使不抓取也得随便选个账号。
    """
    created = client.post(
        "/api/auto-tasks",
        json={
            "name": "纯生成护腰笔记",
            "keywords": ["护腰"],
            "creator_account_id": parse_env["creator_account_id"],
            "ai_instruction": "生成10篇护腰主题小红书笔记",
            "schedule_type": "manual",
        },
        headers=parse_env["headers"],
    )

    assert created.status_code == 200, created.text
    body = created.json()
    assert body["pc_account_id"] is None, "未提供采集账号时应存为 null"
    assert body["creator_account_id"] == parse_env["creator_account_id"]


def test_create_auto_task_rejects_foreign_collection_account(parse_env):
    """采集账号可选，但填了就必须是自己的——不能因为「可选」就放松归属校验。"""
    created = client.post(
        "/api/auto-tasks",
        json={
            "name": "越权采集",
            "keywords": ["护腰"],
            "pc_account_id": 999999,
            "creator_account_id": parse_env["creator_account_id"],
            "schedule_type": "manual",
        },
        headers=parse_env["headers"],
    )

    assert created.status_code == 404, f"不存在的采集账号应被拒，实际 {created.status_code}"


def test_parse_description_proposal_fields_are_accepted_by_create_endpoint(parse_env):
    """提案填进现有创建表单后必须能提交成功——两处字段口径不能漂移。"""
    response, _ = _parse(
        parse_env["headers"],
        {
            "name": "护腰口语化种草",
            "keywords": ["护腰", "久坐腰酸"],
            "ai_instruction": "口语化种草",
            "schedule_type": "daily",
            "schedule_time": "09:00",
            "pc_account_id": parse_env["pc_account_id"],
            "creator_account_id": parse_env["creator_account_id"],
        },
    )
    proposal = response.json()["proposal"]

    created = client.post(
        "/api/auto-tasks",
        json={
            "name": proposal["name"],
            "keywords": proposal["keywords"],
            "pc_account_id": proposal["pc_account_id"],
            "creator_account_id": proposal["creator_account_id"],
            "ai_instruction": proposal["ai_instruction"],
            "schedule_type": proposal["schedule_type"],
            "schedule_time": proposal["schedule_time"],
        },
        headers=parse_env["headers"],
    )

    assert created.status_code == 200, created.text
    assert created.json()["name"] == "护腰口语化种草"
    assert _count_auto_tasks() == 1, "只有走创建接口才应产生任务"


def test_parse_description_schedule_types_match_create_endpoint_enum():
    """两边枚举必须同源，否则 AI 产出的合法值会被创建接口拒掉。"""
    from backend.app.api.auto_tasks import SCHEDULE_TYPES, AutoTaskCreateRequest

    pattern = AutoTaskCreateRequest.model_fields["schedule_type"].metadata[0].pattern
    for schedule_type in SCHEDULE_TYPES:
        assert schedule_type in pattern
    assert pattern == f"^({'|'.join(SCHEDULE_TYPES)})$"


def test_extract_json_object_survives_markdown_fence_and_prose():
    """模型即便被要求只输出 JSON 也常包一层 ```json 或前后加一句话。"""
    from backend.app.services.ai_service import _extract_json_object

    assert _extract_json_object('```json\n{"name": "护腰"}\n```') == {"name": "护腰"}
    assert _extract_json_object('好的，配置如下：{"name": "护腰"}') == {"name": "护腰"}
    assert _extract_json_object('{"name": "护腰"}') == {"name": "护腰"}


def test_extract_json_object_rejects_non_object_payloads():
    from backend.app.services.ai_service import _extract_json_object

    for bad in ["完全不是 JSON", "[1, 2, 3]", ""]:
        with pytest.raises(ValueError):
            _extract_json_object(bad)


def test_auto_ops_page_exposes_ai_generation_entry_outside_modal():
    """入口可发现性：输入框和按钮必须直接在页面上，不能藏进弹窗或折叠面板。
    此前两次因为把功能塞进 Modal 收到「没看到」的反馈。"""
    source = open(
        "frontend/src/pages/platforms/xhs/auto-ops-page.tsx", encoding="utf-8"
    ).read()

    assert "parseAutoTaskDescription" in source
    assert "AI 生成任务" in source
    entry_index = source.index("AI 生成任务")
    # 入口必须出现在编辑弹窗之前，且不在 Modal 内
    assert entry_index < source.index("<Modal")
    assert "Collapse" not in source
