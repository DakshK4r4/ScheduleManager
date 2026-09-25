from __future__ import annotations

import json
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.domain.database import get_db
from app.domain.models import Activity, Artifact, ExecutionEvent, Project, ReviewDecision, ScheduleAuditLog
from app.schemas.extraction import ExecutionEventDTO
from app.schemas.matching import MatchCandidateDTO
from app.schemas.review import (
    ReviewDecisionRequest,
    ReviewDecisionResponse,
    ReviewQueueItemDTO,
    ReviewQueueResponse,
)
from app.services.matching_service import MatchingService
from app.services.minio_service import minio_service
from app.services.schedule_update_service import ScheduleUpdateService

logger = logging.getLogger("review_api")
router = APIRouter(tags=["Planner Review"])


@router.get(
    "/api/v1/review/queue",
    response_model=ReviewQueueResponse,
)
def get_review_queue(
    project_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """
    Retrieve all pending ambiguous events for a project that require planner human review,
    complete with candidate matches and MinIO presigned evidence URLs.
    """
    events = (
        db.query(ExecutionEvent)
        .filter(
            ExecutionEvent.project_id == project_id,
            ExecutionEvent.status.in_(["IN_REVIEW", "UNMATCHED"]),
        )
        .order_by(ExecutionEvent.created_at.desc())
        .all()
    )

    items: List[ReviewQueueItemDTO] = []
    for ev in events:
        # Generate presigned URL for the source artifact
        view_url = None
        try:
            view_url = minio_service.get_presigned_view_url(ev.storage_key, expires_seconds=900)
        except Exception as e:
            logger.warning(f"Could not generate presigned URL for event {ev.id}: {e}")

        # Extract or recalculate candidates
        candidates: List[MatchCandidateDTO] = []
        if ev.match_metadata:
            try:
                meta = json.loads(ev.match_metadata)
                candidates = [MatchCandidateDTO(**c) for c in meta.get("all_scored", [])]
            except Exception:
                candidates = []

        if not candidates:
            # Generate candidate list if none stored
            raw_cands = MatchingService.retrieve_candidates(db, ev)
            for c in raw_cands[:5]:
                score, breakdown = MatchingService.score_activity(ev, c, c.wbs_node)
                candidates.append(
                    MatchCandidateDTO(
                        activity_id=c.id,
                        activity_code=c.activity_code,
                        activity_name=c.name,
                        wbs_code=c.wbs_node.code if c.wbs_node else None,
                        match_score=score,
                        margin_delta=0.0,
                        score_breakdown=breakdown,
                    )
                )

        ev_dto = ExecutionEventDTO(
            event_id=ev.id,
            artifact_id=ev.artifact_id,
            source_report_id=ev.source_report_id,
            source_document_name=ev.source_document_name,
            storage_key=ev.storage_key,
            file_sha256=ev.file_sha256,
            page_number=ev.page_number,
            bounding_box=None,
            verbatim_excerpt=ev.verbatim_excerpt,
            activity_reference=ev.activity_reference,
            reported_activity_code=ev.reported_activity_code,
            description=ev.description,
            execution_date=ev.execution_date.strftime("%Y-%m-%d"),
            start_time=ev.start_time,
            end_time=ev.end_time,
            status_reported=ev.status_reported,
            quantity=ev.quantity,
            unit=ev.unit,
            location=ev.location,
            discipline=ev.discipline,
            contractor=ev.contractor,
            asset=ev.asset,
            wbs_hint=ev.wbs_hint,
            extraction_confidence=ev.extraction_confidence,
            extraction_notes=ev.extraction_notes,
            status=ev.status,
            matched_activity_id=ev.matched_activity_id,
            match_score=ev.match_score,
        )

        items.append(
            ReviewQueueItemDTO(
                event=ev_dto,
                candidates=candidates,
                artifact_view_url=view_url,
            )
        )

    return ReviewQueueResponse(
        project_id=project_id,
        pending_count=len(items),
        items=items,
    )


@router.post(
    "/api/v1/review/decisions",
    response_model=ReviewDecisionResponse,
)
def submit_review_decision(
    request: ReviewDecisionRequest,
    db: Session = Depends(get_db),
):
    """
    Submit planner review decision (APPROVED, REASSIGNED, or REJECTED)
    for an ambiguous execution event.
    """
    event = db.query(ExecutionEvent).filter(ExecutionEvent.id == request.event_id).first()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ExecutionEvent {request.event_id} not found.",
        )

    target_activity_id = request.activity_id or event.matched_activity_id
    if target_activity_id:
        target_activity = db.query(Activity).filter(Activity.id == target_activity_id).first()
        if not target_activity:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Target activity {target_activity_id} not found.",
            )
        if target_activity.project_id != event.project_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Target activity {target_activity_id} belongs to project {target_activity.project_id}, not event project {event.project_id}.",
            )

    # Record ReviewDecision
    decision_record = ReviewDecision(
        execution_event_id=event.id,
        project_id=event.project_id,
        activity_id=target_activity_id,
        reviewer_id=request.reviewer_id,
        decision=request.decision.upper(),
        adjustment_percent=request.adjustment_percent,
        notes=request.notes,
    )
    db.add(decision_record)

    applied_percent = None

    if request.decision.upper() in ["APPROVED", "REASSIGNED"]:
        if not target_activity_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot approve event without an assigned target activity.",
            )
        # Apply progress to schedule
        activity = ScheduleUpdateService.apply_event_progress(
            db=db,
            event_id=event.id,
            activity_id=target_activity_id,
            user_id=request.reviewer_id,
            override_percent=request.adjustment_percent,
            action_name=f"PLANNER_{request.decision.upper()}_PROGRESS",
        )
        event.status = "APPROVED"
        applied_percent = activity.percent_complete
    elif request.decision.upper() == "REJECTED":
        event.status = "REJECTED"
        # Record audit entry for rejection
        db.add(
            ScheduleAuditLog(
                project_id=event.project_id,
                activity_id=target_activity_id or "none",
                execution_event_id=event.id,
                artifact_id=event.artifact_id,
                action="PLANNER_REJECTED_MATCH",
                previous_state=None,
                new_state=json.dumps({"notes": request.notes}),
                user_id=request.reviewer_id,
            )
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid decision '{request.decision}'. Must be APPROVED, REASSIGNED, or REJECTED.",
        )

    db.commit()
    db.refresh(decision_record)

    return ReviewDecisionResponse(
        decision_id=decision_record.id,
        event_id=event.id,
        status=event.status,
        activity_id=target_activity_id,
        applied_progress_percent=applied_percent,
        message=f"Planner decision '{request.decision}' processed successfully.",
    )
