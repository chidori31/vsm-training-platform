import logging

from fastapi import HTTPException, Request
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.application.anti_cheat import (
    RateLimiter,
    RateLimitExceeded,
    anomaly_flags,
    database_time,
    record_audit,
)

logger = logging.getLogger(__name__)


def sensitive_action(method: str, path: str) -> str | None:
    if method != "POST":
        return None
    if path == "/api/v1/sessions":
        return "start"
    if path == "/api/v1/shifts":
        return "shift_start"
    parts = path.strip("/").split("/")
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "sessions"]
        and parts[4] == "decisions"
    ):
        return "decision"
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "shifts"]
        and parts[4] == "advance"
    ):
        return "shift_advance"
    return None


def enforce_rate(engine: Engine, action: str, subject: str) -> None:
    try:
        RateLimiter(engine).check(action, subject)
    except RateLimitExceeded as error:
        raise HTTPException(
            429, str(error), headers={"Retry-After": str(error.retry_after)}
        ) from None


def protect_authenticated_command(
    request: Request, engine: Engine, actor_id: str
) -> None:
    action = sensitive_action(request.method, request.url.path)
    if action:
        # Only authenticated server identity is retained. No body, token or network ID.
        request.state.command_audit_context = (engine, actor_id, action)
        enforce_rate(engine, action, actor_id)


def audit_request_rejection(request: Request, outcome: str, code: str) -> None:
    context = getattr(request.state, "command_audit_context", None)
    if context is None:
        return
    engine, actor_id, action = context
    # Written only by a route after strict DTO/header validation. Invalid DTOs
    # never reach this assignment, and an unknown session has no trusted snapshot.
    metadata = dict(getattr(request.state, "command_audit_metadata", {}))
    command_id = metadata.pop("client_event_id", None)
    try:
        with Session(engine) as database, database.begin():
            record_audit(
                database,
                actor_id=actor_id,
                action=action,
                outcome=outcome,
                server_time=database_time(database),
                client_event_id=command_id,
                flags=anomaly_flags(outcome=outcome),
                details={"error_code": code, **metadata},
            )
    except SQLAlchemyError:
        # Accepted state changes fail closed inside their transaction. For a rejected
        # request during DB failure retain the original safe HTTP error and log loss.
        logger.error("Unable to persist rejected-command audit")


def audit_login(engine: Engine, actor_id: str) -> None:
    with Session(engine) as database, database.begin():
        record_audit(
            database,
            actor_id=actor_id,
            action="auth",
            outcome="accepted",
            server_time=database_time(database),
        )
