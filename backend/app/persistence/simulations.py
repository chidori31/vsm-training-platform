from datetime import datetime
from typing import Any

from sqlalchemy import (
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


class StoredSimulation(Base):
    __tablename__ = "simulations"
    __table_args__ = (
        UniqueConstraint("employee_id", "start_key", name="uq_simulation_start"),
        Index(
            "uq_simulation_active_owner",
            "employee_id",
            unique=True,
            postgresql_where="status='active'",
        ),
        CheckConstraint(
            "status IN ('active','completed') AND revision >= 0 AND elapsed_seconds "
            "BETWEEN 0 AND 1200",
            name="ck_simulation_state",
        ),
        CheckConstraint(
            "jsonb_typeof(definition)='object' AND jsonb_typeof(snapshot)='object'",
            name="ck_simulation_json",
        ),
        CheckConstraint(
            "(status='active' AND completed_at IS NULL AND elapsed_seconds<1200) OR "
            "(status='completed' AND completed_at IS NOT NULL AND "
            "completed_at>=started_at AND elapsed_seconds=1200)",
            name="ck_simulation_completion",
        ),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    employee_id: Mapped[str] = mapped_column(
        ForeignKey("user_profiles.id"), nullable=False
    )
    start_key: Mapped[str] = mapped_column(Text, nullable=False)
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


class SimulationCommand(Base):
    __tablename__ = "simulation_commands"
    __table_args__ = (
        CheckConstraint(
            "expected_revision>=0 AND resulting_revision>expected_revision",
            name="ck_simulation_command_revision",
        ),
    )
    simulation_id: Mapped[str] = mapped_column(
        ForeignKey("simulations.id"), primary_key=True
    )
    command_id: Mapped[str] = mapped_column(Text, primary_key=True)
    expected_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    resulting_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SimulationReward(Base):
    __tablename__ = "simulation_rewards"
    __table_args__ = (
        CheckConstraint(
            "xp BETWEEN 0 AND 130 AND rule_version=1", name="ck_simulation_reward"
        ),
        CheckConstraint(
            "jsonb_typeof(event)='object' AND jsonb_typeof(achievements)='array'",
            name="ck_simulation_reward_json",
        ),
        UniqueConstraint("event_id", name="uq_simulation_reward_event"),
    )
    simulation_id: Mapped[str] = mapped_column(
        ForeignKey("simulations.id"), primary_key=True
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
