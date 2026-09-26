"""Owned learning projections from the existing verified session journal."""

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.application.errors import UseCaseError
from app.application.sessions import SessionView
from app.domain.debrief import SessionDebrief, debrief_for
from app.domain.gameplay import SessionStatus
from app.domain.learning_analytics import LearningAnalytics, competency_analytics
from app.persistence.sessions import SessionRepository, StoredSession


def session_debrief(view: SessionView) -> SessionDebrief:
    if view.session.status is not SessionStatus.COMPLETED:
        raise UseCaseError("result_not_ready", "Session is not completed")
    return debrief_for(view.scenario, view.session)


class LearningService:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def competencies(self, employee_id: str) -> LearningAnalytics:
        with Session(self.engine) as database:
            rows = database.scalars(
                select(StoredSession)
                .where(StoredSession.snapshot["employee_id"].astext == employee_id)
                .order_by(StoredSession.updated_at, StoredSession.id)
            )
            repository = SessionRepository(database)
            return competency_analytics(repository.load(row) for row in rows)
