"""Health check endpoints (no auth required)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    version: str
    environment: str


@router.get("/health", response_model=HealthResponse, summary="Health check")
def health():
    settings = get_settings()
    return HealthResponse(
        status="ok",
        timestamp=datetime.utcnow(),
        version=settings.app_version,
        environment=settings.environment.value,
    )


@router.get("/health/ready", response_model=HealthResponse, summary="Readiness probe")
def ready():
    """
    Checks that the application is ready to serve traffic.
    In production, extend this to test Discovery Engine connectivity.
    """
    settings = get_settings()
    return HealthResponse(
        status="ready",
        timestamp=datetime.utcnow(),
        version=settings.app_version,
        environment=settings.environment.value,
    )
