from threading import Event

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
