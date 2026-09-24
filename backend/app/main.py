from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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
from app.domain.database import init_db
from app.services.validation_service import ValidationException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing database tables...")
    try:
        init_db()
        logger.info("Database tables initialized successfully.")
    except Exception as e:
        logger.warning(f"Note on DB initialization (DB might be starting up): {e}")
    yield


app = FastAPI(
    title="Primavera Schedule Management API",
    description="Backend service for storing, querying, validating, and editing Primavera schedules in PostgreSQL.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    return {"status": "ok", "service": "backend"}


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
