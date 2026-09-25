from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict


class NormalizedExtractionEvent(BaseModel):
    """
    Schema for events extracted from LLM or rule parsers after normalization,
    prior to persistence as an authoritative ExecutionEvent in PostgreSQL.
    """
    model_config = ConfigDict(from_attributes=True)

    verbatim_excerpt: str
    activity_reference: Optional[str] = None
    reported_activity_code: Optional[str] = None
    description: str
    execution_date: str
    quantity: Optional[float] = None
    unit: Optional[str] = None
    location: Optional[str] = None
    discipline: Optional[str] = None
    contractor: Optional[str] = None
    asset: Optional[str] = None
    wbs_hint: Optional[str] = None
    status_reported: str = "IN_PROGRESS"
    extraction_confidence: float = 1.0
    extraction_notes: Optional[str] = None
    page_number: int = 1
    bounding_box: Optional[List[float]] = None


class ExecutionEventDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: str
    artifact_id: Optional[str] = None
    source_report_id: Optional[str] = None
    source_document_name: Optional[str] = None
    storage_key: Optional[str] = None
    file_sha256: Optional[str] = None
    page_number: Optional[int] = 1
    bounding_box: Optional[List[float]] = None
    verbatim_excerpt: str
    activity_reference: Optional[str] = None
    reported_activity_code: Optional[str] = None
    description: str
    execution_date: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    status_reported: str = "IN_PROGRESS"
    quantity: Optional[float] = None
    unit: Optional[str] = None
    location: Optional[str] = None
    discipline: Optional[str] = None
    contractor: Optional[str] = None
    asset: Optional[str] = None
    wbs_hint: Optional[str] = None
    extraction_confidence: float = 1.0
    extraction_notes: Optional[str] = None
    status: str = "UNMATCHED"
    matched_activity_id: Optional[str] = None
    match_score: Optional[float] = None


class ExtractionResponse(BaseModel):
    artifact_id: str
    status: str
    events_extracted: int
    events: List[ExecutionEventDTO]
