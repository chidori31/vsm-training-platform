import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.scoring import Metric, MetricRef, ScoreState, apply_effects


def parse(document):
    from app.scenarios.schema import ScenarioDocument

    return ScenarioDocument.model_validate_json(json.dumps(document))


def test_json_converts_to_independent_domain_with_all_effects(document):
    dto = parse(document)
    scenario = dto.to_domain()
    start = scenario.node("start")
    choice = start.choices[0]
    scores = ScoreState(
        {
            MetricRef(Metric.PASSENGER_LOYALTY): 0,
            MetricRef(Metric.SAFETY_RATING): 0,
            MetricRef(Metric.COMPETENCY, "communication"): 0,
        }
    )
    assert choice.condition.matches(scores)
    result = apply_effects(scores, choice.effects)
    assert [result.value(metric) for metric in scores.values] == [2, 1, 3]
    assert choice.explanation == document["nodes"][0]["choices"][0]["explanation"]
    assert start.time_limit_seconds == 15
    timeout = next(c for c in start.choices if c.id == start.timeout_choice_id)
    assert timeout.target_node_id == "expired"
    assert timeout.condition.matches(scores)
    assert timeout.explanation == document["nodes"][0]["timeout"]["explanation"]
    assert scenario.node("done").terminal
    assert parse(json.loads(dto.model_dump_json())).to_domain() == scenario


@pytest.mark.parametrize("missing", ["start", "done", "expired"])
def test_missing_node_is_rejected(document, missing):
    document["nodes"] = [n for n in document["nodes"] if n["id"] != missing]
    with pytest.raises(ValidationError, match="Unknown.*node"):
        parse(document)


@pytest.mark.parametrize("destination", ["ghost", "", 7, None])
def test_invalid_destination(document, destination):
    document["nodes"][0]["choices"][0]["destination"] = destination
    with pytest.raises(ValidationError):
        parse(document)


def test_cycles_follow_document_policy(document):
    document["nodes"][0]["choices"][0]["destination"] = "start"
    with pytest.raises(ValidationError, match="Cycle"):
        parse(document)
    document["cycle_policy"] = "allow"
    assert (
        parse(document).to_domain().node("start").choices[0].target_node_id == "start"
    )


def test_cycle_policy_checks_timeout_edges(document):
    document["nodes"][2] = {
        "id": "expired",
        "text": "Retry",
        "choices": [
            {
                "id": "retry",
                "text": "Retry",
                "destination": "start",
                "explanation": "Retry.",
            }
        ],
    }
    with pytest.raises(ValidationError, match="Cycle"):
        parse(document)


@pytest.mark.parametrize(
    "effect",
    [
        {"type": "eval", "metric": "safety_rating", "delta": 1},
        {"type": "add_score", "metric": "money", "delta": 1},
        {"type": "add_score", "metric": "safety_rating", "delta": "1"},
        {"type": "add_score", "metric": "safety_rating", "delta": True},
        {"type": "add_score", "metric": "safety_rating", "delta": 1.0},
        {"type": "add_score", "metric": "competency", "delta": 1},
        {
            "type": "add_score",
            "metric": "competency",
            "competency_id": "unknown",
            "delta": 1,
        },
        {
            "type": "add_score",
            "metric": "safety_rating",
            "competency_id": "communication",
            "delta": 1,
        },
    ],
)
def test_invalid_effect(document, effect):
    document["nodes"][0]["choices"][0]["effects"] = [effect]
    with pytest.raises(ValidationError):
        parse(document)


def test_timed_node_requires_timeout_branch(document):
    del document["nodes"][0]["timeout"]
    with pytest.raises(ValidationError, match="timeout"):
        parse(document)


def test_timeout_requires_timer_and_separate_destination(document):
    start = document["nodes"][0]
    del start["time_limit_seconds"]
    with pytest.raises(ValidationError, match="timeout"):
        parse(document)
    start["time_limit_seconds"] = 15
    start["timeout"]["destination"] = "done"
    with pytest.raises(ValidationError, match="separate"):
        parse(document)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("schema_version", 1.0),
        ("version", "1"),
        ("version", 0),
        ("version", 2147483648),
        ("title", "  "),
        ("cycle_policy", "maybe"),
        ("script", "print(1)"),
    ],
)
def test_strict_document_rejects_coercion_and_unknown_fields(document, field, value):
    document[field] = value
    with pytest.raises(ValidationError):
        parse(document)


def test_explanation_is_required_and_choices_must_exist(document):
    del document["nodes"][0]["choices"][0]["explanation"]
    with pytest.raises(ValidationError, match="explanation"):
        parse(document)
    document["nodes"][0]["choices"] = []
    with pytest.raises(ValidationError, match="choices"):
        parse(document)


def test_terminal_cannot_have_timer(document):
    document["nodes"][1]["time_limit_seconds"] = 10
    document["nodes"][1]["timeout"] = {"destination": "expired", "explanation": "End."}
    with pytest.raises(ValidationError, match="Terminal"):
        parse(document)


def test_empty_any_and_unknown_predicate_operator_rejected(document):
    condition = document["nodes"][0]["choices"][0]["condition"]
    condition["predicates"][0]["operator"] = "python"
    with pytest.raises(ValidationError):
        parse(document)
    condition.update(mode="any", predicates=[])
    with pytest.raises(ValidationError, match="ANY"):
        parse(document)


def test_duplicate_json_keys_and_malformed_input_rejected(tmp_path, document):
    from app.scenarios.loader import load_document

    path = tmp_path / "scenario.json"
    path.write_text(
        json.dumps(document).replace('"version": 1', '"version": 1, "version": 2'),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        load_document(path)
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        load_document(path)


def test_all_synthetic_demos_are_valid():
    from app.scenarios.loader import load_document

    paths = sorted((Path(__file__).parents[3] / "scenarios" / "demo").glob("*.json"))
    assert len(paths) == 5
    for path in paths:
        document = load_document(path)
        assert document.id.startswith("demo-")
        assert len(document.nodes) <= 8
        assert document.to_domain().node(document.start_node_id).time_limit_seconds


def test_oversized_file_rejected_before_parsing(tmp_path):
    from app.scenarios.loader import MAX_DOCUMENT_BYTES, load_document

    path = tmp_path / "large.json"
    path.write_bytes(b" " * (MAX_DOCUMENT_BYTES + 1))
    with pytest.raises(ValueError, match="1 MiB"):
        load_document(path)


def test_exported_editor_schema_matches_pydantic():
    from app.scenarios.schema import ScenarioDocument

    path = Path(__file__).parents[3] / "scenarios" / "scenario.schema.json"
    assert (
        json.loads(path.read_text(encoding="utf-8"))
        == ScenarioDocument.model_json_schema()
    )
