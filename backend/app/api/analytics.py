from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.domain.database import get_db
from app.repositories.project_repo import ProjectRepository
from app.schemas.institutional_memory import (
    DurationSummaryDTO,
    HistoricalLedgerPageDTO,
    HistoricalQueryRequest,
    HistoricalQueryResponse,
    HistoricalSummaryDTO,
    PlanningBenchmarkDTO,
    ProductivityMetricDTO,
)
from app.services.historical_analytics_service import HistoricalAnalyticsService
from app.services.institutional_memory_service import InstitutionalMemoryService

logger = logging.getLogger("analytics_api")

router = APIRouter(tags=["Institutional Memory"])


def _verify_project(db: Session, project_id: str):
    proj = ProjectRepository.get_by_id(db, project_id)
    if not proj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found.",
        )
    return proj


@router.get(
    "/api/v1/projects/{project_id}/institutional-memory/summary",
    response_model=HistoricalSummaryDTO,
    summary="Get high-level Institutional Memory execution summary and data quality audit",
)
def get_memory_summary(
    project_id: str,
    db: Session = Depends(get_db),
):
    _verify_project(db, project_id)
    return HistoricalAnalyticsService.get_project_summary(db=db, project_id=project_id)


@router.get(
    "/api/v1/projects/{project_id}/institutional-memory/ledger",
    response_model=HistoricalLedgerPageDTO,
    summary="Get paginated, filtered verified historical execution ledger",
)
def get_memory_ledger(
    project_id: str,
    activity_code: Optional[str] = Query(None, description="Filter by activity code substring"),
    discipline: Optional[str] = Query(None, description="Filter by discipline"),
    contractor: Optional[str] = Query(None, description="Filter by contractor name"),
    location: Optional[str] = Query(None, description="Filter by location"),
    unit: Optional[str] = Query(None, description="Filter by unit of measure"),
    from_date: Optional[datetime] = Query(None, description="Reporting date >= from_date"),
    to_date: Optional[datetime] = Query(None, description="Reporting date <= to_date"),
    source_type: Optional[str] = Query(None, description="Filter by source type (ARTIFACT, CONVERSATION)"),
    event_status: Optional[str] = Query(None, alias="status", description="Event status (APPLIED, APPROVED)"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=1000, description="Items per page"),
    db: Session = Depends(get_db),
):
    _verify_project(db, project_id)
    return HistoricalAnalyticsService.get_ledger_entries(
        db=db,
        project_id=project_id,
        activity_code=activity_code,
        discipline=discipline,
        contractor=contractor,
        location=location,
        unit=unit,
        from_date=from_date,
        to_date=to_date,
        source_type=source_type,
        status=event_status,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/api/v1/projects/{project_id}/institutional-memory/productivity",
    response_model=List[ProductivityMetricDTO],
    summary="Get deterministic observed production rates strictly grouped by compatible units",
)
def get_memory_productivity(
    project_id: str,
    discipline: Optional[str] = Query(None, description="Filter by discipline"),
    contractor: Optional[str] = Query(None, description="Filter by contractor"),
    activity_code: Optional[str] = Query(None, description="Filter by activity code"),
    unit: Optional[str] = Query(None, description="Filter by unit"),
    from_date: Optional[datetime] = Query(None, description="Start date"),
    to_date: Optional[datetime] = Query(None, description="End date"),
    db: Session = Depends(get_db),
):
    _verify_project(db, project_id)
    return HistoricalAnalyticsService.calculate_productivity(
        db=db,
        project_id=project_id,
        discipline=discipline,
        contractor=contractor,
        activity_code=activity_code,
        unit=unit,
        from_date=from_date,
        to_date=to_date,
    )


@router.get(
    "/api/v1/projects/{project_id}/institutional-memory/durations",
    response_model=DurationSummaryDTO,
    summary="Get planned vs actual duration variances for completed activities",
)
def get_memory_durations(
    project_id: str,
    discipline: Optional[str] = Query(None, description="Filter by discipline"),
    activity_code: Optional[str] = Query(None, description="Filter by activity code"),
    db: Session = Depends(get_db),
):
    _verify_project(db, project_id)
    return HistoricalAnalyticsService.calculate_durations(
        db=db,
        project_id=project_id,
        discipline=discipline,
        activity_code=activity_code,
    )


@router.get(
    "/api/v1/projects/{project_id}/institutional-memory/benchmarks",
    response_model=PlanningBenchmarkDTO,
    summary="Get advisory planning benchmark with sample size validation",
)
def get_memory_benchmark(
    project_id: str,
    activity_code: Optional[str] = Query(None, description="Activity code to benchmark"),
    discipline: Optional[str] = Query(None, description="Discipline to benchmark"),
    unit: Optional[str] = Query(None, description="Unit to benchmark"),
    db: Session = Depends(get_db),
):
    _verify_project(db, project_id)
    return HistoricalAnalyticsService.get_planning_benchmark(
        db=db,
        project_id=project_id,
        activity_code=activity_code,
        discipline=discipline,
        unit=unit,
    )


@router.post(
    "/api/v1/projects/{project_id}/institutional-memory/query",
    response_model=HistoricalQueryResponse,
    summary="Execute structured or natural language historical knowledge query against PostgreSQL",
)
def execute_memory_query(
    project_id: str,
    payload: HistoricalQueryRequest,
    db: Session = Depends(get_db),
):
    _verify_project(db, project_id)
    return InstitutionalMemoryService.execute_query(
        db=db,
        project_id=project_id,
        request=payload,
    )


@router.get(
    "/api/v1/projects/{project_id}/institutional-memory/ledger/export",
    summary="Export verified historical execution ledger to RFC 4180 CSV",
)
def export_memory_ledger(
    project_id: str,
    activity_code: Optional[str] = Query(None),
    discipline: Optional[str] = Query(None),
    contractor: Optional[str] = Query(None),
    from_date: Optional[datetime] = Query(None),
    to_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
):
    proj = _verify_project(db, project_id)
    csv_content = InstitutionalMemoryService.export_ledger_csv(
        db=db,
        project_id=project_id,
        activity_code=activity_code,
        discipline=discipline,
        contractor=contractor,
        from_date=from_date,
        to_date=to_date,
    )

    now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"institutional_memory_{proj.project_code}_{now_str}.csv"

    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-cache",
        },
    )
