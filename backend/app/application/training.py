"""Server-authoritative training transactions and separate durable settlement."""

import json
import logging
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from secrets import randbelow
from typing import Any
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.application.anti_cheat import database_time, record_audit
from app.application.errors import UseCaseError
from app.application.simulations import SimulationService
from app.domain import simulation as v1
from app.domain.common import DomainError, require_integer, utc_time
from app.domain.debrief import debrief_for
from app.domain.events import DomainEvent, bounded_text
from app.domain.reward_ledger import EventState, RewardGrant, apply_event
from app.domain.training import (
    COMPETENCIES,
    TrainingDefinition,
    TrainingState,
    act,
    advance,
    assessment,
    debrief,
    replay,
    start,
    view,
    xp_for,
)
from app.domain.training import (
    TrainingDefinition as Definition,
)
from app.domain.training import dump as dump_state
from app.domain.training import restore as restore_state
from app.persistence.identity import UserProfile
from app.persistence.sessions import SessionRepository, StoredSession
from app.persistence.simulations import StoredSimulation
from app.persistence.training import (
    StoredTraining,
    TrainingCommand,
    TrainingReward,
)
from app.training.schema import DefinitionDocument, load_definition

logger = logging.getLogger(__name__)


def definition_path() -> Path:
    return Path(
        os.environ.get(
            "TRAINING_DEFINITION",
            "/scenarios/training/demo-v2.json"
            if Path("/scenarios").exists()
            else str(Path(__file__).parents[3] / "scenarios/training/demo-v2.json"),
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
                "training:v2",
            )
            if self.xp
            else None
        )


class TrainingService:
    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[Session], datetime] = database_time,
        seed_factory: Callable[[], int] = lambda: randbelow(2147483648),
    ) -> None:
        self.engine, self.clock, self.seed_factory = engine, clock, seed_factory

    def start(
        self,
        employee_id: str,
        *,
        key: str,
        mode: str = "work",
        competency_id: str | None = None,
        assignment_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        from app.application.training_staff import TrainingStaffService, require_role

        require_role(employee_id, "employee")
        staff = TrainingStaffService(self.engine, clock=self.clock)
        if assignment_id is not None:
            return staff.start_assignment(
                employee_id,
                key=key,
                assignment_id=assignment_id,
                mode=mode,
                competency_id=competency_id,
                training=self,
            )
        return self.start_pinned(
            employee_id,
            key=key,
            mode=mode,
            competency_id=competency_id,
            document=staff.published(),
        )

    def start_pinned(
        self,
        employee_id: str,
        *,
        key: str,
        mode: str,
        document: DefinitionDocument,
        competency_id: str | None = None,
        assignment_id: str | None = None,
        seed: int | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Trusted internal input only; HTTP clients cannot submit content or seed."""
        bounded_text(key, "idempotency key")
        try:
            definition = document.to_domain(mode, competency_id)
        except ValueError as exc:
            raise UseCaseError("training_invalid_mode", str(exc)) from exc
        fingerprint = digest(
            dict(
                mode=mode,
                competency_id=competency_id,
                assignment_id=assignment_id,
                source_id=None,
            )
        )
        with Session(self.engine) as db, db.begin():
            owner = db.scalar(
                select(UserProfile)
                .where(UserProfile.id == employee_id)
                .with_for_update(key_share=True)
            )
            if owner is None:
                raise UseCaseError("unauthorized", "Profile unavailable")
            existing = db.scalar(
                select(StoredTraining).where(
                    StoredTraining.employee_id == employee_id,
                    StoredTraining.start_key == key,
                )
            )
            now = utc_time(self.clock(db), "server time")
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise UseCaseError(
                        "idempotency_conflict", "Start key has different arguments"
                    )
                identity, duplicate = existing.id, True
            else:
                if db.scalar(
                    select(StoredTraining.id).where(
                        StoredTraining.employee_id == employee_id,
                        StoredTraining.status == "active",
                    )
                ):
                    raise UseCaseError(
                        "training_active", "A training run is already active"
                    )
                state = start(definition, self.seed_factory() if seed is None else seed)
                identity, duplicate = str(uuid4()), False
                db.add(
                    StoredTraining(
                        id=identity,
                        employee_id=employee_id,
                        start_key=key,
                        request_fingerprint=fingerprint,
                        mode=mode,
                        competency_id=competency_id,
                        assignment_id=assignment_id,
                        source_id=None,
                        source_at_seconds=None,
                        reward_eligible=mode == "work",
                        duration_seconds=state.duration,
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
                action="training_start",
                outcome="duplicate" if duplicate else "accepted",
                server_time=now,
                client_event_id=key,
                details={"training_id": identity, "mode": mode},
            )
        return self.get(identity, employee_id), duplicate

    @staticmethod
    def _owned(db: Session, identity: str, employee_id: str) -> StoredTraining:
        bounded_text(identity, "training_id")
        row = db.scalar(
            select(StoredTraining)
            .where(
                StoredTraining.id == identity,
                StoredTraining.employee_id == employee_id,
            )
            .with_for_update()
        )
        if row is None:
            raise UseCaseError("training_not_found", "Operational shift not found")
        return row

    @staticmethod
    def _load(row: StoredTraining) -> tuple[Definition, TrainingState]:
        if digest(row.definition) != row.definition_hash:
            raise DomainError("Pinned training definition changed")
        definition = DefinitionDocument.model_validate(row.definition).to_domain(
            row.mode, row.competency_id
        )
        state = restore_state(definition, row.snapshot)
        if (state.status, state.revision, state.elapsed) != (
            row.status,
            row.revision,
            row.elapsed_seconds,
        ):
            raise DomainError("Training columns disagree with verified snapshot")
        return definition, state

    @staticmethod
    def _save(
        db: Session, row: StoredTraining, state: TrainingState, now: datetime
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
        row: StoredTraining,
        definition: Definition,
        state: TrainingState,
        now: datetime,
    ) -> None:
        if (
            not row.reward_eligible
            or state.status != "completed"
            or db.get(TrainingReward, row.id) is not None
        ):
            return
        db.scalar(
            select(UserProfile)
            .where(UserProfile.id == row.employee_id)
            .with_for_update(key_share=True)
        )
        event_id = uuid5(UUID(row.id), "training:completed:v2")
        event = DomainEvent(
            event_id,
            "training.completed",
            row.started_at + timedelta(seconds=state.duration),
            now,
            "training-server",
            row.employee_id,
            {
                "training_id": row.id,
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
            TrainingReward(
                training_id=row.id,
                employee_id=row.employee_id,
                event_id=str(event_id),
                event=event_json,
                transaction=json.loads(json.dumps(asdict(transaction), default=str))
                if transaction
                else None,
                xp=xp_for(state),
                rule_version=2,
                achievements=debrief(definition, state)["achievements"],
                awarded_at=now,
            )
        )
        db.flush()

    def _tick(
        self, db: Session, row: StoredTraining
    ) -> tuple[Definition, TrainingState, datetime]:
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
                    action="training_tick",
                    outcome="accepted",
                    server_time=now,
                    previous_revision=state.revision,
                    resulting_revision=updated.revision,
                    details={
                        "training_id": row.id,
                        "elapsed_seconds": updated.elapsed,
                    },
                )
        self._settle(db, row, definition, updated, now)
        return definition, updated, now

    @staticmethod
    def _view(
        row: StoredTraining,
        definition: Definition,
        state: TrainingState,
        now: datetime,
    ) -> dict[str, Any]:
        return dict(
            view(definition, state),
            id=row.id,
            server_time=now,
            started_at=row.started_at,
            source_id=row.source_id,
            assignment_id=row.assignment_id,
            reward_eligible=row.reward_eligible,
            replay=False,
            xp=xp_for(state) if row.reward_eligible else 0,
        )

    def get(self, identity: str, employee_id: str) -> dict[str, Any]:
        with Session(self.engine) as db, db.begin():
            row = self._owned(db, identity, employee_id)
            definition, state, now = self._tick(db, row)
            return self._view(row, definition, state, now)

    def current(self, employee_id: str) -> dict[str, Any] | None:
        with Session(self.engine) as db:
            identity = db.scalar(
                select(StoredTraining.id)
                .where(StoredTraining.employee_id == employee_id)
                .order_by(
                    (StoredTraining.status == "active").desc(),
                    StoredTraining.started_at.desc(),
                    StoredTraining.id.desc(),
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
            receipt = db.get(TrainingCommand, (identity, command_id))
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
                    "training_revision_conflict",
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
                    failure = UseCaseError("training_action_unavailable", str(error))
                if failure is None:
                    self._save(db, row, state, now)
                    db.add(
                        TrainingCommand(
                            training_id=row.id,
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
                action="training_action",
                outcome=(
                    "rejected"
                    if failure.code == "training_action_unavailable"
                    else "conflicting"
                )
                if failure
                else outcome,
                server_time=now,
                client_event_id=command_id,
                previous_revision=before_revision,
                resulting_revision=state.revision,
                details={
                    "training_id": row.id,
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
        if not result["simulation"]["reward_eligible"]:
            result["achievements"] = []
        return result

    def sweep(self, limit: int = 100) -> int:
        with Session(self.engine) as db:
            identities = list(
                db.scalars(
                    select(StoredTraining.id)
                    .where(StoredTraining.status == "active")
                    .order_by(StoredTraining.updated_at, StoredTraining.id)
                    .limit(limit)
                )
            )
        changed = 0
        for identity in identities:
            try:
                with Session(self.engine) as db, db.begin():
                    row = db.scalar(
                        select(StoredTraining)
                        .where(
                            StoredTraining.id == identity,
                            StoredTraining.status == "active",
                        )
                        .with_for_update(skip_locked=True)
                    )
                    if row is not None:
                        previous = row.revision
                        _, state, _ = self._tick(db, row)
                        changed += int(previous != state.revision)
            except DomainError:
                logger.error("Invalid operational training skipped by timer")
        return changed

    def history(
        self, employee_id: str, limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        require_integer(limit, "limit")
        require_integer(offset, "offset")
        if not 1 <= limit <= 100 or not 0 <= offset <= 100000:
            raise DomainError("Invalid history pagination")
        with Session(self.engine) as db:
            query = select(StoredTraining).where(
                StoredTraining.employee_id == employee_id
            )
            total = db.scalar(
                select(func.count())
                .select_from(StoredTraining)
                .where(StoredTraining.employee_id == employee_id)
            )
            rows = list(
                db.scalars(
                    query.order_by(
                        StoredTraining.started_at.desc(), StoredTraining.id.desc()
                    )
                    .limit(limit)
                    .offset(offset)
                )
            )
            return dict(
                items=[
                    dict(
                        id=r.id,
                        title=r.definition["title"],
                        mode=r.mode,
                        status=r.status,
                        started_at=r.started_at,
                        source_id=r.source_id,
                    )
                    for r in rows
                ],
                total=total,
                limit=limit,
                offset=offset,
            )

    def replay(
        self, identity: str, employee_id: str, at_seconds: int
    ) -> dict[str, Any]:
        require_integer(at_seconds, "at_seconds")
        with Session(self.engine) as db, db.begin():
            row = self._owned(db, identity, employee_id)
            definition, state = self._load(row)
            if state.status != "completed":
                raise UseCaseError(
                    "result_not_ready", "Replay requires a completed run"
                )
            historical = replay(definition, state, at_seconds)
            result = self._view(
                row, definition, historical, utc_time(self.clock(db), "server time")
            )
            result["replay"] = True
            result["reward_eligible"] = False
            result["xp"] = 0
            return result

    def fork(
        self, identity: str, employee_id: str, *, key: str, at_seconds: int
    ) -> tuple[dict[str, Any], bool]:
        bounded_text(key, "idempotency key")
        require_integer(at_seconds, "at_seconds")
        fingerprint = digest(dict(source_id=identity, at_seconds=at_seconds))
        with Session(self.engine) as db, db.begin():
            source = self._owned(db, identity, employee_id)
            definition, state = self._load(source)
            if state.status != "completed":
                raise UseCaseError(
                    "result_not_ready",
                    "Alternative attempts require a completed source",
                )
            if not 0 <= at_seconds < state.duration:
                raise DomainError("Alternative checkpoint must precede completion")
            db.scalar(
                select(UserProfile)
                .where(UserProfile.id == employee_id)
                .with_for_update(key_share=True)
            )
            existing = db.scalar(
                select(StoredTraining).where(
                    StoredTraining.employee_id == employee_id,
                    StoredTraining.start_key == key,
                )
            )
            now = utc_time(self.clock(db), "server time")
            if existing:
                if existing.request_fingerprint != fingerprint:
                    raise UseCaseError(
                        "idempotency_conflict", "Fork key has different arguments"
                    )
                new_id, duplicate = existing.id, True
            else:
                if db.scalar(
                    select(StoredTraining.id).where(
                        StoredTraining.employee_id == employee_id,
                        StoredTraining.status == "active",
                    )
                ):
                    raise UseCaseError(
                        "training_active", "A training run is already active"
                    )
                historical = replay(definition, state, at_seconds)
                new_id, duplicate = str(uuid4()), False
                db.add(
                    StoredTraining(
                        id=new_id,
                        employee_id=employee_id,
                        start_key=key,
                        request_fingerprint=fingerprint,
                        mode=source.mode,
                        competency_id=source.competency_id,
                        assignment_id=None,
                        source_id=source.id,
                        source_at_seconds=at_seconds,
                        reward_eligible=False,
                        duration_seconds=historical.duration,
                        definition=source.definition,
                        definition_hash=source.definition_hash,
                        snapshot=dump_state(historical),
                        status=historical.status,
                        revision=historical.revision,
                        elapsed_seconds=historical.elapsed,
                        started_at=now - timedelta(seconds=at_seconds),
                        updated_at=now,
                    )
                )
                db.flush()
            record_audit(
                db,
                actor_id=employee_id,
                action="training_fork",
                outcome="duplicate" if duplicate else "accepted",
                server_time=now,
                client_event_id=key,
                details={
                    "training_id": new_id,
                    "source_id": identity,
                    "at_seconds": at_seconds,
                },
            )
        return self.get(new_id, employee_id), duplicate

    def comparison(self, identity: str, employee_id: str) -> dict[str, Any]:
        current = self.get(identity, employee_id)
        if current["status"] != "completed" or current["source_id"] is None:
            raise UseCaseError(
                "result_not_ready", "Comparison requires a completed alternative"
            )
        source = self.get(current["source_id"], employee_id)
        return dict(
            source_id=source["id"],
            source_metrics=source["metrics"],
            current_metrics=current["metrics"],
            differences=[
                dict(metric=k, delta=current["metrics"][k] - source["metrics"][k])
                for k in v1.METRICS
            ],
            source_resolved=sum(i["status"] == "resolved" for i in source["incidents"]),
            current_resolved=sum(
                i["status"] == "resolved" for i in current["incidents"]
            ),
        )

    def learning(self, employee_id: str) -> dict[str, Any]:
        # Each authored case contributes only its latest observed criterion. Repeats,
        # new versions and alternate modes cannot accumulate mastery evidence.
        cases: dict[tuple[str, str, str], tuple[datetime, str, bool]] = {}
        pattern_runs: dict[tuple[datetime, str], set[str]] = {}
        completed_runs = 0
        completed_scenarios = 0
        notice = load_definition(definition_path()).source_notice

        def add_world(
            definition: TrainingDefinition,
            state: TrainingState,
            at: datetime,
            run_id: str,
        ) -> None:
            patterns_for_run = pattern_runs.setdefault((at, run_id), set())
            for incident in state.world.incidents:
                local = replace(
                    state, world=replace(state.world, incidents=(incident,))
                )
                for criterion in assessment(definition, local)["criteria"]:
                    if criterion["met"] is None:
                        continue
                    key = (
                        definition.base.id,
                        incident.id,
                        criterion["competency_id"],
                    )
                    value = (at, run_id, bool(criterion["met"]))
                    if key not in cases or value[:2] > cases[key][:2]:
                        cases[key] = value
                for pattern, occurred in [
                    ("escalation", incident.severity >= 3),
                    ("unresolved", incident.status != "resolved"),
                    ("unexplained", not incident.talked),
                ]:
                    if occurred:
                        patterns_for_run.add(pattern)

        with Session(self.engine) as db:
            for row in db.scalars(
                select(StoredTraining)
                .where(
                    StoredTraining.employee_id == employee_id,
                    StoredTraining.status == "completed",
                    StoredTraining.source_id.is_(None),
                )
                .order_by(StoredTraining.completed_at, StoredTraining.id)
            ):
                definition, state = self._load(row)
                completed_runs += 1
                if row.mode != "demo":
                    add_world(
                        definition, state, row.completed_at or row.updated_at, row.id
                    )
            document = load_definition(definition_path())
            for legacy in db.scalars(
                select(StoredSimulation).where(
                    StoredSimulation.employee_id == employee_id,
                    StoredSimulation.status == "completed",
                )
            ):
                legacy_definition, legacy_state = SimulationService._load(legacy)
                wrapped = replace(document.to_domain("work"), base=legacy_definition)
                add_world(
                    wrapped,
                    TrainingState(legacy_state),
                    legacy.completed_at or legacy.updated_at,
                    legacy.id,
                )
                completed_runs += 1
            repository = SessionRepository(db)
            for session_row in db.scalars(
                select(StoredSession).where(
                    StoredSession.snapshot["employee_id"].astext == employee_id,
                    StoredSession.state == "completed",
                )
            ):
                scenario, session = repository.load(session_row)
                report = debrief_for(scenario, session)
                completed_scenarios += 1
                patterns_for_run = pattern_runs.setdefault(
                    (report.completed_at, session.id), set()
                )
                for decision in report.decisions:
                    patterns_for_run.update(decision.pattern_codes)
                    values = {c.competency_id: c.delta for c in decision.competencies}
                    measured = {
                        c.competency_id for c in decision.competencies if c.delta != 0
                    }
                    measured.update(
                        c.competency_id
                        for alternative in decision.alternatives
                        if alternative.available
                        for c in alternative.competencies
                        if c.delta != 0
                    )
                    if decision.safety.requested_delta or any(
                        a.available and a.safety_delta for a in decision.alternatives
                    ):
                        measured.add("safety")
                        values["safety"] = decision.safety.requested_delta
                    if decision.loyalty.requested_delta or any(
                        a.available and a.loyalty_delta for a in decision.alternatives
                    ):
                        measured.add("service")
                        values["service"] = decision.loyalty.requested_delta
                    for key in measured & set(COMPETENCIES):
                        case = (scenario.id, decision.node_id, key)
                        value = (
                            report.completed_at,
                            session.id,
                            values.get(key, 0) > 0 and not decision.was_timeout,
                        )
                        if case not in cases or value[:2] > cases[case][:2]:
                            cases[case] = value
        competencies: list[dict[str, Any]] = []
        for key, title in COMPETENCIES.items():
            observed_values = [v[2] for case, v in cases.items() if case[2] == key]
            score = (
                round(100 * sum(observed_values) / len(observed_values), 1)
                if observed_values
                else None
            )
            enough = len(observed_values) >= 3
            strong = enough and score is not None and score >= 80
            competencies.append(
                dict(
                    id=key,
                    title=title,
                    score=score,
                    evidence_count=len(observed_values),
                    status="steady"
                    if strong
                    else "developing"
                    if enough
                    else "insufficient",
                    strong=strong,
                )
            )
        descriptions = {
            "escalation": (
                "Поздняя реакция",
                (
                    "Не менее одного обращения достигло выраженного ухудшения. "
                    "Практикуйте порядок срочных действий."
                ),
            ),
            "unresolved": (
                "Незавершённый контроль",
                (
                    "По обращению не подтверждён результат. Планируйте время на "
                    "помощь и проверку результата."
                ),
            ),
            "unexplained": (
                "Неуточнённая потребность",
                (
                    "Разговор с участником ситуации не завершён. Уточняйте "
                    "потребность и следующий шаг."
                ),
            ),
        }
        descriptions.update(
            {
                "timeout": (
                    "Пропущенный срок решения",
                    "Оцените срочность и доступные действия до окончания срока.",
                ),
                "safety_loss": (
                    "Снижение безопасности",
                    "Сравните наблюдаемые риски выбранного действия и альтернатив.",
                ),
                "loyalty_loss": (
                    "Снижение сервиса",
                    "Разберите объяснение действия и доступную помощь пассажиру.",
                ),
                "competency_regression": (
                    "Отрицательное свидетельство навыка",
                    "Проверьте последствия решения в разборе сценария.",
                ),
            }
        )
        recent_patterns = [pattern_runs[key] for key in sorted(pattern_runs)[-5:]]
        patterns: list[dict[str, Any]] = []
        for key, (title, explanation) in descriptions.items():
            count = sum(key in found for found in recent_patterns)
            if count:
                recurrence = (
                    f"Повторяется в {count} из последних "
                    f"{len(recent_patterns)} прогонов. "
                    if count >= 2
                    else "Единичный сигнал в последних "
                    f"{len(recent_patterns)} прогонах. "
                )
                patterns.append(
                    dict(
                        id=key,
                        title=title,
                        count=count,
                        explanation=recurrence + explanation,
                    )
                )
        recommendations = [
            dict(
                competency_id=c["id"],
                title="Практика: " + c["title"],
                explanation="Недостаточно разных наблюдаемых ситуаций."
                if c["status"] == "insufficient"
                else "Разберите невыполненные критерии и повторите целевую практику.",
            )
            for c in sorted(
                competencies,
                key=lambda c: (c["score"] if c["score"] is not None else -1, c["id"]),
            )
            if not c["strong"]
        ][:3]
        return dict(
            competencies=competencies,
            patterns=[p for p in patterns if p["count"]],
            recommendations=recommendations,
            statistics=dict(
                completed_runs=completed_runs, completed_scenarios=completed_scenarios
            ),
            source_notice=notice
            + (
                " Повтор одного кейса заменяет свидетельство, а не увеличивает их"
                " число; учебная устойчивость требует не менее трёх разных "
                "наблюдаемых ситуаций и 80% выполненных критериев."
            ),
        )
