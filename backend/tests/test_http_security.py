import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.api.dependencies import get_engine
from app.api.sessions import DecisionRequest
from app.main import app


@pytest.mark.parametrize(
    "exception, status, code",
    [
        (
            RuntimeError("Traceback /private/app.py synthetic-password"),
            500,
            "internal_error",
        ),
        (
            OperationalError("SELECT synthetic-password", {}, None),
            503,
            "database_unavailable",
        ),
    ],
)
def test_failures_never_expose_internal_details_or_allow_caching(
    exception, status, code
):
    def failing_engine():
        raise exception

    app.dependency_overrides[get_engine] = failing_engine
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get(
                "/api/v1/profiles/me",
                headers={"Authorization": "Bearer synthetic-token"},
            )
        assert response.status_code == status
        assert response.json()["error"]["code"] == code
        assert response.json()["data"] is None
        assert not any(
            word in response.text
            for word in ("Traceback", "private", "synthetic-password", "SELECT")
        )
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("chunked", [False, True])
def test_oversized_requests_are_bounded_before_authentication(chunked, monkeypatch):
    monkeypatch.delenv("DEMO_AUTH_ENABLED", raising=False)
    content = b" " * (64 * 1024 + 1)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/demo",
            content=iter([content[:32000], content[32000:]]) if chunked else content,
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


@pytest.mark.parametrize(
    "field, value",
    [
        ("decision_id", "synthetic\x00id"),
        ("node_id", "start\n"),
        ("choice_id", "help\x7f"),
        ("expected_sequence", 2147483648),
    ],
)
def test_decision_commands_reject_control_characters_and_unbounded_revision(
    field, value
):
    payload = dict(
        decision_id="synthetic-id",
        node_id="start",
        choice_id="help",
        expected_sequence=0,
    )
    with pytest.raises(ValidationError):
        DecisionRequest.model_validate(payload | {field: value})


def test_validation_errors_do_not_echo_submitted_values(monkeypatch):
    monkeypatch.setenv("DEMO_AUTH_ENABLED", "true")
    app.dependency_overrides[get_engine] = lambda: None
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/auth/demo",
                json={"email": "synthetic-sensitive-value", "persona_id": "invalid"},
            )
        assert response.status_code == 422
        assert "synthetic-sensitive-value" not in response.text
        assert response.headers["cache-control"] == "no-store"
    finally:
        app.dependency_overrides.clear()
