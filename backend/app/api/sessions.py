from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from app.api.dependencies import Database, Limit, Offset, User
from app.api.errors import error_response
from app.api.schemas import Page, ResultResponse
from app.application.queries import decision_history, session_result
from app.application.sessions import SessionService, SessionView
from app.scenarios.schema import Identifier
from app.scenarios.session_state import DecisionDocument, SessionDocument, dump_session

router = APIRouter(prefix="/sessions", tags=["scenario sessions"])


def safe_request_id(value: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Identifiers must not contain control characters")
    return value


RequestId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"\S"),
    AfterValidator(safe_request_id),
]


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scenario_id: Identifier
    scenario_version: Annotated[int, Field(gt=0, le=2147483647)]


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision_id: RequestId
    node_id: RequestId
    choice_id: RequestId
    expected_sequence: Annotated[int, Field(ge=0, le=2147483647)]

    @field_validator("decision_id")
    @classmethod
    def reserve_timeout_namespace(cls, value: str) -> str:
        if value.startswith("timeout:"):
            raise ValueError("The timeout: namespace belongs to the server")
        return value


class NodeResponse(BaseModel):
    id: str
    text: str
    terminal: bool
    time_limit_seconds: int | None


class ChoiceResponse(BaseModel):
    id: str
    text: str


class SessionResponse(BaseModel):
    session: SessionDocument
    server_time: datetime
    deadline: datetime | None
    expected_sequence: int
    current_node: NodeResponse
    available_choices: list[ChoiceResponse]


class DecisionResponse(SessionResponse):
    outcome: Literal["accepted", "duplicate"]
    acknowledged_decision_id: str


def get_session_service(engine: Database) -> SessionService:
    return SessionService(engine)


Service = Annotated[SessionService, Depends(get_session_service)]


def response_for(view: SessionView) -> SessionResponse:
    node = view.node
    return SessionResponse(
        session=SessionDocument.model_validate_json(
            dump_session(view.scenario, view.session)
        ),
        server_time=view.server_time,
        deadline=view.deadline,
        expected_sequence=len(view.session.decisions),
        current_node=NodeResponse(
            id=node.id,
            text=node.text,
            terminal=node.terminal,
            time_limit_seconds=node.time_limit_seconds,
        ),
        available_choices=[
            ChoiceResponse(id=item.id, text=item.text) for item in view.choices
        ],
    )


@router.post(
    "",
    status_code=201,
    response_model=SessionResponse,
    responses={200: {"model": SessionResponse, "description": "Idempotent replay"}},
)
def start_session(
    request: StartRequest,
    http_request: Request,
    response: Response,
    user: User,
    service: Service,
    idempotency_key: Annotated[RequestId, Header(alias="Idempotency-Key")],
) -> SessionResponse:
    http_request.state.command_audit_metadata = {
        "client_event_id": idempotency_key,
        "requested_scenario_id": request.scenario_id,
        "requested_scenario_version": request.scenario_version,
    }
    view, replayed = service.start_idempotent(
        **request.model_dump(), employee_id=user.id, key=idempotency_key
    )
    response.status_code = 200 if replayed else 201
    response.headers["Location"] = f"/api/v1/sessions/{view.session.id}"
    return response_for(view)


@router.get("/{session_id}", response_model=SessionResponse)
def get_session(session_id: str, user: User, service: Service) -> SessionResponse:
    return response_for(service.get(session_id, employee_id=user.id))


@router.post(
    "/{session_id}/decisions", response_model=DecisionResponse, tags=["decisions"]
)
def make_decision(
    session_id: str,
    request: DecisionRequest,
    http_request: Request,
    user: User,
    service: Service,
) -> DecisionResponse | JSONResponse:
    http_request.state.command_audit_metadata = {
        "client_event_id": request.decision_id,
        "requested_node_id": request.node_id,
        "requested_choice_id": request.choice_id,
        "expected_revision": request.expected_sequence,
    }
    result = service.decide(session_id, **request.model_dump(), employee_id=user.id)
    state = response_for(result.view)
    if result.outcome in {"timed_out", "rejected"}:
        return error_response(
            409,
            f"decision_{result.outcome}",
            result.error or "Decision rejected",
            data=state.model_dump(mode="json"),
        )
    return DecisionResponse.model_validate(
        {
            **state.model_dump(),
            "outcome": result.outcome,
            "acknowledged_decision_id": result.acknowledged_decision_id,
        }
    )


@router.get(
    "/{session_id}/decisions", response_model=Page[DecisionDocument], tags=["decisions"]
)
def decisions(
    session_id: str,
    user: User,
    service: Service,
    limit: Limit = 20,
    offset: Offset = 0,
) -> Page[DecisionDocument]:
    return Page[DecisionDocument].model_validate(
        decision_history(service.get(session_id, employee_id=user.id), limit, offset)
    )


@router.get("/{session_id}/result", response_model=ResultResponse, tags=["results"])
def result(session_id: str, user: User, service: Service) -> ResultResponse:
    return ResultResponse.model_validate(
        session_result(service.get(session_id, employee_id=user.id))
    )
