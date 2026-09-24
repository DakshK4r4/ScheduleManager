from __future__ import annotations

import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.domain.database import get_db
from app.domain.models import Conversation
from app.schemas.agent import (
    AttachmentResponseDTO,
    BulkProposalConfirmRequest,
    BulkProposalConfirmResponse,
    ConversationCreateRequest,
    ConversationDTO,
    ConversationPinRequest,
    ConversationSummaryDTO,
    MessageResponseDTO,
    MessageSendRequest,
    ProposalConfirmRequest,
    ProposalConfirmResponse,
    TTSRequest,
    TTSResponse,
    VoiceMessageResponseDTO,
)
from app.services.agent_service import TimeAgentService
from app.services.sarvam_service import SarvamService

logger = logging.getLogger("agent_api")

router = APIRouter(tags=["Time Agent"])


@router.get(
    "/api/v1/projects/{project_id}/agent/conversations",
    response_model=List[ConversationSummaryDTO],
    status_code=status.HTTP_200_OK,
)
def list_conversations(
    project_id: str,
    q: Optional[str] = Query(default=None, description="Search query matching title and message content"),
    x_user_id: str = Header(default="site-supervisor", alias="X-User-ID"),
    db: Session = Depends(get_db),
):
    """
    List lightweight conversation summaries strictly scoped to the specified project.
    Supports search query parameter `q` matching title and message content.
    Ordered by most recently updated first.
    """
    return TimeAgentService.list_conversations(
        db=db,
        project_id=project_id,
        user_id=x_user_id,
        search_query=q,
    )


@router.post(
    "/api/v1/projects/{project_id}/agent/conversations",
    response_model=ConversationDTO,
    status_code=status.HTTP_200_OK,
)
def start_or_get_conversation(
    project_id: str,
    payload: Optional[ConversationCreateRequest] = None,
    x_user_id: str = Header(default="site-supervisor", alias="X-User-ID"),
    db: Session = Depends(get_db),
):
    """
    Start a new conversational execution reporting session or retrieve an active one.
    Accepts optional active_activity_id from UI anchoring, and force_new boolean for + New Chat.
    """
    active_act_id = payload.active_activity_id if payload else None
    force_new = payload.force_new if payload else False
    title = payload.title if payload else None
    return TimeAgentService.get_or_create_conversation(
        db=db,
        project_id=project_id,
        user_id=x_user_id,
        active_activity_id=active_act_id,
        force_new=force_new,
        title=title,
    )


@router.get(
    "/api/v1/projects/{project_id}/agent/conversations/{conversation_id}",
    response_model=ConversationDTO,
)
def get_conversation(
    project_id: str,
    conversation_id: str,
    db: Session = Depends(get_db),
):
    """
    Fetch current conversation details and message history.
    Enforces strict project scoping: rejects if conversation does not belong to project_id.
    """
    from app.domain.models import Conversation
    conv = (
        db.query(Conversation)
        .filter(Conversation.id == conversation_id, Conversation.project_id == project_id)
        .first()
    )
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found in project {project_id}.",
        )
    return TimeAgentService._to_conversation_dto(db, conv)


@router.patch(
    "/api/v1/projects/{project_id}/agent/conversations/{conversation_id}/pin",
    response_model=ConversationSummaryDTO,
    status_code=status.HTTP_200_OK,
)
def pin_conversation(
    project_id: str,
    conversation_id: str,
    payload: ConversationPinRequest,
    db: Session = Depends(get_db),
):
    """
    Toggle pin state for a conversation strictly scoped to project_id.
    """
    return TimeAgentService.pin_conversation(
        db=db,
        project_id=project_id,
        conversation_id=conversation_id,
        is_pinned=payload.is_pinned,
    )


@router.delete(
    "/api/v1/projects/{project_id}/agent/conversations/{conversation_id}",
    status_code=status.HTTP_200_OK,
)
def delete_conversation(
    project_id: str,
    conversation_id: str,
    db: Session = Depends(get_db),
):
    """
    Permanently delete a conversation and its messages strictly scoped to project_id.
    """
    TimeAgentService.delete_conversation(
        db=db,
        project_id=project_id,
        conversation_id=conversation_id,
    )
    return {"success": True, "message": f"Conversation {conversation_id} permanently deleted."}


@router.post(
    "/api/v1/projects/{project_id}/agent/conversations/{conversation_id}/messages",
    response_model=MessageResponseDTO,
    status_code=status.HTTP_200_OK,
)
def send_agent_message(
    project_id: str,
    conversation_id: str,
    payload: MessageSendRequest,
    x_user_id: str = Header(default="site-supervisor", alias="X-User-ID"),
    db: Session = Depends(get_db),
):
    """
    Send a natural language progress report, update request, or clarification answer.
    """
    return TimeAgentService.process_message(
        db=db,
        project_id=project_id,
        conversation_id=conversation_id,
        user_content=payload.content,
        caller_id=x_user_id,
    )


@router.post(
    "/api/v1/projects/{project_id}/agent/conversations/{conversation_id}/voice",
    response_model=VoiceMessageResponseDTO,
    status_code=status.HTTP_200_OK,
)
async def process_voice_message(
    project_id: str,
    conversation_id: str,
    file: UploadFile = File(...),
    x_user_id: str = Header(default="site-supervisor", alias="X-User-ID"),
    db: Session = Depends(get_db),
):
    """
    Process multilingual voice audio input via Sarvam AI Speech-to-Text (saaras:v4).
    Transcribes audio with automatic language detection, primes construction-specific keyterms,
    and forwards the resulting transcript into the existing Time Agent pipeline.
    """
    # 1. Validate conversation and project scoping
    conv = (
        db.query(Conversation)
        .filter(Conversation.id == conversation_id, Conversation.project_id == project_id)
        .first()
    )
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found for project {project_id}.",
        )

    # 2. Read and validate audio payload
    audio_bytes = await file.read()
    valid, err_msg = SarvamService.validate_audio_file(file.filename, audio_bytes)
    if not valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_msg)

    # 3. Build construction-specific keyterms dynamically (max 50 terms)
    keyterms = SarvamService.build_project_keyterms(db=db, project_id=project_id)

    # 4. Determine transcription language and mode (always 'unknown' for unbiased automatic audio detection)
    target_mode = "codemix" if (conv.language_style == "hinglish" or conv.language == "hinglish") else "transcribe"
    stt_lang = "unknown"

    # 5. Call Sarvam STT
    stt_res = SarvamService.transcribe_audio(
        file_bytes=audio_bytes,
        filename=file.filename or "recording.webm",
        language_code=stt_lang,
        mode=target_mode,
        keyterms=keyterms,
    )
    if isinstance(stt_res, dict):
        transcript = (stt_res.get("transcription") or stt_res.get("transcript") or "").strip()
        detected_lang = stt_res.get("detected_language_code") or "en-IN"
        short_lang = stt_res.get("detected_language") or detected_lang.split("-")[0].lower()
        is_code_mixed = bool(stt_res.get("is_code_mixed", False))
        detected_languages = stt_res.get("detected_languages", [short_lang])
        confidence = stt_res.get("confidence", 1.0)
        low_confidence = bool(stt_res.get("low_confidence", False))
    elif isinstance(stt_res, (list, tuple)):
        transcript = (stt_res[0] or "").strip()
        detected_lang = stt_res[1] if len(stt_res) > 1 else "en-IN"
        short_lang = detected_lang.split("-")[0].lower()
        is_code_mixed = False
        detected_languages = [short_lang]
        confidence = 1.0
        low_confidence = False
    else:
        transcript = str(stt_res or "").strip()
        detected_lang = "en-IN"
        short_lang = "en"
        is_code_mixed = False
        detected_languages = ["en"]
        confidence = 1.0
        low_confidence = False

    if not transcript:
        raise HTTPException(
            status_code=getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", status.HTTP_422_UNPROCESSABLE_ENTITY),
            detail="Couldn't transcribe the audio. Please try again.",
        )

    if low_confidence:
        raise HTTPException(
            status_code=getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", status.HTTP_422_UNPROCESSABLE_ENTITY),
            detail="Audio confidence is too low. Please repeat your message clearly.",
        )

    # 6. Feed transcript into existing Time Agent pipeline with detected language & style
    msg_resp = TimeAgentService.process_message(
        db=db,
        project_id=project_id,
        conversation_id=conversation_id,
        user_content=transcript.strip(),
        caller_id=x_user_id,
        detected_audio_language=detected_lang,
        detected_audio_style="hinglish" if is_code_mixed and "hi" in detected_languages else None,
        raw_transcript=transcript.strip(),
    )

    return VoiceMessageResponseDTO(
        message_id=msg_resp.message_id,
        sender=msg_resp.sender,
        reply_text=msg_resp.reply_text,
        action_card=msg_resp.action_card,
        created_at=msg_resp.created_at,
        transcript=transcript.strip(),
        transcription=transcript.strip(),
        detected_language=short_lang,
        detected_languages=detected_languages,
        is_code_mixed=is_code_mixed,
        confidence=confidence,
        conversation_language=msg_resp.conversation_language,
        conversation_style=msg_resp.conversation_style,
        language_locked=msg_resp.language_locked,
    )


@router.post(
    "/api/v1/projects/{project_id}/agent/voice",
    response_model=VoiceMessageResponseDTO,
    status_code=status.HTTP_200_OK,
)
async def process_project_voice_message(
    project_id: str,
    conversation_id: str = Form(...),
    file: UploadFile = File(...),
    x_user_id: str = Header(default="site-supervisor", alias="X-User-ID"),
    db: Session = Depends(get_db),
):
    """
    Alternative endpoint for voice submission specifying conversation_id as a form parameter.
    """
    return await process_voice_message(
        project_id=project_id,
        conversation_id=conversation_id,
        file=file,
        x_user_id=x_user_id,
        db=db,
    )


@router.post(
    "/api/v1/projects/{project_id}/agent/conversations/{conversation_id}/attachments",
    response_model=AttachmentResponseDTO,
    status_code=status.HTTP_201_CREATED,
)
def upload_agent_attachment(
    project_id: str,
    conversation_id: str,
    file: UploadFile = File(...),
    x_user_id: str = Header(default="site-supervisor", alias="X-User-ID"),
    db: Session = Depends(get_db),
):
    """
    Upload a field report file (PDF, XLSX, CSV, or Audio) into the active conversation.
    Artifact is stored in MinIO and extracted into ExecutionEvents bound to the conversation.
    """
    content = file.file.read()
    return TimeAgentService.process_attachment(
        db=db,
        project_id=project_id,
        conversation_id=conversation_id,
        file_bytes=content,
        filename=file.filename,
        caller_id=x_user_id,
    )


@router.post(
    "/api/v1/projects/{project_id}/agent/conversations/{conversation_id}/confirm",
    response_model=ProposalConfirmResponse,
    status_code=status.HTTP_200_OK,
)
def confirm_update_proposal(
    project_id: str,
    conversation_id: str,
    payload: ProposalConfirmRequest,
    x_user_id: str = Header(default="site-supervisor", alias="X-User-ID"),
    db: Session = Depends(get_db),
):
    """
    Explicitly confirm a staged update proposal.
    Executes an atomic row-locked transaction against PostgreSQL and mutates schedule progress.
    """
    if payload.action.upper() == "REJECT":
        from app.domain.models import UpdateProposal, ExecutionEvent, Conversation, ConversationMessage
        import json
        prop = (
            db.query(UpdateProposal)
            .filter(
                UpdateProposal.id == payload.proposal_id,
                UpdateProposal.conversation_id == conversation_id,
                UpdateProposal.project_id == project_id,
            )
            .first()
        )
        if not prop:
            raise HTTPException(status_code=404, detail="Proposal not found.")
        prop.status = "REJECTED"
        if prop.event_id:
            ev = db.query(ExecutionEvent).filter(ExecutionEvent.id == prop.event_id).first()
            if ev:
                ev.status = "REJECTED"

        conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if conv:
            conv.active_event_id = None
            conv.status = "ACTIVE"
            prior_msgs = (
                db.query(ConversationMessage)
                .filter(ConversationMessage.conversation_id == conv.id, ConversationMessage.sender == "AGENT")
                .all()
            )
            for m in prior_msgs:
                if m.message_metadata:
                    try:
                        meta = json.loads(m.message_metadata) if isinstance(m.message_metadata, str) else m.message_metadata
                        if meta.get("proposal_id") == prop.id:
                            meta["proposal_status"] = "REJECTED"
                            m.message_metadata = json.dumps(meta)
                    except Exception:
                        pass

        db.commit()
        return ProposalConfirmResponse(
            status="REJECTED",
            activity_id=prop.matched_activity_id,
            activity_code="N/A",
            previous_percent=0.0,
            new_percent=0.0,
            audit_log_id="none",
            message="Proposal was rejected.",
        )

    return TimeAgentService.confirm_proposal(
        db=db,
        project_id=project_id,
        conversation_id=conversation_id,
        proposal_id=payload.proposal_id,
        caller_id=x_user_id,
    )


@router.post(
    "/api/v1/projects/{project_id}/agent/conversations/{conversation_id}/bulk-confirm",
    response_model=BulkProposalConfirmResponse,
    status_code=status.HTTP_200_OK,
)
def confirm_bulk_update_proposal(
    project_id: str,
    conversation_id: str,
    payload: BulkProposalConfirmRequest,
    x_user_id: str = Header(default="site-supervisor", alias="X-User-ID"),
    db: Session = Depends(get_db),
):
    """
    Explicitly confirm a staged bulk update proposal.
    Executes an atomic schedule mutation across all confirmed activities.
    """
    return TimeAgentService.confirm_bulk_proposal(
        db=db,
        project_id=project_id,
        conversation_id=conversation_id,
        payload=payload,
        caller_id=x_user_id,
    )


@router.post(
    "/api/tts",
    response_model=TTSResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate text-to-speech audio via Sarvam AI Bulbul V3",
)
@router.post(
    "/api/v1/tts",
    response_model=TTSResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
def convert_text_to_speech(
    payload: TTSRequest,
):
    """
    On-demand Text-to-Speech synthesis using Sarvam AI Bulbul V3 model.
    Accepts text and language code, returning base64-encoded audio data.
    """
    if not payload.text or not payload.text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Text content cannot be empty.",
        )

    try:
        result = SarvamService.text_to_speech(
            text=payload.text,
            language_code=payload.language or "en-IN",
            speaker=payload.speaker,
            model=payload.model or "bulbul:v3",
        )
        return TTSResponse(
            audio_base64=result["audio_base64"],
            content_type=result.get("content_type", "audio/wav"),
            language=result.get("language_code", payload.language or "en-IN"),
            speaker=result.get("speaker", "shubh"),
        )
    except ValueError as ve:
        logger.warning(f"TTS request rejected: {ve}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(ve),
        )
    except Exception as e:
        logger.error(f"TTS synthesis error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate text-to-speech audio. Please try again.",
        )

