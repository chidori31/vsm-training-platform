import os
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Engine, create_engine

from app.application.errors import UseCaseError
from app.application.identity import IdentityService
from app.domain.profiles import EmployeeProfile

bearer = HTTPBearer(auto_error=False, scheme_name="BearerAuth")


def get_engine() -> Iterator[Engine]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise HTTPException(503, "Database unavailable")
    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    try:
        yield engine
    finally:
        engine.dispose()


Database = Annotated[Engine, Depends(get_engine)]


def require_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    if credentials is None or len(credentials.credentials) > 256:
        raise UseCaseError("unauthorized", "Bearer token required")
    return credentials.credentials


def current_user(
    request: Request,
    token: Annotated[str, Depends(require_token)],
    engine: Database,
) -> EmployeeProfile:
    from app.api.anti_cheat import protect_authenticated_command

    user = IdentityService(engine).authenticate(token)
    protect_authenticated_command(request, engine, user.id)
    return user


User = Annotated[EmployeeProfile, Depends(current_user)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=2147483647)]


def require_demo_mode() -> None:
    if os.environ.get("DEMO_AUTH_ENABLED", "").lower() != "true":
        raise UseCaseError("demo_auth_disabled", "Demo authentication is disabled")
