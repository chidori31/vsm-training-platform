from dataclasses import FrozenInstanceError

import pytest

from app.domain.common import DomainError
from app.domain.rules import Condition, ConditionMode, Operator, Predicate
from app.domain.scoring import AddScore, Metric, MetricRef, ScoreState, apply_effects

SAFETY = MetricRef(Metric.SAFETY_RATING)
LOYALTY = MetricRef(Metric.PASSENGER_LOYALTY)
SERVICE = MetricRef(Metric.COMPETENCY, "service")


@pytest.mark.parametrize(
    ("operator", "threshold", "expected"),
    [
        (Operator.EQ, 9, False),
        (Operator.EQ, 10, True),
        (Operator.EQ, 11, False),
        (Operator.NE, 9, True),
        (Operator.NE, 10, False),
        (Operator.NE, 11, True),
        (Operator.GT, 9, True),
        (Operator.GT, 10, False),
        (Operator.GT, 11, False),
        (Operator.GTE, 9, True),
        (Operator.GTE, 10, True),
        (Operator.GTE, 11, False),
        (Operator.LT, 9, False),
        (Operator.LT, 10, False),
        (Operator.LT, 11, True),
        (Operator.LTE, 9, False),
        (Operator.LTE, 10, True),
        (Operator.LTE, 11, True),
    ],
)
def test_predicate_comparison_boundaries(operator, threshold, expected):
    state = ScoreState({SAFETY: 10})
    assert (
        Condition(predicates=(Predicate(SAFETY, operator, threshold),)).matches(state)
        is expected
    )


def test_all_any_and_unconditional():
    state = ScoreState({SAFETY: 10})
    predicates = (Predicate(SAFETY, Operator.GT, 5), Predicate(SAFETY, Operator.LT, 5))
    assert not Condition(ConditionMode.ALL, predicates).matches(state)
    assert Condition(ConditionMode.ANY, predicates).matches(state)
    assert Condition().matches(state)
    with pytest.raises(DomainError):
        Condition(ConditionMode.ANY)


def test_missing_metric_fails_even_after_a_true_predicate():
    condition = Condition(
        ConditionMode.ANY,
        (
            Predicate(SAFETY, Operator.EQ, 10),
            Predicate(SERVICE, Operator.GT, 0),
        ),
    )
    with pytest.raises(DomainError, match="Unknown metric"):
        condition.matches(ScoreState({SAFETY: 10}))


def test_effects_return_new_snapshot_and_do_not_modify_input():
    source = {SAFETY: 10, LOYALTY: 0, SERVICE: 2}
    state = ScoreState(source)
    source[SAFETY] = -999
    result = apply_effects(
        state, (AddScore(SAFETY, -3), AddScore(SERVICE, 4), AddScore(SAFETY, 1))
    )
    assert dict(result.values) == {SAFETY: 8, LOYALTY: 0, SERVICE: 6}
    assert dict(state.values) == {SAFETY: 10, LOYALTY: 0, SERVICE: 2}
    with pytest.raises(TypeError):
        state.values[SAFETY] = 0
    with pytest.raises(FrozenInstanceError):
        state.values = {}


def test_unknown_effect_target_rejects_entire_calculation():
    state = ScoreState({SAFETY: 10})
    with pytest.raises(DomainError):
        apply_effects(state, (AddScore(SAFETY, -1), AddScore(SERVICE, 3)))
    assert state.values[SAFETY] == 10


@pytest.mark.parametrize("bad", [True, 1.5, "1"])
def test_scores_and_deltas_require_real_integers(bad):
    with pytest.raises(DomainError):
        ScoreState({SAFETY: bad})
    with pytest.raises(DomainError):
        AddScore(SAFETY, bad)
    with pytest.raises(DomainError):
        Predicate(SAFETY, Operator.EQ, bad)


def test_declarative_vocabulary_is_closed():
    with pytest.raises(DomainError):
        MetricRef("__import__('os')")
    with pytest.raises(DomainError):
        Predicate(SAFETY, "eval", 0)
    with pytest.raises(DomainError):
        Condition("exec")
    with pytest.raises(DomainError):
        apply_effects(ScoreState({SAFETY: 0}), ("safety += 1",))
    with pytest.raises(DomainError):
        Condition(predicates=("safety > 1",))


def test_competency_reference_and_rule_size_are_validated():
    with pytest.raises(DomainError):
        MetricRef(Metric.COMPETENCY)
    with pytest.raises(DomainError):
        MetricRef(Metric.SAFETY_RATING, "service")
    with pytest.raises(DomainError):
        Condition(predicates=(Predicate(SAFETY, Operator.EQ, 1),) * 65)
