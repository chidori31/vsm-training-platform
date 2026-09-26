"""Deterministic reward rules. No transport, database or wall clock dependencies."""

from collections.abc import Iterable
from dataclasses import dataclass
from math import isqrt

from .common import DomainError, require_integer
from .gameplay import ScenarioSession, SessionStatus
from .scenario import Scenario
from .scoring import Metric, MetricRef


@dataclass(frozen=True)
class BehaviorAchievement:
    id: str
    name: str
    description: str
    target: int


ACHIEVEMENTS = (
    BehaviorAchievement(
        "safe-shift",
        "Надёжная смена",
        "Завершите 3 ситуации с safety не ниже 50, без его снижения и без таймаутов.",
        3,
    ),
    BehaviorAchievement(
        "conflict-care",
        "Общий язык",
        "Разрешите демо-конфликт пассажиров без снижения доверия и без таймаута.",
        1,
    ),
    BehaviorAchievement(
        "critical-streak",
        "Точно в срок",
        "Примите 3 критических решения подряд вовремя и без снижения safety.",
        3,
    ),
    BehaviorAchievement(
        "communication-growth",
        "Мастер диалога",
        "Накопите 3 очка компетенции «Коммуникация».",
        3,
    ),
)


@dataclass(frozen=True)
class RewardFact:
    session_id: str
    xp: int
    competencies: tuple[tuple[str, int], ...]
    safe_completion: bool
    conflict_resolved: bool
    critical: tuple[bool, ...]


@dataclass(frozen=True)
class Progress:
    xp: int
    level: int
    level_start: int
    next_level: int
    competencies: tuple[tuple[str, int], ...]
    values: dict[str, int]


def level_for(xp: int) -> tuple[int, int, int]:
    require_integer(xp, "XP")
    if xp < 0:
        raise DomainError("XP cannot be negative")
    level = (1 + isqrt(1 + 4 * (xp // 50))) // 2
    return level, 50 * level * (level - 1), 50 * level * (level + 1)


def reward_for(scenario: Scenario, session: ScenarioSession) -> RewardFact:
    if session.status is not SessionStatus.COMPLETED or session.initial_scores is None:
        raise DomainError("Only completed verified sessions earn rewards")
    if (session.scenario_id, session.scenario_version) != (
        scenario.id,
        scenario.version,
    ):
        raise DomainError("Reward scenario mismatch")
    initial = session.initial_scores
    loyalty = MetricRef(Metric.PASSENGER_LOYALTY)
    safety = MetricRef(Metric.SAFETY_RATING)
    has_choice = any(d.choice_id != "__timeout__" for d in session.decisions)
    timeout = any(d.choice_id == "__timeout__" for d in session.decisions)
    changes = [c for d in session.decisions for c in d.score_changes]
    safe = not any(c.metric == safety and c.applied_delta < 0 for c in changes)
    loyal = not any(c.metric == loyalty and c.applied_delta < 0 for c in changes)
    critical = tuple(
        d.choice_id != "__timeout__"
        and not any(c.metric == safety and c.applied_delta < 0 for c in d.score_changes)
        for d in session.decisions
        if scenario.node(d.node_id).time_limit_seconds is not None
    )
    competencies = tuple(
        sorted(
            (
                key,
                max(
                    0,
                    session.scores.value(MetricRef(Metric.COMPETENCY, key))
                    - initial.value(MetricRef(Metric.COMPETENCY, key)),
                ),
            )
            for key in scenario.competency_ids
            if has_choice
            and session.scores.value(MetricRef(Metric.COMPETENCY, key))
            > initial.value(MetricRef(Metric.COMPETENCY, key))
        )
    )
    gain = sum(
        max(0, session.scores.value(m) - initial.value(m)) for m in (loyalty, safety)
    )
    xp = (
        (
            20
            + min(50, gain * 5)
            + min(30, sum(v for _, v in competencies) * 10)
            + min(30, sum(critical) * 10)
        )
        if has_choice
        else 0
    )
    return RewardFact(
        session.id,
        xp,
        competencies,
        has_choice and not timeout and safe and session.scores.value(safety) >= 50,
        has_choice
        and not timeout
        and loyal
        and scenario.id == "demo-passenger-conflict"
        and scenario.version == 1
        and session.current_node_id == "resolved",
        critical,
    )


def progress_for(facts: Iterable[RewardFact]) -> Progress:
    xp = safe = conflict = streak = 0
    streak_unlocked = False
    competencies: dict[str, int] = {}
    seen: set[str] = set()
    for fact in facts:
        if fact.session_id in seen:
            continue
        seen.add(fact.session_id)
        xp += fact.xp
        safe += int(fact.safe_completion)
        conflict += int(fact.conflict_resolved)
        for key, value in fact.competencies:
            competencies[key] = competencies.get(key, 0) + value
        for success in fact.critical:
            streak = streak + 1 if success else 0
            streak_unlocked |= streak >= 3
    level, start, end = level_for(xp)
    return Progress(
        xp,
        level,
        start,
        end,
        tuple(sorted(competencies.items())),
        {
            "safe-shift": min(3, safe),
            "conflict-care": min(1, conflict),
            "critical-streak": 3 if streak_unlocked else min(3, streak),
            "communication-growth": min(3, competencies.get("communication", 0)),
        },
    )
