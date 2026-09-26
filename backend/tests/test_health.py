from fastapi.testclient import TestClient

from app.main import app


def test_health_is_available_without_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_without_configuration_returns_service_unavailable(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"


def test_readiness_with_unreachable_database_does_not_leak_credentials(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://test:secret@127.0.0.1:1/test"
    )
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
    assert "secret" not in response.text
