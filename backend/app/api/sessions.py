import os
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from app.application.sessions import (
    ScenarioNotFound,
    SessionNotFound,
    SessionService,
    SessionView,
)
from app.domain.common import DomainError
from app.scenarios.session_state import SessionDocument, dump_session

router = APIRouter(prefix="/sessions", tags=["sessions"])
RequestId = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"\S")
]


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scenario_id: RequestId
    scenario_version: Annotated[int, Field(gt=0, le=2147483647)]
    employee_id: RequestId


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision_id: RequestId
    node_id: RequestId
    choice_id: RequestId
    expected_sequence: Annotated[int, Field(ge=0)]

    @field_validator("decision_id")
    @classmethod
    def reserve_timeout_namespace(cls, value: str) -> str:
        if value.startswith("timeout:"):
            raise ValueError("The timeout: namespace belongs to the server")
        return value


class SessionResponse(BaseModel):
    session: SessionDocument
    server_time: datetime
    deadline: datetime | None


class DecisionResponse(SessionResponse):
    outcome: Literal["accepted", "duplicate", "timed_out", "rejected"]
    acknowledged_decision_id: str | None
    error: str | None


def get_session_service() -> Iterator[SessionService]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise HTTPException(status_code=503, detail="Database unavailable")
    engine = None
    try:
        engine = create_engine(url, pool_pre_ping=True)
        yield SessionService(engine)
    except (SessionNotFound, ScenarioNotFound) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Database unavailable") from error
    except DomainError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    finally:
        if engine is not None:
            engine.dispose()


Service = Annotated[SessionService, Depends(get_session_service)]


def response_for(view: SessionView) -> SessionResponse:
    return SessionResponse(
        session=SessionDocument.model_validate_json(
            dump_session(view.scenario, view.session)
        ),
        server_time=view.server_time,
        deadline=view.deadline,
    )


@router.post("", status_code=201, response_model=SessionResponse)
def start_session(request: StartRequest, service: Service) -> SessionResponse:
    return response_for(service.start(**request.model_dump()))


@router.get("/{session_id}", response_model=SessionResponse)
def get_session(session_id: str, service: Service) -> SessionResponse:
    return response_for(service.get(session_id))


@router.post(
    "/{session_id}/decisions",
    response_model=DecisionResponse,
    responses={
        409: {
            "model": DecisionResponse,
            "description": "Choice rejected; current state is saved",
        }
    },
)
def make_decision(
    session_id: str, request: DecisionRequest, response: Response, service: Service
) -> DecisionResponse:
    result = service.decide(session_id, **request.model_dump())
    if result.outcome in {"timed_out", "rejected"}:
        response.status_code = 409
    return DecisionResponse(
        **response_for(result.view).model_dump(),
        outcome=result.outcome,
        acknowledged_decision_id=result.acknowledged_decision_id,
        error=result.error,
    )
