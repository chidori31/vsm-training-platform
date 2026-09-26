from dataclasses import asdict
from pathlib import Path

import pytest

from app.domain.common import DomainError
from app.domain.simulation import act, advance, available_actions, start, view
from app.simulation.schema import load_definition

CONTENT = Path(__file__).parents[3] / "scenarios/simulation/demo-v1.json"


@pytest.fixture
def definition():
    return load_definition(CONTENT).to_domain()


def command(d, s, action, incident=None, zone=None):
    return act(d, s, action_id=action, incident_id=incident, zone_id=zone)


def test_seed_controls_people_locations_timing_and_hidden_causes(definition):
    first = start(definition, 42)
    assert first == start(definition, 42)
    assert first != start(definition, 43)
    assert first.duration == 1200
    assert len(first.incidents) >= 5


def test_polling_and_one_catchup_have_identical_state(definition):
    original = start(definition, 42)
    polled = original
    for second in range(1, 1201):
        polled = advance(definition, polled, second)
    assert polled == advance(definition, original, 1200)
    assert polled.status == "completed"
    assert any(j.kind == "escalation" for j in polled.journal)


def test_parallel_events_and_hidden_information(definition):
    state = advance(definition, start(definition, 42), 150)
    visible = view(definition, state)
    assert len(visible["incidents"]) >= 3
    assert len(visible["incidents"]) < len(state.incidents)
    assert all(not i["facts"] for i in visible["incidents"])
    assert "hidden" not in str(visible)
    assert all(i.cause not in str(visible) for i in state.incidents)


def test_noop_tick_does_not_change_revision_and_time_cannot_reverse(definition):
    state = start(definition, 42)
    assert advance(definition, state, 1).revision == state.revision
    with pytest.raises(DomainError):
        advance(definition, advance(definition, state, 5), 4)


def test_investigation_requires_location_and_reveals_only_discovered_facts(definition):
    state = start(definition, 42)
    incident = state.incidents[0]
    with pytest.raises(DomainError):
        command(definition, state, "inspect", incident.id)
    state = command(definition, state, "move", zone=incident.zone_id)
    state = advance(definition, state, 8)
    state = command(definition, state, "inspect", incident.id)
    visible = view(definition, state)["incidents"][0]
    assert visible["status"] == "investigated"
    assert visible["discovered_at_seconds"] == 8
    assert visible["first_reaction_seconds"] == 8
    assert len(visible["facts"]) == 1
    with pytest.raises(DomainError):
        command(definition, state, "assist", incident.id)


def test_equipment_communication_and_delayed_resolution(definition):
    state = start(definition, 42)
    state = command(definition, state, "take:radio")
    state = command(definition, state, "take:flashlight")
    state = advance(definition, state, 100)
    incident = next(i for i in state.incidents if i.kind == "safety")
    state = command(definition, state, "move", zone=incident.zone_id)
    for action in ("inspect", "verify", "restrict", "contact"):
        state = command(definition, state, action, incident.id)
    with pytest.raises(DomainError):
        command(definition, state, "assist", incident.id)
    response = state.communications[0].expected_response_seconds
    state = advance(definition, state, response)
    state = command(definition, state, "assist", incident.id)
    assert next(i for i in state.incidents if i.id == incident.id).status == "handled"
    state = advance(definition, state, response + 20)
    assert next(i for i in state.incidents if i.id == incident.id).status == "resolved"
    assert (
        next(
            i for i in advance(definition, state, 700).incidents if i.id == incident.id
        ).status
        == "resolved"
    )


def test_defer_does_not_suspend_deadline_and_changes_npc(definition):
    state = start(definition, 42)
    incident = state.incidents[0]
    state = command(definition, state, "move", zone=incident.zone_id)
    state = command(definition, state, "defer", incident.id)
    assert state.passengers[0].trust < start(definition, 42).passengers[0].trust
    late = advance(definition, state, 350)
    assert late.incidents[0].status == "critical"
    assert any(j.kind == "consequence" for j in late.journal)


def test_station_task_requires_dwell(definition):
    state = command(definition, start(definition, 42), "take:documents")
    state = advance(definition, state, 700)
    incident = next(i for i in state.incidents if i.kind == "station")
    state = command(definition, state, "move", zone=incident.zone_id)
    for action in ("inspect", "verify"):
        state = command(definition, state, action, incident.id)
    before = next(
        a
        for a in available_actions(definition, state, incident.id)
        if a["id"] == "assist"
    )
    assert not before["enabled"]
    state = advance(definition, state, 760)
    assert next(
        a
        for a in available_actions(definition, state, incident.id)
        if a["id"] == "assist"
    )["enabled"]
    state = command(definition, state, "assist", incident.id)
    assert any(j.kind == "station" for j in state.journal)


def test_deadline_transition_precedes_same_second_command(definition):
    state = start(definition, 42)
    incident = state.incidents[0]
    state = command(definition, state, "move", zone=incident.zone_id)
    state = advance(definition, state, incident.reported_at_seconds + 60)
    assert state.incidents[0].status == "ignored"
    state = command(definition, state, "inspect", incident.id)
    assert state.incidents[0].severity == 2


def test_strict_definition_rejects_unknown_fields_and_bad_reference():
    import json

    from app.simulation.schema import DefinitionDocument

    raw = json.loads(CONTENT.read_text(encoding="utf-8"))
    raw["surprise"] = True
    with pytest.raises(ValueError):
        DefinitionDocument.model_validate(raw)
    del raw["surprise"]
    raw["incidents"][0]["zones"] = ["missing"]
    with pytest.raises(ValueError):
        DefinitionDocument.model_validate(raw)


def test_snapshot_replay_detects_forged_score(definition):
    from app.simulation.snapshot import dump_state, restore_state

    state = advance(definition, start(definition, 42), 160)
    payload = dump_state(state)
    assert restore_state(definition, payload) == state
    payload["metrics"][0][1] = 100
    with pytest.raises(DomainError):
        restore_state(definition, payload)


def test_completed_projection_contains_full_journal_without_future_queue(definition):
    state = advance(definition, start(definition, 42), 1200)
    visible = view(definition, state)
    assert visible["status"] == "completed" and visible["actions"] == []
    assert visible["xp"] == 0 and len(visible["journal"]) > 10
    assert "consequences" in asdict(state) and "consequences" not in visible


def test_service_wait_overlaps_next_event_and_npc_is_shared(definition):
    state = command(definition, start(definition, 42), "take:radio")
    incident = state.incidents[0]
    state = command(definition, state, "move", zone=incident.zone_id)
    for action in ("inspect", "talk", "verify", "contact"):
        state = command(definition, state, action, incident.id)
    state = advance(definition, state, 100)
    assert state.communications[0].status == "pending"
    assert (
        sum(i.status != "scheduled" and i.status != "resolved" for i in state.incidents)
        >= 2
    )
    health = next(i for i in state.incidents if i.kind == "health")
    assert health.passenger_id == incident.passenger_id
    assert (
        "talk"
        in next(p for p in state.passengers if p.id == health.passenger_id).history
    )


def test_prepared_station_task_does_not_penalize_waiting_for_dwell(definition):
    state = command(definition, start(definition, 42), "take:documents")
    state = advance(definition, state, 675)
    incident = next(i for i in state.incidents if i.kind == "station")
    state = command(definition, state, "move", zone=incident.zone_id)
    state = command(definition, state, "inspect", incident.id)
    state = command(definition, state, "verify", incident.id)
    state = advance(definition, state, 760)
    assert next(i for i in state.incidents if i.id == incident.id).severity == 1


def test_schema_rejects_boolean_version_and_wrong_initial_incident():
    import json

    from app.simulation.schema import DefinitionDocument

    raw = json.loads(CONTENT.read_text(encoding="utf-8"))
    raw["schema_version"] = True
    with pytest.raises(ValueError):
        DefinitionDocument.model_validate(raw)
    raw["schema_version"] = 1
    raw["incidents"][0]["kind"] = "health"
    raw["incidents"][3]["kind"] = "service"
    with pytest.raises(ValueError):
        DefinitionDocument.model_validate(raw)


def test_first_reaction_is_absolute_time_but_average_is_delay(definition):
    state = advance(definition, start(definition, 42), 110)
    incident = next(i for i in state.incidents if i.kind == "safety")
    state = command(definition, state, "move", zone=incident.zone_id)
    state = command(definition, state, "inspect", incident.id)
    assert (
        next(i for i in state.incidents if i.id == incident.id).first_reaction_seconds
        == 110
    )
    assert (
        view(definition, state)["metrics"]["average_reaction_seconds"]
        == 110 - incident.reported_at_seconds
    )


def test_all_incidents_have_reachable_success_with_parallel_time(definition):
    state = start(definition, 42)
    for equipment in state.equipment:
        state = command(definition, state, "take:" + equipment.id)
    for elapsed in range(0, 1200, 5):
        state = advance(definition, state, elapsed)
        for incident in state.incidents:
            if incident.status == "scheduled":
                continue
            if state.location != incident.zone_id:
                state = command(definition, state, "move", zone=incident.zone_id)
            for verb in (
                "inspect",
                "talk",
                "verify",
                "restrict",
                "contact",
                "assist",
                "record",
            ):
                available = available_actions(definition, state, incident.id)
                if any(a["id"] == verb and a["enabled"] for a in available):
                    state = command(definition, state, verb, incident.id)
    state = advance(definition, state, 1200)
    assert all(i.status == "resolved" for i in state.incidents)
    assert all(i.severity == 1 for i in state.incidents)
    assert view(definition, state)["xp"] > 0


def test_unrevealed_cause_cannot_leak_through_communication_eta(definition):
    from dataclasses import replace

    state = command(definition, start(definition, 42), "take:radio")
    state = advance(definition, state, 110)
    incident = next(i for i in state.incidents if i.kind == "safety")
    state = command(definition, state, "move", zone=incident.zone_id)
    state = command(definition, state, "inspect", incident.id)
    projections = []
    for cause in next(s for s in definition.incidents if s.id == incident.id).causes:
        variant = replace(
            state,
            incidents=tuple(
                replace(i, cause=cause) if i.id == incident.id else i
                for i in state.incidents
            ),
        )
        variant = command(definition, variant, "contact", incident.id)
        projections.append(view(definition, variant))
    assert projections[0] == projections[1]


def test_command_limit_rejects_before_creating_unrestorable_state(definition):
    from app.simulation.snapshot import dump_state, restore_state

    state = start(definition, 42)
    for index in range(5000):
        state = command(
            definition, state, "move", zone="corridor" if index % 2 == 0 else "service"
        )
    assert restore_state(definition, dump_state(state)) == state
    with pytest.raises(DomainError, match="command limit"):
        command(definition, state, "move", zone="corridor")


def test_future_and_nonexistent_incident_have_identical_public_rejection(definition):
    messages = []
    for identity in ("wellbeing", "nonexistent"):
        with pytest.raises(DomainError) as error:
            command(definition, start(definition, 42), "inspect", identity)
        messages.append(str(error.value))
    assert messages[0] == messages[1]
