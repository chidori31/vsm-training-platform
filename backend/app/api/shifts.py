from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import Database, User
from app.api.sessions import RequestId
from app.application.shifts import ShiftService

router = APIRouter(prefix="/shifts", tags=["training shifts"])


class ShiftStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    difficulty: Literal["standard", "advanced"] = "standard"


class ShiftAdvanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    command_id: RequestId
    expected_step: Annotated[int, Field(ge=0, le=2)]
    session_id: RequestId


class ShiftStepResponse(BaseModel):
    index: int
    kind: Literal["service", "conflict", "critical"]
    title: str
    scenario_id: str
    scenario_version: int
    passenger_profile: str
    context: str
    session_id: str | None
    status: Literal["locked", "active", "completed"]


class ShiftMetricsResponse(BaseModel):
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


class ShiftAchievementResponse(BaseModel):
    id: str
    title: str
    description: str
    unlocked: bool


class ShiftResponse(BaseModel):
    id: str
    title: str
    status: Literal["active", "completed"]
    seed: int
    difficulty: Literal["standard", "advanced"]
    started_at: datetime
    completed_at: datetime | None
    current_step: int
    current_session_id: str | None
    steps: list[ShiftStepResponse]
    metrics: ShiftMetricsResponse
    achievements: list[ShiftAchievementResponse]
    recommendations: list[str]


class CurrentShiftResponse(BaseModel):
    shift: ShiftResponse | None


def get_shift_service(engine: Database) -> ShiftService:
    return ShiftService(engine)


Service = Annotated[ShiftService, Depends(get_shift_service)]


@router.post(
    "",
    response_model=ShiftResponse,
    status_code=201,
    responses={200: {"model": ShiftResponse, "description": "Idempotent replay"}},
)
def start(
    request: ShiftStartRequest,
    response: Response,
    http_request: Request,
    user: User,
    service: Service,
    idempotency_key: Annotated[RequestId, Header(alias="Idempotency-Key")],
) -> ShiftResponse:
    http_request.state.command_audit_metadata = {"client_event_id": idempotency_key}
    value, replayed = service.start(
        user.id, key=idempotency_key, difficulty=request.difficulty
    )
    response.status_code = 200 if replayed else 201
    return ShiftResponse.model_validate(value)


@router.get("/current", response_model=CurrentShiftResponse)
def current(user: User, service: Service) -> CurrentShiftResponse:
    value = service.current(user.id)
    return CurrentShiftResponse(
        shift=ShiftResponse.model_validate(value) if value else None
    )


@router.get("/{shift_id}", response_model=ShiftResponse)
def get(shift_id: RequestId, user: User, service: Service) -> ShiftResponse:
    return ShiftResponse.model_validate(service.get(shift_id, user.id))


@router.post("/{shift_id}/advance", response_model=ShiftResponse)
def advance(
    shift_id: RequestId,
    request: ShiftAdvanceRequest,
    http_request: Request,
    user: User,
    service: Service,
) -> ShiftResponse:
    http_request.state.command_audit_metadata = {
        "client_event_id": request.command_id,
        "requested_shift_id": shift_id,
        "expected_step": request.expected_step,
        "requested_session_id": request.session_id,
    }
    return ShiftResponse.model_validate(
        service.advance(shift_id, user.id, **request.model_dump())
    )
