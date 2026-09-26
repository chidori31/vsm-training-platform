from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class TrainingShift(Base):
    __tablename__ = "training_shifts"
    __table_args__ = (
        UniqueConstraint("employee_id", "start_key", name="uq_shift_start_key"),
        CheckConstraint(
            "difficulty IN ('standard','advanced') AND route_version = 1 AND seed >= 0",
            name="ck_shift_configuration",
        ),
        CheckConstraint(
            "(status = 'active' AND current_step BETWEEN 0 AND 2 AND completed_at IS "
            "NULL AND summary IS NULL) OR (status = 'completed' AND current_step = 3 "
            "AND completed_at IS NOT NULL AND summary IS NOT NULL AND completed_at >= "
            "started_at AND jsonb_typeof(summary) = 'object')",
            name="ck_shift_state",
        ),
        Index(
            "uq_shift_active_employee",
            "employee_id",
            unique=True,
            postgresql_where="status = 'active'",
        ),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    employee_id: Mapped[str] = mapped_column(
        ForeignKey("user_profiles.id"), nullable=False
    )
    start_key: Mapped[str] = mapped_column(Text, nullable=False)
    route_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    difficulty: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    current_step: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class ShiftStep(Base):
    __tablename__ = "shift_steps"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scenario_id", "scenario_version"],
            ["scenario_versions.id", "scenario_versions.version"],
        ),
        CheckConstraint(
            "step_index BETWEEN 0 AND 2 AND kind IN ('service','conflict','critical')",
            name="ck_shift_step",
        ),
        UniqueConstraint("session_id", name="uq_shift_step_session"),
    )
    shift_id: Mapped[str] = mapped_column(
        ForeignKey("training_shifts.id"), primary_key=True
    )
    step_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    scenario_id: Mapped[str] = mapped_column(Text, nullable=False)
    scenario_version: Mapped[int] = mapped_column(Integer, nullable=False)
    passenger_profile: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("scenario_sessions.id"))


class ShiftCommand(Base):
    __tablename__ = "shift_commands"
    __table_args__ = (
        ForeignKeyConstraint(
            ["shift_id", "expected_step"],
            ["shift_steps.shift_id", "shift_steps.step_index"],
        ),
        CheckConstraint(
            "expected_step BETWEEN 0 AND 2 AND resulting_step = expected_step + 1",
            name="ck_shift_command_order",
        ),
    )
    shift_id: Mapped[str] = mapped_column(
        ForeignKey("training_shifts.id"), primary_key=True
    )
    command_id: Mapped[str] = mapped_column(Text, primary_key=True)
    expected_step: Mapped[int] = mapped_column(Integer, nullable=False)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("scenario_sessions.id"), nullable=False
    )
    resulting_step: Mapped[int] = mapped_column(Integer, nullable=False)
    server_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ShiftAchievement(Base):
    __tablename__ = "shift_achievements"
    employee_id: Mapped[str] = mapped_column(
        ForeignKey("user_profiles.id"), primary_key=True
    )
    achievement_id: Mapped[str] = mapped_column(Text, primary_key=True)
    shift_id: Mapped[str] = mapped_column(
        ForeignKey("training_shifts.id"), nullable=False
    )
    awarded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
