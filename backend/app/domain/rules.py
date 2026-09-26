from dataclasses import dataclass
from enum import StrEnum

from .common import DomainError, freeze_items, require_integer
from .scoring import MetricRef, ScoreState


class Operator(StrEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"


class ConditionMode(StrEnum):
    ALL = "all"
    ANY = "any"


@dataclass(frozen=True, slots=True)
class Predicate:
    metric: MetricRef
    operator: Operator
    value: int

    def __post_init__(self) -> None:
        if not isinstance(self.metric, MetricRef) or not isinstance(
            self.operator, Operator
        ):
            raise DomainError("Unknown predicate metric or operator")
        require_integer(self.value, "predicate value")

    def matches(self, state: ScoreState) -> bool:
        actual = state.value(self.metric)
        match self.operator:
            case Operator.EQ:
                return actual == self.value
            case Operator.NE:
                return actual != self.value
            case Operator.GT:
                return actual > self.value
            case Operator.GTE:
                return actual >= self.value
            case Operator.LT:
                return actual < self.value
            case Operator.LTE:
                return actual <= self.value
        raise DomainError("Unknown predicate operator")


@dataclass(frozen=True, slots=True)
class Condition:
    mode: ConditionMode = ConditionMode.ALL
    predicates: tuple[Predicate, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ConditionMode):
            raise DomainError("Unknown condition mode")
        predicates = freeze_items(self.predicates, Predicate)
        if len(predicates) > 64:
            raise DomainError("Condition exceeds 64 predicates")
        if self.mode is ConditionMode.ANY and not predicates:
            raise DomainError("ANY condition must not be empty")
        object.__setattr__(self, "predicates", predicates)

    def matches(self, state: ScoreState) -> bool:
        results = tuple(predicate.matches(state) for predicate in self.predicates)
        return all(results) if self.mode is ConditionMode.ALL else any(results)
