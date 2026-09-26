from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.identity import IdentityService
from app.application.retention import RetentionService
from app.persistence.retention import (
    ChallengeRecord,
    ChallengeScenario,
    NotificationRecord,
)
from app.persistence.scenarios import ScenarioVersion

pytestmark = pytest.mark.postgres


@pytest.fixture
def retention(database, scenario, clock):
    IdentityService(database).demo_login()
    with Session(database) as db, db.begin():
        row = db.get(ScenarioVersion, (scenario.id, scenario.version))
        row.created_at = clock.now
        db.add(
            ChallengeRecord(
                id="day",
                title="Учебные сутки",
                description="Практика",
                starts_at=clock.now,
                expires_at=clock.now + timedelta(hours=2),
                target=1,
            )
        )
        db.flush()
        db.add(
            ChallengeScenario(
                challenge_id="day", scenario_id=scenario.id, scenario_version=1
            )
        )
    return RetentionService(database, clock=clock)


def test_notifications_reconcile_concurrently_without_duplicates_or_read_reset(
    retention,
):
    with ThreadPoolExecutor(max_workers=4) as pool:
        pages = list(
            pool.map(lambda _: retention.notifications("demo-employee"), range(4))
        )
    assert all(page["unread_count"] == 2 for page in pages)
    item = pages[0]["items"][0]
    read = retention.mark_read("demo-employee", item["id"], True)
    assert read["read_at"]
    assert retention.mark_read("demo-employee", item["id"], True) == read
    assert retention.notifications("demo-employee")["unread_count"] == 1
    retention.mark_read("demo-employee", item["id"], False)
    assert retention.notifications("demo-employee")["unread_count"] == 2


def test_worker_ending_event_expiry_and_restart(retention, database, clock):
    assert retention.sweep() == 2
    clock.now += timedelta(hours=1)
    assert retention.sweep() == 1
    assert retention.sweep() == 0
    clock.now += timedelta(hours=1)
    restarted = RetentionService(database, clock=clock)
    page = restarted.notifications("demo-employee")
    assert page["unread_count"] == 1
    assert len([n for n in page["items"] if n["expired"]]) == 2
    assert restarted.challenges("demo-employee")["items"][0]["status"] == "expired"


def test_completed_sessions_count_once_and_achievement_notice_is_durable(
    retention, service, scenario, clock, database
):
    for index in range(2):
        view = service.start(
            scenario_id=scenario.id, scenario_version=1, employee_id="demo-employee"
        )
        service.decide(
            view.session.id,
            decision_id=f"help-{index}",
            node_id="start",
            choice_id="help",
            expected_sequence=0,
            employee_id="demo-employee",
        )
        service.decide(
            view.session.id,
            decision_id=f"finish-{index}",
            node_id="followup",
            choice_id="finish",
            expected_sequence=1,
            employee_id="demo-employee",
        )
    row = retention.challenges("demo-employee")["items"][0]
    assert row["progress"] == 1 and row["status"] == "completed"
    assert retention.challenges("someone-else")["items"][0]["progress"] == 0
    # Unlock timestamps come from the real DB clock, not the session fixture.
    clock.now = datetime.now(UTC)
    page = retention.notifications("demo-employee")
    assert any(n["kind"] == "achievement_unlocked" for n in page["items"])
    with Session(database) as db:
        assert len(db.scalars(select(NotificationRecord)).all()) == len(page["items"])


def test_retention_http_auth_ownership_validation_and_pagination(
    retention, database, monkeypatch
):
    from app.api.dependencies import get_engine
    from app.api.retention import get_retention_service
    from app.main import app

    monkeypatch.setenv("DEMO_AUTH_ENABLED", "true")
    app.dependency_overrides[get_engine] = lambda: database
    app.dependency_overrides[get_retention_service] = lambda: retention
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/challenges").status_code == 401
            assert client.get("/api/v1/notifications").status_code == 401
            token = client.post("/api/v1/auth/demo").json()["access_token"]
            client.headers["Authorization"] = "Bearer " + token
            page = client.get("/api/v1/notifications?limit=1").json()
            assert page["total"] == 2 and len(page["items"]) == 1
            nid = page["items"][0]["id"]
            assert client.put(
                f"/api/v1/notifications/{nid}/read", json={"read": True}
            ).json()["read_at"]
            assert (
                client.put(
                    f"/api/v1/notifications/{nid}/read", json={"read": "false"}
                ).status_code
                == 422
            )
            assert client.get("/api/v1/challenges?limit=0").status_code == 422
            assert client.get("/api/v1/challenges").json()["items"][0]["target"] == 1
            token = client.post(
                "/api/v1/auth/demo", json={"persona_id": "demo-north-02"}
            ).json()["access_token"]
            client.headers["Authorization"] = "Bearer " + token
            assert (
                client.put(
                    f"/api/v1/notifications/{nid}/read", json={"read": False}
                ).status_code
                == 404
            )
    finally:
        app.dependency_overrides.clear()


def test_publication_preserves_windows_and_requires_imported_scenarios(database):
    from pathlib import Path

    from app.persistence.scenarios import ScenarioRepository
    from app.retention_seed import publish
    from app.scenarios.loader import load_document

    with pytest.raises(ValueError, match="Import"):
        publish(database)
    with Session(database) as db, db.begin():
        for path in (Path(__file__).parents[3] / "scenarios" / "demo").glob("*.json"):
            ScenarioRepository(db).add(load_document(path))
    assert publish(database) == 2
    with Session(database) as db:
        before = [
            (r.id, r.starts_at, r.expires_at)
            for r in db.scalars(select(ChallengeRecord).order_by(ChallengeRecord.id))
        ]
    assert publish(database) == 0
    with Session(database) as db:
        after = [
            (r.id, r.starts_at, r.expires_at)
            for r in db.scalars(select(ChallengeRecord).order_by(ChallengeRecord.id))
        ]
    assert before == after


def test_delayed_reconciliation_does_not_resurrect_expired_events(retention, clock):
    clock.now += timedelta(days=8)
    assert retention.sweep() == 0
    page = retention.notifications("demo-employee")
    assert page["items"] == [] and page["unread_count"] == 0
