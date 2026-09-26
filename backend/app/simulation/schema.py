"""Strict authored content boundary, separate from the simulation domain."""

import json
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.domain.simulation import Definition, Equipment, IncidentSpec, Station, Zone

Identifier = Annotated[
    str, StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
]
Text = Annotated[str, StringConstraints(min_length=1, max_length=1500)]
Second = Annotated[int, Field(ge=0, le=1200)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ZoneDocument(Strict):
    id: Identifier
    title: Text
    kind: Literal["vestibule", "corridor", "passenger", "service", "sanitary"]


class StationDocument(Strict):
    id: Identifier
    title: Text
    arrival_seconds: Second
    departure_seconds: Second

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.departure_seconds <= self.arrival_seconds:
            raise ValueError("Station departure must follow arrival")
        return self


class EquipmentDocument(Strict):
    id: Identifier
    title: Text
    zone_id: Identifier


class IncidentDocument(Strict):
    id: Identifier
    kind: Literal["service", "conflict", "safety", "health", "station"]
    title: Text
    zones: Annotated[list[Identifier], Field(min_length=1, max_length=6)]
    reported_at_seconds: Annotated[int, Field(ge=0, le=850)]
    observation: Text
    facts: Annotated[list[Text], Field(min_length=2, max_length=5)]
    causes: Annotated[list[Text], Field(min_length=2, max_length=4)]
    alternatives: Annotated[list[Text], Field(min_length=2, max_length=5)]
    equipment_id: Identifier | None = None
    communication_type: Literal["chief", "technical", "medical"] | None = None
    station_id: Identifier | None = None


class DefinitionDocument(Strict):
    schema_version: Literal[1]
    id: Identifier
    version: Annotated[int, Field(ge=1, le=2147483647)]
    title: Text
    duration_seconds: Literal[1200]
    zones: Annotated[list[ZoneDocument], Field(min_length=5, max_length=8)]
    stations: Annotated[list[StationDocument], Field(min_length=2, max_length=5)]
    equipment: Annotated[list[EquipmentDocument], Field(min_length=3, max_length=8)]
    incidents: Annotated[list[IncidentDocument], Field(min_length=5, max_length=8)]
    passenger_names: Annotated[list[Text], Field(min_length=5, max_length=12)]

    @model_validator(mode="before")
    @classmethod
    def strict_schema_version(cls, value: Any) -> Any:
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @model_validator(mode="after")
    def consistent(self) -> Self:
        for rows in (self.zones, self.stations, self.equipment, self.incidents):
            if len({r.id for r in rows}) != len(rows):
                raise ValueError("Duplicate content identifier")
        zones = {z.id for z in self.zones}
        equipment = {e.id for e in self.equipment}
        stations = {s.id for s in self.stations}
        if "service" not in zones or "radio" not in equipment:
            raise ValueError("Service zone and radio are required")
        if {i.kind for i in self.incidents} != {
            "service",
            "conflict",
            "safety",
            "health",
            "station",
        }:
            raise ValueError("The demo must cover every operational incident kind")
        if any(e.zone_id not in zones for e in self.equipment):
            raise ValueError("Equipment zone reference missing")
        for incident in self.incidents:
            if not set(incident.zones) <= zones or len(set(incident.zones)) != len(
                incident.zones
            ):
                raise ValueError("Incident zone reference invalid")
            if incident.equipment_id and incident.equipment_id not in equipment:
                raise ValueError("Incident equipment missing")
            if incident.station_id and incident.station_id not in stations:
                raise ValueError("Incident station missing")
            if incident.kind == "station" and incident.station_id is None:
                raise ValueError("Station task needs a station")
        times = [(s.arrival_seconds, s.departure_seconds) for s in self.stations]
        if times != sorted(times) or any(
            a[1] > b[0] for a, b in zip(times, times[1:], strict=False)
        ):
            raise ValueError("Station windows overlap or are unordered")
        if (
            self.incidents[0].reported_at_seconds != 0
            or self.incidents[0].kind != "service"
        ):
            raise ValueError("Initial report must start the shift")
        return self

    def to_domain(self) -> Definition:
        return Definition(
            self.id,
            self.version,
            self.title,
            self.duration_seconds,
            tuple(Zone(**v.model_dump()) for v in self.zones),
            tuple(Station(**v.model_dump()) for v in self.stations),
            tuple(Equipment(**v.model_dump()) for v in self.equipment),
            tuple(
                IncidentSpec(
                    **{
                        **v.model_dump(),
                        "zones": tuple(v.zones),
                        "facts": tuple(v.facts),
                        "causes": tuple(v.causes),
                        "alternatives": tuple(v.alternatives),
                    }
                )
                for v in self.incidents
            ),
            tuple(self.passenger_names),
        )


def load_definition(path: Path) -> DefinitionDocument:
    if path.stat().st_size > 128 * 1024:
        raise ValueError("Simulation definition exceeds 128 KiB")

    def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate JSON object key")
            value[key] = item
        return value

    return DefinitionDocument.model_validate(
        json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)
    )
