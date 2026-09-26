"""Persistent completion rewards and organization membership."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260926_04"
down_revision = "20260926_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("company_id", "depot_id", "brigade_id"):
        op.add_column("user_profiles", sa.Column(name, sa.String(64), nullable=True))
    op.add_column(
        "achievement_definitions", sa.Column("behavior_rule", sa.Text(), nullable=True)
    )
    op.create_table(
        "session_rewards",
        sa.Column(
            "session_id",
            sa.Text(),
            sa.ForeignKey("scenario_sessions.id"),
            primary_key=True,
        ),
        sa.Column(
            "employee_id", sa.Text(), sa.ForeignKey("user_profiles.id"), nullable=False
        ),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("xp", sa.Integer(), nullable=False),
        sa.Column("competencies", postgresql.JSONB(), nullable=False),
        sa.Column("safe_completion", sa.Boolean(), nullable=False),
        sa.Column("conflict_resolved", sa.Boolean(), nullable=False),
        sa.Column("critical", postgresql.JSONB(), nullable=False),
        sa.Column(
            "awarded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint("xp >= 0 AND rule_version = 1", name="ck_reward_v1"),
    )
    op.create_index(
        "ix_session_rewards_employee_id", "session_rewards", ["employee_id"]
    )
    definitions = sa.table(
        "achievement_definitions",
        sa.column("id", sa.Text()),
        sa.column("version", sa.Integer()),
        sa.column("name", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("condition", postgresql.JSONB()),
        sa.column("behavior_rule", sa.Text()),
    )
    op.bulk_insert(
        definitions,
        [
            {
                "id": key,
                "version": 1,
                "name": name,
                "description": description,
                "condition": {"predicates": []},
                "behavior_rule": key,
            }
            for key, name, description in (
                (
                    "safe-shift",
                    "Надёжная смена",
                    "3 завершения с safety ≥ 50 без снижения safety и без таймаутов.",
                ),
                (
                    "conflict-care",
                    "Общий язык",
                    "Разрешённый демо-конфликт без снижения loyalty и без таймаута.",
                ),
                (
                    "critical-streak",
                    "Точно в срок",
                    "3 критических решения подряд вовремя и без снижения safety.",
                ),
                (
                    "communication-growth",
                    "Мастер диалога",
                    "3 накопленных очка коммуникации.",
                ),
            )
        ],
    )
    op.execute(
        "UPDATE user_profiles SET company_id='demo-company', "
        "depot_id='north', brigade_id='01' WHERE id='demo-employee'"
    )


def downgrade() -> None:
    op.drop_table("session_rewards")
    # Only definitions introduced by this migration and their dependent unlocks.
    op.execute(
        "DELETE FROM achievement_unlocks WHERE achievement_version=1 "
        "AND achievement_id IN ('safe-shift','conflict-care',"
        "'critical-streak','communication-growth')"
    )
    op.execute(
        "DELETE FROM achievement_definitions WHERE version=1 "
        "AND id IN ('safe-shift','conflict-care','critical-streak',"
        "'communication-growth')"
    )
    op.drop_column("achievement_definitions", "behavior_rule")
    for name in ("brigade_id", "depot_id", "company_id"):
        op.drop_column("user_profiles", name)
