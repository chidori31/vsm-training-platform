"""Internal challenges and durable, deduplicated notifications."""

import sqlalchemy as sa

from alembic import op

revision = "20260926_05"
down_revision = "20260926_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "challenges",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "expires_at > starts_at AND target > 0", name="ck_challenge_window"
        ),
    )
    op.create_table(
        "challenge_scenarios",
        sa.Column(
            "challenge_id", sa.Text(), sa.ForeignKey("challenges.id"), primary_key=True
        ),
        sa.Column("scenario_id", sa.Text(), primary_key=True),
        sa.Column("scenario_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["scenario_id", "scenario_version"],
            ["scenario_versions.id", "scenario_versions.version"],
        ),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "employee_id", sa.Text(), sa.ForeignKey("user_profiles.id"), nullable=False
        ),
        sa.Column("event_key", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("employee_id", "event_key", name="uq_notification_event"),
        sa.CheckConstraint(
            "expires_at > created_at AND (read_at IS NULL OR read_at >= created_at)",
            name="ck_notification_dates",
        ),
        sa.CheckConstraint(
            "kind IN ('new_scenario','challenge_started','challenge_ending',"
            "'achievement_unlocked')",
            name="ck_notification_kind",
        ),
    )
    op.create_index("ix_notifications_employee_id", "notifications", ["employee_id"])


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("challenge_scenarios")
    op.drop_table("challenges")
