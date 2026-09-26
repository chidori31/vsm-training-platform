from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AchievementRecord(Base):
    __tablename__ = "achievement_definitions"
    __table_args__ = (CheckConstraint("version > 0", name="ck_achievement_version"),)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    condition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class AchievementUnlockRecord(Base):
    __tablename__ = "achievement_unlocks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["achievement_id", "achievement_version"],
            ["achievement_definitions.id", "achievement_definitions.version"],
        ),
        UniqueConstraint(
            "employee_id",
            "achievement_id",
            "achievement_version",
            name="uq_employee_achievement",
        ),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    employee_id: Mapped[str] = mapped_column(
        ForeignKey("user_profiles.id"), nullable=False
    )
    achievement_id: Mapped[str] = mapped_column(Text, nullable=False)
    achievement_version: Mapped[int] = mapped_column(Integer, nullable=False)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("scenario_sessions.id"), nullable=False
    )
    unlocked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
