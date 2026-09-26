from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import Database, Limit, Offset, User, require_demo_mode
from app.api.schemas import (
    AchievementResponse,
    AnalyticsResponse,
    LeaderboardEntry,
    LoginResponse,
    Page,
    ProfileResponse,
    ResultSummary,
    ScenarioSummary,
    UnlockResponse,
)
from app.application.identity import IdentityService
from app.application.queries import QueryService
from app.scenarios.schema import ScenarioDocument

router = APIRouter()


class DemoLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    persona_id: Literal[
        "demo-employee",
        "demo-north-02",
        "demo-north-03",
        "demo-south-04",
        "demo-other-05",
    ] = "demo-employee"


@router.post(
    "/auth/demo",
    response_model=LoginResponse,
    tags=["auth"],
    dependencies=[Depends(require_demo_mode)],
)
def demo_login(engine: Database, body: DemoLoginRequest | None = None) -> LoginResponse:
    result = IdentityService(engine).demo_login(
        body.persona_id if body else "demo-employee"
    )
    return LoginResponse(
        access_token=result.access_token,
        expires_at=result.expires_at,
        profile=ProfileResponse.model_validate(result.profile),
    )


@router.get("/auth/me", response_model=ProfileResponse, tags=["auth"])
@router.get("/profiles/me", response_model=ProfileResponse, tags=["profiles"])
def profile(user: User) -> ProfileResponse:
    return ProfileResponse.model_validate(user)


@router.get(
    "/profiles/me/achievements", response_model=Page[UnlockResponse], tags=["profiles"]
)
def profile_achievements(
    user: User,
    engine: Database,
    limit: Limit = 20,
    offset: Offset = 0,
) -> Page[UnlockResponse]:
    return Page[UnlockResponse].model_validate(
        QueryService(engine).unlocks(user.id, limit, offset)
    )


@router.get("/scenarios", response_model=Page[ScenarioSummary], tags=["scenarios"])
def scenarios(
    user: User, engine: Database, limit: Limit = 20, offset: Offset = 0
) -> Page[ScenarioSummary]:
    return Page[ScenarioSummary].model_validate(
        QueryService(engine).scenarios(limit, offset)
    )


@router.get(
    "/scenarios/{scenario_id}/versions/{version}",
    response_model=ScenarioDocument,
    tags=["scenarios"],
)
def scenario(
    scenario_id: str,
    version: Annotated[int, Path(gt=0, le=2147483647)],
    user: User,
    engine: Database,
) -> ScenarioDocument:
    return QueryService(engine).scenario(scenario_id, version)


@router.get("/results", response_model=Page[ResultSummary], tags=["results"])
def results(
    user: User, engine: Database, limit: Limit = 20, offset: Offset = 0
) -> Page[ResultSummary]:
    return Page[ResultSummary].model_validate(
        QueryService(engine).results(user.id, limit, offset)
    )


@router.get(
    "/achievements", response_model=Page[AchievementResponse], tags=["achievements"]
)
def achievements(
    user: User,
    engine: Database,
    limit: Limit = 20,
    offset: Offset = 0,
) -> Page[AchievementResponse]:
    return Page[AchievementResponse].model_validate(
        QueryService(engine).achievements(limit, offset)
    )


@router.get("/leaderboard", response_model=Page[LeaderboardEntry], tags=["leaderboard"])
def leaderboard(
    user: User,
    engine: Database,
    scenario_id: Annotated[str, Query(min_length=1, max_length=64)],
    scenario_version: Annotated[int, Query(gt=0, le=2147483647)],
    metric: Literal["passenger_loyalty", "safety_rating"] = "safety_rating",
    limit: Limit = 20,
    offset: Offset = 0,
) -> Page[LeaderboardEntry]:
    return Page[LeaderboardEntry].model_validate(
        QueryService(engine).leaderboard(
            scenario_id, scenario_version, metric, limit, offset
        )
    )


@router.get("/analytics/me", response_model=AnalyticsResponse, tags=["analytics"])
def analytics(user: User, engine: Database) -> AnalyticsResponse:
    return AnalyticsResponse.model_validate(QueryService(engine).analytics(user.id))
