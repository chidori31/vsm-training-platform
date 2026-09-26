"""Durable deadline polling, independent of browser activity and HTTP requests."""

import logging
import math
import os
import signal
from collections.abc import Callable
from threading import Event

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from app.application.sessions import SessionService

logger = logging.getLogger(__name__)


def run_worker(
    sweep: Callable[[], int], *, stop: Event, poll_seconds: float = 1.0
) -> None:
    if not math.isfinite(poll_seconds) or poll_seconds <= 0:
        raise ValueError("TIMER_POLL_SECONDS must be a finite positive number")
    while not stop.is_set():
        try:
            count = sweep()
            if count:
                logger.info("Applied %d timed outcomes", count)
        except SQLAlchemyError:
            logger.error(
                "Timeout sweep failed; check database connectivity and migrations"
            )
        stop.wait(poll_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stop = Event()
    for kind in (signal.SIGINT, signal.SIGTERM):
        signal.signal(kind, lambda _signal, _frame: stop.set())
    engine = create_engine(
        os.environ["DATABASE_URL"],
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )
    try:
        service = SessionService(engine)
        run_worker(
            service.expire_due,
            stop=stop,
            poll_seconds=float(os.environ.get("TIMER_POLL_SECONDS", "1")),
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
