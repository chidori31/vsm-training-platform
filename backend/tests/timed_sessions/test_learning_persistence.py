from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.postgres


def login(client, persona="demo-employee"):
    response = client.post("/api/v1/auth/demo", json={"persona_id": persona})
    assert response.status_code == 200
    client.headers["Authorization"] = "Bearer " + response.json()["access_token"]


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


def start(client, scenario, key="learning"):
    response = client.post(
        "/api/v1/sessions",
        headers={"Idempotency-Key": key},
        json={"scenario_id": scenario.id, "scenario_version": scenario.version},
    )
    assert response.status_code == 201
    return response.json()["session"]["id"]


def decide(client, sid, node="start", choice="help", sequence=0):
    return client.post(
        f"/api/v1/sessions/{sid}/decisions",
        json={
            "decision_id": f"decision-{sequence}",
            "node_id": node,
            "choice_id": choice,
            "expected_sequence": sequence,
        },
    )


def complete(client, scenario, key="learning"):
    sid = start(client, scenario, key)
    assert decide(client, sid).status_code == 200
    assert decide(client, sid, "followup", "finish", 1).status_code == 200
    return sid


def test_debrief_routes_require_auth_ownership_and_completed_attempt(client, scenario):
    sid = start(client, scenario)
    response = client.get(f"/api/v1/sessions/{sid}/debrief")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "result_not_ready"
    login(client, "demo-north-02")
    assert client.get(f"/api/v1/sessions/{sid}/debrief").status_code == 404
    assert client.get("/api/v1/sessions/missing/debrief").status_code == 404
    client.headers.pop("Authorization")
    assert client.get(f"/api/v1/sessions/{sid}/debrief").status_code == 401
    assert client.get("/api/v1/analytics/me/competencies").status_code == 401


def test_debrief_exact_contract_persists_across_replay_and_new_engine(
    client, scenario, database, clock
):
    from app.api.dependencies import get_engine
    from app.api.sessions import get_session_service
    from app.application.sessions import SessionService
    from app.main import app
    from app.persistence.sessions import StoredSession

    sid = complete(client, scenario)
    response = client.get(f"/api/v1/sessions/{sid}/debrief")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "rule_version",
        "session_id",
        "scenario_id",
        "scenario_version",
        "title",
        "completed_at",
        "summary",
        "decisions",
    }
    assert body["summary"] == {
        "decision_count": 2,
        "timeout_count": 0,
        "loyalty_delta": 47,
        "safety_delta": 5,
    }
    first, second = body["decisions"]
    assert set(first) == {
        "sequence",
        "decision_id",
        "node_id",
        "node_text",
        "choice_id",
        "choice_text",
        "destination_id",
        "destination_text",
        "decided_at",
        "elapsed_seconds",
        "was_timeout",
        "explanation",
        "loyalty",
        "safety",
        "competencies",
        "alternatives",
        "suggestion",
        "pattern_codes",
        "assessment",
    }
    assert (first["loyalty"]["requested_delta"], first["loyalty"]["delta"]) == (60, 50)
    assert second["safety"]["delta"] == 0
    assert first["competencies"][0]["delta"] == 3
    assert first["assessment"]["status"] == "strong"
    assert first["alternatives"][0]["choice_id"] == "decline"
    assert decide(client, sid, "followup", "finish", 1).json()["outcome"] == "duplicate"
    assert client.get(f"/api/v1/sessions/{sid}/debrief").json() == body
    with Session(database) as db:
        stored = db.get(StoredSession, sid)
        assert stored.revision == 2 and len(stored.snapshot["decisions"]) == 2
        snapshot = stored.snapshot
    fresh = create_engine(database.url)
    app.dependency_overrides[get_engine] = lambda: fresh
    app.dependency_overrides[get_session_service] = lambda: SessionService(
        fresh, clock=clock
    )
    try:
        assert client.get(f"/api/v1/sessions/{sid}/debrief").json() == body
        analytics = client.get("/api/v1/analytics/me/competencies").json()
        assert analytics["decision_count"] == 2
        performance = analytics["performance"]
        assert performance["measured_decision_count"] == 2
        assert performance["average_decision_seconds"] == 0
        assert performance["best_loyalty"] == 97
        assert performance["best_safety"] == 55
        assert len(performance["weeks"]) == 1
        assert performance["weeks"][0]["completed_sessions"] == 1
        assert performance["weeks"][0]["average_loyalty"] == 97
        assert performance["weeks"][0]["average_safety"] == 55
        row = analytics["competencies"][0]
        assert (row["earned_points"], row["net_delta"], row["opportunities"]) == (
            3,
            3,
            1,
        )
        assert row["status"] == "insufficient_data"
        assert row["trend"][0]["session_id"] == sid
        with Session(fresh) as db:
            assert db.get(StoredSession, sid).snapshot == snapshot
    finally:
        fresh.dispose()


def test_debrief_get_expires_due_node_once_and_explains_predeadline_choices(
    client, scenario, clock
):
    sid = start(client, scenario)
    assert decide(client, sid).status_code == 200
    clock.now += timedelta(seconds=11)
    response = client.get(f"/api/v1/sessions/{sid}/debrief")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["timeout_count"] == 1
    final = body["decisions"][-1]
    assert final["was_timeout"] and final["elapsed_seconds"] == 11
    assert final["assessment"]["status"] == "critical_error"
    assert final["assessment"]["is_critical"]
    assert final["pattern_codes"] == ["timeout", "safety_loss"]
    assert final["alternatives"][0]["choice_id"] == "finish"
    assert final["alternatives"][0]["available"]
    assert "срок" in final["suggestion"]["text"]
    assert client.get(f"/api/v1/sessions/{sid}/debrief").json() == body
    analytics = client.get("/api/v1/analytics/me/competencies").json()
    assert analytics["timeout_count"] == 1 and analytics["decision_count"] == 2
    assert analytics["performance"]["measured_decision_count"] == 1
    assert analytics["performance"]["average_decision_seconds"] == 0
    assert analytics["performance"]["weeks"][0]["timeout_count"] == 1


def test_analytics_keeps_owner_history_without_pagination_and_old_route_compatible(
    client, scenario
):
    empty = client.get("/api/v1/analytics/me/competencies")
    assert empty.status_code == 200
    assert empty.json() == {
        "rule_version": 1,
        "total_sessions": 0,
        "completed_sessions": 0,
        "active_sessions": 0,
        "decision_count": 0,
        "timeout_count": 0,
        "competencies": [],
        "strengths": [],
        "weaknesses": [],
        "patterns": [],
        "scenarios": [],
        "performance": {
            "measured_decision_count": 0,
            "average_decision_seconds": None,
            "best_loyalty": None,
            "best_safety": None,
            "weeks": [],
        },
    }
    for index in range(3):
        complete(client, scenario, f"attempt-{index}")
    active = start(client, scenario, "active")
    assert decide(client, active).status_code == 200
    body = client.get("/api/v1/analytics/me/competencies?limit=1").json()
    assert (
        body["total_sessions"],
        body["completed_sessions"],
        body["active_sessions"],
    ) == (4, 3, 1)
    assert body["decision_count"] == 6
    assert body["performance"]["measured_decision_count"] == 6
    assert body["performance"]["weeks"][0]["completed_sessions"] == 3
    row = body["competencies"][0]
    assert (row["earned_points"], row["opportunities"], row["practiced_sessions"]) == (
        9,
        3,
        3,
    )
    assert row["status"] == "strength" and body["strengths"] == ["communication"]
    assert [p["cumulative_points"] for p in row["trend"]] == [3, 6, 9]
    assert len(row["trend"]) == 3
    assert body["patterns"][0]["count"] == 3 and body["patterns"][0]["recurring"]
    assert client.get("/api/v1/analytics/me").json()["decision_count"] == 7
    assert client.get("/api/v1/profiles/me/progress").json()["competencies"] == [
        {"competency_id": "communication", "value": 9}
    ]
    login(client, "demo-north-02")
    assert client.get("/api/v1/analytics/me/competencies").json() == empty.json()


def test_analytics_is_read_only_even_when_active_deadline_has_passed(
    client, scenario, clock, database
):
    from app.persistence.sessions import StoredSession

    sid = start(client, scenario)
    clock.now += timedelta(seconds=16)
    analytics = client.get("/api/v1/analytics/me/competencies")
    assert analytics.status_code == 200
    body = analytics.json()
    assert body["active_sessions"] == 1 and body["decision_count"] == 0
    assert body["competencies"] == []
    assert body["scenarios"][0]["average_duration_seconds"] is None
    with Session(database) as db:
        row = db.scalar(select(StoredSession).where(StoredSession.id == sid))
        assert row.revision == 0 and row.state == "active"


def test_openapi_includes_typed_learning_contracts(client):
    schema = client.get("/openapi.json").json()
    for path, model in [
        ("/api/v1/sessions/{session_id}/debrief", "DebriefResponse"),
        ("/api/v1/analytics/me/competencies", "LearningAnalyticsResponse"),
    ]:
        operation = schema["paths"][path]["get"]
        assert operation["security"] == [{"BearerAuth": []}]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith("/" + model)
