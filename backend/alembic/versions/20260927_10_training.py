"""Owned operational trainings and separate immutable reward settlement."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260927_10"
down_revision = "20260927_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trainings",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "employee_id", sa.Text(), sa.ForeignKey("user_profiles.id"), nullable=False
        ),
        sa.Column("start_key", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("competency_id", sa.Text()),
        sa.Column("assignment_id", sa.Text()),
        sa.Column("source_id", sa.Text(), sa.ForeignKey("trainings.id")),
        sa.Column("source_at_seconds", sa.Integer()),
        sa.Column("reward_eligible", sa.Boolean(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
        sa.Column("definition_hash", sa.Text(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("elapsed_seconds", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            (
                "mode IN ('work','tutorial','demo','practice') AND ((mode='work' "
                "AND duration_seconds=1200) OR (mode IN ('tutorial','practice') "
                "AND duration_seconds=180) OR (mode='demo' AND "
                "duration_seconds=240))"
            ),
            name="ck_training_mode",
        ),
        sa.CheckConstraint(
            "reward_eligible = (mode='work' AND source_id IS NULL)",
            name="ck_training_eligibility",
        ),
        sa.CheckConstraint(
            (
                "(source_id IS NULL AND source_at_seconds IS NULL) OR (source_id "
                "IS NOT NULL AND source_at_seconds>=0 AND "
                "source_at_seconds<duration_seconds)"
            ),
            name="ck_training_source",
        ),
        sa.UniqueConstraint("employee_id", "start_key", name="uq_training_start"),
        sa.CheckConstraint(
            "status IN ('active','completed') AND revision >= 0 AND elapsed_seconds "
            "BETWEEN 0 AND 1200",
            name="ck_training_state",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(definition)='object' AND jsonb_typeof(snapshot)='object'",
            name="ck_training_json",
        ),
        sa.CheckConstraint(
            (
                "(status='active' AND completed_at IS NULL AND "
                "elapsed_seconds<duration_seconds) OR "
                + "(status='completed' AND completed_at IS NOT NULL AND "
                "completed_at>=started_at AND elapsed_seconds=duration_seconds)"
            ),
            name="ck_training_completion",
        ),
    )
    op.create_index(
        "uq_training_active_owner",
        "trainings",
        ["employee_id"],
        unique=True,
        postgresql_where=sa.text("status='active'"),
    )
    op.create_table(
        "training_commands",
        sa.Column(
            "training_id",
            sa.Text(),
            sa.ForeignKey("trainings.id"),
            primary_key=True,
        ),
        sa.Column("command_id", sa.Text(), primary_key=True),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("resulting_revision", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "expected_revision>=0 AND resulting_revision>expected_revision",
            name="ck_training_command_revision",
        ),
    )
    op.create_table(
        "training_rewards",
        sa.Column(
            "training_id",
            sa.Text(),
            sa.ForeignKey("trainings.id"),
            primary_key=True,
        ),
        sa.Column(
            "employee_id", sa.Text(), sa.ForeignKey("user_profiles.id"), nullable=False
        ),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("event", postgresql.JSONB(), nullable=False),
        sa.Column("transaction", postgresql.JSONB()),
        sa.Column("xp", sa.Integer(), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("achievements", postgresql.JSONB(), nullable=False),
        sa.Column("awarded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "xp BETWEEN 0 AND 130 AND rule_version=2", name="ck_training_reward"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(event)='object' AND jsonb_typeof(achievements)='array'",
            name="ck_training_reward_json",
        ),
        sa.UniqueConstraint("event_id", name="uq_training_reward_event"),
    )
    op.create_index(
        "ix_training_rewards_employee_id", "training_rewards", ["employee_id"]
    )
    op.execute("""CREATE FUNCTION reject_training_reward_mutation()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'training reward settlements are immutable'; END; $$""")
    op.execute(
        "CREATE TRIGGER training_reward_immutable BEFORE UPDATE OR DELETE ON "
        "training_rewards FOR EACH ROW EXECUTE FUNCTION "
        "reject_training_reward_mutation()"
    )


def downgrade() -> None:
    op.drop_table("training_rewards")
    op.execute("DROP FUNCTION reject_training_reward_mutation()")
    op.drop_table("training_commands")
    op.drop_table("trainings")
