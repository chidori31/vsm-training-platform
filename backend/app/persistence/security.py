from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RateBucket(Base):
    __tablename__ = "rate_buckets"
    __table_args__ = (CheckConstraint("count >= 0", name="ck_rate_count"),)

    action: Mapped[str] = mapped_column(String(32), primary_key=True)
    subject: Mapped[str] = mapped_column(String(128), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    count: Mapped[int] = mapped_column(Integer, nullable=False)


class CommandAudit(Base):
    """Append-only evidence; a database trigger rejects UPDATE and DELETE."""

    __tablename__ = "command_audits"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('accepted','duplicate','rejected','timed_out',"
            "'conflicting','rate_limited')",
            name="ck_audit_outcome",
        ),
        CheckConstraint(
            "(previous_revision IS NULL OR previous_revision >= 0) AND "
            "(resulting_revision IS NULL OR resulting_revision >= 0)",
            name="ck_audit_revisions",
        ),
        CheckConstraint(
            "jsonb_typeof(reward) = 'object' AND jsonb_typeof(flags) = 'array' "
            "AND jsonb_typeof(details) = 'object'",
            name="ck_audit_json",
        ),
        Index("ix_audit_actor_time", "actor_id", "created_at"),
        Index("ix_audit_session_time", "session_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    session_id: Mapped[str | None] = mapped_column(Text)
    scenario_id: Mapped[str | None] = mapped_column(String(64))
    scenario_version: Mapped[int | None] = mapped_column(Integer)
    client_event_id: Mapped[str | None] = mapped_column(Text)
    previous_revision: Mapped[int | None] = mapped_column(Integer)
    resulting_revision: Mapped[int | None] = mapped_column(Integer)
    previous_state: Mapped[str | None] = mapped_column(String(16))
    resulting_state: Mapped[str | None] = mapped_column(String(16))
    sequence_fingerprint: Mapped[str | None] = mapped_column(String(64))
    reward: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    flags: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
