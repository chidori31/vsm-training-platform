from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.application.errors import UseCaseError
from app.application.identity import IdentityService
from app.application.training import TrainingService
from app.application.training_staff import (
    TrainingStaffService,
    access,
    validate_document,
)
from app.persistence.training import StoredTraining
from app.persistence.training_staff import (
    TrainingAssignment,
    TrainingAssignmentMember,
    TrainingContent,
)

pytestmark = pytest.mark.postgres
METHODIST = "demo-methodist"
INSTRUCTOR = "demo-instructor"


@pytest.fixture
def staff(database, clock):
    identity = IdentityService(database)
    for persona in (
        METHODIST,
        INSTRUCTOR,
        "demo-employee",
        "demo-north-03",
        "demo-south-04",
        "demo-other-05",
    ):
        identity.demo_login(persona)
    return TrainingStaffService(database, clock=clock, seed_factory=lambda: 987)


@pytest.fixture
def training(database, clock, staff):
    return TrainingService(database, clock=clock, seed_factory=lambda: 42)


def assignment(staff):
    return staff.create_assignment(
        INSTRUCTOR, key="assignment", title="Учебная группа", mode="demo"
    )[0]


def draft(staff):
    return staff.draft(METHODIST, key="draft")[0]


def test_server_roles_and_group_boundaries(staff, training):
    assert access(INSTRUCTOR) == {
        "role": "instructor",
        "group_id": "demo-company:north:01",
    }
    assert access(METHODIST) == {"role": "methodist", "group_id": None}
    assigned = assignment(staff)
    assert assigned["member_count"] == 2 and assigned["completed_count"] == 0
    for stranger in ("demo-north-03", "demo-south-04", "demo-other-05"):
        assert staff.assignments(stranger) == {"items": []}
        with pytest.raises(UseCaseError) as denied:
            training.start(
                stranger, key="foreign", mode="demo", assignment_id=assigned["id"]
            )
        assert denied.value.code == "assignment_not_found"
        with pytest.raises(UseCaseError):
            staff.assignment_detail(stranger, assigned["id"])
    own = staff.assignment_detail("demo-employee", assigned["id"])
    assert [m["employee_id"] for m in own["members"]] == ["demo-employee"]
    for actor in ("demo-employee", INSTRUCTOR):
        with pytest.raises(UseCaseError):
            staff.list_content(actor)
        with pytest.raises(UseCaseError):
            staff.draft(actor, key="forbidden")
    with pytest.raises(UseCaseError):
        staff.create_assignment(METHODIST, key="bad", title="No", mode="work")
    with pytest.raises(UseCaseError):
        training.start(METHODIST, key="bad", mode="demo")


def test_concurrent_bootstrap_and_draft_receipt(staff, database):
    with ThreadPoolExecutor(max_workers=4) as pool:
        versions = list(pool.map(lambda _: staff.published(), range(4)))
    assert len({v.version for v in versions}) == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: staff.draft(METHODIST, key="same"), range(4)))
    assert len({r[0]["id"] for r in results}) == 1
    assert sum(not duplicate for _, duplicate in results) == 1
    with pytest.raises(UseCaseError) as conflict:
        staff.draft(METHODIST, key="same", source_id="training-bootstrap-v2")
    assert conflict.value.code == "idempotency_conflict"
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(TrainingContent)) == 2


def test_revision_race_and_invalid_draft_saved(staff):
    record = draft(staff)
    raw = deepcopy(record["document"])
    raw["action_durations"]["assist"] = 0

    def attempt(_):
        try:
            return staff.save(
                METHODIST, record["id"], expected_revision=0, document=raw
            )
        except UseCaseError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert "content_revision_conflict" in results
    validation = staff.validate(METHODIST, record["id"])
    assert (
        not validation["valid"] and "action_durations.assist" in validation["errors"][0]
    )
    with pytest.raises(UseCaseError) as bad:
        staff.publish(METHODIST, record["id"], key="publish", expected_revision=1)
    assert bad.value.code == "content_invalid"
    assert staff.get_content(METHODIST, record["id"])["status"] == "draft"


def test_publish_concurrent_replay_and_immutable(staff, database):
    record = draft(staff)
    assert staff.validate(METHODIST, record["id"])["valid"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(
            pool.map(
                lambda _: staff.publish(
                    METHODIST, record["id"], key="publish", expected_revision=0
                ),
                range(3),
            )
        )
    assert results[0][0] == results[1][0] == results[2][0]
    assert sum(not duplicate for _, duplicate in results) == 1
    for operation in (
        lambda: staff.save(
            METHODIST, record["id"], expected_revision=1, document=record["document"]
        ),
        lambda: staff.publish(
            METHODIST, record["id"], key="publish2", expected_revision=1
        ),
    ):
        with pytest.raises(UseCaseError) as immutable:
            operation()
        assert immutable.value.code == "content_immutable"
    with pytest.raises(UseCaseError) as conflict:
        staff.publish(METHODIST, record["id"], key="publish", expected_revision=1)
    assert conflict.value.code == "idempotency_conflict"
    with pytest.raises(DBAPIError), Session(database) as db, db.begin():
        db.execute(
            text("UPDATE training_content SET title='changed' WHERE id=:id"),
            {"id": record["id"]},
        )
    with pytest.raises(DBAPIError), Session(database) as db, db.begin():
        db.execute(
            text("DELETE FROM training_content WHERE id=:id"), {"id": record["id"]}
        )


def test_validation_feasibility_and_overlap_warnings(staff):
    raw = draft(staff)["document"]
    report = validate_document(raw, raw["version"])
    assert report["valid"] and report["warnings"] and report["timeline"]
    short = deepcopy(raw)
    short["modes"]["demo"]["stations"][1]["departure_seconds"] = 175
    report = validate_document(short, short["version"])
    assert not report["valid"] and any("окно станции" in e for e in report["errors"])
    late = deepcopy(raw)
    late["modes"]["demo"]["incident_seconds"]["wellbeing"] = 235
    assert not validate_document(late, late["version"])["valid"]
    missing = deepcopy(raw)
    missing["incidents"][1]["equipment_id"] = "nonexistent"
    assert not validate_document(missing, missing["version"])["valid"]
    missing["equipment"] = [e for e in missing["equipment"] if e["id"] != "radio"]
    feedback = validate_document(missing, missing["version"])
    assert not feedback["valid"]
    assert any("incidents.1.equipment_id" in e for e in feedback["errors"])
    assert any("communication_type" in e and "radio" in e for e in feedback["errors"])
    unavailable = deepcopy(raw)
    unavailable["equipment"][0]["available"] = False
    assert not validate_document(unavailable, unavailable["version"])["valid"]


def test_assignment_duplicate_seed_version_and_active_pinning(
    staff, training, database
):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: assignment(staff), range(2)))
    assert results[0] == results[1]
    assigned = results[0]
    with pytest.raises(UseCaseError) as duplicate:
        staff.create_assignment(
            INSTRUCTOR, key="assignment", title="Different", mode="demo"
        )
    assert duplicate.value.code == "idempotency_conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(
            pool.map(
                lambda _: training.start(
                    "demo-employee",
                    key="run",
                    mode="demo",
                    assignment_id=assigned["id"],
                ),
                range(2),
            )
        )
    assert runs[0][0] == runs[1][0] and runs[0][1] != runs[1][1]
    original = runs[0][0]
    record = draft(staff)
    raw = record["document"]
    raw["title"] = "Новая опубликованная версия"
    staff.save(METHODIST, record["id"], expected_revision=0, document=raw)
    staff.publish(METHODIST, record["id"], key="publish", expected_revision=1)
    peer = training.start(
        "demo-north-02", key="peer", mode="demo", assignment_id=assigned["id"]
    )[0]
    personal = training.start("demo-north-03", key="personal", mode="tutorial")[0]
    with Session(database) as db:
        a, b, c = [db.get(StoredTraining, r["id"]) for r in (original, peer, personal)]
        assert a.definition == b.definition and a.definition["version"] == 1
        assert a.snapshot["world"]["seed"] == b.snapshot["world"]["seed"] == 987
        assert c.definition["version"] == record["version"]
        assert c.definition["title"] == raw["title"]
        assert db.scalar(select(func.count()).select_from(TrainingAssignment)) == 1
    with pytest.raises(UseCaseError) as repeated:
        training.start(
            "demo-employee", key="different", mode="demo", assignment_id=assigned["id"]
        )
    assert repeated.value.code == "assignment_started"


@pytest.mark.parametrize("reported,valid", [(151, False), (127, False), (126, True)])
def test_validation_requires_resolution_and_record_before_mode_end(
    staff, reported, valid
):
    raw = draft(staff)["document"]
    raw["modes"]["tutorial"]["incident_seconds"]["dispute"] = reported
    report = validate_document(raw, raw["version"])
    assert report["valid"] is valid
    if not valid:
        assert any(
            "modes.tutorial.incident_seconds.dispute" in e for e in report["errors"]
        )


@pytest.mark.parametrize("arrival,valid", [(155, False), (142, True)])
def test_validation_counts_station_wait_and_allows_record_at_deadline(
    staff, arrival, valid
):
    raw = draft(staff)["document"]
    tutorial = raw["modes"]["tutorial"]
    tutorial["incident_seconds"]["arrival"] = 15
    tutorial["stations"][1]["arrival_seconds"] = arrival
    tutorial["stations"][1]["departure_seconds"] = 175
    report = validate_document(raw, raw["version"])
    assert report["valid"] is valid
    if not valid:
        assert any(
            "modes.tutorial.incident_seconds.arrival" in e for e in report["errors"]
        )


def test_instructor_only_assigned_completed_and_comments(staff, training, clock):
    personal = training.start("demo-north-03", key="personal", mode="tutorial")[0]
    assigned = assignment(staff)
    run = training.start(
        "demo-employee", key="run", mode="demo", assignment_id=assigned["id"]
    )[0]
    with pytest.raises(UseCaseError):
        staff.instructor_debrief(INSTRUCTOR, personal["id"])
    with pytest.raises(UseCaseError) as pending:
        staff.instructor_debrief(INSTRUCTOR, run["id"])
    assert pending.value.code == "result_not_ready"
    clock.now += timedelta(seconds=240)
    report = staff.instructor_debrief(INSTRUCTOR, run["id"])
    event_id = report["simulation"]["journal"][0]["id"]
    with pytest.raises(UseCaseError) as missing:
        staff.comment(
            INSTRUCTOR, run["id"], key="missing", event_id="invented", body="Проверка"
        )
    assert missing.value.code == "comment_invalid"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: staff.comment(
                    INSTRUCTOR,
                    run["id"],
                    key="comment",
                    event_id=event_id,
                    body="Проверьте последовательность действий",
                ),
                range(2),
            )
        )
    assert results[0][0] == results[1][0] and len(results[0][0]["items"]) == 1
    with pytest.raises(UseCaseError) as conflicting_comment:
        staff.comment(
            INSTRUCTOR, run["id"], key="comment", event_id=event_id, body="Другой текст"
        )
    assert conflicting_comment.value.code == "idempotency_conflict"
    assert (
        staff.comments("demo-employee", run["id"])["items"][0]["event_id"] == event_id
    )
    with pytest.raises(UseCaseError):
        staff.comments("demo-north-02", run["id"])
    with pytest.raises(UseCaseError):
        staff.comment(
            "demo-employee", run["id"], key="self", event_id=event_id, body="No"
        )
    detail = staff.assignment_detail(INSTRUCTOR, assigned["id"])
    assert len(detail["members"]) == 2 and detail["difficulties"]
    assert staff.assignments(INSTRUCTOR)["items"][0]["completed_count"] == 1


def test_instructor_rejects_foreign_group_even_when_author_matches(
    staff, training, database, clock
):
    published = staff.list_content(METHODIST)["items"][0]
    with Session(database) as db, db.begin():
        db.add(
            TrainingAssignment(
                id="foreign-assignment",
                title="Другая группа",
                mode="demo",
                group_id="demo-company:south:01",
                instructor_id=INSTRUCTOR,
                content_id=published["id"],
                seed=12,
                created_at=clock.now,
            )
        )
        db.flush()
        db.add(
            TrainingAssignmentMember(
                assignment_id="foreign-assignment", employee_id="demo-south-04"
            )
        )
    run = training.start(
        "demo-south-04", key="foreign", mode="demo", assignment_id="foreign-assignment"
    )[0]
    clock.now += timedelta(seconds=240)
    training.get(run["id"], "demo-south-04")
    with pytest.raises(UseCaseError):
        staff.assignment_detail(INSTRUCTOR, "foreign-assignment")
    with pytest.raises(UseCaseError):
        staff.instructor_debrief(INSTRUCTOR, run["id"])
    with pytest.raises(UseCaseError):
        staff.comments(INSTRUCTOR, run["id"])
    assert staff.assignments(INSTRUCTOR) == {"items": []}


def test_assignment_is_immutable_and_mode_is_server_owned(staff, training, database):
    assigned = assignment(staff)
    with pytest.raises(UseCaseError) as wrong_mode:
        training.start(
            "demo-employee", key="wrong", mode="work", assignment_id=assigned["id"]
        )
    assert wrong_mode.value.code == "assignment_invalid"
    with pytest.raises(DBAPIError), Session(database) as db, db.begin():
        db.execute(
            text("UPDATE training_assignments SET seed=20 WHERE id=:id"),
            {"id": assigned["id"]},
        )
    with pytest.raises(DBAPIError), Session(database) as db, db.begin():
        db.execute(
            text("DELETE FROM training_assignment_members WHERE assignment_id=:id"),
            {"id": assigned["id"]},
        )


def test_large_and_nonfinite_drafts_are_rejected(staff):
    record = draft(staff)
    for raw in ({"title": "x" * 131073}, {"title": float("nan")}):
        with pytest.raises(UseCaseError) as bad:
            staff.save(METHODIST, record["id"], expected_revision=0, document=raw)
        assert bad.value.code == "content_invalid"
    assert staff.get_content(METHODIST, record["id"])["revision"] == 0


def test_staff_http_roles_strict_intent_and_export(
    staff, database, training, monkeypatch
):
    from app.api.dependencies import get_engine
    from app.api.training import get_training_service
    from app.api.training_staff import get_training_staff_service
    from app.main import app

    monkeypatch.setenv("DEMO_AUTH_ENABLED", "true")
    app.dependency_overrides[get_engine] = lambda: database
    app.dependency_overrides[get_training_service] = lambda: training
    app.dependency_overrides[get_training_staff_service] = lambda: staff
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/training/access").status_code == 401

            def login(persona):
                response = client.post(
                    "/api/v1/auth/demo", json={"persona_id": persona}
                )
                assert response.status_code == 200
                token = response.json()["access_token"]
                client.headers["Authorization"] = "Bearer " + token

            login("demo-employee")
            assert client.get("/api/v1/training/access").json()["role"] == "employee"
            assert client.get("/api/v1/training/content").status_code == 403
            assert (
                client.post(
                    "/api/v1/training/assignments",
                    headers={"Idempotency-Key": "a"},
                    json={"title": "x", "mode": "demo"},
                ).status_code
                == 403
            )
            login(INSTRUCTOR)
            assert client.get("/api/v1/training/access").json()["role"] == "instructor"
            for extra in ("role", "seed", "group_id", "content_id"):
                assert (
                    client.post(
                        "/api/v1/training/assignments",
                        headers={"Idempotency-Key": "a"},
                        json={"title": "x", "mode": "demo", extra: "forged"},
                    ).status_code
                    == 422
                )
            login(METHODIST)
            response = client.post(
                "/api/v1/training/content/drafts",
                headers={"Idempotency-Key": "draft"},
                json={},
            )
            assert response.status_code == 201
            record = response.json()
            path = "/api/v1/training/content/" + record["id"]
            assert client.get(path).json() == record
            record["document"]["action_durations"]["assist"] = 0
            assert (
                client.put(
                    path, json={"expected_revision": 0, "document": record["document"]}
                ).status_code
                == 200
            )
            assert not client.post(path + "/validate").json()["valid"]
            assert (
                client.post(
                    path + "/publish",
                    headers={"Idempotency-Key": "p"},
                    json={"expected_revision": 1},
                ).status_code
                == 422
            )
            login("demo-employee")
            assert client.get(path).status_code == 403
            assert client.post(path + "/validate").status_code == 403
            assert (
                client.put(
                    path, json={"expected_revision": 1, "document": {}}
                ).status_code
                == 403
            )
    finally:
        app.dependency_overrides.clear()
