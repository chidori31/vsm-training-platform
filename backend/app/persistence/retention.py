from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ChallengeRecord(Base):
    __tablename__ = "challenges"
    __table_args__ = (
        CheckConstraint(
            "expires_at > starts_at AND target > 0", name="ck_challenge_window"
        ),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    target: Mapped[int] = mapped_column(Integer, nullable=False)


class ChallengeScenario(Base):
    __tablename__ = "challenge_scenarios"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scenario_id", "scenario_version"],
            ["scenario_versions.id", "scenario_versions.version"],
        ),
    )
    challenge_id: Mapped[str] = mapped_column(
        ForeignKey("challenges.id"), primary_key=True
    )
    scenario_id: Mapped[str] = mapped_column(Text, primary_key=True)
    scenario_version: Mapped[int] = mapped_column(Integer, nullable=False)


class NotificationRecord(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("employee_id", "event_key", name="uq_notification_event"),
        CheckConstraint(
            "expires_at > created_at AND (read_at IS NULL OR read_at >= created_at)",
            name="ck_notification_dates",
        ),
        CheckConstraint(
            "kind IN ('new_scenario','challenge_started','challenge_ending',"
            "'achievement_unlocked')",
            name="ck_notification_kind",
        ),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"), index=True)
    event_key: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
