from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .common import (
    DomainError,
    freeze_items,
    require_integer,
    require_text,
    require_unique,
    utc_time,
)
from .scoring import AddScore, ScoreChange, ScoreState, ScoringPolicy


class SessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class Decision:
    id: str
    session_id: str
    node_id: str
    choice_id: str
    sequence: int
    decided_at: datetime
    effects: tuple[AddScore, ...] = ()
    explanation: str = ""
    score_changes: tuple[ScoreChange, ...] = ()

    def __post_init__(self) -> None:
        for field, value in (
            ("decision id", self.id),
            ("session_id", self.session_id),
            ("node_id", self.node_id),
            ("choice_id", self.choice_id),
        ):
            require_text(value, field)
        require_integer(self.sequence, "decision sequence", positive=True)
        if self.explanation != "":
            require_text(self.explanation, "decision explanation")
        object.__setattr__(self, "decided_at", utc_time(self.decided_at, "decided_at"))
        object.__setattr__(self, "effects", freeze_items(self.effects, AddScore))
        object.__setattr__(
            self, "score_changes", freeze_items(self.score_changes, ScoreChange)
        )


@dataclass(frozen=True, slots=True)
class ScenarioSession:
    id: str
    employee_id: str
    scenario_id: str
    scenario_version: int
    current_node_id: str
    scores: ScoreState
    started_at: datetime
    status: SessionStatus = SessionStatus.ACTIVE
    completed_at: datetime | None = None
    decisions: tuple[Decision, ...] = ()
    initial_scores: ScoreState | None = None
    scoring_policy: ScoringPolicy = ScoringPolicy()

    def __post_init__(self) -> None:
        for field, value in (
            ("session id", self.id),
            ("employee_id", self.employee_id),
            ("scenario_id", self.scenario_id),
            ("current_node_id", self.current_node_id),
        ):
            require_text(value, field)
        require_integer(self.scenario_version, "scenario_version", positive=True)
        object.__setattr__(self, "started_at", utc_time(self.started_at, "started_at"))
        if not isinstance(self.scores, ScoreState) or not isinstance(
            self.status, SessionStatus
        ):
            raise DomainError("Invalid session scores or status")
        if self.initial_scores is not None and not isinstance(
            self.initial_scores, ScoreState
        ):
            raise DomainError("Invalid initial session scores")
        if not isinstance(self.scoring_policy, ScoringPolicy):
            raise DomainError("Invalid session scoring policy")
        if (self.status is SessionStatus.COMPLETED) != (self.completed_at is not None):
            raise DomainError("Only completed sessions require completed_at")
        if self.completed_at is not None:
            object.__setattr__(
                self, "completed_at", utc_time(self.completed_at, "completed_at")
            )
        decisions = freeze_items(self.decisions, Decision)
        require_unique((decision.id for decision in decisions), "decision id")
        previous = self.started_at
        for sequence, decision in enumerate(decisions, start=1):
            if decision.session_id != self.id or decision.sequence != sequence:
                raise DomainError("Inconsistent decision session or sequence")
            if decision.decided_at < previous:
                raise DomainError("Decisions must be chronological")
            previous = decision.decided_at
        if self.completed_at is not None and self.completed_at < previous:
            raise DomainError("Completion precedes session activity")
        object.__setattr__(self, "decisions", decisions)
