from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.application.errors import UseCaseError
from app.application.identity import IdentityService
from app.application.training import TrainingService
from app.persistence.training import StoredTraining, TrainingCommand, TrainingReward

pytestmark = pytest.mark.postgres


@pytest.fixture
def training(database, clock):
    IdentityService(database).demo_login()
    return TrainingService(database, clock=clock, seed_factory=lambda: 42)


def act(service, view, key="c", action="take:radio", incident=None, zone=None):
    return service.action(
        view["id"],
        "demo-employee",
        command_id=key,
        expected_revision=view["revision"],
        action_id=action,
        incident_id=incident,
        zone_id=zone,
    )


def finish_action(training, clock, view, key, action, incident=None, zone=None):
    pending = act(training, view, key, action, incident, zone)
    clock.now += timedelta(
        seconds=pending["pending_action"]["completes_at_seconds"]
        - pending["elapsed_seconds"]
    )
    return training.get(view["id"], "demo-employee")


def test_pinning_ownership_and_start_conflict(training, database):
    view, duplicate = training.start("demo-employee", key="start", mode="tutorial")
    again, replayed = training.start("demo-employee", key="start", mode="tutorial")
    assert view == again and not duplicate and replayed
    with pytest.raises(UseCaseError, match="different arguments"):
        training.start("demo-employee", key="start", mode="work")
    with pytest.raises(UseCaseError) as missing:
        training.get(view["id"], "other")
    assert missing.value.code == "training_not_found"
    with Session(database) as db:
        row = db.get(StoredTraining, view["id"])
        assert (
            row.snapshot["world"]["seed"] == 42
            and row.definition["schema_version"] == 2
        )


def test_duplicate_and_revision_race(training, database):
    view = training.start("demo-employee", key="s")[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: act(training, view), range(2)))
    assert results[0] == results[1] and results[0]["pending_action"]
    with pytest.raises(UseCaseError, match="different arguments"):
        act(training, view, action="take:flashlight")
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(TrainingCommand)) == 1


def test_completion_priority_cancel_and_durable_rejection(training, clock, database):
    view = training.start("demo-employee", key="s")[0]
    pending = act(training, view)
    clock.now += timedelta(seconds=4)
    with pytest.raises(UseCaseError):
        act(training, pending, key="cancel", action="cancel")
    with Session(database) as db:
        row = db.get(StoredTraining, view["id"])
        assert row.elapsed_seconds == 4 and row.snapshot["pending"] is None
        assert row.snapshot["world"]["equipment"][0]["carried"]
    current = training.get(view["id"], "demo-employee")
    next_pending = act(training, current, key="flash", action="take:flashlight")
    clock.now += timedelta(seconds=2)
    cancelled = act(training, next_pending, key="cancel2", action="cancel")
    assert (
        cancelled["pending_action"] is None and not cancelled["equipment"][1]["carried"]
    )
    clock.now += timedelta(seconds=80)
    with pytest.raises(UseCaseError):
        act(training, cancelled, key="stale")
    with Session(database) as db:
        assert db.get(StoredTraining, view["id"]).elapsed_seconds == 86


def test_worker_rewards_once_profile_leaderboard_and_immutable_ledger(
    training, clock, database
):
    from app.application.gamification import GamificationService

    view = training.start("demo-employee", key="s")[0]
    view = finish_action(
        training, clock, view, "move", "move", zone=view["incidents"][0]["zone_id"]
    )
    view = finish_action(
        training, clock, view, "inspect", "inspect", incident="request"
    )
    clock.now += timedelta(seconds=1200)
    assert training.sweep() == 1 and training.sweep() == 0
    done = training.get(view["id"], "demo-employee")
    profile = GamificationService(database).profile("demo-employee")
    assert profile["xp"] == done["xp"] > 0 and profile["completed_sessions"] == 1
    board = GamificationService(database).leaderboard("demo-employee", "company", 20, 0)
    assert board["items"][0]["xp"] == done["xp"]
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(TrainingReward)) == 1
        reward = db.get(TrainingReward, view["id"])
        assert reward.transaction["amount"] == done["xp"] and reward.rule_version == 2
    with pytest.raises(DBAPIError), Session(database) as db, db.begin():
        db.execute(
            text("UPDATE training_rewards SET xp=130 WHERE training_id=:id"),
            {"id": view["id"]},
        )


@pytest.mark.parametrize(
    "mode,competency",
    [("tutorial", None), ("demo", None), ("practice", "communication")],
)
def test_nonwork_does_not_reward(training, clock, database, mode, competency):
    view = training.start(
        "demo-employee", key="s", mode=mode, competency_id=competency
    )[0]
    view = finish_action(
        training, clock, view, "move", "move", zone=view["incidents"][0]["zone_id"]
    )
    view = finish_action(
        training, clock, view, "inspect", "inspect", incident="request"
    )
    clock.now += timedelta(seconds=300)
    done = training.get(view["id"], "demo-employee")
    assert done["xp"] == 0 and not done["reward_eligible"]
    assert training.debrief(view["id"], "demo-employee")["achievements"] == []
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(TrainingReward)) == 0


def test_replay_fork_independent_and_comparison(training, clock, database):
    view = training.start("demo-employee", key="s", mode="tutorial")[0]
    act(training, view, action="move", zone=view["incidents"][0]["zone_id"])
    clock.now += timedelta(seconds=180)
    done = training.get(view["id"], "demo-employee")
    historical = training.replay(view["id"], "demo-employee", 3)
    assert (
        historical["pending_action"]
        and historical["location"] == "service"
        and historical["replay"]
    )
    with Session(database) as db:
        source_snapshot = db.get(StoredTraining, view["id"]).snapshot
    fork, duplicate = training.fork(
        view["id"], "demo-employee", key="fork", at_seconds=3
    )
    assert not duplicate and fork["pending_action"] and fork["source_id"] == view["id"]
    assert not fork["reward_eligible"]
    assert training.fork(view["id"], "demo-employee", key="fork", at_seconds=3)[1]
    act(training, fork, key="cancel", action="cancel")
    clock.now += timedelta(seconds=180)
    comparison = training.comparison(fork["id"], "demo-employee")
    assert comparison["source_id"] == view["id"] and len(comparison["differences"]) == 5
    with Session(database) as db:
        assert db.get(StoredTraining, view["id"]).snapshot == source_snapshot
    assert training.get(view["id"], "demo-employee")["metrics"] == done["metrics"]


def test_learning_deduplicates_cases_and_has_old_simulations(training, clock, database):
    from app.application.simulations import SimulationService

    baseline = training.learning("demo-employee")
    assert all(c["score"] is None for c in baseline["competencies"])
    for index in range(2):
        view = training.start("demo-employee", key=str(index), mode="tutorial")[0]
        clock.now += timedelta(seconds=180)
        training.get(view["id"], "demo-employee")
        report = training.learning("demo-employee")
        assert all(
            c["evidence_count"] <= 1 and not c["strong"] for c in report["competencies"]
        )
        if index == 0:
            first = report["recommendations"]
        else:
            assert first == report["recommendations"]
    legacy = SimulationService(database, clock=clock, seed_factory=lambda: 42)
    old = legacy.start("demo-employee", key="old")[0]
    clock.now += timedelta(seconds=1200)
    legacy.get(old["id"], "demo-employee")
    assert training.learning("demo-employee")["statistics"]["completed_runs"] == 3


def test_api_strictness_no_future_state_and_owned_routes(
    training, database, clock, monkeypatch
):
    from app.api.dependencies import get_engine
    from app.api.training import get_training_service
    from app.main import app

    monkeypatch.setenv("DEMO_AUTH_ENABLED", "true")
    app.dependency_overrides[get_engine] = lambda: database
    app.dependency_overrides[get_training_service] = lambda: training
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/training/current").status_code == 401
            token = client.post("/api/v1/auth/demo").json()["access_token"]
            client.headers["Authorization"] = "Bearer " + token
            assert client.get("/api/v1/training/current").json() == {"simulation": None}
            for extra in ["seed", "clock", "xp", "metrics", "definition"]:
                response = client.post(
                    "/api/v1/training/runs",
                    headers={"Idempotency-Key": "s"},
                    json={"mode": "tutorial", extra: 1},
                )
                assert response.status_code == 422
            response = client.post(
                "/api/v1/training/runs",
                headers={"Idempotency-Key": "s"},
                json={"mode": "tutorial"},
            )
            assert response.status_code == 201
            view = response.json()
            path = "/api/v1/training/runs/" + view["id"]
            assert view["engine_version"] == 2 and len(view["incidents"]) == 1
            assert (
                len(view["passengers"]) == 1
                and "seed" not in view
                and "consequences" not in view
            )
            assert all("duration_seconds" in a for a in view["actions"])
            assert client.get(path + "/replay?at_seconds=0").status_code == 409
            assert client.get("/api/v1/training/runs/other").status_code == 404
            body = dict(
                command_id="c",
                expected_revision=view["revision"],
                action_id="take:radio",
                incident_id=None,
                zone_id=None,
            )
            assert (
                client.post(
                    path + "/actions",
                    headers={"Idempotency-Key": "c"},
                    json={**body, "elapsed_seconds": 180},
                ).status_code
                == 422
            )
            assert client.post(
                path + "/actions", headers={"Idempotency-Key": "c"}, json=body
            ).json()["pending_action"]
            clock.now += timedelta(seconds=180)
            report = client.get(path + "/debrief")
            assert (
                report.status_code == 200
                and report.json()["assessment"]["methodology_version"]
                == "demo-methodology-v1"
            )
            assert client.get(path + "/replay?at_seconds=2").json()["pending_action"]
            assert client.get("/api/v1/training/learning").status_code == 200
            assert client.get("/api/v1/training/history").json()["total"] == 1
    finally:
        app.dependency_overrides.clear()


def test_learning_uses_graph_evidence_once_per_case(training, service, scenario):
    for index in range(3):
        session = service.start(
            scenario_id=scenario.id,
            scenario_version=scenario.version,
            employee_id="demo-employee",
        )
        service.decide(
            session.session.id,
            decision_id=f"help-{index}",
            node_id="start",
            choice_id="help",
            expected_sequence=0,
            employee_id="demo-employee",
        )
        service.decide(
            session.session.id,
            decision_id=f"finish-{index}",
            node_id="followup",
            choice_id="finish",
            expected_sequence=1,
            employee_id="demo-employee",
        )
    report = training.learning("demo-employee")
    communication = next(
        c for c in report["competencies"] if c["id"] == "communication"
    )
    assert communication["evidence_count"] == 1 and communication["score"] == 100
    assert not communication["strong"]
    assert report["statistics"]["completed_scenarios"] == 3
    assert any(p["id"] == "loyalty_loss" for p in report["patterns"])


def test_parallel_different_intents_accept_one_revision(training):
    view = training.start("demo-employee", key="s")[0]

    def attempt(key):
        try:
            act(training, view, key=key)
            return "accepted"
        except UseCaseError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, ["one", "two"])) == [
            "accepted",
            "training_revision_conflict",
        ]


def test_restart_validates_pending_and_clock_after_row_lock(training, database, clock):
    from threading import Event

    from app.domain.common import DomainError

    view = training.start("demo-employee", key="s")[0]
    pending = act(training, view)
    assert (
        TrainingService(database, clock=clock).get(view["id"], "demo-employee")
        == pending
    )
    observed = Event()

    def authoritative(db):
        observed.set()
        return clock.now

    waiting = TrainingService(database, clock=authoritative)
    with Session(database) as db, db.begin():
        db.scalar(
            select(StoredTraining)
            .where(StoredTraining.id == view["id"])
            .with_for_update()
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(waiting.get, view["id"], "demo-employee")
            assert not observed.wait(0.1)
            clock.now += timedelta(seconds=4)
            db.commit()
            result = future.result(timeout=5)
    assert observed.is_set() and result["pending_action"] is None
    with Session(database) as db, db.begin():
        row = db.get(StoredTraining, view["id"])
        row.snapshot = {
            **row.snapshot,
            "world": {**row.snapshot["world"], "revision": 999},
        }
    with pytest.raises(DomainError):
        training.get(view["id"], "demo-employee")


def test_work_fork_never_awards_again_or_changes_learning(training, clock, database):
    view = training.start("demo-employee", key="work")[0]
    view = finish_action(
        training, clock, view, "move", "move", zone=view["incidents"][0]["zone_id"]
    )
    view = finish_action(
        training, clock, view, "inspect", "inspect", incident="request"
    )
    clock.now += timedelta(seconds=1200)
    training.get(view["id"], "demo-employee")
    before = training.learning("demo-employee")
    fork, _ = training.fork(view["id"], "demo-employee", key="fork", at_seconds=16)
    assert fork["elapsed_seconds"] == 16 and not fork["reward_eligible"]
    clock.now += timedelta(seconds=1200)
    assert training.get(fork["id"], "demo-employee")["xp"] == 0
    assert training.learning("demo-employee") == before
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(TrainingReward)) == 1


def test_early_debrief_and_rejected_terminal_action_commit_tick(
    training, clock, database
):
    view = training.start("demo-employee", key="work")[0]
    clock.now += timedelta(seconds=80)
    with pytest.raises(UseCaseError):
        training.debrief(view["id"], "demo-employee")
    with Session(database) as db:
        assert db.get(StoredTraining, view["id"]).elapsed_seconds == 80
    clock.now += timedelta(seconds=1200)
    with pytest.raises(UseCaseError):
        act(training, view)
    with Session(database) as db:
        assert db.get(StoredTraining, view["id"]).status == "completed"
        assert db.get(TrainingReward, view["id"]).xp == 0


def test_pattern_recurrence_bounded_to_five_runs_and_excludes_demo(training, clock):
    for index in range(6):
        view = training.start("demo-employee", key=f"run-{index}", mode="tutorial")[0]
        clock.now += timedelta(seconds=180)
        training.get(view["id"], "demo-employee")
        patterns = training.learning("demo-employee")["patterns"]
        unresolved = next(p for p in patterns if p["id"] == "unresolved")
        assert unresolved["count"] == min(index + 1, 5)
        assert ("Единичный" if index == 0 else "Повторяется") in unresolved[
            "explanation"
        ]
    demo = training.start("demo-employee", key="demo", mode="demo")[0]
    clock.now += timedelta(seconds=240)
    training.get(demo["id"], "demo-employee")
    assert training.learning("demo-employee")["patterns"] == patterns


def test_learning_preserves_distinct_same_kind_cases_and_caps_repeated_case(
    training, clock
):
    from copy import deepcopy

    from app.application.training import definition_path
    from app.training.schema import DefinitionDocument, load_definition

    data = load_definition(definition_path()).model_dump()
    second = deepcopy(data["incidents"][0])
    second.update(id="second-request", title="Другая просьба")
    data["incidents"].append(second)
    data["modes"]["tutorial"]["incident_seconds"]["second-request"] = 0
    document = DefinitionDocument.model_validate(data)
    for index in range(2):
        view = training.start_pinned(
            "demo-employee", key=f"distinct-{index}", mode="tutorial", document=document
        )[0]
        view = finish_action(
            training,
            clock,
            view,
            f"move-{index}",
            "move",
            zone=view["incidents"][0]["zone_id"],
        )
        view = finish_action(
            training, clock, view, f"talk-{index}", "talk", incident="request"
        )
        clock.now += timedelta(seconds=180)
        training.get(view["id"], "demo-employee")
        result = training.learning("demo-employee")
        evidence = next(c for c in result["competencies"] if c["id"] == "communication")
        assert evidence["evidence_count"] == 2
        assert evidence["score"] == 50


def test_graph_positive_choice_at_cap_is_positive_mastery_evidence(
    training, service, database
):
    import json

    from app.persistence.scenarios import ScenarioRepository
    from app.scenarios.schema import ScenarioDocument

    document = ScenarioDocument.model_validate_json(
        json.dumps(
            {
                "schema_version": 1,
                "id": "saturated-safe-training",
                "version": 1,
                "title": "Положительный выбор на границе шкалы",
                "start_node_id": "first",
                "competency_ids": [],
                "nodes": [
                    {
                        "id": "first",
                        "text": "Первый выбор",
                        "choices": [
                            {
                                "id": "safe",
                                "text": "Обезопасить",
                                "destination": "second",
                                "explanation": "Безопасное действие",
                                "effects": [
                                    {
                                        "type": "add_score",
                                        "metric": "safety_rating",
                                        "delta": 50,
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "id": "second",
                        "text": "Повторная проверка",
                        "choices": [
                            {
                                "id": "safe",
                                "text": "Проверить",
                                "destination": "done",
                                "explanation": "Положительное действие на максимуме",
                                "effects": [
                                    {
                                        "type": "add_score",
                                        "metric": "safety_rating",
                                        "delta": 5,
                                    }
                                ],
                            },
                            {
                                "id": "unsafe",
                                "text": "Пропустить",
                                "destination": "done",
                                "explanation": "Пропуск снижает безопасность",
                                "effects": [
                                    {
                                        "type": "add_score",
                                        "metric": "safety_rating",
                                        "delta": -5,
                                    }
                                ],
                            },
                        ],
                    },
                    {"id": "done", "text": "Готово", "terminal": True},
                ],
            }
        )
    )
    with Session(database) as db, db.begin():
        ScenarioRepository(db).add(document)
    view = service.start(
        scenario_id=document.id, scenario_version=1, employee_id="demo-employee"
    )
    for index, node in enumerate(("first", "second")):
        service.decide(
            view.session.id,
            decision_id=f"cap-{index}",
            node_id=node,
            choice_id="safe",
            expected_sequence=index,
            employee_id="demo-employee",
        )
    result = training.learning("demo-employee")
    safety = next(c for c in result["competencies"] if c["id"] == "safety")
    assert safety["evidence_count"] == 2
    assert safety["score"] == 100
