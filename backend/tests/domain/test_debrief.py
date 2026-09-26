from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.common import DomainError
from app.domain.engine import advance, expire, start_session
from app.domain.rules import Condition, Operator, Predicate
from app.domain.scenario import Choice, Scenario, ScenarioNode
from app.domain.scoring import (
    AddScore,
    Metric,
    MetricRef,
    ScoreBounds,
    ScoreState,
    ScoringPolicy,
)

LOYALTY = MetricRef(Metric.PASSENGER_LOYALTY)
SAFETY = MetricRef(Metric.SAFETY_RATING)
COMMUNICATION = MetricRef(Metric.COMPETENCY, "communication")
CARE = MetricRef(Metric.COMPETENCY, "care")
NOW = datetime(2026, 9, 26, tzinfo=UTC)


def graph(choices=None, *, timed=False, version=1):
    choices = choices or (
        Choice("gain", "Помочь", "done", effects=(AddScore(COMMUNICATION, 2),)),
        Choice("loss", "Отказать", "done", effects=(AddScore(COMMUNICATION, -1),)),
        Choice("neutral", "Выслушать", "done"),
    )
    if timed:
        choices = (*choices, Choice("__timeout__", "Время истекло", "done"))
    return Scenario(
        "learning",
        version,
        "Учебная ситуация",
        "start",
        (
            ScenarioNode(
                "start",
                "Пассажиру нужна помощь",
                choices,
                time_limit_seconds=10 if timed else None,
                timeout_choice_id="__timeout__" if timed else None,
            ),
            ScenarioNode("done", "Ситуация завершена", terminal=True),
        ),
        ("communication", "care"),
    )


def play(scenario, choices=("gain",), *, sid="s", at=NOW, loyalty=50, policy=None):
    session = start_session(
        scenario,
        session_id=sid,
        employee_id="e",
        now=at,
        initial_scores=ScoreState(
            {LOYALTY: loyalty, SAFETY: 50, COMMUNICATION: 0, CARE: 0}
        ),
        scoring_policy=policy or ScoringPolicy(),
    )
    for index, choice_id in enumerate(choices):
        at += timedelta(seconds=10 if choice_id == "__timeout__" else 2)
        args = dict(
            node_id=session.current_node_id,
            decision_id=f"{sid}-{index}",
            expected_sequence=index,
            now=at,
        )
        session = (
            expire(scenario, session, **args)
            if choice_id == "__timeout__"
            else advance(scenario, session, choice_id=choice_id, **args)
        )
    return session


def test_professional_assessment_uses_actual_quality_not_speed_or_xp():
    from app.domain.debrief import debrief_for

    scenario = graph(
        (
            Choice("safe", "Помочь", "done", effects=(AddScore(SAFETY, 2),)),
            Choice("risk", "Рискнуть", "done", effects=(AddScore(SAFETY, -2),)),
            Choice("neutral", "Выслушать", "done"),
        ),
        timed=True,
    )
    strong = debrief_for(scenario, play(scenario, ("safe",))).decisions[0]
    critical = debrief_for(scenario, play(scenario, ("risk",))).decisions[0]
    neutral = debrief_for(scenario, play(scenario, ("neutral",))).decisions[0]
    assert strong.assessment.status == "strong"
    assert critical.assessment.status == "critical_error"
    assert critical.assessment.is_critical is True
    assert neutral.assessment.status == "neutral"
    assert "безопасност" in critical.assessment.explanation.lower()


def test_unavoidable_timeout_is_not_classified_as_a_learner_critical_error():
    from app.domain.debrief import debrief_for

    scenario = graph(
        (
            Choice(
                "closed",
                "Помочь",
                "done",
                condition=Condition(predicates=(Predicate(SAFETY, Operator.GT, 100),)),
            ),
        ),
        timed=True,
    )
    result = debrief_for(scenario, play(scenario, ("__timeout__",)))
    assert result.decisions[0].assessment.status == "neutral"


def test_performance_weeks_exclude_timeouts_from_measured_reaction_and_deduplicate():
    from app.domain.learning_analytics import competency_analytics

    scenario = graph(timed=True)
    sunday = play(
        scenario, sid="sunday", at=datetime(2026, 9, 27, 12, tzinfo=UTC), loyalty=60
    )
    monday = play(
        scenario, sid="monday", at=datetime(2026, 9, 28, 12, tzinfo=UTC), loyalty=70
    )
    timed_out = play(
        scenario,
        ("__timeout__",),
        sid="timeout",
        at=datetime(2026, 9, 28, 13, tzinfo=UTC),
    )
    active = play(scenario, (), sid="active")
    result = competency_analytics(
        (
            (scenario, timed_out),
            (scenario, monday),
            (scenario, sunday),
            (scenario, sunday),
            (scenario, active),
        )
    )
    performance = result.performance
    assert performance.measured_decision_count == 2
    assert performance.average_decision_seconds == 2
    assert (performance.best_loyalty, performance.best_safety) == (70, 50)
    assert [week.week_start for week in performance.weeks] == [
        datetime(2026, 9, 21, tzinfo=UTC),
        datetime(2026, 9, 28, tzinfo=UTC),
    ]
    assert [week.completed_sessions for week in performance.weeks] == [1, 2]
    assert performance.weeks[1].decision_count == 2
    assert performance.weeks[1].timeout_count == 1
    assert performance.weeks[1].average_decision_seconds == 2
    assert performance.weeks[1].average_loyalty == 60


def test_empty_and_timeout_only_performance_has_no_invented_reaction():
    from app.domain.learning_analytics import competency_analytics

    empty = competency_analytics(()).performance
    assert empty.weeks == ()
    assert empty.best_safety is None and empty.average_decision_seconds is None
    scenario = graph(timed=True)
    timed_out = play(scenario, ("__timeout__",))
    performance = competency_analytics(((scenario, timed_out),)).performance
    assert performance.measured_decision_count == 0
    assert performance.average_decision_seconds is None
    assert performance.weeks[0].average_decision_seconds is None


def test_week_uses_utc_completion_even_when_entry_and_local_date_are_different():
    from datetime import timezone

    from app.domain.learning_analytics import competency_analytics

    scenario = graph()
    crossed_midnight = play(
        scenario, sid="midnight", at=datetime(2026, 9, 27, 23, 59, 59, tzinfo=UTC)
    )
    local_monday = play(
        scenario,
        sid="local-monday",
        at=datetime(2026, 9, 28, 0, 30, tzinfo=timezone(timedelta(hours=3))),
    )
    weeks = competency_analytics(
        ((scenario, crossed_midnight), (scenario, local_monday))
    ).performance.weeks
    assert [week.week_start for week in weeks] == [
        datetime(2026, 9, 21, tzinfo=UTC),
        datetime(2026, 9, 28, tzinfo=UTC),
    ]
    assert [week.completed_sessions for week in weeks] == [1, 1]


def test_debrief_explains_actual_clamped_scores_and_zero_effects():
    from app.domain.debrief import debrief_for

    scenario = graph(
        (
            Choice(
                "gain",
                "Помочь",
                "done",
                effects=(AddScore(LOYALTY, 10), AddScore(COMMUNICATION, -2)),
                explanation="Разбор последствия.",
            ),
        )
    )
    session = play(
        scenario, loyalty=78, policy=ScoringPolicy(loyalty=ScoreBounds(0, 80))
    )
    result = debrief_for(scenario, session)
    decision = result.decisions[0]
    assert (result.rule_version, result.session_id, result.completed_at) == (
        1,
        "s",
        NOW + timedelta(seconds=2),
    )
    assert (decision.node_text, decision.choice_text, decision.destination_text) == (
        "Пассажиру нужна помощь",
        "Помочь",
        "Ситуация завершена",
    )
    assert decision.elapsed_seconds == 2
    assert (
        decision.loyalty.before,
        decision.loyalty.after,
        decision.loyalty.delta,
        decision.loyalty.requested_delta,
    ) == (78, 80, 2, 10)
    assert "границ" in decision.loyalty.explanation
    assert (decision.safety.before, decision.safety.after, decision.safety.delta) == (
        50,
        50,
        0,
    )
    assert "не измен" in decision.safety.explanation
    assert (
        next(
            c for c in decision.competencies if c.competency_id == "communication"
        ).delta
        == -2
    )
    assert decision.pattern_codes == ("competency_regression",)
    assert (result.summary.loyalty_delta, result.summary.safety_delta) == (2, 0)


def test_predecision_alternatives_exclude_unavailable_and_equal_advice():
    from app.domain.debrief import debrief_for

    effects = (AddScore(LOYALTY, 2),)
    scenario = graph(
        (
            Choice("gain", "Выбрано", "done", effects=effects),
            Choice("equal", "Равный вариант", "done", effects=effects),
            Choice(
                "better",
                "Доступно до решения",
                "done",
                Condition(predicates=(Predicate(LOYALTY, Operator.LT, 51),)),
                (AddScore(LOYALTY, 3),),
            ),
            Choice(
                "locked",
                "Недоступно до решения",
                "done",
                Condition(predicates=(Predicate(LOYALTY, Operator.GTE, 51),)),
                (AddScore(LOYALTY, 10),),
            ),
        )
    )
    decision = debrief_for(scenario, play(scenario)).decisions[0]
    assert [(a.choice_id, a.available) for a in decision.alternatives] == [
        ("equal", True),
        ("better", True),
        ("locked", False),
    ]
    assert decision.suggestion.choice_ids == ("better",)
    assert "непосредствен" in decision.suggestion.text


def test_tradeoffs_include_all_competencies_and_do_not_invent_best_choice():
    from app.domain.debrief import debrief_for

    scenario = graph(
        (
            Choice("gain", "Выбрано", "done", effects=(AddScore(COMMUNICATION, 2),)),
            Choice(
                "tradeoff",
                "Компромисс",
                "done",
                effects=(AddScore(COMMUNICATION, 3), AddScore(CARE, -1)),
            ),
            Choice(
                "equal", "Равный вариант", "done", effects=(AddScore(COMMUNICATION, 2),)
            ),
        )
    )
    decision = debrief_for(scenario, play(scenario)).decisions[0]
    assert decision.suggestion.choice_ids == ()
    assert "компромисс" in decision.suggestion.text.lower()


def test_timeout_uses_choices_available_before_deadline_and_counts_patterns_once():
    from app.domain.debrief import debrief_for

    scenario = graph(timed=True)
    decision = debrief_for(scenario, play(scenario, ("__timeout__",))).decisions[0]
    assert decision.was_timeout and decision.elapsed_seconds == 10
    assert {a.choice_id for a in decision.alternatives} == {"gain", "loss", "neutral"}
    assert all(a.available for a in decision.alternatives)
    assert decision.pattern_codes == ("timeout",)
    assert "срок" in decision.suggestion.text
    assert "__timeout__" not in decision.suggestion.choice_ids


def test_repeated_node_visits_reconstruct_each_predecision_state():
    from app.domain.debrief import debrief_for

    scenario = graph(
        (
            Choice("again", "Повторить", "start", effects=(AddScore(LOYALTY, 2),)),
            Choice(
                "gain",
                "Завершить",
                "done",
                Condition(predicates=(Predicate(LOYALTY, Operator.GTE, 52),)),
            ),
        )
    )
    result = debrief_for(scenario, play(scenario, ("again", "again", "gain")))
    assert [d.loyalty.before for d in result.decisions] == [50, 52, 54]
    assert [d.elapsed_seconds for d in result.decisions] == [2, 2, 2]
    assert result.decisions[0].alternatives[0].available is False
    assert result.decisions[1].alternatives[0].available is True


def test_debrief_requires_completed_verified_history():
    from app.domain.debrief import debrief_for

    scenario = graph()
    with pytest.raises(DomainError):
        debrief_for(scenario, play(scenario, ()))
    session = play(scenario)
    with pytest.raises(DomainError):
        debrief_for(scenario, replace(session, scores=session.initial_scores))
    with pytest.raises(DomainError):
        debrief_for(replace(scenario, version=2), session)


@pytest.mark.parametrize(
    "choices,status,positive,negative",
    [
        (("gain", "gain"), "insufficient_data", 2, 0),
        (("gain", "gain", "neutral"), "developing", 2, 0),
        (("gain", "gain", "gain"), "strength", 3, 0),
        (("gain", "neutral", "loss"), "growth_area", 1, 1),
        (("gain",) * 7 + ("loss",) * 2 + ("neutral",), "strength", 7, 2),
        (("gain",) * 7 + ("loss",) * 3, "growth_area", 7, 3),
    ],
)
def test_analytics_evidence_thresholds_and_neutral_observations(
    choices, status, positive, negative
):
    from app.domain.learning_analytics import competency_analytics

    scenario = graph()
    result = competency_analytics(
        (scenario, play(scenario, (c,), sid=str(i))) for i, c in enumerate(choices)
    )
    row = next(c for c in result.competencies if c.competency_id == "communication")
    assert row.status == status
    assert (
        row.positive_decisions,
        row.negative_decisions,
        row.opportunities,
        row.practiced_sessions,
    ) == (positive, negative, len(choices), len(choices))
    assert row.earned_points == positive * 2
    assert row.net_delta == positive * 2 - negative
    assert result.strengths == (("communication",) if status == "strength" else ())
    assert result.weaknesses == (("communication",) if status == "growth_area" else ())
    care = next(c for c in result.competencies if c.competency_id == "care")
    assert care.status == "insufficient_data" and care.opportunities == 0


def test_analytics_needs_two_sessions_and_uses_net_reward_not_positive_decision_sum():
    from app.domain.learning_analytics import competency_analytics

    scenario = graph(
        (
            Choice("gain", "Практика", "start", effects=(AddScore(COMMUNICATION, 2),)),
            Choice("loss", "Завершить", "done", effects=(AddScore(COMMUNICATION, -5),)),
        )
    )
    session = play(scenario, ("gain", "gain", "loss"))
    result = competency_analytics(((scenario, session), (scenario, session)))
    row = next(c for c in result.competencies if c.competency_id == "communication")
    assert (row.earned_points, row.net_delta, row.opportunities) == (0, -1, 3)
    assert row.status == "insufficient_data"
    assert row.practiced_sessions == 1 and result.completed_sessions == 1


def test_analytics_history_order_versions_active_exclusion_and_patterns():
    from app.domain.learning_analytics import competency_analytics

    choices = (
        Choice(
            "bad",
            "Ошибка",
            "done",
            effects=(
                AddScore(LOYALTY, -1),
                AddScore(SAFETY, -2),
                AddScore(COMMUNICATION, -1),
                AddScore(CARE, -2),
            ),
        ),
    )
    scenario = graph(choices, timed=True)
    later = play(scenario, ("bad",), sid="z", at=NOW + timedelta(days=1))
    earlier = play(scenario, ("bad",), sid="b")
    same_time = play(scenario, ("bad",), sid="a")
    v2 = graph(timed=True, version=2)
    active = play(v2, (), sid="active")
    timeout = play(scenario, ("__timeout__",), sid="timeout")
    values = [
        (scenario, later),
        (v2, active),
        (scenario, earlier),
        (scenario, same_time),
        (scenario, timeout),
    ]
    result = competency_analytics(values)
    assert result == competency_analytics(reversed(values))
    assert (
        result.total_sessions,
        result.completed_sessions,
        result.active_sessions,
    ) == (5, 4, 1)
    assert (result.decision_count, result.timeout_count) == (4, 1)
    row = next(c for c in result.competencies if c.competency_id == "communication")
    assert [t.session_id for t in row.trend] == ["a", "b", "timeout", "z"]
    assert row.negative_decisions == 3 and row.opportunities == 4
    patterns = {p.code: p for p in result.patterns}
    assert patterns["competency_regression"].count == 3
    assert patterns["competency_regression"].session_count == 3
    assert patterns["competency_regression"].recurring
    assert not patterns["timeout"].recurring
    assert [
        (s.scenario_version, s.attempts, s.completed, s.active)
        for s in result.scenarios
    ] == [(1, 4, 4, 0), (2, 1, 0, 1)]
    assert result.scenarios[0].average_duration_seconds == 4
    assert result.scenarios[1].average_loyalty is None


@pytest.mark.parametrize("timeout_delta", [-5, 5])
def test_timeout_only_never_earns_progress_and_unavailable_skill_is_not_observed(
    timeout_delta,
):
    from app.domain.learning_analytics import competency_analytics

    scenario = graph(
        (
            Choice(
                "locked",
                "Недоступно",
                "done",
                Condition(predicates=(Predicate(LOYALTY, Operator.GT, 90),)),
                (AddScore(COMMUNICATION, 2),),
            ),
            Choice("neutral", "Доступно", "done"),
            Choice(
                "__timeout__",
                "Истекло",
                "done",
                effects=(AddScore(COMMUNICATION, timeout_delta),),
            ),
        )
    )
    scenario = replace(
        scenario,
        nodes=(
            replace(
                scenario.nodes[0],
                time_limit_seconds=10,
                timeout_choice_id="__timeout__",
            ),
            scenario.nodes[1],
        ),
    )
    result = competency_analytics(((scenario, play(scenario, ("__timeout__",))),))
    row = next(c for c in result.competencies if c.competency_id == "communication")
    assert (
        row.earned_points,
        row.net_delta,
        row.opportunities,
        row.positive_decisions,
        row.negative_decisions,
    ) == (0, timeout_delta, 0, 0, 0)
    assert row.trend[0].earned_points == 0


def test_cancelled_effects_do_not_create_exposure_or_sufficient_practice():
    from app.domain.learning_analytics import competency_analytics

    repeated = graph(
        (
            Choice(
                "again", "Повторить", "start", effects=(AddScore(COMMUNICATION, 2),)
            ),
            Choice(
                "finish", "Закончить", "done", effects=(AddScore(COMMUNICATION, 2),)
            ),
        )
    )
    cancelled = graph(
        (
            Choice(
                "gain",
                "Отмена эффектов",
                "done",
                effects=(
                    AddScore(COMMUNICATION, 2),
                    AddScore(COMMUNICATION, -2),
                ),
            ),
        ),
        version=2,
    )
    result = competency_analytics(
        (
            (repeated, play(repeated, ("again", "again", "finish"), sid="practice")),
            (cancelled, play(cancelled, sid="cancelled")),
        )
    )
    row = next(c for c in result.competencies if c.competency_id == "communication")
    assert (row.positive_decisions, row.opportunities, row.practiced_sessions) == (
        3,
        3,
        1,
    )
    assert row.status == "insufficient_data" and len(row.trend) == 2


def test_repeated_pattern_within_one_session_is_not_recurring():
    from app.domain.learning_analytics import competency_analytics

    scenario = graph(
        (
            Choice(
                "again", "Повторить", "start", effects=(AddScore(COMMUNICATION, -1),)
            ),
            Choice(
                "finish", "Закончить", "done", effects=(AddScore(COMMUNICATION, -1),)
            ),
        )
    )
    session = play(scenario, ("again", "again", "finish"))
    pattern = competency_analytics(((scenario, session),)).patterns[0]
    assert (pattern.count, pattern.session_count, pattern.recurring) == (3, 1, False)


def test_terminal_start_and_empty_history_have_no_false_evidence():
    from app.domain.debrief import debrief_for
    from app.domain.learning_analytics import competency_analytics

    scenario = Scenario(
        "empty",
        1,
        "Пустой",
        "done",
        (ScenarioNode("done", "Готово", terminal=True),),
        ("communication", "care"),
    )
    session = play(scenario, ())
    result = debrief_for(scenario, session)
    assert result.decisions == () and result.summary.decision_count == 0
    analytics = competency_analytics(((scenario, session),))
    assert all(
        c.opportunities == 0 and c.status == "insufficient_data"
        for c in analytics.competencies
    )
    assert not analytics.patterns
    empty = competency_analytics(())
    assert empty.total_sessions == 0 and empty.competencies == ()


def test_fractional_average_duration_is_independent_of_input_order():
    from app.domain.learning_analytics import competency_analytics

    scenario = graph()
    attempts = []
    for index, microseconds in enumerate((100_000, 200_000, 300_000)):
        initial = play(scenario, (), sid=str(index))
        completed = advance(
            scenario,
            initial,
            node_id="start",
            choice_id="gain",
            decision_id=f"fraction-{index}",
            expected_sequence=0,
            now=NOW + timedelta(microseconds=microseconds),
        )
        attempts.append((scenario, completed))
    assert competency_analytics(attempts) == competency_analytics(reversed(attempts))


def test_unavoidable_timeout_has_no_blame_or_behavior_pattern():
    from app.domain.debrief import debrief_for
    from app.domain.learning_analytics import competency_analytics

    scenario = graph(
        (
            Choice(
                "locked",
                "Недоступно",
                "done",
                Condition(predicates=(Predicate(LOYALTY, Operator.GT, 99),)),
                (AddScore(COMMUNICATION, 2),),
            ),
        ),
        timed=True,
    )
    attempts = [
        (scenario, play(scenario, ("__timeout__",), sid=str(i))) for i in range(2)
    ]
    debrief = debrief_for(*attempts[0])
    decision = debrief.decisions[0]
    assert decision.was_timeout and debrief.summary.timeout_count == 1
    assert not decision.alternatives[0].available
    assert decision.suggestion.choice_ids == ()
    assert "доступных действий не было" in decision.suggestion.text
    assert "timeout" not in decision.pattern_codes
    analytics = competency_analytics(attempts)
    assert analytics.timeout_count == 2 and analytics.scenarios[0].timeout_count == 2
    assert not analytics.patterns
