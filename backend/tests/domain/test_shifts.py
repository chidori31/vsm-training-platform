from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.domain.common import DomainError
from app.domain.engine import advance, expire, start_session
from app.domain.scoring import Metric, MetricRef, ScoreState
from app.scenarios.loader import load_document

NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)
CONTENT = Path(__file__).parents[3] / "scenarios" / "shifts"


def complete(scenario, choices=("assess", "confirm"), seconds=3):
    scores = {
        MetricRef(Metric.PASSENGER_LOYALTY): 50,
        MetricRef(Metric.SAFETY_RATING): 50,
    }
    scores.update(
        {MetricRef(Metric.COMPETENCY, key): 0 for key in scenario.competency_ids}
    )
    session = start_session(
        scenario,
        session_id=scenario.id,
        employee_id="e",
        initial_scores=ScoreState(scores),
        now=NOW,
    )
    for index, choice in enumerate(choices):
        now = NOW + timedelta(seconds=(index + 1) * seconds)
        session = advance(
            scenario,
            session,
            node_id=session.current_node_id,
            choice_id=choice,
            decision_id=f"d-{index}",
            expected_sequence=index,
            now=now,
        )
    return session


def test_seeded_shift_plan_is_reproducible_and_varies_across_seeds():
    from app.domain.shifts import shift_plan

    assert shift_plan(42, "standard") == shift_plan(42, "standard")
    assert [s.kind for s in shift_plan(42, "standard")] == [
        "service",
        "conflict",
        "critical",
    ]
    assert (
        len(
            {
                tuple(s.scenario_id for s in shift_plan(seed, "standard"))
                for seed in range(16)
            }
        )
        > 1
    )
    assert all(s.scenario_version == 2 for s in shift_plan(42, "advanced"))
    assert all(s.passenger_profile and s.context for s in shift_plan(42, "standard"))


@pytest.mark.parametrize(
    "seed,difficulty", [(True, "standard"), (-1, "standard"), (1, "unknown")]
)
def test_shift_plan_rejects_uncontrolled_parameters(seed, difficulty):
    from app.domain.shifts import shift_plan

    with pytest.raises(DomainError):
        shift_plan(seed, difficulty)


def test_all_published_shift_variants_are_real_branches_and_advanced_has_extra_event():
    from app.domain.shifts import SHIFT_VARIANTS

    for variants in SHIFT_VARIANTS.values():
        for variant in variants:
            versions = [
                load_document(CONTENT / f"{variant.scenario_id}-v{v}.json").to_domain()
                for v in (1, 2)
            ]
            for scenario in versions:
                assert {"communication", "regulation"} <= set(scenario.competency_ids)
                good = complete(scenario, ("assess",))
                bad = complete(scenario, ("rush",))
                assert good.current_node_id != bad.current_node_id
                assert good.scores != bad.scores
                assert scenario.node("start").time_limit_seconds is not None
            assert len(versions[1].nodes) > len(versions[0].nodes)
            assert (
                versions[1].node("start").time_limit_seconds
                < versions[0].node("start").time_limit_seconds
            )


def test_shift_metrics_reuse_professional_scores_and_server_reaction_time():
    from app.domain.shifts import shift_metrics, shift_plan

    pairs = []
    for stage in shift_plan(42, "standard"):
        scenario = load_document(CONTENT / f"{stage.scenario_id}-v1.json").to_domain()
        pairs.append((scenario, complete(scenario)))
    metrics = shift_metrics(pairs, xp=150)
    assert metrics.completed_scenarios == metrics.total_scenarios == 3
    assert metrics.decision_count == 6 and metrics.average_reaction_seconds == 3
    assert metrics.regulation > 0 and metrics.communication > 0
    assert metrics.safety > 50 and metrics.service > 50
    assert metrics.xp == 150 and metrics.critical_errors == 0


def test_shift_qualifications_require_behavior_and_completed_route():
    from app.domain.shifts import qualification_ids, shift_metrics, shift_plan

    pairs = []
    for stage in shift_plan(1, "standard"):
        scenario = load_document(CONTENT / f"{stage.scenario_id}-v1.json").to_domain()
        pairs.append((scenario, complete(scenario)))
    metrics = shift_metrics(pairs)
    assert qualification_ids(pairs, metrics, "standard", completed=False) == ()
    awards = qualification_ids(pairs, metrics, "standard", completed=True)
    assert "shift-regulation" in awards and "shift-conflict" in awards
    assert "shift-advanced" not in awards
    assert "shift-advanced" in qualification_ids(
        pairs, metrics, "advanced", completed=True
    )


def test_timeout_is_a_critical_error_but_not_measured_player_reaction():
    from app.domain.shifts import shift_metrics, shift_plan

    stage = shift_plan(1, "standard")[2]
    scenario = load_document(CONTENT / f"{stage.scenario_id}-v1.json").to_domain()
    session = complete(scenario, ())
    delayed = expire(
        scenario,
        session,
        node_id="start",
        decision_id="timeout:one",
        expected_sequence=0,
        now=NOW + timedelta(seconds=100),
    )
    metrics = shift_metrics([(scenario, delayed)])
    assert metrics.critical_errors == 1 and metrics.average_reaction_seconds is None
    assert metrics.decision_count == 1
    assert metrics.completed_scenarios == 0


def test_growth_qualification_requires_recovery_of_previously_negative_skill():
    from app.domain.shifts import qualification_ids, shift_metrics, shift_plan

    pairs = []
    for stage in shift_plan(1, "standard"):
        scenario = load_document(CONTENT / f"{stage.scenario_id}-v1.json").to_domain()
        pairs.append((scenario, complete(scenario)))
    metrics = shift_metrics(pairs)
    for previous, expected in [
        (None, False),
        ({"regulation": 0}, False),
        ({"regulation": 1}, False),
        ({"regulation": -1}, True),
        ({"communication": -2}, True),
    ]:
        earned = qualification_ids(
            pairs, metrics, "standard", completed=True, previous_competencies=previous
        )
        assert ("shift-growth" in earned) is expected
    assert "shift-growth" not in qualification_ids(
        pairs,
        metrics,
        "standard",
        completed=False,
        previous_competencies={"regulation": -1},
    )
