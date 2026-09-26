import os
import subprocess
import sys
from pathlib import Path


def test_domain_imports_without_any_site_packages():
    backend = Path(__file__).resolve().parents[2]
    script = """
import importlib
import pkgutil
import app.domain
for module in pkgutil.walk_packages(app.domain.__path__, app.domain.__name__ + '.'):
    importlib.import_module(module.name)
from app.domain.rules import Condition
from app.domain.scoring import ScoreState
assert Condition().matches(ScoreState({}))
from datetime import UTC, datetime
from app.domain.engine import start_session, advance, restore_session
from app.domain.scenario import Scenario, ScenarioNode, Choice
from app.domain.scoring import Metric, MetricRef
graph = Scenario('isolated', 1, 'Isolated', 'start', (
    ScenarioNode('start', 'Start', (Choice('finish', 'Finish', 'end'),)),
    ScenarioNode('end', 'End', terminal=True),
))
now = datetime(2026, 9, 26, tzinfo=UTC)
scores = ScoreState({MetricRef(Metric.PASSENGER_LOYALTY): 0,
                     MetricRef(Metric.SAFETY_RATING): 0})
session = start_session(graph, session_id='s', employee_id='e',
                        initial_scores=scores, now=now)
completed = advance(graph, session, node_id='start', choice_id='finish',
                    decision_id='d', expected_sequence=0, now=now)
assert completed.current_node_id == 'end'
assert restore_session(graph, completed) == completed
"""
    result = subprocess.run(
        [sys.executable, "-S", "-c", script],
        cwd=backend,
        env={
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "DATABASE_URL"}
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
