"""Deterministic educational heuristics; no professional fitness assessment."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from .common import DomainError
from .debrief import LOYALTY, SAFETY, SessionDebrief, debrief_for
from .engine import restore_session
from .gameplay import ScenarioSession, SessionStatus
from .scenario import Scenario
from .scoring import Metric, MetricRef

CompetencyStatus = Literal["insufficient_data", "strength", "growth_area", "developing"]


@dataclass(frozen=True)
class TrendPoint:
    session_id: str
    completed_at: datetime
    delta: int
    earned_points: int
    cumulative_points: int


@dataclass(frozen=True)
class CompetencyAnalytics:
    competency_id: str
    earned_points: int
    net_delta: int
    positive_decisions: int
    negative_decisions: int
    opportunities: int
    practiced_sessions: int
    status: CompetencyStatus
    trend: tuple[TrendPoint, ...]


@dataclass(frozen=True)
class Pattern:
    code: str
    title: str
    description: str
    count: int
    session_count: int
    recurring: bool
    advice: str


@dataclass(frozen=True)
class ScenarioStatistics:
    scenario_id: str
    scenario_version: int
    title: str
    attempts: int
    completed: int
    active: int
    timeout_count: int
    average_duration_seconds: float | None
    average_loyalty: float | None
    average_safety: float | None


@dataclass(frozen=True)
class LearningAnalytics:
    rule_version: int
    total_sessions: int
    completed_sessions: int
    active_sessions: int
    decision_count: int
    timeout_count: int
    competencies: tuple[CompetencyAnalytics, ...]
    strengths: tuple[str, ...]
    weaknesses: tuple[str, ...]
    patterns: tuple[Pattern, ...]
    scenarios: tuple[ScenarioStatistics, ...]


_PATTERNS = (
    (
        "timeout",
        "Решение после срока",
        "Время на принятие решения истекло.",
        "Сначала определите срочное действие и следите за оставшимся временем.",
    ),
    (
        "safety_loss",
        "Снижение безопасности",
        "Действие снизило шкалу safety.",
        "Перед выбором проверьте непосредственные риски для безопасности.",
    ),
    (
        "loyalty_loss",
        "Снижение доверия",
        "Действие снизило шкалу loyalty.",
        "Объясните пассажиру причину действия и предложите доступную помощь.",
    ),
    (
        "competency_regression",
        "Снижение компетенции",
        "В решении снизилась хотя бы одна учебная компетенция.",
        "Сопоставьте выбранное действие с доступными альтернативами в разборе.",
    ),
)


@dataclass
class _Evidence:
    earned: int = 0
    net: int = 0
    positive: int = 0
    negative: int = 0
    opportunities: int = 0
    sessions: set[str] = field(default_factory=set)
    trend: list[TrendPoint] = field(default_factory=list)

    def fact(self, competency_id: str) -> CompetencyAnalytics:
        status: CompetencyStatus = "insufficient_data"
        if self.opportunities >= 3 and len(self.sessions) >= 2:
            if (
                self.positive * 100 >= self.opportunities * 70
                and self.negative * 100 <= self.opportunities * 20
            ):
                status = "strength"
            elif self.negative * 100 >= self.opportunities * 30:
                status = "growth_area"
            else:
                status = "developing"
        return CompetencyAnalytics(
            competency_id,
            self.earned,
            self.net,
            self.positive,
            self.negative,
            self.opportunities,
            len(self.sessions),
            status,
            tuple(self.trend),
        )


def _record_skills(
    evidence: dict[str, _Evidence],
    scenario: Scenario,
    session: ScenarioSession,
    debrief: SessionDebrief,
) -> None:
    if session.initial_scores is None:
        raise DomainError("Learning analytics requires initial scores")
    has_choice = any(not d.was_timeout for d in debrief.decisions)
    for key in sorted(scenario.competency_ids):
        row = evidence.setdefault(key, _Evidence())
        metric = MetricRef(Metric.COMPETENCY, key)
        delta = session.scores.value(metric) - session.initial_scores.value(metric)
        earned = max(0, delta) if has_choice else 0
        row.net += delta
        row.earned += earned
        row.trend.append(
            TrendPoint(
                session.id,
                debrief.completed_at,
                delta,
                earned,
                row.earned,
            )
        )
    for decision in debrief.decisions:
        observed = {
            c.competency_id
            for alternative in decision.alternatives
            if alternative.available
            for c in alternative.competencies
            if c.delta != 0
        }
        if not decision.was_timeout:
            observed.update(
                c.competency_id for c in decision.competencies if c.delta != 0
            )
        for c in decision.competencies:
            if c.competency_id not in observed:
                continue
            row = evidence[c.competency_id]
            row.opportunities += 1
            row.positive += int(c.delta > 0)
            row.negative += int(c.delta < 0)
            row.sessions.add(session.id)


def competency_analytics(
    attempts: Iterable[tuple[Scenario, ScenarioSession]],
) -> LearningAnalytics:
    """Consume the complete owner's history, independent of input order/pagination."""
    unique: dict[str, tuple[Scenario, ScenarioSession]] = {}
    for scenario, session in attempts:
        previous = unique.get(session.id)
        if previous is not None and previous != (scenario, session):
            raise DomainError("Conflicting copies of a session")
        unique[session.id] = (scenario, session)
    completed = sorted(
        (pair for pair in unique.values() if pair[1].status is SessionStatus.COMPLETED),
        key=lambda pair: (pair[1].completed_at or pair[1].started_at, pair[1].id),
    )
    evidence: dict[str, _Evidence] = {}
    reports: dict[str, SessionDebrief] = {}
    pattern_decisions: dict[str, int] = {}
    pattern_sessions: dict[str, set[str]] = {}
    for scenario, session in completed:
        report = debrief_for(scenario, session)
        reports[session.id] = report
        _record_skills(evidence, scenario, session, report)
        for decision in report.decisions:
            for code in decision.pattern_codes:
                pattern_decisions[code] = pattern_decisions.get(code, 0) + 1
                pattern_sessions.setdefault(code, set()).add(session.id)
    groups: dict[tuple[str, int], list[tuple[Scenario, ScenarioSession]]] = {}
    for scenario, session in unique.values():
        if session.status is not SessionStatus.COMPLETED:
            restore_session(scenario, session)
        groups.setdefault((scenario.id, scenario.version), []).append(
            (scenario, session)
        )
    statistics = []
    for (scenario_id, version), values in sorted(groups.items()):
        finished = sorted(
            (s for _, s in values if s.id in reports),
            key=lambda s: (reports[s.id].completed_at, s.id),
        )
        count = len(finished)
        statistics.append(
            ScenarioStatistics(
                scenario_id,
                version,
                values[0][0].title,
                len(values),
                count,
                len(values) - count,
                sum(reports[s.id].summary.timeout_count for s in finished),
                sum(
                    (reports[s.id].completed_at - s.started_at).total_seconds()
                    for s in finished
                )
                / count
                if count
                else None,
                sum(s.scores.value(LOYALTY) for s in finished) / count
                if count
                else None,
                sum(s.scores.value(SAFETY) for s in finished) / count
                if count
                else None,
            )
        )
    competencies = tuple(row.fact(key) for key, row in sorted(evidence.items()))
    patterns = tuple(
        Pattern(
            code,
            title,
            description,
            pattern_decisions[code],
            len(pattern_sessions[code]),
            len(pattern_sessions[code]) >= 2,
            advice,
        )
        for code, title, description, advice in _PATTERNS
        if code in pattern_decisions
    )
    return LearningAnalytics(
        1,
        len(unique),
        len(completed),
        len(unique) - len(completed),
        sum(r.summary.decision_count for r in reports.values()),
        sum(r.summary.timeout_count for r in reports.values()),
        competencies,
        tuple(c.competency_id for c in competencies if c.status == "strength"),
        tuple(c.competency_id for c in competencies if c.status == "growth_area"),
        patterns,
        tuple(statistics),
    )
