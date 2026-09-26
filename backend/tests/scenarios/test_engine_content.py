import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.domain.engine import advance, available_choices, expire, start_session
from app.domain.gameplay import SessionStatus
from app.domain.scoring import Metric, MetricRef, ScoreState
from app.scenarios.loader import load_document
from app.scenarios.schema import ScenarioDocument

DEMO = Path(__file__).parents[3] / "scenarios" / "demo"
NOW = datetime(2026, 9, 26, tzinfo=UTC)


def start(graph):
    scores = {
        MetricRef(Metric.PASSENGER_LOYALTY): 0,
        MetricRef(Metric.SAFETY_RATING): 0,
    }
    scores.update(
        {MetricRef(Metric.COMPETENCY, key): 0 for key in graph.competency_ids}
    )
    return start_session(
        graph,
        session_id="test",
        employee_id="synthetic",
        now=NOW,
        initial_scores=ScoreState(scores),
    )


@pytest.mark.parametrize(
    "filename",
    ["passenger-conflict.json", "medical-incident.json", "service-situation.json"],
)
def test_existing_demo_runs_normal_and_timeout_paths(filename):
    graph = load_document(DEMO / filename).to_domain()
    initial = start(graph)
    session = initial
    for step in range(2):
        now = NOW + timedelta(seconds=step + 1)
        choices = available_choices(graph, session, now=now)
        assert len(choices) == 2
        session = advance(
            graph,
            session,
            node_id=session.current_node_id,
            choice_id=choices[0].id,
            decision_id=f"d{step}",
            expected_sequence=step,
            now=now,
        )
    assert session.status is SessionStatus.COMPLETED
    assert all(d.explanation for d in session.decisions)
    timeout = expire(
        graph,
        initial,
        node_id=graph.start_node_id,
        decision_id="timeout",
        expected_sequence=0,
        now=NOW + timedelta(seconds=60),
    )
    assert timeout.status is SessionStatus.COMPLETED
    assert timeout.current_node_id != session.current_node_id


def test_new_branch_is_only_json_data():
    document = json.loads(
        (DEMO / "passenger-conflict.json").read_text(encoding="utf-8")
    )
    document["version"] += 1
    document["nodes"][0]["choices"].append(
        {
            "id": "new-branch",
            "text": "New synthetic branch",
            "destination": "new-final",
            "explanation": "A teammate added this branch without modifying the engine.",
            "effects": [{"type": "add_score", "metric": "safety_rating", "delta": 7}],
        }
    )
    document["nodes"].append({"id": "new-final", "text": "New final", "terminal": True})
    graph = ScenarioDocument.model_validate_json(json.dumps(document)).to_domain()
    result = advance(
        graph,
        start(graph),
        node_id=graph.start_node_id,
        choice_id="new-branch",
        decision_id="new",
        expected_sequence=0,
        now=NOW,
    )
    assert result.current_node_id == "new-final"
    assert result.scores.value(MetricRef(Metric.SAFETY_RATING)) == 7
    assert result.status is SessionStatus.COMPLETED
