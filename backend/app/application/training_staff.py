"""Server-owned demo roles, content publication and instructor permissions.

The allowlist is a demo identity boundary, not production authentication. Group
membership comes from this server configuration, never from client role claims.
"""

import copy
import json
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from secrets import randbelow
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import Engine, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.application.anti_cheat import database_time
from app.application.demo_personas import PERSONAS
from app.application.errors import UseCaseError
from app.application.training import TrainingService, definition_path, digest
from app.domain.events import bounded_text
from app.persistence.identity import UserProfile
from app.persistence.training import StoredTraining
from app.persistence.training_staff import (
    TrainingAssignment,
    TrainingAssignmentMember,
    TrainingComment,
    TrainingContent,
    TrainingStaffReceipt,
)
from app.training.schema import DefinitionDocument, load_definition

CONTENT_LOCK = 9271101


def access(employee_id: str) -> dict[str, Any]:
    persona = next((p for p in PERSONAS if p["id"] == employee_id), None)
    if employee_id == "demo-methodist":
        return {"role": "methodist", "group_id": None}
    role = "instructor" if employee_id == "demo-instructor" else "employee"
    group = (
        ":".join(persona[k] for k in ("company_id", "depot_id", "brigade_id"))
        if persona
        else None
    )
    return {"role": role, "group_id": group}


def require_role(actor: str, role: str) -> dict[str, Any]:
    grant = access(actor)
    if grant["role"] != role:
        raise UseCaseError("forbidden", "Недостаточно прав для этого действия")
    return grant


def content_view(row: TrainingContent, *, document: bool = True) -> dict[str, Any]:
    result = dict(
        id=row.id,
        version=row.version,
        title=row.title,
        status=row.status,
        revision=row.revision,
    )
    if document:
        result["document"] = copy.deepcopy(row.document)
    return result


def validate_document(raw: dict[str, Any], version: int) -> dict[str, Any]:
    """Return bounded, field-oriented author feedback, including feasibility."""
    errors: list[str] = []
    warnings: list[str] = []
    timeline: list[dict[str, Any]] = []
    # Resolve references before strict model validation so editors can identify the
    # exact field even when a model-level invariant would reject the whole document.
    raw_equipment = raw.get("equipment")
    raw_incidents = raw.get("incidents")
    raw_zones = raw.get("zones")
    if isinstance(raw_equipment, list) and isinstance(raw_incidents, list):
        available = {
            e["id"]
            for e in raw_equipment
            if isinstance(e, dict)
            and isinstance(e.get("id"), str)
            and e.get("available", True) is not False
        }
        zones = (
            {
                z["id"]
                for z in raw_zones
                if isinstance(z, dict) and isinstance(z.get("id"), str)
            }
            if isinstance(raw_zones, list)
            else set()
        )
        for index, incident in enumerate(raw_incidents):
            if not isinstance(incident, dict):
                continue
            equipment_id = incident.get("equipment_id")
            if isinstance(equipment_id, str) and equipment_id not in available:
                errors.append(
                    f"incidents.{index}.equipment_id: оборудование недоступно"
                )
            if incident.get("communication_type") and "radio" not in available:
                errors.append(
                    f"incidents.{index}.communication_type: нужна радиостанция radio"
                )
            locations = incident.get("zones")
            if isinstance(locations, list) and any(
                not isinstance(z, str) or z not in zones for z in locations
            ):
                errors.append(f"incidents.{index}.zones: неизвестная зона")
    try:
        document = DefinitionDocument.model_validate(raw)
    except ValidationError as exc:
        errors.extend(
            f"{'.'.join(map(str, e['loc'])) or 'document'}: {e['msg']}"
            for e in exc.errors(include_url=False, include_context=False)[:100]
        )
        # Malformed drafts remain savable; only valid structures reach feasibility.
        return dict(valid=False, errors=errors, warnings=warnings, timeline=timeline)
    if document.version != version:
        errors.append(f"version: ожидается серверная версия {version}")
    equipment = {e.id: e for e in document.equipment}
    stations = {s.id for s in document.stations}
    durations = document.action_durations
    for index, incident in enumerate(document.incidents):
        field = f"incidents.{index}"
        if incident.station_id and incident.station_id not in stations:
            errors.append(f"{field}.station_id: неизвестная станция")
        if incident.kind == "station" and not incident.station_id:
            errors.append(f"{field}.station_id: для передачи требуется станция")
        if incident.communication_type and "radio" not in equipment:
            errors.append(f"{field}.communication_type: для связи нужна радиостанция")
        if incident.equipment_id and incident.equipment_id not in equipment:
            errors.append(f"{field}.equipment_id: оборудование недоступно")
    for mode, schedule in document.modes.items():
        spans = []
        for incident in document.incidents:
            if incident.id not in schedule.incident_seconds:
                continue
            at = schedule.incident_seconds[incident.id]
            timeline.append(
                dict(at_seconds=at, title=f"{mode}: {incident.title}", kind="incident")
            )
            # A best-case lower bound: position and equipment may be prepared before
            # a report; observation and verification cannot precede the report.
            preparation = durations["inspect"] + durations["verify"]
            if incident.communication_type:
                # Contact after observation, verify while waiting. These minimum
                # delays are the unchanged v1 service response rules.
                delay = 110 if incident.communication_type == "chief" else 30
                preparation = (
                    durations["inspect"]
                    + durations["contact"]
                    + max(durations["verify"], delay)
                )
            assistance_start = at + preparation
            if incident.station_id:
                station = next(
                    (s for s in schedule.stations if s.id == incident.station_id), None
                )
                if station:
                    assistance_start = max(assistance_start, station.arrival_seconds)
                if (
                    station
                    and assistance_start + durations["assist"]
                    >= station.departure_seconds
                ):
                    errors.append(
                        f"modes.{mode}.stations.{incident.station_id}: "
                        "передача с длительностью действия не помещается в окно станции"
                    )
            # Assistance first schedules the engine's 20-second outcome check.
            # Only then can the learner record the resolved incident. The record
            # may finish exactly at the mode deadline; engine-2 applies its effect
            # before closing the shift. Station waiting also consumes mode time.
            completion = (
                assistance_start + durations["assist"] + 20 + durations["record"]
            )
            needed = completion - at
            if completion > schedule.duration_seconds:
                errors.append(
                    f"modes.{mode}.incident_seconds.{incident.id}: "
                    f"недостаточно времени для помощи, контроля и записи ({needed} с)"
                )
            spans.append((at, at + needed, incident.title))
        for station in schedule.stations:
            timeline.extend(
                [
                    dict(
                        at_seconds=station.arrival_seconds,
                        title=f"{mode}: {station.title} — прибытие",
                        kind="station",
                    ),
                    dict(
                        at_seconds=station.departure_seconds,
                        title=f"{mode}: {station.title} — отправление",
                        kind="station",
                    ),
                ]
            )
        spans.sort()
        for index, span in enumerate(spans):
            for other in spans[index + 1 :]:
                if other[0] < span[1]:
                    warnings.append(
                        f"modes.{mode}: пересекаются действия «{span[2]}» "
                        f"и «{other[2]}»; потребуется расставить приоритеты"
                    )
    timeline.sort(key=lambda e: (e["at_seconds"], e["title"]))
    return dict(valid=not errors, errors=errors, warnings=warnings, timeline=timeline)


class TrainingStaffService:
    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[Session], datetime] = database_time,
        seed_factory: Callable[[], int] = lambda: randbelow(2147483648),
    ) -> None:
        self.engine, self.clock, self.seed_factory = engine, clock, seed_factory

    @staticmethod
    def _lock(db: Session) -> None:
        # Small synthetic catalog: one transaction lock gives deterministic bootstrap,
        # revision allocation, publication order and durable idempotency races.
        db.execute(text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": CONTENT_LOCK})

    def _bootstrap(self, db: Session) -> TrainingContent:
        self._lock(db)
        latest = db.scalar(
            select(TrainingContent)
            .where(TrainingContent.status == "published")
            .order_by(
                TrainingContent.published_at.desc(), TrainingContent.version.desc()
            )
            .limit(1)
        )
        if latest is None:
            source = load_definition(definition_path())
            latest = TrainingContent(
                id="training-bootstrap-v2",
                version=source.version,
                title=source.title,
                status="published",
                revision=0,
                document=source.model_dump(),
                created_at=self.clock(db),
                published_at=self.clock(db),
            )
            db.add(latest)
            db.flush()
        return latest

    def published(self) -> DefinitionDocument:
        with Session(self.engine) as db, db.begin():
            return DefinitionDocument.model_validate(self._bootstrap(db).document)

    @staticmethod
    def _receipt(
        db: Session, actor: str, key: str, args: dict[str, Any]
    ) -> dict[str, Any] | None:
        bounded_text(key, "idempotency key")
        receipt = db.get(TrainingStaffReceipt, (actor, key))
        if receipt:
            if receipt.fingerprint != digest(args):
                raise UseCaseError(
                    "idempotency_conflict",
                    "Этот ключ уже использован с другими параметрами",
                )
            return copy.deepcopy(receipt.response)
        return None

    @staticmethod
    def _remember(
        db: Session, actor: str, key: str, args: dict[str, Any], result: dict[str, Any]
    ) -> None:
        db.add(
            TrainingStaffReceipt(
                actor_id=actor,
                key=key,
                fingerprint=digest(args),
                response=json.loads(json.dumps(result, default=str)),
            )
        )

    @staticmethod
    def _content(db: Session, identity: str) -> TrainingContent:
        row = db.get(TrainingContent, identity)
        if row is None:
            raise UseCaseError("content_not_found", "Версия содержания не найдена")
        return row

    def list_content(self, actor: str) -> dict[str, Any]:
        require_role(actor, "methodist")
        with Session(self.engine) as db, db.begin():
            self._bootstrap(db)
            return {
                "items": [
                    content_view(r, document=False)
                    for r in db.scalars(
                        select(TrainingContent).order_by(TrainingContent.version.desc())
                    )
                ]
            }

    def get_content(self, actor: str, identity: str) -> dict[str, Any]:
        require_role(actor, "methodist")
        with Session(self.engine) as db:
            return content_view(self._content(db, identity))

    def draft(
        self, actor: str, *, key: str, source_id: str | None = None
    ) -> tuple[dict[str, Any], bool]:
        require_role(actor, "methodist")
        args = dict(operation="draft", source_id=source_id)
        with Session(self.engine) as db, db.begin():
            latest = self._bootstrap(db)
            if (result := self._receipt(db, actor, key, args)) is not None:
                return result, True
            source = self._content(db, source_id) if source_id else latest
            version = (db.scalar(select(func.max(TrainingContent.version))) or 0) + 1
            raw = copy.deepcopy(source.document)
            raw["version"] = version
            row = TrainingContent(
                id=str(uuid4()),
                version=version,
                title=source.title,
                status="draft",
                revision=0,
                document=raw,
                created_at=self.clock(db),
            )
            db.add(row)
            db.flush()
            result = content_view(row)
            self._remember(db, actor, key, args, result)
            return result, False

    def save(
        self,
        actor: str,
        identity: str,
        *,
        expected_revision: int,
        document: dict[str, Any],
    ) -> dict[str, Any]:
        require_role(actor, "methodist")
        try:
            size = len(
                json.dumps(document, ensure_ascii=False, allow_nan=False).encode()
            )
        except (ValueError, RecursionError) as exc:
            raise UseCaseError(
                "content_invalid", "document: ожидается конечный JSON"
            ) from exc
        if size > 128 * 1024:
            raise UseCaseError("content_invalid", "document: превышен предел 128 КиБ")
        with Session(self.engine) as db, db.begin():
            self._lock(db)
            row = self._content(db, identity)
            self._editable(row, expected_revision)
            row.document = copy.deepcopy(document)
            title = document.get("title")
            if isinstance(title, str) and title.strip():
                row.title = title[:1500]
            row.revision += 1
            db.flush()
            return content_view(row)

    @staticmethod
    def _editable(row: TrainingContent, revision: int) -> None:
        if row.status != "draft":
            raise UseCaseError(
                "content_immutable",
                "Опубликованная версия неизменяема; создайте черновик",
            )
        if row.revision != revision:
            raise UseCaseError(
                "content_revision_conflict",
                "Черновик изменился; обновите версию перед сохранением",
            )

    def validate(self, actor: str, identity: str) -> dict[str, Any]:
        require_role(actor, "methodist")
        with Session(self.engine) as db:
            row = self._content(db, identity)
            return validate_document(row.document, row.version)

    def publish(
        self, actor: str, identity: str, *, key: str, expected_revision: int
    ) -> tuple[dict[str, Any], bool]:
        require_role(actor, "methodist")
        args = dict(
            operation="publish", id=identity, expected_revision=expected_revision
        )
        with Session(self.engine) as db, db.begin():
            self._lock(db)
            if (result := self._receipt(db, actor, key, args)) is not None:
                return result, True
            row = self._content(db, identity)
            self._editable(row, expected_revision)
            report = validate_document(row.document, row.version)
            if not report["valid"]:
                raise UseCaseError(
                    "content_invalid", "; ".join(report["errors"])[:6000]
                )
            row.status, row.published_at, row.revision = (
                "published",
                self.clock(db),
                row.revision + 1,
            )
            db.flush()
            result = content_view(row)
            self._remember(db, actor, key, args, result)
            return result, False

    @staticmethod
    def _assignment(db: Session, actor: str, identity: str) -> TrainingAssignment:
        grant = access(actor)
        row = db.get(TrainingAssignment, identity)
        allowed = (
            row is not None
            and row.group_id == grant["group_id"]
            and (
                (grant["role"] == "instructor" and row.instructor_id == actor)
                or (
                    grant["role"] == "employee"
                    and db.get(TrainingAssignmentMember, (identity, actor)) is not None
                )
            )
        )
        if not allowed or row is None:
            raise UseCaseError("assignment_not_found", "Назначение не найдено")
        return row

    @staticmethod
    def _assignment_view(db: Session, row: TrainingAssignment) -> dict[str, Any]:
        members = (
            db.scalar(
                select(func.count())
                .select_from(TrainingAssignmentMember)
                .where(TrainingAssignmentMember.assignment_id == row.id)
            )
            or 0
        )
        completed = (
            db.scalar(
                select(func.count())
                .select_from(StoredTraining)
                .where(
                    StoredTraining.assignment_id == row.id,
                    StoredTraining.source_id.is_(None),
                    StoredTraining.status == "completed",
                )
            )
            or 0
        )
        return dict(
            id=row.id,
            title=row.title,
            mode=row.mode,
            created_at=row.created_at.isoformat(),
            member_count=members,
            completed_count=completed,
        )

    def create_assignment(
        self, actor: str, *, key: str, title: str, mode: str
    ) -> tuple[dict[str, Any], bool]:
        grant = require_role(actor, "instructor")
        if mode not in {"work", "demo"} or not title.strip() or len(title) > 200:
            raise UseCaseError(
                "assignment_invalid", "Укажите название и режим work или demo"
            )
        args = dict(operation="assignment", title=title, mode=mode)
        with Session(self.engine) as db, db.begin():
            content = self._bootstrap(db)
            if (result := self._receipt(db, actor, key, args)) is not None:
                return result, True
            row = TrainingAssignment(
                id=str(uuid4()),
                title=title,
                mode=mode,
                group_id=grant["group_id"],
                instructor_id=actor,
                content_id=content.id,
                seed=self.seed_factory(),
                created_at=self.clock(db),
            )
            db.add(row)
            db.flush()
            for persona in PERSONAS:
                member = access(persona["id"])
                if member["role"] == "employee" and member["group_id"] == row.group_id:
                    db.execute(
                        insert(UserProfile)
                        .values(**persona)
                        .on_conflict_do_nothing(index_elements=["id"])
                    )
                    db.add(
                        TrainingAssignmentMember(
                            assignment_id=row.id, employee_id=persona["id"]
                        )
                    )
            db.flush()
            result = self._assignment_view(db, row)
            self._remember(db, actor, key, args, result)
            return result, False

    def assignments(self, actor: str) -> dict[str, Any]:
        grant = access(actor)
        if grant["role"] == "methodist":
            raise UseCaseError(
                "forbidden", "Назначения доступны сотруднику и инструктору"
            )
        with Session(self.engine) as db:
            rows = db.scalars(
                select(TrainingAssignment)
                .where(TrainingAssignment.group_id == grant["group_id"])
                .order_by(TrainingAssignment.created_at.desc(), TrainingAssignment.id)
            )
            return {
                "items": [
                    self._assignment_view(db, row)
                    for row in rows
                    if (
                        row.instructor_id == actor
                        if grant["role"] == "instructor"
                        else db.get(TrainingAssignmentMember, (row.id, actor))
                        is not None
                    )
                ]
            }

    def assignment_detail(self, actor: str, identity: str) -> dict[str, Any]:
        service = TrainingService(self.engine, clock=self.clock)
        with Session(self.engine) as db:
            assignment = self._assignment(db, actor, identity)
            result: dict[str, Any] = dict(
                id=assignment.id,
                title=assignment.title,
                mode=assignment.mode,
                members=[],
                difficulties=[],
            )
            members = list(
                db.scalars(
                    select(UserProfile)
                    .join(
                        TrainingAssignmentMember,
                        TrainingAssignmentMember.employee_id == UserProfile.id,
                    )
                    .where(TrainingAssignmentMember.assignment_id == identity)
                    .order_by(UserProfile.id)
                )
            )
            counts: Counter[str] = Counter()
            for member in members:
                if access(actor)["role"] == "employee" and member.id != actor:
                    continue
                run = db.scalar(
                    select(StoredTraining).where(
                        StoredTraining.assignment_id == identity,
                        StoredTraining.employee_id == member.id,
                        StoredTraining.source_id.is_(None),
                    )
                )
                view = service.get(run.id, member.id) if run else None
                result["members"].append(
                    dict(
                        employee_id=member.id,
                        display_name=member.display_name,
                        run_id=run.id if run else None,
                        status=view["status"] if view else "not_started",
                        metrics=view["metrics"]
                        if view and view["status"] == "completed"
                        else None,
                    )
                )
                if run and view and view["status"] == "completed":
                    report = service.debrief(run.id, member.id)
                    counts.update(
                        c["title"]
                        for c in report["assessment"]["criteria"]
                        if c["met"] is False
                    )
            result["difficulties"] = [
                dict(title=title, count=count)
                for title, count in sorted(counts.items())
            ]
            return result

    def start_assignment(
        self,
        actor: str,
        *,
        key: str,
        assignment_id: str,
        mode: str,
        competency_id: str | None,
        training: TrainingService,
    ) -> tuple[dict[str, Any], bool]:
        require_role(actor, "employee")
        # Serialize assignment attempts through completion of start_pinned. Its own
        # profile lock then orders this command with all personal starts as well.
        with Session(self.engine) as db, db.begin():
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 11))"),
                {"identity": actor + ":" + assignment_id},
            )
            assignment = self._assignment(db, actor, assignment_id)
            if mode != assignment.mode or competency_id is not None:
                raise UseCaseError(
                    "assignment_invalid", "Режим определяется назначением инструктора"
                )
            existing = db.scalar(
                select(StoredTraining).where(
                    StoredTraining.employee_id == actor,
                    StoredTraining.assignment_id == assignment_id,
                    StoredTraining.source_id.is_(None),
                )
            )
            if existing and existing.start_key != key:
                raise UseCaseError(
                    "assignment_started",
                    "Назначение уже начато; откройте существующий прогон",
                )
            content = self._content(db, assignment.content_id)
            return training.start_pinned(
                actor,
                key=key,
                mode=mode,
                competency_id=None,
                assignment_id=assignment.id,
                seed=assignment.seed,
                document=DefinitionDocument.model_validate(content.document),
            )

    def _run_owner(
        self, db: Session, actor: str, identity: str, *, employee_read: bool = False
    ) -> str:
        row = db.get(StoredTraining, identity)
        if (
            row
            and employee_read
            and access(actor)["role"] == "employee"
            and row.employee_id == actor
        ):
            return row.employee_id
        if (
            row
            and access(actor)["role"] == "instructor"
            and row.assignment_id
            and row.source_id is None
        ):
            self._assignment(db, actor, row.assignment_id)
            if (
                db.get(TrainingAssignmentMember, (row.assignment_id, row.employee_id))
                and access(row.employee_id)["group_id"] == access(actor)["group_id"]
            ):
                return row.employee_id
        raise UseCaseError("training_not_found", "Прогон не найден")

    def instructor_debrief(self, actor: str, identity: str) -> dict[str, Any]:
        require_role(actor, "instructor")
        with Session(self.engine) as db:
            owner = self._run_owner(db, actor, identity)
        return TrainingService(self.engine, clock=self.clock).debrief(identity, owner)

    @staticmethod
    def _comments(db: Session, identity: str) -> dict[str, Any]:
        rows = db.execute(
            select(TrainingComment, UserProfile.display_name)
            .join(UserProfile, TrainingComment.author_id == UserProfile.id)
            .where(TrainingComment.run_id == identity)
            .order_by(TrainingComment.created_at, TrainingComment.id)
        )
        return {
            "items": [
                dict(
                    id=row.id,
                    event_id=row.event_id,
                    text=row.text,
                    author_name=name,
                    created_at=row.created_at.isoformat(),
                )
                for row, name in rows
            ]
        }

    def comments(self, actor: str, identity: str) -> dict[str, Any]:
        with Session(self.engine) as db:
            self._run_owner(db, actor, identity, employee_read=True)
            return self._comments(db, identity)

    def comment(
        self, actor: str, identity: str, *, key: str, event_id: str, body: str
    ) -> tuple[dict[str, Any], bool]:
        require_role(actor, "instructor")
        if not body.strip() or len(body) > 2000:
            raise UseCaseError(
                "comment_invalid", "Комментарий должен содержать от 1 до 2000 символов"
            )
        args = dict(operation="comment", id=identity, event_id=event_id, text=body)
        with Session(self.engine) as db, db.begin():
            self._lock(db)
            owner = self._run_owner(db, actor, identity)
            if (result := self._receipt(db, actor, key, args)) is not None:
                return result, True
            # Only completed, authorized evidence is commentable; never client events.
            report = TrainingService(self.engine, clock=self.clock).debrief(
                identity, owner
            )
            if event_id not in {e["id"] for e in report["simulation"]["journal"]}:
                raise UseCaseError(
                    "comment_invalid", "event_id: событие отсутствует в журнале прогона"
                )
            db.add(
                TrainingComment(
                    id=str(uuid4()),
                    run_id=identity,
                    author_id=actor,
                    event_id=event_id,
                    text=body,
                    created_at=self.clock(db),
                )
            )
            db.flush()
            result = self._comments(db, identity)
            self._remember(db, actor, key, args, result)
            return result, False
