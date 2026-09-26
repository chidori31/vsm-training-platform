"""Strict JSON snapshots of server-owned sessions, verified by engine replay."""

import json
from typing import Annotated

from pydantic import AwareDatetime, Field, field_validator

from app.domain.common import DomainError
from app.domain.engine import restore_session
from app.domain.gameplay import Decision, ScenarioSession, SessionStatus
from app.domain.scenario import Scenario
from app.domain.scoring import AddScore, MetricRef, ScoreState

from .schema import DocumentModel, EffectDocument, MetricDocument, Text


class SessionMetricDocument(MetricDocument):
    # Domain identifiers are not limited to the scenario editor's slug syntax.
    competency_id: Text | None = None


class ScoreDocument(SessionMetricDocument):
    value: int


class SessionEffectDocument(EffectDocument):
    competency_id: Text | None = None


class DecisionDocument(DocumentModel):
    id: Text
    session_id: Text
    node_id: Text
    choice_id: Text
    sequence: Annotated[int, Field(gt=0)]
    decided_at: AwareDatetime
    effects: tuple[SessionEffectDocument, ...]
    explanation: str

    def to_domain(self) -> Decision:
        return Decision(
            id=self.id,
            session_id=self.session_id,
            node_id=self.node_id,
            choice_id=self.choice_id,
            sequence=self.sequence,
            decided_at=self.decided_at,
            effects=tuple(effect.to_domain() for effect in self.effects),
            explanation=self.explanation,
        )


class SessionDocument(DocumentModel):
    format_version: Annotated[int, Field(ge=1, le=1)]
    id: Text
    employee_id: Text
    scenario_id: Text
    scenario_version: Annotated[int, Field(gt=0)]
    current_node_id: Text
    scores: tuple[ScoreDocument, ...]
    initial_scores: tuple[ScoreDocument, ...]
    started_at: AwareDatetime
    status: SessionStatus
    completed_at: AwareDatetime | None
    decisions: tuple[DecisionDocument, ...]

    @field_validator("scores", "initial_scores")
    @classmethod
    def unique_metrics(
        cls, scores: tuple[ScoreDocument, ...]
    ) -> tuple[ScoreDocument, ...]:
        metrics = [score.to_metric() for score in scores]
        if len(metrics) != len(set(metrics)):
            raise ValueError("Duplicate score metric")
        return scores

    def to_domain(self) -> ScenarioSession:
        return ScenarioSession(
            id=self.id,
            employee_id=self.employee_id,
            scenario_id=self.scenario_id,
            scenario_version=self.scenario_version,
            current_node_id=self.current_node_id,
            scores=ScoreState(
                {score.to_metric(): score.value for score in self.scores}
            ),
            initial_scores=ScoreState(
                {score.to_metric(): score.value for score in self.initial_scores}
            ),
            started_at=self.started_at,
            status=self.status,
            completed_at=self.completed_at,
            decisions=tuple(decision.to_domain() for decision in self.decisions),
        )


def _score_document(metric: MetricRef, value: int) -> ScoreDocument:
    return ScoreDocument.model_validate(
        {
            "metric": metric.metric.value,
            "competency_id": metric.competency_id,
            "value": value,
        }
    )


def _effect_document(effect: AddScore) -> SessionEffectDocument:
    return SessionEffectDocument.model_validate(
        {
            "type": "add_score",
            "metric": effect.metric.metric.value,
            "competency_id": effect.metric.competency_id,
            "delta": effect.delta,
        }
    )


def dump_session(scenario: Scenario, session: ScenarioSession) -> str:
    """Serialize only a session that agrees with the supplied scenario and replay."""
    session = restore_session(scenario, session)
    if session.initial_scores is None:
        raise DomainError("Session snapshot requires initial_scores")
    document = SessionDocument(
        format_version=1,
        id=session.id,
        employee_id=session.employee_id,
        scenario_id=session.scenario_id,
        scenario_version=session.scenario_version,
        current_node_id=session.current_node_id,
        scores=tuple(
            _score_document(metric, value)
            for metric, value in session.scores.values.items()
        ),
        initial_scores=tuple(
            _score_document(metric, value)
            for metric, value in session.initial_scores.values.items()
        ),
        started_at=session.started_at,
        status=session.status,
        completed_at=session.completed_at,
        decisions=tuple(
            DecisionDocument(
                id=decision.id,
                session_id=decision.session_id,
                node_id=decision.node_id,
                choice_id=decision.choice_id,
                sequence=decision.sequence,
                decided_at=decision.decided_at,
                effects=tuple(_effect_document(effect) for effect in decision.effects),
                explanation=decision.explanation,
            )
            for decision in session.decisions
        ),
    )
    return document.model_dump_json(indent=2)


def _unique_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_session(scenario: Scenario, raw: str | bytes) -> ScenarioSession:
    """Validate an unmodified server snapshot and reconstruct it through replay."""
    # Check before Pydantic parsing, which otherwise retains only the last key.
    json.loads(raw, object_pairs_hook=_unique_json_keys)
    document = SessionDocument.model_validate_json(raw)
    return restore_session(scenario, document.to_domain())
