from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.postgres


def test_http_conflict_returns_timeout_that_is_committed_in_postgres(
    database, scenario, service, clock
):
    from app.api.dependencies import current_user
    from app.api.sessions import get_session_service
    from app.application.sessions import SessionService
    from app.domain.profiles import EmployeeProfile
    from app.main import app

    previous = app.dependency_overrides.get(get_session_service)
    app.dependency_overrides[get_session_service] = lambda: service
    app.dependency_overrides[current_user] = lambda: EmployeeProfile(
        "http-synthetic-employee", "Synthetic"
    )
    try:
        with TestClient(app) as client:
            session_id = service.start(
                scenario_id=scenario.id,
                scenario_version=1,
                employee_id="http-synthetic-employee",
            ).session.id
            created = client.get(f"/api/v1/sessions/{session_id}").json()
            assert created["session"]["format_version"] == 2
            clock.now += timedelta(seconds=15)
            response = client.post(
                f"/api/v1/sessions/{session_id}/decisions",
                json={
                    "decision_id": "http-choice",
                    "node_id": "start",
                    "choice_id": "help",
                    "expected_sequence": 0,
                },
            )
            assert response.status_code == 409
            body = response.json()
            assert body["error"]["code"] == "decision_timed_out"
            body = body["data"]
            assert body["session"]["current_node_id"] == "recovery"
            assert len(body["session"]["decisions"]) == 1

            # Replace the service instance as well as opening a fresh DB session:
            # the next read must reconstruct the committed state.
            restarted = SessionService(database, clock=clock)
            app.dependency_overrides[get_session_service] = lambda: restarted
            refreshed = client.get(f"/api/v1/sessions/{session_id}")
            assert refreshed.status_code == 200
            assert refreshed.json()["session"] == body["session"]
    finally:
        app.dependency_overrides.pop(current_user, None)
        if previous is None:
            app.dependency_overrides.pop(get_session_service, None)
        else:
            app.dependency_overrides[get_session_service] = previous
