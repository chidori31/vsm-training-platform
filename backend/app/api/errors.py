from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from app.application.errors import UseCaseError
from app.application.sessions import ScenarioNotFound, SessionNotFound
from app.domain.common import DomainError


class ErrorDetail(BaseModel):
    location: list[str | int]
    message: str
    type: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorBody
    data: dict[str, Any] | None = None


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (401, 404, 405, 409, 422, 500, 501, 503)
}


def error_response(
    status: int,
    code: str,
    message: str,
    *,
    data: dict[str, Any] | None = None,
    details: list[ErrorDetail] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(code=code, message=message, details=details or []), data=data
    )
    return JSONResponse(
        status_code=status, content=body.model_dump(mode="json"), headers=headers
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            ErrorDetail(
                location=list(item["loc"]), message=item["msg"], type=item["type"]
            )
            for item in exc.errors()
        ]
        return error_response(
            422, "validation_error", "Request validation failed", details=details
        )

    @app.exception_handler(UseCaseError)
    async def use_case_error(request: Request, exc: UseCaseError) -> JSONResponse:
        status = {
            "unauthorized": 401,
            "demo_auth_disabled": 404,
            "idempotency_conflict": 409,
            "result_not_ready": 409,
            "integration_not_configured": 501,
        }.get(exc.code, 409)
        return error_response(
            status,
            exc.code,
            str(exc),
            headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
        )

    @app.exception_handler(SessionNotFound)
    async def session_not_found(request: Request, exc: SessionNotFound) -> JSONResponse:
        return error_response(404, "session_not_found", "Session not found")

    @app.exception_handler(ScenarioNotFound)
    async def scenario_not_found(
        request: Request, exc: ScenarioNotFound
    ) -> JSONResponse:
        return error_response(404, "scenario_not_found", "Scenario version not found")

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        return error_response(503, "database_unavailable", "Database unavailable")

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return error_response(409, "domain_conflict", str(exc))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code = {
            404: "not_found",
            405: "method_not_allowed",
            503: "database_unavailable",
        }.get(exc.status_code, "http_error")
        return error_response(
            exc.status_code, code, str(exc.detail), headers=exc.headers
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        return error_response(500, "internal_error", "Internal server error")
