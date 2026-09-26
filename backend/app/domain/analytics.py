from dataclasses import dataclass

from .gameplay import ScenarioSession, SessionStatus
from .scoring import ScoreState


@dataclass(frozen=True, slots=True)
class SessionSummary:
    session_id: str
    scenario_id: str
    scenario_version: int
    status: SessionStatus
    decision_count: int
    duration_seconds: float | None
    scores: ScoreState


def summarize_session(session: ScenarioSession) -> SessionSummary:
    duration = None
    if session.completed_at is not None:
        duration = (session.completed_at - session.started_at).total_seconds()
    return SessionSummary(
        session.id,
        session.scenario_id,
        session.scenario_version,
        session.status,
        len(session.decisions),
        duration,
        session.scores,
    )
