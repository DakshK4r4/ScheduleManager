from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
import pytest
from fastapi import HTTPException

from app.domain.models import (
    Activity,
    ExecutionEvent,
    Project,
    UpdateProposal,
)
from app.schemas.review import ReviewDecisionRequest
from app.services.schedule_update_service import ScheduleUpdateService
from app.api.review import submit_review_decision
from app.services.agent_service import TimeAgentService


def test_cross_project_service_layer_rejection(db_session):
    """
    Direct service invocation of ScheduleUpdateService.apply_event_progress
    must fail if the target activity does not belong to the event's project.
    """
    proj_a = Project(
        id=f"proj-a-{uuid.uuid4().hex[:8]}",
        project_code="PROJ-A",
        name="Project A",
    )
    proj_b = Project(
        id=f"proj-b-{uuid.uuid4().hex[:8]}",
        project_code="PROJ-B",
        name="Project B",
    )
    db_session.add_all([proj_a, proj_b])
    db_session.flush()

    act_b = Activity(
        id=f"act-b-{uuid.uuid4().hex[:8]}",
        project_id=proj_b.id,
        activity_code="ACT-B1",
        name="Foundation Pour B",
        status="NOT_STARTED",
        percent_complete=0.0,
    )
    event_a = ExecutionEvent(
        id=f"ev-a-{uuid.uuid4().hex[:8]}",
        project_id=proj_a.id,
        source_type="FIELD_REPORT",
        status="AMBIGUOUS",
        verbatim_excerpt="Foundation poured on site A",
        description="Progress reported on site",
        execution_date=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add_all([act_b, event_a])
    db_session.flush()

    with pytest.raises(ValueError, match="Security violation: Target activity.*does not belong to event"):
        ScheduleUpdateService.apply_event_progress(
            db=db_session,
            event_id=event_a.id,
            activity_id=act_b.id,
            user_id="test-planner",
            override_percent=50.0,
        )


def test_cross_project_review_decision_api_rejection(db_session):
    """
    API call to submit_review_decision must reject reassigning or approving an event
    to an activity belonging to another project.
    """
    proj_a = Project(
        id=f"proj-a2-{uuid.uuid4().hex[:8]}",
        project_code="PROJ-A2",
        name="Project A2",
    )
    proj_b = Project(
        id=f"proj-b2-{uuid.uuid4().hex[:8]}",
        project_code="PROJ-B2",
        name="Project B2",
    )
    db_session.add_all([proj_a, proj_b])
    db_session.flush()

    act_b = Activity(
        id=f"act-b2-{uuid.uuid4().hex[:8]}",
        project_id=proj_b.id,
        activity_code="ACT-B2",
        name="Steel Erection B2",
        status="NOT_STARTED",
        percent_complete=0.0,
    )
    event_a = ExecutionEvent(
        id=f"ev-a2-{uuid.uuid4().hex[:8]}",
        project_id=proj_a.id,
        source_type="FIELD_REPORT",
        status="AMBIGUOUS",
        verbatim_excerpt="Steel arrived on site A",
        description="Steel delivered to site",
        execution_date=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add_all([act_b, event_a])
    db_session.flush()

    request = ReviewDecisionRequest(
        event_id=event_a.id,
        activity_id=act_b.id,
        reviewer_id="lead-planner",
        decision="APPROVED",
        notes="Attempting cross-project reassignment",
    )

    with pytest.raises(HTTPException) as exc_info:
        submit_review_decision(request=request, db=db_session)

    assert exc_info.value.status_code == 400
    assert "belongs to project" in exc_info.value.detail


def test_cross_project_proposal_confirmation_rejection(db_session):
    """
    TimeAgentService.confirm_proposal must reject confirmation if proposal/event/activity
    do not strictly align with the project scope.
    """
    proj_1 = Project(
        id=f"proj-1-{uuid.uuid4().hex[:8]}",
        project_code="PROJ-1",
        name="Project 1",
    )
    proj_2 = Project(
        id=f"proj-2-{uuid.uuid4().hex[:8]}",
        project_code="PROJ-2",
        name="Project 2",
    )
    db_session.add_all([proj_1, proj_2])
    db_session.flush()

    # Proposal created for Project 1
    proposal = UpdateProposal(
        id=f"prop-{uuid.uuid4().hex[:8]}",
        project_id=proj_1.id,
        conversation_id="conv-123",
        event_id="ev-123",
        matched_activity_id="act-123",
        proposed_state=json.dumps({"proposed_percent": 60.0}),
        status="PENDING",
        baseline_activity_state="{}",
        expires_at=datetime(2030, 1, 1),
    )
    db_session.add(proposal)
    db_session.flush()

    # Attempt to confirm proposal specifying Project 2
    with pytest.raises(HTTPException) as exc_info:
        TimeAgentService.confirm_proposal(
            db=db_session,
            project_id=proj_2.id,  # Mismatched project
            conversation_id="conv-123",
            proposal_id=proposal.id,
            caller_id="intruder",
        )

    assert exc_info.value.status_code == 404
