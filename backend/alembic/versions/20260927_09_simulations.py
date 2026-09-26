"""Owned operational simulations and separate immutable reward settlement."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260927_09"
down_revision = "20260926_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "simulations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "employee_id", sa.Text(), sa.ForeignKey("user_profiles.id"), nullable=False
        ),
        sa.Column("start_key", sa.Text(), nullable=False),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
        sa.Column("definition_hash", sa.Text(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("elapsed_seconds", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("employee_id", "start_key", name="uq_simulation_start"),
        sa.CheckConstraint(
            "status IN ('active','completed') AND revision >= 0 AND elapsed_seconds "
            "BETWEEN 0 AND 1200",
            name="ck_simulation_state",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(definition)='object' AND jsonb_typeof(snapshot)='object'",
            name="ck_simulation_json",
        ),
        sa.CheckConstraint(
            "(status='active' AND completed_at IS NULL AND elapsed_seconds<1200) OR "
            "(status='completed' AND completed_at IS NOT NULL AND "
            "completed_at>=started_at AND elapsed_seconds=1200)",
            name="ck_simulation_completion",
        ),
    )
    op.create_index(
        "uq_simulation_active_owner",
        "simulations",
        ["employee_id"],
        unique=True,
        postgresql_where=sa.text("status='active'"),
    )
    op.create_table(
        "simulation_commands",
        sa.Column(
            "simulation_id",
            sa.Text(),
            sa.ForeignKey("simulations.id"),
            primary_key=True,
        ),
        sa.Column("command_id", sa.Text(), primary_key=True),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("resulting_revision", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "expected_revision>=0 AND resulting_revision>expected_revision",
            name="ck_simulation_command_revision",
        ),
    )
    op.create_table(
        "simulation_rewards",
        sa.Column(
            "simulation_id",
            sa.Text(),
            sa.ForeignKey("simulations.id"),
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
            "xp BETWEEN 0 AND 130 AND rule_version=1", name="ck_simulation_reward"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(event)='object' AND jsonb_typeof(achievements)='array'",
            name="ck_simulation_reward_json",
        ),
        sa.UniqueConstraint("event_id", name="uq_simulation_reward_event"),
    )
    op.create_index(
        "ix_simulation_rewards_employee_id", "simulation_rewards", ["employee_id"]
    )
    op.execute("""CREATE FUNCTION reject_simulation_reward_mutation()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'simulation reward settlements are immutable'; END; $$""")
    op.execute(
        "CREATE TRIGGER simulation_reward_immutable BEFORE UPDATE OR DELETE ON "
        "simulation_rewards FOR EACH ROW EXECUTE FUNCTION "
        "reject_simulation_reward_mutation()"
    )


def downgrade() -> None:
    op.drop_table("simulation_rewards")
    op.execute("DROP FUNCTION reject_simulation_reward_mutation()")
    op.drop_table("simulation_commands")
    op.drop_table("simulations")
