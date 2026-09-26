from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.postgres


@pytest.fixture
def client(database, service, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api.dependencies import get_engine
    from app.api.sessions import get_session_service
    from app.main import app

    monkeypatch.setenv("DEMO_AUTH_ENABLED", "true")
    app.dependency_overrides[get_engine] = lambda: database
    app.dependency_overrides[get_session_service] = lambda: service
    try:
        with TestClient(app) as http:
            token = http.post("/api/v1/auth/demo").json()["access_token"]
            http.headers["Authorization"] = "Bearer " + token
            yield http
    finally:
        app.dependency_overrides.clear()


def test_durable_rate_limit_survives_service_restart_and_has_fixed_reset(
    database, clock
):
    from app.application.anti_cheat import RateLimiter, RateLimitExceeded, RatePolicy

    policy = RatePolicy(2, 60)
    limiter = RateLimiter(database, clock=clock)
    limiter.check("decision", "synthetic-user", policy=policy)
    limiter.check("decision", "synthetic-user", policy=policy)
    with pytest.raises(RateLimitExceeded) as error:
        RateLimiter(database, clock=clock).check(
            "decision", "synthetic-user", policy=policy
        )
    assert 1 <= error.value.retry_after <= 60
    clock.now += timedelta(seconds=60)
    limiter.check("decision", "synthetic-user", policy=policy)


def test_rate_limit_is_atomic_across_concurrent_workers(database, clock):
    from app.application.anti_cheat import RateLimiter, RateLimitExceeded, RatePolicy

    barrier = Barrier(6)

    def hit():
        barrier.wait(timeout=10)
        try:
            RateLimiter(database, clock=clock).check(
                "start", "synthetic-user", policy=RatePolicy(2, 60)
            )
        except RateLimitExceeded:
            return False
        return True

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(lambda _: hit(), range(6))) == 2


def test_rate_buckets_are_separate_for_subject_and_action(database, clock):
    from app.application.anti_cheat import RateLimiter, RatePolicy

    limiter = RateLimiter(database, clock=clock)
    for action, subject in [("start", "one"), ("start", "two"), ("decision", "one")]:
        limiter.check(action, subject, policy=RatePolicy(1, 60))


def test_accepted_duplicate_conflicting_commands_have_trusted_durable_audit(
    service, started, database
):
    from app.persistence.security import CommandAudit

    args = dict(
        node_id="start",
        choice_id="help",
        decision_id="audit-command",
        expected_sequence=0,
    )
    service.decide(started.session.id, **args)
    service.decide(started.session.id, **args)
    service.decide(started.session.id, **(args | {"choice_id": "finish"}))
    with Session(database) as db:
        rows = db.scalars(
            select(CommandAudit)
            .where(CommandAudit.action == "decision")
            .order_by(CommandAudit.created_at, CommandAudit.id)
        ).all()
        assert sorted(row.outcome for row in rows) == [
            "accepted",
            "conflicting",
            "duplicate",
        ]
        accepted = next(row for row in rows if row.outcome == "accepted")
        assert accepted.actor_id == started.session.employee_id
        assert accepted.session_id == started.session.id
        assert accepted.scenario_id == started.session.scenario_id
        assert accepted.previous_revision == 0 and accepted.resulting_revision == 1
        assert accepted.client_event_id == "audit-command"
        assert accepted.reward == {"xp_total": 0, "xp_granted": 0}
        assert (
            "conflicting_command"
            in next(row for row in rows if row.outcome == "conflicting").flags
        )


def test_audit_failure_rolls_back_the_accepted_transition(
    service, started, database, monkeypatch
):
    import app.application.sessions as sessions
    from app.persistence.sessions import StoredSession

    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic audit failure")

    monkeypatch.setattr(sessions, "record_session_command", fail)
    with pytest.raises(RuntimeError, match="Synthetic audit failure"):
        service.decide(
            started.session.id,
            node_id="start",
            choice_id="help",
            decision_id="one",
            expected_sequence=0,
        )
    with Session(database) as db:
        assert db.get(StoredSession, started.session.id).revision == 0


def test_rate_rejections_are_audited_without_client_payload(database, clock):
    from app.application.anti_cheat import RateLimiter, RateLimitExceeded, RatePolicy
    from app.persistence.security import CommandAudit

    limiter = RateLimiter(database, clock=clock)
    limiter.check("auth", "global", policy=RatePolicy(1, 60))
    with pytest.raises(RateLimitExceeded):
        limiter.check("auth", "global", policy=RatePolicy(1, 60))
    with Session(database) as db:
        row = db.scalar(
            select(CommandAudit).where(CommandAudit.outcome == "rate_limited")
        )
        assert row is not None and row.actor_id is None
        assert row.session_id is None and row.client_event_id is None
        assert db.scalar(select(func.count()).select_from(CommandAudit)) == 1


def test_http_limits_and_rejections_use_trusted_identity_without_raw_payload(
    client, scenario, database, monkeypatch
):
    from app.persistence.security import CommandAudit

    monkeypatch.setenv("RATE_LIMIT_DECISION_PER_MINUTE", "2")
    response = client.post(
        "/api/v1/sessions",
        headers={"Idempotency-Key": "rate-start"},
        json={"scenario_id": scenario.id, "scenario_version": 1},
    )
    sid = response.json()["session"]["id"]
    payload = dict(
        decision_id="rate-one", node_id="start", choice_id="help", expected_sequence=0
    )
    assert (
        client.post(f"/api/v1/sessions/{sid}/decisions", json=payload).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/sessions/{sid}/decisions",
            json=payload | {"xp": "private-raw-value"},
        ).status_code
        == 422
    )
    denied = client.post(f"/api/v1/sessions/{sid}/decisions", json=payload)
    assert (
        denied.status_code == 429 and denied.json()["error"]["code"] == "rate_limited"
    )
    assert 1 <= int(denied.headers["retry-after"]) <= 60
    assert denied.headers["cache-control"] == "no-store"
    with Session(database) as db:
        rows = db.scalars(
            select(CommandAudit).where(CommandAudit.action == "decision")
        ).all()
        assert sorted(row.outcome for row in rows) == [
            "accepted",
            "rate_limited",
            "rejected",
        ]
        rejected = next(row for row in rows if row.outcome == "rejected")
        assert rejected.actor_id == "demo-employee" and rejected.session_id is None
        assert rejected.details == {"error_code": "validation_error"}
        assert "private-raw-value" not in str(rejected.details)


def test_auth_limit_is_global_across_personas_without_ip_tracking(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_AUTH_PER_MINUTE", "1")
    denied = client.post("/api/v1/auth/demo", json={"persona_id": "demo-north-02"})
    assert denied.status_code == 429


def test_invalid_auth_dto_is_rate_limited_and_audited_without_payload(
    client, database, monkeypatch
):
    from app.persistence.security import CommandAudit

    monkeypatch.setenv("RATE_LIMIT_AUTH_PER_MINUTE", "2")
    invalid = client.post(
        "/api/v1/auth/demo", json={"persona_id": "private-client-value"}
    )
    assert invalid.status_code == 422
    assert client.post("/api/v1/auth/demo").status_code == 429
    with Session(database) as db:
        row = db.scalar(
            select(CommandAudit).where(
                CommandAudit.action == "auth", CommandAudit.outcome == "rejected"
            )
        )
        assert row is not None and row.actor_id is None
        assert row.details == {"error_code": "validation_error"}


def test_start_conflict_retains_validated_client_id_and_requested_version(
    client, scenario, database
):
    from app.persistence.security import CommandAudit

    path = "/api/v1/sessions"
    headers = {"Idempotency-Key": "conflicting-start"}
    assert (
        client.post(
            path,
            headers=headers,
            json={"scenario_id": scenario.id, "scenario_version": 1},
        ).status_code
        == 201
    )
    assert (
        client.post(
            path,
            headers=headers,
            json={"scenario_id": scenario.id, "scenario_version": 2},
        ).status_code
        == 409
    )
    with Session(database) as db:
        row = db.scalar(
            select(CommandAudit).where(CommandAudit.outcome == "conflicting")
        )
        assert row is not None and row.actor_id == "demo-employee"
        assert row.client_event_id == "conflicting-start"
        assert row.details == {
            "error_code": "idempotency_conflict",
            "requested_scenario_id": scenario.id,
            "requested_scenario_version": 2,
        }


def test_parallel_attempt_and_frequent_replay_flags_do_not_block_training(
    service, scenario, database
):
    from app.persistence.security import CommandAudit

    for _ in range(5):
        view = service.start(
            scenario_id=scenario.id,
            scenario_version=1,
            employee_id="synthetic-employee",
        )
    args = dict(
        decision_id="replay-burst",
        node_id="start",
        choice_id="help",
        expected_sequence=0,
    )
    assert service.decide(view.session.id, **args).outcome == "accepted"
    for _ in range(5):
        assert service.decide(view.session.id, **args).outcome == "duplicate"
    with Session(database) as db:
        starts = db.scalars(
            select(CommandAudit).where(CommandAudit.action == "start")
        ).all()
        assert sum("many_parallel_sessions" in row.flags for row in starts) == 1
        replays = db.scalars(
            select(CommandAudit).where(CommandAudit.outcome == "duplicate")
        ).all()
        assert sum("frequent_replay" in row.flags for row in replays) == 1


def test_audit_rows_cannot_be_updated_or_deleted(database, clock):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from app.application.anti_cheat import record_audit

    with Session(database) as db, db.begin():
        record_audit(
            db,
            actor_id="synthetic",
            action="decision",
            outcome="rejected",
            server_time=clock.now,
        )
    for command in [
        "UPDATE command_audits SET outcome = 'accepted'",
        "DELETE FROM command_audits",
    ]:
        with pytest.raises(DBAPIError), database.begin() as connection:
            connection.execute(text(command))


def test_terminal_replay_has_no_second_reward_and_repeated_sequences_only_flag(
    service, scenario, database, clock
):
    import json

    from app.application.identity import IdentityService
    from app.persistence.scenarios import ScenarioRepository
    from app.persistence.security import CommandAudit
    from app.scenarios.schema import ScenarioDocument

    IdentityService(database).demo_login()
    with Session(database) as db, db.begin():
        payload = ScenarioRepository(db).get(scenario.id, 1).model_dump(mode="json")
        payload["id"] = "synthetic-perfect"
        payload["nodes"][1]["choices"][0]["effects"][0]["delta"] = 3
        perfect = ScenarioDocument.model_validate_json(json.dumps(payload))
        ScenarioRepository(db).add(perfect)
    for index in range(3):
        view = service.start(
            scenario_id=perfect.id, scenario_version=1, employee_id="demo-employee"
        )
        clock.now += timedelta(seconds=1)
        service.decide(
            view.session.id,
            decision_id=f"{index}-first",
            node_id="start",
            choice_id="help",
            expected_sequence=0,
        )
        clock.now += timedelta(seconds=1)
        command = dict(
            decision_id=f"{index}-last",
            node_id="followup",
            choice_id="finish",
            expected_sequence=1,
        )
        service.decide(view.session.id, **command)
        service.decide(view.session.id, **command)
    with Session(database) as db:
        final = db.scalars(
            select(CommandAudit).where(CommandAudit.client_event_id == "2-last")
        ).all()
        accepted = next(row for row in final if row.outcome == "accepted")
        replayed = next(row for row in final if row.outcome == "duplicate")
        assert accepted.reward == {"xp_total": 120, "xp_granted": 120}
        assert replayed.reward == {"xp_total": 120, "xp_granted": 0}
        assert "repeated_perfect_sequence" in accepted.flags


def test_worker_timeout_is_audited_from_server_history(
    service, started, clock, database
):
    from app.persistence.security import CommandAudit

    clock.now += timedelta(seconds=15)
    assert service.expire_due() == 1
    with Session(database) as db:
        row = db.scalar(select(CommandAudit).where(CommandAudit.action == "timeout"))
        assert row is not None and row.actor_id == "system:timer"
        assert row.previous_revision == 0 and row.resulting_revision == 1
        assert row.resulting_state == "active"
        assert row.created_at == clock.now


def test_neutral_timeout_never_counts_as_a_perfect_sequence(
    service, scenario, database, clock
):
    import json

    from app.persistence.scenarios import ScenarioRepository
    from app.persistence.security import CommandAudit
    from app.scenarios.schema import ScenarioDocument

    with Session(database) as db, db.begin():
        payload = ScenarioRepository(db).get(scenario.id, 1).model_dump(mode="json")
        payload["id"] = "synthetic-neutral-timeout"
        payload["nodes"][0]["timeout"]["effects"] = []
        payload["nodes"][2]["choices"][0]["condition"] = {"predicates": []}
        neutral = ScenarioDocument.model_validate_json(json.dumps(payload))
        ScenarioRepository(db).add(neutral)
    for index in range(3):
        view = service.start(
            scenario_id=neutral.id, scenario_version=1, employee_id="synthetic-employee"
        )
        clock.now += timedelta(seconds=15)
        service.get(view.session.id)
        result = service.decide(
            view.session.id,
            decision_id=f"recover-{index}",
            node_id="recovery",
            choice_id="recover",
            expected_sequence=1,
        )
        assert result.outcome == "accepted"
    with Session(database) as db:
        rows = db.scalars(
            select(CommandAudit).where(CommandAudit.action == "decision")
        ).all()
        assert all(row.sequence_fingerprint is None for row in rows)
        assert all("repeated_perfect_sequence" not in row.flags for row in rows)


def test_completed_shift_replay_burst_is_flagged_without_extra_xp(
    service, database, clock
):
    from pathlib import Path

    from app.application.identity import IdentityService
    from app.application.shifts import ShiftService
    from app.persistence.gamification import SessionReward
    from app.persistence.scenarios import ScenarioRepository
    from app.persistence.security import CommandAudit
    from app.scenarios.loader import load_document

    IdentityService(database).demo_login()
    with Session(database) as db, db.begin():
        for path in (Path(__file__).parents[3] / "scenarios" / "shifts").glob("*.json"):
            ScenarioRepository(db).add(load_document(path))
    shifts = ShiftService(database, clock=clock, seed_factory=lambda: 42)
    current, _ = shifts.start("demo-employee", key="replay-shift")
    for step in range(3):
        sid = current["current_session_id"]
        for sequence, (node, choice) in enumerate(
            (("start", "assess"), ("check", "confirm"))
        ):
            clock.now += timedelta(seconds=2)
            assert (
                service.decide(
                    sid,
                    decision_id=f"choice-{sequence}",
                    node_id=node,
                    choice_id=choice,
                    expected_sequence=sequence,
                    employee_id="demo-employee",
                ).outcome
                == "accepted"
            )
        command = dict(command_id=f"advance-{step}", expected_step=step, session_id=sid)
        current = shifts.advance(current["id"], "demo-employee", **command)
    assert current["status"] == "completed"
    initial_xp = current["metrics"]["xp"]
    for _ in range(5):
        replay = shifts.advance(current["id"], "demo-employee", **command)
        assert replay["metrics"]["xp"] == initial_xp
    with Session(database) as db:
        rows = db.scalars(
            select(CommandAudit).where(
                CommandAudit.action == "shift_advance",
                CommandAudit.outcome == "duplicate",
            )
        ).all()
        assert len(rows) == 5
        assert sum("frequent_replay" in row.flags for row in rows) == 1
        assert all(row.reward["xp_granted"] == 0 for row in rows)
        assert db.scalar(select(func.sum(SessionReward.xp))) == initial_xp


def test_generic_conflict_audit_enriches_flags_without_duplicates(database, clock):
    from app.application.anti_cheat import record_audit
    from app.persistence.security import CommandAudit

    with Session(database) as db, db.begin():
        for flags in ([], ["conflicting_command"]):
            record_audit(
                db,
                actor_id="synthetic-employee",
                action="shift_advance",
                outcome="conflicting",
                server_time=clock.now,
                flags=flags,
            )
    with Session(database) as db:
        assert all(
            row.flags == ["conflicting_command"]
            for row in db.scalars(select(CommandAudit))
        )
