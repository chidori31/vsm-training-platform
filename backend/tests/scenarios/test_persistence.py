import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from alembic import command

pytestmark = pytest.mark.postgres


@pytest.fixture
def database():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
    admin = create_engine(url)
    schema = "test_scenarios_" + uuid4().hex
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


def test_migration_downgrade_and_upgrade_in_isolated_schema(database):
    with database.begin() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.check(config)
        assert "scenario_versions" in inspect(connection).get_table_names()
        command.downgrade(config, "base")
        assert "scenario_versions" not in inspect(connection).get_table_names()
        command.upgrade(config, "head")
        command.check(config)


def test_committed_documents_round_trip_and_versions_are_pinned(database, document):
    from app.persistence.scenarios import ScenarioRepository
    from app.scenarios.schema import ScenarioDocument

    first = ScenarioDocument.model_validate_json(json.dumps(document))
    document.update(version=2, title="Revised synthetic test")
    second = ScenarioDocument.model_validate_json(json.dumps(document))
    with Session(database) as session, session.begin():
        repo = ScenarioRepository(session)
        assert repo.add(first) is True
        assert repo.add(first) is False
        assert repo.add(second) is True
    with Session(database) as session:
        repo = ScenarioRepository(session)
        assert repo.get(first.id, 1).to_domain() == first.to_domain()
        assert repo.get(first.id, 2).to_domain() == second.to_domain()
        assert repo.get(first.id, 3) is None


def test_conflicting_version_rolls_back_whole_transaction(database, document):
    from app.persistence.scenarios import ScenarioRepository, ScenarioVersionConflict
    from app.scenarios.schema import ScenarioDocument

    original = ScenarioDocument.model_validate_json(json.dumps(document))
    with Session(database) as session, session.begin():
        ScenarioRepository(session).add(original)
    document["title"] = "Changed without incrementing version"
    conflict = ScenarioDocument.model_validate_json(json.dumps(document))
    document["id"] = "another-scenario"
    another = ScenarioDocument.model_validate_json(json.dumps(document))
    with Session(database) as session:
        with pytest.raises(ScenarioVersionConflict), session.begin():
            repo = ScenarioRepository(session)
            repo.add(another)
            repo.add(conflict)
    with Session(database) as session:
        repo = ScenarioRepository(session)
        assert repo.get(another.id, 1) is None
        assert repo.get(original.id, 1) == original


def test_concurrent_import_is_idempotent(database, document):
    from app.persistence.scenarios import ScenarioRepository, ScenarioVersion
    from app.scenarios.schema import ScenarioDocument

    dto = ScenarioDocument.model_validate_json(json.dumps(document))
    barrier = Barrier(2)

    def save():
        with Session(database) as session, session.begin():
            barrier.wait(timeout=10)
            return ScenarioRepository(session).add(dto)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save) for _ in range(2)]
        assert sorted(f.result(timeout=20) for f in futures) == [False, True]
    with Session(database) as session:
        assert len(session.scalars(select(ScenarioVersion)).all()) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"id": "different", "version": 1, "schema_version": 1},
        {"id": "synthetic-test", "version": 0, "schema_version": 1},
        {"id": "synthetic-test", "version": 1, "schema_version": 2},
    ],
)
def test_database_rejects_mismatched_document_identity(database, payload):
    from app.persistence.scenarios import ScenarioVersion

    with Session(database) as session, pytest.raises(IntegrityError), session.begin():
        session.add(ScenarioVersion(id="synthetic-test", version=1, document=payload))
        session.flush()


def test_repository_revalidates_documents_on_read(database, document):
    from pydantic import ValidationError

    from app.persistence.scenarios import ScenarioRepository, ScenarioVersion

    document["nodes"][0]["choices"][0]["destination"] = "ghost"
    with Session(database) as session, session.begin():
        session.add(ScenarioVersion(id=document["id"], version=1, document=document))
    with Session(database) as session, pytest.raises(ValidationError):
        ScenarioRepository(session).get(document["id"], 1)


def test_cli_import_and_conflict_rollback(
    database, document, tmp_path, monkeypatch, capsys
):
    from app.persistence.scenarios import ScenarioRepository
    from app.scenarios.__main__ import main

    monkeypatch.setenv(
        "DATABASE_URL", database.url.render_as_string(hide_password=False)
    )
    original = tmp_path / "original.json"
    original.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["scenarios", "import", str(original)])
    assert main() == 0
    assert "Imported: 1; unchanged: 0" in capsys.readouterr().out
    assert main() == 0
    assert "Imported: 0; unchanged: 1" in capsys.readouterr().out

    document["title"] = "Conflicting title"
    conflict = tmp_path / "conflict.json"
    conflict.write_text(json.dumps(document), encoding="utf-8")
    document["id"] = "another-scenario"
    another = tmp_path / "another.json"
    another.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv", ["scenarios", "import", str(another), str(conflict)]
    )
    assert main() == 1
    output = capsys.readouterr()
    assert "increment version" in output.err
    assert "Imported" not in output.out
    with Session(database) as session:
        assert ScenarioRepository(session).get("another-scenario", 1) is None
