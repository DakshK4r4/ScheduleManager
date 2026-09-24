from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.domain.models import (
    Activity,
    Artifact,
    Conversation,
    ConversationMessage,
    ExecutionEvent,
    Project,
    ScheduleAuditLog,
    UpdateProposal,
    WBSNode,
)
from app.schemas.agent import (
    ActionCardDTO,
    AttachmentResponseDTO,
    BulkProposalConfirmRequest,
    BulkProposalConfirmResponse,
    ConversationCreateRequest,
    ConversationDTO,
    ConversationSummaryDTO,
    MessageDTO,
    MessageResponseDTO,
    ParsedConversationalIntent,
    PendingActionDTO,
    ProposalConfirmResponse,
)
from app.services.agent_parser import ConversationalParser
from app.services.extraction_service import ExtractionService
from app.services.matching_service import MatchingService
from app.services.minio_service import minio_service
from app.services.sarvam_service import SarvamService
from app.services.schedule_update_service import ScheduleUpdateService
from app.services.validation_service import ValidationException

logger = logging.getLogger("agent_service")


def _format_utc_iso(dt: Optional[datetime]) -> Optional[str]:
    """
    Format datetime to ISO 8601 with explicit UTC timezone designator ('Z').
    Prevents browsers from interpreting naive UTC strings as local time,
    which leads to timezone-offset discrepancies (e.g. +05:30 in India).
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    iso = dt.isoformat()
    if iso.endswith("+00:00"):
        iso = iso[:-6] + "Z"
    elif not iso.endswith("Z"):
        iso = iso + "Z"
    return iso


class TimeAgentService:
    @classmethod
    def _generate_conversation_title(
        cls,
        text: str,
        parsed: Optional[ParsedConversationalIntent] = None,
    ) -> str:
        """
        Deterministic 3-7 word conversation title generation based on user message and parsed signals.
        Never makes external API calls.
        """
        lower = text.lower()
        loc = parsed.location if parsed and parsed.location else None
        act_code = parsed.reported_activity_code if parsed and parsed.reported_activity_code else None

        # Look for construction tags like F-204, CT-07, CIV-1001
        code_match = re.search(r"\b([A-Z]{1,4}-\d{2,5})\b", text, re.IGNORECASE)
        anchor = loc or (code_match.group(1).upper() if code_match else act_code)

        if anchor and "concrete" in lower:
            return f"{anchor} Concrete Pour"
        if anchor and ("cable" in lower or "tray" in lower):
            return f"{anchor} Cable Tray Progress"
        if anchor and ("pipe" in lower or "piping" in lower):
            return f"{anchor} Piping Progress"
        if anchor and "foundation" in lower:
            return f"{anchor} Foundation Progress"
        if anchor and ("update" in lower or "%" in lower):
            return f"{anchor} Progress Update"
        if anchor:
            return f"{anchor} Execution Report"

        if "upcoming" in lower and "civil" in lower:
            return "Upcoming Civil Activities"
        if "upcoming" in lower and "activit" in lower:
            return "Upcoming Activities"
        if "concrete" in lower and ("pour" in lower or "poured" in lower):
            return "Concrete Pour Progress"
        if "cable tray" in lower:
            return "Cable Tray Progress"
        if "piping" in lower:
            return "Piping Progress"
        if "pump" in lower and ("install" in lower or "installation" in lower):
            return "Pump Installation"
        if "pump" in lower:
            return "Pump Installation"
        if "inspection" in lower:
            return "Foundation Inspection"
        if "mechanical" in lower:
            return "Mechanical Progress"
        if "electrical" in lower:
            return "Electrical Progress"

        # Fallback: clean words
        words = [
            w.strip(",.!?\"';:()[]{}")
            for w in text.split()
            if w.lower() not in {
                "we", "i", "can", "you", "please", "the", "a", "an", "is", "are",
                "for", "to", "in", "at", "today", "yesterday", "our", "all",
            }
        ]
        clean_words = [w for w in words if w]
        if clean_words:
            cand = " ".join(clean_words[:5]).title()
            return cand[:45].strip()

        return "Site Progress Report"

    @classmethod
    def get_or_create_conversation(
        cls,
        db: Session,
        project_id: str,
        user_id: str = "site-supervisor",
        active_activity_id: Optional[str] = None,
        force_new: bool = False,
        title: Optional[str] = None,
    ) -> ConversationDTO:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project {project_id} not found.",
            )

        conv = None
        if not force_new:
            # Look for existing active conversation for this user and project
            conv = (
                db.query(Conversation)
                .filter(
                    Conversation.project_id == project_id,
                    Conversation.user_id == user_id,
                    Conversation.status.in_(["ACTIVE", "WAITING_FOR_USER"]),
                )
                .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
                .first()
            )

        if not conv:
            conv = Conversation(
                id=f"conv-{uuid.uuid4().hex[:8]}",
                project_id=project_id,
                title=title or "New Chat",
                user_id=user_id,
                active_activity_id=active_activity_id,
                status="ACTIVE",
            )
            db.add(conv)
            db.commit()
            db.refresh(conv)
        elif active_activity_id and conv.active_activity_id != active_activity_id:
            conv.active_activity_id = active_activity_id
            conv.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
            db.refresh(conv)

        return cls._to_conversation_dto(db, conv)

    @classmethod
    def list_conversations(
        cls,
        db: Session,
        project_id: str,
        user_id: str = "site-supervisor",
        search_query: Optional[str] = None,
    ) -> List[ConversationSummaryDTO]:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project {project_id} not found.",
            )

        # Base query strictly scoped to project_id - HARD ISOLATION
        base_query = db.query(Conversation).filter(
            Conversation.project_id == project_id,
        )

        if search_query and search_query.strip():
            term = f"%{search_query.strip()}%"
            matching_msg_conv_ids = (
                db.query(ConversationMessage.conversation_id)
                .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
                .filter(
                    Conversation.project_id == project_id,
                    ConversationMessage.content.ilike(term),
                )
            )
            base_query = base_query.filter(
                or_(
                    Conversation.title.ilike(term),
                    Conversation.id.in_(matching_msg_conv_ids),
                )
            )

        conversations = (
            base_query
            .order_by(Conversation.is_pinned.desc(), Conversation.updated_at.desc(), Conversation.created_at.desc())
            .all()
        )

        conv_ids = [c.id for c in conversations]
        msg_counts = {}
        if conv_ids:
            counts = (
                db.query(ConversationMessage.conversation_id, func.count(ConversationMessage.id))
                .filter(ConversationMessage.conversation_id.in_(conv_ids))
                .group_by(ConversationMessage.conversation_id)
                .all()
            )
            msg_counts = {cid: cnt for cid, cnt in counts}

        summaries = []
        for c in conversations:
            summaries.append(
                ConversationSummaryDTO(
                    id=c.id,
                    project_id=c.project_id,
                    title=c.title or "New Chat",
                    status=c.status,
                    language=getattr(c, "language", None),
                    conversation_language=getattr(c, "language", None),
                    conversation_style=getattr(c, "language_style", None),
                    language_locked=bool(getattr(c, "language_locked", False)),
                    is_pinned=bool(getattr(c, "is_pinned", False)),
                    created_at=_format_utc_iso(c.created_at) or _format_utc_iso(datetime.now(timezone.utc)),
                    updated_at=_format_utc_iso(c.updated_at) or _format_utc_iso(datetime.now(timezone.utc)),
                    message_count=msg_counts.get(c.id, 0),
                    active_activity_id=c.active_activity_id,
                    active_event_id=c.active_event_id,
                )
            )
        return summaries

    @classmethod
    def _to_conversation_dto(cls, db: Session, conv: Conversation) -> ConversationDTO:
        act_dict = None
        if conv.active_activity_id:
            act = db.query(Activity).filter(Activity.id == conv.active_activity_id).first()
            if act:
                act_dict = {
                    "activity_id": act.id,
                    "activity_code": act.activity_code,
                    "name": act.name,
                    "percent_complete": act.percent_complete or 0.0,
                    "status": act.status,
                }

        messages = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.conversation_id == conv.id)
            .order_by(ConversationMessage.created_at.asc())
            .all()
        )
        msg_dtos = []
        for m in messages:
            meta = json.loads(m.message_metadata) if m.message_metadata else None
            # Enrich proposal status from authoritative update_proposals table
            if meta and meta.get("type") == "PROPOSAL_CONFIRMATION" and meta.get("proposal_id"):
                prop = (
                    db.query(UpdateProposal)
                    .filter(UpdateProposal.id == meta["proposal_id"])
                    .first()
                )
                if prop:
                    if prop.status == "PENDING" and datetime.now(timezone.utc).replace(tzinfo=None) > prop.expires_at:
                        meta["proposal_status"] = "EXPIRED"
                    else:
                        meta["proposal_status"] = prop.status

            msg_dtos.append(
                MessageDTO(
                    id=m.id,
                    sender=m.sender,
                    content=m.content,
                    message_metadata=meta,
                    created_at=_format_utc_iso(m.created_at) or _format_utc_iso(datetime.now(timezone.utc)),
                )
            )

        pending_action: Optional[PendingActionDTO] = None
        current_status = conv.status
        for m_dto in reversed(msg_dtos):
            if m_dto.sender == "AGENT" and m_dto.message_metadata:
                meta = m_dto.message_metadata
                if meta.get("type") == "BULK_SCOPE_PROPOSAL":
                    if meta.get("proposal_status") not in ("APPLIED", "CANCELLED", "REJECTED"):
                        acts = meta.get("bulk_activities") or []
                        pending_action = PendingActionDTO(
                            type="BULK_UPDATE_CONFIRMATION",
                            status="PENDING",
                            activity_count=meta.get("bulk_count") or len(acts),
                            target_percent=meta.get("target_percent", 100.0),
                            bulk_activities=acts,
                        )
                        current_status = "WAITING_FOR_BULK_UPDATE_CONFIRMATION"
                    elif current_status == "WAITING_FOR_BULK_UPDATE_CONFIRMATION":
                        current_status = "ACTIVE"
                    break
                elif meta.get("type") == "PROPOSAL_CONFIRMATION":
                    if meta.get("proposal_status") == "PENDING":
                        pending_action = PendingActionDTO(
                            type="PROPOSAL_CONFIRMATION",
                            proposal_id=meta.get("proposal_id"),
                            status="PENDING",
                            target_percent=meta.get("proposed_percent"),
                        )
                    break

        return ConversationDTO(
            conversation_id=conv.id,
            project_id=conv.project_id,
            title=conv.title or "New Chat",
            status=current_status,
            language=getattr(conv, "language", None),
            conversation_language=getattr(conv, "language", None),
            conversation_style=getattr(conv, "language_style", None),
            language_locked=bool(getattr(conv, "language_locked", False)),
            is_pinned=bool(getattr(conv, "is_pinned", False)),
            active_activity=act_dict,
            active_event_id=conv.active_event_id,
            clarification_turns=conv.clarification_turns,
            created_at=_format_utc_iso(conv.created_at),
            updated_at=_format_utc_iso(conv.updated_at),
            pending_action=pending_action,
            history=msg_dtos,
        )

    @classmethod
    def pin_conversation(
        cls,
        db: Session,
        project_id: str,
        conversation_id: str,
        is_pinned: bool,
    ) -> ConversationSummaryDTO:
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
        conv.is_pinned = is_pinned
        db.commit()
        db.refresh(conv)

        msg_count = (
            db.query(func.count(ConversationMessage.id))
            .filter(ConversationMessage.conversation_id == conv.id)
            .scalar()
            or 0
        )

        return ConversationSummaryDTO(
            id=conv.id,
            project_id=conv.project_id,
            title=conv.title or "New Chat",
            status=conv.status,
            language=getattr(conv, "language", None),
            is_pinned=bool(getattr(conv, "is_pinned", False)),
            created_at=_format_utc_iso(conv.created_at) or _format_utc_iso(datetime.now(timezone.utc)),
            updated_at=_format_utc_iso(conv.updated_at) or _format_utc_iso(datetime.now(timezone.utc)),
            message_count=msg_count,
            active_activity_id=conv.active_activity_id,
            active_event_id=conv.active_event_id,
        )

    @classmethod
    def delete_conversation(
        cls,
        db: Session,
        project_id: str,
        conversation_id: str,
    ) -> None:
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

        # Safely unlink references
        conv.active_event_id = None
        conv.active_activity_id = None
        db.flush()

        # Unlink execution events
        db.query(ExecutionEvent).filter(ExecutionEvent.conversation_id == conv.id).update(
            {"conversation_id": None, "message_id": None}
        )
        db.flush()

        db.delete(conv)
        db.commit()

    @classmethod
    def _resolve_template_language(cls, conv: Conversation) -> str:
        lang = (conv.language or "en").lower().strip()
        style = (conv.language_style or "").lower().strip()
        if style == "hinglish" or lang == "hinglish":
            return "hinglish"
        if lang.startswith("hi"):
            return "hi"
        return "en"

    @classmethod
    def process_message(
        cls,
        db: Session,
        project_id: str,
        conversation_id: str,
        user_content: str,
        caller_id: str = "site-supervisor",
        detected_audio_language: Optional[str] = None,
        detected_audio_style: Optional[str] = None,
        raw_transcript: Optional[str] = None,
    ) -> MessageResponseDTO:
        # 1. Validate conversation and project
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

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")

        # Record user message
        user_msg = ConversationMessage(
            id=f"msg-{uuid.uuid4().hex[:8]}",
            conversation_id=conv.id,
            sender="USER",
            content=user_content,
        )
        db.add(user_msg)
        db.flush()

        active_act_code = None
        if conv.active_activity_id:
            act = db.query(Activity).filter(Activity.id == conv.active_activity_id).first()
            if act:
                active_act_code = act.activity_code

        is_clarification = (conv.active_event_id is not None and conv.clarification_turns > 0)

        # 1b. Check for explicit translation requests (Requirement 4)
        explicit_trans_target = ConversationalParser.is_explicit_translation_request(user_content)
        if explicit_trans_target:
            norm_trans_target = SarvamService.normalize_language_code(explicit_trans_target)
            last_agent_msg = (
                db.query(ConversationMessage)
                .filter(ConversationMessage.conversation_id == conv.id, ConversationMessage.sender == "AGENT")
                .order_by(ConversationMessage.created_at.desc())
                .first()
            )
            if last_agent_msg and last_agent_msg.content:
                translated_content = SarvamService.translate_text(
                    last_agent_msg.content,
                    target_language_code=norm_trans_target,
                    source_language_code=conv.language or "en-IN",
                )
                conv.language = norm_trans_target
                conv.language_style = "hindi" if norm_trans_target.startswith("hi") else "english"
                db.flush()
                return cls._save_and_return_agent_response(
                    db, conv, translated_content, action_card=None,
                    transcript=raw_transcript, detected_language=norm_trans_target,
                )

        # 1c. Check for explicit language switch requests (Requirement 22)
        explicit_target = ConversationalParser.is_explicit_language_switch_request(user_content)
        if explicit_target:
            if conv.language and not detected_audio_language:
                lang_tag = conv.language.lower()
                style_tag = (conv.language_style or "").lower()
                if style_tag == "hinglish" or lang_tag == "hinglish":
                    reply_text = "Yeh conversation Hinglish mein locked hai. Aap English ya Hindi mein baat karne ke liye naya chat shuru kar sakte hain."
                elif lang_tag.startswith("hi"):
                    reply_text = "यह बातचीत हिंदी में लॉक है। आप अंग्रेज़ी में नया चैट शुरू कर सकते हैं।"
                else:
                    reply_text = "This conversation is locked to English. You can start a new chat to converse in another language."
                return cls._save_and_return_agent_response(
                    db, conv, reply_text, action_card=None,
                    transcript=raw_transcript, detected_language=detected_audio_language,
                )
            else:
                chosen = explicit_target if explicit_target in ("en", "hi", "hinglish") else "en"
                conv.language = SarvamService.normalize_language_code(chosen)
                conv.language_style = "hinglish" if chosen == "hinglish" else ("hindi" if chosen == "hi" else "english")
                conv.language_locked = True
                db.flush()
                if chosen == "hi":
                    reply_text = "नमस्ते! यह बातचीत हिंदी में सेट कर दी गई है। आप किस activity की प्रगति रिपोर्ट करना चाहते हैं?"
                elif chosen == "hinglish":
                    reply_text = "Hello! Yeh conversation Hinglish mein set ho gayi hai. Aap kis activity ki progress report karna chahte hain?"
                else:
                    reply_text = "Hello! This conversation is set to English. Which activity would you like to report progress on?"
                return cls._save_and_return_agent_response(
                    db, conv, reply_text, action_card=None,
                    transcript=raw_transcript, detected_language=detected_audio_language,
                )

        # 1d. Language detection: Spoken audio language takes immediate precedence
        if detected_audio_language:
            norm_audio_lang = SarvamService.normalize_language_code(detected_audio_language)
            conv.language = norm_audio_lang
            if detected_audio_style:
                conv.language_style = detected_audio_style
            else:
                lang_analysis = ConversationalParser.analyze_language_and_codemixing(user_content, audio_detected_lang=norm_audio_lang)
                conv.language_style = lang_analysis.get("language_style", "standard")
            conv.language_locked = True
            db.flush()
        elif not conv.language_locked or conv.language is None:
            if ConversationalParser.is_meaningful_for_language_lock(user_content):
                lang_analysis = ConversationalParser.analyze_language_and_codemixing(user_content)
                conv.language = lang_analysis["primary_language"]
                conv.language_style = lang_analysis["language_style"]
                conv.language_locked = True
                db.flush()
        elif conv.language and not conv.language_locked:
            conv.language_locked = True
            db.flush()

        # The conversation language determines the response template / translation
        resp_lang = cls._resolve_template_language(conv)

        # 1c. Fast-path cancellation and bulk decision check (MUST run BEFORE ConversationalParser / Gemini LLM calls)
        clean_user_txt = user_content.strip().lower().rstrip(".,;:!?")
        user_upper = user_content.strip().upper()

        cancellation_phrases = [
            "cancel", "cancel this", "cancel update", "cancel this update", "cancel it",
            "cancel proposal", "cancel all", "cancel bulk", "cancel them", "don't update", "dont update",
            "do not update", "don't update them", "dont update them", "do not update them",
            "no", "no don't", "no dont", "no don't update", "no, don't update", "no, dont update",
            "no, don't update them", "no don't update them", "no, dont update them", "no thanks", "no thank you",
            "don't", "dont", "abort", "reject", "reject proposal", "reject update", "dismiss", "stop",
            "radd karo", "radd kardo", "update cancel karo", "update cancel kardo",
            "cancel karo", "cancel kardo", "nahi karna", "mat karo", "mat karna", "nahi", "nahin",
            "isko cancel karo", "kripya cancel karein", "kripya radd karein", "band karo"
        ]
        is_explicit_cancel = (
            any(clean_user_txt == c or clean_user_txt.startswith(c + " ") or clean_user_txt.startswith(c + ",") for c in cancellation_phrases)
            or clean_user_txt in cancellation_phrases
            or any(c in user_content for c in ["रद्द करें", "कैंसिल करें", "रद्द करो", "कैंसिल करो", "खारिज करें", "नहीं", "मत करो"])
        )

        # Check if conversation has a pending bulk update proposal awaiting user decision
        prior_agent_msgs_for_bulk = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.conversation_id == conv.id, ConversationMessage.sender == "AGENT")
            .order_by(ConversationMessage.created_at.desc())
            .limit(10)
            .all()
        )
        pending_bulk_meta = None
        pending_bulk_msg = None
        for m in prior_agent_msgs_for_bulk:
            if m.message_metadata:
                try:
                    meta = json.loads(m.message_metadata) if isinstance(m.message_metadata, str) else m.message_metadata
                    if meta.get("type") == "BULK_SCOPE_PROPOSAL":
                        if meta.get("proposal_status") not in ("APPLIED", "CANCELLED", "REJECTED"):
                            pending_bulk_meta = meta
                            pending_bulk_msg = m
                        break
                    elif meta.get("type") == "PROPOSAL_CONFIRMATION":
                        break
                except Exception:
                    pass

        # Case 1: Pending bulk update is active
        if pending_bulk_meta:
            # 1.1 Cancellation of pending bulk update (Fast-path, no LLM)
            if is_explicit_cancel:
                pending_bulk_meta["proposal_status"] = "CANCELLED"
                if pending_bulk_msg:
                    pending_bulk_msg.message_metadata = json.dumps(pending_bulk_meta)

                if conv.active_event_id:
                    active_ev = db.query(ExecutionEvent).filter(ExecutionEvent.id == conv.active_event_id).first()
                    if active_ev:
                        active_ev.status = "REJECTED"

                pending_props = db.query(UpdateProposal).filter(
                    UpdateProposal.conversation_id == conv.id,
                    UpdateProposal.status == "PENDING",
                ).all()
                for p in pending_props:
                    p.status = "REJECTED"

                conv.active_event_id = None
                conv.status = "ACTIVE"
                conv.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
                db.commit()

                if resp_lang == "hi":
                    reply_text = "Bulk update cancel कर दिया गया है। Authoritative schedule में कोई बदलाव नहीं किया गया।"
                elif resp_lang == "hinglish":
                    reply_text = "Bulk update cancel kar diya gaya hai. Authoritative schedule mein koi change nahi hua."
                else:
                    reply_text = "Bulk update was cancelled. No changes were applied to the schedule."
                return cls._save_and_return_agent_response(db, conv, reply_text, action_card=None)

            # 1.2 Confirmation of pending bulk update (Fast-path, no LLM)
            confirmation_phrases = [
                "yes", "yes update them", "yes, update them", "update them", "update all",
                "update all of them", "confirm", "confirm update", "confirm all", "confirm all of them",
                "apply", "apply update", "apply all", "proceed", "go ahead",
                "haan", "ha", "theek hai", "kardo", "kar do", "update kardo", "update kar do",
                "confirm kardo", "sabhi update kardo", "sab update kardo", "confirm all bulk"
            ]
            is_explicit_confirm = (
                any(clean_user_txt == c or clean_user_txt.startswith(c + " ") or clean_user_txt.startswith(c + ",") for c in confirmation_phrases)
                or any(c in user_content for c in ["हाँ", "पुष्टि करें", "सभी को अपडेट करें", "अपडेट करें"])
                or user_upper == "CONFIRM_ALL_BULK"
            )
            if is_explicit_confirm:
                bulk_acts = pending_bulk_meta.get("bulk_activities") or []
                target_act_ids = [a["activity_id"] for a in bulk_acts if a.get("activity_id")]
                target_pct = pending_bulk_meta.get("target_percent", 100.0)
                if target_act_ids:
                    bulk_req = BulkProposalConfirmRequest(
                        activity_ids=target_act_ids,
                        action="CONFIRM",
                        target_percent=target_pct,
                        status_reported=pending_bulk_meta.get("proposed_status", "COMPLETED"),
                    )
                    bulk_resp = cls.confirm_bulk_proposal(
                        db=db,
                        project_id=project_id,
                        conversation_id=conv.id,
                        payload=bulk_req,
                        caller_id=caller_id,
                    )
                    return MessageResponseDTO(
                        message_id=f"msg-{uuid.uuid4().hex[:8]}",
                        sender="AGENT",
                        reply_text=bulk_resp.message,
                        action_card=None,
                        created_at=_format_utc_iso(datetime.now(timezone.utc)),
                        conversation_language=conv.language,
                        conversation_style=conv.language_style,
                        language_locked=bool(conv.language_locked),
                    )

            # 1.3 User selects a specific activity code or table review
            bulk_acts = pending_bulk_meta.get("bulk_activities") or []
            bulk_codes = [a.get("activity_code", "").strip().upper() for a in bulk_acts if a.get("activity_code")]
            if user_upper in bulk_codes or user_upper == "REVIEW_IN_TABLE":
                # Fall through to standard activity intent handling for this specific code
                pass
            else:
                # 1.4 Chat lock guard: Block unrelated messages while bulk update confirmation is pending
                conv.status = "WAITING_FOR_BULK_UPDATE_CONFIRMATION"
                db.commit()
                if resp_lang == "hi":
                    reply_text = (
                        "एक bulk update प्रस्ताव वर्तमान में आपके निर्णय की प्रतीक्षा कर रहा है। "
                        "कृपया नया संदेश भेजने से पहले ऊपर दिए गए प्रस्ताव की पुष्टि करें या उसे रद्द करें।"
                    )
                elif resp_lang == "hinglish":
                    reply_text = (
                        "Ek bulk update proposal filhal aapke decision ka wait kar raha hai. "
                        "Kripya naya message bhejne se pehle upar diye gaye proposal ko confirm ya cancel karein."
                    )
                else:
                    reply_text = (
                        "A bulk update proposal is currently awaiting your decision. "
                        "Please confirm or cancel the pending proposal above before sending another request."
                    )
                return cls._save_and_return_agent_response(db, conv, reply_text, action_card=None)

        # Case 1b: Pending single activity update proposal is active awaiting confirmation
        pending_single_prop = (
            db.query(UpdateProposal)
            .filter(
                UpdateProposal.conversation_id == conv.id,
                UpdateProposal.status == "PENDING",
            )
            .first()
        )
        if pending_single_prop:
            if is_explicit_cancel:
                pending_single_prop.status = "REJECTED"
                if pending_single_prop.event_id:
                    ev = db.query(ExecutionEvent).filter(ExecutionEvent.id == pending_single_prop.event_id).first()
                    if ev:
                        ev.status = "REJECTED"
                conv.active_event_id = None
                conv.status = "ACTIVE"
                conv.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
                db.commit()
                if resp_lang == "hi":
                    reply_text = "Update cancel कर दिया गया है। Authoritative schedule में कोई बदलाव नहीं किया गया।"
                elif resp_lang == "hinglish":
                    reply_text = "Update cancel kar diya gaya hai. Authoritative schedule mein koi change nahi hua."
                else:
                    reply_text = "Update cancelled. No changes were applied to the schedule."
                return cls._save_and_return_agent_response(db, conv, reply_text, action_card=None)

            confirmation_phrases = [
                "yes", "yes update it", "yes, update it", "update it", "confirm", "confirm update",
                "apply", "apply update", "proceed", "go ahead",
                "haan", "ha", "theek hai", "kardo", "kar do", "update kardo", "update kar do",
                "confirm kardo", "confirm update"
            ]
            is_single_confirm = (
                any(clean_user_txt == c or clean_user_txt.startswith(c + " ") or clean_user_txt.startswith(c + ",") for c in confirmation_phrases)
                or any(c in user_content for c in ["हाँ", "पुष्टि करें", "अपडेट करें"])
                or user_upper == "CONFIRM"
            )
            if is_single_confirm:
                conf_resp = cls.confirm_proposal(
                    db=db,
                    project_id=project_id,
                    conversation_id=conv.id,
                    proposal_id=pending_single_prop.id,
                    caller_id=caller_id,
                )
                return cls._save_and_return_agent_response(db, conv, conf_resp.message, action_card=None)

            # If not confirm/cancel, allow INFORMATION_QUERY to be answered; other intents are blocked after parsing below.
            pass

        # Case 2: Standard cancellation when no bulk proposal is pending
        if is_explicit_cancel:
            active_ev = None
            if conv.active_event_id:
                active_ev = db.query(ExecutionEvent).filter(ExecutionEvent.id == conv.active_event_id).first()
                if active_ev:
                    active_ev.status = "REJECTED"

            pending_props = db.query(UpdateProposal).filter(
                UpdateProposal.conversation_id == conv.id,
                UpdateProposal.status == "PENDING",
            ).all()
            for p in pending_props:
                p.status = "REJECTED"

            prior_agent_msgs = (
                db.query(ConversationMessage)
                .filter(ConversationMessage.conversation_id == conv.id, ConversationMessage.sender == "AGENT")
                .order_by(ConversationMessage.created_at.desc())
                .limit(10)
                .all()
            )
            had_pending_action_card = False
            for m in prior_agent_msgs:
                if m.message_metadata:
                    try:
                        meta = json.loads(m.message_metadata) if isinstance(m.message_metadata, str) else m.message_metadata
                        if meta.get("type") == "BULK_SCOPE_PROPOSAL" and meta.get("proposal_status") not in ("APPLIED", "CANCELLED", "REJECTED"):
                            meta["proposal_status"] = "CANCELLED"
                            m.message_metadata = json.dumps(meta)
                            had_pending_action_card = True
                        elif meta.get("type") == "PROPOSAL_CONFIRMATION" and meta.get("proposal_status") in ("PENDING", None):
                            meta["proposal_status"] = "REJECTED"
                            m.message_metadata = json.dumps(meta)
                            had_pending_action_card = True
                    except Exception:
                        pass

            if active_ev or pending_props or had_pending_action_card or conv.status in ("WAITING_CLARIFICATION", "WAITING_FOR_BULK_UPDATE_CONFIRMATION"):
                conv.active_event_id = None
                conv.status = "ACTIVE"
                conv.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
                db.commit()
                if resp_lang == "hi":
                    reply_text = "Update cancel कर दिया गया है। Authoritative schedule में कोई बदलाव नहीं किया गया।"
                elif resp_lang == "hinglish":
                    reply_text = "Update cancel kar diya gaya hai. Authoritative schedule mein koi changes apply nahi hue."
                else:
                    reply_text = "Update cancelled. No changes were applied to the schedule."
                return cls._save_and_return_agent_response(db, conv, reply_text, action_card=None)
            else:
                was_recently_updated = any(
                    any(term in m.content.lower() for term in ["successfully updated", "successfully applied", "confirmed and applied"])
                    for m in prior_agent_msgs
                )
                if was_recently_updated:
                    if resp_lang == "hi":
                        reply_text = (
                            "पिछला update पहले ही authoritative schedule ledger में commit किया जा चुका है। "
                            "Committed progress को केवल cancel command से पूर्ववत (undo) नहीं किया जा सकता; "
                            "प्रगति को संशोधित करने के लिए कृपया नया progress percentage बताएं (जैसे 'set ELE-1001 to 0%')।"
                        )
                    elif resp_lang == "hinglish":
                        reply_text = (
                            "Previous update already authoritative schedule ledger mein commit ho chuka hai. "
                            "Committed progress ko cancel command se undo nahi kiya ja sakta; progress change karne ke liye "
                            "kripya naya percentage report karein (e.g. 'set ELE-1001 to 0%') ya Activities Table mein update karein."
                        )
                    else:
                        reply_text = (
                            "The previous update has already been committed and applied to the authoritative schedule ledger. "
                            "Committed progress cannot be simply undone with a cancel command; to adjust or revert progress, "
                            "please report the new progress percentage (e.g., 'set ELE-1001 to 0%') or update the activities in the Activities Table."
                        )
                else:
                    if resp_lang == "hi":
                        reply_text = "Cancel करने के लिए कोई pending update या proposal नहीं है।"
                    elif resp_lang == "hinglish":
                        reply_text = "Cancel karne ke liye koi pending update ya proposal nahi hai."
                    else:
                        reply_text = "There are no pending updates or proposals to cancel."
                return cls._save_and_return_agent_response(db, conv, reply_text, action_card=None)

        # 2. Conversational Intent & Entity Extraction via Gemini (with rule fallback)
        parsed = ConversationalParser.parse_message(
            text=user_content,
            project_data_date=project.data_date,
            active_activity_code=active_act_code,
            is_clarification_turn=is_clarification,
            conversation_language=conv.language,
            conversation_style=conv.language_style,
            language_locked=bool(conv.language_locked),
        )

        # 2b. Automatically generate deterministic title on first meaningful message
        if not conv.title or conv.title == "New Chat":
            conv.title = cls._generate_conversation_title(user_content, parsed)
        conv.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        # 2c. Check for schedule baseline structure requests (Activity creation / deletion)
        lower_txt = user_content.lower().strip()
        is_structure_request = any(
            re.search(p, lower_txt) for p in [
                r"\b(create|add|new)\b.*?\b(activity|task|wbs)\b",
                r"\b(delete|remove|drop)\b.*?\b(activity|task)\b",
                r"\b(activity|task)\b.*?\b(create|delete|remove|drop|hatao|hata do|banao|bana do)\b",
                r"\b(naya|nayi)\b.*?\b(activity|task)\b",
            ]
        ) or any(w in user_content for w in ["एक्टिविटी बनाएं", "एक्टिविटी जोड़ें", "एक्टिविटी हटाएं", "एक्टिविटी डिलीट"])

        if is_structure_request and not is_clarification:
            if resp_lang == "hi":
                reply_text = (
                    "Primavera P6 WBS और CPM बेसलाइन अखंडता बनाए रखने के लिए, एक्टिविटी निर्माण या विलोपन "
                    "प्रोजेक्ट शेड्यूल कंट्रोल/एक्टिविटीज टेबल या XER/XML बेसलाइन आयात के माध्यम से प्रबंधित किया जाता है। "
                    "मैं साइट पर प्रगति और निष्पादन अपडेट रिकॉर्ड करने में आपकी सहायता कर सकता हूँ।"
                )
            elif resp_lang == "hinglish":
                reply_text = (
                    "Primavera P6 WBS aur CPM baseline integrity preserve karne ke liye, activities create ya delete "
                    "karna Project Activities Table ya XER/XML schedule import ke zariye governed hota hai. "
                    "Aap mujhse field progress updates aur activity tracking karwa sakte hain."
                )
            else:
                reply_text = (
                    "To maintain Primavera P6 WBS hierarchy and CPM baseline integrity, creating or deleting activities "
                    "is governed through the Project Schedule Control / Activities Table or XER/XML baseline imports. "
                    "I can help you record execution progress, quantities, and status updates for existing activities."
                )
            return cls._save_and_return_agent_response(
                db=db,
                conv=conv,
                reply_text=reply_text,
                action_card=None,
                transcript=raw_transcript,
                detected_language=detected_audio_language,
            )

        # 3. Branch by Intent
        if parsed.intent == "INFORMATION_QUERY":
            reply_text = cls._handle_information_query(db, project, conv, parsed, user_content)
            return cls._save_and_return_agent_response(
                db=db,
                conv=conv,
                reply_text=reply_text,
                action_card=None,
                transcript=raw_transcript,
                detected_language=detected_audio_language,
            )

        if pending_single_prop:
            target_act = db.query(Activity).filter(Activity.id == pending_single_prop.matched_activity_id).first()
            act_code = target_act.activity_code if target_act else "the activity"
            if resp_lang == "hi":
                reply_text = f"{act_code} के लिए एक अपडेट प्रस्ताव वर्तमान में आपके निर्णय की प्रतीक्षा कर रहा है। कृपया नया संदेश भेजने से पहले ऊपर दिए गए प्रस्ताव की पुष्टि करें या उसे रद्द करें।"
            elif resp_lang == "hinglish":
                reply_text = f"{act_code} ke liye update proposal filhal aapke decision ka wait kar raha hai. Kripya naya message bhejne se pehle upar diye gaye proposal ko confirm ya cancel karein."
            else:
                reply_text = f"A proposed update for {act_code} is currently awaiting your decision. Please confirm or cancel the pending proposal above before sending another request."
            return cls._save_and_return_agent_response(db, conv, reply_text, action_card=None)

        if parsed.intent == "BULK_PROGRESS_REPORT" or (parsed.is_bulk and not is_clarification):
            res = cls._handle_bulk_progress(
                db=db,
                project=project,
                conv=conv,
                parsed=parsed,
                raw_text=user_content,
                trigger_message_id=user_msg.id,
                caller_id=caller_id,
            )
            if raw_transcript is not None and res.transcript is None:
                res.transcript = raw_transcript
            if detected_audio_language is not None and res.detected_language is None:
                res.detected_language = detected_audio_language
            res.conversation_language = conv.language
            res.conversation_style = conv.language_style
            res.language_locked = bool(conv.language_locked)
            return res

        # Handle Progress Report / Update Request / Clarification
        res = cls._handle_progress_or_clarification(
            db=db,
            project=project,
            conv=conv,
            parsed=parsed,
            raw_text=user_content,
            trigger_message_id=user_msg.id,
            caller_id=caller_id,
        )
        if raw_transcript is not None and res.transcript is None:
            res.transcript = raw_transcript
        if detected_audio_language is not None and res.detected_language is None:
            res.detected_language = detected_audio_language
        res.conversation_language = conv.language
        res.conversation_style = conv.language_style
        res.language_locked = bool(conv.language_locked)
        return res

    @classmethod
    def _handle_information_query(
        cls,
        db: Session,
        project: Project,
        conv: Conversation,
        parsed: ParsedConversationalIntent,
        raw_text: str,
    ) -> str:
        resp_lang = cls._resolve_template_language(conv)

        # 1. Query activity by code or location tag (e.g. F-204) if specifically cited in message
        target_ref = parsed.reported_activity_code or parsed.location
        if not target_ref:
            extracted_code = ConversationalParser.extract_activity_code(raw_text)
            if extracted_code:
                target_ref = extracted_code

        if target_ref:
            act = (
                db.query(Activity)
                .filter(
                    Activity.project_id == project.id,
                    or_(
                        Activity.activity_code == target_ref,
                        Activity.location_code == target_ref,
                        Activity.name.ilike(f"%{target_ref}%"),
                    ),
                )
                .first()
            )
            if act:
                pct = int(round(act.percent_complete or 0.0)) if (act.percent_complete or 0.0).is_integer() else (act.percent_complete or 0.0)
                display_id = act.activity_code if target_ref == act.activity_code else f"{act.activity_code} ({target_ref})"
                if resp_lang == "hi":
                    return f"{display_id} की वर्तमान प्रगति {pct}% है।"
                elif resp_lang == "hinglish":
                    return f"{display_id} ka current progress {pct}% hai."
                else:
                    return f"The current progress of {display_id} is {pct}%."
            elif parsed.reported_activity_code or extracted_code:
                if resp_lang == "hi":
                    return f"प्रोजेक्ट '{project.project_code}' में एक्टिविटी '{target_ref}' नहीं मिली। कृपया एक्टिविटी कोड की जांच करें।"
                elif resp_lang == "hinglish":
                    return f"Project '{project.project_code}' mein activity '{target_ref}' nahi mili. Kripya activity code check karein."
                else:
                    return f"Activity '{target_ref}' was not found in project '{project.project_code}'. Please check the activity code and try again."

        # 2. If active activity is anchored and no specific activity cited, return its status
        if conv.active_activity_id:
            act = db.query(Activity).filter(Activity.id == conv.active_activity_id).first()
            if act:
                wbs = db.query(WBSNode).filter(WBSNode.id == act.wbs_id).first() if act.wbs_id else None
                p_start = act.planned_start.strftime('%Y-%m-%d') if act.planned_start else 'N/A'
                p_finish = act.planned_finish.strftime('%Y-%m-%d') if act.planned_finish else 'N/A'
                wbs_name = wbs.name if wbs else 'N/A'
                pct = int(round(act.percent_complete or 0.0)) if (act.percent_complete or 0.0).is_integer() else (act.percent_complete or 0.0)
                if resp_lang == "hi":
                    return (
                        f"{act.activity_code} ({act.name}) की वर्तमान प्रगति {pct}% है "
                        f"(Status: {act.status}, Planned finish: {p_finish})।"
                    )
                elif resp_lang == "hinglish":
                    return (
                        f"{act.activity_code} ({act.name}) ka current progress {pct}% hai "
                        f"(Status: {act.status}, Planned finish: {p_finish})."
                    )
                else:
                    return (
                        f"Current progress for {act.activity_code} ({act.name}) is {pct}% "
                        f"(Status: {act.status}, Planned finish: {p_finish})."
                    )

        # 3. Check for historical knowledge / productivity / duration questions
        lower = raw_text.lower()
        is_historical = any(
            term in lower
            for term in [
                "historical", "observed", "production rate", "rate of", "how long did",
                "past", "average duration", "planned vs actual", "variance", "productivity",
                "per day", "per reporting day", "history", "benchmark", "how much did we install",
                "pouring rate", "installation rate", "pichle", "pichla", "purane", "purana",
                "pehle", "itishas"
            ]
        ) or any(term in raw_text for term in ["उत्पादकता", "पिछला", "पुराना", "इतिहास"])

        if is_historical:
            q_type = "PRODUCTIVITY"
            if any(term in lower for term in ["duration", "long", "time taken", "variance", "samay"]):
                q_type = "DURATION"
            elif any(term in lower for term in ["history", "records", "ledger", "record"]):
                q_type = "EXECUTION_HISTORY"

            disc = parsed.discipline
            if not disc:
                if any(k in lower for k in ["pipe", "piping", "spool", "पाइप"]):
                    disc = "Piping"
                elif any(k in lower for k in ["concrete", "civil", "foundation", "dhalai", "कंक्रीट", "नींव"]):
                    disc = "Civil"
                elif any(k in lower for k in ["cable", "electrical", "wire", "bijli", "तार", "केबल"]):
                    disc = "Electrical"
                elif any(k in lower for k in ["mechanical", "steel", "loha", "लोहा"]):
                    disc = "Mechanical"

            act_code = parsed.reported_activity_code

            tool_res = cls.query_historical_performance(
                db=db,
                project_id=project.id,
                query_type=q_type,
                discipline=disc,
                activity_code=act_code,
                unit=parsed.unit,
            )
            summary = tool_res.get("summary", "")
            evidence_count = len(tool_res.get("evidence", []))
            if evidence_count > 0:
                if resp_lang == "hi":
                    summary += f"\n\n[सत्यापित साक्ष्य: PostgreSQL में {evidence_count} आधिकारिक लेजर रिकॉर्ड]"
                elif resp_lang == "hinglish":
                    summary += f"\n\n[Verified Evidence: PostgreSQL mein {evidence_count} authoritative ledger record(s)]"
                else:
                    summary += f"\n\n[Verified Evidence: {evidence_count} authoritative ledger record(s) in PostgreSQL]"
            return summary

        # 4. General project summary
        act_count = db.query(Activity).filter(Activity.project_id == project.id).count()
        data_date_str = project.data_date.strftime('%Y-%m-%d') if project.data_date else 'Current'
        if resp_lang == "hi":
            return (
                f"Project {project.name} ({project.project_code}) में कुल {act_count} activities हैं। "
                f"वर्तमान data date {data_date_str} है। आप site progress report करने के लिए quantity और location बता सकते हैं "
                f"(जैसे 'आज F-204 में 35 cubic meter concrete डाला है')।"
            )
        elif resp_lang == "hinglish":
            return (
                f"Project {project.name} ({project.project_code}) mein total {act_count} activities hain. "
                f"Current data date {data_date_str} hai. Aap site progress report karne ke liye quantity aur location bata sakte hain "
                f"(e.g. 'Aaj F-204 mein 35 cubic meter concrete dala hai')."
            )
        else:
            return (
                f"Project {project.name} ({project.project_code}) has {act_count} activities. "
                f"Current data date is {data_date_str}. You can report progress by stating quantities and locations "
                f"(e.g., 'We poured 35 m3 for F-204 today')."
            )

    @classmethod
    def query_historical_performance(
        cls,
        db: Session,
        project_id: str,
        query_type: str = "PRODUCTIVITY",
        discipline: Optional[str] = None,
        activity_code: Optional[str] = None,
        unit: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Read-only Time Agent tool to query historical performance and productivity records.
        Strictly scopes to project_id.
        PostgreSQL is authoritative: returns deterministic calculation with evidence records.
        """
        from app.schemas.institutional_memory import HistoricalQueryRequest
        from app.services.institutional_memory_service import InstitutionalMemoryService

        req = HistoricalQueryRequest(
            query_type=query_type,
            discipline=discipline,
            activity_code=activity_code,
            unit=unit,
        )
        res = InstitutionalMemoryService.execute_query(db, project_id, req)
        return res.model_dump()

    @classmethod
    def _handle_bulk_progress(
        cls,
        db: Session,
        project: Project,
        conv: Conversation,
        parsed: ParsedConversationalIntent,
        raw_text: str,
        trigger_message_id: str,
        caller_id: str,
        active_event: Optional[ExecutionEvent] = None,
    ) -> MessageResponseDTO:
        """
        Governed bulk intent handler.
        The LLM NEVER determines activity membership.
        The backend queries the authoritative database for activities matching the extracted scope.
        """
        scope = parsed.bulk_scope or {}
        disc = scope.get("discipline") or parsed.discipline
        loc = scope.get("location") or parsed.location
        kw = scope.get("keyword")

        # Query authoritative active activities for this project
        query = db.query(Activity).filter(
            Activity.project_id == project.id,
            Activity.status != "COMPLETED",
        )

        all_acts = query.all()
        matched_acts: List[Activity] = []

        # Collect all targeted disciplines across multilingual tokens and parser scopes
        target_disciplines = set()
        if disc:
            target_disciplines.add(disc)
        if parsed.bulk_scope and isinstance(parsed.bulk_scope, dict):
            for d in parsed.bulk_scope.get("disciplines", []):
                target_disciplines.add(d)
            if parsed.bulk_scope.get("discipline"):
                target_disciplines.add(parsed.bulk_scope.get("discipline"))
        for d in ConversationalParser.extract_multilingual_disciplines(raw_text):
            target_disciplines.add(d)

        def matches_discipline_canon(activity_item: Activity, d_name: str) -> bool:
            d_l = d_name.lower()
            if activity_item.discipline and d_l in activity_item.discipline.lower():
                return True
            if d_l in activity_item.name.lower():
                return True
            if d_l in ("electrical", "industrial") and (activity_item.activity_code.startswith("ELE-") or "electric" in activity_item.name.lower() or "cable" in activity_item.name.lower()):
                return True
            if d_l == "civil" and (activity_item.activity_code.startswith("CIV-") or "civil" in activity_item.name.lower() or "concrete" in activity_item.name.lower()):
                return True
            if d_l == "piping" and (activity_item.activity_code.startswith("PIP-") or "pipe" in activity_item.name.lower()):
                return True
            if d_l == "structural" and (activity_item.activity_code.startswith("STR-") or "steel" in activity_item.name.lower() or "structure" in activity_item.name.lower()):
                return True
            if d_l == "mechanical" and (activity_item.activity_code.startswith("MEC-") or "mechanic" in activity_item.name.lower()):
                return True
            if d_l == "instrumentation" and (activity_item.activity_code.startswith("INS-") or "instrument" in activity_item.name.lower()):
                return True
            if d_l == "insulation" and (activity_item.activity_code.startswith("INSU-") or "insulat" in activity_item.name.lower()):
                return True
            if d_l == "painting" and (activity_item.activity_code.startswith("PAI-") or "paint" in activity_item.name.lower()):
                return True
            return False

        for act in all_acts:
            match = False
            # 1. Match any targeted discipline
            if target_disciplines:
                if any(matches_discipline_canon(act, td) for td in target_disciplines):
                    match = True

            # 2. Match keyword (including acoustic aliases like industrial -> electrical)
            if kw and not match:
                kw_low = kw.lower()
                if kw_low in act.name.lower() or (act.discipline and kw_low in act.discipline.lower()):
                    match = True
                elif any(w in kw_low for w in ["electric", "industrial"]) and (act.activity_code.startswith("ELE-") or "electric" in act.name.lower() or "cable" in act.name.lower()):
                    match = True
                elif "instrument" in kw_low and (act.activity_code.startswith("INS-") or "instrument" in act.name.lower()):
                    match = True
                elif "mechanic" in kw_low and (act.activity_code.startswith("MEC-") or "mechanic" in act.name.lower()):
                    match = True
                elif "civil" in kw_low and (act.activity_code.startswith("CIV-") or "civil" in act.name.lower() or "concrete" in act.name.lower()):
                    match = True
                elif "pipe" in kw_low and (act.activity_code.startswith("PIP-") or "pipe" in act.name.lower()):
                    match = True
                elif any(w in kw_low for w in ["steel", "structural"]) and (act.activity_code.startswith("STR-") or "steel" in act.name.lower()):
                    match = True
                elif "insulat" in kw_low and (act.activity_code.startswith("INSU-") or "insulat" in act.name.lower()):
                    match = True
                elif "paint" in kw_low and (act.activity_code.startswith("PAI-") or "paint" in act.name.lower()):
                    match = True

            # 3. Match location if specified
            if loc and act.location_code and loc.lower() in act.location_code.lower():
                match = True

            if match and act not in matched_acts:
                matched_acts.append(act)

        # Fallback 1: if specific discipline/keyword didn't find any, check tokens in raw_text
        if not matched_acts:
            clean_tokens = [
                w for w in re.findall(r"[A-Za-z0-9]+", raw_text.lower())
                if len(w) > 3 and w not in ["have", "completed", "finish", "finished", "update", "them", "these", "project", "work", "activities", "activity", "both"]
            ]
            if clean_tokens:
                for act in all_acts:
                    if any(tok in act.name.lower() or (act.discipline and tok in act.discipline.lower()) for tok in clean_tokens):
                        if act not in matched_acts:
                            matched_acts.append(act)
                for act in all_acts:
                    if any(tok in act.name.lower() or (act.discipline and tok in act.discipline.lower()) for tok in clean_tokens):
                        if act not in matched_acts:
                            matched_acts.append(act)

        # Fallback 2: If during clarification or active event, resolve to candidate activities of the active event
        if not matched_acts:
            active_ev = active_event
            if not active_ev and conv.active_event_id:
                active_ev = db.query(ExecutionEvent).filter(ExecutionEvent.id == conv.active_event_id).first()
            if active_ev:
                eval_res = MatchingService.evaluate_event_for_agent(db, active_ev)
                cands = eval_res.all_candidates
                if cands:
                    cand_slice = [c for c in cands if c.match_score >= 0.25]
                    if len(cand_slice) < 2 and len(cands) >= 2:
                        cand_slice = cands[:2]
                    elif not cand_slice:
                        cand_slice = cands[:4]
                    cand_ids = [c.activity_id for c in cand_slice]
                    acts_found = db.query(Activity).filter(Activity.id.in_(cand_ids)).all()
                    act_map = {a.id: a for a in acts_found}
                    for cid in cand_ids:
                        if cid in act_map and act_map[cid] not in matched_acts:
                            matched_acts.append(act_map[cid])

        target_pct = parsed.override_percent if parsed.override_percent is not None else 100.0
        if target_disciplines:
            effective_disc = " & ".join(sorted(target_disciplines))
        else:
            effective_disc = disc
        if not effective_disc and kw and any(w in kw.lower() for w in ["electric", "industrial"]):
            effective_disc = "Electrical"
        scope_desc = f"{effective_disc} " if effective_disc else (f"{kw} " if kw else "")
        scope_desc = f"{scope_desc}activities".strip()
        if scope_desc == "activities":
            scope_desc = "matching candidate activities"

        if len(matched_acts) == 0:
            resp_lang = cls._resolve_template_language(conv)
            if resp_lang == "hi":
                reply_text = (
                    f"मुझे project {project.name} में '{raw_text}' से संबंधित कोई सक्रिय activities नहीं मिलीं। "
                    f"क्या आप कृपया बता सकते हैं कि यह प्रगति किस activity code या work package की है?"
                )
            elif resp_lang == "hinglish":
                reply_text = (
                    f"Mujhe project {project.name} mein '{raw_text}' se match hone wali koi active activities nahi mili. "
                    f"Kripya batayein ki yeh progress kis activity code ya work package ki hai?"
                )
            else:
                reply_text = (
                    f"I could not find any active activities matching '{raw_text}' in project {project.name}. "
                    f"Could you please specify which activity code or work package this progress belongs to?"
                )
            return cls._save_and_return_agent_response(db=db, conv=conv, reply_text=reply_text, action_card=None)

        if len(matched_acts) == 1:
            # Single activity match -> route to single proposal
            target_act = matched_acts[0]
            prev_pct = target_act.percent_complete or 0.0
            event = ExecutionEvent(
                id=f"ev-{uuid.uuid4().hex[:8]}",
                project_id=project.id,
                source_type="CONVERSATION",
                conversation_id=conv.id,
                message_id=trigger_message_id,
                verbatim_excerpt=raw_text,
                description=parsed.description or raw_text,
                reported_activity_code=target_act.activity_code,
                execution_date=project.data_date or datetime.now(timezone.utc).replace(tzinfo=None),
                status_reported=parsed.status_reported or "COMPLETED",
                extraction_confidence=1.0,
                status="DRAFT",
            )
            db.add(event)
            db.flush()
            conv.active_event_id = event.id
            proposal = cls.stage_proposal(
                db=db,
                conversation=conv,
                event=event,
                activity=target_act,
                proposed_percent=target_pct,
                proposed_status="COMPLETED" if target_pct == 100.0 else "IN_PROGRESS",
                quantity_semantics="INCREMENTAL",
                override_percent=target_pct,
            )
            card = ActionCardDTO(
                type="PROPOSAL_CONFIRMATION",
                proposal_id=proposal.id,
                event_id=event.id,
                activity_id=target_act.id,
                activity_code=target_act.activity_code,
                activity_name=target_act.name,
                current_percent=prev_pct,
                proposed_percent=target_pct,
                execution_date=event.execution_date.strftime("%Y-%m-%d"),
            )
            resp_lang = cls._resolve_template_language(conv)
            if resp_lang == "hi":
                reply_text = (
                    f"मैंने इसे {target_act.activity_code} ({target_act.name}) से match किया है। "
                    f"यह progress को {prev_pct}% से {target_pct}% तक बढ़ाएगा। "
                    f"Authoritative schedule में इस update को apply करने के लिए कृपया confirm करें।"
                )
                card.confirm_label = "अपडेट की पुष्टि करें"
                card.reject_label = "रद्द करें"
                card.review_label = "समीक्षा करें"
            elif resp_lang == "hinglish":
                reply_text = (
                    f"Maine is update ko {target_act.activity_code} ({target_act.name}) se match kiya hai. "
                    f"Yeh progress ko {prev_pct}% se {target_pct}% tak advance karega. "
                    f"Authoritative schedule mein apply karne ke liye please confirm karein."
                )
                card.confirm_label = "Update Confirm Karein"
                card.reject_label = "Reject Karein"
                card.review_label = "Details Review Karein"
            else:
                reply_text = (
                    f"I've matched this to {target_act.activity_code} ({target_act.name}). "
                    f"This will advance progress from {prev_pct}% to {target_pct}%. "
                    f"Please confirm to apply this update to the authoritative schedule."
                )
                card.confirm_label = "Confirm Update"
                card.reject_label = "Reject"
                card.review_label = "Review Details & Audit Safety"
            return cls._save_and_return_agent_response(db=db, conv=conv, reply_text=reply_text, action_card=card)

        resp_lang = cls._resolve_template_language(conv)

        if 2 <= len(matched_acts) <= 6:
            bulk_act_dtos = [
                {
                    "activity_id": act.id,
                    "activity_code": act.activity_code,
                    "activity_name": act.name,
                    "current_percent": act.percent_complete or 0.0,
                    "proposed_percent": target_pct,
                }
                for act in matched_acts
            ]

            confirm_bulk_label = (
                f"सभी {len(matched_acts)} को {target_pct}% पर अपडेट करें" if resp_lang == "hi"
                else (f"Sabhi {len(matched_acts)} ko {target_pct}% par update karein" if resp_lang == "hinglish"
                else f"Update All {len(matched_acts)} to {target_pct}%")
            )
            cancel_label = "रद्द करें" if resp_lang == "hi" else "Cancel"

            card = ActionCardDTO(
                type="BULK_SCOPE_PROPOSAL",
                proposal_status="PENDING",
                bulk_activities=bulk_act_dtos,
                bulk_count=len(matched_acts),
                scope_label=scope_desc.title(),
                proposed_status="COMPLETED" if target_pct == 100.0 else "IN_PROGRESS",
                target_percent=target_pct,
                confirm_label="अपडेट की पुष्टि करें" if resp_lang == "hi" else ("Update Confirm Karein" if resp_lang == "hinglish" else "Confirm Update"),
                reject_label=cancel_label,
                options=[
                    {
                        "label": confirm_bulk_label,
                        "value": "CONFIRM_ALL_BULK",
                    },
                    *[
                        {
                            "label": f"{a.activity_code} - {a.name}",
                            "value": a.activity_code,
                        }
                        for a in matched_acts
                    ],
                    {
                        "label": cancel_label,
                        "value": "CANCEL",
                    },
                ],
            )
            if resp_lang == "hi":
                reply_text = (
                    f"मुझे इस project में {len(matched_acts)} {scope_desc} मिलीं। "
                    f"क्या आप इन सभी {len(matched_acts)} को {target_pct}% पर update करना चाहते हैं, या कोई specific activity चुनना चाहते हैं?"
                )
            elif resp_lang == "hinglish":
                reply_text = (
                    f"Mujhe is project mein {len(matched_acts)} {scope_desc} mili hain. "
                    f"Kya aap in sabhi {len(matched_acts)} activities ko {target_pct}% par update karna chahte hain, ya koi specific activity select karenge?"
                )
            else:
                reply_text = (
                    f"I found {len(matched_acts)} {scope_desc} in this project. "
                    f"Do you want to update all {len(matched_acts)} to {target_pct}%, or select a specific activity?"
                )
            conv.status = "WAITING_FOR_BULK_UPDATE_CONFIRMATION"
            return cls._save_and_return_agent_response(
                db=db,
                conv=conv,
                reply_text=reply_text,
                action_card=card,
            )

        # More than 6 activities -> summarize + scoped review
        bulk_act_dtos = [
            {
                "activity_id": act.id,
                "activity_code": act.activity_code,
                "activity_name": act.name,
                "current_percent": act.percent_complete or 0.0,
                "proposed_percent": target_pct,
            }
            for act in matched_acts[:5]
        ]
        review_table_label = (
            f"शेड्यूल तालिका में सभी {len(matched_acts)} activities की समीक्षा करें" if resp_lang == "hi"
            else (f"Schedule Table mein sabhi {len(matched_acts)} activities review karein" if resp_lang == "hinglish"
            else f"Review all {len(matched_acts)} activities in schedule table")
        )
        cancel_label = "रद्द करें" if resp_lang == "hi" else "Cancel"

        card = ActionCardDTO(
            type="BULK_SCOPE_PROPOSAL",
            proposal_status="PENDING",
            bulk_activities=bulk_act_dtos,
            bulk_count=len(matched_acts),
            scope_label=scope_desc.title(),
            proposed_status="COMPLETED" if target_pct == 100.0 else "IN_PROGRESS",
            target_percent=target_pct,
            reject_label=cancel_label,
            options=[
                {
                    "label": review_table_label,
                    "value": "REVIEW_IN_TABLE",
                },
                {
                    "label": cancel_label,
                    "value": "CANCEL",
                },
            ],
        )
        if resp_lang == "hi":
            reply_text = (
                f"मुझे project schedule में {len(matched_acts)} {scope_desc} मिलीं। "
                f"क्योंकि यह एक बड़ा work package है, इन सभी को एक साथ update करने से पहले Activities Table में review करना सुरक्षित है, "
                f"या आप scope को सीमित करने के लिए specific area बता सकते हैं।"
            )
        elif resp_lang == "hinglish":
            reply_text = (
                f"Mujhe project schedule mein {len(matched_acts)} {scope_desc} mili hain. "
                f"Yeh ek broad work package hai, isliye bulk update karne se pehle Activities Table mein review karna behtar hoga, "
                f"ya aap specific area specify kar sakte hain."
            )
        else:
            reply_text = (
                f"I found {len(matched_acts)} {scope_desc} in this project schedule. "
                f"Because this is a broad work package, updating all of them at once requires review in the Activities Table, "
                f"or you can specify a specific area or block to narrow down the scope."
            )
        conv.status = "WAITING_FOR_BULK_UPDATE_CONFIRMATION"
        return cls._save_and_return_agent_response(
            db=db,
            conv=conv,
            reply_text=reply_text,
            action_card=card,
        )

    @classmethod
    def _get_or_init_update_context(cls, event: Optional[ExecutionEvent]) -> Dict[str, Any]:
        default_ctx = {
            "status": "WAITING_FOR_ACTIVITY_CLARIFICATION",
            "activity_id": None,
            "activity_code": None,
            "activity_name": None,
            "discipline": None,
            "equipment": None,
            "location": None,
            "contractor": None,
            "description": None,
            "override_percent": None,
            "quantity": None,
            "unit": None,
            "quantity_semantics": None,
            "last_question_type": None,
            "clarification_turns": 0,
        }
        if not event or not event.match_metadata:
            return default_ctx
        try:
            meta = json.loads(event.match_metadata) if isinstance(event.match_metadata, str) else event.match_metadata
            ctx = meta.get("update_context")
            if isinstance(ctx, dict):
                merged = dict(default_ctx)
                merged.update(ctx)
                return merged
        except Exception:
            pass
        return default_ctx

    @classmethod
    def _save_update_context(cls, event: ExecutionEvent, ctx: Dict[str, Any]) -> None:
        try:
            meta = json.loads(event.match_metadata) if event.match_metadata else {}
        except Exception:
            meta = {}
        meta["update_context"] = ctx
        event.match_metadata = json.dumps(meta)

    @classmethod
    def _merge_update_context(
        cls,
        ctx: Dict[str, Any],
        parsed: ParsedConversationalIntent,
        raw_text: str,
        project_acts: List[Activity],
    ) -> Dict[str, Any]:
        # 1. Activity Code
        if parsed.reported_activity_code:
            ctx["activity_code"] = parsed.reported_activity_code
        elif not ctx.get("activity_code"):
            extracted_code = ConversationalParser.extract_activity_code(raw_text)
            if extracted_code:
                ctx["activity_code"] = extracted_code
            else:
                raw_clean_up = raw_text.strip().upper()
                for a in project_acts:
                    code_up = a.activity_code.upper()
                    if code_up in raw_clean_up or code_up.replace("-", "") in raw_clean_up or code_up.replace("-", " ") in raw_clean_up:
                        ctx["activity_code"] = a.activity_code
                        break

        # 2. Discipline
        if parsed.discipline:
            ctx["discipline"] = parsed.discipline
        elif not ctx.get("discipline"):
            for disc in ["Mechanical", "Civil", "Electrical", "Piping", "Structural", "Instrumentation", "HVAC"]:
                if disc.lower() in raw_text.lower():
                    ctx["discipline"] = disc
                    break

        # 3. Equipment / Asset
        if parsed.asset:
            ctx["equipment"] = parsed.asset
        elif not ctx.get("equipment"):
            equip_m = re.search(
                r"\b(pump|compressor|generator|turbine|boiler|tank|vessel|chiller|transformer|motor|valve|conveyor|पंप|कंप्रेसर|मोटर)\b",
                raw_text,
                re.IGNORECASE,
            )
            if equip_m:
                ctx["equipment"] = equip_m.group(1).title()

        # 4. Location
        if parsed.location:
            ctx["location"] = parsed.location
        elif not ctx.get("location"):
            loc_m = re.search(
                r"\b(Unit\s+\d+|Unit-\d+|Area\s+[A-Za-z0-9]+|Block\s+\d+|Pier\s+\d+|Foundation\s+(?:[A-Za-z]?\d+[A-Za-z0-9-]*|[A-Za-z]\b)|Level\s+\d+|F-\d+|यूनिट\s+\d+)\b",
                raw_text,
                re.IGNORECASE,
            )
            if loc_m:
                ctx["location"] = loc_m.group(1).title()

        # 5. Percentage / Override progress
        if parsed.override_percent is not None:
            ctx["override_percent"] = parsed.override_percent
        else:
            pct_m = re.search(
                r"\b(?:set\s+(?:progress\s+)?to\s+|to\s+|progress\s+to\s+|progress\s+)?(\d+(?:\.\d+)?)\s*(?:%|percent)\b",
                raw_text,
                re.IGNORECASE,
            )
            if not pct_m:
                pct_m = re.search(r"\bto\s+(\d+(?:\.\d+)?)\b", raw_text, re.IGNORECASE)
            if pct_m:
                try:
                    val = float(pct_m.group(1))
                    if 0.0 <= val <= 100.0:
                        ctx["override_percent"] = val
                except ValueError:
                    pass

        # 6. Quantity and semantics
        if parsed.quantity is not None:
            loc_str = ctx.get("location") or ""
            is_loc_digit = bool(loc_str and re.search(rf"\b{re.escape(str(int(parsed.quantity)))}\b", loc_str))
            if not is_loc_digit:
                ctx["quantity"] = parsed.quantity
        if parsed.unit:
            ctx["unit"] = parsed.unit
        if parsed.quantity_semantics and parsed.quantity_semantics != "UNKNOWN":
            ctx["quantity_semantics"] = parsed.quantity_semantics

        # 7. Description accumulation
        clean_raw = raw_text.strip()
        if not ctx.get("description"):
            ctx["description"] = clean_raw
        else:
            prev_desc = ctx["description"]
            if clean_raw.lower() not in prev_desc.lower() and len(clean_raw) > 2:
                ctx["description"] = f"{prev_desc} {clean_raw}"

        return ctx

    @classmethod
    def _find_distinguishing_question(
        cls,
        candidates: List[MatchCandidateDTO],
        db: Session,
        project: Project,
        resp_lang: str,
        ctx: Dict[str, Any],
    ) -> str:
        # Check distinguishing locations between top candidates
        cand_locations = []
        for c in candidates[:4]:
            act = db.query(Activity).filter(Activity.id == c.activity_id).first()
            if act:
                loc = act.location_code
                if not loc:
                    m = re.search(
                        r"\b(Unit\s+\d+|Unit-\d+|Area\s+[A-Za-z0-9]+|Block\s+\d+|Pier\s+\d+|Level\s+\d+|F-\d+|Foundation\s+(?:[A-Za-z]?\d+[A-Za-z0-9-]*|[A-Za-z]\b))\b",
                        act.name,
                        re.I,
                    )
                    if m:
                        loc = m.group(1).title()
                if loc and loc not in cand_locations:
                    cand_locations.append(loc)

        # 1. If user provided broad discipline (e.g. Mechanical) but not equipment or location
        if ctx.get("discipline") and not ctx.get("equipment") and not ctx.get("location"):
            disc = ctx["discipline"]
            ctx["last_question_type"] = "discipline_specific"
            if resp_lang == "hi":
                return f"कृपया बताइए कि कौन-सी {disc.lower()} activity update करनी है। आप activity का नाम, equipment, location या activity ID बता सकते हैं।"
            elif resp_lang == "hinglish":
                return f"Please specify karein ki kaun-si {disc.lower()} activity update karni hai. Aap activity name, equipment, location ya activity ID bata sakte hain."
            else:
                return f"Please specify which {disc.lower()} activity you want to update. You can provide the activity name, equipment, location, or activity ID."

        # 2. If user provided equipment (e.g. Pump) and has not specified location
        if ctx.get("equipment") and not ctx.get("location"):
            eq = ctx["equipment"]
            if ctx.get("last_question_type") == "equipment_location" and len(cand_locations) >= 2:
                l1, l2 = cand_locations[0], cand_locations[1]
                ctx["last_question_type"] = "distinguishing_location"
                if resp_lang == "hi":
                    return f"कौन-सी location सही है: {l1} या {l2}?"
                elif resp_lang == "hinglish":
                    return f"Kaun si location correct hai: {l1} ya {l2}?"
                else:
                    return f"Which location is correct: {l1} or {l2}?"
            else:
                ctx["last_question_type"] = "equipment_location"
                if resp_lang == "hi":
                    return f"आप किस {eq} activity की बात कर रहे हैं? कृपया location, equipment का नाम, या activity ID बताएं।"
                elif resp_lang == "hinglish":
                    return f"Aap kis {eq} activity ki baat kar rahe hain? Please location, equipment name, ya activity ID batayein."
                else:
                    return f"Which {eq.lower()} installation do you mean? Please provide the location, equipment name, or activity ID."

        # 4. If user provided location (e.g. Unit 4) but not activity
        if ctx.get("location") and not ctx.get("equipment") and not ctx.get("discipline"):
            loc = ctx["location"]
            ctx["last_question_type"] = "location_specific"
            if resp_lang == "hi":
                return f"आप {loc} पर कौन-सी activity update करना चाहते हैं? आप activity का नाम, equipment, discipline, या activity ID बता सकते हैं।"
            elif resp_lang == "hinglish":
                return f"Aap {loc} par kaun-si activity update karna chahte hain? Aap activity name, equipment, discipline, ya activity ID bata sakte hain."
            else:
                return f"Which activity at {loc} do you want to update? You can provide the activity name, equipment, discipline, or activity ID."

        # 5. General candidate distinguishing locations
        if len(cand_locations) >= 2:
            l1, l2 = cand_locations[0], cand_locations[1]
            ctx["last_question_type"] = "distinguishing_location"
            if resp_lang == "hi":
                return f"कौन-सी location सही है: {l1} या {l2}?"
            elif resp_lang == "hinglish":
                return f"Kaun si location correct hai: {l1} ya {l2}?"
            else:
                return f"Which location is correct: {l1} or {l2}?"

        # If 2 candidates with distinct names
        if len(candidates) >= 2:
            c1, c2 = candidates[0], candidates[1]
            ctx["last_question_type"] = "candidates_ab"
            if resp_lang == "hi":
                return f"क्या यह activity {c1.activity_code} ({c1.activity_name}) के लिए थी या {c2.activity_code} ({c2.activity_name}) के लिए? कृपया बताएं।"
            elif resp_lang == "hinglish":
                return f"Kya yeh activity {c1.activity_code} ({c1.activity_name}) ke liye thi ya {c2.activity_code} ({c2.activity_name}) ke liye? Kripya batayein."
            else:
                return f"Did this work apply to {c1.activity_code} ({c1.activity_name}) or {c2.activity_code} ({c2.activity_name})?"

        ctx["last_question_type"] = "general_clarification"
        if resp_lang == "hi":
            return "कृपया activity का नाम, activity ID, location या equipment बताएं।"
        elif resp_lang == "hinglish":
            return "Please activity name, activity ID, location ya equipment batayein."
        else:
            return "Please provide the activity name, activity ID, location, or equipment."

    @classmethod
    def _handle_progress_or_clarification(
        cls,
        db: Session,
        project: Project,
        conv: Conversation,
        parsed: ParsedConversationalIntent,
        raw_text: str,
        trigger_message_id: str,
        caller_id: str,
    ) -> MessageResponseDTO:
        event = None
        resp_lang = cls._resolve_template_language(conv)

        # Check if we are enriching an in-flight draft event
        if conv.active_event_id:
            event = (
                db.query(ExecutionEvent)
                .filter(
                    ExecutionEvent.id == conv.active_event_id,
                    ExecutionEvent.project_id == project.id,
                )
                .first()
            )

        ctx = cls._get_or_init_update_context(event)
        clean_ans = raw_text.strip().rstrip(".,;:!?").lower()

        # 1. Handle "None of these"
        if clean_ans in ["none_of_these", "none of these", "neither", "none", "no"]:
            if conv.clarification_turns >= 2:
                if event:
                    event.status = "IN_REVIEW"
                conv.active_event_id = None
                conv.status = "ACTIVE"
                if resp_lang == "hi":
                    reply_text = "Clarification के बाद भी activity resolve नहीं हो सकी। मैंने इसे Lead Planner Review Queue में भेज दिया है।"
                elif resp_lang == "hinglish":
                    reply_text = "Clarification ke baad bhi activity resolve nahi hui. Maine is report ko Lead Planner Review Queue mein route kar diya hai."
                else:
                    reply_text = "I could not resolve this report after clarification. I have routed it to the Lead Planner Review Queue for manual verification."
                return cls._save_and_return_agent_response(db=db, conv=conv, reply_text=reply_text, action_card=None)
            else:
                conv.clarification_turns += 1
                conv.status = "WAITING_FOR_USER"
                if resp_lang == "hi":
                    reply_text = "समझ गया। क्या आप exact activity code (जैसे ELE-1001), location, या work package बता सकते हैं?"
                elif resp_lang == "hinglish":
                    reply_text = "Samajh gaya. Kya aap exact activity code (e.g. ELE-1001), location, ya work package bata sakte hain?"
                else:
                    reply_text = "Understood. Could you please specify the exact activity code (e.g., ELE-1001), location, or work package name?"
                return cls._save_and_return_agent_response(db=db, conv=conv, reply_text=reply_text, action_card=None)

        # 2. Handle "All of them" / bulk intent during clarification turn
        if parsed.is_bulk or any(clean_ans == term or clean_ans.startswith(term) for term in [
            "all of them", "all", "both", "both of them", "all of these", "update all", "update all of them", "all activities"
        ]):
            if event and not parsed.discipline and event.discipline:
                parsed.discipline = event.discipline
            if not parsed.bulk_scope:
                parsed.bulk_scope = {}
            if event and not parsed.bulk_scope.get("discipline") and event.discipline:
                parsed.bulk_scope["discipline"] = event.discipline
            if event and not parsed.bulk_scope.get("keyword") and event.description:
                parsed.bulk_scope["keyword"] = event.description

            return cls._handle_bulk_progress(
                db=db,
                project=project,
                conv=conv,
                parsed=parsed,
                raw_text=(event.verbatim_excerpt if event else raw_text),
                trigger_message_id=(event.message_id if event else trigger_message_id),
                caller_id=caller_id,
                active_event=event,
            )

        # 3. Check if user is in WAITING_FOR_UPDATE_VALUE state (activity was already identified)
        if ctx.get("status") == "WAITING_FOR_UPDATE_VALUE" and (conv.active_activity_id or ctx.get("activity_id")):
            target_act_id = conv.active_activity_id or ctx.get("activity_id")
            target_activity = db.query(Activity).filter(Activity.id == target_act_id).first()
            if target_activity:
                # Check for progress percentage or quantity in this message
                pct_m = re.search(
                    r"\b(?:set\s+(?:progress\s+)?to\s+|to\s+|progress\s+to\s+|progress\s+)?(\d+(?:\.\d+)?)\s*(?:%|percent)\b",
                    raw_text,
                    re.IGNORECASE,
                )
                if not pct_m:
                    pct_m = re.search(r"\bto\s+(\d+(?:\.\d+)?)\b", raw_text, re.IGNORECASE)
                if not pct_m and parsed.override_percent is not None:
                    pct_m = parsed.override_percent

                val = None
                if pct_m is not None:
                    try:
                        val = float(pct_m.group(1)) if hasattr(pct_m, "group") else float(pct_m)
                    except Exception:
                        pass

                if val is not None or parsed.quantity is not None or event.quantity is not None:
                    prev_pct = target_activity.percent_complete or 0.0
                    proposed_pct = val if val is not None else prev_pct
                    incremental_qty = parsed.quantity or (event.quantity if event else None)

                    if val is None and incremental_qty is not None and target_activity.planned_quantity and target_activity.planned_quantity > 0:
                        q_planned = float(target_activity.planned_quantity)
                        qty_ratio = (float(incremental_qty) / q_planned) * 100.0
                        proposed_pct = min(100.0, prev_pct + qty_ratio)

                    proposed_pct = round(proposed_pct, 2)
                    ctx["override_percent"] = proposed_pct
                    ctx["status"] = "PROPOSAL_PENDING"

                    if not event:
                        event = ExecutionEvent(
                            id=f"ev-{uuid.uuid4().hex[:8]}",
                            project_id=project.id,
                            artifact_id=None,
                            source_type="CONVERSATION",
                            conversation_id=conv.id,
                            message_id=trigger_message_id,
                            verbatim_excerpt=raw_text,
                            description=f"{target_activity.name} {raw_text}",
                            reported_activity_code=target_activity.activity_code,
                            execution_date=project.data_date or datetime.now(timezone.utc).replace(tzinfo=None),
                            status_reported="IN_PROGRESS",
                            status="DRAFT",
                        )
                        db.add(event)
                        conv.active_event_id = event.id

                    cls._save_update_context(event, ctx)
                    db.flush()

                    proposal = cls.stage_proposal(
                        db=db,
                        conversation=conv,
                        event=event,
                        activity=target_activity,
                        proposed_percent=proposed_pct,
                        proposed_status=target_activity.status,
                        quantity_semantics=ctx.get("quantity_semantics") or "INCREMENTAL",
                        incremental_quantity=incremental_qty,
                        override_percent=proposed_pct,
                    )

                    card = ActionCardDTO(
                        type="PROPOSAL_CONFIRMATION",
                        proposal_id=proposal.id,
                        event_id=event.id,
                        activity_id=target_activity.id,
                        activity_code=target_activity.activity_code,
                        activity_name=target_activity.name,
                        current_percent=prev_pct,
                        proposed_percent=proposed_pct,
                        incremental_quantity=incremental_qty,
                        unit=event.unit,
                        execution_date=event.execution_date.strftime("%Y-%m-%d"),
                    )

                    if resp_lang == "hi":
                        reply_text = (
                            f"मुझे {target_activity.activity_code} ({target_activity.name}) मिल गई है। "
                            f"प्रस्तावित प्रगति अपडेट {proposed_pct}% है। कृपया लागू करने से पहले पुष्टि करें।"
                        )
                        card.confirm_label = "अपडेट की पुष्टि करें"
                        card.reject_label = "रद्द करें"
                        card.review_label = "समीक्षा करें"
                    elif resp_lang == "hinglish":
                        reply_text = (
                            f"Mujhe {target_activity.activity_code} ({target_activity.name}) mil gayi hai. "
                            f"Proposed progress update {proposed_pct}% hai. Please apply karne se pehle confirm karein."
                        )
                        card.confirm_label = "Update Confirm Karein"
                        card.reject_label = "Reject Karein"
                        card.review_label = "Details Review Karein"
                    else:
                        reply_text = (
                            f"I found {target_activity.activity_code} ({target_activity.name}). "
                            f"The proposed progress update is {proposed_pct}%. Please confirm before I apply it."
                        )
                        card.confirm_label = "Confirm Update"
                        card.reject_label = "Reject"
                        card.review_label = "Review Details & Audit Safety"

                    return cls._save_and_return_agent_response(
                        db=db,
                        conv=conv,
                        reply_text=reply_text,
                        action_card=card,
                    )

        # 4. Progress or Clarification context merging
        project_acts = db.query(Activity).filter(Activity.project_id == project.id).all()
        ctx = cls._merge_update_context(ctx, parsed, raw_text, project_acts)

        if event:
            logger.info(f"Enriching existing draft ExecutionEvent {event.id} on turn {conv.clarification_turns}")
            if ctx.get("location"):
                event.location = ctx["location"]
            if ctx.get("quantity") is not None:
                event.quantity = ctx["quantity"]
            if ctx.get("unit"):
                event.unit = ctx["unit"]
            if ctx.get("discipline"):
                event.discipline = ctx["discipline"]
            if ctx.get("equipment"):
                event.asset = ctx["equipment"]
            if ctx.get("activity_code"):
                event.reported_activity_code = ctx["activity_code"]

            if raw_text:
                event.description = f"{event.description} {raw_text}".strip()
                event.verbatim_excerpt = f"{event.verbatim_excerpt} {raw_text}".strip()

            resolved_date = ConversationalParser.resolve_date(parsed.execution_date, project.data_date)
            if resolved_date:
                event.execution_date = resolved_date
                if event.extraction_notes:
                    event.extraction_notes = event.extraction_notes.replace("[DATE_NEEDED]", "").strip()

            notes = event.extraction_notes or ""
            event.extraction_notes = f"{notes}\n[Clarification Turn {conv.clarification_turns}]: {raw_text}".strip()
            cls._save_update_context(event, ctx)
            db.flush()
        else:
            resolved_date = ConversationalParser.resolve_date(parsed.execution_date, project.data_date)
            if resolved_date is None and (
                parsed.status_reported == "COMPLETED"
                or ctx.get("override_percent") is not None
                or parsed.intent in ("PROGRESS_UPDATE_REQUEST", "BULK_PROGRESS_REPORT")
                or ctx.get("activity_code")
            ):
                resolved_date = project.data_date or datetime.now(timezone.utc).replace(tzinfo=None)
                date_needed = False
            else:
                date_needed = resolved_date is None
            event_date = resolved_date or project.data_date or datetime.now(timezone.utc).replace(tzinfo=None)
            initial_notes = "[DATE_NEEDED]" if date_needed else ""

            event = ExecutionEvent(
                id=f"ev-{uuid.uuid4().hex[:8]}",
                project_id=project.id,
                artifact_id=None,
                source_type="CONVERSATION",
                conversation_id=conv.id,
                message_id=trigger_message_id,
                verbatim_excerpt=raw_text,
                description=ctx.get("description") or raw_text,
                reported_activity_code=ctx.get("activity_code"),
                execution_date=event_date,
                status_reported=parsed.status_reported,
                quantity=ctx.get("quantity"),
                unit=ctx.get("unit"),
                location=ctx.get("location"),
                discipline=ctx.get("discipline"),
                contractor=ctx.get("contractor"),
                asset=ctx.get("equipment"),
                wbs_hint=ctx.get("wbs_hint"),
                extraction_confidence=parsed.confidence,
                extraction_notes=initial_notes,
                status="DRAFT",
            )
            db.add(event)
            db.flush()
            conv.active_event_id = event.id
            cls._save_update_context(event, ctx)
            db.flush()

        # 5. Fast check for Generic update request without identifying details (Turn 1)
        is_generic_update_start = (
            conv.clarification_turns == 0
            and not ctx.get("activity_code")
            and not ctx.get("discipline")
            and not ctx.get("equipment")
            and not ctx.get("location")
            and ctx.get("override_percent") is None
            and ctx.get("quantity") is None
            and (
                any(phrase in raw_text.lower() for phrase in [
                    "want to update an activity", "want to update activity",
                    "update an activity", "update activity", "update schedule",
                    "activity update karni hai", "activity update karna hai",
                    "ek activity update", "activity update",
                ]) or any(phrase in raw_text for phrase in [
                    "activity update करनी है", "activity update करना है", "एक्टिविटी अपडेट"
                ])
            )
        )
        if is_generic_update_start:
            conv.clarification_turns = 0
            conv.status = "WAITING_FOR_USER"
            ctx["status"] = "WAITING_FOR_ACTIVITY_CLARIFICATION"
            ctx["last_question_type"] = "initial_activity"
            ctx["clarification_turns"] = 0
            cls._save_update_context(event, ctx)

            if resp_lang == "hi":
                reply = "ज़रूर। आप किस प्रकार की activity update करना चाहते हैं?\nआप activity का प्रकार, नाम, ID, location, equipment या discipline बता सकते हैं।"
            elif resp_lang == "hinglish":
                reply = "Sure. Aap kis type ki activity update karna chahte hain?\nAap activity type, name, ID, location, equipment, ya discipline bata sakte hain."
            else:
                reply = "Sure. Which activity would you like to update?\nYou can provide the activity type, name, ID, location, equipment, discipline, or another identifying detail."

            return cls._save_and_return_agent_response(db=db, conv=conv, reply_text=reply, action_card=None)

        # 6. Deterministic evaluation with accumulated context
        eval_result = MatchingService.evaluate_event_for_agent(db, event)

        # Branch on Matching Confidence Routing
        if eval_result.route == "AUTO_LINK" and eval_result.selected_candidate:
            top_cand = eval_result.selected_candidate
            target_activity = db.query(Activity).filter(Activity.id == top_cand.activity_id).first()
            conv.active_activity_id = target_activity.id
            ctx["activity_id"] = target_activity.id
            ctx["activity_code"] = target_activity.activity_code
            ctx["activity_name"] = target_activity.name
            ctx["status"] = "ACTIVITY_IDENTIFIED"

            # Check if execution date was missing and not resolved
            date_missing = "[DATE_NEEDED]" in (event.extraction_notes or "")
            if date_missing:
                conv.clarification_turns += 1
                conv.status = "WAITING_FOR_USER"
                data_date_str = project.data_date.strftime("%Y-%m-%d") if project.data_date else "today"
                if resp_lang == "hi":
                    question = f"{target_activity.activity_code} ({target_activity.name}) से match हुआ। यह कार्य किस तारीख को किया गया था? (Project data date: {data_date_str})"
                elif resp_lang == "hinglish":
                    question = f"{target_activity.activity_code} ({target_activity.name}) se match hua. Yeh work kis date ko perform kiya gaya tha? (Project data date: {data_date_str})"
                else:
                    question = f"Matched to {target_activity.activity_code} ({target_activity.name}). On what date was this work performed? (Project data date is {data_date_str})"

                cls._save_update_context(event, ctx)
                return cls._save_and_return_agent_response(
                    db=db,
                    conv=conv,
                    reply_text=question,
                    action_card=None,
                )

            # Check quantity semantics clarification if ambiguous
            if ctx.get("quantity") is not None and parsed.quantity_semantics == "UNKNOWN" and conv.clarification_turns == 0:
                conv.clarification_turns += 1
                conv.status = "WAITING_FOR_USER"
                if resp_lang == "hi":
                    question = (
                        f"मैंने इसे {target_activity.activity_code} ({target_activity.name}) से match किया है। "
                        f"क्या {ctx.get('quantity')} {parsed.unit or ''} आज का incremental कार्य है, "
                        f"या अब तक का cumulative total?"
                    )
                elif resp_lang == "hinglish":
                    question = (
                        f"Maine is update ko {target_activity.activity_code} ({target_activity.name}) se match kiya hai. "
                        f"Kya {ctx.get('quantity')} {parsed.unit or ''} aaj ka incremental work hai, "
                        f"ya ab tak ka cumulative total?"
                    )
                else:
                    question = (
                        f"I matched this to {target_activity.activity_code} ({target_activity.name}). "
                        f"Is {ctx.get('quantity')} {parsed.unit or ''} the incremental amount completed today, "
                        f"or the cumulative total completed to date?"
                    )

                cls._save_update_context(event, ctx)
                return cls._save_and_return_agent_response(
                    db=db,
                    conv=conv,
                    reply_text=question,
                    action_card=None,
                )

            # Check if user provided an update value (percent, quantity, completion)
            has_update_value = (
                ctx.get("override_percent") is not None
                or (ctx.get("quantity") is not None and target_activity.planned_quantity and target_activity.planned_quantity > 0)
                or event.status_reported == "COMPLETED"
            )

            if not has_update_value:
                # Ask what update the user wants to make without assuming or guessing
                ctx["status"] = "WAITING_FOR_UPDATE_VALUE"
                conv.status = "WAITING_FOR_USER"
                cls._save_update_context(event, ctx)

                if resp_lang == "hi":
                    reply_text = f"मैंने {target_activity.activity_code} ({target_activity.name}) की पहचान कर ली है।\nआप इस activity के लिए क्या update करना चाहते हैं?"
                elif resp_lang == "hinglish":
                    reply_text = f"Maine {target_activity.activity_code} ({target_activity.name}) identify kar li hai.\nAap is activity ke liye kya update karna chahte hain?"
                else:
                    reply_text = f"I identified the {target_activity.name} activity ({target_activity.activity_code}). What update would you like to make?"

                return cls._save_and_return_agent_response(
                    db=db,
                    conv=conv,
                    reply_text=reply_text,
                    action_card=None,
                )

            # High confidence match ready for proposal staging!
            prev_pct = target_activity.percent_complete or 0.0
            proposed_pct = prev_pct
            incremental_qty = event.quantity

            if ctx.get("override_percent") is not None:
                proposed_pct = ctx["override_percent"]
            elif event.quantity is not None and target_activity.planned_quantity and target_activity.planned_quantity > 0:
                q_planned = float(target_activity.planned_quantity)
                if parsed.quantity_semantics == "CUMULATIVE":
                    q_prev = round((prev_pct / 100.0) * q_planned, 4)
                    incremental_qty = round(max(0.0, float(event.quantity) - q_prev), 2)
                    proposed_pct = min(100.0, (float(event.quantity) / q_planned) * 100.0)
                else:
                    qty_ratio = (float(event.quantity) / q_planned) * 100.0
                    proposed_pct = min(100.0, prev_pct + qty_ratio)
            elif event.status_reported == "COMPLETED":
                proposed_pct = 100.0
            else:
                proposed_pct = min(100.0, prev_pct + 25.0 if prev_pct < 75.0 else 100.0)

            proposed_pct = round(proposed_pct, 2)
            ctx["status"] = "PROPOSAL_PENDING"
            cls._save_update_context(event, ctx)

            # Stage persistent UpdateProposal in PostgreSQL
            proposal = cls.stage_proposal(
                db=db,
                conversation=conv,
                event=event,
                activity=target_activity,
                proposed_percent=proposed_pct,
                proposed_status=target_activity.status,
                quantity_semantics=parsed.quantity_semantics or "INCREMENTAL",
                incremental_quantity=incremental_qty,
                override_percent=ctx.get("override_percent"),
            )

            card = ActionCardDTO(
                type="PROPOSAL_CONFIRMATION",
                proposal_id=proposal.id,
                event_id=event.id,
                activity_id=target_activity.id,
                activity_code=target_activity.activity_code,
                activity_name=target_activity.name,
                current_percent=prev_pct,
                proposed_percent=proposed_pct,
                incremental_quantity=incremental_qty,
                unit=event.unit,
                execution_date=event.execution_date.strftime("%Y-%m-%d"),
            )

            score_pct = int(round(top_cand.match_score * 100))
            delta_note = f" (+{incremental_qty} {event.unit})" if incremental_qty else ""
            qty_display = f"+{incremental_qty} {event.unit}" if incremental_qty else "Reported"
            loc_display = event.location or target_activity.activity_code

            if resp_lang == "hi":
                reply_text = (
                    f"मुझे {target_activity.activity_code} ({target_activity.name}) मिल गई है। "
                    f"प्रस्तावित प्रगति अपडेट {proposed_pct}% है। कृपया लागू करने से पहले पुष्टि करें।"
                )
                card.confirm_label = "अपडेट की पुष्टि करें"
                card.reject_label = "रद्द करें"
                card.review_label = "समीक्षा करें"
            elif resp_lang == "hinglish":
                reply_text = (
                    f"Mujhe {target_activity.activity_code} ({target_activity.name}) mil gayi hai. "
                    f"Proposed progress update {proposed_pct}% hai. Please apply karne se pehle confirm karein."
                )
                card.confirm_label = "Update Confirm Karein"
                card.reject_label = "Reject Karein"
                card.review_label = "Details Review Karein"
            else:
                reply_text = (
                    f"I found {target_activity.activity_code} ({target_activity.name}). "
                    f"The proposed progress update is {proposed_pct}%. Please confirm before I apply it."
                )
                card.confirm_label = "Confirm Update"
                card.reject_label = "Reject"
                card.review_label = "Review Details & Audit Safety"

            return cls._save_and_return_agent_response(
                db=db,
                conv=conv,
                reply_text=reply_text,
                action_card=card,
            )

        # Ambiguous Match or Low Confidence Routing
        candidates = eval_result.all_candidates
        top_score = candidates[0].match_score if candidates else 0.0

        loc_specified = ctx.get("location")
        if loc_specified and candidates:
            # Check if any candidate in the project matches the user's explicitly specified location
            loc_matched = False
            for c in candidates:
                act = db.query(Activity).filter_by(id=c.activity_id).first()
                if act and (
                    loc_specified.lower() in (act.location_code or "").lower()
                    or loc_specified.lower() in (act.name or "").lower()
                ):
                    loc_matched = True
                    break
            if not loc_matched:
                candidates = []

        if not candidates or top_score < 0.20:
            # No matching activity found
            conv.clarification_turns += 1
            conv.status = "WAITING_FOR_USER"
            ctx["clarification_turns"] = conv.clarification_turns
            ctx["last_question_type"] = "no_match"
            cls._save_update_context(event, ctx)

            desc_cited = ctx.get("description") or raw_text
            if resp_lang == "hi":
                question = (
                    f"मुझे इस project में उस विवरण से मेल खाती कोई activity नहीं मिली। "
                    f"कृपया कोई अन्य identifier प्रदान करें, जैसे activity ID, WBS, equipment का नाम, या अधिक विशिष्ट विवरण।"
                )
            elif resp_lang == "hinglish":
                question = (
                    f"Mujhe is project mein us description se match hone wali koi activity nahi mili. "
                    f"Please koi doosra identifier provide karein, jaise activity ID, WBS, equipment name, ya specific description."
                )
            else:
                question = (
                    f"I couldn't find a matching activity for that description in this project. "
                    f"Please provide another identifier, such as the activity ID, WBS, equipment name, or a more specific activity description."
                )

            return cls._save_and_return_agent_response(
                db=db,
                conv=conv,
                reply_text=question,
                action_card=None,
            )

        # Clarification turn counter increment
        conv.clarification_turns += 1
        conv.status = "WAITING_FOR_USER"
        ctx["clarification_turns"] = conv.clarification_turns

        # Check turn limit -> route to Lead Planner Review Queue after 3 failed turns
        if conv.clarification_turns >= 4:
            event.status = "IN_REVIEW"
            conv.active_event_id = None
            conv.status = "ACTIVE"
            cls._save_update_context(event, ctx)
            if resp_lang == "hi":
                reply_text = (
                    f"{conv.clarification_turns} clarification turns के बाद भी high confidence से activity match नहीं हो सकी। "
                    f"मैंने event {event.id} को manual verification के लिए Lead Planner Review Queue में भेज दिया है।"
                )
            elif resp_lang == "hinglish":
                reply_text = (
                    f"{conv.clarification_turns} clarification turns ke baad bhi high confidence match nahi mila. "
                    f"Maine event {event.id} ko manual verification ke liye Lead Planner Review Queue mein route kar diya hai."
                )
            else:
                reply_text = (
                    f"I could not resolve this report with high confidence after {conv.clarification_turns} clarification turns. "
                    f"I have routed event {event.id} to the Lead Planner Review Queue for manual verification."
                )
            return cls._save_and_return_agent_response(
                db=db,
                conv=conv,
                reply_text=reply_text,
                action_card=None,
            )

        meaningful_cands = [
            c for c in candidates
            if c.match_score >= 0.25 and c.match_score >= (top_score - 0.25)
        ]
        if not meaningful_cands:
            meaningful_cands = candidates[:2]

        # FALLBACK CANDIDATE CARD: Only on turn >= 3 as a controlled last resort
        if conv.clarification_turns >= 3:
            none_of_these_label = "इनमें से कोई नहीं" if resp_lang == "hi" else "None of these"
            slice_cands = meaningful_cands[:4]
            if resp_lang == "hi":
                question = "गतिविधि की विशिष्ट पहचान के लिए मुझे और विवरण की आवश्यकता है। क्या आप शेष मिलान वाली गतिविधियों में से चुनना चाहेंगे?"
            elif resp_lang == "hinglish":
                question = "Activity ko uniquely identify karne ke liye mujhe aur details chahiye. Kya aap bachi hui matching activities mein se select karna chahte hain?"
            else:
                question = "I still need more details to identify the activity. Would you like to choose from the remaining matching activities?"

            options = [
                {
                    "label": f"{c.activity_code} - {c.activity_name}",
                    "value": c.activity_code,
                    "activity_code": c.activity_code,
                    "activity_name": c.activity_name,
                    "confidence_score": c.match_score,
                }
                for c in slice_cands
            ]
            options.append({
                "label": none_of_these_label,
                "value": "NONE_OF_THESE",
            })
            cls._save_update_context(event, ctx)
            return cls._save_and_return_agent_response(
                db=db,
                conv=conv,
                reply_text=question,
                action_card=ActionCardDTO(
                    type="CLARIFICATION_CHOICE",
                    event_id=event.id,
                    question=question,
                    options=options,
                ),
            )

        # TURNS 1 AND 2: Pure conversational clarification without candidate card
        question = cls._find_distinguishing_question(
            candidates=meaningful_cands,
            db=db,
            project=project,
            resp_lang=resp_lang,
            ctx=ctx,
        )
        cls._save_update_context(event, ctx)

        return cls._save_and_return_agent_response(
            db=db,
            conv=conv,
            reply_text=question,
            action_card=None,
        )

    @classmethod
    def _save_and_return_agent_response(
        cls,
        db: Session,
        conv: Conversation,
        reply_text: str,
        action_card: Optional[ActionCardDTO],
        transcript: Optional[str] = None,
        detected_language: Optional[str] = None,
    ) -> MessageResponseDTO:
        # Check if conversation language requires translation fallback
        target_lang = conv.language
        if target_lang and not SarvamService.is_native_generation_supported(target_lang):
            try:
                reply_text = SarvamService.translate_text(
                    text=reply_text,
                    target_language_code=target_lang,
                    source_language_code="en-IN",
                )
                if action_card:
                    if action_card.question:
                        action_card.question = SarvamService.translate_text(
                            action_card.question,
                            target_language_code=target_lang,
                            source_language_code="en-IN",
                        )
                    if action_card.confirm_label:
                        action_card.confirm_label = SarvamService.translate_text(
                            action_card.confirm_label,
                            target_language_code=target_lang,
                            source_language_code="en-IN",
                        )
                    if action_card.reject_label:
                        action_card.reject_label = SarvamService.translate_text(
                            action_card.reject_label,
                            target_language_code=target_lang,
                            source_language_code="en-IN",
                        )
                    if action_card.review_label:
                        action_card.review_label = SarvamService.translate_text(
                            action_card.review_label,
                            target_language_code=target_lang,
                            source_language_code="en-IN",
                        )
                    if action_card.scope_label:
                        action_card.scope_label = SarvamService.translate_text(
                            action_card.scope_label,
                            target_language_code=target_lang,
                            source_language_code="en-IN",
                        )
                    if action_card.options:
                        for opt in action_card.options:
                            if isinstance(opt, dict) and opt.get("label"):
                                opt["label"] = SarvamService.translate_text(
                                    opt["label"],
                                    target_language_code=target_lang,
                                    source_language_code="en-IN",
                                )
            except Exception as e:
                logger.error(f"Translation fallback error: {e}")

        meta_json = json.dumps(action_card.model_dump()) if action_card else None
        agent_msg = ConversationMessage(
            id=f"msg-{uuid.uuid4().hex[:8]}",
            conversation_id=conv.id,
            sender="AGENT",
            content=reply_text,
            message_metadata=meta_json,
        )
        conv.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add(agent_msg)
        db.commit()

        return MessageResponseDTO(
            message_id=agent_msg.id,
            sender="AGENT",
            reply_text=reply_text,
            action_card=action_card,
            created_at=_format_utc_iso(agent_msg.created_at) or _format_utc_iso(datetime.now(timezone.utc)),
            transcript=transcript,
            detected_language=detected_language,
            conversation_language=conv.language,
            conversation_style=conv.language_style,
            language_locked=bool(conv.language_locked),
        )

    @classmethod
    def stage_proposal(
        cls,
        db: Session,
        conversation: Conversation,
        event: ExecutionEvent,
        activity: Activity,
        proposed_percent: float,
        proposed_status: str = "IN_PROGRESS",
        quantity_semantics: str = "INCREMENTAL",
        incremental_quantity: Optional[float] = None,
        override_percent: Optional[float] = None,
        ttl_seconds: int = 300,
    ) -> UpdateProposal:
        proposal = UpdateProposal(
            id=f"prop-{uuid.uuid4().hex[:8]}",
            conversation_id=conversation.id,
            event_id=event.id,
            project_id=conversation.project_id,
            matched_activity_id=activity.id,
            proposed_state=json.dumps({
                "current_percent": activity.percent_complete or 0.0,
                "proposed_percent": proposed_percent,
                "incremental_quantity": incremental_quantity or event.quantity,
                "unit": event.unit,
                "quantity_semantics": quantity_semantics,
                "override_percent": override_percent,
            }),
            baseline_activity_state=json.dumps({
                "percent_complete": activity.percent_complete or 0.0,
                "status": activity.status,
            }),
            status="PENDING",
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=ttl_seconds),
        )
        db.add(proposal)
        conversation.status = "WAITING_FOR_USER"
        db.flush()
        return proposal

    @classmethod
    def process_attachment(
        cls,
        db: Session,
        project_id: str,
        conversation_id: str,
        file_bytes: bytes,
        filename: str,
        caller_id: str = "site-supervisor",
    ) -> AttachmentResponseDTO:
        conv = (
            db.query(Conversation)
            .filter(Conversation.id == conversation_id, Conversation.project_id == project_id)
            .first()
        )
        if not conv:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

        # Upload to MinIO using existing MinIO service
        storage_key, file_hash, is_dup = minio_service.upload_artifact(
            project_id=project_id,
            file_bytes=file_bytes,
            filename=filename,
        )

        # Record in artifacts table
        artifact = Artifact(
            id=f"art-{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            filename=f"projects/{project_id}/reports/chat/{filename}",
            original_filename=filename,
            file_type=filename.split(".")[-1].lower() if "." in filename else "bin",
            file_size=len(file_bytes),
            sha256=file_hash,
            storage_bucket="sih-artifacts",
            storage_key=storage_key,
            uploaded_by=caller_id,
            extraction_status="PENDING",
        )
        db.add(artifact)
        db.flush()

        # Call ExtractionService to extract events from document
        extracted_events = ExtractionService.extract_artifact(
            db=db,
            artifact_id=artifact.id,
            force_reextract=True,
        )

        # Bind events to conversation as HYBRID
        for ev in extracted_events:
            ev.conversation_id = conv.id
            ev.source_type = "HYBRID"
        db.flush()

        if extracted_events:
            first_event = extracted_events[0]
            conv.active_event_id = first_event.id
            conv.clarification_turns = 0
            db.flush()

            # Evaluate match
            eval_res = MatchingService.evaluate_event_for_agent(db, first_event)
            if eval_res.route == "AUTO_LINK" and eval_res.selected_candidate:
                top_c = eval_res.selected_candidate
                act = db.query(Activity).filter(Activity.id == top_c.activity_id).first()

                prev_pct = act.percent_complete or 0.0
                proposed_pct = min(100.0, prev_pct + 25.0 if prev_pct < 75.0 else 100.0)
                if first_event.quantity and act.planned_quantity and act.planned_quantity > 0:
                    proposed_pct = min(100.0, prev_pct + (first_event.quantity / act.planned_quantity) * 100.0)
                proposed_pct = round(proposed_pct, 2)

                proposal = UpdateProposal(
                    id=f"prop-{uuid.uuid4().hex[:8]}",
                    conversation_id=conv.id,
                    event_id=first_event.id,
                    project_id=project_id,
                    matched_activity_id=act.id,
                    proposed_state=json.dumps({
                        "current_percent": prev_pct,
                        "proposed_percent": proposed_pct,
                        "incremental_quantity": first_event.quantity,
                        "unit": first_event.unit,
                    }),
                    baseline_activity_state=json.dumps({
                        "percent_complete": act.percent_complete,
                        "status": act.status,
                    }),
                    status="PENDING",
                    expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=300),
                )
                db.add(proposal)
                db.commit()

                card = ActionCardDTO(
                    type="PROPOSAL_CONFIRMATION",
                    proposal_id=proposal.id,
                    event_id=first_event.id,
                    activity_id=act.id,
                    activity_code=act.activity_code,
                    activity_name=act.name,
                    current_percent=prev_pct,
                    proposed_percent=proposed_pct,
                    incremental_quantity=first_event.quantity,
                    unit=first_event.unit,
                    execution_date=first_event.execution_date.strftime("%Y-%m-%d"),
                )
                agent_text = (
                    f"Processed {filename} and extracted {len(extracted_events)} event(s). "
                    f"Matched to {act.activity_code} ({act.name}). Ready for your confirmation."
                )
            else:
                db.commit()
                agent_text = (
                    f"Processed {filename} and extracted {len(extracted_events)} event(s). "
                    f"The top candidate match is ambiguous. Which specific foundation or work package was performed?"
                )
                card = None
        else:
            db.commit()
            agent_text = f"Processed {filename}, but no physical construction progress events were detected."
            card = None

        return AttachmentResponseDTO(
            artifact_id=artifact.id,
            filename=filename,
            extracted_events_count=len(extracted_events),
            agent_message=agent_text,
            action_card=card,
        )

    @classmethod
    def confirm_proposal(
        cls,
        db: Session,
        project_id: str,
        conversation_id: str,
        proposal_id: str,
        caller_id: str = "site-supervisor",
    ) -> ProposalConfirmResponse:
        """
        Executes an atomic, row-locked confirmation transaction:
        1. Acquires SELECT FOR UPDATE on update_proposals
        2. Validates project/conversation/event scope invariants
        3. Verifies TTL and baseline activity state
        4. Invokes ScheduleUpdateService.apply_event_progress(commit=False)
        5. Updates proposal status to CONSUMED and event status to APPLIED
        6. Single atomic commit
        """
        # 1. Acquire row lock within active transaction
        proposal_query = (
            db.query(UpdateProposal)
            .filter(
                UpdateProposal.id == proposal_id,
                UpdateProposal.conversation_id == conversation_id,
                UpdateProposal.project_id == project_id,
            )
        )
        # In SQLite (unit tests), with_for_update is ignored or not supported, in PostgreSQL it locks the row
        if not db.bind.name.startswith("sqlite"):
            proposal_query = proposal_query.with_for_update()

        proposal = proposal_query.first()
        if not proposal:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Proposal {proposal_id} not found in this conversation.",
            )

        # 2. Check proposal status
        if proposal.status != "PENDING":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Proposal already {proposal.status.lower()} (PROPOSAL_ALREADY_CONSUMED).",
            )

        # 3. Check expiration
        if datetime.now(timezone.utc).replace(tzinfo=None) > proposal.expires_at:
            proposal.status = "EXPIRED"
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Proposal has expired (PROPOSAL_EXPIRED). Please submit a new report.",
            )

        # 4. Scope and entity verification
        event = db.query(ExecutionEvent).filter(ExecutionEvent.id == proposal.event_id).first()
        if not event or event.project_id != project_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-project entity violation.")

        activity = db.query(Activity).filter(Activity.id == proposal.matched_activity_id).first()
        if not activity or activity.project_id != project_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-project activity violation.")

        # 5. Stale baseline concurrency check
        baseline = json.loads(proposal.baseline_activity_state)
        current_pct = activity.percent_complete or 0.0
        baseline_pct = baseline.get("percent_complete") or 0.0
        if round(current_pct, 2) != round(baseline_pct, 2) or activity.status != baseline.get("status"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Activity baseline state modified concurrently (STALE_ACTIVITY_BASELINE).",
            )

        # 6. Execute atomic schedule mutation inside caller transaction
        try:
            proposal.status = "CONFIRMED"
            proposal.confirmed_by = caller_id
            proposal.confirmed_at = datetime.now(timezone.utc).replace(tzinfo=None)

            proposed_data = json.loads(proposal.proposed_state)
            override_pct = proposed_data.get("override_percent")
            target_proposed = proposed_data.get("proposed_percent")
            eff_override = override_pct if override_pct is not None else target_proposed

            updated_act = ScheduleUpdateService.apply_event_progress(
                db=db,
                event_id=proposal.event_id,
                activity_id=proposal.matched_activity_id,
                user_id=caller_id,
                override_percent=eff_override,
                action_name="TIME_AGENT_CONVERSATIONAL_UPDATE",
                quantity_semantics=proposed_data.get("quantity_semantics", "INCREMENTAL"),
                commit=False,  # Single unified commit owned by this method
            )

            proposal.status = "CONSUMED"
            proposal.consumed_at = datetime.now(timezone.utc).replace(tzinfo=None)

            # Clear active event from conversation
            conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
            if conv:
                conv.active_event_id = None
                conv.status = "RESOLVED"

            # Fetch audit log ID
            audit_entry = (
                db.query(ScheduleAuditLog)
                .filter(
                    ScheduleAuditLog.activity_id == activity.id,
                    ScheduleAuditLog.execution_event_id == event.id,
                )
                .order_by(ScheduleAuditLog.timestamp.desc())
                .first()
            )
            audit_id = audit_entry.id if audit_entry else "audit-recorded"

            db.commit()
            db.refresh(updated_act)

            return ProposalConfirmResponse(
                status="APPLIED",
                activity_id=updated_act.id,
                activity_code=updated_act.activity_code,
                previous_percent=baseline_pct,
                new_percent=updated_act.percent_complete,
                audit_log_id=audit_id,
                message=f"{updated_act.activity_code} successfully updated to {updated_act.percent_complete}%.",
            )
        except ValidationException as ve:
            db.rollback()
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
        except Exception as e:
            db.rollback()
            logger.error(f"Error during proposal confirmation: {e}")
            raise

    @classmethod
    def confirm_bulk_proposal(
        cls,
        db: Session,
        project_id: str,
        conversation_id: str,
        payload: BulkProposalConfirmRequest,
        caller_id: str = "site-supervisor",
    ) -> BulkProposalConfirmResponse:
        conv = (
            db.query(Conversation)
            .filter(Conversation.id == conversation_id, Conversation.project_id == project_id)
            .first()
        )
        if not conv:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

        if payload.action == "CANCEL":
            conv.active_event_id = None
            conv.status = "ACTIVE"

            # Mark prior bulk proposal message as CANCELLED (scan recent messages)
            prior_msgs = (
                db.query(ConversationMessage)
                .filter(ConversationMessage.conversation_id == conv.id, ConversationMessage.sender == "AGENT")
                .order_by(ConversationMessage.created_at.desc())
                .limit(10)
                .all()
            )
            for m in prior_msgs:
                if m.message_metadata:
                    try:
                        meta = json.loads(m.message_metadata) if isinstance(m.message_metadata, str) else m.message_metadata
                        if meta.get("type") == "BULK_SCOPE_PROPOSAL":
                            meta["proposal_status"] = "CANCELLED"
                            m.message_metadata = json.dumps(meta)
                            break
                    except Exception:
                        pass

            resp_lang = cls._resolve_template_language(conv)
            if resp_lang == "hi":
                cancel_text = "Bulk update cancel कर दिया गया है। Authoritative schedule में कोई बदलाव नहीं किया गया।"
            elif resp_lang == "hinglish":
                cancel_text = "Bulk update cancel kar diya gaya hai. Authoritative schedule mein koi change nahi hua."
            else:
                cancel_text = "Bulk update was cancelled. No changes were applied to the schedule."

            cancel_msg = ConversationMessage(
                id=f"msg-{uuid.uuid4().hex[:8]}",
                conversation_id=conv.id,
                sender="AGENT",
                content=cancel_text,
            )
            db.add(cancel_msg)
            db.commit()
            return BulkProposalConfirmResponse(
                status="CANCELLED",
                updated_count=0,
                updated_activities=[],
                message=cancel_text,
            )

        act_query = db.query(Activity).filter(Activity.project_id == project_id)
        if payload.activity_ids:
            act_query = act_query.filter(Activity.id.in_(payload.activity_ids))
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No activity IDs provided for bulk update.")

        target_acts = act_query.all()
        if not target_acts:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No matching activities found.")

        target_pct = payload.target_percent if payload.target_percent is not None else 100.0
        updated_records = []

        try:
            for act in target_acts:
                ev = ExecutionEvent(
                    id=f"ev-{uuid.uuid4().hex[:8]}",
                    project_id=project_id,
                    source_type="CONVERSATION",
                    conversation_id=conv.id,
                    verbatim_excerpt=f"Bulk update {act.activity_code} to {target_pct}%",
                    description=f"Bulk progress update to {target_pct}% complete",
                    reported_activity_code=act.activity_code,
                    execution_date=datetime.now(timezone.utc).replace(tzinfo=None),
                    status_reported=payload.status_reported or "COMPLETED",
                    extraction_confidence=1.0,
                    status="AUTO_LINKED",
                )
                db.add(ev)
                db.flush()

                prev_pct = act.percent_complete or 0.0
                updated_act = ScheduleUpdateService.apply_event_progress(
                    db=db,
                    event_id=ev.id,
                    activity_id=act.id,
                    user_id=caller_id,
                    override_percent=target_pct,
                    action_name="TIME_AGENT_BULK_UPDATE",
                    quantity_semantics="INCREMENTAL",
                    commit=False,
                )
                updated_records.append({
                    "activity_id": updated_act.id,
                    "activity_code": updated_act.activity_code,
                    "previous_percent": prev_pct,
                    "new_percent": updated_act.percent_complete,
                    "status": updated_act.status,
                })

            conv.active_event_id = None
            conv.status = "RESOLVED"
            conv.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

            # Mark prior bulk proposal card as APPLIED
            prior_msgs = (
                db.query(ConversationMessage)
                .filter(ConversationMessage.conversation_id == conv.id, ConversationMessage.sender == "AGENT")
                .all()
            )
            for m in prior_msgs:
                if m.message_metadata:
                    try:
                        meta = json.loads(m.message_metadata) if isinstance(m.message_metadata, str) else m.message_metadata
                        if meta.get("type") == "BULK_SCOPE_PROPOSAL":
                            meta["proposal_status"] = "APPLIED"
                            m.message_metadata = json.dumps(meta)
                    except Exception:
                        pass

            summary_codes = ", ".join([r["activity_code"] for r in updated_records])
            confirm_msg = ConversationMessage(
                id=f"msg-{uuid.uuid4().hex[:8]}",
                conversation_id=conv.id,
                sender="AGENT",
                content=f"Successfully updated {len(updated_records)} activities to {target_pct}% complete: {summary_codes}. Ledger audit records created and schedule refreshed.",
            )
            db.add(confirm_msg)
            db.commit()

            return BulkProposalConfirmResponse(
                status="APPLIED",
                updated_count=len(updated_records),
                updated_activities=updated_records,
                message=f"Applied bulk update to {len(updated_records)} activities.",
            )
        except Exception as e:
            db.rollback()
            logger.error(f"Bulk update failed: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
