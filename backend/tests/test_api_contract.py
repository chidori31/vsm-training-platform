import pytest
from fastapi.testclient import TestClient

from app.main import app


def test_versioned_openapi_describes_gameplay_and_contract():
    schema = TestClient(app).get("/openapi.json").json()
    paths = schema["paths"]
    for path in (
        "/api/v1/auth/demo",
        "/api/v1/profiles/me",
        "/api/v1/scenarios",
        "/api/v1/sessions",
        "/api/v1/sessions/{session_id}",
        "/api/v1/sessions/{session_id}/decisions",
        "/api/v1/sessions/{session_id}/result",
        "/api/v1/results",
        "/api/v1/achievements",
        "/api/v1/leaderboard",
        "/api/v1/analytics/me",
        "/api/v1/integrations/hr-lms/training-results",
    ):
        assert path in paths
    assert "/sessions" not in paths
    assert "BearerAuth" in schema["components"]["securitySchemes"]
    assert paths["/api/v1/sessions"]["post"]["responses"]["422"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("/ErrorResponse")


@pytest.mark.parametrize("path", ["/missing", "/sessions"])
def test_unknown_and_retired_routes_have_uniform_errors(path):
    response = TestClient(app).get(path)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_missing_token_is_401_even_without_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    response = TestClient(app).get("/api/v1/profiles/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "unauthorized"


def test_demo_auth_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DEMO_AUTH_ENABLED", raising=False)
    response = TestClient(app).post("/api/v1/auth/demo")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "demo_auth_disabled"


def test_integration_contract_validates_without_sending_or_saving():
    client = TestClient(app)
    contract = client.get("/api/v1/integrations/hr-lms/contract")
    assert contract.status_code == 200
    assert contract.json()["status"] == "contract_only"
    payload = {
        "contract_version": 1,
        "event_id": "synthetic-event",
        "employee_reference": "synthetic-employee",
        "session_id": "synthetic-session",
        "scenario_id": "synthetic-scenario",
        "scenario_version": 1,
        "completed_at": "2026-09-26T10:00:00Z",
        "passenger_loyalty": 60,
        "safety_rating": 80,
        "competencies": [],
    }
    response = client.post("/api/v1/integrations/hr-lms/training-results", json=payload)
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "integration_not_configured"
    response = client.post(
        "/api/v1/integrations/hr-lms/training-results",
        json={**payload, "safety_rating": 101, "secret": "do-not-reflect"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "do-not-reflect" not in response.text
