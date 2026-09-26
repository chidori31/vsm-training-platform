"""Store versioned scenario documents in PostgreSQL JSONB."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260926_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scenario_versions",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", "version"),
        sa.CheckConstraint("version > 0", name="ck_scenario_version_positive"),
        sa.CheckConstraint(
            "jsonb_typeof(document) = 'object' "
            "AND document ?& ARRAY['id', 'version', 'schema_version'] "
            "AND document->>'id' = id "
            "AND document->>'version' = version::text "
            "AND document->>'schema_version' = '1' "
            "AND jsonb_typeof(document->'id') = 'string' "
            "AND jsonb_typeof(document->'version') = 'number' "
            "AND jsonb_typeof(document->'schema_version') = 'number'",
            name="ck_scenario_document_identity",
        ),
    )


def downgrade() -> None:
    op.drop_table("scenario_versions")
