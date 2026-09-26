"""Explicit, idempotent publication of synthetic retention campaigns."""

import os
from datetime import timedelta

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.application.sessions import database_time
from app.domain.retention import Challenge
from app.persistence.retention import ChallengeRecord, ChallengeScenario
from app.persistence.scenarios import ScenarioVersion

SCENARIOS = (("demo-boarding-assistance", 1), ("demo-lost-property", 1))


def publish(engine: Engine) -> int:
    inserted = 0
    with Session(engine) as database, database.begin():
        for key, version in SCENARIOS:
            if database.get(ScenarioVersion, (key, version)) is None:
                raise ValueError(
                    "Import the new demo scenarios before publishing challenges"
                )
        now = database_time(database)
        for key, title, days, target in (
            ("demo-practice-day-v1", "Две ситуации за сутки", 1, 2),
            ("demo-practice-week-v1", "Новая практика недели", 7, 1),
        ):
            rule = Challenge(key, now, now + timedelta(days=days), SCENARIOS, target)
            created = database.scalar(
                insert(ChallengeRecord)
                .values(
                    id=key,
                    title=title,
                    description=(
                        "Пройдите новые ситуации без таймаутов с безопасностью "
                        "не ниже 50. Повтор сценария не увеличивает прогресс."
                    ),
                    starts_at=rule.starts_at,
                    expires_at=rule.expires_at,
                    target=target,
                )
                .on_conflict_do_nothing(index_elements=["id"])
                .returning(ChallengeRecord.id)
            )
            if created is not None:
                inserted += 1
                for scenario_id, version in SCENARIOS:
                    database.add(
                        ChallengeScenario(
                            challenge_id=key,
                            scenario_id=scenario_id,
                            scenario_version=version,
                        )
                    )
            else:
                existing = database.get(ChallengeRecord, key)
                targets = set(
                    database.execute(
                        select(
                            ChallengeScenario.scenario_id,
                            ChallengeScenario.scenario_version,
                        ).where(ChallengeScenario.challenge_id == key)
                    ).all()
                )
                if (
                    existing is None
                    or existing.target != target
                    or targets != set(SCENARIOS)
                ):
                    raise ValueError(
                        "Published challenge differs; use a new campaign ID"
                    )
    return inserted


def main() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        print(f"Published challenges: {publish(engine)}; existing windows preserved")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
