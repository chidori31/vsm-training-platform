from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.errors import UseCaseError
from app.application.identity import IdentityService
from app.application.simulations import SimulationService
from app.persistence.simulations import (
    SimulationCommand,
    SimulationReward,
    StoredSimulation,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def simulations(database, clock):
    IdentityService(database).demo_login()
    return SimulationService(database, clock=clock, seed_factory=lambda: 42)


def action(service, view, command="c", action_id="take:radio", **extra):
    return service.action(
        view["id"],
        "demo-employee",
        command_id=command,
        expected_revision=view["revision"],
        action_id=action_id,
        incident_id=extra.get("incident_id"),
        zone_id=extra.get("zone_id"),
    )


def test_start_ownership_pinning_and_idempotency(simulations, database):
    first, duplicate = simulations.start("demo-employee", key="start")
    again, replayed = simulations.start("demo-employee", key="start")
    assert not duplicate and replayed and first == again
    with pytest.raises(UseCaseError):
        simulations.start("demo-employee", key="other")
    with pytest.raises(UseCaseError) as missing:
        simulations.get(first["id"], "other")
    assert missing.value.code == "simulation_not_found"
    with Session(database) as db:
        row = db.get(StoredSimulation, first["id"])
        assert row.definition["version"] == 1 and row.snapshot["seed"] == 42


def test_revision_race_and_exact_duplicate_return_current(simulations, clock, database):
    first = simulations.start("demo-employee", key="start")[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        views = list(pool.map(lambda _: action(simulations, first), range(2)))
    assert views[0] == views[1]
    clock.now += timedelta(seconds=150)
    replay = action(simulations, first)
    assert replay["elapsed_seconds"] == 150 and len(replay["incidents"]) >= 3
    with pytest.raises(UseCaseError) as conflict:
        action(simulations, first, action_id="take:flashlight")
    assert conflict.value.code == "idempotency_conflict"
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(SimulationCommand)) == 1


def test_bad_command_commits_due_events_and_time_is_monotonic(simulations, clock):
    first = simulations.start("demo-employee", key="start")[0]
    clock.now += timedelta(seconds=80)
    with pytest.raises(UseCaseError):
        action(simulations, first)
    current = simulations.current("demo-employee")
    assert current["elapsed_seconds"] == 80 and current["revision"] > first["revision"]
    clock.now -= timedelta(seconds=30)
    assert simulations.get(first["id"], "demo-employee")["elapsed_seconds"] == 80


def test_worker_completes_offline_and_settles_once_with_profile(
    simulations, clock, database
):
    from app.application.gamification import GamificationService

    first = simulations.start("demo-employee", key="start")[0]
    current = action(
        simulations, first, action_id="move", zone_id=first["incidents"][0]["zone_id"]
    )
    action(
        simulations,
        current,
        command="inspect",
        action_id="inspect",
        incident_id="request",
    )
    clock.now += timedelta(seconds=1500)
    assert simulations.sweep() == 1
    done = simulations.get(first["id"], "demo-employee")
    assert done["status"] == "completed" and done["elapsed_seconds"] == 1200
    assert simulations.sweep() == 0
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(SimulationReward)) == 1
    profile = GamificationService(database).profile("demo-employee")
    assert profile["xp"] == done["xp"] and profile["completed_sessions"] == 1
    assert {item["competency_id"]: item["value"] for item in profile["competencies"]}[
        "regulation"
    ] == 2
    with Session(database) as db:
        assert (
            db.get(SimulationReward, first["id"]).event["payload"]["competency_deltas"][
                "regulation"
            ]
            == 2
        )
    board = GamificationService(database).leaderboard("demo-employee", "company", 20, 0)
    assert board["items"][0]["xp"] == done["xp"]
    report = simulations.debrief(first["id"], "demo-employee")
    assert len(report["incidents"]) == 5 and report["simulation"] == done
    assert all(i["alternatives"] for i in report["incidents"])


def test_restart_restores_pinned_definition_and_rejects_forged_state(
    simulations, database, clock
):
    from app.domain.common import DomainError

    first = simulations.start("demo-employee", key="start")[0]
    current = action(simulations, first)
    restarted = SimulationService(database, clock=clock)
    assert restarted.get(first["id"], "demo-employee") == current
    with Session(database) as db, db.begin():
        row = db.get(StoredSimulation, first["id"])
        row.snapshot = {**row.snapshot, "revision": 999}
    with pytest.raises(DomainError):
        restarted.get(first["id"], "demo-employee")


def test_audit_failure_rolls_back_command(simulations, database, monkeypatch):
    first = simulations.start("demo-employee", key="start")[0]
    with monkeypatch.context() as patch:
        patch.setattr(
            "app.application.simulations.record_audit",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
        )
        with pytest.raises(RuntimeError):
            action(simulations, first)
    assert simulations.get(first["id"], "demo-employee") == first
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(SimulationCommand)) == 0


def test_simulation_api_strict_contract_and_no_hidden_state(
    simulations, database, monkeypatch
):
    from app.api.dependencies import get_engine
    from app.api.simulations import get_simulation_service
    from app.main import app

    monkeypatch.setenv("DEMO_AUTH_ENABLED", "true")
    app.dependency_overrides[get_engine] = lambda: database
    app.dependency_overrides[get_simulation_service] = lambda: simulations
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/simulations/current").status_code == 401
            token = client.post("/api/v1/auth/demo").json()["access_token"]
            client.headers["Authorization"] = "Bearer " + token
            assert client.get("/api/v1/simulations/current").json() == {
                "simulation": None
            }
            assert (
                client.post(
                    "/api/v1/simulations",
                    headers={"Idempotency-Key": "s"},
                    json={"seed": 1},
                ).status_code
                == 422
            )
            response = client.post(
                "/api/v1/simulations", headers={"Idempotency-Key": "s"}, json={}
            )
            assert response.status_code == 201
            value = response.json()
            assert "snapshot" not in value and "seed" not in value
            assert len(value["incidents"]) == 1 and value["incidents"][0]["facts"] == []
            path = f"/api/v1/simulations/{value['id']}"
            assert client.get(path + "/debrief").status_code == 409
            body = dict(
                command_id="c",
                expected_revision=value["revision"],
                action_id="take:radio",
                incident_id=None,
                zone_id=None,
            )
            assert (
                client.post(
                    path + "/actions", headers={"Idempotency-Key": "wrong"}, json=body
                ).status_code
                == 409
            )
            body["elapsed_seconds"] = 1200
            assert (
                client.post(
                    path + "/actions", headers={"Idempotency-Key": "c"}, json=body
                ).status_code
                == 422
            )
            del body["elapsed_seconds"]
            assert (
                client.post(
                    path + "/actions", headers={"Idempotency-Key": "c"}, json=body
                ).status_code
                == 200
            )
            assert client.get("/api/v1/simulations/" + "x" * 129).status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_early_debrief_rejection_keeps_due_events_committed(
    simulations, clock, database
):
    first = simulations.start("demo-employee", key="start")[0]
    clock.now += timedelta(seconds=150)
    with pytest.raises(UseCaseError):
        simulations.debrief(first["id"], "demo-employee")
    with Session(database) as db:
        row = db.get(StoredSimulation, first["id"])
        assert row.elapsed_seconds == 150 and row.revision > first["revision"]


def test_duplicate_survives_completion_and_ledger_is_immutable(
    simulations, clock, database
):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    first = simulations.start("demo-employee", key="start")[0]
    action(simulations, first)
    current = simulations.get(first["id"], "demo-employee")
    current = action(
        simulations,
        current,
        command="move",
        action_id="move",
        zone_id=current["incidents"][0]["zone_id"],
    )
    action(
        simulations,
        current,
        command="inspect",
        action_id="inspect",
        incident_id="request",
    )
    clock.now += timedelta(seconds=1201)
    done = action(simulations, first)
    assert done["status"] == "completed"
    with Session(database) as db:
        reward = db.get(SimulationReward, first["id"])
        assert reward.event["source"] == "simulation-server"
        assert reward.transaction["amount"] == reward.xp
        assert reward.transaction["source_event_id"] == reward.event_id
    with pytest.raises(DBAPIError), Session(database) as db, db.begin():
        db.execute(
            text("UPDATE simulation_rewards SET xp=130 WHERE simulation_id=:id"),
            {"id": first["id"]},
        )


def test_parallel_different_commands_accept_one_revision(simulations):
    first = simulations.start("demo-employee", key="start")[0]

    def attempt(command):
        try:
            action(simulations, first, command=command)
            return "accepted"
        except UseCaseError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, ["one", "two"])) == [
            "accepted",
            "simulation_revision_conflict",
        ]


def test_inactive_completion_remembers_zero_reward_without_transaction(
    simulations, clock, database
):
    first = simulations.start("demo-employee", key="start")[0]
    action(simulations, first)
    clock.now += timedelta(seconds=1200)
    assert simulations.get(first["id"], "demo-employee")["xp"] == 0
    with Session(database) as db:
        reward = db.get(SimulationReward, first["id"])
        assert reward.xp == 0 and reward.transaction is None


def test_simulation_sensitive_actions_have_rate_limit_and_audit(
    simulations, database, monkeypatch
):
    from app.api.dependencies import get_engine
    from app.api.simulations import get_simulation_service
    from app.main import app
    from app.persistence.security import CommandAudit

    monkeypatch.setenv("DEMO_AUTH_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_SIMULATION_ACTION_PER_MINUTE", "1")
    app.dependency_overrides[get_engine] = lambda: database
    app.dependency_overrides[get_simulation_service] = lambda: simulations
    try:
        with TestClient(app) as client:
            token = client.post("/api/v1/auth/demo").json()["access_token"]
            client.headers["Authorization"] = "Bearer " + token
            value = client.post(
                "/api/v1/simulations", headers={"Idempotency-Key": "rate"}, json={}
            ).json()
            path = f"/api/v1/simulations/{value['id']}/actions"
            body = dict(
                command_id="rate-command",
                expected_revision=value["revision"],
                action_id="take:radio",
                incident_id=None,
                zone_id=None,
            )
            assert (
                client.post(
                    path, headers={"Idempotency-Key": "rate-command"}, json=body
                ).status_code
                == 200
            )
            limited = client.post(
                path, headers={"Idempotency-Key": "rate-command"}, json=body
            )
            assert (
                limited.status_code == 429 and int(limited.headers["Retry-After"]) > 0
            )
        with Session(database) as db:
            assert db.scalar(select(func.count()).select_from(SimulationCommand)) == 1
            assert db.scalar(
                select(CommandAudit.id).where(
                    CommandAudit.action == "simulation_action",
                    CommandAudit.outcome == "rate_limited",
                )
            )
    finally:
        app.dependency_overrides.clear()


def test_authoritative_clock_is_read_after_waiting_for_row_lock(
    simulations, database, clock
):
    from threading import Event

    from sqlalchemy import select

    observed = Event()
    first = simulations.start("demo-employee", key="start")[0]

    def authoritative(db):
        observed.set()
        return clock.now

    waiting = SimulationService(database, clock=authoritative)
    with Session(database) as db, db.begin():
        db.scalar(
            select(StoredSimulation)
            .where(StoredSimulation.id == first["id"])
            .with_for_update()
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(waiting.get, first["id"], "demo-employee")
            assert not observed.wait(0.15)
            clock.now += timedelta(seconds=150)
            db.commit()
            result = future.result(timeout=5)
    assert observed.is_set() and result["elapsed_seconds"] == 150
    assert len(result["incidents"]) >= 3


def test_rejected_simulation_command_has_exactly_one_audit(
    simulations, database, monkeypatch
):
    from app.api.dependencies import get_engine
    from app.api.simulations import get_simulation_service
    from app.main import app
    from app.persistence.security import CommandAudit

    monkeypatch.setenv("DEMO_AUTH_ENABLED", "true")
    app.dependency_overrides[get_engine] = lambda: database
    app.dependency_overrides[get_simulation_service] = lambda: simulations
    try:
        with TestClient(app) as client:
            token = client.post("/api/v1/auth/demo").json()["access_token"]
            client.headers["Authorization"] = "Bearer " + token
            value = client.post(
                "/api/v1/simulations", headers={"Idempotency-Key": "audits"}, json={}
            ).json()
            for key, revision, action_id, outcome in [
                ("stale", 0, "take:radio", "conflicting"),
                ("unavailable", value["revision"], "forged", "rejected"),
            ]:
                response = client.post(
                    f"/api/v1/simulations/{value['id']}/actions",
                    headers={"Idempotency-Key": key},
                    json=dict(
                        command_id=key,
                        expected_revision=revision,
                        action_id=action_id,
                        incident_id=None,
                        zone_id=None,
                    ),
                )
                assert response.status_code == 409
                with Session(database) as db:
                    records = list(
                        db.scalars(
                            select(CommandAudit).where(
                                CommandAudit.client_event_id == key
                            )
                        )
                    )
                    assert len(records) == 1 and records[0].outcome == outcome
    finally:
        app.dependency_overrides.clear()


def test_command_limit_rejection_still_commits_catchup(
    simulations, clock, database, monkeypatch
):
    first = simulations.start("demo-employee", key="start")[0]
    action(simulations, first)
    monkeypatch.setattr("app.domain.simulation.MAX_COMMANDS", 1)
    clock.now += timedelta(seconds=150)
    current = simulations.get(first["id"], "demo-employee")
    with pytest.raises(UseCaseError, match="command limit"):
        action(simulations, current, command="two", action_id="take:flashlight")
    with Session(database) as db:
        row = db.get(StoredSimulation, first["id"])
        assert row.elapsed_seconds == 150 and len(row.snapshot["commands"]) == 1
