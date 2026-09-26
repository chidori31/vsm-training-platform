"""Deterministic operational training, without transport or persistence imports."""

from dataclasses import asdict, dataclass, replace
from random import Random
from typing import Any

from .common import DomainError, require_integer

MAX_COMMANDS = 5000

METRICS = ("safety", "service", "regulation", "prioritization", "communication")


@dataclass(frozen=True, slots=True)
class Zone:
    id: str
    title: str
    kind: str


@dataclass(frozen=True, slots=True)
class Station:
    id: str
    title: str
    arrival_seconds: int
    departure_seconds: int


@dataclass(frozen=True, slots=True)
class Equipment:
    id: str
    title: str
    zone_id: str
    carried: bool = False
    available: bool = True


@dataclass(frozen=True, slots=True)
class IncidentSpec:
    id: str
    kind: str
    title: str
    zones: tuple[str, ...]
    reported_at_seconds: int
    observation: str
    facts: tuple[str, ...]
    causes: tuple[str, ...]
    alternatives: tuple[str, ...]
    equipment_id: str | None = None
    communication_type: str | None = None
    station_id: str | None = None


@dataclass(frozen=True, slots=True)
class Definition:
    id: str
    version: int
    title: str
    duration_seconds: int
    zones: tuple[Zone, ...]
    stations: tuple[Station, ...]
    equipment: tuple[Equipment, ...]
    incidents: tuple[IncidentSpec, ...]
    passenger_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PassengerState:
    id: str
    name: str
    zone_id: str
    trust: int
    stress: int
    satisfaction: int
    health: int
    needs: tuple[str, ...]
    history: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IncidentState:
    id: str
    kind: str
    zone_id: str
    passenger_id: str
    cause: str
    reported_at_seconds: int
    status: str = "scheduled"
    severity: int = 1
    discovered_at_seconds: int | None = None
    first_reaction_seconds: int | None = None
    resolved_at_seconds: int | None = None
    facts: tuple[str, ...] = ()
    contained: bool = False
    deferred: bool = False
    talked: bool = False
    recorded: bool = False
    confirmed: bool = False


@dataclass(frozen=True, slots=True)
class CommunicationRequest:
    id: str
    incident_id: str
    type: str
    title: str
    status: str
    requested_at_seconds: int
    expected_response_seconds: int
    result: str | None = None


@dataclass(frozen=True, slots=True)
class Consequence:
    id: str
    at_seconds: int
    kind: str
    incident_id: str | None
    explanation: str
    severity: int = 0


@dataclass(frozen=True, slots=True)
class MetricChange:
    metric: str
    delta: int
    before: int
    after: int


@dataclass(frozen=True, slots=True)
class JournalEntry:
    id: str
    at_seconds: int
    kind: str
    incident_id: str | None
    title: str
    explanation: str
    metric_changes: tuple[MetricChange, ...] = ()


@dataclass(frozen=True, slots=True)
class AppliedAction:
    at_seconds: int
    action_id: str
    incident_id: str | None
    zone_id: str | None


@dataclass(frozen=True, slots=True)
class SimulationState:
    seed: int
    duration: int
    elapsed: int
    status: str
    revision: int
    location: str
    incidents: tuple[IncidentState, ...]
    passengers: tuple[PassengerState, ...]
    equipment: tuple[Equipment, ...]
    communications: tuple[CommunicationRequest, ...]
    consequences: tuple[Consequence, ...]
    metrics: tuple[tuple[str, int], ...]
    journal: tuple[JournalEntry, ...]
    commands: tuple[AppliedAction, ...] = ()


def _incident(state: SimulationState, identity: str) -> IncidentState:
    for incident in state.incidents:
        if incident.id == identity:
            return incident
    raise DomainError("Unknown incident")


def _spec(definition: Definition, identity: str) -> IncidentSpec:
    return next(i for i in definition.incidents if i.id == identity)


def _put(state: SimulationState, incident: IncidentState) -> SimulationState:
    return replace(
        state,
        incidents=tuple(
            incident if i.id == incident.id else i for i in state.incidents
        ),
    )


def _log(
    state: SimulationState,
    kind: str,
    identity: str | None,
    title: str,
    explanation: str,
    **deltas: int,
) -> SimulationState:
    values = dict(state.metrics)
    changes = []
    for key in METRICS:
        if key in deltas:
            before = values[key]
            values[key] = max(0, min(100, before + deltas[key]))
            changes.append(MetricChange(key, values[key] - before, before, values[key]))
    entry = JournalEntry(
        f"event-{state.revision + 1}",
        state.elapsed,
        kind,
        identity,
        title,
        explanation,
        tuple(changes),
    )
    return replace(
        state,
        revision=state.revision + 1,
        metrics=tuple(values.items()),
        journal=state.journal + (entry,),
    )


def _npc(
    state: SimulationState,
    incident: IncidentState,
    action: str,
    trust: int = 0,
    stress: int = 0,
    health: int = 0,
) -> SimulationState:
    return replace(
        state,
        passengers=tuple(
            replace(
                p,
                trust=max(0, min(100, p.trust + trust)),
                stress=max(0, min(100, p.stress + stress)),
                satisfaction=max(0, min(100, p.satisfaction + trust)),
                health=max(0, min(100, p.health + health)),
                history=p.history + (action,),
            )
            if p.id == incident.passenger_id
            else p
            for p in state.passengers
        ),
    )


def start(definition: Definition, seed: int) -> SimulationState:
    require_integer(seed, "seed")
    if not 0 <= seed <= 2147483647:
        raise DomainError("Invalid seed")
    random = Random(seed)
    names = list(definition.passenger_names)
    random.shuffle(names)
    incidents: list[IncidentState] = []
    passengers: list[PassengerState] = []
    consequences: list[Consequence] = []
    for index, spec in enumerate(definition.incidents):
        zone = random.choice(spec.zones)
        reported = spec.reported_at_seconds + (random.randrange(21) if index else 0)
        passenger = PassengerState(
            f"passenger-{index}",
            names[index % len(names)],
            zone,
            random.randint(40, 65),
            random.randint(15, 45),
            50,
            60 if spec.kind == "health" else 90,
            (spec.kind,),
        )
        if spec.kind == "health":
            # The later wellbeing report belongs to the earlier service passenger.
            # Earlier trust/history therefore changes later cooperation.
            passenger = passengers[0]
            zone = passenger.zone_id
        else:
            passengers.append(passenger)
        incidents.append(
            IncidentState(
                spec.id,
                spec.kind,
                zone,
                passenger.id,
                random.choice(spec.causes),
                reported,
            )
        )
        consequences.append(
            Consequence(
                f"report:{spec.id}", reported, "report", spec.id, spec.observation
            )
        )
        for level, delay in enumerate((60, 150, 270), 2):
            consequences.append(
                Consequence(
                    f"escalate:{spec.id}:{level}",
                    reported + delay,
                    "escalation",
                    spec.id,
                    "Обращение осталось без завершённого контроля; риск и напряжение "
                    "выросли.",
                    level,
                )
            )
    for station in definition.stations:
        for phase, second in (
            ("arrival", station.arrival_seconds),
            ("departure", station.departure_seconds),
        ):
            consequences.append(
                Consequence(
                    f"{phase}:{station.id}",
                    second,
                    "station",
                    None,
                    ("Прибытие: " if phase == "arrival" else "Отправление: ")
                    + station.title,
                )
            )
    state = SimulationState(
        seed,
        definition.duration_seconds,
        0,
        "active",
        0,
        "service",
        tuple(incidents),
        tuple(passengers),
        definition.equipment,
        (),
        tuple(consequences),
        tuple((k, 50) for k in METRICS),
        (),
    )
    return advance(definition, state, 0)


def _fire(
    definition: Definition, state: SimulationState, event: Consequence
) -> SimulationState:
    if event.kind == "station":
        return _log(
            state,
            "station",
            None,
            event.explanation,
            "Учебное расписание: доступность задач на стоянке изменилась.",
        )
    assert event.incident_id is not None
    incident = _incident(state, event.incident_id)
    spec = _spec(definition, incident.id)
    if event.kind == "report":
        state = _put(state, replace(incident, status="reported"))
        return _log(state, "reported", incident.id, spec.title, event.explanation)
    if event.kind == "escalation":
        if spec.station_id and incident.confirmed:
            station = next(s for s in definition.stations if s.id == spec.station_id)
            if state.elapsed < station.departure_seconds:
                return state
        if incident.contained or incident.status in {"handled", "resolved"}:
            return state
        stage = {2: "ignored", 3: "escalated", 4: "critical"}[event.severity]
        state = _put(state, replace(incident, status=stage, severity=event.severity))
        state = _npc(
            state,
            incident,
            stage,
            trust=-5,
            stress=12,
            health=-5 if incident.kind == "health" else 0,
        )
        return _log(
            state,
            "escalation",
            incident.id,
            f"{spec.title}: ситуация ухудшилась",
            event.explanation,
            safety=-5 if incident.kind in {"safety", "health"} else 0,
            service=-3,
            prioritization=-4,
            regulation=-2,
        )
    if event.kind == "response":
        state = replace(
            state,
            communications=tuple(
                replace(c, status="answered", result=event.explanation)
                if c.id == event.id.removeprefix("response:")
                else c
                for c in state.communications
            ),
        )
        return _log(
            state,
            "communication",
            incident.id,
            "Получен ответ службы",
            event.explanation,
        )
    if event.kind == "resolve" and incident.status == "handled":
        state = _put(
            state,
            replace(incident, status="resolved", resolved_at_seconds=state.elapsed),
        )
        state = _npc(state, incident, "resolved", trust=8, stress=-20)
        return _log(
            state,
            "consequence",
            incident.id,
            "Результат подтверждён",
            event.explanation,
            safety=3,
            service=4,
            regulation=3,
        )
    if event.kind == "complaint" and incident.status != "resolved":
        passenger = next(p for p in state.passengers if p.id == incident.passenger_id)
        if passenger.trust < 50:
            return _log(
                state,
                "consequence",
                incident.id,
                "Повторное обращение пассажира",
                "После отсрочки без результата пассажир потерял доверие и обратился "
                "повторно.",
                service=-5,
                communication=-3,
            )
    return state


def advance(
    definition: Definition, state: SimulationState, elapsed: int
) -> SimulationState:
    require_integer(elapsed, "elapsed")
    if elapsed < state.elapsed:
        raise DomainError("Simulation time cannot move backwards")
    if state.status == "completed":
        return state
    target = min(elapsed, state.duration)
    while due := sorted(
        (c for c in state.consequences if c.at_seconds <= target),
        key=lambda c: (c.at_seconds, c.id),
    ):
        event = due[0]
        state = replace(
            state,
            elapsed=event.at_seconds,
            consequences=tuple(c for c in state.consequences if c.id != event.id),
        )
        state = _fire(definition, state, event)
    state = replace(state, elapsed=target)
    if target == state.duration:
        state = replace(state, status="completed")
        unresolved = sum(i.status != "resolved" for i in state.incidents)
        state = _log(
            state,
            "completed",
            None,
            "Смена завершена",
            f"Незавершённых обращений: {unresolved}. Итог рассчитан по журналу.",
        )
    return state


def _action(
    identity: str,
    label: str,
    description: str,
    reason: str | None = None,
    incident: str | None = None,
    zone: str | None = None,
) -> dict[str, Any]:
    return dict(
        id=identity,
        label=label,
        description=description,
        enabled=reason is None,
        reason=reason,
        zone_id=zone,
        incident_id=incident,
    )


def available_actions(
    definition: Definition, state: SimulationState, incident_id: str | None = None
) -> list[dict[str, Any]]:
    if state.status != "active":
        return []
    if incident_id is None:
        actions = [
            _action(
                "move",
                f"Перейти: {z.title}",
                "Осмотреть контекст выбранной зоны.",
                "Вы уже здесь" if z.id == state.location else None,
                zone=z.id,
            )
            for z in definition.zones
        ]
        actions += [
            _action(
                f"take:{e.id}",
                f"Взять: {e.title}",
                "Оборудование останется при вас.",
                None if e.zone_id == state.location else "Перейдите к месту хранения",
                zone=e.zone_id,
            )
            for e in state.equipment
            if e.available and not e.carried
        ]
        return actions
    i = next((i for i in state.incidents if i.id == incident_id), None)
    if i is None or i.status == "scheduled":
        return []
    spec = _spec(definition, incident_id)
    nearby = None if state.location == i.zone_id else "Перейдите в зону обращения"
    actions = []

    def add(key: str, label: str, description: str, reason: str | None = None) -> None:
        actions.append(
            _action(key, label, description, nearby or reason, i.id, i.zone_id)
        )

    if i.status != "resolved":
        if not i.facts:
            add(
                "inspect",
                "Осмотреть",
                "Получить наблюдаемые факты без предположений о причине.",
            )
        if not i.talked:
            add("talk", "Поговорить", "Уточнить потребность и объяснить следующий шаг.")
        if not i.confirmed:
            add(
                "verify",
                "Проверить сведения",
                "Сопоставить наблюдения и подтвердить порядок действий.",
                None if i.facts else "Сначала получите наблюдаемые факты",
            )
        if spec.communication_type and not any(
            c.incident_id == i.id for c in state.communications
        ):
            carried_radio = any(e.id == "radio" and e.carried for e in state.equipment)
            add(
                "contact",
                "Запросить помощь",
                "Передать наблюдения ответственной службе и дождаться ответа.",
                "Нужны наблюдения"
                if not i.facts
                else None
                if carried_radio
                else "Возьмите средство связи",
            )
        if i.kind == "safety" and not i.contained:
            add(
                "restrict",
                "Ограничить доступ",
                "Обозначить опасную зону до прибытия ответственных.",
                None if i.facts else "Сначала осмотрите зону",
            )
        if i.status != "handled":
            reason = None
            if not i.confirmed:
                reason = "Сначала проверьте сведения"
            elif spec.equipment_id and not any(
                e.id == spec.equipment_id and e.carried for e in state.equipment
            ):
                reason = "Возьмите необходимое оборудование: " + next(
                    e.title for e in state.equipment if e.id == spec.equipment_id
                )
            elif spec.communication_type and not any(
                c.incident_id == i.id and c.status == "answered"
                for c in state.communications
            ):
                reason = "Дождитесь ответа ответственной службы"
            elif spec.station_id:
                station = next(
                    s for s in definition.stations if s.id == spec.station_id
                )
                if (
                    not station.arrival_seconds
                    <= state.elapsed
                    < station.departure_seconds
                ):
                    reason = "Действие доступно только во время стоянки"
            add(
                "assist",
                "Выполнить служебное действие",
                "Помочь в пределах учебной процедуры; проверить результат позже.",
                reason,
            )
        if not i.deferred and i.status != "handled":
            add(
                "defer",
                "Отложить задачу",
                "Вернуться позже. Время и развитие ситуации не останавливаются.",
            )
    elif not i.recorded:
        add(
            "record",
            "Оформить событие",
            "Сохранить подтверждённый результат в служебной записи.",
        )
    return actions


def act(
    definition: Definition,
    state: SimulationState,
    *,
    action_id: str,
    incident_id: str | None = None,
    zone_id: str | None = None,
) -> SimulationState:
    if len(state.commands) >= MAX_COMMANDS:
        raise DomainError("Simulation command limit reached")
    candidates = available_actions(definition, state, incident_id)
    selected = next(
        (
            a
            for a in candidates
            if a["id"] == action_id and (action_id != "move" or a["zone_id"] == zone_id)
        ),
        None,
    )
    if selected is None or not selected["enabled"]:
        raise DomainError(selected["reason"] if selected else "Action unavailable")
    if incident_id is not None and zone_id not in {None, selected["zone_id"]}:
        raise DomainError("Action zone mismatch")
    if action_id == "move":
        state = replace(state, location=str(selected["zone_id"]))
        state = _log(
            state, "movement", None, selected["label"], "Проводник сменил рабочую зону."
        )
    elif action_id.startswith("take:"):
        state = replace(
            state,
            equipment=tuple(
                replace(e, carried=True) if e.id == action_id[5:] else e
                for e in state.equipment
            ),
        )
        state = _log(
            state,
            "equipment",
            None,
            selected["label"],
            "Оборудование доступно для служебных действий.",
        )
    else:
        assert incident_id is not None
        i = _incident(state, incident_id)
        spec = _spec(definition, incident_id)
        if i.first_reaction_seconds is None:
            i = replace(i, first_reaction_seconds=state.elapsed)
        deltas: dict[str, int] = {}
        explanation = selected["description"]
        if action_id == "inspect":
            i = replace(
                i,
                status="investigated",
                facts=(spec.facts[0],),
                discovered_at_seconds=state.elapsed,
            )
            deltas = {"regulation": 2, "prioritization": 3 if i.severity == 1 else 0}
            explanation = "Наблюдение: " + spec.facts[0]
        elif action_id == "talk":
            i = replace(i, talked=True)
            state = _npc(state, i, "talk", trust=15, stress=-10)
            deltas = {"communication": 4, "service": 2}
        elif action_id == "verify":
            i = replace(
                i, confirmed=True, status="confirmed", facts=spec.facts + (i.cause,)
            )
            deltas = {"regulation": 3}
            explanation = "Проверенные сведения: " + " ".join(spec.facts[1:])
        elif action_id == "contact":
            passenger = next(p for p in state.passengers if p.id == i.passenger_id)
            delay = 30 if passenger.trust >= 50 else 50
            if spec.communication_type == "chief":
                delay += 80
                i = replace(i, contained=True)
            if spec.communication_type == "technical" and i.confirmed:
                delay += 20 if "локальный нагрев" in i.cause else 5
            request = CommunicationRequest(
                f"request:{i.id}",
                i.id,
                str(spec.communication_type),
                {
                    "technical": "Техническая помощь",
                    "medical": "Медицинская помощь",
                    "chief": "Начальник поезда",
                }.get(str(spec.communication_type), "Служебная связь"),
                "pending",
                state.elapsed,
                state.elapsed + delay,
            )
            response = (
                "Ответственный принял наблюдения. Сохраняйте безопасные условия, "
                "подготовьте оборудование и передайте ситуацию специалисту."
            )
            state = replace(
                state,
                communications=state.communications + (request,),
                consequences=state.consequences
                + (
                    Consequence(
                        f"response:{request.id}",
                        request.expected_response_seconds,
                        "response",
                        i.id,
                        response,
                    ),
                ),
            )
            deltas = {"communication": 3, "regulation": 2}
        elif action_id == "restrict":
            i = replace(i, contained=True)
            deltas = {"safety": 5, "prioritization": 4}
            explanation = (
                "Доступ ограничен по наблюдаемым признакам: "
                "дальнейшая эскалация остановлена до помощи."
            )
        elif action_id == "assist":
            i = replace(i, status="handled", contained=True)
            deltas = {"safety": 5, "service": 4, "prioritization": 3}
            state = replace(
                state,
                consequences=state.consequences
                + (
                    Consequence(
                        f"resolve:{i.id}",
                        state.elapsed + 20,
                        "resolve",
                        i.id,
                        "После служебного действия выполнен контроль: обращение "
                        "завершено, пассажир получил результат.",
                    ),
                ),
            )
        elif action_id == "defer":
            i = replace(i, deferred=True)
            state = _npc(state, i, "defer", trust=-15, stress=10)
            state = replace(
                state,
                consequences=state.consequences
                + (
                    Consequence(
                        f"complaint:{i.id}", state.elapsed + 45, "complaint", i.id, ""
                    ),
                ),
            )
            deltas = {"prioritization": -2}
        elif action_id == "record":
            i = replace(i, recorded=True)
            deltas = {"regulation": 3}
        state = _put(state, i)
        state = _log(state, "action", i.id, selected["label"], explanation, **deltas)
    return replace(
        state,
        commands=state.commands
        + (AppliedAction(state.elapsed, action_id, incident_id, zone_id),),
    )


def xp_for(state: SimulationState) -> int:
    if state.status != "completed" or not any(
        command.incident_id is not None and command.action_id != "defer"
        for command in state.commands
    ):
        return 0
    resolved = sum(i.status == "resolved" for i in state.incidents)
    return min(130, 20 + resolved * 15 + sum(v for _, v in state.metrics) // 25)


def view(definition: Definition, state: SimulationState) -> dict[str, Any]:
    reactions = [
        i.first_reaction_seconds - i.reported_at_seconds
        for i in state.incidents
        if i.first_reaction_seconds is not None
    ]
    return dict(
        title=definition.title,
        status=state.status,
        revision=state.revision,
        elapsed_seconds=state.elapsed,
        duration_seconds=state.duration,
        location=state.location,
        zones=[asdict(z) for z in definition.zones],
        stations=[
            dict(
                **asdict(s),
                status="upcoming"
                if state.elapsed < s.arrival_seconds
                else "dwell"
                if state.elapsed < s.departure_seconds
                else "passed",
            )
            for s in definition.stations
        ],
        passengers=[
            dict(
                id=p.id,
                name=p.name,
                zone_id=p.zone_id,
                observation="Пассажир встревожен"
                if p.stress >= 50
                else "Пассажир спокоен",
            )
            for p in state.passengers
        ],
        incidents=[
            dict(
                id=i.id,
                title=_spec(definition, i.id).title,
                kind=i.kind,
                zone_id=i.zone_id,
                passenger_id=i.passenger_id,
                status=i.status,
                severity=i.severity,
                reported_at_seconds=i.reported_at_seconds,
                discovered_at_seconds=i.discovered_at_seconds,
                first_reaction_seconds=i.first_reaction_seconds,
                observation=_spec(definition, i.id).observation,
                facts=list(i.facts),
                actions=available_actions(definition, state, i.id),
            )
            for i in state.incidents
            if i.status != "scheduled"
        ],
        actions=available_actions(definition, state),
        equipment=[asdict(e) for e in state.equipment],
        communications=[asdict(c) for c in state.communications],
        metrics=dict(
            state.metrics,
            average_reaction_seconds=sum(reactions) / len(reactions)
            if reactions
            else None,
        ),
        xp=xp_for(state),
        journal=[asdict(j) for j in state.journal],
    )


def debrief(definition: Definition, state: SimulationState) -> dict[str, Any]:
    if state.status != "completed":
        raise DomainError("Debrief is available after completion")
    metrics = dict(state.metrics)
    resolved = sum(i.status == "resolved" for i in state.incidents)
    recommendations = []
    if any(i.severity >= 3 for i in state.incidents):
        recommendations.append(
            "Сопоставляйте срочность параллельных обращений: эскалация продолжается "
            "вне выбранной зоны."
        )
    if any(not i.talked for i in state.incidents):
        recommendations.append(
            "Уточняйте потребность и объясняйте следующий шаг: доверие влияет на "
            "полноту передачи сведений."
        )
    if resolved < len(state.incidents):
        recommendations.append(
            "Планируйте оборудование и запросы помощи заранее, а станционные задачи — "
            "в пределах стоянки."
        )
    achievements = []
    if resolved == len(state.incidents):
        achievements.append("Все обращения доведены до результата")
    if metrics["safety"] >= 70 and all(i.severity < 3 for i in state.incidents):
        achievements.append("Безопасная операционная смена")
    if metrics["communication"] >= 70:
        achievements.append("Согласованная работа со службами")
    return dict(
        summary=(
            f"Завершено обращений: {resolved} из {len(state.incidents)}. "
            "Журнал отражает серверное время и фактические последствия."
        ),
        incidents=[
            dict(
                id=i.id,
                title=_spec(definition, i.id).title,
                outcome=i.status,
                reported_at_seconds=i.reported_at_seconds,
                discovered_at_seconds=i.discovered_at_seconds,
                first_reaction_seconds=i.first_reaction_seconds,
                resolved_at_seconds=i.resolved_at_seconds,
                alternatives=list(_spec(definition, i.id).alternatives),
            )
            for i in state.incidents
        ],
        recommendations=recommendations
        or ["Повторите смену с другим сочетанием пассажиров и событий."],
        achievements=achievements,
    )
