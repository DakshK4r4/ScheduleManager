from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class ConversationCreateRequest(BaseModel):
    active_activity_id: Optional[str] = None
    force_new: Optional[bool] = False
    title: Optional[str] = None


class MessageDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    sender: str
    content: str
    message_metadata: Optional[Dict[str, Any]] = None
    created_at: str


class ConversationSummaryDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    title: str
    status: str
    language: Optional[str] = None
    conversation_language: Optional[str] = None
    conversation_style: Optional[str] = None
    language_locked: bool = False
    is_pinned: bool = False
    created_at: str
    updated_at: str
    message_count: int = 0
    active_activity_id: Optional[str] = None
    active_event_id: Optional[str] = None


class PendingActionDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: str  # "BULK_UPDATE_CONFIRMATION", "PROPOSAL_CONFIRMATION"
    proposal_id: Optional[str] = None
    status: str = "PENDING"  # "PENDING", "RESOLVING", "RESOLVED"
    activity_count: Optional[int] = None
    target_percent: Optional[float] = None
    bulk_activities: Optional[List[Dict[str, Any]]] = None


class ConversationDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    conversation_id: str
    project_id: str
    title: Optional[str] = "New Chat"
    status: str
    language: Optional[str] = None
    conversation_language: Optional[str] = None
    conversation_style: Optional[str] = None
    language_locked: bool = False
    is_pinned: bool = False
    active_activity: Optional[Dict[str, Any]] = None
    active_event_id: Optional[str] = None
    clarification_turns: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    pending_action: Optional[PendingActionDTO] = None
    history: List[MessageDTO] = []


class ConversationPinRequest(BaseModel):
    is_pinned: bool = True


class MessageSendRequest(BaseModel):
    content: str


class ActionCardDTO(BaseModel):
    type: str  # "PROPOSAL_CONFIRMATION", "CLARIFICATION_CHOICE", "INFORMATIONAL", "BULK_SCOPE_PROPOSAL"
    proposal_id: Optional[str] = None
    proposal_status: Optional[str] = None  # "PENDING", "CONSUMED", "APPLIED", "REJECTED", "EXPIRED"
    event_id: Optional[str] = None
    activity_id: Optional[str] = None
    activity_code: Optional[str] = None
    activity_name: Optional[str] = None
    current_percent: Optional[float] = None
    proposed_percent: Optional[float] = None
    incremental_quantity: Optional[float] = None
    unit: Optional[str] = None
    execution_date: Optional[str] = None
    question: Optional[str] = None
    options: Optional[List[Dict[str, Any]]] = None
    bulk_proposal_id: Optional[str] = None
    bulk_activities: Optional[List[Dict[str, Any]]] = None
    bulk_count: Optional[int] = None
    confirm_label: Optional[str] = None
    reject_label: Optional[str] = None
    review_label: Optional[str] = None
    scope_label: Optional[str] = None
    proposed_status: Optional[str] = None
    target_percent: Optional[float] = None


class MessageResponseDTO(BaseModel):
    message_id: str
    sender: str
    reply_text: str
    action_card: Optional[ActionCardDTO] = None
    created_at: Optional[str] = None
    transcript: Optional[str] = None
    detected_language: Optional[str] = None
    conversation_language: Optional[str] = None
    conversation_style: Optional[str] = None
    language_locked: bool = False


class VoiceMessageResponseDTO(MessageResponseDTO):
    transcript: str
    transcription: Optional[str] = None
    detected_language: Optional[str] = None
    detected_languages: Optional[List[str]] = []
    is_code_mixed: bool = False
    confidence: Optional[float] = 1.0


class AttachmentResponseDTO(BaseModel):
    artifact_id: str
    filename: str
    extracted_events_count: int
    agent_message: str
    action_card: Optional[ActionCardDTO] = None


class ProposalConfirmRequest(BaseModel):
    proposal_id: str
    action: str = Field(default="CONFIRM")  # "CONFIRM", "REJECT"


class ProposalConfirmResponse(BaseModel):
    status: str  # "APPLIED", "REJECTED"
    activity_id: str
    activity_code: str
    previous_percent: float
    new_percent: float
    audit_log_id: str
    message: str


class BulkProposalConfirmRequest(BaseModel):
    bulk_proposal_id: Optional[str] = None
    activity_ids: Optional[List[str]] = None
    action: str = Field(default="CONFIRM")  # "CONFIRM", "CANCEL"
    target_percent: Optional[float] = 100.0
    status_reported: Optional[str] = "COMPLETED"


class BulkProposalConfirmResponse(BaseModel):
    status: str  # "APPLIED", "CANCELLED"
    updated_count: int
    updated_activities: List[Dict[str, Any]] = []
    message: str


class ParsedConversationalIntent(BaseModel):
    intent: str  # "INFORMATION_QUERY", "PROGRESS_REPORT", "PROGRESS_UPDATE_REQUEST", "CLARIFICATION_RESPONSE", "ARTIFACT_SUBMISSION", "BULK_PROGRESS_REPORT"
    confidence: float = 0.95
    is_bulk: bool = False
    bulk_scope: Optional[Dict[str, Any]] = None
    entities_present: List[str] = []
    quantity: Optional[float] = None
    unit: Optional[str] = None
    quantity_semantics: Optional[str] = "UNKNOWN"  # "INCREMENTAL", "CUMULATIVE", "UNKNOWN"
    location: Optional[str] = None
    discipline: Optional[str] = None
    contractor: Optional[str] = None
    asset: Optional[str] = None
    wbs_hint: Optional[str] = None
    reported_activity_code: Optional[str] = None
    execution_date: Optional[str] = None
    status_reported: Optional[str] = "IN_PROGRESS"
    override_percent: Optional[float] = None
    description: Optional[str] = None
    detected_language: Optional[str] = "en"  # "en", "hi", "hinglish"


class TTSRequest(BaseModel):
    text: str = Field(..., description="Text content to convert to speech", min_length=1)
    language: Optional[str] = Field("en-IN", description="Language code e.g. en-IN, hi-IN, ta-IN")
    speaker: Optional[str] = Field(None, description="Preferred Sarvam voice speaker (defaults to shubh for v3)")
    model: Optional[str] = Field("bulbul:v3", description="Sarvam TTS model, defaults to bulbul:v3")


class TTSResponse(BaseModel):
    audio_base64: str = Field(..., description="Base64 encoded audio bytes")
    content_type: str = Field("audio/wav", description="Audio MIME format")
    language: str = Field(..., description="Resolved language code used for synthesis")
    speaker: str = Field(..., description="Voice speaker used")

