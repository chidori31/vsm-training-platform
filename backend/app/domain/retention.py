"""Reproducible retention rules; no network, database or wall-clock access."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .common import DomainError, require_integer, require_text, utc_time
from .gameplay import ScenarioSession, SessionStatus
from .scoring import Metric, MetricRef


@dataclass(frozen=True, slots=True)
class Challenge:
    id: str
    starts_at: datetime
    expires_at: datetime
    scenarios: tuple[tuple[str, int], ...]
    target: int

    def __post_init__(self) -> None:
        require_text(self.id, "challenge id")
        object.__setattr__(self, "starts_at", utc_time(self.starts_at, "starts_at"))
        object.__setattr__(self, "expires_at", utc_time(self.expires_at, "expires_at"))
        require_integer(self.target, "target", positive=True)
        items = tuple((key, version) for key, version in self.scenarios)
        for key, version in items:
            require_text(key, "scenario id")
            require_integer(version, "scenario version", positive=True)
        if (
            self.expires_at <= self.starts_at
            or len({key for key, _ in items}) != len(items)
            or not 1 <= self.target <= len(items)
        ):
            raise DomainError("Invalid challenge window or distinct scenario target")
        object.__setattr__(self, "scenarios", items)


@dataclass(frozen=True, slots=True)
class ChallengeProgress:
    progress: int
    completed_scenarios: tuple[str, ...]
    status: Literal["scheduled", "active", "completed", "expired"]


def challenge_progress(
    challenge: Challenge, sessions: Iterable[ScenarioSession], now: datetime
) -> ChallengeProgress:
    now = utc_time(now, "now")
    completed = {
        session.scenario_id
        for session in sessions
        if session.status is SessionStatus.COMPLETED
        and session.completed_at is not None
        and (session.scenario_id, session.scenario_version) in challenge.scenarios
        and challenge.starts_at <= session.started_at
        and session.completed_at < challenge.expires_at
        and session.completed_at <= now
        and session.decisions
        and all(item.choice_id != "__timeout__" for item in session.decisions)
        and session.scores.value(MetricRef(Metric.SAFETY_RATING)) >= 50
    }
    status: Literal["scheduled", "active", "completed", "expired"]
    if len(completed) >= challenge.target:
        status = "completed"
    elif now < challenge.starts_at:
        status = "scheduled"
    elif now >= challenge.expires_at:
        status = "expired"
    else:
        status = "active"
    return ChallengeProgress(
        min(len(completed), challenge.target), tuple(sorted(completed)), status
    )
