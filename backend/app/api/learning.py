from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import Database, User
from app.api.sessions import Service
from app.application.learning import LearningService, session_debrief

router = APIRouter(tags=["learning"])


class LearningFact(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ScaleExplanationResponse(LearningFact):
    before: int
    after: int
    delta: int
    requested_delta: int
    explanation: str


class CompetencyExplanationResponse(LearningFact):
    competency_id: str
    before: int
    after: int
    delta: int
    explanation: str


class CompetencyDeltaResponse(LearningFact):
    competency_id: str
    delta: int


class AlternativeResponse(LearningFact):
    choice_id: str
    text: str
    destination_id: str
    explanation: str
    available: bool
    loyalty_delta: int
    safety_delta: int
    competencies: list[CompetencyDeltaResponse]


class SuggestionResponse(LearningFact):
    text: str
    choice_ids: list[str]


class DecisionDebriefResponse(LearningFact):
    sequence: int
    decision_id: str
    node_id: str
    node_text: str
    choice_id: str
    choice_text: str
    destination_id: str
    destination_text: str
    decided_at: datetime
    elapsed_seconds: float
    was_timeout: bool
    explanation: str
    loyalty: ScaleExplanationResponse
    safety: ScaleExplanationResponse
    competencies: list[CompetencyExplanationResponse]
    alternatives: list[AlternativeResponse]
    suggestion: SuggestionResponse
    pattern_codes: list[str]


class DebriefSummaryResponse(LearningFact):
    decision_count: int
    timeout_count: int
    loyalty_delta: int
    safety_delta: int


class DebriefResponse(LearningFact):
    rule_version: Literal[1]
    session_id: str
    scenario_id: str
    scenario_version: int
    title: str
    completed_at: datetime
    summary: DebriefSummaryResponse
    decisions: list[DecisionDebriefResponse]


class TrendPointResponse(LearningFact):
    session_id: str
    completed_at: datetime
    delta: int
    earned_points: int
    cumulative_points: int


class CompetencyAnalyticsResponse(LearningFact):
    competency_id: str
    earned_points: int
    net_delta: int
    positive_decisions: int
    negative_decisions: int
    opportunities: int
    practiced_sessions: int
    status: Literal["insufficient_data", "strength", "growth_area", "developing"]
    trend: list[TrendPointResponse]


class PatternResponse(LearningFact):
    code: str
    title: str
    description: str
    count: int
    session_count: int
    recurring: bool
    advice: str


class ScenarioStatisticsResponse(LearningFact):
    scenario_id: str
    scenario_version: int
    title: str
    attempts: int
    completed: int
    active: int
    timeout_count: int
    average_duration_seconds: float | None
    average_loyalty: float | None
    average_safety: float | None


class LearningAnalyticsResponse(LearningFact):
    rule_version: Literal[1]
    total_sessions: int
    completed_sessions: int
    active_sessions: int
    decision_count: int
    timeout_count: int
    competencies: list[CompetencyAnalyticsResponse]
    strengths: list[str]
    weaknesses: list[str]
    patterns: list[PatternResponse]
    scenarios: list[ScenarioStatisticsResponse]


@router.get("/sessions/{session_id}/debrief", response_model=DebriefResponse)
def debrief(session_id: str, user: User, service: Service) -> DebriefResponse:
    return DebriefResponse.model_validate(
        session_debrief(service.get(session_id, employee_id=user.id))
    )


@router.get("/analytics/me/competencies", response_model=LearningAnalyticsResponse)
def competencies(user: User, engine: Database) -> LearningAnalyticsResponse:
    return LearningAnalyticsResponse.model_validate(
        LearningService(engine).competencies(user.id)
    )
