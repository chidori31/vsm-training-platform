"""Transactional, owned multi-scenario routes using the existing session engine."""

from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from secrets import randbelow
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.application.anti_cheat import record_audit
from app.application.errors import UseCaseError
from app.application.sessions import SessionService, database_time
from app.domain.common import DomainError, require_integer, utc_time
from app.domain.events import bounded_text
from app.domain.gameplay import ScenarioSession, SessionStatus
from app.domain.scenario import Scenario
from app.domain.shifts import (
    QUALIFICATIONS,
    qualification_ids,
    shift_metrics,
    shift_plan,
    shift_recommendations,
)
from app.persistence.gamification import SessionReward
from app.persistence.identity import UserProfile
from app.persistence.sessions import SessionRepository, StoredSession
from app.persistence.shifts import (
    ShiftAchievement,
    ShiftCommand,
    ShiftStep,
    TrainingShift,
)


def _seed() -> int:
    return randbelow(2147483648)


class ShiftService:
    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[Session], datetime] = database_time,
        seed_factory: Callable[[], int] = _seed,
    ) -> None:
        self.engine = engine
        self.clock = clock
        self.seed_factory = seed_factory
        self.sessions = SessionService(engine, clock=clock)

    def start(
        self, employee_id: str, *, key: str, difficulty: str = "standard"
    ) -> tuple[dict[str, Any], bool]:
        bounded_text(key, "idempotency key")
        shift_plan(0, difficulty)
        with Session(self.engine) as db, db.begin():
            profile = db.scalar(
                select(UserProfile)
                .where(UserProfile.id == employee_id)
                .with_for_update(key_share=True)
            )
            if profile is None:
                raise UseCaseError("unauthorized", "Profile unavailable")
            existing = db.scalar(
                select(TrainingShift).where(
                    TrainingShift.employee_id == employee_id,
                    TrainingShift.start_key == key,
                )
            )
            replayed = existing is not None
            if existing is not None:
                if existing.difficulty != difficulty:
                    raise UseCaseError(
                        "idempotency_conflict", "Start key has different difficulty"
                    )
                shift_id = existing.id
                record_audit(
                    db,
                    actor_id=employee_id,
                    action="shift_start",
                    outcome="duplicate",
                    server_time=utc_time(self.clock(db), "server time"),
                    client_event_id=key,
                    details={
                        "shift_id": shift_id,
                        "resulting_step": existing.current_step,
                    },
                )
            else:
                active = db.scalar(
                    select(TrainingShift.id).where(
                        TrainingShift.employee_id == employee_id,
                        TrainingShift.status == "active",
                    )
                )
                if active is not None:
                    raise UseCaseError(
                        "shift_already_active", "A training shift is already active"
                    )
                seed = self.seed_factory()
                plan = shift_plan(seed, difficulty)
                now = utc_time(self.clock(db), "server time")
                row = TrainingShift(
                    id=str(uuid4()),
                    employee_id=employee_id,
                    start_key=key,
                    route_version=1,
                    seed=seed,
                    difficulty=difficulty,
                    status="active",
                    current_step=0,
                    started_at=now,
                )
                db.add(row)
                db.flush()
                for stage in plan:
                    step = ShiftStep(
                        shift_id=row.id,
                        step_index=stage.index,
                        kind=stage.kind,
                        title=stage.title,
                        scenario_id=stage.scenario_id,
                        scenario_version=stage.scenario_version,
                        passenger_profile=stage.passenger_profile,
                        context=stage.context,
                    )
                    db.add(step)
                    if stage.index == 0:
                        view = self.sessions._start(
                            db,
                            stage.scenario_id,
                            stage.scenario_version,
                            employee_id,
                            client_event_id=key,
                            action="shift_start",
                        )
                        step.session_id = view.session.id
                shift_id = row.id
        # Release the profile start lock before acquiring an existing shift/session
        # lock. Completion awards take session -> profile locks, in that order.
        return self.get(shift_id, employee_id), replayed

    def current(self, employee_id: str) -> dict[str, Any] | None:
        with Session(self.engine) as db:
            shift_id = db.scalar(
                select(TrainingShift.id)
                .where(TrainingShift.employee_id == employee_id)
                .order_by(TrainingShift.started_at.desc(), TrainingShift.id.desc())
                .limit(1)
            )
        return self.get(shift_id, employee_id) if shift_id else None

    @staticmethod
    def _owned(db: Session, shift_id: str, employee_id: str) -> TrainingShift:
        row = db.scalar(
            select(TrainingShift)
            .where(
                TrainingShift.id == shift_id, TrainingShift.employee_id == employee_id
            )
            .with_for_update()
        )
        if row is None:
            raise UseCaseError("shift_not_found", "Training shift not found")
        return row

    @staticmethod
    def _steps(db: Session, row: TrainingShift) -> list[ShiftStep]:
        steps = list(
            db.scalars(
                select(ShiftStep)
                .where(ShiftStep.shift_id == row.id)
                .order_by(ShiftStep.step_index)
            )
        )
        plan = shift_plan(row.seed, row.difficulty)
        if len(steps) != 3 or row.route_version != 1:
            raise DomainError("Invalid saved shift route")
        for step, expected in zip(steps, plan, strict=True):
            if (
                step.step_index,
                step.kind,
                step.scenario_id,
                step.scenario_version,
                step.title,
                step.passenger_profile,
                step.context,
            ) != (
                expected.index,
                expected.kind,
                expected.scenario_id,
                expected.scenario_version,
                expected.title,
                expected.passenger_profile,
                expected.context,
            ):
                raise DomainError("Saved shift route differs from its seed and version")
            if (step.session_id is not None) != (step.step_index <= row.current_step):
                raise DomainError("Saved shift session order is inconsistent")
        return steps

    def _refresh_current(
        self, db: Session, row: TrainingShift, steps: list[ShiftStep]
    ) -> None:
        if row.status != "active":
            return
        step = steps[row.current_step]
        if step.session_id is None:
            raise DomainError("Current shift stage has no session")
        repository = SessionRepository(db)
        stored = repository.lock(step.session_id)
        if stored is None:
            raise DomainError("Shift session is unavailable")
        scenario, session = repository.load(stored)
        if session.employee_id != row.employee_id:
            raise DomainError("Shift session owner mismatch")
        now = utc_time(self.clock(db), "server time")
        self.sessions._expire_due(repository, stored, scenario, session, now)

    def get(self, shift_id: str, employee_id: str) -> dict[str, Any]:
        with Session(self.engine) as db, db.begin():
            row = self._owned(db, shift_id, employee_id)
            steps = self._steps(db, row)
            self._refresh_current(db, row, steps)
            return self._view(db, row, steps)

    @staticmethod
    def _attempts(
        db: Session, row: TrainingShift, steps: list[ShiftStep]
    ) -> list[tuple[Scenario, ScenarioSession]]:
        pairs = []
        repository = SessionRepository(db)
        for step in steps:
            if step.session_id is None:
                continue
            stored = db.get(StoredSession, step.session_id)
            if stored is None:
                raise DomainError("Missing shift session")
            scenario, session = repository.load(stored)
            if session.employee_id != row.employee_id or (
                scenario.id,
                scenario.version,
            ) != (step.scenario_id, step.scenario_version):
                raise DomainError("Linked shift session identity mismatch")
            if (
                step.step_index < row.current_step
                and session.status is not SessionStatus.COMPLETED
            ):
                raise DomainError("A prior shift stage is not completed")
            pairs.append((scenario, session))
        return pairs

    def _view(
        self, db: Session, row: TrainingShift, steps: list[ShiftStep]
    ) -> dict[str, Any]:
        pairs = self._attempts(db, row, steps)
        sessions = {session.id: session for _, session in pairs}
        xp = (
            db.scalar(
                select(func.coalesce(func.sum(SessionReward.xp), 0)).where(
                    SessionReward.session_id.in_(sessions),
                    SessionReward.employee_id == row.employee_id,
                )
            )
            or 0
        )
        metrics = shift_metrics(pairs, xp=xp)
        if row.status == "completed" and row.summary != asdict(metrics):
            raise DomainError("Completed shift summary differs from verified sessions")
        unlocked = set(
            db.scalars(
                select(ShiftAchievement.achievement_id).where(
                    ShiftAchievement.employee_id == row.employee_id
                )
            )
        )
        return {
            "id": row.id,
            "title": "Рабочая смена · учебный маршрут",
            "status": row.status,
            "seed": row.seed,
            "difficulty": row.difficulty,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
            "current_step": row.current_step,
            "current_session_id": steps[row.current_step].session_id
            if row.status == "active"
            else None,
            "steps": [
                {
                    "index": step.step_index,
                    "kind": step.kind,
                    "title": step.title,
                    "scenario_id": step.scenario_id,
                    "scenario_version": step.scenario_version,
                    "passenger_profile": step.passenger_profile,
                    "context": step.context,
                    "session_id": step.session_id,
                    "status": "locked"
                    if step.session_id is None
                    else "completed"
                    if sessions[step.session_id].status is SessionStatus.COMPLETED
                    else "active",
                }
                for step in steps
            ],
            "metrics": asdict(metrics),
            "achievements": [
                {
                    "id": a.id,
                    "title": a.title,
                    "description": a.description,
                    "unlocked": a.id in unlocked,
                }
                for a in QUALIFICATIONS
            ],
            "recommendations": list(shift_recommendations(metrics)),
        }

    def advance(
        self,
        shift_id: str,
        employee_id: str,
        *,
        command_id: str,
        expected_step: int,
        session_id: str,
    ) -> dict[str, Any]:
        bounded_text(command_id, "command_id")
        bounded_text(session_id, "session_id")
        require_integer(expected_step, "expected_step")
        failure = None
        with Session(self.engine) as db, db.begin():
            row = self._owned(db, shift_id, employee_id)
            steps = self._steps(db, row)
            receipt = db.get(ShiftCommand, (shift_id, command_id))
            if receipt is not None:
                if (receipt.expected_step, receipt.session_id) != (
                    expected_step,
                    session_id,
                ):
                    raise UseCaseError(
                        "idempotency_conflict",
                        "Command id has different shift arguments",
                    )
                self._refresh_current(db, row, steps)
                record_audit(
                    db,
                    actor_id=employee_id,
                    action="shift_advance",
                    outcome="duplicate",
                    server_time=utc_time(self.clock(db), "server time"),
                    client_event_id=command_id,
                    session_id=session_id,
                    details={
                        "shift_id": row.id,
                        "expected_step": expected_step,
                        "resulting_step": row.current_step,
                    },
                    reward={"xp_granted": 0},
                )
                return self._view(db, row, steps)
            if (
                row.status != "active"
                or row.current_step != expected_step
                or steps[row.current_step].session_id != session_id
            ):
                raise UseCaseError(
                    "shift_step_conflict",
                    "Command does not target the current linked stage",
                )
            self._refresh_current(db, row, steps)
            pairs = self._attempts(db, row, steps)
            if pairs[-1][1].status is not SessionStatus.COMPLETED:
                failure = UseCaseError(
                    "shift_step_not_ready",
                    "Complete the current scenario before advancing",
                )
            else:
                now = utc_time(self.clock(db), "server time")
                next_index = row.current_step + 1
                if next_index < 3:
                    step = steps[next_index]
                    view = self.sessions._start(
                        db,
                        step.scenario_id,
                        step.scenario_version,
                        employee_id,
                        client_event_id=command_id,
                        action="shift_advance",
                    )
                    step.session_id = view.session.id
                else:
                    xp = (
                        db.scalar(
                            select(func.coalesce(func.sum(SessionReward.xp), 0)).where(
                                SessionReward.session_id.in_([s.id for _, s in pairs])
                            )
                        )
                        or 0
                    )
                    metrics = shift_metrics(pairs, xp=xp)
                    row.status = "completed"
                    row.completed_at = now
                    row.summary = asdict(metrics)
                row.current_step = next_index
                db.add(
                    ShiftCommand(
                        shift_id=row.id,
                        command_id=command_id,
                        expected_step=expected_step,
                        session_id=session_id,
                        resulting_step=next_index,
                        server_time=now,
                    )
                )
                db.flush()
                if row.status == "completed":
                    self._award(db, row, pairs, now)
                    scenario, _ = pairs[-1]
                    record_audit(
                        db,
                        actor_id=employee_id,
                        action="shift_advance",
                        outcome="accepted",
                        server_time=now,
                        client_event_id=command_id,
                        session_id=session_id,
                        scenario_id=scenario.id,
                        scenario_version=scenario.version,
                        details={
                            "shift_id": row.id,
                            "expected_step": expected_step,
                            "resulting_step": next_index,
                        },
                        reward={"xp_granted": 0},
                    )
            result = self._view(db, row, steps)
        # Automatic timeout is durable even when it leaves a recovery node active.
        if failure is not None:
            raise failure
        return result

    @staticmethod
    def _award(
        db: Session,
        row: TrainingShift,
        pairs: list[tuple[Scenario, ScenarioSession]],
        now: datetime,
    ) -> None:
        db.scalar(
            select(UserProfile)
            .where(UserProfile.id == row.employee_id)
            .with_for_update(key_share=True)
        )
        recent = [
            row,
            *db.scalars(
                select(TrainingShift)
                .where(
                    TrainingShift.employee_id == row.employee_id,
                    TrainingShift.status == "completed",
                    TrainingShift.id != row.id,
                )
                .order_by(TrainingShift.completed_at.desc(), TrainingShift.id.desc())
                .limit(2)
            ),
        ]
        streak = 0
        for finished in recent:
            summary = finished.summary or {}
            if summary.get("critical_errors") != 0 or summary.get("safety", 0) < 50:
                break
            streak += 1
        metrics = shift_metrics(pairs)
        previous = next(
            (finished for finished in recent if finished.id != row.id), None
        )
        previous_competencies = (
            {
                key: int((previous.summary or {}).get(key, 0))
                for key in ("regulation", "communication")
            }
            if previous is not None
            else None
        )
        for key in qualification_ids(
            pairs,
            metrics,
            row.difficulty,
            completed=True,
            safe_streak=streak,
            previous_competencies=previous_competencies,
        ):
            db.execute(
                insert(ShiftAchievement)
                .values(
                    employee_id=row.employee_id,
                    achievement_id=key,
                    shift_id=row.id,
                    awarded_at=now,
                )
                .on_conflict_do_nothing()
            )
        db.flush()
