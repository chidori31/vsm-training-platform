import subprocess
import sys
from pathlib import Path
from threading import Event

import pytest
from sqlalchemy.exc import OperationalError


def test_worker_sweeps_without_user_requests_and_retries_database_outage():
    from app.worker import run_worker

    stop = Event()
    calls = []

    def sweep():
        calls.append("sweep")
        if len(calls) == 1:
            raise OperationalError("unavailable", {}, None)
        stop.set()
        return 1

    run_worker(sweep, stop=stop, poll_seconds=0.01)
    assert calls == ["sweep", "sweep"]


def test_stopped_worker_does_not_process_more_sessions():
    from app.worker import run_worker

    stop = Event()
    stop.set()
    calls = []
    run_worker(lambda: calls.append(1), stop=stop, poll_seconds=0.01)
    assert calls == []


@pytest.mark.parametrize("entrypoint", ["app.application.training", "app.worker"])
def test_standalone_training_entrypoint_resolves_assignment_metadata(entrypoint):
    # HTTP routes and Alembic import every model; a fresh worker process does not.
    # Keep this isolated so collection of unrelated API tests cannot mask imports.
    script = f"""
import importlib
importlib.import_module({entrypoint!r})
from app.db import Base
from app.persistence.training import StoredTraining
key, = StoredTraining.__table__.c.assignment_id.foreign_keys
assert key.column.table.name == 'training_assignments'
assert 'training_assignments' in {{table.name for table in Base.metadata.sorted_tables}}
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
