"""Demo identities, idempotent starts and achievement read models."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260926_03"
down_revision = "20260926_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_profiles",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("display_name", sa.String(256), nullable=False),
    )
    op.create_table(
        "demo_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "employee_id", sa.Text(), sa.ForeignKey("user_profiles.id"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "session_start_keys",
        sa.Column(
            "employee_id",
            sa.Text(),
            sa.ForeignKey("user_profiles.id"),
            primary_key=True,
        ),
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("scenario_id", sa.String(64), nullable=False),
        sa.Column("scenario_version", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Text(), sa.ForeignKey("scenario_sessions.id")),
    )
    op.create_table(
        "achievement_definitions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("condition", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_achievement_version"),
    )
    op.create_table(
        "achievement_unlocks",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "employee_id", sa.Text(), sa.ForeignKey("user_profiles.id"), nullable=False
        ),
        sa.Column("achievement_id", sa.Text(), nullable=False),
        sa.Column("achievement_version", sa.Integer(), nullable=False),
        sa.Column(
            "session_id",
            sa.Text(),
            sa.ForeignKey("scenario_sessions.id"),
            nullable=False,
        ),
        sa.Column("unlocked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["achievement_id", "achievement_version"],
            ["achievement_definitions.id", "achievement_definitions.version"],
        ),
        sa.UniqueConstraint(
            "employee_id",
            "achievement_id",
            "achievement_version",
            name="uq_employee_achievement",
        ),
    )


def downgrade() -> None:
    for table in (
        "achievement_unlocks",
        "achievement_definitions",
        "session_start_keys",
        "demo_tokens",
        "user_profiles",
    ):
        op.drop_table(table)
