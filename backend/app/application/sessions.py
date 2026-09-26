import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import uuid4

from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.application.anti_cheat import record_session_command
from app.application.errors import UseCaseError
from app.application.gamification import settle_profile
from app.domain.common import DomainError, require_integer, require_text, utc_time
from app.domain.engine import (
    advance,
    available_choices,
    current_node,
    expire,
    node_deadline,
    start_session,
)
from app.domain.gameplay import ScenarioSession, SessionStatus
from app.domain.scenario import Choice, Scenario, ScenarioNode
from app.domain.scoring import Metric, MetricRef, ScoreState
from app.persistence.identity import SessionStartKey
from app.persistence.scenarios import ScenarioRepository
from app.persistence.sessions import SessionRepository, StoredSession

logger = logging.getLogger(__name__)


class SessionNotFound(LookupError):
    pass


class ScenarioNotFound(LookupError):
    pass


def database_time(database: Session) -> datetime:
    # A separate statement AFTER FOR UPDATE: transaction time can precede a lock wait.
    value = database.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise DomainError("Database did not provide an authoritative timestamp")
    return utc_time(value, "database time")


@dataclass(frozen=True)
class SessionView:
    session: ScenarioSession
    server_time: datetime
    deadline: datetime | None
    scenario: Scenario

    @property
    def node(self) -> ScenarioNode:
        return current_node(self.scenario, self.session)

    @property
    def choices(self) -> tuple[Choice, ...]:
        return available_choices(self.scenario, self.session, now=self.server_time)


@dataclass(frozen=True)
class DecisionResult:
    view: SessionView
    outcome: Literal["accepted", "duplicate", "timed_out", "rejected"]
    acknowledged_decision_id: str | None = None
    error: str | None = None


class SessionService:
    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[Session], datetime] = database_time,
    ) -> None:
        self.engine = engine
        self.clock = clock

    def start(
        self, *, scenario_id: str, scenario_version: int, employee_id: str
    ) -> SessionView:
        require_text(scenario_id, "scenario_id")
        require_text(employee_id, "employee_id")
        require_integer(scenario_version, "scenario_version", positive=True)
        with Session(self.engine) as database, database.begin():
            view = self._start(database, scenario_id, scenario_version, employee_id)
        return view

    def _start(
        self,
        database: Session,
        scenario_id: str,
        scenario_version: int,
        employee_id: str,
        *,
        client_event_id: str | None = None,
        action: str = "start",
    ) -> SessionView:
        document = ScenarioRepository(database).get(scenario_id, scenario_version)
        if document is None:
            raise ScenarioNotFound("Scenario version not found")
        scenario = document.to_domain()
        values = {
            MetricRef(Metric.PASSENGER_LOYALTY): 50,
            MetricRef(Metric.SAFETY_RATING): 50,
        }
        values.update(
            {MetricRef(Metric.COMPETENCY, key): 0 for key in scenario.competency_ids}
        )
        now = utc_time(self.clock(database), "server time")
        session = start_session(
            scenario,
            session_id=str(uuid4()),
            employee_id=employee_id,
            initial_scores=ScoreState(values),
            now=now,
        )
        SessionRepository(database).add(scenario, session)
        if session.status is SessionStatus.COMPLETED:
            settle_profile(database, session.employee_id)
        record_session_command(
            database,
            actor_id=employee_id,
            action=action,
            client_event_id=client_event_id or session.id,
            before=None,
            after=session,
            outcome="accepted",
            server_time=now,
            expected_revision=0,
        )
        return self._view(scenario, session, now)

    def start_idempotent(
        self, *, scenario_id: str, scenario_version: int, employee_id: str, key: str
    ) -> tuple[SessionView, bool]:
        require_text(key, "idempotency key")
        with Session(self.engine) as database, database.begin():
            database.execute(
                insert(SessionStartKey)
                .values(
                    employee_id=employee_id,
                    key=key,
                    scenario_id=scenario_id,
                    scenario_version=scenario_version,
                )
                .on_conflict_do_nothing(index_elements=["employee_id", "key"])
            )
            record = database.scalar(
                select(SessionStartKey)
                .where(
                    SessionStartKey.employee_id == employee_id,
                    SessionStartKey.key == key,
                )
                .with_for_update()
            )
            if record is None:
                raise DomainError("Missing session start key")
            if (record.scenario_id, record.scenario_version) != (
                scenario_id,
                scenario_version,
            ):
                raise UseCaseError(
                    "idempotency_conflict",
                    "Key already used for a different start request",
                )
            replayed = record.session_id is not None
            if record.session_id is None:
                view = self._start(
                    database,
                    scenario_id,
                    scenario_version,
                    employee_id,
                    client_event_id=key,
                )
                record.session_id = view.session.id
            else:
                repository = SessionRepository(database)
                row = repository.lock(record.session_id)
                if row is None:
                    raise DomainError("Session start key refers to a missing session")
                scenario, session = repository.load(row)
                if session.employee_id != employee_id:
                    raise DomainError("Session start key owner mismatch")
                now = utc_time(self.clock(database), "server time")
                before = session
                session, _ = self._expire_due(repository, row, scenario, session, now)
                record_session_command(
                    database,
                    actor_id=employee_id,
                    action="start",
                    client_event_id=key,
                    before=before,
                    after=session,
                    outcome="duplicate",
                    server_time=now,
                )
                view = self._view(scenario, session, now)
        return view, replayed

    @staticmethod
    def _view(
        scenario: Scenario, session: ScenarioSession, now: datetime
    ) -> SessionView:
        return SessionView(session, now, node_deadline(scenario, session), scenario)

    @staticmethod
    def _expire_due(
        repository: SessionRepository,
        row: StoredSession,
        scenario: Scenario,
        session: ScenarioSession,
        now: datetime,
    ) -> tuple[ScenarioSession, bool]:
        if row.deadline is None or now < row.deadline:
            return session, False
        before = session
        session = expire(
            scenario,
            session,
            node_id=session.current_node_id,
            decision_id=f"timeout:{session.id}:{len(session.decisions)}",
            expected_sequence=len(session.decisions),
            now=now,
        )
        repository.save(row, scenario, session)
        if session.status is SessionStatus.COMPLETED:
            settle_profile(repository.database, session.employee_id)
        record_session_command(
            repository.database,
            actor_id="system:timer",
            action="timeout",
            client_event_id=session.decisions[-1].id,
            before=before,
            after=session,
            outcome="accepted",
            server_time=now,
            expected_revision=len(before.decisions),
            node_id=before.current_node_id,
        )
        return session, True

    def get(self, session_id: str, *, employee_id: str | None = None) -> SessionView:
        with Session(self.engine) as database, database.begin():
            repository = SessionRepository(database)
            row = repository.lock(session_id)
            if row is None:
                raise SessionNotFound("Session not found")
            scenario, session = repository.load(row)
            if employee_id is not None and session.employee_id != employee_id:
                raise SessionNotFound("Session not found")
            now = utc_time(self.clock(database), "server time")
            session, _ = self._expire_due(repository, row, scenario, session, now)
            view = self._view(scenario, session, now)
        return view

    def decide(
        self,
        session_id: str,
        *,
        decision_id: str,
        node_id: str,
        choice_id: str,
        expected_sequence: int,
        employee_id: str | None = None,
    ) -> DecisionResult:
        require_text(decision_id, "decision_id")
        if decision_id.startswith("timeout:"):
            raise DomainError("The timeout: decision id namespace is reserved")
        require_text(node_id, "node_id")
        require_text(choice_id, "choice_id")
        require_integer(expected_sequence, "expected_sequence")
        if expected_sequence < 0:
            raise DomainError("expected_sequence must not be negative")
        with Session(self.engine) as database, database.begin():
            repository = SessionRepository(database)
            row = repository.lock(session_id)
            if row is None:
                raise SessionNotFound("Session not found")
            scenario, session = repository.load(row)
            if employee_id is not None and session.employee_id != employee_id:
                raise SessionNotFound("Session not found")
            now = utc_time(self.clock(database), "server time")
            before = session
            session, timed_out = self._expire_due(
                repository, row, scenario, session, now
            )
            duplicate = any(event.id == decision_id for event in session.decisions)
            if timed_out and not duplicate:
                result = DecisionResult(
                    self._view(scenario, session, now),
                    "timed_out",
                    error="Choice deadline expired; timeout outcome saved",
                )
            else:
                try:
                    updated = advance(
                        scenario,
                        session,
                        node_id=node_id,
                        choice_id=choice_id,
                        decision_id=decision_id,
                        expected_sequence=expected_sequence,
                        now=now,
                    )
                except DomainError as error:
                    result = DecisionResult(
                        self._view(scenario, session, now), "rejected", error=str(error)
                    )
                else:
                    if not duplicate:
                        repository.save(row, scenario, updated)
                        if updated.status is SessionStatus.COMPLETED:
                            settle_profile(database, updated.employee_id)
                    result = DecisionResult(
                        self._view(scenario, updated, now),
                        "duplicate" if duplicate else "accepted",
                        decision_id,
                    )
            # Conflict is a committed result: do not roll back an automatic timeout.
            record_session_command(
                database,
                actor_id=session.employee_id,
                action="decision",
                client_event_id=decision_id,
                before=before,
                after=result.view.session,
                outcome=result.outcome,
                server_time=now,
                expected_revision=expected_sequence,
                node_id=node_id,
                choice_id=choice_id,
            )
        return result

    def expire_due(self, *, limit: int = 100) -> int:
        require_integer(limit, "limit", positive=True)
        visited: set[str] = set()
        processed = 0
        for _ in range(limit):
            try:
                with Session(self.engine) as database, database.begin():
                    repository = SessionRepository(database)
                    row = repository.lock_due(visited)
                    if row is None:
                        break
                    visited.add(row.id)
                    scenario, session = repository.load(row)
                    now = utc_time(self.clock(database), "server time")
                    _, changed = self._expire_due(
                        repository, row, scenario, session, now
                    )
                processed += int(changed)
            except (DomainError, ValueError):
                logger.error("Invalid saved session skipped during timeout sweep")
        return processed
