"""Owned, sequential training routes with durable behavior qualifications."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260926_07"
down_revision = "20260926_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "training_shifts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "employee_id", sa.Text(), sa.ForeignKey("user_profiles.id"), nullable=False
        ),
        sa.Column("start_key", sa.Text(), nullable=False),
        sa.Column("route_version", sa.Integer(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("difficulty", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_step", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("summary", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint("employee_id", "start_key", name="uq_shift_start_key"),
        sa.CheckConstraint(
            "difficulty IN ('standard','advanced') AND route_version = 1 AND seed >= 0",
            name="ck_shift_configuration",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND current_step BETWEEN 0 AND 2 AND completed_at IS "
            "NULL AND summary IS NULL) OR (status = 'completed' AND current_step = 3 "
            "AND completed_at IS NOT NULL AND summary IS NOT NULL AND completed_at >= "
            "started_at AND jsonb_typeof(summary) = 'object')",
            name="ck_shift_state",
        ),
    )
    op.create_index(
        "uq_shift_active_employee",
        "training_shifts",
        ["employee_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "shift_steps",
        sa.Column(
            "shift_id", sa.Text(), sa.ForeignKey("training_shifts.id"), primary_key=True
        ),
        sa.Column("step_index", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("scenario_id", sa.Text(), nullable=False),
        sa.Column("scenario_version", sa.Integer(), nullable=False),
        sa.Column("passenger_profile", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), sa.ForeignKey("scenario_sessions.id")),
        sa.ForeignKeyConstraint(
            ["scenario_id", "scenario_version"],
            ["scenario_versions.id", "scenario_versions.version"],
        ),
        sa.CheckConstraint(
            "step_index BETWEEN 0 AND 2 AND kind IN ('service','conflict','critical')",
            name="ck_shift_step",
        ),
        sa.UniqueConstraint("session_id", name="uq_shift_step_session"),
    )
    op.create_table(
        "shift_commands",
        sa.Column(
            "shift_id", sa.Text(), sa.ForeignKey("training_shifts.id"), primary_key=True
        ),
        sa.Column("command_id", sa.Text(), primary_key=True),
        sa.Column("expected_step", sa.Integer(), nullable=False),
        sa.Column(
            "session_id",
            sa.Text(),
            sa.ForeignKey("scenario_sessions.id"),
            nullable=False,
        ),
        sa.Column("resulting_step", sa.Integer(), nullable=False),
        sa.Column("server_time", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["shift_id", "expected_step"],
            ["shift_steps.shift_id", "shift_steps.step_index"],
        ),
        sa.CheckConstraint(
            "expected_step BETWEEN 0 AND 2 AND resulting_step = expected_step + 1",
            name="ck_shift_command_order",
        ),
    )
    op.create_table(
        "shift_achievements",
        sa.Column(
            "employee_id",
            sa.Text(),
            sa.ForeignKey("user_profiles.id"),
            primary_key=True,
        ),
        sa.Column("achievement_id", sa.Text(), primary_key=True),
        sa.Column(
            "shift_id", sa.Text(), sa.ForeignKey("training_shifts.id"), nullable=False
        ),
        sa.Column("awarded_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("shift_achievements")
    op.drop_table("shift_commands")
    op.drop_table("shift_steps")
    op.drop_table("training_shifts")
