import os
from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from app.api.catalog import router as catalog_router
from app.api.errors import ERROR_RESPONSES, install_error_handlers
from app.api.gamification import router as gamification_router
from app.api.integrations import router as integrations_router
from app.api.learning import router as learning_router
from app.api.retention import router as retention_router
from app.api.security import ApiSecurityMiddleware
from app.api.sessions import router as sessions_router
from app.api.shifts import router as shifts_router
from app.db import database_available

app = FastAPI(title="VSM Platform API", version="1.0.0", responses=ERROR_RESPONSES)
install_error_handlers(app)
app.add_middleware(ApiSecurityMiddleware)
v1 = APIRouter(prefix="/api/v1")
v1.include_router(sessions_router)
v1.include_router(catalog_router)
v1.include_router(integrations_router)
v1.include_router(gamification_router)
v1.include_router(learning_router)
v1.include_router(retention_router)
v1.include_router(shifts_router)
app.include_router(v1)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    status: Literal["ready"] = "ready"


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@app.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"description": "Database unavailable"}},
)
def ready() -> ReadinessResponse:
    if not database_available(os.environ.get("DATABASE_URL")):
        raise HTTPException(status_code=503, detail="Database unavailable")
    return ReadinessResponse()
