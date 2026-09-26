from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.domain.common import DomainError
from app.domain.engine import advance, expire, start_session
from app.domain.scoring import Metric, MetricRef, ScoreState
from app.scenarios.schema import ScenarioDocument


def play(name="passenger-conflict", choices=("listen", "agree"), sid="s"):
    graph = ScenarioDocument.model_validate_json(
        (Path(__file__).parents[3] / "scenarios" / "demo" / f"{name}.json").read_text(
            encoding="utf-8"
        )
    ).to_domain()
    now = datetime(2026, 9, 26, tzinfo=UTC)
    scores = {
        MetricRef(Metric.PASSENGER_LOYALTY): 50,
        MetricRef(Metric.SAFETY_RATING): 50,
    }
    scores.update(
        {MetricRef(Metric.COMPETENCY, key): 0 for key in graph.competency_ids}
    )
    session = start_session(
        graph,
        session_id=sid,
        employee_id="e",
        initial_scores=ScoreState(scores),
        now=now,
    )
    for index, choice in enumerate(choices):
        now += timedelta(seconds=60 if choice == "__timeout__" else 1)
        args = dict(
            node_id=session.current_node_id,
            decision_id=f"{sid}-{index}",
            expected_sequence=index,
            now=now,
        )
        session = (
            expire(graph, session, **args)
            if choice == "__timeout__"
            else advance(graph, session, choice_id=choice, **args)
        )
    return graph, session


def test_reward_uses_final_net_competency_and_independent_score_gains():
    from app.domain.gamification import reward_for

    fact = reward_for(*play())
    assert fact.xp == 55  # 20 completion + 15 scale gain + 10 competency + 10 critical
    assert dict(fact.competencies) == {"communication": 1}
    assert fact.safe_completion and fact.conflict_resolved
    assert fact.critical == (True,)


def test_timeout_and_bad_choice_do_not_unlock_behavior_rewards():
    from app.domain.gamification import reward_for

    timeout = reward_for(*play(choices=("__timeout__",)))
    assert timeout.xp == 0 and not timeout.competencies
    assert timeout.critical == (False,)
    bad = reward_for(*play(choices=("blame",)))
    assert bad.xp == 20
    assert not bad.safe_completion and not bad.conflict_resolved
    assert bad.critical == (False,)


def test_unfinished_sessions_never_earn_xp():
    from app.domain.gamification import reward_for

    with pytest.raises(DomainError):
        reward_for(*play(choices=("listen",)))


def test_progress_accumulates_unlocks_at_threshold_and_deduplicates_sessions():
    from app.domain.gamification import progress_for, reward_for

    facts = [reward_for(*play(sid=f"s{i}")) for i in range(3)]
    progress = progress_for([*facts, facts[0]])
    assert progress.xp == 165 and progress.level == 2
    assert progress.level_start == 100 and progress.next_level == 300
    assert dict(progress.competencies) == {"communication": 3}
    assert progress.values == {
        "safe-shift": 3,
        "conflict-care": 1,
        "critical-streak": 3,
        "communication-growth": 3,
    }


def test_failure_breaks_critical_streak_and_noncritical_does_not_count():
    from app.domain.gamification import progress_for, reward_for

    facts = [
        reward_for(*play(sid="a")),
        reward_for(*play(choices=("__timeout__",), sid="b")),
        reward_for(*play(sid="c")),
    ]
    progress = progress_for(facts)
    assert progress.values["critical-streak"] == 1
    assert progress.values["safe-shift"] == 2


@pytest.mark.parametrize(
    "xp,level,start,end",
    [
        (0, 1, 0, 100),
        (99, 1, 0, 100),
        (100, 2, 100, 300),
        (299, 2, 100, 300),
        (300, 3, 300, 600),
        (600, 4, 600, 1000),
    ],
)
def test_level_boundaries(xp, level, start, end):
    from app.domain.gamification import level_for

    assert level_for(xp) == (level, start, end)
