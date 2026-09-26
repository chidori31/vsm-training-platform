from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

from .invariants import REWARD_JSON


class SessionReward(Base):
    __tablename__ = "session_rewards"
    __table_args__ = (
        CheckConstraint("xp <= 130", name="ck_reward_xp_cap"),
        CheckConstraint(REWARD_JSON, name="ck_reward_json"),
        CheckConstraint("xp >= 0 AND rule_version = 1", name="ck_reward_v1"),
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("scenario_sessions.id"), primary_key=True
    )
    employee_id: Mapped[str] = mapped_column(
        ForeignKey("user_profiles.id"), nullable=False, index=True
    )
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    xp: Mapped[int] = mapped_column(Integer, nullable=False)
    competencies: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    safe_completion: Mapped[bool] = mapped_column(Boolean, nullable=False)
    conflict_resolved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    critical: Mapped[list[bool]] = mapped_column(JSONB, nullable=False)
    awarded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )
