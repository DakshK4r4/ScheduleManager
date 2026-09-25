from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.domain.models import (
    Activity,
    Conversation,
    ConversationMessage,
    ExecutionEvent,
    Project,
    UpdateProposal,
)
from app.schemas.agent import (
    ConversationDTO,
    ConversationSummaryDTO,
    MessageDTO,
    PendingActionDTO,
)

logger = logging.getLogger(__name__)


def format_utc_iso(dt: Optional[datetime]) -> Optional[str]:
    """
    Format datetime to ISO 8601 with explicit UTC timezone designator ('Z').
    Prevents browsers from interpreting naive UTC strings as local time.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AgentConversationService:
    """
    Dedicated Service for Time Agent Conversation Lifecycle, State,
    Persistence, Pinning, Deletion, and Multilingual Language Resolution.
    """

    @classmethod
    def generate_conversation_title(
        cls,
        text: str,
        parsed: Optional[Any] = None,
    ) -> str:
        """
        Deterministic 3-7 word conversation title generation based on user message and parsed signals.
        Never makes external API calls.
        """
        lower = text.lower()
        loc = parsed.location if parsed and hasattr(parsed, "location") and parsed.location else None
        act_code = parsed.reported_activity_code if parsed and hasattr(parsed, "reported_activity_code") and parsed.reported_activity_code else None

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
            conv = (
                db.query(Conversation)
                .filter(
                    Conversation.project_id == project_id,
                    Conversation.user_id == user_id,
                    Conversation.status.in_(["ACTIVE", "CLARIFYING", "WAITING_FOR_BULK_UPDATE_CONFIRMATION"]),
                )
                .order_by(Conversation.updated_at.desc())
                .first()
            )

        if not conv:
            initial_title = title or "New Chat"
            conv = Conversation(
                project_id=project_id,
                user_id=user_id,
                title=initial_title,
                status="ACTIVE",
                active_activity_id=active_activity_id,
                clarification_turns=0,
            )
            db.add(conv)
            db.commit()
            db.refresh(conv)
        elif active_activity_id and conv.active_activity_id != active_activity_id:
            conv.active_activity_id = active_activity_id
            db.commit()
            db.refresh(conv)

        return cls.to_conversation_dto(db, conv)

    @classmethod
    def list_conversations(
        cls,
        db: Session,
        project_id: str,
        user_id: str = "site-supervisor",
    ) -> List[ConversationSummaryDTO]:
        conversations = (
            db.query(Conversation)
            .filter(
                Conversation.project_id == project_id,
                Conversation.user_id == user_id,
            )
            .order_by(
                Conversation.is_pinned.desc(),
                Conversation.updated_at.desc(),
            )
            .all()
        )
        summaries = []
        for c in conversations:
            msg_count = (
                db.query(func.count(ConversationMessage.id))
                .filter(ConversationMessage.conversation_id == c.id)
                .scalar()
                or 0
            )
            summaries.append(
                ConversationSummaryDTO(
                    id=c.id,
                    project_id=c.project_id,
                    title=c.title or "New Chat",
                    status=c.status,
                    language=getattr(c, "language", None),
                    is_pinned=bool(getattr(c, "is_pinned", False)),
                    created_at=format_utc_iso(c.created_at) or format_utc_iso(datetime.now(timezone.utc)),
                    updated_at=format_utc_iso(c.updated_at) or format_utc_iso(datetime.now(timezone.utc)),
                    message_count=msg_count,
                    active_activity_id=c.active_activity_id,
                    active_event_id=c.active_event_id,
                )
            )
        return summaries

    @classmethod
    def to_conversation_dto(cls, db: Session, conv: Conversation) -> ConversationDTO:
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
                    created_at=format_utc_iso(m.created_at) or format_utc_iso(datetime.now(timezone.utc)),
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
            created_at=format_utc_iso(conv.created_at),
            updated_at=format_utc_iso(conv.updated_at),
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
            created_at=format_utc_iso(conv.created_at) or format_utc_iso(datetime.now(timezone.utc)),
            updated_at=format_utc_iso(conv.updated_at) or format_utc_iso(datetime.now(timezone.utc)),
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

        conv.active_event_id = None
        conv.active_activity_id = None
        db.flush()

        db.query(ExecutionEvent).filter(ExecutionEvent.conversation_id == conv.id).update(
            {"conversation_id": None, "message_id": None}
        )
        db.flush()

        db.delete(conv)
        db.commit()

    @classmethod
    def resolve_template_language(cls, conv: Conversation) -> str:
        lang = (conv.language or "en").lower().strip()
        style = (conv.language_style or "").lower().strip()
        if style == "hinglish" or lang == "hinglish":
            return "hinglish"
        if lang.startswith("hi"):
            return "hi"
        return "en"
