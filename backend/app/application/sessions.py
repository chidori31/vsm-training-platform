import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import uuid4

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.domain.common import DomainError, require_integer, require_text, utc_time
from app.domain.engine import advance, expire, node_deadline, start_session
from app.domain.gameplay import ScenarioSession
from app.domain.scenario import Scenario
from app.domain.scoring import Metric, MetricRef, ScoreState
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
            document = ScenarioRepository(database).get(scenario_id, scenario_version)
            if document is None:
                raise ScenarioNotFound("Scenario version not found")
            scenario = document.to_domain()
            values = {
                MetricRef(Metric.PASSENGER_LOYALTY): 50,
                MetricRef(Metric.SAFETY_RATING): 50,
            }
            values.update(
                {
                    MetricRef(Metric.COMPETENCY, key): 0
                    for key in scenario.competency_ids
                }
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
            view = self._view(scenario, session, now)
        return view

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
        session = expire(
            scenario,
            session,
            node_id=session.current_node_id,
            decision_id=f"timeout:{session.id}:{len(session.decisions)}",
            expected_sequence=len(session.decisions),
            now=now,
        )
        repository.save(row, scenario, session)
        return session, True

    def get(self, session_id: str) -> SessionView:
        with Session(self.engine) as database, database.begin():
            repository = SessionRepository(database)
            row = repository.lock(session_id)
            if row is None:
                raise SessionNotFound("Session not found")
            scenario, session = repository.load(row)
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
            now = utc_time(self.clock(database), "server time")
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
                    result = DecisionResult(
                        self._view(scenario, updated, now),
                        "duplicate" if duplicate else "accepted",
                        decision_id,
                    )
            # Conflict is a committed result: do not roll back an automatic timeout.
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
