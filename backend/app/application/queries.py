"""Read use cases. Small demo projections are computed from verified snapshots."""

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.application.errors import UseCaseError
from app.application.sessions import ScenarioNotFound, SessionView
from app.domain.achievements import Achievement, AchievementUnlock
from app.domain.analytics import summarize_session
from app.domain.gameplay import ScenarioSession, SessionStatus
from app.domain.scoring import Metric, MetricRef
from app.persistence.achievements import AchievementRecord, AchievementUnlockRecord
from app.persistence.identity import UserProfile
from app.persistence.scenarios import ScenarioRepository, ScenarioVersion
from app.persistence.sessions import SessionRepository, StoredSession
from app.scenarios.schema import ConditionDocument, ScenarioDocument
from app.scenarios.session_state import SessionDocument, dump_session


def page[T](items: Sequence[T], limit: int, offset: int) -> dict[str, Any]:
    return {
        "items": list(items[offset : offset + limit]),
        "total": len(items),
        "limit": limit,
        "offset": offset,
    }


def summary(session: ScenarioSession) -> dict[str, Any]:
    value = summarize_session(session)
    return {
        "session_id": value.session_id,
        "scenario_id": value.scenario_id,
        "scenario_version": value.scenario_version,
        "status": value.status.value,
        "decision_count": value.decision_count,
        "duration_seconds": value.duration_seconds,
        "passenger_loyalty": value.scores.value(MetricRef(Metric.PASSENGER_LOYALTY)),
        "safety_rating": value.scores.value(MetricRef(Metric.SAFETY_RATING)),
        "completed_at": session.completed_at,
        "competencies": [
            {"competency_id": metric.competency_id, "value": score}
            for metric, score in value.scores.values.items()
            if metric.metric is Metric.COMPETENCY
        ],
    }


def session_result(view: SessionView) -> dict[str, Any]:
    if view.session.status is not SessionStatus.COMPLETED:
        raise UseCaseError("result_not_ready", "Session is not completed")
    return {
        "summary": summary(view.session),
        "session": SessionDocument.model_validate_json(
            dump_session(view.scenario, view.session)
        ),
    }


def decision_history(view: SessionView, limit: int, offset: int) -> dict[str, Any]:
    document = SessionDocument.model_validate_json(
        dump_session(view.scenario, view.session)
    )
    return page(document.decisions, limit, offset)


class QueryService:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def scenarios(self, limit: int, offset: int) -> dict[str, Any]:
        with Session(self.engine) as db:
            total = db.scalar(select(func.count()).select_from(ScenarioVersion))
            rows = db.scalars(
                select(ScenarioVersion)
                .order_by(ScenarioVersion.id, ScenarioVersion.version)
                .offset(offset)
                .limit(limit)
            )
            items = []
            for row in rows:
                document = ScenarioDocument.model_validate_json(
                    json.dumps(row.document)
                )
                items.append(
                    {
                        "id": document.id,
                        "version": document.version,
                        "title": document.title,
                        "competency_ids": document.competency_ids,
                    }
                )
            return {"items": items, "total": total, "limit": limit, "offset": offset}

    def scenario(self, scenario_id: str, version: int) -> ScenarioDocument:
        with Session(self.engine) as db:
            result = ScenarioRepository(db).get(scenario_id, version)
            if result is None:
                raise ScenarioNotFound("Scenario version not found")
            return result

    def _sessions(
        self,
        *,
        employee_id: str | None = None,
        completed: bool = False,
        scenario_id: str | None = None,
        scenario_version: int | None = None,
    ) -> list[ScenarioSession]:
        query = select(StoredSession).order_by(
            StoredSession.updated_at.desc(), StoredSession.id
        )
        if employee_id is not None:
            query = query.where(
                StoredSession.snapshot["employee_id"].astext == employee_id
            )
        if completed:
            query = query.where(StoredSession.state == "completed")
        if scenario_id is not None:
            query = query.where(
                StoredSession.scenario_id == scenario_id,
                StoredSession.scenario_version == scenario_version,
            )
        with Session(self.engine) as db:
            repository = SessionRepository(db)
            return [repository.load(row)[1] for row in db.scalars(query)]

    def results(self, employee_id: str, limit: int, offset: int) -> dict[str, Any]:
        sessions = self._sessions(employee_id=employee_id, completed=True)
        return page([summary(item) for item in sessions], limit, offset)

    def achievements(self, limit: int, offset: int) -> dict[str, Any]:
        with Session(self.engine) as db:
            total = db.scalar(select(func.count()).select_from(AchievementRecord))
            rows = db.scalars(
                select(AchievementRecord)
                .order_by(AchievementRecord.id, AchievementRecord.version)
                .limit(limit)
                .offset(offset)
            )
            items = []
            for row in rows:
                condition = ConditionDocument.model_validate_json(
                    json.dumps(row.condition)
                )
                achievement = Achievement(
                    row.id,
                    row.version,
                    row.name,
                    row.description,
                    condition.to_domain(),
                )
                items.append(
                    {
                        "id": achievement.id,
                        "version": achievement.version,
                        "name": achievement.name,
                        "description": achievement.description,
                        "condition": None if row.behavior_rule else condition,
                        "behavior_rule": row.behavior_rule,
                    }
                )
            return {"items": items, "total": total, "limit": limit, "offset": offset}

    def unlocks(self, employee_id: str, limit: int, offset: int) -> dict[str, Any]:
        with Session(self.engine) as db:
            rows = db.scalars(
                select(AchievementUnlockRecord)
                .where(AchievementUnlockRecord.employee_id == employee_id)
                .order_by(
                    AchievementUnlockRecord.unlocked_at, AchievementUnlockRecord.id
                )
            )
            values = [
                AchievementUnlock(
                    row.id,
                    row.employee_id,
                    row.achievement_id,
                    row.achievement_version,
                    row.session_id,
                    row.unlocked_at,
                )
                for row in rows
            ]
            return page(values, limit, offset)

    def analytics(self, employee_id: str) -> dict[str, Any]:
        sessions = self._sessions(employee_id=employee_id)
        completed = [
            item for item in sessions if item.status is SessionStatus.COMPLETED
        ]
        results = [summary(item) for item in completed]
        competencies: dict[str, list[int]] = {}
        for item in completed:
            for metric, value in item.scores.values.items():
                if metric.competency_id is not None:
                    competencies.setdefault(metric.competency_id, []).append(value)
        return {
            "total_sessions": len(sessions),
            "completed_sessions": len(completed),
            "active_sessions": len(sessions) - len(completed),
            "decision_count": sum(len(item.decisions) for item in sessions),
            "timeout_count": sum(
                decision.choice_id == "__timeout__"
                for item in sessions
                for decision in item.decisions
            ),
            "average_passenger_loyalty": (
                sum(item["passenger_loyalty"] for item in results) / len(results)
                if results
                else None
            ),
            "average_safety_rating": (
                sum(item["safety_rating"] for item in results) / len(results)
                if results
                else None
            ),
            "competencies": [
                {"competency_id": key, "average": sum(values) / len(values)}
                for key, values in sorted(competencies.items())
            ],
        }

    def leaderboard(
        self,
        scenario_id: str,
        scenario_version: int,
        metric: str,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        self.scenario(scenario_id, scenario_version)
        sessions = self._sessions(
            completed=True, scenario_id=scenario_id, scenario_version=scenario_version
        )
        with Session(self.engine) as db:
            profiles = {
                row.id: row.display_name for row in db.scalars(select(UserProfile))
            }
        # Select each user's best completed attempt; ties choose the earlier
        # completion, then ID. Rank ties use competition ranking (1, 1, 3).
        ordered = sorted(
            sessions,
            key=lambda item: (
                -item.scores.value(MetricRef(Metric(metric))),
                item.completed_at,
                item.id,
            ),
        )
        best: dict[str, ScenarioSession] = {}
        for item in ordered:
            if item.employee_id in profiles:
                best.setdefault(item.employee_id, item)
        items = []
        last_score = None
        rank = 0
        for index, item in enumerate(best.values(), start=1):
            value = summary(item)
            score = value[metric]
            if score != last_score:
                rank = index
            last_score = score
            items.append(
                {
                    "rank": rank,
                    "employee_id": item.employee_id,
                    "display_name": profiles[item.employee_id],
                    "session_id": item.id,
                    "score": score,
                    "passenger_loyalty": value["passenger_loyalty"],
                    "safety_rating": value["safety_rating"],
                }
            )
        return page(items, limit, offset)
