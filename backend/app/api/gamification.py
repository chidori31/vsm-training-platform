from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import Database, Limit, Offset, User, require_demo_mode
from app.api.schemas import CompetencyScore
from app.application.demo_personas import PERSONAS
from app.application.gamification import GamificationService

router = APIRouter()


class Organization(BaseModel):
    company_id: str | None
    depot_id: str | None
    brigade_id: str | None
    company_name: str
    depot_name: str
    brigade_name: str


class AchievementProgress(BaseModel):
    id: str
    name: str
    description: str
    current: int
    target: int
    unlocked: bool
    unlocked_at: datetime | None
    session_id: str | None


class RewardResponse(BaseModel):
    session_id: str
    xp: int
    competencies: list[CompetencyScore]
    unlocks: list[str]


class ProgressResponse(BaseModel):
    id: str
    display_name: str
    organization: Organization
    xp: int
    level: int
    level_start_xp: int
    next_level_xp: int
    completed_sessions: int
    rule_version: int
    competencies: list[CompetencyScore]
    achievements: list[AchievementProgress]
    reward: RewardResponse | None


class OrganizationEntry(BaseModel):
    rank: int
    employee_id: str
    display_name: str
    xp: int
    level: int
    completed_sessions: int
    is_me: bool


class OrganizationLeaderboard(BaseModel):
    scope: Literal["brigade", "depot", "company"]
    group_name: str
    assigned: bool
    items: list[OrganizationEntry]
    total: int
    limit: int
    offset: int


class DemoPersona(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    display_name: str
    company_id: str
    depot_id: str
    brigade_id: str


@router.get(
    "/auth/demo/personas",
    response_model=list[DemoPersona],
    tags=["auth"],
    dependencies=[Depends(require_demo_mode)],
)
def personas() -> list[DemoPersona]:
    return [DemoPersona.model_validate(p) for p in PERSONAS]


@router.get("/profiles/me/progress", response_model=ProgressResponse, tags=["profiles"])
def progress(
    user: User,
    engine: Database,
    session_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> ProgressResponse:
    return ProgressResponse.model_validate(
        GamificationService(engine).profile(user.id, session_id)
    )


@router.get(
    "/leaderboard/organization",
    response_model=OrganizationLeaderboard,
    tags=["leaderboard"],
)
def leaderboard(
    user: User,
    engine: Database,
    scope: Literal["brigade", "depot", "company"] = "brigade",
    limit: Limit = 20,
    offset: Offset = 0,
) -> OrganizationLeaderboard:
    return OrganizationLeaderboard.model_validate(
        GamificationService(engine).leaderboard(user.id, scope, limit, offset)
    )
