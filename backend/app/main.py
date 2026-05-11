"""
FastAPI application entry point.

Startup sequence:
  1. Load settings from environment / .env file.
  2. Load user↔department mapping (from file or in-process seed).
  3. Mount API router.
  4. Register CORS and request-logging middleware.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import router as v1_router
from app.config import get_settings
from app.utils.user_mapper import load_mapping_from_file

logger = logging.getLogger(__name__)


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Starting %s v%s [%s]", settings.app_name, settings.app_version, settings.environment.value)

    if settings.user_mapping_file:
        load_mapping_from_file(settings.user_mapping_file)
    else:
        logger.warning(
            "USER_MAPPING_FILE not set. "
            "Seed mappings programmatically or set the env var before deploying."
        )

    yield

    logger.info("Shutting down %s", settings.app_name)


# ── App factory ────────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Enterprise Knowledge Management Chatbot API.\n\n"
            "Uses Gemini Enterprise (Vertex AI Search) for grounded, ACL-aware "
            "responses from managed Discovery Engine datastores. "
            "User identities are resolved via Domain-wide Delegation so that "
            "each request sees only documents the caller is authorised to access."
        ),
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        openapi_url="/openapi.json" if settings.debug else None,
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # ── Request logging ────────────────────────────────────────────────────────
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "%s %s %d %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response

    # ── Global exception handler ───────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.error("Unhandled exception on %s %s: %s", request.method, request.url, exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"detail": "An internal server error occurred."},
        )

    # ── Routers ────────────────────────────────────────────────────────────────
    app.include_router(v1_router)

    return app


app = create_app()
