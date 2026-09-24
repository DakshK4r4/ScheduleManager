from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    planned_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    planned_finish: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    data_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )

    # Relationships
    wbs_nodes: Mapped[List[WBSNode]] = relationship(
        "WBSNode", back_populates="project", cascade="all, delete-orphan"
    )
    activities: Mapped[List[Activity]] = relationship(
        "Activity", back_populates="project", cascade="all, delete-orphan"
    )
    relationships: Mapped[List[ActivityRelationship]] = relationship(
        "ActivityRelationship", back_populates="project", cascade="all, delete-orphan"
    )
    artifacts: Mapped[List[Artifact]] = relationship(
        "Artifact", back_populates="project", cascade="all, delete-orphan"
    )
    execution_events: Mapped[List[ExecutionEvent]] = relationship(
        "ExecutionEvent", back_populates="project", cascade="all, delete-orphan"
    )
    conversations: Mapped[List[Conversation]] = relationship(
        "Conversation", back_populates="project", cascade="all, delete-orphan"
    )
    update_proposals: Mapped[List[UpdateProposal]] = relationship(
        "UpdateProposal", back_populates="project", cascade="all, delete-orphan"
    )


class WBSNode(Base):
    __tablename__ = "wbs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("wbs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    # Relationships
    project: Mapped[Project] = relationship("Project", back_populates="wbs_nodes")
    parent: Mapped[Optional[WBSNode]] = relationship(
        "WBSNode", remote_side=[id], backref="children"
    )
    activities: Mapped[List[Activity]] = relationship(
        "Activity", back_populates="wbs_node"
    )

    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_wbs_project_code"),
    )


class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    wbs_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("wbs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    activity_code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    activity_type: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, default="TT_Task"
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="NOT_STARTED", index=True
    )
    planned_start: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, index=True
    )
    planned_finish: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, index=True
    )
    actual_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    actual_finish: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    original_duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    remaining_duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    percent_complete: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, default=0.0
    )
    calendar: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    location_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    discipline: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    contractor_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    planned_quantity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    quantity_unit: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    early_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    early_finish: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    late_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    late_finish: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    total_float: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    free_float: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_critical: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True, default=False)
    driving_predecessor_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("activities.id", ondelete="SET NULL"), nullable=True
    )
    constraint_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    constraint_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )

    # Relationships
    project: Mapped[Project] = relationship("Project", back_populates="activities")
    wbs_node: Mapped[Optional[WBSNode]] = relationship(
        "WBSNode", back_populates="activities"
    )
    outgoing_relationships: Mapped[List[ActivityRelationship]] = relationship(
        "ActivityRelationship",
        foreign_keys="[ActivityRelationship.predecessor_id]",
        back_populates="predecessor",
        cascade="all, delete-orphan",
    )
    incoming_relationships: Mapped[List[ActivityRelationship]] = relationship(
        "ActivityRelationship",
        foreign_keys="[ActivityRelationship.successor_id]",
        back_populates="successor",
        cascade="all, delete-orphan",
    )
    execution_events: Mapped[List[ExecutionEvent]] = relationship(
        "ExecutionEvent", back_populates="matched_activity"
    )
    progress_records: Mapped[List[ActualProgressLedger]] = relationship(
        "ActualProgressLedger", back_populates="activity", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("project_id", "activity_code", name="uq_activity_project_code"),
        Index("ix_activities_proj_status", "project_id", "status"),
        Index("ix_activities_proj_wbs", "project_id", "wbs_id"),
    )


class ActivityRelationship(Base):
    __tablename__ = "activity_relationships"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    predecessor_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("activities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    successor_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("activities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relationship_type: Mapped[str] = mapped_column(
        String(10), nullable=False, default="FS"
    )
    lag: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    # Relationships
    project: Mapped[Project] = relationship("Project", back_populates="relationships")
    predecessor: Mapped[Activity] = relationship(
        "Activity",
        foreign_keys=[predecessor_id],
        back_populates="outgoing_relationships",
    )
    successor: Mapped[Activity] = relationship(
        "Activity",
        foreign_keys=[successor_id],
        back_populates="incoming_relationships",
    )

    __table_args__ = (
        UniqueConstraint(
            "predecessor_id",
            "successor_id",
            "relationship_type",
            name="uq_relationship_pred_succ_type",
        ),
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    report_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    artifact_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # 'PDF_REPORT', 'SPREADSHEET', 'VOICE_MEMO', 'IMAGE'
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_bucket: Mapped[str] = mapped_column(
        String(100), nullable=False, default="sih-artifacts"
    )
    storage_key: Mapped[str] = mapped_column(
        String(500), nullable=False, unique=True
    )
    uploaded_by: Mapped[str] = mapped_column(
        String(100), nullable=False, default="site-user"
    )
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    extraction_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="UPLOADED"
    )  # 'UPLOADED', 'EXTRACTING', 'EXTRACTED', 'FAILED'
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    project: Mapped[Project] = relationship("Project", back_populates="artifacts")
    execution_events: Mapped[List[ExecutionEvent]] = relationship(
        "ExecutionEvent", back_populates="artifact", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_artifacts_project_sha", "project_id", "sha256"),
    )


class ExecutionEvent(Base):
    __tablename__ = "execution_events"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    artifact_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("artifacts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="ARTIFACT"
    )  # ARTIFACT, CONVERSATION, HYBRID
    conversation_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    message_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("conversation_messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_report_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    source_document_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    storage_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    file_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=1)
    bounding_box: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON string
    verbatim_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    activity_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reported_activity_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    execution_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    start_time: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    end_time: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    status_reported: Mapped[str] = mapped_column(
        String(50), nullable=False, default="IN_PROGRESS"
    )
    quantity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unit: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    discipline: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    contractor: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    asset: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    wbs_hint: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    extraction_confidence: Mapped[float] = mapped_column(Float, default=1.0)
    extraction_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), default="UNMATCHED", index=True
    )  # DRAFT, UNMATCHED, IN_REVIEW, AUTO_LINKED, APPLIED, REJECTED
    matched_activity_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("activities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    match_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    match_metadata: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON string
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    # Relationships
    artifact: Mapped[Optional[Artifact]] = relationship("Artifact", back_populates="execution_events")
    project: Mapped[Project] = relationship("Project", back_populates="execution_events")
    conversation: Mapped[Optional[Conversation]] = relationship(
        "Conversation", back_populates="execution_events", foreign_keys=[conversation_id]
    )
    message: Mapped[Optional[ConversationMessage]] = relationship(
        "ConversationMessage", foreign_keys=[message_id]
    )
    matched_activity: Mapped[Optional[Activity]] = relationship(
        "Activity", back_populates="execution_events"
    )
    review_decision: Mapped[Optional[ReviewDecision]] = relationship(
        "ReviewDecision", back_populates="execution_event", uselist=False, cascade="all, delete-orphan"
    )
    progress_entries: Mapped[List[ActualProgressLedger]] = relationship(
        "ActualProgressLedger", back_populates="execution_event", cascade="all, delete-orphan"
    )
    update_proposals: Mapped[List[UpdateProposal]] = relationship(
        "UpdateProposal", back_populates="event", cascade="all, delete-orphan"
    )


class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    execution_event_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("execution_events.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    activity_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("activities.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewer_id: Mapped[str] = mapped_column(
        String(100), nullable=False, default="planner-user"
    )
    decision: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # 'APPROVED', 'REJECTED', 'REASSIGNED'
    adjustment_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    # Relationships
    execution_event: Mapped[ExecutionEvent] = relationship(
        "ExecutionEvent", back_populates="review_decision"
    )
    project: Mapped[Project] = relationship("Project")
    activity: Mapped[Optional[Activity]] = relationship("Activity")


class ActualProgressLedger(Base):
    __tablename__ = "actual_progress_ledger"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    activity_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("activities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    execution_event_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("execution_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reporting_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    installed_quantity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unit_of_measure: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    incremental_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cumulative_percent: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    # Relationships
    activity: Mapped[Activity] = relationship("Activity", back_populates="progress_records")
    execution_event: Mapped[ExecutionEvent] = relationship(
        "ExecutionEvent", back_populates="progress_entries"
    )

    __table_args__ = (
        UniqueConstraint("activity_id", "execution_event_id", name="uq_activity_event_progress"),
    )


class ScheduleAuditLog(Base):
    __tablename__ = "schedule_audit_log"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    activity_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("activities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    execution_event_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("execution_events.id", ondelete="SET NULL"),
        nullable=True,
    )
    artifact_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    previous_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class DomainOutbox(Base):
    __tablename__ = "domain_outbox"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="PENDING"
    )  # PENDING, PROCESSED, FAILED
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, default="New Chat")
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, default="site-supervisor")
    active_activity_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("activities.id", ondelete="SET NULL"),
        nullable=True,
    )
    active_event_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("execution_events.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    clarification_turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="ACTIVE", index=True
    )  # ACTIVE, WAITING_FOR_USER, RESOLVED, ABANDONED
    language: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default=None)
    language_style: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, default=None)
    language_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )

    # Relationships
    project: Mapped[Project] = relationship("Project", back_populates="conversations")
    active_activity: Mapped[Optional[Activity]] = relationship(
        "Activity", foreign_keys=[active_activity_id]
    )
    active_event: Mapped[Optional[ExecutionEvent]] = relationship(
        "ExecutionEvent", foreign_keys=[active_event_id], post_update=True
    )
    messages: Mapped[List[ConversationMessage]] = relationship(
        "ConversationMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.created_at",
    )
    execution_events: Mapped[List[ExecutionEvent]] = relationship(
        "ExecutionEvent",
        back_populates="conversation",
        foreign_keys="ExecutionEvent.conversation_id",
    )
    update_proposals: Mapped[List[UpdateProposal]] = relationship(
        "UpdateProposal",
        back_populates="conversation",
        cascade="all, delete-orphan",
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender: Mapped[str] = mapped_column(String(50), nullable=False)  # USER, AGENT, SYSTEM
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_metadata: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)

    # Relationships
    conversation: Mapped[Conversation] = relationship("Conversation", back_populates="messages")


class UpdateProposal(Base):
    __tablename__ = "update_proposals"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("execution_events.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    matched_activity_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("activities.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    proposed_state: Mapped[str] = mapped_column(Text, nullable=False)  # JSON delta
    baseline_activity_state: Mapped[str] = mapped_column(Text, nullable=False)  # JSON snapshot
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="PENDING", index=True
    )  # PENDING, CONFIRMED, CONSUMED, REJECTED, EXPIRED
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    confirmed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    conversation: Mapped[Conversation] = relationship("Conversation", back_populates="update_proposals")
    event: Mapped[ExecutionEvent] = relationship("ExecutionEvent", back_populates="update_proposals")
    project: Mapped[Project] = relationship("Project", back_populates="update_proposals")
    matched_activity: Mapped[Activity] = relationship("Activity")

    @property
    def baseline_percent(self) -> float:
        if not self.baseline_activity_state:
            return 0.0
        try:
            return float(json.loads(self.baseline_activity_state).get("percent_complete", 0.0))
        except Exception:
            return 0.0

    @property
    def proposed_percent(self) -> float:
        if not self.proposed_state:
            return 0.0
        try:
            return float(json.loads(self.proposed_state).get("proposed_percent", 0.0))
        except Exception:
            return 0.0

