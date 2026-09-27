"""Authenticated engine-2 intent API; server chooses clocks, content and rewards."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, Field

from app.api.dependencies import Database, User
from app.api.sessions import RequestId
from app.api.simulations import (
    ActionRequest,
    ActionView,
    CommunicationView,
    EquipmentView,
    IncidentDebrief,
    JournalView,
    MetricsView,
    PassengerView,
    StationView,
    ZoneView,
)
from app.api.simulations import StartRequest as StrictRequest
from app.application.errors import UseCaseError
from app.application.training import TrainingService

router = APIRouter(prefix="/training", tags=["training engine 2"])
Mode = Literal["work", "tutorial", "demo", "practice"]


class StartRequest(StrictRequest):
    mode: Mode
    competency_id: RequestId | None = None
    assignment_id: RequestId | None = None


class ForkRequest(StrictRequest):
    at_seconds: Annotated[int, Field(ge=0, le=1199)]


class TimedAction(ActionView):
    duration_seconds: int


class TrainingIncident(BaseModel):
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
    actions: list[TimedAction]


class PendingAction(BaseModel):
    id: str
    action_id: str
    label: str
    incident_id: str | None
    zone_id: str | None
    started_at_seconds: int
    completes_at_seconds: int
    interruptible: bool


class TrainingView(BaseModel):
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
    incidents: list[TrainingIncident]
    actions: list[TimedAction]
    equipment: list[EquipmentView]
    communications: list[CommunicationView]
    metrics: MetricsView
    xp: int
    journal: list[JournalView]

    engine_version: Literal[2]
    mode: Mode
    source_id: str | None
    assignment_id: str | None
    reward_eligible: bool
    pending_action: PendingAction | None
    replay: bool


class CurrentView(BaseModel):
    simulation: TrainingView | None


class Evidence(BaseModel):
    id: str
    competency_id: str
    title: str
    met: bool | None
    explanation: str
    source: str
    source_version: str
    evidence_event_ids: list[str]


class Assessment(BaseModel):
    criteria: list[Evidence]
    methodology_version: str
    source_notice: str


class TrainingDebrief(BaseModel):
    simulation: TrainingView
    summary: str
    incidents: list[IncidentDebrief]
    recommendations: list[str]
    achievements: list[str]
    assessment: Assessment


class HistoryItem(BaseModel):
    id: str
    title: str
    mode: Mode
    status: Literal["active", "completed"]
    started_at: datetime
    source_id: str | None


class History(BaseModel):
    items: list[HistoryItem]
    total: int
    limit: int
    offset: int


class Competency(BaseModel):
    id: str
    title: str
    score: float | None
    evidence_count: int
    status: Literal["insufficient", "developing", "steady"]
    strong: bool


class Pattern(BaseModel):
    id: str
    title: str
    count: int
    explanation: str


class Recommendation(BaseModel):
    competency_id: str
    title: str
    explanation: str


class Statistics(BaseModel):
    completed_runs: int
    completed_scenarios: int


class Learning(BaseModel):
    competencies: list[Competency]
    patterns: list[Pattern]
    recommendations: list[Recommendation]
    statistics: Statistics
    source_notice: str


class Difference(BaseModel):
    metric: str
    delta: int


class Comparison(BaseModel):
    source_id: str
    source_metrics: MetricsView
    current_metrics: MetricsView
    differences: list[Difference]
    source_resolved: int
    current_resolved: int


def get_training_service(engine: Database) -> TrainingService:
    return TrainingService(engine)


Service = Annotated[TrainingService, Depends(get_training_service)]
Key = Annotated[RequestId, Header(alias="Idempotency-Key")]


@router.get("/current", response_model=CurrentView)
def current(user: User, service: Service) -> CurrentView:
    result = service.current(user.id)
    return CurrentView(
        simulation=TrainingView.model_validate(result) if result else None
    )


@router.post("/runs", response_model=TrainingView, status_code=201)
def create(
    body: StartRequest,
    response: Response,
    request: Request,
    user: User,
    service: Service,
    idempotency_key: Key,
) -> TrainingView:
    request.state.command_audit_metadata = {"client_event_id": idempotency_key}
    result, duplicate = service.start(user.id, key=idempotency_key, **body.model_dump())
    response.status_code = 200 if duplicate else 201
    return TrainingView.model_validate(result)


@router.get("/learning", response_model=Learning)
def learning(user: User, service: Service) -> Learning:
    return Learning.model_validate(service.learning(user.id))


@router.get("/history", response_model=History)
def history(
    user: User,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> History:
    return History.model_validate(service.history(user.id, limit, offset))


@router.get("/runs/{run_id}", response_model=TrainingView)
def get(run_id: RequestId, user: User, service: Service) -> TrainingView:
    return TrainingView.model_validate(service.get(run_id, user.id))


@router.post("/runs/{run_id}/actions", response_model=TrainingView)
def action(
    run_id: RequestId,
    body: ActionRequest,
    request: Request,
    user: User,
    service: Service,
    idempotency_key: Key,
) -> TrainingView:
    request.state.command_audit_metadata = {
        "client_event_id": body.command_id,
        "training_id": run_id,
    }
    if body.command_id != idempotency_key:
        raise UseCaseError(
            "idempotency_conflict", "Header and body command IDs must match"
        )
    return TrainingView.model_validate(
        service.action(run_id, user.id, **body.model_dump())
    )


@router.get("/runs/{run_id}/debrief", response_model=TrainingDebrief)
def debrief(run_id: RequestId, user: User, service: Service) -> TrainingDebrief:
    return TrainingDebrief.model_validate(service.debrief(run_id, user.id))


@router.get("/runs/{run_id}/replay", response_model=TrainingView)
def replay(
    run_id: RequestId,
    user: User,
    service: Service,
    at_seconds: Annotated[int, Query(ge=0, le=1200)],
) -> TrainingView:
    return TrainingView.model_validate(service.replay(run_id, user.id, at_seconds))


@router.post("/runs/{run_id}/fork", response_model=TrainingView, status_code=201)
def fork(
    run_id: RequestId,
    body: ForkRequest,
    response: Response,
    request: Request,
    user: User,
    service: Service,
    idempotency_key: Key,
) -> TrainingView:
    request.state.command_audit_metadata = {
        "client_event_id": idempotency_key,
        "training_id": run_id,
    }
    result, duplicate = service.fork(
        run_id, user.id, key=idempotency_key, at_seconds=body.at_seconds
    )
    response.status_code = 200 if duplicate else 201
    return TrainingView.model_validate(result)


@router.get("/runs/{run_id}/comparison", response_model=Comparison)
def comparison(run_id: RequestId, user: User, service: Service) -> Comparison:
    return Comparison.model_validate(service.comparison(run_id, user.id))
