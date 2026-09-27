from pathlib import Path

import pytest

from app.domain import training as t
from app.domain.common import DomainError
from app.training.schema import load_definition


@pytest.fixture
def definition():
    return load_definition(
        Path(__file__).parents[3] / "scenarios/training/demo-v2.json"
    ).to_domain("work")


def test_duration_cancel_and_deadline_wins(definition):
    state = t.start(definition, 42)
    state = t.act(definition, state, action_id="take:radio")
    assert not state.world.equipment[0].carried
    assert state.pending.completes_at_seconds == 4
    cancelled = t.act(definition, t.advance(definition, state, 3), action_id="cancel")
    assert cancelled.pending is None and not cancelled.world.equipment[0].carried
    assert "Частичный эффект отсутствует" in cancelled.world.journal[-1].explanation
    completed = t.advance(definition, state, 4)
    assert completed.pending is None and completed.world.equipment[0].carried
    with pytest.raises(DomainError):
        t.act(definition, completed, action_id="cancel")


def test_parallel_escalation_and_replay_pending(definition):
    state = t.advance(definition, t.start(definition, 42), 55)
    state = t.act(definition, state, action_id="move", zone_id="vestibule")
    end = t.advance(definition, state, 1200)
    historical = t.replay(definition, end, 60)
    assert historical.pending is not None and historical.world.location == "service"
    assert any(
        e.kind == "escalation" and e.at_seconds == 60 for e in historical.world.journal
    )
    assert len(t.view(definition, historical)["incidents"]) == 1
    assert t.restore(definition, t.dump(end)) == end
    forged = t.dump(end)
    forged["world"]["metrics"][0][1] = 99
    with pytest.raises(DomainError):
        t.restore(definition, forged)


@pytest.mark.parametrize(
    "mode,duration",
    [("work", 1200), ("tutorial", 180), ("demo", 240), ("practice", 180)],
)
def test_modes_have_authored_schedule(mode, duration):
    document = load_definition(
        Path(__file__).parents[3] / "scenarios/training/demo-v2.json"
    )
    definition = document.to_domain(mode, "safety" if mode == "practice" else None)
    state = t.start(definition, 42)
    assert state.world.duration == duration
    assert all(i.reported_at_seconds < duration for i in state.world.incidents)
    assert t.advance(definition, state, duration).world.status == "completed"


def test_assessment_is_observable_and_unmeasured_is_null(definition):
    state = t.advance(definition, t.start(definition, 42), 1200)
    evidence = t.assessment(definition, state)["criteria"]
    assert {e["competency_id"] for e in evidence} == set(t.v1.METRICS)
    assert all(e["met"] is False for e in evidence)
    assert all(e["evidence_event_ids"] for e in evidence)


def _perform(definition, state, action, incident=None, zone=None):
    started = t.act(
        definition, state, action_id=action, incident_id=incident, zone_id=zone
    )
    return t.advance(definition, started, started.pending.completes_at_seconds)


def test_confirmed_service_help_is_achievable_with_dialogue_and_record():
    doc = load_definition(Path(__file__).parents[3] / "scenarios/training/demo-v2.json")
    definition = doc.to_domain("tutorial")
    state = t.start(definition, 42)
    zone = state.world.incidents[0].zone_id
    state = _perform(definition, state, "move", zone=zone)
    for action in ["inspect", "talk", "verify", "assist"]:
        state = _perform(definition, state, action, "request")
    assert state.world.incidents[0].status == "handled"
    assert "Пассажир:" in t.view(definition, state)["incidents"][0]["observation"]
    assert (
        state.world.incidents[0].cause
        not in t.view(definition, state)["incidents"][0]["facts"]
    )
    state = t.advance(definition, state, state.elapsed + 20)
    state = _perform(definition, state, "record", "request")
    done = t.advance(definition, state, 180)
    assert done.world.incidents[0].recorded
    evidence = t.assessment(definition, done)["criteria"]
    assert next(e for e in evidence if e["competency_id"] == "safety")["met"] is None
    assert all(e["met"] is True for e in evidence if e["competency_id"] != "safety")


def test_completion_at_run_deadline_precedes_finish(definition):
    state = t.advance(definition, t.start(definition, 42), 1196)
    state = t.act(definition, state, action_id="take:radio")
    done = t.advance(definition, state, 1200)
    assert done.world.equipment[0].carried and done.pending is None
    assert done.world.journal[-1].kind == "completed"
    assert t.restore(definition, t.dump(done)) == done


def test_authored_effects_apply_once_and_are_strict():
    from pydantic import ValidationError

    from app.training.schema import DefinitionDocument

    doc = load_definition(Path(__file__).parents[3] / "scenarios/training/demo-v2.json")
    payload = doc.model_dump()
    payload["completion_effects"]["service"] = {"service": 20, "safety": -20}
    definition = DefinitionDocument.model_validate(payload).to_domain("tutorial")
    state = t.start(definition, 42)
    state = _perform(definition, state, "move", zone=state.world.incidents[0].zone_id)
    for action in ["inspect", "verify", "assist"]:
        state = _perform(definition, state, action, "request")
    authored = [e for e in state.world.journal if e.kind == "authored_consequence"]
    assert len(authored) == 1 and {
        c.metric: c.delta for c in authored[0].metric_changes
    } == {"safety": -20, "service": 20}
    assert t.restore(definition, t.dump(state)) == state
    payload["completion_effects"]["service"]["service"] = 21
    with pytest.raises(ValidationError):
        DefinitionDocument.model_validate(payload)


def test_replay_all_seconds_seed_reproducible_and_no_future_disclosure(definition):
    state = t.start(definition, 42)
    state = _perform(definition, state, "take:radio")
    state = t.act(definition, state, action_id="move", zone_id="vestibule")
    final = t.advance(definition, state, 1200)
    for second in [0, 3, 4, 5, 11, 12, 60, 79, 80, 399, 400, 1199, 1200]:
        historical = t.replay(definition, final, second)
        assert historical == t.restore(definition, t.dump(historical))
        value = t.view(definition, historical)
        assert all(i["reported_at_seconds"] <= second for i in value["incidents"])
        assert all(e["at_seconds"] <= second for e in value["journal"])
        assert not any(k in value for k in ["seed", "consequences", "commands"])


def test_practice_requires_known_competency_and_content_rejects_unknown_fields():
    from pydantic import ValidationError

    from app.training.schema import DefinitionDocument

    doc = load_definition(Path(__file__).parents[3] / "scenarios/training/demo-v2.json")
    with pytest.raises(ValueError):
        doc.to_domain("practice")
    with pytest.raises(ValueError):
        doc.to_domain("work", "safety")
    payload = doc.model_dump()
    payload["script"] = "eval(42)"
    with pytest.raises(ValidationError):
        DefinitionDocument.model_validate(payload)


def test_failed_assessment_explains_missing_observed_results(definition):
    done = t.advance(definition, t.start(definition, 42), 1200)
    evidence = t.assessment(definition, done)["criteria"]
    assert all(
        "Критерий:" in e["explanation"]
        and "Выполнено 0 из" in e["explanation"]
        and "Не подтверждено:" in e["explanation"]
        for e in evidence
    )
    journal_ids = {e.id for e in done.world.journal}
    assert all(set(e["evidence_event_ids"]) <= journal_ids for e in evidence)


def test_defer_does_not_count_as_timely_meaningful_reaction(definition):
    state = t.start(definition, 42)
    state = _perform(definition, state, "move", zone=state.world.incidents[0].zone_id)
    state = _perform(definition, state, "defer", "request")
    criteria = t.assessment(definition, t.advance(definition, state, 1200))["criteria"]
    assert (
        next(c for c in criteria if c["competency_id"] == "prioritization")["met"]
        is False
    )


def test_authored_consequences_clamp_and_restore():
    from app.training.schema import DefinitionDocument

    doc = load_definition(Path(__file__).parents[3] / "scenarios/training/demo-v2.json")
    payload = doc.model_dump()
    payload["completion_effects"]["service"] = {"service": 20}
    payload["completion_effects"]["conflict"] = {"service": 20}
    payload["modes"]["demo"]["incident_seconds"] = {"request": 0, "dispute": 0}
    definition = DefinitionDocument.model_validate(payload).to_domain("demo")
    state = t.start(definition, 42)
    for identity in ["request", "dispute"]:
        zone = next(i.zone_id for i in state.world.incidents if i.id == identity)
        if zone != state.world.location:
            state = _perform(definition, state, "move", zone=zone)
        for action in ["inspect", "talk", "verify", "assist"]:
            state = _perform(definition, state, action, identity)
        state = t.advance(definition, state, state.elapsed + 20)
    authored = [e for e in state.world.journal if e.kind == "authored_consequence"]
    assert len(authored) == 2
    assert any(
        c.metric == "service" and c.delta < 20 and c.after == 100
        for e in authored
        for c in e.metric_changes
    )
    assert t.restore(definition, t.dump(state)) == state


def test_snapshot_rejects_equal_looking_noninteger_version(definition):
    payload = t.dump(t.start(definition, 42))
    payload["engine_version"] = 2.0
    with pytest.raises(DomainError):
        t.restore(definition, payload)
