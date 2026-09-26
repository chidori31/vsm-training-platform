"""Durable command evidence and shared rate buckets."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260926_08"
down_revision = "20260926_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rate_buckets",
        sa.Column("action", sa.String(32), primary_key=True),
        sa.Column("subject", sa.String(128), primary_key=True),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.CheckConstraint("count >= 0", name="ck_rate_count"),
    )
    op.create_table(
        "command_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("event_fingerprint", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(128)),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.Text()),
        sa.Column("scenario_id", sa.String(64)),
        sa.Column("scenario_version", sa.Integer()),
        sa.Column("client_event_id", sa.Text()),
        sa.Column("previous_revision", sa.Integer()),
        sa.Column("resulting_revision", sa.Integer()),
        sa.Column("previous_state", sa.String(16)),
        sa.Column("resulting_state", sa.String(16)),
        sa.Column("sequence_fingerprint", sa.String(64)),
        sa.Column("reward", postgresql.JSONB(), nullable=False),
        sa.Column("flags", postgresql.JSONB(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('accepted','duplicate','rejected','timed_out',"
            "'conflicting','rate_limited')",
            name="ck_audit_outcome",
        ),
        sa.CheckConstraint(
            "(previous_revision IS NULL OR previous_revision >= 0) AND "
            "(resulting_revision IS NULL OR resulting_revision >= 0)",
            name="ck_audit_revisions",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(reward) = 'object' AND jsonb_typeof(flags) = 'array' "
            "AND jsonb_typeof(details) = 'object'",
            name="ck_audit_json",
        ),
    )
    op.create_index("ix_audit_actor_time", "command_audits", ["actor_id", "created_at"])
    op.create_index(
        "ix_audit_session_time", "command_audits", ["session_id", "created_at"]
    )
    op.execute("""
        CREATE FUNCTION prevent_command_audit_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Command audit rows are append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER command_audits_append_only
        BEFORE UPDATE OR DELETE ON command_audits
        FOR EACH ROW EXECUTE FUNCTION prevent_command_audit_mutation()
    """)


def downgrade() -> None:
    op.drop_table("command_audits")
    op.execute("DROP FUNCTION prevent_command_audit_mutation()")
    op.drop_table("rate_buckets")
