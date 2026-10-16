from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import backend.app.api.operations as operations_module
from backend.app.api.operations import (
    PlanGenerateDraftPayload,
    ProposalDecisionPayload,
    _confirmed_profile_context,
    _active_operation_task,
    _fallback_strategy,
    _clean_strategy_output,
    _select_analysis_samples,
    account_operation_summary,
    approve_strategy,
    generate_plan_draft,
)
from backend.app.core.database import Base
from backend.app.models import AccountCookieVersion, AccountOperationProfile, AutoTask, ModelConfig, OperationPlan, OperationStrategyProposal, PlatformAccount, PublishJob, Task, User
from backend.app.services.scheduler_service import _auto_publish_allowed, _execute_auto_task_background


def _note(title: str, views: int = 0, likes: int = 0):
    return SimpleNamespace(
        title=title,
        raw_json={"views": views, "likes": likes, "collects": 0, "comments": 0, "shares": 0},
    )


def test_fallback_strategy_does_not_guess_a_product_category():
    account = SimpleNamespace(nickname="测试账号")

    strategy = _fallback_strategy(account, [_note("椰奶味牙膏", views=200), _note("可爱牙刷", likes=8)])

    serialized = str(strategy)
    assert strategy["analysis_source"] == "rules"
    assert strategy["positioning"] == "测试账号｜围绕当前内容的实用分享"
    assert strategy["content_directions"] == ["高表现内容复盘", "产品或服务特点分享", "使用场景与真实体验", "选购与对比参考"]
    assert "护腰" not in serialized
    assert "牙刷" not in strategy["positioning"]


def test_fallback_strategy_preserves_confirmed_active_profile():
    account = SimpleNamespace(nickname="测试账号")
    profile = SimpleNamespace(
        status="active",
        positioning="手动确认的定位",
        target_audience="手动确认的受众",
        content_directions_json='["方向甲", "方向乙"]',
        daily_generate_count=2,
        daily_publish_count=1,
        preferred_times_json='["09:30", "20:00"]',
        goals_json='["验证新方向"]',
        tone="自然具体",
        forbidden_topics_json='["不要虚构"]',
    )

    strategy = _fallback_strategy(account, [_note("任意历史内容")], profile)

    assert strategy["positioning"] == "手动确认的定位"
    assert strategy["target_audience"] == "手动确认的受众"
    assert strategy["content_directions"] == ["方向甲", "方向乙"]
    assert strategy["daily_generate_count"] == 2
    assert strategy["daily_publish_count"] == 1
    assert strategy["preferred_times"] == ["09:30", "20:00"]
    assert strategy["goals"] == ["验证新方向"]
    assert strategy["tone"] == "自然具体"
    assert strategy["forbidden_topics"] == ["不要虚构"]


def test_fallback_strategy_ignores_paused_profile_as_authority():
    account = SimpleNamespace(nickname="测试账号")
    profile = SimpleNamespace(
        status="paused",
        positioning="旧产品定位",
        target_audience="旧产品受众",
        content_directions_json='["旧产品方向"]',
    )

    strategy = _fallback_strategy(account, [_note("当前内容")], profile)

    assert strategy["positioning"] == "测试账号｜围绕当前内容的实用分享"
    assert strategy["content_directions"][0] == "高表现内容复盘"
    assert _confirmed_profile_context(profile) == {}


def test_analysis_samples_include_content_metrics_source_and_balanced_groups():
    rows = []
    for index in range(9):
        rows.append(SimpleNamespace(
            id=index + 1,
            note_id=f"n-{index}",
            title=f"标题{index}",
            content="正文内容 " * 100,
            created_at=None,
            raw_json={
                "source": "android_ui" if index % 2 == 0 else "pc_api",
                "views": 1000 - index * 50,
                "likes": index * 10,
                "collects": index,
                "comments": 2,
                "shares": 1,
                "published_at": f"2026-09-{index + 1:02d}",
            },
        ))

    samples = _select_analysis_samples(rows, limit=9)
    assert len(samples) == 9
    groups = " ".join(item["sample_group"] for item in samples)
    assert "high_performance" in groups and "recent" in groups and "low_performance" in groups
    assert all(item["note_id"] for item in samples)
    assert all(item["body_summary"] for item in samples)
    assert all(item["source"] in {"android_ui", "pc_api"} for item in samples)
    assert all(item["engagement_rate"] is not None for item in samples)


def test_clean_strategy_output_adds_evidence_and_topic_fields_but_rejects_duplicates():
    fallback = _fallback_strategy(SimpleNamespace(nickname="账号"), [_note("历史")])
    generated = {
        "positioning": "定位", "target_audience": "受众",
        "content_directions": ["方向"],
        "learning_insights": [{
            "type": "content", "title": "结论", "conclusion": "基于样本",
            "confidence": "0.8", "evidence_note_ids": ["n-1", "missing"],
        }],
        "seven_day_topics": [
            {"direction": "方向", "topic": f"不同选题{i}"}
            for i in range(7)
        ],
    }
    cleaned = _clean_strategy_output(generated, fallback, {"n-1"})
    insight = cleaned["learning_insights"][0]
    assert insight["evidence_note_ids"] == ["n-1"]
    assert insight["recommended_action"]
    assert all("angle" in topic and "title_idea" in topic for topic in cleaned["seven_day_topics"])


def _operation_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    user = User(username="operations-owner", password_hash="test")
    db.add(user)
    db.flush()
    account = PlatformAccount(
        user_id=user.id, platform="xhs", sub_type="android", nickname="测试账号", status="active",
    )
    db.add(account)
    db.flush()
    strategy = {
        "positioning": "测试定位", "target_audience": "测试受众",
        "content_directions": ["方向甲"], "daily_generate_count": 1,
        "daily_publish_count": 1, "preferred_times": ["20:30"],
        "learning_insights": [],
        "seven_day_topics": [{"direction": "方向甲", "topic": "具体选题"}],
    }
    proposal = OperationStrategyProposal(
        user_id=user.id, platform_account_id=account.id, source_note_count=3,
        strategy_json=__import__("json").dumps(strategy, ensure_ascii=False),
    )
    db.add(proposal)
    db.commit()
    return db, user, account, proposal


def test_approve_strategy_creates_paused_auto_tasks_by_default():
    db, user, _account, proposal = _operation_db()
    try:
        result = approve_strategy(proposal.id, ProposalDecisionPayload(), user, db)
        task = db.scalar(select(AutoTask))

        assert result["auto_tasks_created"] == 1
        assert result["auto_tasks_enabled"] is False
        assert task is not None
        assert task.status == "paused"
        assert task.next_run_at is None
    finally:
        db.close()


def test_approve_strategy_can_skip_auto_task_creation():
    db, user, _account, proposal = _operation_db()
    try:
        result = approve_strategy(
            proposal.id,
            ProposalDecisionPayload(create_auto_tasks=False),
            user,
            db,
        )

        assert result["plans_created"] == 7
        assert result["auto_tasks_created"] == 0
        assert db.scalar(select(AutoTask)) is None
    finally:
        db.close()


def test_account_operation_summary_reports_execution_safety_state():
    db, user, account, _proposal = _operation_db()
    try:
        summary = account_operation_summary(account.id, user, db)

        assert summary["account"]["id"] == account.id
        assert summary["sync"]["note_count"] == 0
        assert summary["analysis"]["latest_proposal"]["status"] == "pending"
        assert summary["execution"]["auto_task_active_count"] == 0
        assert summary["execution"]["auto_task_paused_count"] == 0
    finally:
        db.close()


def test_stale_running_operation_is_failed_and_no_longer_blocks_account():
    db, user, account, _proposal = _operation_db()
    old = __import__("datetime").datetime.now() - __import__("datetime").timedelta(minutes=31)
    task = Task(
        user_id=user.id, platform="xhs", task_type="account_content_sync",
        status="running", progress=25, started_at=old,
        payload={"account_id": account.id, "phase": "syncing_remote"},
    )
    db.add(task)
    db.commit()
    try:
        assert _active_operation_task(db, user.id, account.id) is None
        db.refresh(task)
        db.refresh(account)
        metadata = __import__("json").loads(account.profile_json)["operation_sync"]
        assert task.status == "failed"
        assert task.error_type == "operation_task_stale"
        assert metadata["status"] == "failed"
        assert metadata["finished_at"]
    finally:
        db.close()


def test_sync_thread_exception_marks_persisted_sync_metadata_failed(monkeypatch):
    db, user, account, _proposal = _operation_db()
    account.profile_json = __import__("json").dumps({
        "operation_sync": {"status": "running", "started_at": "2026-09-03T12:00:00"},
    })
    task = Task(
        user_id=user.id, platform="xhs", task_type="account_content_sync",
        status="pending", progress=0, payload={"account_id": account.id},
    )
    db.add(task)
    db.commit()

    class _SessionWrapper:
        def __init__(self, session):
            self.session = session

        def __getattr__(self, name):
            return getattr(self.session, name)

        def close(self):
            pass

    monkeypatch.setattr(operations_module, "SessionLocal", lambda: _SessionWrapper(db))
    monkeypatch.setattr(operations_module, "_sync_account_content_now", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("模拟同步崩溃")))
    try:
        operations_module._run_operation_task(task.id, "sync")
        db.refresh(task)
        db.refresh(account)
        metadata = __import__("json").loads(account.profile_json)["operation_sync"]
        assert task.status == "failed"
        assert task.error_type == "operation_sync_failed"
        assert metadata["status"] == "failed"
        assert metadata["error"] == "模拟同步崩溃"
        assert metadata["finished_at"]
    finally:
        db.close()


def test_auto_publish_gate_requires_explicit_review_opt_out():
    db, user, account, _proposal = _operation_db()
    try:
        task = AutoTask(
            user_id=user.id,
            name="普通自动任务",
            keywords=["测试"],
            creator_account_id=account.id,
        )
        db.add(task)
        db.commit()

        # Older/manual tasks without an operations profile must stay in review.
        assert _auto_publish_allowed(db, task) is False

        profile = AccountOperationProfile(
            user_id=user.id,
            platform_account_id=account.id,
            review_required=True,
        )
        db.add(profile)
        db.commit()
        assert _auto_publish_allowed(db, task) is False

        # Only an explicit, persisted opt-out permits an irreversible Creator
        # publish.  The UI label alone is not part of this decision.
        profile.review_required = False
        db.commit()
        assert _auto_publish_allowed(db, task) is True
    finally:
        db.close()


def _creator_auto_task_db(review_required: bool):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    user = User(username=f"creator-gate-{review_required}", password_hash="test")
    db.add(user)
    db.flush()
    source = PlatformAccount(
        user_id=user.id, platform="xhs", sub_type="pc", nickname="采集号", status="active",
    )
    creator = PlatformAccount(
        user_id=user.id, platform="xhs", sub_type="creator", nickname="发布号", status="active",
    )
    db.add_all([source, creator])
    db.flush()
    db.add_all([
        AccountCookieVersion(platform_account_id=source.id, encrypted_cookies="source-cookie"),
        AccountCookieVersion(platform_account_id=creator.id, encrypted_cookies="creator-cookie"),
        AccountOperationProfile(
            user_id=user.id,
            platform_account_id=creator.id,
            review_required=review_required,
        ),
    ])
    task = AutoTask(
        user_id=user.id,
        name="普通自动任务",
        keywords=["测试主题"],
        pc_account_id=source.id,
        creator_account_id=creator.id,
    )
    db.add(task)
    db.commit()
    return db, task


def _install_auto_task_adapters(monkeypatch):
    calls = {"post_note": 0}

    class FakeAdapter:
        def __init__(self, _cookies):
            pass

        def search_note(self, _keyword, page=1):
            return True, "ok", [{
                "title": "参考标题",
                "content": "参考正文",
                "likes": 10,
                "collects": 2,
                "comments": 1,
                "shares": 0,
                "image_urls": ["https://example.test/one.jpg"],
            }]

        def upload_media(self, _url, _media_type):
            return {"fileIds": "uploaded-1"}

        def post_note(self, _note_info):
            calls["post_note"] += 1
            return {"note_id": "published-1"}

    import backend.app.adapters.xhs.creator_api_adapter as creator_module
    import backend.app.adapters.xhs.pc_api_adapter as pc_module
    import backend.app.api.platforms.xhs.crawl as crawl_module
    import backend.app.api.platforms.xhs.pc as pc_api_module
    import backend.app.services.scheduler_service as scheduler_module

    monkeypatch.setattr(pc_module, "XhsPcApiAdapter", FakeAdapter)
    monkeypatch.setattr(creator_module, "XhsCreatorApiAdapter", FakeAdapter)
    monkeypatch.setattr(crawl_module, "_data_items", lambda raw: raw)
    monkeypatch.setattr(pc_api_module, "_normalize_search_item", lambda item: item)
    monkeypatch.setattr(scheduler_module, "decrypt_text", lambda value: value)
    return calls


def test_creator_auto_task_with_review_required_never_posts(monkeypatch):
    db, task = _creator_auto_task_db(review_required=True)
    calls = _install_auto_task_adapters(monkeypatch)
    try:
        _execute_auto_task_background(db, task)
        job = db.scalar(select(PublishJob))

        assert job is not None
        assert job.status == "pending"
        assert job.published_at is None
        assert calls["post_note"] == 0
    finally:
        db.close()


def test_creator_auto_task_posts_only_after_explicit_review_opt_out(monkeypatch):
    db, task = _creator_auto_task_db(review_required=False)
    calls = _install_auto_task_adapters(monkeypatch)
    try:
        _execute_auto_task_background(db, task)
        job = db.scalar(select(PublishJob))

        assert job is not None
        assert job.status == "published"
        assert job.external_note_id == "published-1"
        assert calls["post_note"] == 1
    finally:
        db.close()


class _FakePlanTextClient:
    def __init__(self):
        self.calls = []

    def generate_note(self, **kwargs):
        self.calls.append(kwargs)
        return {"title": "具体原创标题", "body": "这是根据运营计划生成、等待人工审核的正文。"}


def _draft_plan_db():
    db, user, account, _proposal = _operation_db()
    db.add(ModelConfig(
        user_id=user.id, name="文本模型", model_type="text", provider="test",
        model_name="test-model", base_url="https://example.test/v1",
        encrypted_api_key="not-empty", is_default=True,
    ))
    plan = OperationPlan(
        user_id=user.id, platform_account_id=account.id,
        plan_date=__import__("datetime").date.today(),
        content_direction="使用场景", topic="办公桌收纳的三个细节",
        rationale="来自已确认策略",
    )
    db.add(plan)
    db.commit()
    return db, user, account, plan


def test_generate_plan_draft_defaults_to_draft_only(monkeypatch):
    db, user, account, plan = _draft_plan_db()
    client = _FakePlanTextClient()
    monkeypatch.setattr("backend.app.api.operations.decrypt_text", lambda value: "test-key")
    try:
        result = generate_plan_draft(plan.id, PlanGenerateDraftPayload(), user, db, client)

        assert result["created"] is True
        assert result["draft"]["platform_account_id"] == account.id
        assert result["publish_job"] is None
        assert result["plan"]["status"] == "drafted"
        assert db.scalar(select(PublishJob)) is None
        assert client.calls[0]["topic"] == "办公桌收纳的三个细节"
        assert "账号定位" in client.calls[0]["instruction"]
    finally:
        db.close()


def test_generate_plan_draft_is_idempotent(monkeypatch):
    db, user, _account, plan = _draft_plan_db()
    client = _FakePlanTextClient()
    monkeypatch.setattr("backend.app.api.operations.decrypt_text", lambda value: "test-key")
    try:
        first = generate_plan_draft(plan.id, PlanGenerateDraftPayload(), user, db, client)
        second = generate_plan_draft(plan.id, PlanGenerateDraftPayload(), user, db, client)

        assert first["draft"]["id"] == second["draft"]["id"]
        assert second["created"] is False
        assert len(client.calls) == 1
    finally:
        db.close()


def test_generate_plan_draft_can_stage_but_never_execute_publish_job(monkeypatch):
    db, user, _account, plan = _draft_plan_db()
    client = _FakePlanTextClient()
    monkeypatch.setattr("backend.app.api.operations.decrypt_text", lambda value: "test-key")
    try:
        result = generate_plan_draft(
            plan.id, PlanGenerateDraftPayload(create_publish_job=True), user, db, client,
        )
        job = db.get(PublishJob, result["publish_job"]["id"])

        assert result["plan"]["status"] == "scheduled"
        assert job.status == "pending"
        assert job.publish_mode == "immediate"
        assert job.scheduled_at is None
        assert job.published_at is None
        assert job.external_note_id == ""
    finally:
        db.close()
