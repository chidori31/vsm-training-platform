"""Explicit role-gated training authoring and instruction API."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Response
from pydantic import Field, StringConstraints

from app.api.dependencies import Database, User
from app.api.sessions import RequestId
from app.api.simulations import StartRequest as StrictRequest
from app.api.training import TrainingDebrief
from app.application.training_staff import TrainingStaffService, access

router = APIRouter(prefix="/training", tags=["training staff"])
Key = Annotated[RequestId, Header(alias="Idempotency-Key")]


def get_training_staff_service(engine: Database) -> TrainingStaffService:
    return TrainingStaffService(engine)


Service = Annotated[TrainingStaffService, Depends(get_training_staff_service)]


class AssignmentRequest(StrictRequest):
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    mode: Literal["work", "demo"]


class DraftRequest(StrictRequest):
    source_id: RequestId | None = None


class RevisionRequest(StrictRequest):
    expected_revision: Annotated[int, Field(ge=0, le=2147483647)]


class SaveRequest(RevisionRequest):
    document: dict[str, Any]


class CommentRequest(StrictRequest):
    event_id: RequestId
    text: Annotated[str, StringConstraints(min_length=1, max_length=2000)]


@router.get("/access")
def training_access(user: User) -> dict[str, Any]:
    return access(user.id)


@router.get("/assignments")
def assignments(user: User, service: Service) -> dict[str, Any]:
    return service.assignments(user.id)


@router.post("/assignments", status_code=201)
def create_assignment(
    body: AssignmentRequest,
    user: User,
    service: Service,
    response: Response,
    idempotency_key: Key,
) -> dict[str, Any]:
    result, duplicate = service.create_assignment(
        user.id, key=idempotency_key, **body.model_dump()
    )
    response.status_code = 200 if duplicate else 201
    return result


@router.get("/assignments/{assignment_id}")
def assignment(
    assignment_id: RequestId, user: User, service: Service
) -> dict[str, Any]:
    return service.assignment_detail(user.id, assignment_id)


@router.get("/instructor/runs/{run_id}", response_model=TrainingDebrief)
def instructor_run(run_id: RequestId, user: User, service: Service) -> TrainingDebrief:
    return TrainingDebrief.model_validate(service.instructor_debrief(user.id, run_id))


@router.get("/instructor/runs/{run_id}/comments")
def comments(run_id: RequestId, user: User, service: Service) -> dict[str, Any]:
    return service.comments(user.id, run_id)


@router.post("/instructor/runs/{run_id}/comments", status_code=201)
def comment(
    run_id: RequestId,
    body: CommentRequest,
    user: User,
    service: Service,
    response: Response,
    idempotency_key: Key,
) -> dict[str, Any]:
    result, duplicate = service.comment(
        user.id, run_id, key=idempotency_key, event_id=body.event_id, body=body.text
    )
    response.status_code = 200 if duplicate else 201
    return result


@router.get("/content")
def content(user: User, service: Service) -> dict[str, Any]:
    return service.list_content(user.id)


@router.post("/content/drafts", status_code=201)
def draft(
    body: DraftRequest,
    user: User,
    service: Service,
    response: Response,
    idempotency_key: Key,
) -> dict[str, Any]:
    result, duplicate = service.draft(
        user.id, key=idempotency_key, source_id=body.source_id
    )
    response.status_code = 200 if duplicate else 201
    return result


@router.get("/content/{content_id}")
def content_detail(
    content_id: RequestId, user: User, service: Service
) -> dict[str, Any]:
    return service.get_content(user.id, content_id)


@router.put("/content/{content_id}")
def save(
    content_id: RequestId, body: SaveRequest, user: User, service: Service
) -> dict[str, Any]:
    return service.save(user.id, content_id, **body.model_dump())


@router.post("/content/{content_id}/validate")
def validate(content_id: RequestId, user: User, service: Service) -> dict[str, Any]:
    return service.validate(user.id, content_id)


@router.post("/content/{content_id}/publish")
def publish(
    content_id: RequestId,
    body: RevisionRequest,
    user: User,
    service: Service,
    idempotency_key: Key,
) -> dict[str, Any]:
    return service.publish(
        user.id,
        content_id,
        key=idempotency_key,
        expected_revision=body.expected_revision,
    )[0]
