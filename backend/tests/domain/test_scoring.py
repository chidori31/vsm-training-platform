from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.common import DomainError
from app.domain.engine import advance, restore_session, start_session
from app.domain.scenario import Choice, Scenario, ScenarioNode
from app.domain.scoring import AddScore, Metric, MetricRef, ScoreState

NOW = datetime(2026, 9, 26, tzinfo=UTC)
LOYALTY = MetricRef(Metric.PASSENGER_LOYALTY)
SAFETY = MetricRef(Metric.SAFETY_RATING)
SKILL = MetricRef(Metric.COMPETENCY, "communication")


def graph_with(effects):
    return Scenario(
        "scoring",
        1,
        "Scoring",
        "start",
        (
            ScenarioNode(
                "start",
                "Choose",
                (
                    Choice(
                        "respond",
                        "Respond",
                        "done",
                        effects=effects,
                        explanation="The passenger trusts the response.",
                    ),
                ),
            ),
            ScenarioNode("done", "Finished", terminal=True),
        ),
        competency_ids=("communication",),
    )


def initial(graph, loyalty=50, safety=50, skill=0, **kwargs):
    return start_session(
        graph,
        session_id="s",
        employee_id="e",
        now=NOW,
        initial_scores=ScoreState({LOYALTY: loyalty, SAFETY: safety, SKILL: skill}),
        **kwargs,
    )


def choose(graph, session, **kwargs):
    return advance(
        graph,
        session,
        node_id="start",
        choice_id="respond",
        decision_id="d1",
        expected_sequence=0,
        now=NOW + timedelta(seconds=1),
        **kwargs,
    )


@pytest.mark.parametrize(
    "loyalty,safety,effects,expected",
    [
        (95, 5, (AddScore(LOYALTY, 20), AddScore(SAFETY, -20)), (100, 0)),
        (95, 5, (AddScore(LOYALTY, 5), AddScore(SAFETY, -5)), (100, 0)),
        (100, 0, (AddScore(LOYALTY, 1), AddScore(SAFETY, -1)), (100, 0)),
        (50, 50, (AddScore(LOYALTY, 20), AddScore(SAFETY, -20)), (70, 30)),
        (50, 50, (AddScore(LOYALTY, -20), AddScore(SAFETY, 20)), (30, 70)),
        (50, 50, (AddScore(SAFETY, 7),), (50, 57)),
        (50, 50, (), (50, 50)),
    ],
)
def test_engine_clamps_dual_scales_independently(loyalty, safety, effects, expected):
    graph = graph_with(effects)
    session = choose(graph, initial(graph, loyalty, safety))
    assert (session.scores.value(LOYALTY), session.scores.value(SAFETY)) == expected


@pytest.mark.parametrize("deltas", [(20, -20), (-20, 20), (5, 15, -20)])
def test_multiple_effects_are_summed_before_one_clamp(deltas):
    graph = graph_with(tuple(AddScore(LOYALTY, delta) for delta in deltas))
    session = choose(graph, initial(graph, loyalty=95))
    assert session.scores.value(LOYALTY) == 95
    changes = session.decisions[0].score_changes
    assert len(changes) == 1
    assert (changes[0].before, changes[0].requested_delta, changes[0].after) == (
        95,
        0,
        95,
    )
    assert changes[0].applied_delta == 0


@pytest.mark.parametrize(
    "start_value,delta,after,actual",
    [(95, 20, 100, 5), (100, 20, 100, 0), (0, -20, 0, 0)],
)
def test_journal_records_requested_and_actual_change_and_choice_explanation(
    start_value, delta, after, actual
):
    graph = graph_with((AddScore(LOYALTY, delta),))
    session = choose(graph, initial(graph, loyalty=start_value))
    (change,) = session.decisions[0].score_changes
    assert change.metric == LOYALTY
    assert (
        change.before,
        change.requested_delta,
        change.after,
        change.applied_delta,
    ) == (start_value, delta, after, actual)
    assert change.explanation == "The passenger trusts the response."
    assert session.decisions[0].explanation == change.explanation
    with pytest.raises(FrozenInstanceError):
        change.after = 42


def test_no_effects_produce_no_journal_entries():
    graph = graph_with(())
    assert choose(graph, initial(graph)).decisions[0].score_changes == ()


def test_competency_scores_remain_unbounded_and_independent():
    graph = graph_with((AddScore(SKILL, -200), AddScore(LOYALTY, 200)))
    session = choose(graph, initial(graph, skill=-50))
    assert session.scores.value(SKILL) == -250
    assert session.scores.value(LOYALTY) == 100
    assert session.scores.value(SAFETY) == 50
    assert [
        (c.metric, c.applied_delta) for c in session.decisions[0].score_changes
    ] == [(SKILL, -200), (LOYALTY, 50)]


def test_custom_bounds_are_independent_pinned_and_replayed():
    from app.domain.scoring import ScoreBounds, ScoringPolicy

    policy = ScoringPolicy(loyalty=ScoreBounds(-10, 10), safety=ScoreBounds(20, 30))
    graph = graph_with((AddScore(LOYALTY, -100), AddScore(SAFETY, 100)))
    session = choose(graph, initial(graph, loyalty=0, safety=25, scoring_policy=policy))
    assert session.scoring_policy == policy
    assert (session.scores.value(LOYALTY), session.scores.value(SAFETY)) == (-10, 30)
    assert restore_session(graph, session) == session
    assert restore_session(graph, session) == restore_session(graph, session)
    with pytest.raises(FrozenInstanceError):
        session.scoring_policy.loyalty.minimum = 0


@pytest.mark.parametrize("loyalty,safety", [(-1, 50), (101, 50), (50, -1), (50, 101)])
def test_start_rejects_dual_values_outside_pinned_bounds(loyalty, safety):
    with pytest.raises(DomainError, match="bounds"):
        initial(graph_with(()), loyalty=loyalty, safety=safety)


def test_custom_bounds_apply_to_initial_scores_and_accept_exact_edges():
    from app.domain.scoring import ScoreBounds, ScoringPolicy

    policy = ScoringPolicy(ScoreBounds(-10, 10), ScoreBounds(20, 30))
    graph = graph_with(())
    assert initial(graph, -10, 30, scoring_policy=policy).scores.value(LOYALTY) == -10
    with pytest.raises(DomainError, match="bounds"):
        initial(graph, 0, 19, scoring_policy=policy)


@pytest.mark.parametrize(
    "minimum,maximum", [(1, 0), (True, 100), (0, False), (0.0, 100), (0, "100")]
)
def test_bounds_reject_inversion_and_non_integer_values(minimum, maximum):
    from app.domain.scoring import ScoreBounds

    with pytest.raises(DomainError):
        ScoreBounds(minimum, maximum)


def test_equal_bounds_keep_touched_scale_fixed():
    from app.domain.scoring import ScoreBounds, ScoringPolicy

    graph = graph_with((AddScore(LOYALTY, 1),))
    session = choose(
        graph,
        initial(graph, loyalty=5, scoring_policy=ScoringPolicy(ScoreBounds(5, 5))),
    )
    assert session.scores.value(LOYALTY) == 5
    assert session.decisions[0].score_changes[0].applied_delta == 0


def test_retry_preserves_journal_without_duplicate_entries():
    graph = graph_with((AddScore(LOYALTY, 20),))
    session = choose(graph, initial(graph, loyalty=95))
    retried = choose(graph, session)
    assert retried is session
    assert len(retried.decisions) == len(retried.decisions[0].score_changes) == 1
    assert retried.decisions[0].score_changes[0].applied_delta == 5


def test_replay_rejects_changed_bounds_and_journal():
    from app.domain.scoring import ScoreBounds, ScoringPolicy

    graph = graph_with((AddScore(LOYALTY, 20),))
    session = choose(graph, initial(graph, loyalty=95))
    decision = session.decisions[0]
    change = decision.score_changes[0]
    forged = [
        replace(session, scoring_policy=ScoringPolicy(ScoreBounds(0, 110))),
        replace(session, decisions=(replace(decision, score_changes=()),)),
        replace(
            session,
            decisions=(replace(decision, score_changes=(replace(change, before=94),)),),
        ),
        replace(
            session,
            decisions=(
                replace(decision, score_changes=(replace(change, requested_delta=5),)),
            ),
        ),
        replace(
            session,
            decisions=(replace(decision, score_changes=(replace(change, after=99),)),),
        ),
        replace(
            session,
            decisions=(
                replace(
                    decision, score_changes=(replace(change, explanation="Invented"),)
                ),
            ),
        ),
    ]
    for snapshot in forged:
        with pytest.raises(DomainError):
            restore_session(graph, snapshot)
