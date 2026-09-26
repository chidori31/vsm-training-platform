"""Transactional awards and small-demo projections from completed results."""

from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.application.errors import UseCaseError
from app.domain.gamification import (
    ACHIEVEMENTS,
    RewardFact,
    level_for,
    progress_for,
    reward_for,
)
from app.persistence.achievements import AchievementUnlockRecord
from app.persistence.gamification import SessionReward
from app.persistence.identity import UserProfile
from app.persistence.sessions import SessionRepository, StoredSession


def fact_from(record: SessionReward) -> RewardFact:
    return RewardFact(
        record.session_id,
        record.xp,
        tuple(sorted(record.competencies.items())),
        record.safe_completion,
        record.conflict_resolved,
        tuple(record.critical),
    )


def rewards(database: Session, employee_id: str) -> list[SessionReward]:
    return list(
        database.scalars(
            select(SessionReward)
            .where(SessionReward.employee_id == employee_id)
            .order_by(SessionReward.awarded_at, SessionReward.session_id)
        )
    )


def settle_profile(database: Session, employee_id: str) -> UserProfile | None:
    """Caller owns transaction. One profile lock, no locks on other session rows."""
    database.flush()
    profile = database.scalar(
        select(UserProfile)
        .where(UserProfile.id == employee_id)
        # NO KEY UPDATE serializes awards while allowing start-key FK KEY SHARE.
        # Profile identity is unchanged; FOR UPDATE would deadlock terminal starts.
        .with_for_update(key_share=True)
    )
    if profile is None:
        # Legacy pure service users can have sessions without a registered profile.
        return None
    pending = list(
        database.scalars(
            select(StoredSession)
            .where(
                StoredSession.state == "completed",
                StoredSession.snapshot["employee_id"].astext == employee_id,
                StoredSession.id.not_in(select(SessionReward.session_id)),
            )
            .order_by(StoredSession.updated_at, StoredSession.id)
        )
    )
    facts = [fact_from(row) for row in rewards(database, employee_id)]
    unlocked = set(
        database.scalars(
            select(AchievementUnlockRecord.achievement_id).where(
                AchievementUnlockRecord.employee_id == employee_id,
                AchievementUnlockRecord.achievement_version == 1,
            )
        )
    )
    repository = SessionRepository(database)
    for row in pending:
        fact = reward_for(*repository.load(row))
        record = SessionReward(
            session_id=fact.session_id,
            employee_id=employee_id,
            rule_version=1,
            xp=fact.xp,
            competencies=dict(fact.competencies),
            safe_completion=fact.safe_completion,
            conflict_resolved=fact.conflict_resolved,
            critical=list(fact.critical),
        )
        database.add(record)
        database.flush()
        facts.append(fact)
        progress = progress_for(facts)
        for achievement in ACHIEVEMENTS:
            if (
                progress.values[achievement.id] >= achievement.target
                and achievement.id not in unlocked
            ):
                database.add(
                    AchievementUnlockRecord(
                        id=str(uuid4()),
                        employee_id=employee_id,
                        achievement_id=achievement.id,
                        achievement_version=1,
                        session_id=fact.session_id,
                        unlocked_at=record.awarded_at,
                    )
                )
                unlocked.add(achievement.id)
    database.flush()
    return profile


def organization(profile: UserProfile) -> dict[str, str | None]:
    return {
        "company_id": profile.company_id,
        "depot_id": profile.depot_id,
        "brigade_id": profile.brigade_id,
        "company_name": {
            "demo-company": "Учебная компания ВСМ",
            "demo-other": "Учебная компания 02",
        }.get(profile.company_id or "", profile.company_id or "Не назначено"),
        "depot_name": {"north": "Депо Север", "south": "Депо Юг"}.get(
            profile.depot_id or "", profile.depot_id or "Не назначено"
        ),
        "brigade_name": f"Бригада {profile.brigade_id}"
        if profile.brigade_id
        else "Не назначено",
    }


class GamificationService:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def profile(
        self, employee_id: str, session_id: str | None = None
    ) -> dict[str, Any]:
        with Session(self.engine) as database, database.begin():
            profile = settle_profile(database, employee_id)
            if profile is None:
                raise UseCaseError("unauthorized", "Profile unavailable")
            records = rewards(database, employee_id)
            progress = progress_for(fact_from(record) for record in records)
            unlocked = {
                row.achievement_id: row
                for row in database.scalars(
                    select(AchievementUnlockRecord).where(
                        AchievementUnlockRecord.employee_id == employee_id,
                        AchievementUnlockRecord.achievement_version == 1,
                    )
                )
            }
            reward = next(
                (row for row in records if row.session_id == session_id), None
            )
            return {
                "id": profile.id,
                "display_name": profile.display_name,
                "organization": organization(profile),
                "xp": progress.xp,
                "level": progress.level,
                "level_start_xp": progress.level_start,
                "next_level_xp": progress.next_level,
                "completed_sessions": len(records),
                "rule_version": 1,
                "competencies": [
                    {"competency_id": key, "value": value}
                    for key, value in progress.competencies
                ],
                "achievements": [
                    {
                        "id": a.id,
                        "name": a.name,
                        "description": a.description,
                        "target": a.target,
                        "current": a.target
                        if a.id in unlocked
                        else progress.values[a.id],
                        "unlocked": a.id in unlocked,
                        "unlocked_at": unlocked[a.id].unlocked_at
                        if a.id in unlocked
                        else None,
                        "session_id": unlocked[a.id].session_id
                        if a.id in unlocked
                        else None,
                    }
                    for a in ACHIEVEMENTS
                ],
                "reward": {
                    "session_id": reward.session_id,
                    "xp": reward.xp,
                    "competencies": [
                        {"competency_id": k, "value": v}
                        for k, v in sorted(reward.competencies.items())
                    ],
                    "unlocks": [
                        a.id
                        for a in ACHIEVEMENTS
                        if a.id in unlocked
                        and unlocked[a.id].session_id == reward.session_id
                    ],
                }
                if reward
                else None,
            }

    def leaderboard(
        self, employee_id: str, scope: str, limit: int, offset: int
    ) -> dict[str, Any]:
        if scope not in {"brigade", "depot", "company"}:
            raise UseCaseError("invalid_scope", "Unknown organization scope")
        with Session(self.engine) as database:
            profile = database.get(UserProfile, employee_id)
            if profile is None:
                raise UseCaseError("unauthorized", "Profile unavailable")
            org = organization(profile)
            fields = (
                ["company_id"]
                + (["depot_id"] if scope != "company" else [])
                + (["brigade_id"] if scope == "brigade" else [])
            )
            assigned = all(getattr(profile, key) is not None for key in fields)
            filters = [
                getattr(UserProfile, key) == getattr(profile, key) for key in fields
            ]
            ids = (
                list(
                    database.scalars(
                        select(UserProfile.id).where(*filters).order_by(UserProfile.id)
                    )
                )
                if assigned
                else []
            )
        # Each backfill holds only one profile lock; no cross-profile lock cycle.
        for identity in ids:
            with Session(self.engine) as database, database.begin():
                settle_profile(database, identity)
        with Session(self.engine) as database:
            rows = database.execute(
                select(
                    UserProfile.id,
                    UserProfile.display_name,
                    func.sum(SessionReward.xp).label("xp"),
                    func.count(SessionReward.session_id).label("completed"),
                )
                .join(SessionReward, SessionReward.employee_id == UserProfile.id)
                .where(UserProfile.id.in_(ids))
                .group_by(UserProfile.id, UserProfile.display_name)
                .order_by(func.sum(SessionReward.xp).desc(), UserProfile.id)
            ).all()
        items = []
        rank, previous = 0, None
        for index, row in enumerate(rows, start=1):
            if row.xp != previous:
                rank = index
            previous = row.xp
            items.append(
                {
                    "rank": rank,
                    "employee_id": row.id,
                    "display_name": row.display_name,
                    "xp": row.xp,
                    "level": level_for(row.xp)[0],
                    "completed_sessions": row.completed,
                    "is_me": row.id == employee_id,
                }
            )
        return {
            "scope": scope,
            "group_name": org[f"{scope}_name"],
            "assigned": assigned,
            "items": items[offset : offset + limit],
            "total": len(items),
            "limit": limit,
            "offset": offset,
        }
