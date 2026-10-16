"""API-level regression tests for the first operations-console safety pass.

Every test uses a private SQLite database.  Nothing here calls an AI provider,
Xiaohongshu, or a phone.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.main import app


client = TestClient(app)


@pytest.fixture
def operations_api(tmp_path):
    from backend.app.core.database import Base, get_db
    from backend.app.models import OperationStrategyProposal, PlatformAccount, User

    engine = create_engine(
        f"sqlite:///{tmp_path / 'operations-phase1-api.db'}",
        connect_args={"check_same_thread": False},
    )
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    def register(username: str) -> dict[str, str]:
        response = client.post(
            "/api/auth/register",
            json={"username": username, "password": "secret123"},
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    owner_headers = register("phase1-owner")
    other_headers = register("phase1-other")
    with testing_session() as db:
        owner = db.scalar(select(User).where(User.username == "phase1-owner"))
        other = db.scalar(select(User).where(User.username == "phase1-other"))
        assert owner is not None and other is not None
        owner_account = PlatformAccount(
            user_id=owner.id,
            platform="xhs",
            sub_type="creator",
            nickname="运营发布号",
            status="active",
        )
        other_account = PlatformAccount(
            user_id=other.id,
            platform="xhs",
            sub_type="creator",
            nickname="其他人的发布号",
            status="active",
        )
        db.add_all([owner_account, other_account])
        db.flush()
        strategy = {
            "positioning": "测试定位",
            "target_audience": "测试受众",
            "content_directions": ["方向甲"],
            "daily_generate_count": 1,
            "daily_publish_count": 1,
            "preferred_times": ["20:30"],
            "goals": ["验证流程"],
            "tone": "自然具体",
            "forbidden_topics": ["虚构体验"],
            "learning_insights": [],
            "seven_day_topics": [{"direction": "方向甲", "topic": "具体选题"}],
        }
        proposal = OperationStrategyProposal(
            user_id=owner.id,
            platform_account_id=owner_account.id,
            source_note_count=3,
            strategy_json=json.dumps(strategy, ensure_ascii=False),
        )
        db.add(proposal)
        db.commit()
        seeded = {
            "owner_id": owner.id,
            "other_id": other.id,
            "owner_account_id": owner_account.id,
            "other_account_id": other_account.id,
            "proposal_id": proposal.id,
        }

    try:
        yield {
            **seeded,
            "owner_headers": owner_headers,
            "other_headers": other_headers,
            "session": testing_session,
        }
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_summary_is_owner_scoped(operations_api):
    account_id = operations_api["owner_account_id"]

    owner_response = client.get(
        f"/api/operations/accounts/{account_id}/summary",
        headers=operations_api["owner_headers"],
    )
    assert owner_response.status_code == 200, owner_response.text
    assert owner_response.json()["account"]["id"] == account_id

    other_response = client.get(
        f"/api/operations/accounts/{account_id}/summary",
        headers=operations_api["other_headers"],
    )
    assert other_response.status_code == 404


def test_approve_defaults_to_paused_auto_tasks(operations_api):
    from backend.app.models import AutoTask, OperationPlan

    response = client.post(
        f"/api/operations/strategy-proposals/{operations_api['proposal_id']}/approve",
        json={},
        headers=operations_api["owner_headers"],
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plans_created"] == 7
    assert payload["auto_tasks_created"] == 1
    assert payload["auto_tasks_enabled"] is False

    with operations_api["session"]() as db:
        tasks = db.scalars(select(AutoTask)).all()
        plans = db.scalars(select(OperationPlan)).all()
        assert len(tasks) == 1
        assert tasks[0].status == "paused"
        assert tasks[0].next_run_at is None
        assert len(plans) == 7


def test_approve_can_skip_auto_task_creation(operations_api):
    from backend.app.models import AutoTask, OperationPlan

    response = client.post(
        f"/api/operations/strategy-proposals/{operations_api['proposal_id']}/approve",
        json={"create_auto_tasks": False},
        headers=operations_api["owner_headers"],
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plans_created"] == 7
    assert payload["auto_tasks_created"] == 0
    assert payload["auto_tasks_updated"] == 0
    assert payload["auto_tasks_enabled"] is False

    with operations_api["session"]() as db:
        assert db.scalars(select(AutoTask)).all() == []
        assert len(db.scalars(select(OperationPlan)).all()) == 7


def test_plan_patch_updates_owned_plan_and_hides_it_from_other_users(operations_api):
    from backend.app.models import OperationPlan

    with operations_api["session"]() as db:
        plan = OperationPlan(
            user_id=operations_api["owner_id"],
            platform_account_id=operations_api["owner_account_id"],
            plan_date=date(2026, 9, 4),
            content_direction="旧方向",
            topic="旧选题",
            planned_at=datetime(2026, 9, 4, 10, 0),
        )
        db.add(plan)
        db.commit()
        plan_id = plan.id

    response = client.patch(
        f"/api/operations/plans/{plan_id}",
        json={
            "content_direction": "新方向",
            "topic": "新选题",
            "planned_at": "2026-09-04T20:30:00",
            "status": "skipped",
        },
        headers=operations_api["owner_headers"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["content_direction"] == "新方向"
    assert response.json()["topic"] == "新选题"
    assert response.json()["status"] == "skipped"

    forbidden = client.patch(
        f"/api/operations/plans/{plan_id}",
        json={"topic": "越权修改"},
        headers=operations_api["other_headers"],
    )
    assert forbidden.status_code == 404

    with operations_api["session"]() as db:
        stored = db.get(OperationPlan, plan_id)
        assert stored is not None
        assert stored.topic == "新选题"
        assert stored.planned_at == datetime(2026, 9, 4, 20, 30)


def test_review_required_api_setting_is_the_service_publish_gate(operations_api):
    from backend.app.models import AutoTask
    from backend.app.services.scheduler_service import _auto_publish_allowed

    account_id = operations_api["owner_account_id"]
    task_response = client.post(
        "/api/auto-tasks",
        json={
            "name": "门禁验证任务",
            "keywords": ["测试主题"],
            "creator_account_id": account_id,
            "schedule_type": "manual",
        },
        headers=operations_api["owner_headers"],
    )
    assert task_response.status_code == 200, task_response.text
    task_id = task_response.json()["id"]

    with operations_api["session"]() as db:
        task = db.get(AutoTask, task_id)
        assert task is not None
        # No profile is safe by default.
        assert _auto_publish_allowed(db, task) is False

    base_profile = {
        "homepage_url": "",
        "positioning": "测试定位",
        "target_audience": "测试受众",
        "content_directions": ["方向甲"],
        "daily_post_count": 1,
        "daily_generate_count": 1,
        "daily_publish_count": 1,
        "preferred_times": ["20:30"],
        "goals": [],
        "tone": "自然",
        "forbidden_topics": [],
        "status": "active",
    }
    review_on = client.put(
        f"/api/operations/profiles/{account_id}",
        json={**base_profile, "review_required": True},
        headers=operations_api["owner_headers"],
    )
    assert review_on.status_code == 200, review_on.text
    with operations_api["session"]() as db:
        assert _auto_publish_allowed(db, db.get(AutoTask, task_id)) is False

    review_off = client.put(
        f"/api/operations/profiles/{account_id}",
        json={**base_profile, "review_required": False},
        headers=operations_api["owner_headers"],
    )
    assert review_off.status_code == 200, review_off.text
    with operations_api["session"]() as db:
        assert _auto_publish_allowed(db, db.get(AutoTask, task_id)) is True

