import os
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.db import database_available

app = FastAPI(title="VSM Platform API", version="0.1.0")


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
