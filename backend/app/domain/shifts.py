"""Reproducible training routes and professional summaries, independent of XP."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from random import Random
from types import MappingProxyType
from typing import Literal

from .common import DomainError, require_integer
from .engine import restore_session
from .gameplay import ScenarioSession, SessionStatus
from .scenario import Scenario
from .scoring import Metric, MetricRef

Difficulty = Literal["standard", "advanced"]
StageKind = Literal["service", "conflict", "critical"]
LOYALTY = MetricRef(Metric.PASSENGER_LOYALTY)
SAFETY = MetricRef(Metric.SAFETY_RATING)


@dataclass(frozen=True, slots=True)
class ShiftVariant:
    scenario_id: str
    title: str
    passenger_profile: str
    context: str


SHIFT_VARIANTS: Mapping[str, tuple[ShiftVariant, ...]] = MappingProxyType(
    {
        "service": (
            ShiftVariant(
                "shift-service-family",
                "Посадка: помощь семье",
                "Семья с ребёнком",
                "Плотная посадка; проход необходимо оставить свободным.",
            ),
            ShiftVariant(
                "shift-service-transfer",
                "Сервис: короткая пересадка",
                "Пассажир с пересадкой",
                "Пассажир спешит и просит исключение из порядка обслуживания.",
            ),
        ),
        "conflict": (
            ShiftVariant(
                "shift-conflict-noise",
                "Обращение: шум в вагоне",
                "Пассажир, которому нужен отдых",
                "Две стороны по-разному понимают допустимый уровень шума.",
            ),
            ShiftVariant(
                "shift-conflict-seat",
                "Обращение: спор о месте",
                "Пассажир с багажом",
                "Соседи спорят о размещении вещей и личном пространстве.",
            ),
        ),
        "critical": (
            ShiftVariant(
                "shift-critical-passage",
                "Нештатная ситуация: проход",
                "Группа с крупным багажом",
                "В учебной модели проход перекрыт перед проверкой готовности.",
            ),
            ShiftVariant(
                "shift-critical-alert",
                "Нештатная ситуация: сигнал",
                "Встревоженный пассажир",
                "Поступил неполный сигнал; нужны уточнение и передача ответственному.",
            ),
        ),
    }
)


@dataclass(frozen=True, slots=True)
class ShiftStage:
    index: int
    kind: StageKind
    title: str
    scenario_id: str
    scenario_version: int
    passenger_profile: str
    context: str


def shift_plan(seed: int, difficulty: str) -> tuple[ShiftStage, ...]:
    require_integer(seed, "seed")
    if not 0 <= seed <= 2147483647 or difficulty not in {"standard", "advanced"}:
        raise DomainError("Invalid shift seed or difficulty")
    random = Random(seed)
    kinds: tuple[StageKind, ...] = ("service", "conflict", "critical")
    result = []
    for index, kind in enumerate(kinds):
        variant = random.choice(SHIFT_VARIANTS[kind])
        result.append(
            ShiftStage(
                index,
                kind,
                variant.title,
                variant.scenario_id,
                2 if difficulty == "advanced" else 1,
                variant.passenger_profile,
                variant.context,
            )
        )
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ShiftMetrics:
    safety: float | None
    service: float | None
    regulation: int
    communication: int
    average_reaction_seconds: float | None
    decision_count: int
    critical_errors: int
    completed_scenarios: int
    total_scenarios: int
    xp: int


def shift_metrics(
    attempts: Iterable[tuple[Scenario, ScenarioSession]], *, xp: int = 0
) -> ShiftMetrics:
    require_integer(xp, "xp")
    if xp < 0:
        raise DomainError("XP cannot be negative")
    pairs = tuple(attempts)
    if len(pairs) > 3:
        raise DomainError("A shift contains three training stages")
    regulation = communication = critical = completed = decision_count = 0
    elapsed = []
    for scenario, session in pairs:
        restore_session(scenario, session)
        assert session.initial_scores is not None
        for key in ("regulation", "communication"):
            if key in scenario.competency_ids:
                metric = MetricRef(Metric.COMPETENCY, key)
                delta = session.scores.value(metric) - session.initial_scores.value(
                    metric
                )
                if key == "regulation":
                    regulation += delta
                else:
                    communication += delta
        completed += int(session.status is SessionStatus.COMPLETED)
        entered_at = session.started_at
        for decision in session.decisions:
            decision_count += 1
            node = scenario.node(decision.node_id)
            if decision.choice_id != node.timeout_choice_id:
                elapsed.append((decision.decided_at - entered_at).total_seconds())
            critical += int(
                node.time_limit_seconds is not None
                and (
                    decision.choice_id == node.timeout_choice_id
                    or any(
                        c.metric == SAFETY and c.applied_delta < 0
                        for c in decision.score_changes
                    )
                )
            )
            entered_at = decision.decided_at
    return ShiftMetrics(
        sum(s.scores.value(SAFETY) for _, s in pairs) / len(pairs) if pairs else None,
        sum(s.scores.value(LOYALTY) for _, s in pairs) / len(pairs) if pairs else None,
        regulation,
        communication,
        sum(elapsed) / len(elapsed) if elapsed else None,
        decision_count,
        critical,
        completed,
        3,
        xp,
    )


@dataclass(frozen=True, slots=True)
class Qualification:
    id: str
    title: str
    description: str


QUALIFICATIONS = (
    Qualification(
        "shift-safe",
        "Надёжный маршрут",
        "Завершите смену без критических ошибок с безопасностью не ниже 60.",
    ),
    Qualification(
        "shift-regulation",
        "По регламенту",
        "Улучшите соблюдение регламента в каждом этапе без его снижения в решениях.",
    ),
    Qualification(
        "shift-conflict",
        "Диалог без эскалации",
        "Пройдите конфликтный этап с приростом коммуникации и без снижения сервиса.",
    ),
    Qualification(
        "shift-response",
        "Вовремя и точно",
        "Все решения завершённой смены приняты за 1–15 секунд без отрицательных "
        "последствий.",
    ),
    Qualification(
        "shift-advanced",
        "Подготовка: сложная смена",
        "Завершите сложную смену без критических ошибок.",
    ),
    Qualification(
        "shift-growth",
        "Рост навыка",
        "После отрицательного результата прошлого завершённого маршрута получите "
        "положительный прирост того же навыка: регламента или коммуникации.",
    ),
    Qualification(
        "shift-steady",
        "Три надёжные смены",
        "Завершите три смены подряд без критических ошибок с безопасностью не ниже 50.",
    ),
)


def qualification_ids(
    attempts: Iterable[tuple[Scenario, ScenarioSession]],
    metrics: ShiftMetrics,
    difficulty: str,
    *,
    completed: bool,
    safe_streak: int = 0,
    previous_competencies: Mapping[str, int] | None = None,
) -> tuple[str, ...]:
    pairs = tuple(attempts)
    if not completed or metrics.completed_scenarios != 3:
        return ()
    earned = []
    changes = [c for _, s in pairs for d in s.decisions for c in d.score_changes]
    if metrics.critical_errors == 0 and (metrics.safety or 0) >= 60:
        earned.append("shift-safe")
    regulation = MetricRef(Metric.COMPETENCY, "regulation")
    if all(
        sum(
            c.applied_delta
            for d in s.decisions
            for c in d.score_changes
            if c.metric == regulation
        )
        > 0
        for _, s in pairs
    ) and not any(c.metric == regulation and c.applied_delta < 0 for c in changes):
        earned.append("shift-regulation")
    for scenario, session in pairs:
        if scenario.id.startswith("shift-conflict-"):
            communication = MetricRef(Metric.COMPETENCY, "communication")
            local = [c for d in session.decisions for c in d.score_changes]
            if sum(
                c.applied_delta for c in local if c.metric == communication
            ) >= 2 and not any(
                c.metric == LOYALTY and c.applied_delta < 0 for c in local
            ):
                earned.append("shift-conflict")
    elapsed = []
    for _, session in pairs:
        entered = session.started_at
        for decision in session.decisions:
            elapsed.append((decision.decided_at - entered).total_seconds())
            entered = decision.decided_at
    if (
        elapsed
        and all(1 <= seconds <= 15 for seconds in elapsed)
        and not any(c.applied_delta < 0 for c in changes)
        and metrics.critical_errors == 0
    ):
        earned.append("shift-response")
    if difficulty == "advanced" and metrics.critical_errors == 0:
        earned.append("shift-advanced")
    if previous_competencies and any(
        previous_competencies.get(key, 0) < 0 and current > 0
        for key, current in (
            ("regulation", metrics.regulation),
            ("communication", metrics.communication),
        )
    ):
        earned.append("shift-growth")
    if safe_streak >= 3:
        earned.append("shift-steady")
    return tuple(earned)


def shift_recommendations(metrics: ShiftMetrics) -> tuple[str, ...]:
    advice = []
    if metrics.critical_errors:
        advice.append(
            "Разберите решения с таймаутом или снижением безопасности на срочном этапе."
        )
    if metrics.regulation <= 0:
        advice.append(
            "Повторите проверку условий и подтверждение передачи информации: это "
            "развивает соблюдение регламента."
        )
    if metrics.communication <= 0:
        advice.append(
            "Уточняйте запрос пассажира и озвучивайте следующий шаг до действия."
        )
    if (
        metrics.average_reaction_seconds is not None
        and metrics.average_reaction_seconds > 15
    ):
        advice.append(
            "Сначала выделите срочную задачу; скорость оценивайте вместе с качеством "
            "решения."
        )
    if not advice:
        advice.append(
            "Сохраните качество решений и попробуйте сложную смену с дополнительными "
            "событиями."
        )
    return tuple(advice)
