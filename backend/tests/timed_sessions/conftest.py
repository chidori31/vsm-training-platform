import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from alembic import command
from app.persistence.scenarios import ScenarioRepository
from app.scenarios.schema import ScenarioDocument


@pytest.fixture
def database():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
    admin = create_engine(url)
    schema = "test_timed_sessions_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(
        make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    )
    try:
        with engine.begin() as connection:
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


@pytest.fixture
def scenario(database):
    def effect(metric, delta, **extra):
        return {"type": "add_score", "metric": metric, "delta": delta, **extra}

    document = ScenarioDocument.model_validate_json(
        json.dumps(
            {
                "schema_version": 1,
                "id": "timed-integration",
                "version": 1,
                "title": "Synthetic transactional timeout scenario",
                "start_node_id": "start",
                "competency_ids": ["communication"],
                "nodes": [
                    {
                        "id": "start",
                        "text": "Choose before the first deadline",
                        "time_limit_seconds": 15,
                        "choices": [
                            {
                                "id": "help",
                                "text": "Help safely",
                                "destination": "followup",
                                "explanation": "Help improves separate metrics.",
                                "effects": [
                                    effect("passenger_loyalty", 60),
                                    effect("safety_rating", 5),
                                    effect(
                                        "competency",
                                        3,
                                        competency_id="communication",
                                    ),
                                ],
                            },
                            {
                                "id": "decline",
                                "text": "Decline",
                                "destination": "done",
                                "explanation": "Both scores reach their lower bounds.",
                                "effects": [
                                    effect("passenger_loyalty", -70),
                                    effect("safety_rating", -60),
                                ],
                            },
                        ],
                        "timeout": {
                            "destination": "recovery",
                            "explanation": "Late handling reduces safety and loyalty.",
                            "effects": [
                                effect("safety_rating", -20),
                                effect("passenger_loyalty", -5),
                            ],
                        },
                    },
                    {
                        "id": "followup",
                        "text": "Complete the follow-up",
                        "time_limit_seconds": 10,
                        "choices": [
                            {
                                "id": "finish",
                                "text": "Finish",
                                "destination": "done",
                                "explanation": "The training is complete.",
                                "effects": [effect("passenger_loyalty", -3)],
                            }
                        ],
                        "timeout": {
                            "destination": "expired",
                            "explanation": "The follow-up was missed.",
                            "effects": [effect("safety_rating", -15)],
                        },
                    },
                    {
                        "id": "recovery",
                        "text": "Recover after the timeout",
                        "choices": [
                            {
                                "id": "recover",
                                "text": "Recover safely",
                                "destination": "done",
                                "explanation": "Recovery restores both scores.",
                                "condition": {
                                    "predicates": [
                                        {
                                            "metric": "safety_rating",
                                            "operator": "lte",
                                            "value": 40,
                                        }
                                    ]
                                },
                                "effects": [
                                    effect("passenger_loyalty", 4),
                                    effect("safety_rating", 10),
                                ],
                            },
                            {
                                "id": "unavailable",
                                "text": "Requires a high safety score",
                                "destination": "done",
                                "explanation": "This condition is not satisfied.",
                                "condition": {
                                    "predicates": [
                                        {
                                            "metric": "safety_rating",
                                            "operator": "gte",
                                            "value": 99,
                                        }
                                    ]
                                },
                            },
                        ],
                    },
                    {"id": "done", "text": "Finished", "terminal": True},
                    {"id": "expired", "text": "Expired", "terminal": True},
                ],
            }
        )
    )
    with Session(database) as session, session.begin():
        ScenarioRepository(session).add(document)
    return document.to_domain()


class MutableClock:
    def __init__(self, now):
        self.now = now

    def __call__(self, session):
        return self.now


@pytest.fixture
def clock():
    # Worker candidate selection uses the real DB clock; every test deadline is
    # therefore selectable while the injected domain time controls eligibility.
    return MutableClock(datetime.now(UTC) - timedelta(days=1))


@pytest.fixture
def service(database, scenario, clock):
    from app.application.sessions import SessionService

    return SessionService(database, clock=clock)


@pytest.fixture
def started(service, scenario):
    return service.start(
        scenario_id=scenario.id,
        scenario_version=scenario.version,
        employee_id="synthetic-employee",
    )
