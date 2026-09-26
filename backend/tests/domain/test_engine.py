from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.common import DomainError
from app.domain.gameplay import SessionStatus
from app.domain.rules import Condition, Operator, Predicate
from app.domain.scenario import Choice, Scenario, ScenarioNode
from app.domain.scoring import AddScore, Metric, MetricRef, ScoreState

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)
LOYALTY = MetricRef(Metric.PASSENGER_LOYALTY)
SAFETY = MetricRef(Metric.SAFETY_RATING)
COMMUNICATION = MetricRef(Metric.COMPETENCY, "communication")


@pytest.fixture
def scenario():
    return Scenario(
        "branching",
        1,
        "Synthetic branching test",
        "start",
        (
            ScenarioNode(
                "start",
                "A disagreement",
                (
                    Choice(
                        "listen",
                        "Listen",
                        "talk",
                        effects=(AddScore(LOYALTY, 2), AddScore(COMMUNICATION, 1)),
                        explanation="Listening builds trust.",
                    ),
                    Choice(
                        "blame",
                        "Blame",
                        "tension",
                        effects=(AddScore(LOYALTY, -2), AddScore(SAFETY, -1)),
                        explanation="Blame increases tension.",
                    ),
                    Choice(
                        "trained",
                        "Use trained skill",
                        "success",
                        condition=Condition(
                            predicates=(Predicate(COMMUNICATION, Operator.GTE, 2),)
                        ),
                        explanation="Training unlocks this option.",
                    ),
                    Choice(
                        "deadline-event",
                        "Timeout",
                        "expired",
                        effects=(AddScore(SAFETY, -3),),
                        explanation="No decision in time.",
                    ),
                ),
                time_limit_seconds=10,
                timeout_choice_id="deadline-event",
            ),
            ScenarioNode(
                "talk",
                "Find an agreement",
                (
                    Choice(
                        "agree",
                        "Agree",
                        "success",
                        condition=Condition(
                            predicates=(Predicate(COMMUNICATION, Operator.GTE, 1),)
                        ),
                        effects=(AddScore(SAFETY, 2),),
                        explanation="Agreement improves safety.",
                    ),
                    Choice(
                        "fallback",
                        "Ask colleague",
                        "neutral",
                        effects=(AddScore(LOYALTY, -1),),
                        explanation="A colleague takes over.",
                    ),
                ),
            ),
            ScenarioNode("success", "Resolved", terminal=True),
            ScenarioNode("neutral", "Handed over", terminal=True),
            ScenarioNode("tension", "Unresolved", terminal=True),
            ScenarioNode("expired", "Time expired", terminal=True),
        ),
        competency_ids=("communication",),
    )


def start(scenario, **kwargs):
    from app.domain.engine import start_session

    return start_session(
        scenario,
        session_id="session-1",
        employee_id="employee-1",
        initial_scores=ScoreState({LOYALTY: 0, SAFETY: 0, COMMUNICATION: 0}),
        now=NOW,
        **kwargs,
    )


def test_hostile_commands_cannot_change_state_or_prevent_later_valid_completion(
    scenario,
):
    from app.domain.engine import advance, restore_session

    session = start(scenario)
    original = session
    for overrides in (
        {"node_id": "success"},
        {"choice_id": "agree"},
        {"choice_id": "deadline-event"},
        {"choice_id": "__import__('os').system('noop')"},
        {"choice_id": "trained"},
        {"expected_sequence": 1},
        {"expected_sequence": True},
        {"expected_sequence": -1},
    ):
        command = (
            dict(
                node_id="start",
                choice_id="listen",
                decision_id="hostile",
                expected_sequence=0,
                now=NOW + timedelta(seconds=1),
            )
            | overrides
        )
        with pytest.raises(DomainError):
            advance(scenario, session, **command)
        assert session == original
        assert restore_session(scenario, session) == original
    session = choose(scenario, session, "listen")
    session = choose(scenario, session, "agree", seconds=2)
    assert session.status is SessionStatus.COMPLETED
    assert len(session.decisions) == 2
    tampered = replace(
        session, scores=ScoreState({LOYALTY: 999, SAFETY: 999, COMMUNICATION: 999})
    )
    with pytest.raises(DomainError, match="replayed"):
        restore_session(scenario, tampered)


def choose(scenario, session, choice_id, *, seconds=1, **kwargs):
    from app.domain.engine import advance

    command = {
        "node_id": session.current_node_id,
        "choice_id": choice_id,
        "decision_id": f"decision-{len(session.decisions) + 1}",
        "expected_sequence": len(session.decisions),
        "now": NOW + timedelta(seconds=seconds),
    }
    return advance(scenario, session, **(command | kwargs))


@pytest.mark.parametrize(
    "path,final,scores",
    [
        (("listen", "agree"), "success", (2, 2, 1)),
        (("listen", "fallback"), "neutral", (1, 0, 1)),
        (("blame",), "tension", (0, 0, 0)),
    ],
)
def test_three_distinct_branches_have_real_effects_and_history(
    scenario, path, final, scores
):
    from app.domain.engine import available_choices, current_node

    original = session = start(scenario)
    for seconds, choice_id in enumerate(path, 1):
        session = choose(scenario, session, choice_id, seconds=seconds)
    assert current_node(scenario, session).id == final
    assert session.status is SessionStatus.COMPLETED
    assert session.completed_at == NOW + timedelta(seconds=len(path))
    assert (
        tuple(session.scores.value(m) for m in (LOYALTY, SAFETY, COMMUNICATION))
        == scores
    )
    assert [d.choice_id for d in session.decisions] == list(path)
    assert [d.sequence for d in session.decisions] == list(range(1, len(path) + 1))
    assert all(d.explanation for d in session.decisions)
    assert available_choices(scenario, session, now=session.completed_at) == ()
    assert original.current_node_id == "start" and not original.decisions
    assert original.scores.value(LOYALTY) == 0


def test_conditions_use_pre_effect_scores_and_unlock_after_transition(scenario):
    from app.domain.engine import available_choices, start_session

    initial = start(scenario)
    assert [c.id for c in available_choices(scenario, initial, now=NOW)] == [
        "listen",
        "blame",
    ]
    with pytest.raises(DomainError, match="condition"):
        choose(scenario, initial, "trained")
    changed = choose(scenario, initial, "listen")
    assert [
        c.id
        for c in available_choices(scenario, changed, now=NOW + timedelta(seconds=1))
    ] == ["agree", "fallback"]
    skilled = start_session(
        scenario,
        session_id="s",
        employee_id="e",
        now=NOW,
        initial_scores=ScoreState({LOYALTY: 0, SAFETY: 0, COMMUNICATION: 2}),
    )
    assert choose(scenario, skilled, "trained").current_node_id == "success"


def test_invalid_choice_source_stale_revision_and_terminal_rejected(scenario):
    initial = start(scenario)
    for command in (
        {"choice_id": "unknown"},
        {"node_id": "talk"},
        {"expected_sequence": 3},
        {"expected_sequence": True},
        {"expected_sequence": -1},
        {"decision_id": " "},
    ):
        with pytest.raises(DomainError):
            choose(scenario, initial, **({"choice_id": "listen"} | command))
    completed = choose(scenario, initial, "blame")
    with pytest.raises(DomainError, match="completed"):
        choose(scenario, completed, "listen", seconds=2)


def test_retries_return_latest_state_without_reapplying_effects(scenario):
    initial = start(scenario)
    first = choose(scenario, initial, "listen")
    completed = choose(scenario, first, "agree", seconds=2)
    for snapshot in (first, completed):
        retry = choose(
            scenario,
            snapshot,
            "listen",
            node_id="start",
            expected_sequence=0,
            decision_id="decision-1",
            seconds=500,
        )
        assert retry == snapshot
        assert retry.scores.value(LOYALTY) == 2
    for changes in (
        {"choice_id": "blame"},
        {"node_id": "talk"},
        {"expected_sequence": 1},
    ):
        command = {
            "choice_id": "listen",
            "node_id": "start",
            "expected_sequence": 0,
            "decision_id": "decision-1",
            "seconds": 500,
        } | changes
        with pytest.raises(DomainError, match="different command"):
            choose(scenario, completed, **command)


def test_timeout_boundary_and_manual_timeout_choice_rejected(scenario):
    from app.domain.engine import available_choices, expire, node_deadline

    initial = start(scenario)
    assert node_deadline(scenario, initial) == NOW + timedelta(seconds=10)
    assert available_choices(scenario, initial, now=NOW + timedelta(seconds=10)) == ()
    with pytest.raises(DomainError, match="timeout"):
        choose(scenario, initial, "deadline-event")
    with pytest.raises(DomainError, match="deadline"):
        choose(scenario, initial, "listen", seconds=10)
    with pytest.raises(DomainError, match="deadline"):
        expire(
            scenario,
            initial,
            node_id="start",
            expected_sequence=0,
            decision_id="t1",
            now=NOW + timedelta(seconds=9),
        )
    expired = expire(
        scenario,
        initial,
        node_id="start",
        expected_sequence=0,
        decision_id="t1",
        now=NOW + timedelta(seconds=10),
    )
    assert expired.current_node_id == "expired" and expired.scores.value(SAFETY) == 0
    assert expired.decisions[0].score_changes[0].requested_delta == -3
    assert expired.decisions[0].score_changes[0].applied_delta == 0
    assert expired.decisions[0].choice_id == "deadline-event"
    assert expired.completed_at == NOW + timedelta(seconds=10)
    assert (
        expire(
            scenario,
            expired,
            node_id="start",
            expected_sequence=0,
            decision_id="t1",
            now=NOW + timedelta(seconds=20),
        )
        == expired
    )
    with pytest.raises(DomainError):
        choose(
            scenario,
            expired,
            "deadline-event",
            node_id="start",
            expected_sequence=0,
            decision_id="t1",
            seconds=20,
        )


def test_node_entry_time_resets_deadline_and_late_expiry_uses_processing_time(scenario):
    from app.domain.engine import expire, node_deadline

    timed_talk = replace(
        scenario.node("talk"), time_limit_seconds=5, timeout_choice_id="fallback"
    )
    graph = replace(
        scenario,
        nodes=tuple(timed_talk if n.id == "talk" else n for n in scenario.nodes),
    )
    first = choose(graph, start(graph), "listen", seconds=8)
    assert node_deadline(graph, first) == NOW + timedelta(seconds=13)
    final = expire(
        graph,
        first,
        node_id="talk",
        expected_sequence=1,
        decision_id="t2",
        now=NOW + timedelta(seconds=15),
    )
    assert final.current_node_id == "neutral"
    assert final.decisions[-1].decided_at == NOW + timedelta(seconds=15)


def test_cycles_reject_stale_visit_even_when_node_matches(scenario):
    from app.domain.engine import restore_session

    talk = replace(
        scenario.node("talk"),
        choices=scenario.node("talk").choices
        + (Choice("retry", "Retry", "start", effects=(AddScore(SAFETY, 1),)),),
    )
    graph = replace(
        scenario, nodes=tuple(talk if n.id == "talk" else n for n in scenario.nodes)
    )
    first = choose(graph, start(graph), "listen")
    back = choose(graph, first, "retry", seconds=2)
    assert back.current_node_id == "start"
    with pytest.raises(DomainError, match="sequence"):
        choose(
            graph, back, "listen", decision_id="new-id", expected_sequence=0, seconds=3
        )
    assert (
        choose(
            graph,
            back,
            "listen",
            decision_id="decision-1",
            expected_sequence=0,
            seconds=30,
        )
        == back
    )
    assert restore_session(graph, back) == back
    assert choose(graph, back, "listen", seconds=3).scores.value(LOYALTY) == 4


def test_restore_replays_history_and_detects_tampering(scenario):
    from app.domain.engine import restore_session

    accepted = choose(scenario, start(scenario), "listen")
    assert restore_session(scenario, accepted) == accepted
    changes = [
        {"current_node_id": "success"},
        {"scores": ScoreState({LOYALTY: 999, SAFETY: 0, COMMUNICATION: 1})},
        {"scenario_version": 2},
        {"initial_scores": None},
        {"status": SessionStatus.COMPLETED, "completed_at": NOW + timedelta(seconds=1)},
        {"decisions": (replace(accepted.decisions[0], effects=()),)},
        {"decisions": (replace(accepted.decisions[0], explanation="Forged"),)},
        {"decisions": (replace(accepted.decisions[0], node_id="talk"),)},
        {
            "decisions": (
                replace(accepted.decisions[0], decided_at=NOW + timedelta(seconds=11)),
            )
        },
    ]
    for alteration in changes:
        with pytest.raises(DomainError):
            restore_session(scenario, replace(accepted, **alteration))


def test_queries_reject_wrong_scenario_and_forged_state(scenario):
    from app.domain.engine import available_choices, current_node, node_deadline

    initial = start(scenario)
    for graph, snapshot in (
        (replace(scenario, version=2), initial),
        (scenario, replace(initial, current_node_id="tension")),
    ):
        with pytest.raises(DomainError):
            current_node(graph, snapshot)
        with pytest.raises(DomainError):
            available_choices(graph, snapshot, now=NOW)
        with pytest.raises(DomainError):
            node_deadline(graph, snapshot)


def test_start_requires_explicit_metrics_and_terminal_start_completes(scenario):
    from app.domain.engine import start_session

    for scores in (
        ScoreState({}),
        ScoreState({LOYALTY: 0, SAFETY: 0}),
        ScoreState(
            {
                LOYALTY: 0,
                SAFETY: 0,
                COMMUNICATION: 0,
                MetricRef(Metric.COMPETENCY, "extra"): 1,
            }
        ),
    ):
        with pytest.raises(DomainError, match="metrics"):
            start_session(
                scenario,
                session_id="s",
                employee_id="e",
                initial_scores=scores,
                now=NOW,
            )
    final = Scenario(
        "final", 1, "Final", "end", (ScenarioNode("end", "Done", terminal=True),)
    )
    session = start_session(
        final,
        session_id="s",
        employee_id="e",
        initial_scores=ScoreState({LOYALTY: 0, SAFETY: 0}),
        now=NOW,
    )
    assert session.status is SessionStatus.COMPLETED
    assert session.completed_at == NOW and session.decisions == ()


def test_time_is_explicit_aware_monotonic_and_results_deterministic(scenario):
    from app.domain.engine import available_choices

    initial = start(scenario)
    assert choose(scenario, initial, "listen") == choose(scenario, initial, "listen")
    for now in (NOW - timedelta(seconds=1), NOW.replace(tzinfo=None)):
        with pytest.raises(DomainError):
            choose(scenario, initial, "listen", now=now)
        with pytest.raises(DomainError):
            available_choices(scenario, initial, now=now)
    first = choose(scenario, initial, "listen")
    assert choose(scenario, first, "agree", seconds=1).status is SessionStatus.COMPLETED


def test_no_available_choices_does_not_complete_session(scenario):
    from app.domain.engine import available_choices, expire

    locked = Condition(predicates=(Predicate(SAFETY, Operator.GT, 100),))
    talk = replace(
        scenario.node("talk"),
        choices=tuple(
            replace(c, condition=locked) for c in scenario.node("talk").choices
        ),
    )
    graph = replace(
        scenario, nodes=tuple(talk if n.id == "talk" else n for n in scenario.nodes)
    )
    blocked = choose(graph, start(graph), "listen")
    assert available_choices(graph, blocked, now=NOW + timedelta(seconds=1)) == ()
    assert blocked.status is SessionStatus.ACTIVE
    with pytest.raises(DomainError, match="timeout"):
        expire(
            graph,
            blocked,
            node_id="talk",
            decision_id="t",
            expected_sequence=1,
            now=NOW + timedelta(seconds=2),
        )


def test_timeout_retry_after_later_progress_uses_original_node_identity():
    from app.domain.engine import advance, expire, start_session

    graph = Scenario(
        "timeout-recovery",
        1,
        "Recovery",
        "start",
        (
            ScenarioNode(
                "start",
                "Decide",
                (
                    Choice("finish", "Finish", "end"),
                    Choice(
                        "elapsed",
                        "Elapsed",
                        "recovery",
                        effects=(AddScore(SAFETY, -1),),
                    ),
                ),
                time_limit_seconds=5,
                timeout_choice_id="elapsed",
            ),
            ScenarioNode("recovery", "Recover", (Choice("finish", "Finish", "end"),)),
            ScenarioNode("end", "End", terminal=True),
        ),
    )
    session = start_session(
        graph,
        session_id="s",
        employee_id="e",
        now=NOW,
        initial_scores=ScoreState({LOYALTY: 0, SAFETY: 0}),
    )
    expired = expire(
        graph,
        session,
        node_id="start",
        expected_sequence=0,
        decision_id="timeout",
        now=NOW + timedelta(seconds=20),
    )
    completed = advance(
        graph,
        expired,
        node_id="recovery",
        choice_id="finish",
        expected_sequence=1,
        decision_id="finish",
        now=NOW + timedelta(seconds=21),
    )
    retry = expire(
        graph,
        completed,
        node_id="start",
        expected_sequence=0,
        decision_id="timeout",
        now=NOW + timedelta(seconds=100),
    )
    assert retry == completed and retry.scores.value(SAFETY) == 0
    assert retry.decisions[0].score_changes[0].requested_delta == -1
    assert retry.decisions[0].score_changes[0].applied_delta == 0
    assert len(retry.decisions) == 2
