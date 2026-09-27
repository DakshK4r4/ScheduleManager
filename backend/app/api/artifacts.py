from __future__ import annotations

import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.validation import validate_project_id
from app.domain.database import get_db
from app.domain.models import Artifact, ExecutionEvent, Project
from app.schemas.artifact import ArtifactDTO, ArtifactUploadResponse, PresignedUrlResponse
from app.schemas.extraction import ExecutionEventDTO, ExtractionResponse
from app.services.extraction_service import ExtractionService
from app.services.minio_service import minio_service

router = APIRouter(tags=["Artifacts"])


@router.post(
    "/api/v1/projects/{project_id}/artifacts/upload",
    response_model=ArtifactUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_artifact(
    project_id: str,
    file: UploadFile = File(...),
    report_id: Optional[str] = Form(None),
    uploaded_by: str = Form("site-user"),
    db: Session = Depends(get_db),
):
    """
    Ingest a field execution artifact (PDF, Excel, CSV, audio voice memo),
    store raw binary in MinIO, and record metadata in PostgreSQL.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} does not exist.",
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    sha256 = minio_service.compute_sha256(file_bytes)
    assigned_report_id = report_id or f"rep-{uuid.uuid4().hex[:8]}"

    # Check duplicate detection / idempotency per doc Section 18
    existing_artifact = (
        db.query(Artifact)
        .filter(Artifact.project_id == project_id, Artifact.sha256 == sha256)
        .first()
    )
    if existing_artifact:
        return ArtifactUploadResponse(
            artifact=ArtifactDTO(
                artifact_id=existing_artifact.id,
                project_id=existing_artifact.project_id,
                report_id=existing_artifact.report_id,
                artifact_type=existing_artifact.artifact_type,
                original_filename=existing_artifact.original_filename,
                mime_type=existing_artifact.mime_type,
                size_bytes=existing_artifact.size_bytes,
                sha256=existing_artifact.sha256,
                storage_bucket=existing_artifact.storage_bucket,
                storage_key=existing_artifact.storage_key,
                uploaded_by=existing_artifact.uploaded_by,
                uploaded_at=existing_artifact.uploaded_at,
                extraction_status=existing_artifact.extraction_status,
                error_message=existing_artifact.error_message,
            ),
            is_duplicate=True,
            message="Duplicate artifact detected via SHA-256 hash. Reusing existing artifact record.",
        )

    # Determine artifact type
    fname = file.filename or "unknown_file"
    ctype = file.content_type or "application/octet-stream"
    if fname.lower().endswith(".pdf") or "pdf" in ctype.lower():
        atype = "PDF_REPORT"
    elif any(fname.lower().endswith(x) for x in [".xlsx", ".xls", ".csv"]):
        atype = "SPREADSHEET"
    elif any(fname.lower().endswith(x) for x in [".m4a", ".mp3", ".wav", ".ogg"]):
        atype = "VOICE_MEMO"
    elif any(fname.lower().endswith(x) for x in [".jpg", ".jpeg", ".png"]):
        atype = "IMAGE"
    else:
        atype = "OTHER"

    artifact_id = str(uuid.uuid4())
    storage_key = minio_service.generate_object_key(
        project_id, assigned_report_id, artifact_id, fname
    )

    try:
        # Write binary to MinIO
        minio_service.upload_artifact(storage_key, file_bytes, ctype)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to store artifact in MinIO: {e}",
        )

    # Insert metadata record into PostgreSQL
    artifact = Artifact(
        id=artifact_id,
        project_id=project_id,
        report_id=assigned_report_id,
        artifact_type=atype,
        original_filename=fname,
        mime_type=ctype,
        size_bytes=len(file_bytes),
        sha256=sha256,
        storage_bucket=minio_service.bucket,
        storage_key=storage_key,
        uploaded_by=uploaded_by,
        extraction_status="UPLOADED",
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)

    return ArtifactUploadResponse(
        artifact=ArtifactDTO(
            artifact_id=artifact.id,
            project_id=artifact.project_id,
            report_id=artifact.report_id,
            artifact_type=artifact.artifact_type,
            original_filename=artifact.original_filename,
            mime_type=artifact.mime_type,
            size_bytes=artifact.size_bytes,
            sha256=artifact.sha256,
            storage_bucket=artifact.storage_bucket,
            storage_key=artifact.storage_key,
            uploaded_by=artifact.uploaded_by,
            uploaded_at=artifact.uploaded_at,
            extraction_status=artifact.extraction_status,
            error_message=artifact.error_message,
        ),
        is_duplicate=False,
        message="Artifact uploaded and stored permanently in MinIO.",
    )


@router.get(
    "/api/v1/projects/{project_id}/artifacts",
    response_model=List[ArtifactDTO],
)
def list_project_artifacts(project_id: str, db: Session = Depends(get_db)):
    """List all stored artifacts for a project."""
    clean_id = validate_project_id(project_id)
    artifacts = (
        db.query(Artifact)
        .filter(Artifact.project_id == clean_id)
        .order_by(Artifact.uploaded_at.desc())
        .all()
    )
    return [
        ArtifactDTO(
            artifact_id=a.id,
            project_id=a.project_id,
            report_id=a.report_id,
            artifact_type=a.artifact_type,
            original_filename=a.original_filename,
            mime_type=a.mime_type,
            size_bytes=a.size_bytes,
            sha256=a.sha256,
            storage_bucket=a.storage_bucket,
            storage_key=a.storage_key,
            uploaded_by=a.uploaded_by,
            uploaded_at=a.uploaded_at,
            extraction_status=a.extraction_status,
            error_message=a.error_message,
        )
        for a in artifacts
    ]


@router.get(
    "/api/v1/artifacts/{artifact_id}",
    response_model=ArtifactDTO,
)
def get_artifact(artifact_id: str, db: Session = Depends(get_db)):
    """Retrieve artifact metadata record."""
    artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
    if not artifact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found.",
        )
    return ArtifactDTO(
        artifact_id=artifact.id,
        project_id=artifact.project_id,
        report_id=artifact.report_id,
        artifact_type=artifact.artifact_type,
        original_filename=artifact.original_filename,
        mime_type=artifact.mime_type,
        size_bytes=artifact.size_bytes,
        sha256=artifact.sha256,
        storage_bucket=artifact.storage_bucket,
        storage_key=artifact.storage_key,
        uploaded_by=artifact.uploaded_by,
        uploaded_at=artifact.uploaded_at,
        extraction_status=artifact.extraction_status,
        error_message=artifact.error_message,
    )


@router.get(
    "/api/v1/artifacts/{artifact_id}/view-url",
    response_model=PresignedUrlResponse,
)
def get_artifact_view_url(artifact_id: str, db: Session = Depends(get_db)):
    """Generate a temporary presigned URL for viewing/downloading the artifact from MinIO."""
    artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
    if not artifact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found.",
        )
    try:
        url = minio_service.get_presigned_view_url(artifact.storage_key, expires_seconds=900)
        return PresignedUrlResponse(
            artifact_id=artifact.id,
            url=url,
            expires_in_seconds=900,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate presigned URL: {e}",
        )


@router.get("/api/v1/artifacts/download")
def download_artifact(key: str = Query(...)):
    """Direct artifact binary download/view endpoint (used for local fallback or direct stream)."""
    try:
        data = minio_service.get_artifact_bytes(key)
        media_type = "application/pdf" if key.endswith(".pdf") else "application/octet-stream"
        return Response(content=data, media_type=media_type)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact not found: {e}",
        )


@router.post(
    "/api/v1/artifacts/{artifact_id}/extract",
    response_model=ExtractionResponse,
)
def extract_artifact(
    artifact_id: str,
    force_reextract: bool = Query(False, description="Force re-extraction even if already extracted"),
    db: Session = Depends(get_db),
):
    """Trigger the extraction engine on a stored MinIO artifact (idempotent unless force_reextract=True)."""
    events = ExtractionService.extract_artifact(db, artifact_id, force_reextract=force_reextract)
    artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()

    event_dtos = [
        ExecutionEventDTO(
            event_id=e.id,
            artifact_id=e.artifact_id,
            source_report_id=e.source_report_id,
            source_document_name=e.source_document_name,
            storage_key=e.storage_key,
            file_sha256=e.file_sha256,
            page_number=e.page_number,
            bounding_box=None,
            verbatim_excerpt=e.verbatim_excerpt,
            activity_reference=e.activity_reference,
            reported_activity_code=e.reported_activity_code,
            description=e.description,
            execution_date=e.execution_date.strftime("%Y-%m-%d"),
            start_time=e.start_time,
            end_time=e.end_time,
            status_reported=e.status_reported,
            quantity=e.quantity,
            unit=e.unit,
            location=e.location,
            discipline=e.discipline,
            contractor=e.contractor,
            asset=e.asset,
            wbs_hint=e.wbs_hint,
            extraction_confidence=e.extraction_confidence,
            extraction_notes=e.extraction_notes,
            status=e.status,
            matched_activity_id=e.matched_activity_id,
            match_score=e.match_score,
        )
        for e in events
    ]

    return ExtractionResponse(
        artifact_id=artifact_id,
        status=artifact.extraction_status if artifact else "EXTRACTED",
        events_extracted=len(events),
        events=event_dtos,
    )
