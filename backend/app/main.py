"""
FastAPI application entry point.

Startup sequence:
  1. Load settings from environment / .env file.
  2. Load user ↔ department mapping.
  3. Load datastore registry (bucket → datastore-id mapping).
  4. Mount API router.
  5. Register CORS and request-logging middleware.
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
from app.services.datastore_routing_service import load_registry_from_file
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
    logger.info(
        "Starting %s v%s [%s]",
        settings.app_name,
        settings.app_version,
        settings.environment.value,
    )

    # ── User mapping ────────────────────────────────────────────────────────────
    if settings.user_mapping_file:
        load_mapping_from_file(settings.user_mapping_file)
    else:
        logger.warning(
            "USER_MAPPING_FILE not set — seed user mappings programmatically "
            "or set the env var before deploying to production."
        )

    # ── Datastore registry ──────────────────────────────────────────────────────
    if settings.datastore_registry_file:
        try:
            registry = load_registry_from_file(settings.datastore_registry_file)
            logger.info(
                "Datastore registry loaded: public=%s, %d internal, %d relate, confidential=%s",
                registry.public_datastore_id,
                len(registry.internal_datastores),
                len(registry.relate_datastores),
                registry.confidential_datastore_id or "none",
            )
        except FileNotFoundError as exc:
            logger.error("Datastore registry file not found: %s", exc)
            raise
    else:
        logger.warning(
            "DATASTORE_REGISTRY_FILE not set — the chat endpoint will return "
            "503 until the registry is configured."
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
            "**Two-layer access control:**\n"
            "1. **API layer** — routes queries only to the datastore buckets "
            "the caller is authorised to query (Public / Internal-dept / "
            "Relate-group / Confidential-JD-code).\n"
            "2. **ACL layer** — within each bucket, Discovery Engine enforces "
            "`acl_info` on every document using the caller's impersonated GCP "
            "identity (Domain-wide Delegation).\n\n"
            "Bucket layout:\n"
            "- **Public** — 1 shared datastore, open to all authenticated users\n"
            "- **Internal** — N datastores, one per department\n"
            "- **Relate** — K datastores, one per cross-dept relationship\n"
            "- **Confidential** — 1 datastore, JD-code-gated"
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
        logger.error(
            "Unhandled exception on %s %s: %s",
            request.method,
            request.url,
            exc,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "An internal server error occurred."},
        )

    # ── Routers ────────────────────────────────────────────────────────────────
    app.include_router(v1_router)

    return app


app = create_app()
