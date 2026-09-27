from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.activities import router as activities_router
from app.api.agent import router as agent_router
from app.api.analytics import router as analytics_router
from app.api.artifacts import router as artifacts_router
from app.api.export import router as export_router
from app.api.matching import router as matching_router
from app.api.projects import router as projects_router
from app.api.relationships import router as relationships_router
from app.api.review import router as review_router
from app.api.wbs import router as wbs_router
from app.domain.database import engine, init_db
from app.services.minio_service import minio_service
from app.services.validation_service import ValidationException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing database connection and schema...")
    db_initialized = False
    # Resilient retry loop for PostgreSQL startup (up to 10 attempts with 1s backoff)
    for attempt in range(1, 11):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            init_db()
            logger.info("Database tables and schema initialized successfully.")
            db_initialized = True
            break
        except Exception as e:
            logger.warning(f"Database readiness attempt {attempt}/10 failed: {e}. Retrying in 1s...")
            time.sleep(1)

    if not db_initialized:
        logger.error("Could not verify database readiness after 10 attempts. Continuing in degraded mode.")

    # Idempotently ensure MinIO artifact bucket exists if connected
    try:
        minio_service.ensure_bucket_exists()
    except Exception as e:
        logger.warning(f"MinIO bucket check failed during startup: {e}")

    yield


app = FastAPI(
    title="Primavera Schedule Management API",
    description="Backend service for storing, querying, validating, and editing Primavera schedules in PostgreSQL.",
    version="1.0.0",
    lifespan=lifespan,
)

# Production CORS hardening: use explicit allowlist from CORS_ORIGINS
cors_origins_env = os.getenv("CORS_ORIGINS", "").strip()
if cors_origins_env:
    allowed_origins = [orig.strip() for orig in cors_origins_env.split(",") if orig.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    # Development fallback strictly restricted to local origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8080",
            "http://127.0.0.1:8080",
            "http://localhost",
            "http://127.0.0.1",
        ],
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.exception_handler(ValidationException)
async def validation_exception_handler(request: Request, exc: ValidationException):
    return JSONResponse(
        status_code=getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", status.HTTP_422_UNPROCESSABLE_ENTITY),
        content=exc.to_dict(),
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled server error: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": str(exc) or "Internal server error"},
    )


@app.get("/health", tags=["Health"])
def health():
    db_status = "healthy"
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        logger.warning(f"Database health check query failed: {e}")
        db_status = "unhealthy"

    minio_status = "healthy" if minio_service.is_minio_connected() else "degraded"
    overall_status = "ok" if db_status == "healthy" else "degraded"
    status_code = status.HTTP_200_OK if db_status == "healthy" else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=status_code,
        content={
            "status": overall_status,
            "service": "backend",
            "dependencies": {
                "database": db_status,
                "storage": minio_status,
            },
        },
    )


# Mount API routers
app.include_router(projects_router)
app.include_router(wbs_router)
app.include_router(activities_router)
app.include_router(relationships_router)
app.include_router(artifacts_router)
app.include_router(matching_router)
app.include_router(review_router)
app.include_router(export_router)
app.include_router(agent_router)
app.include_router(analytics_router)
