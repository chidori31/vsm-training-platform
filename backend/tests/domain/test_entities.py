from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo

import pytest

from app.domain.achievements import Achievement, AchievementUnlock
from app.domain.analytics import summarize_session
from app.domain.common import DomainError
from app.domain.competencies import Competency, CompetencyProgress
from app.domain.gameplay import Decision, ScenarioSession, SessionStatus
from app.domain.notifications import Notification
from app.domain.profiles import EmployeeProfile
from app.domain.rules import Condition
from app.domain.scoring import AddScore, Metric, MetricRef, ScoreState

NOW = datetime(2026, 9, 26, tzinfo=UTC)
SAFETY = MetricRef(Metric.SAFETY_RATING)


class RepeatedHour(tzinfo):
    def utcoffset(self, dt):
        return timedelta(hours=1 if dt.fold else 2)

    def dst(self, dt):
        return timedelta(0)


def test_repeated_local_hour_uses_absolute_time_for_duration_and_order():
    zone = RepeatedHour()
    early = datetime(2026, 10, 25, 2, 30, tzinfo=zone, fold=0)
    late = early.replace(fold=1)
    completed = replace(
        session(), started_at=early, completed_at=late, status=SessionStatus.COMPLETED
    )
    assert summarize_session(completed).duration_seconds == 3600
    with pytest.raises(DomainError):
        replace(completed, started_at=late, completed_at=early)
    with pytest.raises(DomainError):
        replace(
            session(),
            started_at=late,
            decisions=(replace(decision(), decided_at=early),),
        )
    with pytest.raises(DomainError):
        Notification("n", "e", "Title", "Body", late, early)


def session():
    return ScenarioSession(
        "s1", "employee1", "boarding", 1, "start", ScoreState({SAFETY: 10}), NOW
    )


def decision():
    return Decision(
        "d1",
        "s1",
        "start",
        "next",
        1,
        NOW + timedelta(seconds=2),
        (AddScore(SAFETY, 2),),
    )


def test_completed_session_and_analytics_use_recorded_decisions():
    completed = replace(
        session(),
        current_node_id="end",
        status=SessionStatus.COMPLETED,
        completed_at=NOW + timedelta(seconds=3),
        decisions=(decision(),),
        scores=ScoreState({SAFETY: 12}),
    )
    summary = summarize_session(completed)
    assert summary.session_id == "s1"
    assert summary.decision_count == 1
    assert summary.duration_seconds == 3
    assert summary.scores.values[SAFETY] == 12
    assert summarize_session(session()).duration_seconds is None


def test_session_rejects_inconsistent_status_and_history():
    for changes in (
        {"status": SessionStatus.COMPLETED},
        {"completed_at": NOW},
        {"status": "finished"},
        {"scenario_version": 0},
        {"decisions": (replace(decision(), session_id="other"),)},
        {"decisions": (replace(decision(), sequence=2),)},
        {"decisions": (replace(decision(), decided_at=NOW - timedelta(seconds=1)),)},
        {"decisions": (decision(), replace(decision(), sequence=2))},
        {
            "decisions": (
                decision(),
                replace(decision(), id="d2", sequence=2, decided_at=NOW),
            )
        },
        {
            "status": SessionStatus.COMPLETED,
            "completed_at": NOW,
            "decisions": (decision(),),
        },
    ):
        with pytest.raises(DomainError):
            replace(session(), **changes)


def test_profile_progress_references_and_duplicate_competencies():
    competency = Competency("service", "Сервис", "Обслуживание пассажиров")
    progress = CompetencyProgress(competency.id, 3)
    source = [progress]
    profile = EmployeeProfile("employee1", "Участник", source)
    source.clear()
    assert profile.competencies == (progress,)
    with pytest.raises(DomainError):
        replace(profile, competencies=(progress, progress))
    with pytest.raises(DomainError):
        CompetencyProgress("service", True)
    with pytest.raises(DomainError):
        Competency("", "Name", "Description")


def test_achievement_unlock_and_notification_references():
    achievement = Achievement("first", 1, "Первый шаг", "Описание", Condition())
    unlock = AchievementUnlock(
        "u1", "employee1", achievement.id, achievement.version, "s1", NOW
    )
    notification = Notification(
        "n1", unlock.employee_id, "Достижение", "Первый шаг", NOW
    )
    assert replace(notification, read_at=NOW).read_at == NOW
    with pytest.raises(DomainError):
        replace(notification, read_at=NOW - timedelta(seconds=1))
    with pytest.raises(DomainError):
        replace(unlock, achievement_version=0)
    with pytest.raises(DomainError):
        replace(achievement, version=0)


def test_entities_reject_empty_ids_and_naive_timestamps():
    for entity in (
        session(),
        decision(),
        EmployeeProfile("e", "Name"),
        Achievement("a", 1, "Name", "Description", Condition()),
        AchievementUnlock("u", "e", "a", 1, "s", NOW),
        Notification("n", "e", "Title", "Body", NOW),
    ):
        with pytest.raises(DomainError):
            replace(entity, id=" ")
    for entity, field in (
        (session(), "started_at"),
        (decision(), "decided_at"),
        (AchievementUnlock("u", "e", "a", 1, "s", NOW), "unlocked_at"),
        (Notification("n", "e", "Title", "Body", NOW), "created_at"),
    ):
        with pytest.raises(DomainError):
            replace(entity, **{field: NOW.replace(tzinfo=None)})
