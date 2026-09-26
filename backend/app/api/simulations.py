from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import Database, User
from app.api.sessions import RequestId
from app.application.errors import UseCaseError
from app.application.simulations import SimulationService

router = APIRouter(prefix="/simulations", tags=["operational simulation"])


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ActionRequest(StartRequest):
    command_id: RequestId
    expected_revision: Annotated[int, Field(ge=0, le=2147483647)]
    action_id: RequestId
    incident_id: RequestId | None
    zone_id: RequestId | None


class ActionView(BaseModel):
    id: str
    label: str
    description: str
    enabled: bool
    reason: str | None
    zone_id: str | None
    incident_id: str | None


class ZoneView(BaseModel):
    id: str
    title: str
    kind: str


class StationView(BaseModel):
    id: str
    title: str
    arrival_seconds: int
    departure_seconds: int
    status: Literal["upcoming", "dwell", "passed"]


class PassengerView(BaseModel):
    id: str
    name: str
    zone_id: str
    observation: str


class IncidentView(BaseModel):
    id: str
    title: str
    kind: str
    zone_id: str
    passenger_id: str | None
    status: str
    severity: int
    reported_at_seconds: int
    discovered_at_seconds: int | None
    first_reaction_seconds: int | None
    observation: str
    facts: list[str]
    actions: list[ActionView]


class EquipmentView(BaseModel):
    id: str
    title: str
    zone_id: str
    carried: bool
    available: bool


class CommunicationView(BaseModel):
    id: str
    incident_id: str
    type: str
    title: str
    status: Literal["pending", "answered"]
    requested_at_seconds: int
    expected_response_seconds: int
    result: str | None


class MetricsView(BaseModel):
    safety: int
    service: int
    regulation: int
    prioritization: int
    communication: int
    average_reaction_seconds: float | None


class MetricChangeView(BaseModel):
    metric: str
    delta: int
    before: int
    after: int


class JournalView(BaseModel):
    id: str
    at_seconds: int
    kind: str
    incident_id: str | None
    title: str
    explanation: str
    metric_changes: list[MetricChangeView]


class SimulationView(BaseModel):
    id: str
    title: str
    status: Literal["active", "completed"]
    revision: int
    server_time: datetime
    started_at: datetime
    elapsed_seconds: int
    duration_seconds: int
    location: str
    zones: list[ZoneView]
    stations: list[StationView]
    passengers: list[PassengerView]
    incidents: list[IncidentView]
    actions: list[ActionView]
    equipment: list[EquipmentView]
    communications: list[CommunicationView]
    metrics: MetricsView
    xp: int
    journal: list[JournalView]


class CurrentView(BaseModel):
    simulation: SimulationView | None


class IncidentDebrief(BaseModel):
    id: str
    title: str
    outcome: str
    reported_at_seconds: int
    discovered_at_seconds: int | None
    first_reaction_seconds: int | None
    resolved_at_seconds: int | None
    alternatives: list[str]


class SimulationDebrief(BaseModel):
    simulation: SimulationView
    summary: str
    incidents: list[IncidentDebrief]
    recommendations: list[str]
    achievements: list[str]


def get_simulation_service(engine: Database) -> SimulationService:
    return SimulationService(engine)


Service = Annotated[SimulationService, Depends(get_simulation_service)]


@router.post("", response_model=SimulationView, status_code=201)
def create(
    body: StartRequest,
    response: Response,
    request: Request,
    user: User,
    service: Service,
    idempotency_key: Annotated[RequestId, Header(alias="Idempotency-Key")],
) -> SimulationView:
    request.state.command_audit_metadata = {"client_event_id": idempotency_key}
    result, duplicate = service.start(user.id, key=idempotency_key)
    response.status_code = 200 if duplicate else 201
    return SimulationView.model_validate(result)


@router.get("/current", response_model=CurrentView)
def current(user: User, service: Service) -> CurrentView:
    value = service.current(user.id)
    return CurrentView(
        simulation=SimulationView.model_validate(value) if value else None
    )


@router.get("/{simulation_id}", response_model=SimulationView)
def get(simulation_id: RequestId, user: User, service: Service) -> SimulationView:
    return SimulationView.model_validate(service.get(simulation_id, user.id))


@router.post("/{simulation_id}/actions", response_model=SimulationView)
def action(
    simulation_id: RequestId,
    body: ActionRequest,
    request: Request,
    user: User,
    service: Service,
    idempotency_key: Annotated[RequestId, Header(alias="Idempotency-Key")],
) -> SimulationView:
    request.state.command_audit_metadata = {
        "client_event_id": body.command_id,
        "simulation_id": simulation_id,
    }
    if body.command_id != idempotency_key:
        raise UseCaseError(
            "idempotency_conflict", "Header and body command IDs must match"
        )
    return SimulationView.model_validate(
        service.action(simulation_id, user.id, **body.model_dump())
    )


@router.get("/{simulation_id}/debrief", response_model=SimulationDebrief)
def report(simulation_id: RequestId, user: User, service: Service) -> SimulationDebrief:
    return SimulationDebrief.model_validate(service.debrief(simulation_id, user.id))
