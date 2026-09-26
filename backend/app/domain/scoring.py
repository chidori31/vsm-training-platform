from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from .common import DomainError, freeze_items, require_integer, require_text


class Metric(StrEnum):
    PASSENGER_LOYALTY = "passenger_loyalty"
    SAFETY_RATING = "safety_rating"
    COMPETENCY = "competency"


@dataclass(frozen=True, slots=True)
class MetricRef:
    metric: Metric
    competency_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.metric, Metric):
            raise DomainError("Unknown metric type")
        if self.metric is Metric.COMPETENCY:
            if self.competency_id is None:
                raise DomainError("Competency metric requires competency_id")
            require_text(self.competency_id, "competency_id")
        elif self.competency_id is not None:
            raise DomainError("Only competency metrics accept competency_id")


@dataclass(frozen=True, slots=True)
class ScoreBounds:
    minimum: int = 0
    maximum: int = 100

    def __post_init__(self) -> None:
        require_integer(self.minimum, "score minimum")
        require_integer(self.maximum, "score maximum")
        if self.minimum > self.maximum:
            raise DomainError("Score minimum must not exceed maximum")


@dataclass(frozen=True, slots=True)
class ScoringPolicy:
    loyalty: ScoreBounds = ScoreBounds()
    safety: ScoreBounds = ScoreBounds()

    def __post_init__(self) -> None:
        if not isinstance(self.loyalty, ScoreBounds) or not isinstance(
            self.safety, ScoreBounds
        ):
            raise DomainError("Scoring policy requires ScoreBounds")

    def bounds_for(self, metric: MetricRef) -> ScoreBounds | None:
        if metric.metric is Metric.PASSENGER_LOYALTY:
            return self.loyalty
        if metric.metric is Metric.SAFETY_RATING:
            return self.safety
        return None


@dataclass(frozen=True, slots=True)
class ScoreChange:
    metric: MetricRef
    before: int
    requested_delta: int
    after: int
    explanation: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.metric, MetricRef):
            raise DomainError("Expected MetricRef")
        require_integer(self.before, "score before")
        require_integer(self.requested_delta, "requested score delta")
        require_integer(self.after, "score after")
        if self.explanation != "":
            require_text(self.explanation, "score change explanation")

    @property
    def applied_delta(self) -> int:
        return self.after - self.before


@dataclass(frozen=True, slots=True)
class ScoreState:
    values: Mapping[MetricRef, int]

    def __post_init__(self) -> None:
        copied = dict(self.values)
        for metric, value in copied.items():
            if not isinstance(metric, MetricRef):
                raise DomainError("Expected MetricRef key")
            require_integer(value, "score")
        object.__setattr__(self, "values", MappingProxyType(copied))

    def value(self, metric: MetricRef) -> int:
        try:
            return self.values[metric]
        except KeyError as error:
            raise DomainError("Unknown metric in score state") from error


@dataclass(frozen=True, slots=True)
class AddScore:
    metric: MetricRef
    delta: int

    def __post_init__(self) -> None:
        if not isinstance(self.metric, MetricRef):
            raise DomainError("Expected MetricRef")
        require_integer(self.delta, "delta")


def apply_effects(state: ScoreState, effects: tuple[AddScore, ...]) -> ScoreState:
    result = dict(state.values)
    for effect in freeze_items(effects, AddScore):
        if effect.metric not in result:
            raise DomainError("Unknown metric in score effect")
        result[effect.metric] += effect.delta
    return ScoreState(result)


def apply_scored_effects(
    state: ScoreState,
    effects: tuple[AddScore, ...],
    *,
    policy: ScoringPolicy,
    explanation: str = "",
) -> tuple[ScoreState, tuple[ScoreChange, ...]]:
    """Aggregate each metric, clamp once and journal in first-effect order."""
    if not isinstance(policy, ScoringPolicy):
        raise DomainError("Expected ScoringPolicy")
    deltas: dict[MetricRef, int] = {}
    for effect in freeze_items(effects, AddScore):
        if effect.metric not in state.values:
            raise DomainError("Unknown metric in score effect")
        deltas[effect.metric] = deltas.get(effect.metric, 0) + effect.delta
    result = dict(state.values)
    changes = []
    for metric, delta in deltas.items():
        before = result[metric]
        after = before + delta
        bounds = policy.bounds_for(metric)
        if bounds is not None:
            after = min(bounds.maximum, max(bounds.minimum, after))
        result[metric] = after
        changes.append(ScoreChange(metric, before, delta, after, explanation))
    return ScoreState(result), tuple(changes)
