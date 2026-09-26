import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    "extra,value",
    [
        ("now", "2020-01-01T00:00:00Z"),
        ("effects", []),
        ("scores", {}),
        ("scoring_policy", {}),
    ],
)
def test_decision_request_cannot_supply_server_authority(extra, value):
    from app.api.sessions import get_session_service
    from app.main import app

    app.dependency_overrides[get_session_service] = lambda: None
    try:
        with TestClient(app) as client:
            response = client.post(
                "/sessions/s/decisions",
                json={
                    "decision_id": "d",
                    "node_id": "start",
                    "choice_id": "next",
                    "expected_sequence": 0,
                    extra: value,
                },
            )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "changes",
    [
        {"expected_sequence": True},
        {"expected_sequence": "0"},
        {"expected_sequence": -1},
        {"decision_id": "timeout:s:0"},
    ],
)
def test_decision_request_rejects_invalid_sequence_and_reserved_id(changes):
    from app.api.sessions import get_session_service
    from app.main import app

    app.dependency_overrides[get_session_service] = lambda: None
    try:
        with TestClient(app) as client:
            response = client.post(
                "/sessions/s/decisions",
                json={
                    "decision_id": "d",
                    "node_id": "start",
                    "choice_id": "next",
                    "expected_sequence": 0,
                }
                | changes,
            )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_session_start_disallows_client_initial_scores():
    from app.api.sessions import get_session_service
    from app.main import app

    app.dependency_overrides[get_session_service] = lambda: None
    try:
        with TestClient(app) as client:
            response = client.post(
                "/sessions",
                json={
                    "scenario_id": "demo",
                    "scenario_version": 1,
                    "employee_id": "synthetic",
                    "initial_scores": {},
                },
            )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("database_url", [None, "not-a-database-url"])
def test_session_endpoint_without_valid_database_is_503(monkeypatch, database_url):
    from app.main import app

    if database_url is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DATABASE_URL", database_url)
    with TestClient(app) as client:
        response = client.get("/sessions/s")
    assert response.status_code == 503
