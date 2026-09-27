"""Strict independently versioned training content. Never alters v1 documents."""

import json
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from app.domain.simulation import Definition, Equipment, IncidentSpec, Station, Zone
from app.domain.training import TrainingDefinition
from app.simulation.schema import (
    EquipmentDocument,
    Identifier,
    IncidentDocument,
    StationDocument,
    Strict,
    Text,
    ZoneDocument,
)

DURATIONS = {"work": 1200, "tutorial": 180, "demo": 240, "practice": 180}
ACTIONS = {
    "move",
    "take",
    "inspect",
    "talk",
    "verify",
    "contact",
    "restrict",
    "assist",
    "record",
    "defer",
}
KINDS = {"service", "conflict", "safety", "health", "station"}


class ModeDocument(Strict):
    duration_seconds: Annotated[int, Field(ge=60, le=1200)]
    incident_seconds: dict[Identifier, Annotated[int, Field(ge=0, le=1199)]]
    stations: Annotated[list[StationDocument], Field(min_length=1, max_length=5)]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if not self.incident_seconds or min(self.incident_seconds.values()) != 0:
            raise ValueError("Mode needs an initial report")
        if any(v >= self.duration_seconds for v in self.incident_seconds.values()):
            raise ValueError("Report outside mode duration")
        windows = [(s.arrival_seconds, s.departure_seconds) for s in self.stations]
        if (
            len({s.id for s in self.stations}) != len(self.stations)
            or windows != sorted(windows)
            or any(a[1] > b[0] for a, b in zip(windows, windows[1:], strict=False))
            or any(b > self.duration_seconds for a, b in windows)
        ):
            raise ValueError("Invalid station windows")
        return self


class DefinitionDocument(Strict):
    schema_version: Literal[2]
    id: Identifier
    version: Annotated[int, Field(ge=1, le=2147483647)]
    title: Text
    zones: Annotated[list[ZoneDocument], Field(min_length=5, max_length=8)]
    stations: Annotated[list[StationDocument], Field(min_length=2, max_length=5)]
    equipment: Annotated[list[EquipmentDocument], Field(min_length=3, max_length=8)]
    incidents: Annotated[list[IncidentDocument], Field(min_length=5, max_length=8)]
    passenger_names: Annotated[list[Text], Field(min_length=5, max_length=12)]
    action_durations: dict[Identifier, Annotated[int, Field(ge=1, le=60)]]
    dialogues: dict[Identifier, Text]
    completion_effects: dict[
        Identifier,
        dict[
            Literal[
                "safety", "service", "regulation", "prioritization", "communication"
            ],
            Annotated[int, Field(ge=-20, le=20)],
        ],
    ]
    completion_explanations: dict[Identifier, Text]
    modes: dict[Identifier, ModeDocument]
    methodology_version: Identifier
    source_notice: Text

    @model_validator(mode="before")
    @classmethod
    def strict_version(cls, value: Any) -> Any:
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if set(self.modes) != set(DURATIONS) or any(
            self.modes[k].duration_seconds != v for k, v in DURATIONS.items()
        ):
            raise ValueError(
                "Expected independently scheduled work/tutorial/demo/practice modes"
            )
        if set(self.action_durations) != ACTIONS or set(self.dialogues) != KINDS:
            raise ValueError("Incomplete action durations or dialogue kinds")
        if (
            set(self.completion_effects) != KINDS
            or set(self.completion_explanations) != KINDS
        ):
            raise ValueError(
                "Expected completion effects and explanations for every kind"
            )
        for rows in (self.zones, self.stations, self.equipment, self.incidents):
            if len({r.id for r in rows}) != len(rows):
                raise ValueError("Duplicate content ID")
        zones = {z.id for z in self.zones}
        equipment = {e.id for e in self.equipment}
        if (
            "service" not in zones
            or "radio" not in equipment
            or any(e.zone_id not in zones for e in self.equipment)
        ):
            raise ValueError("Required zone/equipment missing")
        if {i.kind for i in self.incidents} != KINDS or self.incidents[
            0
        ].kind != "service":
            raise ValueError(
                "All incident kinds and initial service passenger required"
            )
        for i in self.incidents:
            if (
                not set(i.zones) <= zones
                or len(set(i.zones)) != len(i.zones)
                or (i.equipment_id and i.equipment_id not in equipment)
            ):
                raise ValueError("Invalid incident reference")
        for mode in self.modes.values():
            if mode.incident_seconds.get(self.incidents[0].id) != 0:
                raise ValueError("Every mode starts with its known service passenger")
            if not set(mode.incident_seconds) <= {i.id for i in self.incidents}:
                raise ValueError("Unknown mode incident")
            for i in self.incidents:
                if (
                    i.id in mode.incident_seconds
                    and i.station_id
                    and i.station_id not in {s.id for s in mode.stations}
                ):
                    raise ValueError("Mode station missing")
        return self

    def to_domain(
        self, mode: str, competency_id: str | None = None
    ) -> TrainingDefinition:
        if mode not in self.modes:
            raise ValueError("Unknown training mode")
        schedule = self.modes[mode]
        chosen = set(schedule.incident_seconds)
        if mode == "practice":
            kinds = {
                "safety": {"service", "safety"},
                "service": {"service"},
                "regulation": {"service", "station"},
                "prioritization": {"service", "safety", "health"},
                "communication": {"service", "conflict"},
            }
            if competency_id not in kinds:
                raise ValueError("Practice requires a known competency")
            chosen &= {i.id for i in self.incidents if i.kind in kinds[competency_id]}
        elif competency_id is not None:
            raise ValueError("Competency applies only to practice")
        incidents = tuple(
            IncidentSpec(
                **{
                    **i.model_dump(),
                    "reported_at_seconds": schedule.incident_seconds[i.id],
                    "zones": tuple(i.zones),
                    "facts": tuple(i.facts),
                    "causes": tuple(i.causes),
                    "alternatives": tuple(i.alternatives),
                }
            )
            for i in self.incidents
            if i.id in chosen
        )
        base = Definition(
            self.id,
            self.version,
            self.title,
            schedule.duration_seconds,
            tuple(Zone(**z.model_dump()) for z in self.zones),
            tuple(Station(**s.model_dump()) for s in schedule.stations),
            tuple(Equipment(**e.model_dump()) for e in self.equipment),
            incidents,
            tuple(self.passenger_names),
        )
        return TrainingDefinition(
            base,
            mode,
            competency_id,
            tuple(self.action_durations.items()),
            tuple(self.dialogues.items()),
            self.methodology_version,
            self.source_notice,
            tuple(
                (kind, tuple(effects.items()))
                for kind, effects in self.completion_effects.items()
            ),
            tuple(self.completion_explanations.items()),
        )


def load_definition(path: Path) -> DefinitionDocument:
    if path.stat().st_size > 128 * 1024:
        raise ValueError("Training definition exceeds 128 KiB")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON object key")
            result[key] = value
        return result

    return DefinitionDocument.model_validate(
        json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    )
