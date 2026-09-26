from dataclasses import replace

import pytest

from app.domain.common import DomainError
from app.domain.rules import Condition, Operator, Predicate
from app.domain.scenario import Choice, Scenario, ScenarioNode
from app.domain.scoring import AddScore, Metric, MetricRef


def simple_scenario():
    return Scenario(
        "boarding",
        1,
        "Посадка",
        "start",
        (
            ScenarioNode(
                "start", "Вопрос", choices=(Choice("continue", "Далее", "end"),)
            ),
            ScenarioNode("end", "Завершение", terminal=True),
        ),
    )


def test_new_branch_is_only_new_graph_data():
    original = simple_scenario()
    alternative = ScenarioNode(
        "alternative", "Другая развилка", choices=(Choice("finish", "Готово", "end"),)
    )
    start = replace(
        original.nodes[0],
        choices=original.nodes[0].choices + (Choice("other", "Иначе", "alternative"),),
    )
    changed = replace(
        original, version=2, nodes=(start, alternative, original.nodes[1])
    )
    assert changed.node("start").choices[1].target_node_id == "alternative"
    assert original.version == 1
    assert len(original.nodes) == 2


def test_graph_and_collection_are_immutable_snapshots():
    source = list(simple_scenario().nodes)
    scenario = replace(simple_scenario(), nodes=source)
    source.clear()
    assert len(scenario.nodes) == 2
    with pytest.raises(DomainError):
        scenario.node("unknown")


def test_invalid_graph_references_and_duplicate_nodes_are_rejected():
    scenario = simple_scenario()
    with pytest.raises(DomainError):
        replace(scenario, start_node_id="missing")
    with pytest.raises(DomainError):
        replace(scenario, nodes=scenario.nodes + (scenario.nodes[0],))
    bad_start = replace(scenario.nodes[0], choices=(Choice("bad", "Bad", "missing"),))
    with pytest.raises(DomainError):
        replace(scenario, nodes=(bad_start, scenario.nodes[1]))
    with pytest.raises(DomainError):
        replace(
            scenario,
            nodes=scenario.nodes + (ScenarioNode("orphan", "No entry", terminal=True),),
        )


def test_cycles_require_a_structural_exit():
    scenario = simple_scenario()
    loop = Choice("loop", "Повторить", "start")
    with pytest.raises(DomainError):
        replace(scenario, nodes=(replace(scenario.nodes[0], choices=(loop,)),))
    with_exit = replace(scenario.nodes[0], choices=scenario.nodes[0].choices + (loop,))
    assert (
        replace(scenario, nodes=(with_exit, scenario.nodes[1])).start_node_id == "start"
    )


def test_node_shape_choice_ids_and_timers():
    choice = Choice("continue", "Далее", "end")
    for changes in (
        {"choices": ()},
        {"choices": (choice, choice)},
        {"terminal": True, "choices": (choice,)},
        {"time_limit_seconds": 0},
        {"time_limit_seconds": 5},
        {"timeout_choice_id": "continue"},
        {"time_limit_seconds": 5, "timeout_choice_id": "missing"},
    ):
        with pytest.raises(DomainError):
            replace(simple_scenario().nodes[0], **changes)
    node = replace(
        simple_scenario().nodes[0], time_limit_seconds=5, timeout_choice_id="continue"
    )
    assert node.timeout_choice_id == "continue"
    conditional = replace(
        choice,
        condition=Condition(
            predicates=(Predicate(MetricRef(Metric.SAFETY_RATING), Operator.GT, 1),)
        ),
    )
    with pytest.raises(DomainError):
        replace(node, choices=(conditional,))


def test_scenario_rejects_undeclared_competencies():
    scenario = simple_scenario()
    metric = MetricRef(Metric.COMPETENCY, "service")
    for choice in (
        Choice("next", "Next", "end", effects=(AddScore(metric, 1),)),
        Choice(
            "next",
            "Next",
            "end",
            condition=Condition(predicates=(Predicate(metric, Operator.GTE, 1),)),
        ),
    ):
        start = replace(scenario.nodes[0], choices=(choice,))
        with pytest.raises(DomainError):
            replace(scenario, nodes=(start, scenario.nodes[1]))
        valid = replace(
            scenario, nodes=(start, scenario.nodes[1]), competency_ids=("service",)
        )
        assert valid.competency_ids == ("service",)


@pytest.mark.parametrize("version", [0, -1, True])
def test_scenario_version_must_be_positive_integer(version):
    with pytest.raises(DomainError):
        replace(simple_scenario(), version=version)
