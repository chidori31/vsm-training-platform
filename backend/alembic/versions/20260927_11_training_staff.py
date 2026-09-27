"""Immutable published content and assigned synthetic learning groups."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260927_11"
down_revision = "20260927_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "training_content",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('draft','published') AND revision>=0 AND version>0",
            name="ck_training_content_state",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(document)='object'", name="ck_training_content_document"
        ),
    )
    op.create_table(
        "training_assignments",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("group_id", sa.Text(), nullable=False),
        sa.Column(
            "instructor_id",
            sa.Text(),
            sa.ForeignKey("user_profiles.id"),
            nullable=False,
        ),
        sa.Column(
            "content_id",
            sa.Text(),
            sa.ForeignKey("training_content.id"),
            nullable=False,
        ),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "mode IN ('work','demo') AND seed>=0", name="ck_training_assignment_mode"
        ),
    )
    op.create_table(
        "training_assignment_members",
        sa.Column(
            "assignment_id",
            sa.Text(),
            sa.ForeignKey("training_assignments.id"),
            primary_key=True,
        ),
        sa.Column(
            "employee_id",
            sa.Text(),
            sa.ForeignKey("user_profiles.id"),
            primary_key=True,
        ),
    )
    op.create_table(
        "training_comments",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("run_id", sa.Text(), sa.ForeignKey("trainings.id"), nullable=False),
        sa.Column(
            "author_id", sa.Text(), sa.ForeignKey("user_profiles.id"), nullable=False
        ),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_training_comments_run_id", "training_comments", ["run_id"])
    op.create_table(
        "training_staff_receipts",
        sa.Column(
            "actor_id", sa.Text(), sa.ForeignKey("user_profiles.id"), primary_key=True
        ),
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=False),
    )
    op.execute("""CREATE FUNCTION protect_training_content()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.status='published' THEN
        RAISE EXCEPTION 'published training content is immutable';
      END IF;
      RETURN NEW;
    END; $$""")
    op.execute(
        "CREATE TRIGGER training_content_immutable BEFORE UPDATE OR DELETE ON "
        "training_content FOR EACH ROW EXECUTE FUNCTION protect_training_content()"
    )
    op.execute("""CREATE FUNCTION protect_training_assignment()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'training assignments and membership are immutable';
    END; $$""")
    for table in ("training_assignments", "training_assignment_members"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION protect_training_assignment()"
        )
    op.create_foreign_key(
        "fk_training_assignment",
        "trainings",
        "training_assignments",
        ["assignment_id"],
        ["id"],
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_training_assignment_owner ON "
        "trainings(assignment_id,employee_id) "
        "WHERE assignment_id IS NOT NULL AND source_id IS NULL"
    )


def downgrade() -> None:
    op.drop_index("uq_training_assignment_owner", table_name="trainings")
    op.drop_constraint("fk_training_assignment", "trainings", type_="foreignkey")
    for table in (
        "training_staff_receipts",
        "training_comments",
        "training_assignment_members",
        "training_assignments",
        "training_content",
    ):
        op.drop_table(table)
    op.execute("DROP FUNCTION protect_training_assignment()")
    op.execute("DROP FUNCTION protect_training_content()")
