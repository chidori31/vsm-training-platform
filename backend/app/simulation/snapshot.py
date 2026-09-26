"""A stored snapshot is accepted only if server commands replay to the same state."""

import json
from dataclasses import asdict
from typing import Any

from app.domain.common import DomainError
from app.domain.simulation import (
    MAX_COMMANDS,
    Definition,
    SimulationState,
    act,
    advance,
    start,
)


def dump_state(state: SimulationState) -> dict[str, Any]:
    return dict(json.loads(json.dumps(asdict(state), ensure_ascii=False)))


def restore_state(definition: Definition, payload: dict[str, Any]) -> SimulationState:
    try:
        if len(payload.get("commands", [])) > MAX_COMMANDS:
            raise DomainError("Too many simulation commands")
        state = start(definition, payload["seed"])
        for command in payload["commands"]:
            if set(command) != {"at_seconds", "action_id", "incident_id", "zone_id"}:
                raise DomainError("Invalid saved command")
            state = advance(definition, state, command["at_seconds"])
            state = act(
                definition,
                state,
                action_id=command["action_id"],
                incident_id=command["incident_id"],
                zone_id=command["zone_id"],
            )
        state = advance(definition, state, payload["elapsed"])
        if dump_state(state) != payload:
            raise DomainError("Simulation snapshot differs from server replay")
        return state
    except (KeyError, TypeError, ValueError, StopIteration) as error:
        raise DomainError("Invalid simulation snapshot") from error
