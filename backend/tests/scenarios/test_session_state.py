import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.scenario import Choice, Scenario, ScenarioNode
from app.domain.scoring import AddScore, Metric, MetricRef, ScoreState

STARTED_AT = datetime(2026, 9, 26, 9, 0, tzinfo=UTC)
LOYALTY = MetricRef(Metric.PASSENGER_LOYALTY)
SAFETY = MetricRef(Metric.SAFETY_RATING)
COMMUNICATION = MetricRef(Metric.COMPETENCY, "Коммуникация с пассажирами")


@pytest.fixture
def snapshot_scenario():
    return Scenario(
        id="Scenario / Пример",
        version=2147483648,
        title="Snapshot example",
        start_node_id="Start / 1",
        competency_ids=(COMMUNICATION.competency_id,),
        nodes=(
            ScenarioNode(
                id="Start / 1",
                text="Respond to the passenger",
                time_limit_seconds=10,
                timeout_choice_id="__timeout__",
                choices=(
                    Choice(
                        id="Stay calm!",
                        text="Respond calmly",
                        target_node_id="Middle / 2",
                        effects=(AddScore(LOYALTY, 2), AddScore(COMMUNICATION, 3)),
                        explanation="Спокойный ответ помогает пассажиру.",
                    ),
                    Choice(
                        id="__timeout__",
                        text="Time expired",
                        target_node_id="Expired",
                        effects=(AddScore(LOYALTY, -5),),
                        explanation="The response deadline passed.",
                    ),
                ),
            ),
            ScenarioNode(
                id="Middle / 2",
                text="Finish the interaction",
                choices=(
                    Choice(
                        id="Finish!",
                        text="Finish safely",
                        target_node_id="Done",
                        effects=(AddScore(SAFETY, 1),),
                    ),
                ),
            ),
            ScenarioNode(id="Done", text="Finished", terminal=True),
            ScenarioNode(id="Expired", text="Missed deadline", terminal=True),
        ),
    )


def start(scenario):
    from app.domain.engine import start_session

    return start_session(
        scenario,
        session_id="Session / 42",
        employee_id="Employee 17",
        initial_scores=ScoreState({LOYALTY: 50, SAFETY: 60, COMMUNICATION: 0}),
        now=STARTED_AT,
    )


def halfway(scenario):
    from app.domain.engine import advance

    return advance(
        scenario,
        start(scenario),
        node_id="Start / 1",
        choice_id="Stay calm!",
        decision_id="Decision / 1",
        expected_sequence=0,
        now=STARTED_AT + timedelta(seconds=2),
    )


def finish(scenario, session):
    from app.domain.engine import advance

    return advance(
        scenario,
        session,
        node_id="Middle / 2",
        choice_id="Finish!",
        decision_id="Decision / 2",
        expected_sequence=1,
        now=STARTED_AT + timedelta(seconds=4),
    )


def test_halfway_snapshot_continues_like_uninterrupted_session(snapshot_scenario):
    from app.scenarios.session_state import dump_session, load_session

    session = halfway(snapshot_scenario)
    raw = dump_session(snapshot_scenario, session)
    restored = load_session(snapshot_scenario, raw.encode("utf-8"))

    assert restored == session
    assert restored.initial_scores.value(LOYALTY) == 50
    assert restored.scores.value(LOYALTY) == 52
    assert restored.scores.value(COMMUNICATION) == 3
    assert restored.decisions[0].explanation == "Спокойный ответ помогает пассажиру."
    assert finish(snapshot_scenario, restored) == finish(snapshot_scenario, session)
    assert json.loads(raw)["format_version"] == 1


def test_completed_snapshot_retains_history_and_completion(snapshot_scenario):
    from app.scenarios.session_state import dump_session, load_session

    completed = finish(snapshot_scenario, halfway(snapshot_scenario))
    restored = load_session(
        snapshot_scenario, dump_session(snapshot_scenario, completed)
    )

    assert restored == completed
    assert restored.current_node_id == "Done"
    assert restored.completed_at == STARTED_AT + timedelta(seconds=4)
    assert restored.scores.value(SAFETY) == 61
    assert len(restored.decisions) == 2
    assert restored.decisions[1].explanation == ""


def test_timeout_snapshot_retains_reserved_choice_and_effects(snapshot_scenario):
    from app.domain.engine import expire
    from app.scenarios.session_state import dump_session, load_session

    expired = expire(
        snapshot_scenario,
        start(snapshot_scenario),
        node_id="Start / 1",
        decision_id="Timeout / 1",
        expected_sequence=0,
        now=STARTED_AT + timedelta(seconds=10),
    )
    restored = load_session(snapshot_scenario, dump_session(snapshot_scenario, expired))

    assert restored == expired
    assert restored.current_node_id == "Expired"
    assert restored.scores.value(LOYALTY) == 45
    assert restored.decisions[0].choice_id == "__timeout__"
    assert restored.decisions[0].explanation == "The response deadline passed."


@pytest.mark.parametrize(
    "path,value",
    [
        (("scores", 0, "value"), 999),
        (("initial_scores", 0, "value"), 999),
        (("current_node_id",), "Done"),
        (("decisions", 0, "effects", 0, "delta"), 999),
        (("decisions", 0, "explanation"), "Invented explanation"),
        (("decisions", 0, "decided_at"), "2026-09-26T09:00:11Z"),
        (("decisions", 0, "sequence"), 2),
        (("decisions", 0, "choice_id"), "__timeout__"),
        (("scenario_version",), 2147483649),
        (("scenario_id",), "Other scenario"),
        (("status",), "completed"),
    ],
)
def test_replay_rejects_tampered_snapshots(snapshot_scenario, path, value):
    from app.scenarios.session_state import dump_session, load_session

    payload = json.loads(dump_session(snapshot_scenario, halfway(snapshot_scenario)))
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    with pytest.raises(ValueError):
        load_session(snapshot_scenario, json.dumps(payload))


@pytest.mark.parametrize("field", ["initial_scores", "scores"])
def test_duplicate_metric_entries_are_rejected(snapshot_scenario, field):
    from app.scenarios.session_state import dump_session, load_session

    payload = json.loads(dump_session(snapshot_scenario, halfway(snapshot_scenario)))
    payload[field].append(payload[field][0].copy())

    with pytest.raises(ValueError, match="Duplicate.*metric"):
        load_session(snapshot_scenario, json.dumps(payload))


@pytest.mark.parametrize(
    "path,value",
    [
        (("format_version",), 2),
        (("format_version",), True),
        (("format_version",), 1.0),
        (("format_version",), "1"),
        (("scenario_version",), True),
        (("id",), 42),
        (("employee_id",), "  "),
        (("initial_scores",), None),
        (("scores", 0, "value"), "52"),
        (("scores", 0, "value"), True),
        (("scores", 0, "value"), 52.0),
        (("scores", 0, "metric"), "money"),
        (("decisions", 0, "effects", 0, "delta"), "2"),
        (("decisions", 0, "effects", 0, "type"), "eval"),
        (("decisions", 0, "explanation"), 1),
        (("started_at",), "2026-09-26T09:00:00"),
        (("started_at",), 1790413200),
        (("decisions", 0, "decided_at"), "2026-09-26T09:00:02"),
        (("unexpected",), "extra"),
        (("decisions", 0, "unexpected"), "extra"),
        (("scores", 0, "unexpected"), "extra"),
    ],
)
def test_snapshot_rejects_wrong_scalars_and_extra_fields(
    snapshot_scenario, path, value
):
    from app.scenarios.session_state import dump_session, load_session

    payload = json.loads(dump_session(snapshot_scenario, halfway(snapshot_scenario)))
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    with pytest.raises(ValueError):
        load_session(snapshot_scenario, json.dumps(payload))


@pytest.mark.parametrize("field", ["format_version", "initial_scores", "decisions"])
def test_snapshot_requires_replay_fields(snapshot_scenario, field):
    from app.scenarios.session_state import dump_session, load_session

    payload = json.loads(dump_session(snapshot_scenario, halfway(snapshot_scenario)))
    del payload[field]
    with pytest.raises(ValueError):
        load_session(snapshot_scenario, json.dumps(payload))


@pytest.mark.parametrize("duplicate", ['"format_version": 1', '"delta": 2'])
def test_duplicate_json_keys_are_rejected_at_every_depth(snapshot_scenario, duplicate):
    from app.scenarios.session_state import dump_session, load_session

    raw = json.dumps(
        json.loads(dump_session(snapshot_scenario, halfway(snapshot_scenario)))
    )
    raw = raw.replace(duplicate, f"{duplicate}, {duplicate}", 1)
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        load_session(snapshot_scenario, raw)


def test_dump_validates_session_before_serializing(snapshot_scenario):
    from app.scenarios.session_state import dump_session

    tampered = replace(
        halfway(snapshot_scenario),
        scores=ScoreState({LOYALTY: 999, SAFETY: 60, COMMUNICATION: 3}),
    )
    with pytest.raises(ValueError):
        dump_session(snapshot_scenario, tampered)


def test_loading_completed_snapshot_requires_exact_completion_time(snapshot_scenario):
    from app.scenarios.session_state import dump_session, load_session

    completed = finish(snapshot_scenario, halfway(snapshot_scenario))
    payload = json.loads(dump_session(snapshot_scenario, completed))
    payload["completed_at"] = "2026-09-26T09:00:05Z"
    with pytest.raises(ValueError):
        load_session(snapshot_scenario, json.dumps(payload))
