"""Server evidence and review-only signals, without device or network identifiers."""

import json
import math
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.domain.common import DomainError, utc_time
from app.domain.events import DomainEvent
from app.domain.gameplay import ScenarioSession, SessionStatus
from app.domain.scoring import Metric, MetricRef
from app.persistence.gamification import SessionReward
from app.persistence.security import CommandAudit, RateBucket
from app.persistence.sessions import StoredSession


def database_time(database: Session) -> datetime:
    value = database.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise DomainError("Database time unavailable")
    return utc_time(value, "database time")


@dataclass(frozen=True)
class RatePolicy:
    maximum: int
    window_seconds: int = 60

    def __post_init__(self) -> None:
        if type(self.maximum) is not int or not 1 <= self.maximum <= 100000:
            raise ValueError("Rate maximum must be between 1 and 100000")
        if type(self.window_seconds) is not int or not 1 <= self.window_seconds <= 3600:
            raise ValueError("Rate window must be between 1 and 3600 seconds")

    @classmethod
    def from_environment(cls, action: str) -> "RatePolicy":
        defaults = {
            "auth": 120,
            "start": 60,
            "decision": 240,
            "shift_start": 30,
            "shift_advance": 120,
        }
        if action not in defaults:
            raise ValueError("Unknown sensitive action")
        return cls(
            int(
                os.environ.get(
                    f"RATE_LIMIT_{action.upper()}_PER_MINUTE", defaults[action]
                )
            )
        )


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Too many commands; retry after the current window")
        self.retry_after = retry_after


class RateLimiter:
    def __init__(
        self, engine: Engine, *, clock: Callable[[Session], datetime] = database_time
    ) -> None:
        self.engine, self.clock = engine, clock

    def check(
        self, action: str, subject: str, *, policy: RatePolicy | None = None
    ) -> None:
        policy = policy or RatePolicy.from_environment(action)
        retry_after = 0
        with Session(self.engine) as db, db.begin():
            # INSERT also waits for an uncommitted competing first request.
            db.execute(
                insert(RateBucket)
                .values(
                    action=action,
                    subject=subject,
                    window_started_at=func.clock_timestamp(),
                    count=0,
                )
                .on_conflict_do_nothing(index_elements=["action", "subject"])
            )
            row = db.scalar(
                select(RateBucket)
                .where(
                    RateBucket.action == action,
                    RateBucket.subject == subject,
                )
                .with_for_update()
            )
            if row is None:
                raise DomainError("Rate bucket missing")
            now = utc_time(self.clock(db), "rate time")
            if row.count == 0 or now >= row.window_started_at + timedelta(
                seconds=policy.window_seconds
            ):
                row.window_started_at, row.count = now, 0
            if row.count >= policy.maximum:
                retry_after = max(
                    1,
                    math.ceil(
                        (
                            row.window_started_at
                            + timedelta(seconds=policy.window_seconds)
                            - now
                        ).total_seconds()
                    ),
                )
                record_audit(
                    db,
                    actor_id=None if action == "auth" else subject,
                    action=action,
                    outcome="rate_limited",
                    server_time=now,
                )
            else:
                row.count += 1
        # Raising outside the transaction preserves the consumed bucket and audit.
        if retry_after:
            raise RateLimitExceeded(retry_after)


def anomaly_flags(
    *,
    outcome: str,
    elapsed_seconds: float | None = None,
    replay_count: int = 0,
    active_sessions: int = 0,
    perfect_count: int = 0,
    completion_seconds: float | None = None,
) -> list[str]:
    flags = []
    if (
        outcome == "accepted"
        and elapsed_seconds is not None
        and 0 <= elapsed_seconds < 0.4
    ):
        flags.append("fast_decision")
    if replay_count >= 5:
        flags.append("frequent_replay")
    if outcome == "conflicting":
        flags.append("conflicting_command")
    if active_sessions >= 5:
        flags.append("many_parallel_sessions")
    if perfect_count >= 3:
        flags.append("repeated_perfect_sequence")
    if (
        outcome == "accepted"
        and completion_seconds is not None
        and 0 <= completion_seconds < 1
    ):
        flags.append("rapid_completion")
    return flags


def record_audit(
    database: Session,
    *,
    actor_id: str | None,
    action: str,
    outcome: str,
    server_time: datetime,
    session_id: str | None = None,
    scenario_id: str | None = None,
    scenario_version: int | None = None,
    client_event_id: str | None = None,
    previous_revision: int | None = None,
    resulting_revision: int | None = None,
    previous_state: str | None = None,
    resulting_state: str | None = None,
    reward: dict[str, Any] | None = None,
    flags: list[str] | None = None,
    details: dict[str, Any] | None = None,
    sequence_fingerprint: str | None = None,
) -> None:
    now = utc_time(server_time, "audit time")
    replay_count = 0
    if outcome == "duplicate" and actor_id is not None:
        replay_count = (
            int(
                database.scalar(
                    select(func.count())
                    .select_from(CommandAudit)
                    .where(
                        CommandAudit.actor_id == actor_id,
                        CommandAudit.outcome == "duplicate",
                        CommandAudit.created_at >= now - timedelta(seconds=60),
                    )
                )
                or 0
            )
            + 1
        )
    combined_flags = list(
        dict.fromkeys(
            [*(flags or []), *anomaly_flags(outcome=outcome, replay_count=replay_count)]
        )
    )
    payload: dict[str, Any] = dict(
        action=action,
        outcome=outcome,
        session_id=session_id,
        scenario_id=scenario_id,
        scenario_version=scenario_version,
        client_event_id=client_event_id,
        previous_revision=previous_revision,
        resulting_revision=resulting_revision,
        previous_state=previous_state,
        resulting_state=resulting_state,
        reward=reward or {},
        flags=combined_flags,
        details=details or {},
        sequence_fingerprint=sequence_fingerprint,
    )
    event = DomainEvent(
        event_id=uuid4(),
        event_type=f"training.command.{action}",
        occurred_at=now,
        received_at=now,
        source="training-server",
        subject_id=actor_id or "anonymous",
        payload=payload,
    )
    database.add(
        CommandAudit(
            id=str(event.event_id),
            event_type=event.event_type,
            event_fingerprint=event.fingerprint(),
            actor_id=actor_id,
            created_at=now,
            **payload,
        )
    )
    database.flush()


def record_session_command(
    database: Session,
    *,
    actor_id: str,
    action: str,
    client_event_id: str | None,
    before: ScenarioSession | None,
    after: ScenarioSession,
    outcome: str,
    server_time: datetime,
    expected_revision: int | None = None,
    node_id: str | None = None,
    choice_id: str | None = None,
) -> None:
    previous = len(before.decisions) if before else 0
    current = len(after.decisions)
    if (
        outcome == "rejected"
        and before is not None
        and (
            expected_revision != previous
            or any(d.id == client_event_id for d in before.decisions)
        )
    ):
        outcome = "conflicting"
    now = utc_time(server_time, "audit time")
    reward_row = database.get(SessionReward, after.id)
    total = reward_row.xp if reward_row else 0
    became_completed = after.status is SessionStatus.COMPLETED and (
        before is None or before.status is not SessionStatus.COMPLETED
    )
    grant = total if became_completed and outcome == "accepted" else 0
    # Time is computed from the locked server history, never a client duration.
    elapsed = None
    if action == "decision" and outcome == "accepted" and before is not None:
        entered = (
            before.decisions[-1].decided_at if before.decisions else before.started_at
        )
        elapsed = (now - entered).total_seconds()
    active = (
        int(
            database.scalar(
                select(func.count())
                .select_from(StoredSession)
                .where(
                    StoredSession.snapshot["employee_id"].astext == after.employee_id,
                    StoredSession.state == "active",
                )
            )
            or 0
        )
        if action in {"start", "shift_start"}
        else 0
    )
    signature = None
    perfect_count = 0
    if (
        became_completed
        and outcome == "accepted"
        and after.decisions
        and all(
            d.choice_id != "__timeout__"
            and all(c.applied_delta >= 0 for c in d.score_changes)
            for d in after.decisions
        )
        and all(
            after.scores.value(MetricRef(metric)) >= 50
            for metric in (Metric.PASSENGER_LOYALTY, Metric.SAFETY_RATING)
        )
    ):
        signature = sha256(
            json.dumps(
                [
                    after.scenario_id,
                    after.scenario_version,
                    [(d.node_id, d.choice_id) for d in after.decisions],
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        perfect_count = (
            int(
                database.scalar(
                    select(func.count())
                    .select_from(CommandAudit)
                    .where(
                        CommandAudit.actor_id == actor_id,
                        CommandAudit.sequence_fingerprint == signature,
                        CommandAudit.outcome == "accepted",
                    )
                )
                or 0
            )
            + 1
        )
    flags = anomaly_flags(
        outcome=outcome,
        elapsed_seconds=elapsed,
        active_sessions=active,
        perfect_count=perfect_count,
        completion_seconds=(now - after.started_at).total_seconds()
        if became_completed and current
        else None,
    )
    record_audit(
        database,
        actor_id=actor_id,
        action=action,
        outcome=outcome,
        server_time=now,
        session_id=after.id,
        scenario_id=after.scenario_id,
        scenario_version=after.scenario_version,
        client_event_id=client_event_id,
        previous_revision=previous,
        resulting_revision=current,
        previous_state=before.status.value if before else None,
        resulting_state=after.status.value,
        reward={"xp_total": total, "xp_granted": grant},
        flags=flags,
        details={
            "expected_revision": expected_revision,
            "node_id": node_id,
            "choice_id": choice_id,
            "elapsed_seconds": elapsed,
        },
        sequence_fingerprint=signature,
    )
