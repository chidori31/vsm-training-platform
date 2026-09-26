"""Server-authoritative simulation transactions and separate durable settlement."""

import json
import logging
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from secrets import randbelow
from typing import Any
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.application.anti_cheat import database_time, record_audit
from app.application.errors import UseCaseError
from app.domain.common import DomainError, require_integer, utc_time
from app.domain.events import DomainEvent, bounded_text
from app.domain.reward_ledger import EventState, RewardGrant, apply_event
from app.domain.simulation import (
    Definition,
    SimulationState,
    act,
    advance,
    debrief,
    start,
    view,
    xp_for,
)
from app.persistence.identity import UserProfile
from app.persistence.simulations import (
    SimulationCommand,
    SimulationReward,
    StoredSimulation,
)
from app.simulation.schema import DefinitionDocument, load_definition
from app.simulation.snapshot import dump_state, restore_state

logger = logging.getLogger(__name__)


def definition_path() -> Path:
    return Path(
        os.environ.get(
            "SIMULATION_DEFINITION",
            "/scenarios/simulation/demo-v1.json"
            if Path("/scenarios").exists()
            else str(Path(__file__).parents[3] / "scenarios/simulation/demo-v1.json"),
        )
    )


def digest(value: dict[str, Any]) -> str:
    return sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


@dataclass(frozen=True)
class CompletionRule:
    xp: int

    def evaluate(self, event: DomainEvent) -> RewardGrant | None:
        return (
            RewardGrant(
                self.xp,
                "training_xp",
                "Операционная смена завершена",
                "simulation:v1",
            )
            if self.xp
            else None
        )


class SimulationService:
    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[Session], datetime] = database_time,
        seed_factory: Callable[[], int] = lambda: randbelow(2147483648),
    ) -> None:
        self.engine, self.clock, self.seed_factory = engine, clock, seed_factory

    def start(self, employee_id: str, *, key: str) -> tuple[dict[str, Any], bool]:
        bounded_text(key, "idempotency key")
        with Session(self.engine) as db, db.begin():
            owner = db.scalar(
                select(UserProfile)
                .where(UserProfile.id == employee_id)
                .with_for_update(key_share=True)
            )
            if owner is None:
                raise UseCaseError("unauthorized", "Profile unavailable")
            existing = db.scalar(
                select(StoredSimulation).where(
                    StoredSimulation.employee_id == employee_id,
                    StoredSimulation.start_key == key,
                )
            )
            now = utc_time(self.clock(db), "server time")
            if existing is not None:
                identity, replayed = existing.id, True
            else:
                if db.scalar(
                    select(StoredSimulation.id).where(
                        StoredSimulation.employee_id == employee_id,
                        StoredSimulation.status == "active",
                    )
                ):
                    raise UseCaseError(
                        "simulation_active", "An operational shift is already active"
                    )
                document = load_definition(definition_path())
                state = start(document.to_domain(), self.seed_factory())
                identity, replayed = str(uuid4()), False
                db.add(
                    StoredSimulation(
                        id=identity,
                        employee_id=employee_id,
                        start_key=key,
                        definition=document.model_dump(),
                        definition_hash=digest(document.model_dump()),
                        snapshot=dump_state(state),
                        status=state.status,
                        revision=state.revision,
                        elapsed_seconds=state.elapsed,
                        started_at=now,
                        updated_at=now,
                    )
                )
                db.flush()
            record_audit(
                db,
                actor_id=employee_id,
                action="simulation_start",
                outcome="duplicate" if replayed else "accepted",
                server_time=now,
                client_event_id=key,
                details={"simulation_id": identity},
            )
        # Release profile before locking an existing simulation: completion locks owner.
        return self.get(identity, employee_id), replayed

    @staticmethod
    def _owned(db: Session, identity: str, employee_id: str) -> StoredSimulation:
        bounded_text(identity, "simulation_id")
        row = db.scalar(
            select(StoredSimulation)
            .where(
                StoredSimulation.id == identity,
                StoredSimulation.employee_id == employee_id,
            )
            .with_for_update()
        )
        if row is None:
            raise UseCaseError("simulation_not_found", "Operational shift not found")
        return row

    @staticmethod
    def _load(row: StoredSimulation) -> tuple[Definition, SimulationState]:
        if digest(row.definition) != row.definition_hash:
            raise DomainError("Pinned simulation definition changed")
        definition = DefinitionDocument.model_validate(row.definition).to_domain()
        state = restore_state(definition, row.snapshot)
        if (state.status, state.revision, state.elapsed) != (
            row.status,
            row.revision,
            row.elapsed_seconds,
        ):
            raise DomainError("Simulation columns disagree with verified snapshot")
        return definition, state

    @staticmethod
    def _save(
        db: Session, row: StoredSimulation, state: SimulationState, now: datetime
    ) -> None:
        row.snapshot = dump_state(state)
        row.status, row.revision, row.elapsed_seconds = (
            state.status,
            state.revision,
            state.elapsed,
        )
        row.updated_at = now
        if state.status == "completed":
            row.completed_at = row.started_at + timedelta(seconds=state.duration)
        db.flush()

    @staticmethod
    def _settle(
        db: Session,
        row: StoredSimulation,
        definition: Definition,
        state: SimulationState,
        now: datetime,
    ) -> None:
        if state.status != "completed" or db.get(SimulationReward, row.id) is not None:
            return
        db.scalar(
            select(UserProfile)
            .where(UserProfile.id == row.employee_id)
            .with_for_update(key_share=True)
        )
        event_id = uuid5(UUID(row.id), "simulation:completed:v1")
        event = DomainEvent(
            event_id,
            "simulation.completed",
            row.started_at + timedelta(seconds=state.duration),
            now,
            "simulation-server",
            row.employee_id,
            {
                "simulation_id": row.id,
                "definition_hash": row.definition_hash,
                "revision": state.revision,
                "metrics": dict(state.metrics),
                "competency_deltas": {
                    key: sum(
                        max(0, change.delta)
                        for entry in state.journal
                        for change in entry.metric_changes
                        if change.metric == key
                    )
                    for key in ("regulation", "communication")
                },
                "xp": xp_for(state),
            },
        )
        processed = apply_event(EventState(), event, rule=CompletionRule(xp_for(state)))
        transaction = processed.transactions[0] if processed.transactions else None
        event_json = dict(
            event_id=str(event.event_id),
            event_type=event.event_type,
            occurred_at=event.occurred_at.isoformat(),
            received_at=event.received_at.isoformat(),
            source=event.source,
            subject_id=event.subject_id,
            payload=json.loads(json.dumps(dict(event.payload), default=dict)),
            schema_version=event.schema_version,
            fingerprint=event.fingerprint(),
        )
        db.add(
            SimulationReward(
                simulation_id=row.id,
                employee_id=row.employee_id,
                event_id=str(event_id),
                event=event_json,
                transaction=json.loads(json.dumps(asdict(transaction), default=str))
                if transaction
                else None,
                xp=xp_for(state),
                rule_version=1,
                achievements=debrief(definition, state)["achievements"],
                awarded_at=now,
            )
        )
        db.flush()

    def _tick(
        self, db: Session, row: StoredSimulation
    ) -> tuple[Definition, SimulationState, datetime]:
        definition, state = self._load(row)
        # A separate clock query occurs AFTER row lock; clamp on wall-clock rollback.
        now = max(utc_time(self.clock(db), "server time"), row.updated_at)
        elapsed = max(state.elapsed, int((now - row.started_at).total_seconds()))
        updated = advance(definition, state, elapsed)
        if updated != state:
            self._save(db, row, updated, now)
            if updated.revision != state.revision:
                record_audit(
                    db,
                    actor_id="system:timer",
                    action="simulation_tick",
                    outcome="accepted",
                    server_time=now,
                    previous_revision=state.revision,
                    resulting_revision=updated.revision,
                    details={
                        "simulation_id": row.id,
                        "elapsed_seconds": updated.elapsed,
                    },
                )
        self._settle(db, row, definition, updated, now)
        return definition, updated, now

    @staticmethod
    def _view(
        row: StoredSimulation,
        definition: Definition,
        state: SimulationState,
        now: datetime,
    ) -> dict[str, Any]:
        return dict(
            view(definition, state),
            id=row.id,
            server_time=now,
            started_at=row.started_at,
        )

    def get(self, identity: str, employee_id: str) -> dict[str, Any]:
        with Session(self.engine) as db, db.begin():
            row = self._owned(db, identity, employee_id)
            definition, state, now = self._tick(db, row)
            return self._view(row, definition, state, now)

    def current(self, employee_id: str) -> dict[str, Any] | None:
        with Session(self.engine) as db:
            identity = db.scalar(
                select(StoredSimulation.id)
                .where(StoredSimulation.employee_id == employee_id)
                .order_by(
                    (StoredSimulation.status == "active").desc(),
                    StoredSimulation.started_at.desc(),
                    StoredSimulation.id.desc(),
                )
                .limit(1)
            )
        return self.get(identity, employee_id) if identity else None

    def action(
        self,
        identity: str,
        employee_id: str,
        *,
        command_id: str,
        expected_revision: int,
        action_id: str,
        incident_id: str | None,
        zone_id: str | None,
    ) -> dict[str, Any]:
        for field, value in (
            ("command_id", command_id),
            ("action_id", action_id),
            ("incident_id", incident_id),
            ("zone_id", zone_id),
        ):
            if value is not None:
                bounded_text(value, field)
        require_integer(expected_revision, "expected_revision")
        if expected_revision < 0:
            raise DomainError("Invalid revision")
        fingerprint = digest(
            dict(
                expected_revision=expected_revision,
                action_id=action_id,
                incident_id=incident_id,
                zone_id=zone_id,
            )
        )
        failure = None
        with Session(self.engine) as db, db.begin():
            row = self._owned(db, identity, employee_id)
            definition, state, now = self._tick(db, row)
            before_revision = state.revision
            receipt = db.get(SimulationCommand, (identity, command_id))
            outcome = "accepted"
            if receipt is not None:
                if receipt.fingerprint == fingerprint:
                    outcome = "duplicate"
                else:
                    failure = UseCaseError(
                        "idempotency_conflict",
                        "Command identifier has different arguments",
                    )
            elif state.status != "active" or state.revision != expected_revision:
                failure = UseCaseError(
                    "simulation_revision_conflict",
                    "Refresh the operational shift before acting",
                )
            else:
                try:
                    state = act(
                        definition,
                        state,
                        action_id=action_id,
                        incident_id=incident_id,
                        zone_id=zone_id,
                    )
                except DomainError as error:
                    failure = UseCaseError("simulation_action_unavailable", str(error))
                if failure is None:
                    self._save(db, row, state, now)
                    db.add(
                        SimulationCommand(
                            simulation_id=row.id,
                            command_id=command_id,
                            fingerprint=fingerprint,
                            expected_revision=expected_revision,
                            resulting_revision=state.revision,
                            created_at=now,
                        )
                    )
            record_audit(
                db,
                actor_id=employee_id,
                action="simulation_action",
                outcome=(
                    "rejected"
                    if failure.code == "simulation_action_unavailable"
                    else "conflicting"
                )
                if failure
                else outcome,
                server_time=now,
                client_event_id=command_id,
                previous_revision=before_revision,
                resulting_revision=state.revision,
                details={
                    "simulation_id": row.id,
                    "action_id": action_id,
                    "incident_id": incident_id,
                    "zone_id": zone_id,
                },
            )
            result = self._view(row, definition, state, now)
        # Due time transitions and settlement survive a rejected command.
        if failure is not None:
            failure.audit_recorded = True
            raise failure
        return result

    def debrief(self, identity: str, employee_id: str) -> dict[str, Any]:
        with Session(self.engine) as db, db.begin():
            row = self._owned(db, identity, employee_id)
            definition, state, now = self._tick(db, row)
            result = (
                dict(
                    debrief(definition, state),
                    simulation=self._view(row, definition, state, now),
                )
                if state.status == "completed"
                else None
            )
        if result is None:
            raise UseCaseError(
                "result_not_ready", "Debrief is available after completion"
            )
        return result

    def sweep(self, limit: int = 100) -> int:
        with Session(self.engine) as db:
            identities = list(
                db.scalars(
                    select(StoredSimulation.id)
                    .where(StoredSimulation.status == "active")
                    .order_by(StoredSimulation.updated_at, StoredSimulation.id)
                    .limit(limit)
                )
            )
        changed = 0
        for identity in identities:
            try:
                with Session(self.engine) as db, db.begin():
                    row = db.scalar(
                        select(StoredSimulation)
                        .where(
                            StoredSimulation.id == identity,
                            StoredSimulation.status == "active",
                        )
                        .with_for_update(skip_locked=True)
                    )
                    if row is not None:
                        previous = row.revision
                        _, state, _ = self._tick(db, row)
                        changed += int(previous != state.revision)
            except DomainError:
                logger.error("Invalid operational simulation skipped by timer")
        return changed
