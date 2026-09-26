"""Internal retention projections and retryable event reconciliation."""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.application.errors import UseCaseError
from app.application.sessions import database_time
from app.domain.retention import Challenge, challenge_progress
from app.persistence.achievements import AchievementRecord, AchievementUnlockRecord
from app.persistence.identity import UserProfile
from app.persistence.retention import (
    ChallengeRecord,
    ChallengeScenario,
    NotificationRecord,
)
from app.persistence.scenarios import ScenarioVersion
from app.persistence.sessions import SessionRepository, StoredSession


def reconcile(database: Session, employee_id: str, now: datetime) -> int:
    inserted = 0

    def emit(
        key: str, kind: str, title: str, body: str, at: datetime, end: datetime
    ) -> None:
        nonlocal inserted
        if not at <= now < end:
            return
        result = database.scalar(
            insert(NotificationRecord)
            .values(
                id=str(uuid4()),
                employee_id=employee_id,
                event_key=key,
                kind=kind,
                title=title,
                body=body,
                created_at=at,
                expires_at=end,
            )
            .on_conflict_do_nothing(constraint="uq_notification_event")
            .returning(NotificationRecord.id)
        )
        inserted += int(result is not None)

    for scenario in database.scalars(
        select(ScenarioVersion)
        .where(ScenarioVersion.created_at > now - timedelta(days=7))
        .order_by(ScenarioVersion.id, ScenarioVersion.version)
    ):
        emit(
            f"scenario:{scenario.id}:{scenario.version}",
            "new_scenario",
            "Новый сценарий",
            str(scenario.document["title"]),
            scenario.created_at,
            scenario.created_at + timedelta(days=7),
        )
    for challenge in database.scalars(
        select(ChallengeRecord)
        .where(ChallengeRecord.starts_at <= now, ChallengeRecord.expires_at > now)
        .order_by(ChallengeRecord.id)
    ):
        emit(
            f"challenge:{challenge.id}:start",
            "challenge_started",
            "Начался челлендж",
            challenge.title,
            challenge.starts_at,
            challenge.expires_at,
        )
        emit(
            f"challenge:{challenge.id}:ending",
            "challenge_ending",
            "Челлендж скоро завершится",
            challenge.title,
            max(challenge.starts_at, challenge.expires_at - timedelta(hours=1)),
            challenge.expires_at,
        )
    for unlock, definition in database.execute(
        select(AchievementUnlockRecord, AchievementRecord)
        .join(
            AchievementRecord,
            (AchievementUnlockRecord.achievement_id == AchievementRecord.id)
            & (
                AchievementUnlockRecord.achievement_version == AchievementRecord.version
            ),
        )
        .where(
            AchievementUnlockRecord.employee_id == employee_id,
            AchievementUnlockRecord.unlocked_at > now - timedelta(days=30),
        )
        .order_by(AchievementUnlockRecord.id)
    ):
        emit(
            f"achievement:{unlock.id}",
            "achievement_unlocked",
            "Достижение открыто",
            definition.name,
            unlock.unlocked_at,
            unlock.unlocked_at + timedelta(days=30),
        )
    return inserted


def notification_view(row: NotificationRecord, now: datetime) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "read_at": row.read_at,
        "expired": now >= row.expires_at,
    }


class RetentionService:
    def __init__(
        self, engine: Engine, *, clock: Callable[[Session], datetime] = database_time
    ) -> None:
        self.engine = engine
        self.clock = clock

    def challenges(
        self, employee_id: str, limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        with Session(self.engine) as database:
            now = self.clock(database)
            total = (
                database.scalar(select(func.count()).select_from(ChallengeRecord)) or 0
            )
            rows = database.scalars(
                select(ChallengeRecord)
                .order_by(ChallengeRecord.expires_at.desc(), ChallengeRecord.id)
                .limit(limit)
                .offset(offset)
            ).all()
            repository = SessionRepository(database)
            sessions = (
                [
                    repository.load(row)[1]
                    for row in database.scalars(
                        select(StoredSession).where(
                            StoredSession.snapshot["employee_id"].astext == employee_id,
                            StoredSession.state == "completed",
                        )
                    )
                ]
                if rows
                else []
            )
            items = []
            for row in rows:
                targets = list(
                    database.execute(
                        select(ChallengeScenario, ScenarioVersion)
                        .join(
                            ScenarioVersion,
                            (ChallengeScenario.scenario_id == ScenarioVersion.id)
                            & (
                                ChallengeScenario.scenario_version
                                == ScenarioVersion.version
                            ),
                        )
                        .where(ChallengeScenario.challenge_id == row.id)
                        .order_by(ChallengeScenario.scenario_id)
                    )
                )
                rule = Challenge(
                    row.id,
                    row.starts_at,
                    row.expires_at,
                    tuple(
                        (target.scenario_id, target.scenario_version)
                        for target, _ in targets
                    ),
                    row.target,
                )
                progress = challenge_progress(rule, sessions, now)
                items.append(
                    {
                        "id": row.id,
                        "title": row.title,
                        "description": row.description,
                        "starts_at": row.starts_at,
                        "expires_at": row.expires_at,
                        "target": row.target,
                        "progress": progress.progress,
                        "status": progress.status,
                        "scenarios": [
                            {
                                "id": target.scenario_id,
                                "version": target.scenario_version,
                                "title": version.document["title"],
                                "completed": target.scenario_id
                                in progress.completed_scenarios,
                            }
                            for target, version in targets
                        ],
                    }
                )
            return {
                "items": items,
                "total": total,
                "limit": limit,
                "offset": offset,
                "server_time": now,
            }

    def notifications(
        self, employee_id: str, limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        with Session(self.engine) as database, database.begin():
            now = self.clock(database)
            reconcile(database, employee_id, now)
            owned = NotificationRecord.employee_id == employee_id
            total = (
                database.scalar(
                    select(func.count()).select_from(NotificationRecord).where(owned)
                )
                or 0
            )
            unread = (
                database.scalar(
                    select(func.count())
                    .select_from(NotificationRecord)
                    .where(
                        owned,
                        NotificationRecord.read_at.is_(None),
                        NotificationRecord.expires_at > now,
                    )
                )
                or 0
            )
            rows = database.scalars(
                select(NotificationRecord)
                .where(owned)
                .order_by(NotificationRecord.created_at.desc(), NotificationRecord.id)
                .limit(limit)
                .offset(offset)
            )
            return {
                "items": [notification_view(row, now) for row in rows],
                "total": total,
                "unread_count": unread,
                "server_time": now,
                "limit": limit,
                "offset": offset,
            }

    def mark_read(
        self, employee_id: str, notification_id: str, read: bool
    ) -> dict[str, Any]:
        with Session(self.engine) as database, database.begin():
            row = database.scalar(
                select(NotificationRecord)
                .where(
                    NotificationRecord.id == notification_id,
                    NotificationRecord.employee_id == employee_id,
                )
                .with_for_update()
            )
            if row is None:
                raise UseCaseError("notification_not_found", "Notification not found")
            now = self.clock(database)
            row.read_at = (row.read_at or now) if read else None
            database.flush()
            return notification_view(row, now)

    def sweep(self) -> int:
        # Separate transactions keep one recipient from holding all profile locks.
        with Session(self.engine) as database:
            employees = database.scalars(
                select(UserProfile.id).order_by(UserProfile.id)
            ).all()
        count = 0
        for employee_id in employees:
            with Session(self.engine) as database, database.begin():
                count += reconcile(database, employee_id, self.clock(database))
        return count
