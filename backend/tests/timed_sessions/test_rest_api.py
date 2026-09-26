from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.postgres


@pytest.fixture
def api(database, service, monkeypatch):
    from app.api.dependencies import get_engine
    from app.api.sessions import get_session_service
    from app.main import app

    monkeypatch.setenv("DEMO_AUTH_ENABLED", "true")
    app.dependency_overrides[get_engine] = lambda: database
    app.dependency_overrides[get_session_service] = lambda: service
    try:
        with TestClient(app) as client:
            login = client.post("/api/v1/auth/demo")
            assert login.status_code == 200
            client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
            yield client
    finally:
        app.dependency_overrides.clear()


def start(api, scenario, key="start-1"):
    return api.post(
        "/api/v1/sessions",
        headers={"Idempotency-Key": key},
        json={"scenario_id": scenario.id, "scenario_version": 1},
    )


def decision(api, sid, *, node="start", choice="help", sequence=0, key="choice-1"):
    return api.post(
        f"/api/v1/sessions/{sid}/decisions",
        json={
            "decision_id": key,
            "node_id": node,
            "choice_id": choice,
            "expected_sequence": sequence,
        },
    )


def test_full_gameplay_api_uses_domain_and_persisted_read_models(api, scenario, clock):
    created = start(api, scenario)
    assert created.status_code == 201
    body = created.json()
    sid = body["session"]["id"]
    assert body["session"]["employee_id"] == api.get("/api/v1/profiles/me").json()["id"]
    assert body["current_node"]["id"] == "start"
    assert [c["id"] for c in body["available_choices"]] == ["help", "decline"]
    assert body["expected_sequence"] == 0
    not_ready = api.get(f"/api/v1/sessions/{sid}/result")
    assert not_ready.status_code == 409
    assert not_ready.json()["error"]["code"] == "result_not_ready"
    clock.now += timedelta(seconds=1)
    accepted = decision(api, sid)
    assert accepted.status_code == 200
    assert accepted.json()["outcome"] == "accepted"
    assert (
        accepted.json()["session"]["decisions"][0]["score_changes"][0]["after"] == 100
    )
    duplicate = decision(api, sid)
    assert duplicate.json()["outcome"] == "duplicate"
    conflict = decision(api, sid, key="stale")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "decision_rejected"
    assert conflict.json()["data"]["expected_sequence"] == 1
    clock.now += timedelta(seconds=2)
    assert (
        decision(
            api, sid, node="followup", choice="finish", sequence=1, key="choice-2"
        ).status_code
        == 200
    )
    result = api.get(f"/api/v1/sessions/{sid}/result")
    assert result.status_code == 200
    result = result.json()
    assert result["summary"]["duration_seconds"] == 3
    assert result["summary"]["decision_count"] == 2
    assert result["summary"]["passenger_loyalty"] == 97
    assert result["summary"]["safety_rating"] == 55
    history = api.get(f"/api/v1/sessions/{sid}/decisions?limit=1&offset=1").json()
    assert history["total"] == 2 and len(history["items"]) == 1
    assert history["items"][0]["id"] == "choice-2"
    results = api.get("/api/v1/results?limit=1").json()
    assert results["total"] == 1 and results["items"][0]["session_id"] == sid
    analytics = api.get("/api/v1/analytics/me").json()
    assert analytics["completed_sessions"] == 1
    assert analytics["decision_count"] == 2 and analytics["timeout_count"] == 0
    assert analytics["average_passenger_loyalty"] == 97
    assert analytics["competencies"] == [
        {"competency_id": "communication", "average": 3}
    ]
    leaderboard = api.get(
        "/api/v1/leaderboard",
        params={
            "scenario_id": scenario.id,
            "scenario_version": 1,
            "metric": "safety_rating",
        },
    ).json()
    assert leaderboard["total"] == 1
    assert leaderboard["items"][0]["score"] == 55
    assert leaderboard["items"][0]["session_id"] == sid


def test_start_is_idempotent_under_concurrent_http_requests(api, scenario, database):
    from app.persistence.sessions import StoredSession

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: start(api, scenario), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 201]
    assert len({r.json()["session"]["id"] for r in responses}) == 1
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(StoredSession)) == 1
    conflict = api.post(
        "/api/v1/sessions",
        headers={"Idempotency-Key": "start-1"},
        json={"scenario_id": scenario.id, "scenario_version": 2},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


def test_auth_tokens_are_hashed_expire_and_cannot_select_another_owner(api, database):
    from app.persistence.identity import DemoToken

    token = api.headers["Authorization"].removeprefix("Bearer ")
    with Session(database) as db, db.begin():
        record = db.get(DemoToken, sha256(token.encode()).hexdigest())
        assert record is not None
        assert token != record.token_hash
        record.expires_at = db.scalar(select(func.clock_timestamp())) - timedelta(
            seconds=1
        )
    response = api.get("/api/v1/profiles/me")
    assert response.status_code == 401
    assert token not in response.text


def test_foreign_sessions_are_hidden_for_all_gameplay_endpoints(api, service, scenario):
    foreign = service.start(
        scenario_id=scenario.id, scenario_version=1, employee_id="other"
    )
    sid = foreign.session.id
    for suffix in ("", "/result", "/decisions"):
        response = api.get(f"/api/v1/sessions/{sid}{suffix}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "session_not_found"
    assert decision(api, sid).status_code == 404
    assert api.get("/api/v1/results").json()["total"] == 0
    assert api.get("/api/v1/analytics/me").json()["total_sessions"] == 0


def test_catalog_pagination_validation_and_required_start_key(api, scenario):
    catalog = api.get("/api/v1/scenarios?limit=1&offset=0").json()
    assert catalog["total"] == 1 and catalog["items"][0]["id"] == scenario.id
    assert api.get("/api/v1/scenarios?offset=1").json()["items"] == []
    version = api.get(f"/api/v1/scenarios/{scenario.id}/versions/1")
    assert version.status_code == 200 and version.json()["start_node_id"] == "start"
    assert api.get(f"/api/v1/scenarios/{scenario.id}/versions/2").status_code == 404
    for query in ("limit=0", "limit=101", "offset=-1", "offset=word"):
        response = api.get("/api/v1/scenarios?" + query)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
    assert (
        api.post(
            "/api/v1/sessions", json={"scenario_id": scenario.id, "scenario_version": 1}
        ).status_code
        == 422
    )
    assert (
        api.post(
            "/api/v1/sessions",
            headers={"Idempotency-Key": "forged-owner"},
            json={
                "scenario_id": scenario.id,
                "scenario_version": 1,
                "employee_id": "other",
            },
        ).status_code
        == 422
    )
    assert api.get("/api/v1/achievements").json()["total"] == 4
    assert api.get("/api/v1/profiles/me/achievements").json()["total"] == 0


def test_timeout_error_uses_same_envelope_and_commits_state(
    api, scenario, clock, database
):
    from app.persistence.sessions import StoredSession

    sid = start(api, scenario).json()["session"]["id"]
    clock.now += timedelta(seconds=15)
    response = decision(api, sid)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "decision_timed_out"
    assert response.json()["data"]["current_node"]["id"] == "recovery"
    assert [c["id"] for c in response.json()["data"]["available_choices"]] == [
        "recover"
    ]
    with Session(database) as db:
        assert db.get(StoredSession, sid).revision == 1


def test_achievement_catalog_and_unlocks_read_persisted_data(api, database, scenario):
    from app.persistence.achievements import AchievementRecord, AchievementUnlockRecord

    profile = api.get("/api/v1/profiles/me").json()
    sid = start(api, scenario).json()["session"]["id"]
    assert decision(api, sid, choice="decline").status_code == 200
    with Session(database) as db, db.begin():
        db.add(
            AchievementRecord(
                id="synthetic-achievement",
                version=1,
                name="Synthetic achievement",
                description="Test-only catalog fixture",
                condition={"predicates": []},
            )
        )
        db.flush()
        db.add(
            AchievementUnlockRecord(
                id="synthetic-unlock",
                employee_id=profile["id"],
                achievement_id="synthetic-achievement",
                achievement_version=1,
                session_id=sid,
                unlocked_at=db.scalar(select(func.clock_timestamp())),
            )
        )
    catalog = api.get("/api/v1/achievements?limit=1&offset=4").json()
    assert catalog["total"] == 5
    assert catalog["items"][0]["name"] == "Synthetic achievement"
    unlocks = api.get("/api/v1/profiles/me/achievements").json()
    assert unlocks["total"] == 1
    assert unlocks["items"][0]["session_id"] == sid
    assert api.get("/api/v1/achievements?offset=5").json()["items"] == []


def test_leaderboard_uses_best_completed_attempt_per_scale_and_tied_ranks(
    api,
    database,
    scenario,
    service,
    clock,
):
    from app.persistence.identity import UserProfile

    with Session(database) as db, db.begin():
        db.add_all(
            [
                UserProfile(id="second", display_name="Second"),
                UserProfile(id="third", display_name="Third"),
            ]
        )

    def finish(employee, timeout=False, decline=False):
        view = service.start(
            scenario_id=scenario.id, scenario_version=1, employee_id=employee
        )
        sid = view.session.id
        service.decide(
            sid,
            decision_id="one",
            node_id="start",
            choice_id="decline" if decline else "help",
            expected_sequence=0,
        )
        if not decline:
            if timeout:
                clock.now += timedelta(seconds=10)
                service.get(sid)
            else:
                service.decide(
                    sid,
                    decision_id="two",
                    node_id="followup",
                    choice_id="finish",
                    expected_sequence=1,
                )
        clock.now += timedelta(seconds=1)
        return sid

    safe_id = finish("demo-employee")
    loyal_id = finish("demo-employee", timeout=True)
    finish("second")
    finish("third", decline=True)
    start(api, scenario, "still-active")
    params = {"scenario_id": scenario.id, "scenario_version": 1}
    safety = api.get("/api/v1/leaderboard", params=params).json()
    assert safety["total"] == 3
    assert [item["rank"] for item in safety["items"]] == [1, 1, 3]
    assert safety["items"][0]["session_id"] == safe_id
    loyalty = api.get(
        "/api/v1/leaderboard", params={**params, "metric": "passenger_loyalty"}
    ).json()
    assert loyalty["items"][0]["session_id"] == loyal_id
    assert loyalty["items"][0]["score"] == 100
    assert (
        api.get(
            "/api/v1/leaderboard", params={**params, "limit": 1, "offset": 1}
        ).json()["items"][0]["rank"]
        == 1
    )
    analytics = api.get("/api/v1/analytics/me").json()
    assert analytics["total_sessions"] == 3 and analytics["completed_sessions"] == 2
    assert analytics["active_sessions"] == 1 and analytics["timeout_count"] == 1
    assert analytics["average_safety_rating"] == 47.5


def test_failed_start_does_not_reserve_key_and_replay_survives_service_restart(
    api,
    database,
    scenario,
    clock,
):
    from app.api.sessions import get_session_service
    from app.application.sessions import SessionService
    from app.main import app

    response = api.post(
        "/api/v1/sessions",
        headers={"Idempotency-Key": "retry"},
        json={"scenario_id": scenario.id, "scenario_version": 99},
    )
    assert response.status_code == 404
    created = start(api, scenario, "retry")
    assert created.status_code == 201
    app.dependency_overrides[get_session_service] = lambda: SessionService(
        database, clock=clock
    )
    replay = start(api, scenario, "retry")
    assert replay.status_code == 200
    assert replay.json()["session"]["id"] == created.json()["session"]["id"]


def test_unknown_token_and_empty_analytics(api):
    empty = api.get("/api/v1/analytics/me").json()
    assert empty["total_sessions"] == 0
    assert empty["average_safety_rating"] is None
    assert empty["competencies"] == []
    response = api.get(
        "/api/v1/profiles/me", headers={"Authorization": "Bearer invalid"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_start_rejects_scenario_identifier_beyond_storage_contract(api):
    response = api.post(
        "/api/v1/sessions",
        headers={"Idempotency-Key": "long-scenario"},
        json={"scenario_id": "s" * 65, "scenario_version": 1},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
