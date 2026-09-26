from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.postgres


@pytest.fixture
def client(database, service, monkeypatch):
    from app.api.dependencies import get_engine
    from app.api.sessions import get_session_service
    from app.main import app

    monkeypatch.setenv("DEMO_AUTH_ENABLED", "true")
    app.dependency_overrides[get_engine] = lambda: database
    app.dependency_overrides[get_session_service] = lambda: service
    try:
        with TestClient(app) as client:
            login(client)
            yield client
    finally:
        app.dependency_overrides.clear()


def login(client, persona="demo-employee"):
    result = client.post("/api/v1/auth/demo", json={"persona_id": persona})
    assert result.status_code == 200
    client.headers["Authorization"] = "Bearer " + result.json()["access_token"]
    return result.json()["profile"]["id"]


def finish(service, scenario, clock, employee, key):
    started, _ = service.start_idempotent(
        scenario_id=scenario.id, scenario_version=1, employee_id=employee, key=key
    )
    sid = started.session.id
    service.decide(
        sid,
        node_id="start",
        choice_id="help",
        decision_id=f"{key}-1",
        expected_sequence=0,
    )
    result = service.decide(
        sid,
        node_id="followup",
        choice_id="finish",
        decision_id=f"{key}-2",
        expected_sequence=1,
    )
    return result.view.session


def test_completed_reward_persists_once_with_unlocks_and_result_ownership(
    client, service, scenario, clock, database
):
    from app.persistence.gamification import SessionReward

    initial = client.get("/api/v1/profiles/me/progress")
    assert initial.status_code == 200
    assert initial.json()["xp"] == 0
    result = finish(service, scenario, clock, "demo-employee", "reward")
    for _ in range(2):
        service.decide(
            result.id,
            node_id="followup",
            choice_id="finish",
            decision_id="reward-2",
            expected_sequence=1,
        )
        progress = client.get(
            "/api/v1/profiles/me/progress", params={"session_id": result.id}
        ).json()
        assert progress["xp"] == 120 and progress["level"] == 2
        assert progress["competencies"] == [
            {"competency_id": "communication", "value": 3}
        ]
        assert progress["reward"]["xp"] == 120
        assert progress["reward"]["unlocks"] == ["communication-growth"]
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(SessionReward)) == 1
    login(client, "demo-north-02")
    assert (
        client.get(
            "/api/v1/profiles/me/progress", params={"session_id": result.id}
        ).json()["reward"]
        is None
    )


def test_leaderboard_uses_results_and_hierarchy_with_global_ties(
    client, service, scenario, clock
):
    for index, persona in enumerate(
        [
            "demo-employee",
            "demo-north-02",
            "demo-north-03",
            "demo-south-04",
            "demo-other-05",
        ]
    ):
        login(client, persona)
        finish(service, scenario, clock, persona, f"board-{index}")
    login(client)
    for scope, expected in [("brigade", 2), ("depot", 3), ("company", 4)]:
        response = client.get(
            "/api/v1/leaderboard/organization",
            params={"scope": scope, "limit": 1, "offset": 1},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == expected
        assert len(body["items"]) == 1 and body["items"][0]["rank"] == 1
        assert body["items"][0]["xp"] == 120
        assert body["items"][0]["employee_id"] != "demo-other-05"
    assert client.get("/api/v1/leaderboard/organization?scope=world").status_code == 422


def test_concurrent_completions_serialize_profile_awards(
    client, service, scenario, clock, database
):
    from app.persistence.achievements import AchievementUnlockRecord

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(
            pool.map(
                lambda i: finish(
                    service, scenario, clock, "demo-employee", f"parallel-{i}"
                ),
                range(3),
            )
        )
    assert len({s.id for s in results}) == 3
    progress = client.get("/api/v1/profiles/me/progress").json()
    assert progress["xp"] == 360 and progress["level"] == 3
    assert progress["completed_sessions"] == 3
    assert sum(a["unlocked"] for a in progress["achievements"]) == 3
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(AchievementUnlockRecord)) == 3


def test_concurrent_terminal_starts_settle_without_profile_lock_upgrade(
    client, service, database, monkeypatch
):
    from app.application import sessions
    from app.persistence.gamification import SessionReward
    from app.persistence.scenarios import ScenarioRepository
    from app.scenarios.schema import ScenarioDocument

    document = ScenarioDocument.model_validate(
        {
            "schema_version": 1,
            "id": "instant-terminal",
            "version": 1,
            "title": "Synthetic terminal start",
            "start_node_id": "done",
            "nodes": ({"id": "done", "text": "Finished", "terminal": True},),
        }
    )
    with Session(database) as db, db.begin():
        ScenarioRepository(db).add(document)
    original = sessions.settle_profile
    barrier = Barrier(2, timeout=10)

    def synchronized_settlement(db, employee):
        # Both start-key FKs already hold KEY SHARE on the same profile.
        barrier.wait()
        return original(db, employee)

    with monkeypatch.context() as patch:
        patch.setattr(sessions, "settle_profile", synchronized_settlement)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda i: service.start_idempotent(
                        scenario_id=document.id,
                        scenario_version=1,
                        employee_id="demo-employee",
                        key=f"instant-{i}",
                    ),
                    range(2),
                )
            )
    assert len({view.session.id for view, _ in results}) == 2
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(SessionReward)) == 2
    progress = client.get("/api/v1/profiles/me/progress").json()
    assert progress["completed_sessions"] == 2 and progress["xp"] == 0


def test_worker_timeout_settles_zero_xp_and_breaks_streak(
    client, service, scenario, clock
):
    finish(service, scenario, clock, "demo-employee", "before")
    service.start_idempotent(
        scenario_id=scenario.id,
        scenario_version=1,
        employee_id="demo-employee",
        key="late",
    )
    clock.now += timedelta(seconds=16)
    assert service.expire_due() == 1
    # Recovery node still needs a user choice: no award until terminal.
    assert client.get("/api/v1/profiles/me/progress").json()["completed_sessions"] == 1
    # A new unsafe completed critical decision also breaks the series.
    start, _ = service.start_idempotent(
        scenario_id=scenario.id,
        scenario_version=1,
        employee_id="demo-employee",
        key="unsafe",
    )
    service.decide(
        start.session.id,
        node_id="start",
        choice_id="decline",
        decision_id="bad",
        expected_sequence=0,
    )
    progress = client.get("/api/v1/profiles/me/progress").json()
    assert progress["xp"] == 140
    assert (
        next(a for a in progress["achievements"] if a["id"] == "critical-streak")[
            "current"
        ]
        == 0
    )


def test_demo_persona_is_allowlisted_and_routes_require_auth(client, monkeypatch):
    assert (
        client.post(
            "/api/v1/auth/demo", json={"persona_id": "arbitrary-person"}
        ).status_code
        == 422
    )
    assert client.post("/api/v1/auth/demo", json={"xp": 999}).status_code == 422
    client.headers.pop("Authorization")
    assert client.get("/api/v1/profiles/me/progress").status_code == 401
    assert client.get("/api/v1/leaderboard/organization").status_code == 401
    monkeypatch.setenv("DEMO_AUTH_ENABLED", "false")
    assert client.get("/api/v1/auth/demo/personas").status_code == 404


def test_reward_failure_rolls_back_terminal_transition(
    client, service, scenario, database, monkeypatch
):
    from app.application import sessions
    from app.persistence.gamification import SessionReward
    from app.persistence.sessions import StoredSession

    original = sessions.settle_profile

    def broken(db, employee):
        original(db, employee)
        raise RuntimeError("Synthetic award failure")

    started, _ = service.start_idempotent(
        scenario_id=scenario.id,
        scenario_version=1,
        employee_id="demo-employee",
        key="rollback",
    )
    monkeypatch.setattr(sessions, "settle_profile", broken)
    with pytest.raises(RuntimeError):
        service.decide(
            started.session.id,
            node_id="start",
            choice_id="decline",
            decision_id="failed",
            expected_sequence=0,
        )
    with Session(database) as db:
        assert db.get(StoredSession, started.session.id).state == "active"
        assert db.scalar(select(func.count()).select_from(SessionReward)) == 0


def test_backfill_verified_legacy_results_is_idempotent(
    client, service, scenario, clock, monkeypatch
):
    from app.application import sessions

    with monkeypatch.context() as patch:
        patch.setattr(sessions, "settle_profile", lambda *args: None)
        result = finish(service, scenario, clock, "demo-employee", "legacy")
    for _ in range(2):
        body = client.get(
            "/api/v1/profiles/me/progress", params={"session_id": result.id}
        ).json()
        assert body["xp"] == 120 and body["completed_sessions"] == 1
        assert body["reward"]["unlocks"] == ["communication-growth"]


def test_terminal_worker_award_needs_no_browser(
    client, service, scenario, database, clock
):
    from pathlib import Path

    from app.persistence.gamification import SessionReward
    from app.persistence.scenarios import ScenarioRepository
    from app.scenarios.schema import ScenarioDocument

    document = ScenarioDocument.model_validate_json(
        (
            Path(__file__).parents[3] / "scenarios" / "demo" / "medical-incident.json"
        ).read_text(encoding="utf-8")
    )
    with Session(database) as db, db.begin():
        ScenarioRepository(db).add(document)
    view, _ = service.start_idempotent(
        scenario_id=document.id,
        scenario_version=1,
        employee_id="demo-employee",
        key="worker-only",
    )
    clock.now += timedelta(seconds=21)
    assert service.expire_due() == 1
    with Session(database) as db:
        reward = db.get(SessionReward, view.session.id)
        assert reward.xp == 0 and reward.critical == [False]
        assert reward.competencies == {}


def test_unassigned_profiles_never_join_an_unassigned_leaderboard(client, database):
    from app.persistence.identity import UserProfile

    with Session(database) as db, db.begin():
        row = db.get(UserProfile, "demo-employee")
        row.company_id = row.depot_id = row.brigade_id = None
    body = client.get("/api/v1/leaderboard/organization").json()
    assert not body["assigned"] and body["items"] == [] and body["total"] == 0
