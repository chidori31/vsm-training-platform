from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.gamification import SessionReward
from app.persistence.sessions import StoredSession

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
            response = client.post("/api/v1/auth/demo")
            assert response.headers["cache-control"] == "no-store"
            client.headers["Authorization"] = (
                "Bearer " + response.json()["access_token"]
            )
            yield client
    finally:
        app.dependency_overrides.clear()


def start(client, scenario):
    response = client.post(
        "/api/v1/sessions",
        headers={"Idempotency-Key": "hardening"},
        json={"scenario_id": scenario.id, "scenario_version": 1},
    )
    assert response.status_code == 201
    return response.json()


def command(**overrides):
    return (
        dict(decision_id="one", node_id="start", choice_id="help", expected_sequence=0)
        | overrides
    )


def test_hostile_api_actions_leave_session_usable_and_cannot_set_scores(
    client, scenario, database
):
    initial = start(client, scenario)
    sid = initial["session"]["id"]
    for payload, status in (
        (command(loyalty=999), 422),
        (command(safety=999), 422),
        (command(xp=999), 422),
        (command(effects=[{"delta": 999}]), 422),
        (command(destination="done"), 422),
        (command(employee_id="demo-north-02"), 422),
        (command(now="2099-01-01"), 422),
        (command(node_id="done"), 409),
        (command(choice_id="finish"), 409),
        (command(choice_id="__timeout__"), 409),
        (command(expected_sequence=True), 422),
        (command(decision_id="timeout:forged"), 422),
        (command(decision_id="bad\x00id"), 422),
    ):
        response = client.post(f"/api/v1/sessions/{sid}/decisions", json=payload)
        assert response.status_code == status
        with Session(database) as db:
            row = db.get(StoredSession, sid)
            assert row.snapshot == initial["session"] and row.revision == 0
    assert (
        client.post(f"/api/v1/sessions/{sid}/decisions", json=command()).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/sessions/{sid}/decisions",
            json=command(
                decision_id="two",
                node_id="followup",
                choice_id="finish",
                expected_sequence=1,
            ),
        ).status_code
        == 200
    )
    assert len(client.get(f"/api/v1/sessions/{sid}/debrief").json()["decisions"]) == 2


def test_clients_cannot_write_scores_results_profile_or_leaderboards(client, scenario):
    for path in (
        "/leaderboard",
        "/leaderboard/organization",
        "/profiles/me",
        "/profiles/me/progress",
        "/results",
    ):
        for method in ("POST", "PUT", "PATCH"):
            assert (
                client.request(
                    method,
                    "/api/v1" + path,
                    json={"xp": 999, "score": 999, "safety": 999},
                ).status_code
                == 405
            )
    response = client.post(
        "/api/v1/sessions",
        headers={"Idempotency-Key": "forged"},
        json={
            "scenario_id": scenario.id,
            "scenario_version": 1,
            "initial_scores": {"safety": 999},
        },
    )
    assert response.status_code == 422
    assert client.get("/api/v1/profiles/me/progress?xp=999").json()["xp"] == 0
    assert (
        client.get("/api/v1/leaderboard/organization?score=999&xp=999").json()["items"]
        == []
    )


def test_concurrent_terminal_retries_award_exactly_once(client, scenario, database):
    sid = start(client, scenario)["session"]["id"]
    assert (
        client.post(f"/api/v1/sessions/{sid}/decisions", json=command()).status_code
        == 200
    )
    barrier = Barrier(3)
    finish = command(
        decision_id="two", node_id="followup", choice_id="finish", expected_sequence=1
    )

    def submit():
        barrier.wait(timeout=10)
        return client.post(f"/api/v1/sessions/{sid}/decisions", json=finish)

    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(lambda _: submit(), range(3)))
    assert sorted(response.json()["outcome"] for response in responses) == [
        "accepted",
        "duplicate",
        "duplicate",
    ]
    progress = client.get("/api/v1/profiles/me/progress").json()
    assert progress["xp"] == 120 and progress["completed_sessions"] == 1
    with Session(database) as db:
        assert len(db.scalars(select(SessionReward)).all()) == 1
        assert db.get(StoredSession, sid).revision == 2


def test_terminal_timeout_and_http_decision_race_settle_one_result(
    client, scenario, service, clock, database
):
    sid = start(client, scenario)["session"]["id"]
    client.post(f"/api/v1/sessions/{sid}/decisions", json=command())
    clock.now += timedelta(seconds=10)
    barrier = Barrier(2)

    def worker():
        barrier.wait(timeout=10)
        return service.expire_due()

    def player():
        barrier.wait(timeout=10)
        return client.post(
            f"/api/v1/sessions/{sid}/decisions",
            json=command(
                decision_id="late",
                node_id="followup",
                choice_id="finish",
                expected_sequence=1,
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        job = pool.submit(worker)
        response = pool.submit(player).result(timeout=15)
        job.result(timeout=15)
    assert response.status_code == 409
    state = client.get(f"/api/v1/sessions/{sid}").json()["session"]
    assert state["status"] == "completed"
    assert [d["choice_id"] for d in state["decisions"]] == ["help", "__timeout__"]
    before = client.get("/api/v1/profiles/me/progress").json()["xp"]
    for _ in range(2):
        client.post(
            f"/api/v1/sessions/{sid}/decisions",
            json=command(
                decision_id="late",
                node_id="followup",
                choice_id="finish",
                expected_sequence=1,
            ),
        )
    assert client.get("/api/v1/profiles/me/progress").json()["xp"] == before
    with Session(database) as db:
        assert len(db.scalars(select(SessionReward)).all()) == 1
