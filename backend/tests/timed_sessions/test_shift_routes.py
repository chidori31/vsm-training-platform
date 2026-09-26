from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.application.errors import UseCaseError
from app.application.identity import IdentityService
from app.persistence.gamification import SessionReward
from app.persistence.scenarios import ScenarioRepository
from app.persistence.sessions import StoredSession
from app.scenarios.loader import load_document

pytestmark = pytest.mark.postgres


@pytest.fixture
def shifts(database, clock):
    from app.application.shifts import ShiftService

    IdentityService(database).demo_login()
    with Session(database) as db, db.begin():
        for path in (Path(__file__).parents[3] / "scenarios" / "shifts").glob("*.json"):
            ScenarioRepository(db).add(load_document(path))
    return ShiftService(database, clock=clock, seed_factory=lambda: 42)


def start(shifts, key="start", difficulty="standard"):
    return shifts.start("demo-employee", key=key, difficulty=difficulty)[0]


def finish_stage(service, shift, clock):
    sid = shift["current_session_id"]
    for index, choice in enumerate(
        ("assess", "confirm", "recheck")
        if shift["difficulty"] == "advanced"
        else ("assess", "confirm")
    ):
        view = service.get(sid, employee_id="demo-employee")
        clock.now += timedelta(seconds=3)
        result = service.decide(
            sid,
            node_id=view.session.current_node_id,
            choice_id=choice,
            decision_id=f"decision-{index}",
            expected_sequence=index,
            employee_id="demo-employee",
        )
        assert result.outcome == "accepted"


def advance(shifts, shift, command="advance"):
    return shifts.advance(
        shift["id"],
        "demo-employee",
        command_id=command,
        expected_step=shift["current_step"],
        session_id=shift["current_session_id"],
    )


def test_shift_start_is_owned_idempotent_and_only_creates_current_session(
    shifts, database
):
    initial, replay = shifts.start("demo-employee", key="one", difficulty="standard")
    same, replayed = shifts.start("demo-employee", key="one", difficulty="standard")
    assert not replay and replayed and initial == same
    assert initial["current_step"] == 0 and initial["seed"] == 42
    assert [s["status"] for s in initial["steps"]] == ["active", "locked", "locked"]
    assert [s["session_id"] is not None for s in initial["steps"]] == [
        True,
        False,
        False,
    ]
    assert shifts.current("demo-employee") == initial
    with pytest.raises(UseCaseError, match="already active"):
        start(shifts, key="different")
    with pytest.raises(UseCaseError) as conflict:
        start(shifts, key="one", difficulty="advanced")
    assert conflict.value.code == "idempotency_conflict"
    with pytest.raises(UseCaseError) as missing:
        shifts.get(initial["id"], "other")
    assert missing.value.code == "shift_not_found"
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(StoredSession)) == 1


def test_shift_enforces_completion_current_step_and_linked_session(
    shifts, service, clock
):
    initial = start(shifts)
    with pytest.raises(UseCaseError) as early:
        advance(shifts, initial)
    assert early.value.code == "shift_step_not_ready"
    finish_stage(service, initial, clock)
    for expected, session_id in [(1, initial["current_session_id"]), (0, "forged")]:
        with pytest.raises(UseCaseError):
            shifts.advance(
                initial["id"],
                "demo-employee",
                command_id="bad",
                expected_step=expected,
                session_id=session_id,
            )
    next_step = advance(shifts, initial)
    assert next_step["current_step"] == 1
    assert next_step["current_session_id"] != initial["current_session_id"]
    assert next_step["steps"][0]["status"] == "completed"
    assert advance(shifts, initial) == next_step
    with pytest.raises(UseCaseError) as conflicting:
        advance(shifts, next_step)
    assert conflicting.value.code == "idempotency_conflict"


def test_completed_shift_sums_existing_xp_and_persists_behavior_unlocks(
    shifts, service, clock, database
):
    from app.persistence.shifts import ShiftAchievement

    current = start(shifts, difficulty="advanced")
    for index in range(3):
        prior = current
        finish_stage(service, current, clock)
        current = advance(shifts, current, f"advance-{index}")
    assert current["status"] == "completed" and current["current_step"] == 3
    assert current["current_session_id"] is None
    assert current["metrics"]["completed_scenarios"] == 3
    assert current["metrics"]["average_reaction_seconds"] == 3
    assert current["metrics"]["decision_count"] == 9
    assert current["metrics"]["regulation"] == 18
    assert any(
        a["id"] == "shift-advanced" and a["unlocked"] for a in current["achievements"]
    )
    assert advance(shifts, prior, "advance-2") == current
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(SessionReward)) == 3
        assert db.scalar(select(func.sum(SessionReward.xp))) == current["metrics"]["xp"]
        assert db.scalar(select(func.count()).select_from(ShiftAchievement)) >= 4
    assert shifts.current("demo-employee") == current


def test_shift_recovers_after_restart_and_lost_advance_response(
    shifts, service, clock, database
):
    from app.application.shifts import ShiftService

    initial = start(shifts)
    finish_stage(service, initial, clock)
    advanced = advance(shifts, initial)
    fresh = create_engine(database.url)
    try:
        restarted = ShiftService(fresh, clock=clock)
        assert restarted.current("demo-employee") == advanced
        assert advance(restarted, initial) == advanced
    finally:
        fresh.dispose()


def test_parallel_starts_and_advances_do_not_create_duplicate_sessions(
    shifts, service, clock, database
):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: start(shifts), range(2)))
    assert results[0]["id"] == results[1]["id"]
    initial = results[0]
    finish_stage(service, initial, clock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        progressed = list(pool.map(lambda _: advance(shifts, initial), range(2)))
    assert progressed[0] == progressed[1]
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(StoredSession)) == 2


def test_shift_advance_failure_rolls_back_new_session_and_command(
    shifts, service, clock, database, monkeypatch
):
    from app.persistence.shifts import ShiftCommand

    initial = start(shifts)
    finish_stage(service, initial, clock)
    original = shifts.sessions._start

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Synthetic shift creation failure")

    with monkeypatch.context() as patch:
        patch.setattr(shifts.sessions, "_start", fail)
        with pytest.raises(RuntimeError):
            advance(shifts, initial)
    assert shifts.current("demo-employee")["current_step"] == 0
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(StoredSession)) == 1
        assert db.scalar(select(func.count()).select_from(ShiftCommand)) == 0
    assert advance(shifts, initial)["current_step"] == 1


def test_current_shift_get_reconciles_server_timeout_without_skipping_stage(
    shifts, clock
):
    initial = start(shifts)
    clock.now += timedelta(seconds=65)
    recovered = shifts.get(initial["id"], "demo-employee")
    assert recovered["current_step"] == 0
    assert recovered["metrics"]["critical_errors"] == 1
    assert recovered["metrics"]["average_reaction_seconds"] is None
    assert recovered["metrics"]["decision_count"] == 1
    with pytest.raises(UseCaseError):
        advance(shifts, initial)


def test_training_qualification_streak_counts_completed_shifts_only(
    shifts, service, clock
):
    for index in range(3):
        current = start(shifts, key=f"shift-{index}")
        for step in range(3):
            finish_stage(service, current, clock)
            current = advance(shifts, current, f"step-{step}")
        steady = next(a for a in current["achievements"] if a["id"] == "shift-steady")
        assert steady["unlocked"] is (index == 2)


def test_shift_http_contract_rejects_client_seed_scores_and_unowned_ids(
    shifts, service, database, monkeypatch
):
    from app.api.dependencies import get_engine
    from app.api.sessions import get_session_service
    from app.api.shifts import get_shift_service
    from app.main import app

    monkeypatch.setenv("DEMO_AUTH_ENABLED", "true")
    app.dependency_overrides[get_engine] = lambda: database
    app.dependency_overrides[get_shift_service] = lambda: shifts
    app.dependency_overrides[get_session_service] = lambda: service
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/shifts/current").status_code == 401
            token = client.post("/api/v1/auth/demo").json()["access_token"]
            client.headers["Authorization"] = "Bearer " + token
            assert client.get("/api/v1/shifts/current").json() == {"shift": None}
            for extra in [{"seed": 1}, {"xp": 999}, {"employee_id": "other"}]:
                assert (
                    client.post(
                        "/api/v1/shifts",
                        headers={"Idempotency-Key": "http"},
                        json={"difficulty": "standard", **extra},
                    ).status_code
                    == 422
                )
            response = client.post(
                "/api/v1/shifts",
                headers={"Idempotency-Key": "http"},
                json={"difficulty": "standard"},
            )
            assert response.status_code == 201
            sid = response.json()["id"]
            assert (
                client.post(
                    "/api/v1/shifts",
                    headers={"Idempotency-Key": "http"},
                    json={"difficulty": "standard"},
                ).status_code
                == 200
            )
            assert client.get(f"/api/v1/shifts/{sid}").json() == response.json()
            assert (
                client.post(
                    f"/api/v1/shifts/{sid}/advance",
                    json={
                        "command_id": "next",
                        "expected_step": 0,
                        "session_id": response.json()["current_session_id"],
                    },
                ).status_code
                == 409
            )
            token = client.post(
                "/api/v1/auth/demo", json={"persona_id": "demo-north-02"}
            ).json()["access_token"]
            client.headers["Authorization"] = "Bearer " + token
            assert client.get(f"/api/v1/shifts/{sid}").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_growth_unlock_compares_previous_completed_shift_and_is_persisted(
    shifts, service, clock, database
):
    from app.persistence.shifts import ShiftAchievement

    previous = start(shifts, key="weak")
    for step in range(3):
        sid = previous["current_session_id"]
        for index, choice in enumerate(("rush", "dismiss")):
            view = service.get(sid, employee_id="demo-employee")
            clock.now += timedelta(seconds=3)
            service.decide(
                sid,
                node_id=view.session.current_node_id,
                choice_id=choice,
                decision_id=f"bad-{index}",
                expected_sequence=index,
                employee_id="demo-employee",
            )
        previous = advance(shifts, previous, f"bad-step-{step}")
    assert previous["metrics"]["regulation"] < 0
    assert not next(a for a in previous["achievements"] if a["id"] == "shift-growth")[
        "unlocked"
    ]
    current = start(shifts, key="improved")
    for step in range(3):
        finish_stage(service, current, clock)
        current = advance(shifts, current, f"good-step-{step}")
    assert next(a for a in current["achievements"] if a["id"] == "shift-growth")[
        "unlocked"
    ]
    with Session(database) as db:
        achievement = db.get(ShiftAchievement, ("demo-employee", "shift-growth"))
        assert achievement.shift_id == current["id"]
        assert db.scalar(select(func.count()).select_from(SessionReward)) == 6


def test_shift_replays_and_final_advance_are_audited_without_extra_xp(
    shifts, service, clock, database
):
    from app.persistence.security import CommandAudit

    current = start(shifts)
    assert start(shifts)["id"] == current["id"]
    for index in range(3):
        prior = current
        finish_stage(service, current, clock)
        current = advance(shifts, current, f"next-{index}")
    assert advance(shifts, prior, "next-2") == current
    with Session(database) as db:
        audits = list(
            db.scalars(
                select(CommandAudit).where(
                    CommandAudit.action.in_(["shift_start", "shift_advance"])
                )
            )
        )
        assert len(audits) == 6
        assert sum(a.outcome == "duplicate" for a in audits) == 2
        final = next(
            a
            for a in audits
            if a.action == "shift_advance"
            and a.client_event_id == "next-2"
            and a.outcome == "accepted"
        )
        assert final.reward["xp_granted"] == 0
        assert final.details["resulting_step"] == 3


@pytest.mark.parametrize("missing", ["completed_at", "summary"])
def test_database_rejects_completed_shift_without_final_evidence(
    shifts, database, missing
):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    current = start(shifts)
    if missing == "completed_at":
        assignment = "completed_at = NULL, summary = '{}'::jsonb"
    else:
        assignment = "completed_at = started_at, summary = NULL"
    with pytest.raises(IntegrityError), Session(database) as db, db.begin():
        db.execute(
            text(
                "UPDATE training_shifts SET status='completed', current_step=3, "
                + assignment
                + " WHERE id=:id"
            ),
            {"id": current["id"]},
        )


@pytest.mark.parametrize(
    "identifier",
    ["x" * 129, "x" * 65000, "bad%00id"],
    ids=["over-bound", "large-path", "control"],
)
def test_shift_path_is_validated_before_rejection_audit(
    shifts, database, monkeypatch, identifier
):
    from app.api.dependencies import get_engine
    from app.api.shifts import get_shift_service
    from app.main import app
    from app.persistence.security import CommandAudit
    from app.persistence.shifts import ShiftCommand, TrainingShift

    monkeypatch.setenv("DEMO_AUTH_ENABLED", "true")
    app.dependency_overrides[get_engine] = lambda: database
    app.dependency_overrides[get_shift_service] = lambda: shifts
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            token = client.post("/api/v1/auth/demo").json()["access_token"]
            client.headers["Authorization"] = "Bearer " + token
            path = f"/api/v1/shifts/{identifier}"
            assert client.get(path).status_code == 422
            response = client.post(
                path + "/advance",
                json={
                    "command_id": "next",
                    "expected_step": 0,
                    "session_id": "unknown",
                },
            )
            assert response.status_code == 422
        with Session(database) as db:
            assert db.scalar(select(func.count()).select_from(TrainingShift)) == 0
            assert db.scalar(select(func.count()).select_from(ShiftCommand)) == 0
            assert db.scalar(select(func.count()).select_from(StoredSession)) == 0
            audit = db.scalar(
                select(CommandAudit).where(CommandAudit.action == "shift_advance")
            )
            assert audit.outcome == "rejected"
            assert "requested_shift_id" not in audit.details
            assert audit.client_event_id is None
    finally:
        app.dependency_overrides.clear()
