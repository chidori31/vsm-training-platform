"""Rule v1 learning facts from verified, version-pinned decision history."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .common import DomainError
from .engine import restore_session
from .gameplay import ScenarioSession, SessionStatus
from .scenario import Choice, Scenario
from .scoring import Metric, MetricRef, ScoreState, apply_scored_effects

LOYALTY = MetricRef(Metric.PASSENGER_LOYALTY)
SAFETY = MetricRef(Metric.SAFETY_RATING)


@dataclass(frozen=True)
class ScaleExplanation:
    before: int
    after: int
    delta: int
    requested_delta: int
    explanation: str


@dataclass(frozen=True)
class CompetencyExplanation:
    competency_id: str
    before: int
    after: int
    delta: int
    explanation: str


@dataclass(frozen=True)
class CompetencyDelta:
    competency_id: str
    delta: int


@dataclass(frozen=True)
class Alternative:
    choice_id: str
    text: str
    destination_id: str
    explanation: str
    available: bool
    loyalty_delta: int
    safety_delta: int
    competencies: tuple[CompetencyDelta, ...]


@dataclass(frozen=True)
class Suggestion:
    text: str
    choice_ids: tuple[str, ...]


@dataclass(frozen=True)
class DecisionAssessment:
    status: Literal["critical_error", "attention", "strong", "neutral"]
    title: str
    explanation: str
    is_critical: bool


@dataclass(frozen=True)
class DecisionDebrief:
    sequence: int
    decision_id: str
    node_id: str
    node_text: str
    choice_id: str
    choice_text: str
    destination_id: str
    destination_text: str
    decided_at: datetime
    elapsed_seconds: float
    was_timeout: bool
    explanation: str
    loyalty: ScaleExplanation
    safety: ScaleExplanation
    competencies: tuple[CompetencyExplanation, ...]
    alternatives: tuple[Alternative, ...]
    suggestion: Suggestion
    pattern_codes: tuple[str, ...]
    assessment: DecisionAssessment


@dataclass(frozen=True)
class DebriefSummary:
    decision_count: int
    timeout_count: int
    loyalty_delta: int
    safety_delta: int


@dataclass(frozen=True)
class SessionDebrief:
    rule_version: int
    session_id: str
    scenario_id: str
    scenario_version: int
    title: str
    completed_at: datetime
    summary: DebriefSummary
    decisions: tuple[DecisionDebrief, ...]


def _explain(label: str, before: int, after: int, requested: int, reason: str) -> str:
    delta = after - before
    if delta == 0:
        text = f"{label} не изменяется: {before} → {after}."
    else:
        text = f"{label}: {before} → {after} ({delta:+d})."
    if requested != delta:
        text += f" Запрошено {requested:+d}; применено {delta:+d} из-за границ шкалы."
    elif requested == 0:
        text += " Ненулевое изменение этой шкалы не предусмотрено."
    if reason and requested != 0:
        text += f" {reason}"
    return text


def _scale(
    metric: MetricRef, label: str, before: ScoreState, after: ScoreState, choice: Choice
) -> ScaleExplanation:
    requested = sum(e.delta for e in choice.effects if e.metric == metric)
    initial, final = before.value(metric), after.value(metric)
    return ScaleExplanation(
        initial,
        final,
        final - initial,
        requested,
        _explain(label, initial, final, requested, choice.explanation),
    )


def _suggestion(
    actual: tuple[int, ...], alternatives: tuple[Alternative, ...], timeout: bool
) -> Suggestion:
    if timeout and not any(a.available for a in alternatives):
        return Suggestion(
            "В этом состоянии доступных действий не было: условия вариантов"
            " не выполнены. Таймаут произошёл без возможности сделать выбор.",
            (),
        )
    better = []
    tradeoff = False
    for alternative in alternatives:
        if not alternative.available:
            continue
        candidate = (
            alternative.loyalty_delta,
            alternative.safety_delta,
            *(c.delta for c in alternative.competencies),
        )
        improvements = [a > b for a, b in zip(candidate, actual, strict=True)]
        losses = [a < b for a, b in zip(candidate, actual, strict=True)]
        if any(improvements) and not any(losses):
            better.append(alternative.choice_id)
        tradeoff |= any(improvements) and any(losses)
    scope = " Сравнение учитывает только непосредственные эффекты решения."
    if timeout:
        text = (
            "При следующей попытке оцените доступные варианты и примите решение в срок."
        )
        if better:
            text += (
                " Отмеченные варианты улучшали результат без потерь по другим шкалам."
            )
    elif better:
        text = (
            "При следующей попытке рассмотрите отмеченные варианты: они улучшают"
            " хотя бы одну шкалу или компетенцию без снижения остальных."
        )
    elif tradeoff:
        text = (
            "Альтернативы содержат компромисс: улучшение одной шкалы сопровождается"
            " снижением другой. Сопоставьте последствия с задачей ситуации."
        )
    else:
        text = (
            "Доступные альтернативы не дают улучшения без потерь."
            " Повторите ситуацию и объясните, как выбранное действие"
            " повлияло на результат."
        )
    return Suggestion(text + scope, tuple(better))


def _assessment(
    deltas: tuple[int, ...], *, critical: bool, timeout: bool, available: bool
) -> DecisionAssessment:
    if timeout and not available:
        return DecisionAssessment(
            "neutral",
            "Нет доступного действия",
            "Условия не позволяли сделать выбор."
            " Этот таймаут не считается ошибкой проводника.",
            critical,
        )
    if critical and (timeout or deltas[1] < 0):
        return DecisionAssessment(
            "critical_error",
            "Критическое решение требует разбора",
            "В сцене с ограничением времени пропущен выбор или снижена безопасность."
            " Разберите риск и доступные действия.",
            critical,
        )
    if timeout or any(delta < 0 for delta in deltas):
        return DecisionAssessment(
            "attention",
            "Есть зона для улучшения",
            "Решение содержит отрицательный эффект или пропущено по времени."
            " Сопоставьте последствия и альтернативы.",
            critical,
        )
    if any(delta > 0 for delta in deltas):
        return DecisionAssessment(
            "strong",
            "Сильное решение",
            "Есть положительный эффект без снижения других показателей в этом решении."
            " Это оценка непосредственного последствия.",
            critical,
        )
    return DecisionAssessment(
        "neutral",
        "Без изменения показателей",
        "Непосредственные показатели не изменились. Оцените контекст и следующий шаг.",
        critical,
    )


def debrief_for(scenario: Scenario, session: ScenarioSession) -> SessionDebrief:
    """Replay first, then reconstruct each visit's state before evaluating choices."""
    restore_session(scenario, session)
    if (
        session.status is not SessionStatus.COMPLETED
        or session.completed_at is None
        or session.initial_scores is None
    ):
        raise DomainError("Debrief requires a completed verified session")
    before = session.initial_scores
    entered_at = session.started_at
    skills = tuple(sorted(scenario.competency_ids))
    decisions = []
    for recorded in session.decisions:
        node = scenario.node(recorded.node_id)
        selected = next(c for c in node.choices if c.id == recorded.choice_id)
        after, _ = apply_scored_effects(
            before, selected.effects, policy=session.scoring_policy
        )
        loyalty = _scale(LOYALTY, "Клиентский сервис", before, after, selected)
        safety = _scale(SAFETY, "Безопасность", before, after, selected)
        competencies = []
        for key in skills:
            metric = MetricRef(Metric.COMPETENCY, key)
            initial, final = before.value(metric), after.value(metric)
            competencies.append(
                CompetencyExplanation(
                    key,
                    initial,
                    final,
                    final - initial,
                    _explain(
                        "Компетенция",
                        initial,
                        final,
                        final - initial,
                        selected.explanation,
                    ),
                )
            )
        alternatives = []
        for choice in node.choices:
            if choice.id in {node.timeout_choice_id, selected.id}:
                continue
            projected, _ = apply_scored_effects(
                before, choice.effects, policy=session.scoring_policy
            )
            # Conditions deliberately ignore the expired clock: these were the
            # user choices available at the start of this decision opportunity.
            alternatives.append(
                Alternative(
                    choice.id,
                    choice.text,
                    choice.target_node_id,
                    choice.explanation,
                    choice.condition.matches(before),
                    projected.value(LOYALTY) - before.value(LOYALTY),
                    projected.value(SAFETY) - before.value(SAFETY),
                    tuple(
                        CompetencyDelta(
                            key,
                            projected.value(MetricRef(Metric.COMPETENCY, key))
                            - before.value(MetricRef(Metric.COMPETENCY, key)),
                        )
                        for key in skills
                    ),
                )
            )
        timeout = selected.id == node.timeout_choice_id
        patterns = []
        if timeout and any(a.available for a in alternatives):
            patterns.append("timeout")
        if safety.delta < 0:
            patterns.append("safety_loss")
        if loyalty.delta < 0:
            patterns.append("loyalty_loss")
        if any(c.delta < 0 for c in competencies):
            patterns.append("competency_regression")
        decisions.append(
            DecisionDebrief(
                recorded.sequence,
                recorded.id,
                node.id,
                node.text,
                selected.id,
                selected.text,
                selected.target_node_id,
                scenario.node(selected.target_node_id).text,
                recorded.decided_at,
                (recorded.decided_at - entered_at).total_seconds(),
                timeout,
                recorded.explanation,
                loyalty,
                safety,
                tuple(competencies),
                tuple(alternatives),
                _suggestion(
                    (loyalty.delta, safety.delta, *(c.delta for c in competencies)),
                    tuple(alternatives),
                    timeout,
                ),
                tuple(patterns),
                _assessment(
                    (loyalty.delta, safety.delta, *(c.delta for c in competencies)),
                    critical=node.time_limit_seconds is not None,
                    timeout=timeout,
                    available=any(a.available for a in alternatives),
                ),
            )
        )
        before, entered_at = after, recorded.decided_at
    return SessionDebrief(
        1,
        session.id,
        scenario.id,
        scenario.version,
        scenario.title,
        session.completed_at,
        DebriefSummary(
            len(decisions),
            sum(d.was_timeout for d in decisions),
            session.scores.value(LOYALTY) - session.initial_scores.value(LOYALTY),
            session.scores.value(SAFETY) - session.initial_scores.value(SAFETY),
        ),
        tuple(decisions),
    )
