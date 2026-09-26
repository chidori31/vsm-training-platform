import json
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Integer, String, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db import Base
from app.scenarios.schema import ScenarioDocument


class ScenarioVersion(Base):
    __tablename__ = "scenario_versions"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_scenario_version_positive"),
        CheckConstraint(
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

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ScenarioVersionConflict(ValueError):
    """The same published identity was supplied with different content."""


class ScenarioRepository:
    """An explicit version is required. The caller owns the transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, document: ScenarioDocument) -> bool:
        # Revalidate even a model assembled with model_construct/model_copy.
        checked = ScenarioDocument.model_validate_json(document.model_dump_json())
        payload = checked.model_dump(mode="json")
        statement = (
            insert(ScenarioVersion)
            .values(id=checked.id, version=checked.version, document=payload)
            .on_conflict_do_nothing(index_elements=["id", "version"])
            .returning(ScenarioVersion.id)
        )
        if self.session.scalar(statement) is not None:
            return True
        existing = self.get(checked.id, checked.version)
        if existing != checked:
            raise ScenarioVersionConflict(
                f"Scenario {checked.id}@{checked.version} has different content; "
                "increment version"
            )
        return False

    def get(self, scenario_id: str, version: int) -> ScenarioDocument | None:
        payload = self.session.scalar(
            select(ScenarioVersion.document).where(
                ScenarioVersion.id == scenario_id, ScenarioVersion.version == version
            )
        )
        if payload is None:
            return None
        return ScenarioDocument.model_validate_json(json.dumps(payload))
