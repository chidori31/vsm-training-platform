from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.persistence.training_staff import TrainingAssignment


class StoredTraining(Base):
    __tablename__ = "trainings"
    __table_args__ = (
        CheckConstraint(
            (
                "mode IN ('work','tutorial','demo','practice') AND ((mode='work' "
                "AND duration_seconds=1200) OR (mode IN ('tutorial','practice') "
                "AND duration_seconds=180) OR (mode='demo' AND "
                "duration_seconds=240))"
            ),
            name="ck_training_mode",
        ),
        CheckConstraint(
            "reward_eligible = (mode='work' AND source_id IS NULL)",
            name="ck_training_eligibility",
        ),
        CheckConstraint(
            (
                "(source_id IS NULL AND source_at_seconds IS NULL) OR (source_id "
                "IS NOT NULL AND source_at_seconds>=0 AND "
                "source_at_seconds<duration_seconds)"
            ),
            name="ck_training_source",
        ),
        UniqueConstraint("employee_id", "start_key", name="uq_training_start"),
        Index(
            "uq_training_active_owner",
            "employee_id",
            unique=True,
            postgresql_where="status='active'",
        ),
        Index(
            "uq_training_assignment_owner",
            "assignment_id",
            "employee_id",
            unique=True,
            postgresql_where="assignment_id IS NOT NULL AND source_id IS NULL",
        ),
        CheckConstraint(
            "status IN ('active','completed') AND revision >= 0 AND elapsed_seconds "
            "BETWEEN 0 AND 1200",
            name="ck_training_state",
        ),
        CheckConstraint(
            "jsonb_typeof(definition)='object' AND jsonb_typeof(snapshot)='object'",
            name="ck_training_json",
        ),
        CheckConstraint(
            (
                "(status='active' AND completed_at IS NULL AND "
                "elapsed_seconds<duration_seconds) OR "
                + "(status='completed' AND completed_at IS NOT NULL AND "
                "completed_at>=started_at AND elapsed_seconds=duration_seconds)"
            ),
            name="ck_training_completion",
        ),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    employee_id: Mapped[str] = mapped_column(
        ForeignKey("user_profiles.id"), nullable=False
    )
    start_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    competency_id: Mapped[str | None] = mapped_column(Text)
    assignment_id: Mapped[str | None] = mapped_column(
        # Load the target model in worker/CLI processes as well as the HTTP app.
        ForeignKey(TrainingAssignment.__table__.c.id, name="fk_training_assignment")
    )
    source_id: Mapped[str | None] = mapped_column(ForeignKey("trainings.id"))
    source_at_seconds: Mapped[int | None] = mapped_column(Integer)
    reward_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    definition_hash: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    elapsed_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrainingCommand(Base):
    __tablename__ = "training_commands"
    __table_args__ = (
        CheckConstraint(
            "expected_revision>=0 AND resulting_revision>expected_revision",
            name="ck_training_command_revision",
        ),
    )
    training_id: Mapped[str] = mapped_column(
        ForeignKey("trainings.id"), primary_key=True
    )
    command_id: Mapped[str] = mapped_column(Text, primary_key=True)
    expected_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    resulting_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class TrainingReward(Base):
    __tablename__ = "training_rewards"
    __table_args__ = (
        CheckConstraint(
            "xp BETWEEN 0 AND 130 AND rule_version=2", name="ck_training_reward"
        ),
        CheckConstraint(
            "jsonb_typeof(event)='object' AND jsonb_typeof(achievements)='array'",
            name="ck_training_reward_json",
        ),
        UniqueConstraint("event_id", name="uq_training_reward_event"),
    )
    training_id: Mapped[str] = mapped_column(
        ForeignKey("trainings.id"), primary_key=True
    )
    employee_id: Mapped[str] = mapped_column(
        ForeignKey("user_profiles.id"), nullable=False, index=True
    )
    event_id: Mapped[str] = mapped_column(Text, nullable=False)
    event: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    transaction: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    xp: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    achievements: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    awarded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
