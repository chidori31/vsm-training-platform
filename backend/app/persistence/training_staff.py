"""Content snapshots, group assignments and comments for synthetic training."""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class TrainingContent(Base):
    __tablename__ = "training_content"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','published') AND revision>=0 AND version>0",
            name="ck_training_content_state",
        ),
        CheckConstraint(
            "jsonb_typeof(document)='object'", name="ck_training_content_document"
        ),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrainingAssignment(Base):
    __tablename__ = "training_assignments"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('work','demo') AND seed>=0", name="ck_training_assignment_mode"
        ),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    group_id: Mapped[str] = mapped_column(Text, nullable=False)
    instructor_id: Mapped[str] = mapped_column(
        ForeignKey("user_profiles.id"), nullable=False
    )
    content_id: Mapped[str] = mapped_column(
        ForeignKey("training_content.id"), nullable=False
    )
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class TrainingAssignmentMember(Base):
    __tablename__ = "training_assignment_members"
    assignment_id: Mapped[str] = mapped_column(
        ForeignKey("training_assignments.id"), primary_key=True
    )
    employee_id: Mapped[str] = mapped_column(
        ForeignKey("user_profiles.id"), primary_key=True
    )


class TrainingComment(Base):
    __tablename__ = "training_comments"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("trainings.id"), nullable=False, index=True
    )
    author_id: Mapped[str] = mapped_column(
        ForeignKey("user_profiles.id"), nullable=False
    )
    event_id: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class TrainingStaffReceipt(Base):
    __tablename__ = "training_staff_receipts"
    actor_id: Mapped[str] = mapped_column(
        ForeignKey("user_profiles.id"), primary_key=True
    )
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
