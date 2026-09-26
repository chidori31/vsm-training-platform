from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.common import DomainError
from app.domain.gameplay import Decision, ScenarioSession, SessionStatus
from app.domain.retention import Challenge, challenge_progress
from app.domain.scoring import Metric, MetricRef, ScoreState

NOW = datetime(2026, 9, 26, tzinfo=UTC)


def attempt(name="one", *, start=NOW, end=None, choice="help"):
    end = end or NOW + timedelta(minutes=1)
    return ScenarioSession(
        id=name,
        employee_id="employee",
        scenario_id=name,
        scenario_version=1,
        current_node_id="done",
        scores=ScoreState({MetricRef(Metric.SAFETY_RATING): 50}),
        started_at=start,
        completed_at=end,
        status=SessionStatus.COMPLETED,
        decisions=(Decision("decision", name, "start", choice, 1, end),),
    )


def challenge():
    return Challenge(
        "challenge", NOW, NOW + timedelta(hours=1), (("one", 1), ("two", 1)), 2
    )


def test_distinct_versions_and_repeat_do_not_inflate_progress():
    first = attempt()
    result = challenge_progress(
        challenge(),
        [first, first, replace(first, scenario_version=2)],
        NOW + timedelta(minutes=2),
    )
    assert result.progress == 1 and result.status == "active"
    result = challenge_progress(
        challenge(), [first, attempt("two")], NOW + timedelta(hours=2)
    )
    assert result.progress == 2 and result.status == "completed"


@pytest.mark.parametrize(
    "change",
    [
        {"start": NOW - timedelta(seconds=1)},
        {"end": NOW + timedelta(hours=1)},
        {"choice": "__timeout__"},
    ],
)
def test_ineligible_attempts(change):
    assert challenge_progress(challenge(), [attempt(**change)], NOW).progress == 0


def test_deadline_is_exclusive_and_scheduled_is_not_active():
    assert (
        challenge_progress(challenge(), [], NOW - timedelta(seconds=1)).status
        == "scheduled"
    )
    assert (
        challenge_progress(challenge(), [], NOW + timedelta(hours=1)).status
        == "expired"
    )


def test_invalid_window_or_unreachable_target_rejected():
    with pytest.raises(DomainError):
        replace(challenge(), expires_at=NOW)
    with pytest.raises(DomainError):
        replace(challenge(), target=3)


def test_low_safety_empty_and_unfinished_attempts_do_not_count():
    first = attempt()
    unsafe = replace(first, scores=ScoreState({MetricRef(Metric.SAFETY_RATING): 49}))
    empty = replace(first, decisions=())
    active = replace(first, status=SessionStatus.ACTIVE, completed_at=None)
    assert (
        challenge_progress(
            challenge(), [unsafe, empty, active], NOW + timedelta(minutes=2)
        ).progress
        == 0
    )


def test_future_completion_not_visible_and_last_microsecond_still_counts():
    last = attempt(end=challenge().expires_at - timedelta(microseconds=1))
    assert challenge_progress(challenge(), [last], NOW).progress == 0
    assert challenge_progress(challenge(), [last], challenge().expires_at).progress == 1
