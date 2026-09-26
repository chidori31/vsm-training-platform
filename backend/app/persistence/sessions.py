import json
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db import Base
from app.domain.common import DomainError
from app.domain.engine import node_deadline
from app.domain.gameplay import ScenarioSession
from app.domain.scenario import Scenario
from app.scenarios.session_state import dump_session, load_session

from .invariants import SESSION_ENVELOPE
from .scenarios import ScenarioRepository


class StoredSession(Base):
    __tablename__ = "scenario_sessions"
    __table_args__ = (
        CheckConstraint(SESSION_ENVELOPE, name="ck_session_envelope"),
        ForeignKeyConstraint(
            ["scenario_id", "scenario_version"],
            ["scenario_versions.id", "scenario_versions.version"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("revision >= 0", name="ck_session_revision"),
        CheckConstraint("state IN ('active', 'completed')", name="ck_session_state"),
        CheckConstraint(
            "state != 'completed' OR deadline IS NULL", name="ck_session_final_deadline"
        ),
        Index(
            "ix_session_due",
            "deadline",
            postgresql_where="state = 'active' AND deadline IS NOT NULL",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    scenario_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scenario_version: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SessionRepository:
    """Caller owns the transaction; updates must hold the session row lock."""

    def __init__(self, database: Session) -> None:
        self.database = database

    def add(self, scenario: Scenario, session: ScenarioSession) -> StoredSession:
        row = StoredSession(
            id=session.id, scenario_id=scenario.id, scenario_version=scenario.version
        )
        self.save(row, scenario, session)
        self.database.add(row)
        self.database.flush()
        return row

    def lock(self, session_id: str) -> StoredSession | None:
        return self.database.scalar(
            select(StoredSession)
            .where(StoredSession.id == session_id)
            .with_for_update()
        )

    def lock_due(self, excluded: set[str]) -> StoredSession | None:
        query = (
            select(StoredSession)
            .where(
                StoredSession.state == "active",
                StoredSession.deadline <= func.clock_timestamp(),
                StoredSession.id.not_in(excluded),
            )
            .order_by(StoredSession.deadline, StoredSession.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return self.database.scalar(query)

    def load(self, row: StoredSession) -> tuple[Scenario, ScenarioSession]:
        document = ScenarioRepository(self.database).get(
            row.scenario_id, row.scenario_version
        )
        if document is None:
            raise DomainError("Saved session scenario version is unavailable")
        scenario = document.to_domain()
        session = load_session(scenario, json.dumps(row.snapshot))
        if (
            row.id != session.id
            or row.scenario_id != session.scenario_id
            or row.scenario_version != session.scenario_version
            or row.revision != len(session.decisions)
            or row.state != session.status.value
            or row.deadline != node_deadline(scenario, session)
        ):
            raise DomainError("Saved session metadata disagrees with its snapshot")
        return scenario, session

    def save(
        self, row: StoredSession, scenario: Scenario, session: ScenarioSession
    ) -> None:
        row.snapshot = json.loads(dump_session(scenario, session))
        row.revision = len(session.decisions)
        row.state = session.status.value
        row.deadline = node_deadline(scenario, session)
        row.updated_at = (
            session.decisions[-1].decided_at
            if session.decisions
            else session.started_at
        )
