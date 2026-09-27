"""Pure duration-aware engine 2, wrapping frozen v1 effects and world events."""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Any

from . import simulation as v1
from .common import DomainError, require_integer

MAX_COMMANDS = 1000
COMPETENCIES = {
    "safety": "Безопасность",
    "service": "Сервис",
    "regulation": "Проверка и оформление",
    "prioritization": "Приоритеты",
    "communication": "Коммуникация",
}


@dataclass(frozen=True, slots=True)
class TrainingDefinition:
    base: v1.Definition
    mode: str
    competency_id: str | None
    durations: tuple[tuple[str, int], ...]
    dialogues: tuple[tuple[str, str], ...]
    methodology_version: str
    source_notice: str
    completion_effects: tuple[tuple[str, tuple[tuple[str, int], ...]], ...]
    completion_explanations: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class PendingAction:
    id: str
    action_id: str
    label: str
    incident_id: str | None
    zone_id: str | None
    started_at_seconds: int
    completes_at_seconds: int
    interruptible: bool = True


@dataclass(frozen=True, slots=True)
class TrainingState:
    world: v1.SimulationState
    pending: PendingAction | None = None
    commands: tuple[v1.AppliedAction, ...] = ()

    @property
    def duration(self) -> int:
        return self.world.duration

    @property
    def elapsed(self) -> int:
        return self.world.elapsed

    @property
    def revision(self) -> int:
        return self.world.revision

    @property
    def status(self) -> str:
        return self.world.status

    @property
    def metrics(self) -> tuple[tuple[str, int], ...]:
        return self.world.metrics

    @property
    def journal(self) -> tuple[v1.JournalEntry, ...]:
        return self.world.journal


def start(definition: TrainingDefinition, seed: int) -> TrainingState:
    world = v1.start(definition.base, seed)
    # Engine 2 schedules reports exactly as authored. Seed varies people and causes.
    times = {i.id: i.reported_at_seconds for i in definition.base.incidents}
    offsets = {i.id: times[i.id] - i.reported_at_seconds for i in world.incidents}
    world = replace(
        world,
        incidents=tuple(
            replace(i, reported_at_seconds=times[i.id]) for i in world.incidents
        ),
        consequences=tuple(
            replace(c, at_seconds=c.at_seconds + offsets.get(c.incident_id or "", 0))
            for c in world.consequences
        ),
    )
    return TrainingState(v1.advance(definition.base, world, 0))


def duration(definition: TrainingDefinition, action: str) -> int:
    return dict(definition.durations).get(action.split(":")[0], 0)


def _label(kind: str, action: str, fallback: str) -> str:
    labels = {
        "service": {
            "inspect": "Осмотреть проход и багаж",
            "talk": "Уточнить просьбу пассажира",
            "verify": "Сверить маршрут и потребность",
            "assist": "Помочь с размещением и маршрутом",
            "record": "Записать результат помощи",
        },
        "conflict": {
            "inspect": "Оценить обстановку спора",
            "talk": "Выслушать обе стороны",
            "verify": "Уточнить потребности сторон",
            "assist": "Согласовать спокойное решение",
            "record": "Оформить урегулирование",
        },
        "safety": {
            "inspect": "Осмотреть зону с безопасной дистанции",
            "talk": "Уточнить наблюдения пассажира",
            "verify": "Проверить наблюдаемые признаки",
            "contact": "Передать наблюдения технической службе",
            "restrict": "Обозначить опасную зону",
            "assist": "Передать зону техническому специалисту",
            "record": "Оформить контроль безопасности",
        },
        "health": {
            "inspect": "Оценить наблюдаемое самочувствие",
            "talk": "Выслушать жалобы без диагноза",
            "verify": "Уточнить сведения для специалиста",
            "contact": "Вызвать медицинскую помощь",
            "assist": "Подготовить помощь и передать специалисту",
            "record": "Записать передачу медицинской службе",
        },
        "station": {
            "inspect": "Проверить место встречи",
            "talk": "Уточнить план передачи",
            "verify": "Сверить документы до прибытия",
            "assist": "Передать обращение встречающему сотруднику",
            "record": "Оформить станционную передачу",
        },
    }
    return labels.get(kind, {}).get(action, fallback)


def available_actions(
    definition: TrainingDefinition, state: TrainingState, incident_id: str | None = None
) -> list[dict[str, Any]]:
    actions = v1.available_actions(definition.base, state.world, incident_id)
    kind = next((i.kind for i in state.world.incidents if i.id == incident_id), "")
    for action in actions:
        action["duration_seconds"] = duration(definition, action["id"])
        action["label"] = _label(kind, action["id"], action["label"])
        if state.pending:
            action.update(
                enabled=False, reason="Завершите или прервите текущее действие"
            )
        elif state.world.elapsed + action["duration_seconds"] > state.world.duration:
            action.update(enabled=False, reason="До конца режима недостаточно времени")
        elif action["id"] == "assist" and incident_id:
            spec = v1._spec(definition.base, incident_id)
            if spec.station_id:
                station = next(
                    s for s in definition.base.stations if s.id == spec.station_id
                )
                if (
                    state.world.elapsed + action["duration_seconds"]
                    >= station.departure_seconds
                ):
                    action.update(
                        enabled=False,
                        reason="До отправления недостаточно времени для передачи",
                    )
    if state.pending and incident_id is None:
        actions.append(
            dict(
                id="cancel",
                label="Прервать действие",
                description="Затраченное время потеряно; частичного эффекта нет.",
                enabled=True,
                reason=None,
                zone_id=None,
                incident_id=None,
                duration_seconds=0,
            )
        )
    return actions


def act(
    definition: TrainingDefinition,
    state: TrainingState,
    *,
    action_id: str,
    incident_id: str | None = None,
    zone_id: str | None = None,
) -> TrainingState:
    if state.world.status != "active":
        raise DomainError("Training run is completed")
    if len(state.commands) >= MAX_COMMANDS:
        raise DomainError("Training command limit reached")
    if action_id == "cancel":
        if state.pending is None or incident_id is not None or zone_id is not None:
            raise DomainError("No matching action to cancel")
        pending = state.pending
        world = v1._log(
            state.world,
            "action_cancelled",
            pending.incident_id,
            "Действие прервано: " + pending.label,
            f"Потрачено {state.world.elapsed - pending.started_at_seconds} с. "
            "Частичный эффект отсутствует; мир продолжает развиваться.",
        )
        state = replace(state, world=world, pending=None)
    else:
        selected = next(
            (
                a
                for a in available_actions(definition, state, incident_id)
                if a["id"] == action_id
                and (action_id != "move" or a["zone_id"] == zone_id)
            ),
            None,
        )
        if selected is None or not selected["enabled"]:
            raise DomainError(selected["reason"] if selected else "Action unavailable")
        if incident_id is not None and zone_id not in {None, selected["zone_id"]}:
            raise DomainError("Action zone mismatch")
        if (
            incident_id is None
            and action_id != "move"
            and zone_id not in {None, selected["zone_id"]}
        ):
            raise DomainError("Action zone mismatch")
        world = v1._log(
            state.world,
            "action_started",
            incident_id,
            "Начато: " + selected["label"],
            f"Длительность {selected['duration_seconds']} с. "
            "Последствия наступят после завершения.",
        )
        pending = PendingAction(
            f"pending-{world.revision}",
            action_id,
            selected["label"],
            incident_id,
            zone_id,
            state.world.elapsed,
            state.world.elapsed + selected["duration_seconds"],
        )
        state = replace(state, world=world, pending=pending)
    return replace(
        state,
        commands=state.commands
        + (v1.AppliedAction(state.world.elapsed, action_id, incident_id, zone_id),),
    )


def advance(
    definition: TrainingDefinition, state: TrainingState, elapsed: int
) -> TrainingState:
    require_integer(elapsed, "elapsed")
    if elapsed < state.world.elapsed:
        raise DomainError("Training time cannot move backwards")
    if state.world.status == "completed":
        return state
    target = min(elapsed, state.world.duration)
    if state.pending and state.pending.completes_at_seconds <= target:
        pending = state.pending
        # Let effects finishing on the deadline precede shift completion.
        world = v1.advance(
            definition.base,
            replace(state.world, duration=state.world.duration + 1),
            pending.completes_at_seconds,
        )
        world = replace(world, duration=state.world.duration)
        try:
            world = v1.act(
                definition.base,
                world,
                action_id=pending.action_id,
                incident_id=pending.incident_id,
                zone_id=pending.zone_id,
            )
            entry = world.journal[-1]
            explanation = entry.explanation
            if pending.action_id == "talk" and pending.incident_id:
                kind = v1._incident(world, pending.incident_id).kind
                explanation = dict(definition.dialogues)[kind]
            world = replace(
                world,
                journal=world.journal[:-1]
                + (replace(entry, title=pending.label, explanation=explanation),),
            )
            if pending.action_id == "assist" and pending.incident_id:
                kind = v1._incident(world, pending.incident_id).kind
                world = v1._log(
                    world,
                    "authored_consequence",
                    pending.incident_id,
                    "Учебное последствие помощи",
                    dict(definition.completion_explanations)[kind],
                    **dict(dict(definition.completion_effects)[kind]),
                )
        except DomainError:
            world = v1._log(
                world,
                "action_interrupted",
                pending.incident_id,
                "Не завершено: " + pending.label,
                (
                    "Условия изменились во время выполнения. Время потрачено; "
                    "частичный эффект отсутствует."
                ),
            )
        state = replace(state, world=world, pending=None)
    world = v1.advance(definition.base, state.world, target)
    if world.status == "completed" and state.pending:
        world = v1._log(
            world,
            "action_interrupted",
            state.pending.incident_id,
            "Действие остановлено завершением режима",
            "Частичный эффект отсутствует.",
        )
        return replace(state, world=world, pending=None)
    return replace(state, world=world)


def replay(
    definition: TrainingDefinition, state: TrainingState, at_seconds: int
) -> TrainingState:
    require_integer(at_seconds, "at_seconds")
    if not 0 <= at_seconds <= state.world.elapsed:
        raise DomainError("Replay time outside known history")
    rebuilt = start(definition, state.world.seed)
    for command in state.commands:
        if command.at_seconds > at_seconds:
            break
        rebuilt = advance(definition, rebuilt, command.at_seconds)
        rebuilt = act(
            definition,
            rebuilt,
            action_id=command.action_id,
            incident_id=command.incident_id,
            zone_id=command.zone_id,
        )
    return advance(definition, rebuilt, at_seconds)


def dump(state: TrainingState) -> dict[str, Any]:
    return dict(
        engine_version=2, **json.loads(json.dumps(asdict(state), ensure_ascii=False))
    )


def restore(definition: TrainingDefinition, payload: dict[str, Any]) -> TrainingState:
    try:
        if (
            payload.get("engine_version") != 2
            or len(payload["commands"]) > MAX_COMMANDS
        ):
            raise DomainError("Invalid training snapshot version or command limit")
        state = start(definition, payload["world"]["seed"])
        for command in payload["commands"]:
            if set(command) != {"at_seconds", "action_id", "incident_id", "zone_id"}:
                raise DomainError("Invalid stored command")
            state = advance(definition, state, command["at_seconds"])
            state = act(
                definition,
                state,
                action_id=command["action_id"],
                incident_id=command["incident_id"],
                zone_id=command["zone_id"],
            )
        state = advance(definition, state, payload["world"]["elapsed"])
        if json.dumps(dump(state), sort_keys=True) != json.dumps(
            payload, sort_keys=True
        ):
            raise DomainError("Training snapshot differs from verified replay")
        return state
    except (KeyError, TypeError, ValueError, StopIteration) as exc:
        raise DomainError("Invalid training snapshot") from exc


def view(definition: TrainingDefinition, state: TrainingState) -> dict[str, Any]:
    result = v1.view(definition.base, state.world)
    result.update(
        engine_version=2,
        mode=definition.mode,
        pending_action=asdict(state.pending) if state.pending else None,
        actions=available_actions(definition, state),
    )
    known = {i["passenger_id"] for i in result["incidents"]}
    result["passengers"] = [p for p in result["passengers"] if p["id"] in known]
    for incident in result["incidents"]:
        actual = v1._incident(state.world, incident["id"])
        incident["facts"] = [f for f in incident["facts"] if f != actual.cause]
        incident["actions"] = available_actions(definition, state, actual.id)
        if actual.talked:
            incident["observation"] += " " + dict(definition.dialogues)[actual.kind]
    return result


def assessment(definition: TrainingDefinition, state: TrainingState) -> dict[str, Any]:
    criteria = []
    for competency, title in COMPETENCIES.items():
        relevant = [
            i
            for i in state.world.incidents
            if i.status != "scheduled"
            and (competency != "safety" or i.kind in {"safety", "health"})
        ]
        observed: dict[str, Callable[[v1.IncidentState], bool]] = {
            "safety": lambda i: i.contained or i.status == "resolved",
            "service": lambda i: i.status == "resolved",
            "regulation": lambda i: i.confirmed and i.recorded,
            "prioritization": lambda i: any(
                c.incident_id == i.id
                and c.action_id != "defer"
                and c.at_seconds - i.reported_at_seconds <= 60
                for c in state.world.commands
            ),
            "communication": lambda i: i.talked,
        }
        met = all(observed[competency](i) for i in relevant) if relevant else None
        descriptions = {
            "safety": "Опасные и медицинские обращения переданы под контроль.",
            "service": "По каждому известному обращению подтверждён результат.",
            "regulation": "Наблюдения проверены и результат оформлен.",
            "prioritization": (
                "Первое завершённое действие выполнено не позднее "
                "60 секунд после сообщения."
            ),
            "communication": (
                "Потребность уточнена разговором по каждому известному обращению."
            ),
        }
        satisfied = sum(observed[competency](i) for i in relevant)
        missing = [
            v1._spec(definition.base, i.id).title
            for i in relevant
            if not observed[competency](i)
        ]
        explanation = "Критерий: " + descriptions[competency]
        if met is None:
            explanation += " Не было измеримых ситуаций."
        else:
            explanation += f" Выполнено {satisfied} из {len(relevant)}."
            if missing:
                explanation += " Не подтверждено: " + "; ".join(missing) + "."
            explanation += " Основание: указанные события журнала."
        ids = {i.id for i in relevant}
        events = [
            e.id
            for e in state.world.journal
            if e.incident_id in ids
            and e.kind not in {"action_started", "action_cancelled"}
        ]
        criteria.append(
            dict(
                id="demo-" + competency,
                competency_id=competency,
                title=title,
                met=met,
                explanation=explanation,
                source=definition.base.id,
                source_version=str(definition.base.version),
                evidence_event_ids=events,
            )
        )
    return dict(
        criteria=criteria,
        methodology_version=definition.methodology_version,
        source_notice=definition.source_notice,
    )


def debrief(definition: TrainingDefinition, state: TrainingState) -> dict[str, Any]:
    return dict(
        v1.debrief(definition.base, state.world),
        assessment=assessment(definition, state),
    )


def xp_for(state: TrainingState) -> int:
    return v1.xp_for(state.world)
