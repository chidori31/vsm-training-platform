import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from time import monotonic

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.domain.common import DomainError
from app.domain.scoring import Metric, MetricRef
from app.scenarios.session_state import load_session

pytestmark = pytest.mark.postgres


def decide(service, session_id, *, decision_id="choice-1", choice_id="help"):
    return service.decide(
        session_id,
        decision_id=decision_id,
        node_id="start",
        choice_id=choice_id,
        expected_sequence=0,
    )


def score(session, metric):
    return session.scores.value(MetricRef(metric))


def stored(database, session_id):
    from app.persistence.sessions import StoredSession

    with Session(database) as session:
        row = session.get(StoredSession, session_id)
        assert row is not None
        session.expunge(row)
        return row


def assert_metadata(database, scenario, view):
    row = stored(database, view.session.id)
    assert row.id == view.session.id
    assert row.scenario_id == scenario.id
    assert row.scenario_version == scenario.version
    assert row.revision == len(view.session.decisions)
    assert row.state == view.session.status.value
    assert row.deadline == view.deadline
    assert row.snapshot["format_version"] == 2
    assert load_session(scenario, json.dumps(row.snapshot)) == view.session


def wait_for_blocked_connection(database, blocking_pid):
    """Prove PostgreSQL sees a waiter, instead of merely launching two threads."""
    until = monotonic() + 8
    pause = Event()
    with database.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as connection:
        while monotonic() < until:
            blocked = connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                    "WHERE :pid = ANY(pg_blocking_pids(pid)))"
                ),
                {"pid": blocking_pid},
            )
            if blocked:
                return
            pause.wait(0.01)
    pytest.fail("The second database connection never waited for the row lock")


class GateClock:
    """Pause the lock-owning service call at its clock read."""

    def __init__(self, now):
        self.now = now
        self.entered = Event()
        self.release = Event()
        self.pid = None

    def __call__(self, session):
        self.pid = session.scalar(text("SELECT pg_backend_pid()"))
        self.entered.set()
        if not self.release.wait(10):
            raise TimeoutError("Test did not release the lock-owning clock")
        return self.now


def test_default_clock_uses_current_database_time(database, scenario):
    from app.application.sessions import SessionService

    with database.connect() as connection:
        before = connection.scalar(text("SELECT clock_timestamp()"))
    view = SessionService(database).start(
        scenario_id=scenario.id,
        scenario_version=1,
        employee_id="database-clock-employee",
    )
    with database.connect() as connection:
        after = connection.scalar(text("SELECT clock_timestamp()"))
    assert before <= view.server_time <= after
    assert view.session.started_at == view.server_time
    assert view.deadline == view.server_time + timedelta(seconds=15)
    assert_metadata(database, scenario, view)


def test_start_and_clamped_decision_persist_independent_scores(
    database, scenario, service, started, clock
):
    assert score(started.session, Metric.PASSENGER_LOYALTY) == 50
    assert score(started.session, Metric.SAFETY_RATING) == 50
    assert (
        started.session.scores.value(MetricRef(Metric.COMPETENCY, "communication")) == 0
    )
    assert_metadata(database, scenario, started)
    clock.now += timedelta(seconds=1)
    result = decide(service, started.session.id)
    assert result.outcome == "accepted"
    assert result.acknowledged_decision_id == "choice-1"
    assert result.error is None
    assert score(result.view.session, Metric.PASSENGER_LOYALTY) == 100
    assert score(result.view.session, Metric.SAFETY_RATING) == 55
    assert (
        result.view.session.scores.value(MetricRef(Metric.COMPETENCY, "communication"))
        == 3
    )
    decision = result.view.session.decisions[0]
    assert decision.explanation == "Help improves separate metrics."
    assert len(decision.score_changes) == 3
    loyalty_change = next(
        change
        for change in decision.score_changes
        if change.metric == MetricRef(Metric.PASSENGER_LOYALTY)
    )
    assert (
        loyalty_change.before,
        loyalty_change.requested_delta,
        loyalty_change.applied_delta,
        loyalty_change.after,
    ) == (50, 60, 50, 100)
    assert loyalty_change.explanation == "Help improves separate metrics."
    assert_metadata(database, scenario, result.view)


def test_negative_effects_stop_at_lower_bounds(service, started):
    result = decide(service, started.session.id, choice_id="decline")
    assert result.outcome == "accepted"
    assert score(result.view.session, Metric.PASSENGER_LOYALTY) == 0
    assert score(result.view.session, Metric.SAFETY_RATING) == 0
    assert result.view.deadline is None


def test_get_reconciles_timeout_once_and_commits_it(
    database, scenario, service, started, clock
):
    clock.now += timedelta(seconds=15)
    expired = service.get(started.session.id)
    assert expired.session.current_node_id == "recovery"
    assert len(expired.session.decisions) == 1
    assert_metadata(database, scenario, expired)
    assert service.get(started.session.id).session == expired.session


def test_late_rejected_decision_commits_timeout_and_recovery_works(
    database, scenario, service, started, clock
):
    clock.now += timedelta(seconds=15)
    result = decide(service, started.session.id)
    assert result.outcome == "timed_out"
    assert result.acknowledged_decision_id is None
    assert result.error
    assert result.view.session.current_node_id == "recovery"
    assert len(result.view.session.decisions) == 1
    assert result.view.session.decisions[0].choice_id == "__timeout__"
    assert result.view.session.decisions[0].id.startswith("timeout:")
    assert score(result.view.session, Metric.PASSENGER_LOYALTY) == 45
    assert score(result.view.session, Metric.SAFETY_RATING) == 30
    assert_metadata(database, scenario, result.view)

    unavailable = service.decide(
        started.session.id,
        decision_id="unavailable-recovery",
        node_id="recovery",
        choice_id="unavailable",
        expected_sequence=1,
    )
    assert unavailable.outcome == "rejected"
    assert len(unavailable.view.session.decisions) == 1
    recovered = service.decide(
        started.session.id,
        decision_id="recovery-1",
        node_id="recovery",
        choice_id="recover",
        expected_sequence=1,
    )
    assert recovered.outcome == "accepted"
    assert score(recovered.view.session, Metric.PASSENGER_LOYALTY) == 49
    assert score(recovered.view.session, Metric.SAFETY_RATING) == 40
    assert recovered.view.session.status.value == "completed"
    assert_metadata(database, scenario, recovered.view)


def test_retry_after_later_timeout_acknowledges_original_and_returns_latest_state(
    database, scenario, service, started, clock
):
    clock.now += timedelta(seconds=1)
    assert decide(service, started.session.id).outcome == "accepted"
    clock.now += timedelta(seconds=10)
    retry = decide(service, started.session.id)
    assert retry.outcome == "duplicate"
    assert retry.acknowledged_decision_id == "choice-1"
    assert retry.view.session.current_node_id == "expired"
    assert len(retry.view.session.decisions) == 2
    assert score(retry.view.session, Metric.PASSENGER_LOYALTY) == 100
    assert score(retry.view.session, Metric.SAFETY_RATING) == 40
    assert_metadata(database, scenario, retry.view)


def test_same_id_with_different_payload_and_reserved_ids_cannot_apply(service, started):
    with pytest.raises(DomainError, match="reserved"):
        decide(service, started.session.id, decision_id="timeout:forged")
    assert len(service.get(started.session.id).session.decisions) == 0
    assert decide(service, started.session.id).outcome == "accepted"
    conflicting = decide(service, started.session.id, choice_id="decline")
    assert conflicting.outcome == "rejected"
    assert len(conflicting.view.session.decisions) == 1
    assert score(conflicting.view.session, Metric.PASSENGER_LOYALTY) == 100


@pytest.mark.parametrize("identical", [False, True])
def test_concurrent_submissions_apply_one_event(
    database, scenario, service, started, identical
):
    barrier = Barrier(2)

    def submit(index):
        barrier.wait(timeout=10)
        return decide(
            service,
            started.session.id,
            decision_id="same-id" if identical else f"choice-{index}",
            choice_id="help" if identical or index == 0 else "decline",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit, index) for index in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert sorted(result.outcome for result in results) == (
        ["accepted", "duplicate"] if identical else ["accepted", "rejected"]
    )
    view = service.get(started.session.id)
    assert len(view.session.decisions) == 1
    expected = 100 if view.session.decisions[0].choice_id == "help" else 0
    assert score(view.session, Metric.PASSENGER_LOYALTY) == expected
    assert_metadata(database, scenario, view)


def test_timeout_holds_lock_while_user_waits_and_only_timeout_commits(
    database, scenario, service, started, clock
):
    from app.application.sessions import SessionService

    clock.now += timedelta(seconds=15)
    gate = GateClock(clock.now)
    worker = SessionService(database, clock=gate)
    with ThreadPoolExecutor(max_workers=2) as pool:
        worker_future = pool.submit(worker.expire_due)
        try:
            assert gate.entered.wait(8)
            user_future = pool.submit(decide, service, started.session.id)
            wait_for_blocked_connection(database, gate.pid)
        finally:
            gate.release.set()
        assert worker_future.result(timeout=15) == 1
        result = user_future.result(timeout=15)
    assert result.outcome in {"timed_out", "rejected"}
    assert len(result.view.session.decisions) == 1
    assert result.view.session.decisions[0].choice_id == "__timeout__"
    assert score(result.view.session, Metric.SAFETY_RATING) == 30
    assert_metadata(database, scenario, result.view)


def test_user_holds_lock_and_worker_skips_then_rechecks_new_node(
    database, scenario, started, clock
):
    from app.application.sessions import SessionService

    clock.now += timedelta(seconds=14)
    gate = GateClock(clock.now)
    user = SessionService(database, clock=gate)
    worker = SessionService(database, clock=clock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        user_future = pool.submit(decide, user, started.session.id)
        try:
            assert gate.entered.wait(8)
            worker_future = pool.submit(worker.expire_due)
            assert worker_future.result(timeout=5) == 0
        finally:
            gate.release.set()
        result = user_future.result(timeout=15)
    assert result.outcome == "accepted"
    assert result.view.session.current_node_id == "followup"
    assert result.view.deadline == clock.now + timedelta(seconds=10)
    assert worker.expire_due() == 0
    assert len(worker.get(started.session.id).session.decisions) == 1
    assert_metadata(database, scenario, result.view)


def test_request_waiting_across_deadline_reads_clock_after_lock(
    database, service, started, clock
):
    from app.persistence.sessions import StoredSession

    clock.now += timedelta(seconds=14)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(database) as holder, holder.begin():
            holder.scalar(
                select(StoredSession)
                .where(StoredSession.id == started.session.id)
                .with_for_update()
            )
            blocking_pid = holder.scalar(text("SELECT pg_backend_pid()"))
            future = pool.submit(decide, service, started.session.id)
            wait_for_blocked_connection(database, blocking_pid)
            clock.now += timedelta(seconds=1)
        result = future.result(timeout=15)
    assert result.outcome == "timed_out"
    assert len(result.view.session.decisions) == 1
    assert result.view.session.decisions[0].choice_id == "__timeout__"


def test_two_workers_apply_one_timeout(database, service, started, clock):
    from app.application.sessions import SessionService

    clock.now += timedelta(seconds=15)
    barrier = Barrier(2)

    def expire():
        barrier.wait(timeout=10)
        return SessionService(database, clock=clock).expire_due()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(expire) for _ in range(2)]
        assert sorted(future.result(timeout=15) for future in futures) == [0, 1]
    view = service.get(started.session.id)
    assert len(view.session.decisions) == 1
    assert score(view.session, Metric.SAFETY_RATING) == 30


def test_new_worker_finds_overdue_persisted_session_without_a_read_request(
    database, scenario, started, clock
):
    from app.application.sessions import SessionService

    clock.now += timedelta(hours=1)
    restarted = SessionService(database, clock=clock)
    assert restarted.expire_due() == 1
    row = stored(database, started.session.id)
    saved = load_session(scenario, json.dumps(row.snapshot))
    assert row.revision == 1
    assert row.deadline is None
    assert saved.current_node_id == "recovery"
    assert saved.decisions[0].decided_at == clock.now
    assert restarted.expire_due() == 0


def test_failed_write_rolls_back_history_scores_and_deadline(
    database, scenario, service, started
):
    original = stored(database, started.session.id)

    def fail_after_write(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE SCENARIO_SESSIONS"):
            raise RuntimeError("Injected failure after the real database update")

    event.listen(database, "after_cursor_execute", fail_after_write)
    try:
        with pytest.raises(RuntimeError, match="Injected failure"):
            decide(service, started.session.id)
    finally:
        event.remove(database, "after_cursor_execute", fail_after_write)
    persisted = stored(database, started.session.id)
    assert persisted.snapshot == original.snapshot
    assert persisted.revision == 0
    assert persisted.deadline == original.deadline
    assert_metadata(database, scenario, service.get(started.session.id))


def test_worker_logs_bad_row_and_still_processes_other_due_sessions(
    database, scenario, service, started, clock, caplog
):
    from app.persistence.sessions import StoredSession

    other = service.start(
        scenario_id=scenario.id,
        scenario_version=1,
        employee_id="second-synthetic-employee",
    )
    with Session(database) as session, session.begin():
        row = session.get(StoredSession, started.session.id)
        row.snapshot = {**row.snapshot, "scores": []}
    clock.now += timedelta(seconds=15)
    assert service.expire_due() == 1
    assert caplog.records
    assert stored(database, started.session.id).revision == 0
    assert stored(database, other.session.id).revision == 1
    assert service.get(other.session.id).session.current_node_id == "recovery"
