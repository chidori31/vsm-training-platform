from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.scenarios.schema import ConditionDocument
from app.scenarios.session_state import SessionDocument


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    display_name: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    profile: ProfileResponse


class ScenarioSummary(BaseModel):
    id: str
    version: int
    title: str
    competency_ids: list[str]


class CompetencyScore(BaseModel):
    competency_id: str
    value: int


class ResultSummary(BaseModel):
    session_id: str
    scenario_id: str
    scenario_version: int
    status: Literal["completed"]
    decision_count: int
    duration_seconds: float
    passenger_loyalty: int
    safety_rating: int
    completed_at: datetime
    competencies: list[CompetencyScore]


class ResultResponse(BaseModel):
    summary: ResultSummary
    session: SessionDocument


class AchievementResponse(BaseModel):
    id: str
    version: int
    name: str
    description: str
    condition: ConditionDocument | None
    behavior_rule: str | None = None


class UnlockResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    employee_id: str
    achievement_id: str
    achievement_version: int
    session_id: str
    unlocked_at: datetime


class LeaderboardEntry(BaseModel):
    rank: int
    employee_id: str
    display_name: str
    session_id: str
    score: int
    passenger_loyalty: int
    safety_rating: int


class CompetencyAverage(BaseModel):
    competency_id: str
    average: float


class AnalyticsResponse(BaseModel):
    total_sessions: int
    completed_sessions: int
    active_sessions: int
    decision_count: int
    timeout_count: int
    average_passenger_loyalty: float | None
    average_safety_rating: float | None
    competencies: list[CompetencyAverage]
