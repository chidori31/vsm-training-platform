from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.identity import IdentityService
from app.persistence.gamification import SessionReward
from app.persistence.identity import DemoToken
from app.persistence.sessions import StoredSession

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "different"),
        ("scenario_id", "other"),
        ("scenario_version", 2),
        ("status", "completed"),
        ("format_version", 1),
        ("decisions", [{}]),
        ("employee_id", None),
    ],
)
def test_database_rejects_inconsistent_snapshot_envelope(
    database, started, field, value
):
    with Session(database) as db:
        before = deepcopy(db.get(StoredSession, started.session.id).snapshot)
    with pytest.raises(IntegrityError), Session(database) as db, db.begin():
        row = db.get(StoredSession, started.session.id)
        row.snapshot = before | {field: value}
    with Session(database) as db:
        assert db.get(StoredSession, started.session.id).snapshot == before


@pytest.fixture
def reward(database, service, scenario):
    IdentityService(database).demo_login()
    view = service.start(
        scenario_id=scenario.id, scenario_version=1, employee_id="demo-employee"
    )
    service.decide(
        view.session.id,
        node_id="start",
        choice_id="help",
        decision_id="one",
        expected_sequence=0,
    )
    service.decide(
        view.session.id,
        node_id="followup",
        choice_id="finish",
        decision_id="two",
        expected_sequence=1,
    )
    return view.session.id


@pytest.mark.parametrize(
    "field,value",
    [
        ("xp", 131),
        ("xp", -1),
        ("competencies", []),
        ("competencies", {"communication": -1}),
        ("competencies", {"communication": 1.5}),
        ("competencies", {"communication": "999"}),
        ("critical", {}),
        ("critical", [1]),
    ],
)
def test_database_rejects_impossible_reward_data(database, reward, field, value):
    with pytest.raises(IntegrityError), Session(database) as db, db.begin():
        setattr(db.get(SessionReward, reward), field, value)
    with Session(database) as db:
        assert db.get(SessionReward, reward).xp == 120


def test_database_rejects_plaintext_token_storage(database):
    IdentityService(database).demo_login()
    with pytest.raises(IntegrityError), Session(database) as db, db.begin():
        db.scalars(select(DemoToken)).first().token_hash = "plain-bearer-token"
