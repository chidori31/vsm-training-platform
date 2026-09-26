from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, StrictBool

from app.api.dependencies import Database, Limit, Offset, User
from app.application.retention import RetentionService

router = APIRouter(tags=["retention"])


def get_retention_service(engine: Database) -> RetentionService:
    return RetentionService(engine)


Service = Annotated[RetentionService, Depends(get_retention_service)]


class ChallengeScenarioResponse(BaseModel):
    id: str
    version: int
    title: str
    completed: bool


class ChallengeResponse(BaseModel):
    id: str
    title: str
    description: str
    starts_at: datetime
    expires_at: datetime
    target: int
    progress: int
    status: Literal["scheduled", "active", "completed", "expired"]
    scenarios: list[ChallengeScenarioResponse]


class ChallengePage(BaseModel):
    items: list[ChallengeResponse]
    total: int
    limit: int
    offset: int
    server_time: datetime


class NotificationResponse(BaseModel):
    id: str
    kind: Literal[
        "new_scenario", "challenge_started", "challenge_ending", "achievement_unlocked"
    ]
    title: str
    body: str
    created_at: datetime
    expires_at: datetime
    read_at: datetime | None
    expired: bool


class NotificationPage(BaseModel):
    items: list[NotificationResponse]
    total: int
    limit: int
    offset: int
    unread_count: int
    server_time: datetime


class ReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    read: StrictBool


@router.get("/challenges", response_model=ChallengePage)
def challenges(
    user: User, service: Service, limit: Limit = 20, offset: Offset = 0
) -> ChallengePage:
    return ChallengePage.model_validate(service.challenges(user.id, limit, offset))


@router.get("/notifications", response_model=NotificationPage)
def notifications(
    user: User, service: Service, limit: Limit = 20, offset: Offset = 0
) -> NotificationPage:
    return NotificationPage.model_validate(
        service.notifications(user.id, limit, offset)
    )


@router.put(
    "/notifications/{notification_id}/read", response_model=NotificationResponse
)
def mark_read(
    notification_id: str, body: ReadRequest, user: User, service: Service
) -> NotificationResponse:
    return NotificationResponse.model_validate(
        service.mark_read(user.id, notification_id, body.read)
    )
