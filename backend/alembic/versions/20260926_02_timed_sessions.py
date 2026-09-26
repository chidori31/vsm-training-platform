"""Persist session state and indexed deadlines for server timeout processing."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260926_02"
down_revision = "20260926_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scenario_sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("scenario_id", sa.String(64), nullable=False),
        sa.Column("scenario_version", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id", "scenario_version"],
            ["scenario_versions.id", "scenario_versions.version"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_session_revision"),
        sa.CheckConstraint("state IN ('active', 'completed')", name="ck_session_state"),
        sa.CheckConstraint(
            "state != 'completed' OR deadline IS NULL", name="ck_session_final_deadline"
        ),
    )
    op.create_index(
        "ix_session_due",
        "scenario_sessions",
        ["deadline"],
        postgresql_where=sa.text("state = 'active' AND deadline IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_session_due", table_name="scenario_sessions")
    op.drop_table("scenario_sessions")
