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
