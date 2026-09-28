import json
from datetime import datetime, timezone
import pytest
from sqlalchemy.orm import Session

from app.domain.models import (
    Activity,
    Conversation,
    ConversationMessage,
    ExecutionEvent,
    Project,
    UpdateProposal,
    WBSNode,
)
from app.services.agent_service import TimeAgentService


@pytest.fixture
def proposal_test_fixture(db_session: Session):
    proj = Project(
        id="proj-prop-rev-demo",
        project_code="METRO_BRIDGE_DEMO",
        name="Metro Bridge Construction",
        planned_start=datetime(2026, 1, 1, 8, 0),
        planned_finish=datetime(2026, 12, 31, 17, 0),
        data_date=datetime(2026, 9, 1, 0, 0),
    )
    db_session.add(proj)

    wbs = WBSNode(
        id="wbs-civ-demo",
        project_id="proj-prop-rev-demo",
        code="WBS-CIV",
        name="Civil Works",
    )
    db_session.add(wbs)

    act = Activity(
        id="act-civ-2010",
        project_id="proj-prop-rev-demo",
        wbs_id="wbs-civ-demo",
        activity_code="CIV-2010",
        name="Site Mobilization",
        status="NOT_STARTED",
        percent_complete=0.0,
        original_duration=10.0,
        remaining_duration=10.0,
        planned_start=datetime(2026, 9, 1, 8, 0),
        planned_finish=datetime(2026, 9, 11, 17, 0),
    )
    db_session.add(act)

    conv = Conversation(
        id="conv-prop-rev-demo",
        project_id="proj-prop-rev-demo",
        user_id="site-supervisor",
        status="WAITING_FOR_USER",
        language="hinglish",
        language_style="hinglish",
        active_activity_id="act-civ-2010",
    )
    db_session.add(conv)

    event = ExecutionEvent(
        id="ev-prop-rev-demo",
        project_id="proj-prop-rev-demo",
        source_type="CONVERSATION",
        conversation_id=conv.id,
        verbatim_excerpt="CIV-2010",
        description="Site Mobilization",
        reported_activity_code="CIV-2010",
        execution_date=datetime(2026, 9, 1, 0, 0),
        status_reported="COMPLETED",
        status="DRAFT",
    )
    db_session.add(event)
    conv.active_event_id = event.id

    proposal = TimeAgentService.stage_proposal(
        db=db_session,
        conversation=conv,
        event=event,
        activity=act,
        proposed_percent=100.0,
        proposed_status="COMPLETED",
        quantity_semantics="INCREMENTAL",
    )

    # Agent message presenting proposal
    meta = {
        "type": "PROPOSAL_CONFIRMATION",
        "proposal_id": proposal.id,
        "proposal_status": "PENDING",
        "activity_id": act.id,
        "activity_code": "CIV-2010",
        "activity_name": "Site Mobilization",
        "proposed_percent": 100.0,
    }
    agent_msg = ConversationMessage(
        id="msg-agent-prop-100",
        conversation_id=conv.id,
        sender="AGENT",
        content="Mujhe CIV-2010 (Site Mobilization) mil gayi hai. Proposed progress update 100.0% hai.",
        message_metadata=json.dumps(meta),
    )
    db_session.add(agent_msg)
    db_session.commit()

    return {
        "project": proj,
        "activity": act,
        "conversation": conv,
        "event": event,
        "proposal": proposal,
    }


def test_pending_proposal_revision_to_50_percent(db_session: Session, proposal_test_fixture):
    """
    When a 100% proposal is pending, sending 'update it to 50%' must NOT confirm the 100% proposal!
    It must revise the proposal to 50% and present a new confirmation card.
    """
    conv = proposal_test_fixture["conversation"]
    act = proposal_test_fixture["activity"]
    proj = proposal_test_fixture["project"]

    # User says "update it to 50%"
    resp = TimeAgentService.process_message(
        db=db_session,
        project_id=proj.id,
        conversation_id=conv.id,
        user_content="update it to 50%",
        caller_id="site-supervisor",
    )

    # 1. Activity in DB must NOT be updated to 100%
    db_session.refresh(act)
    assert act.percent_complete == 0.0, f"Activity was mutated prematurely to {act.percent_complete}%!"

    # 2. Reply must NOT say "successfully updated to 100.0%"
    assert "successfully updated to 100" not in resp.reply_text.lower(), f"Unexpected confirmation: {resp.reply_text}"

    # 3. An action card proposing 50% must be returned
    assert resp.action_card is not None, "Expected revised action card"
    assert resp.action_card.type == "PROPOSAL_CONFIRMATION"
    assert resp.action_card.proposed_percent == 50.0

    # 4. Old proposal must be REPLACED / cancelled, and new proposal must be PENDING at 50%
    init_prop = proposal_test_fixture["proposal"]
    old_prop = db_session.query(UpdateProposal).filter(UpdateProposal.id == init_prop.id).first()
    assert old_prop.status in ("REPLACED", "CANCELLED", "REJECTED")

    new_prop = (
        db_session.query(UpdateProposal)
        .filter(
            UpdateProposal.conversation_id == conv.id,
            UpdateProposal.status == "PENDING",
        )
        .first()
    )
    assert new_prop is not None
    assert new_prop.id != init_prop.id
    assert new_prop.proposed_percent == 50.0


def test_pending_proposal_explicit_confirm(db_session: Session, proposal_test_fixture):
    """
    Sending 'confirm' or 'update it' (without revision numbers) confirms the pending proposal.
    """
    conv = proposal_test_fixture["conversation"]
    act = proposal_test_fixture["activity"]
    proj = proposal_test_fixture["project"]

    resp = TimeAgentService.process_message(
        db=db_session,
        project_id=proj.id,
        conversation_id=conv.id,
        user_content="confirm",
        caller_id="site-supervisor",
    )

    db_session.refresh(act)
    assert act.percent_complete == 100.0
    assert "successfully updated to 100" in resp.reply_text.lower()


def test_rejection_does_not_mutate_schedule(db_session: Session, proposal_test_fixture):
    """
    When user rejects proposal, schedule is not mutated and proposal is marked REJECTED.
    """
    conv = proposal_test_fixture["conversation"]
    act = proposal_test_fixture["activity"]
    proj = proposal_test_fixture["project"]

    resp = TimeAgentService.process_message(
        db=db_session,
        project_id=proj.id,
        conversation_id=conv.id,
        user_content="cancel",
        caller_id="site-supervisor",
    )

    db_session.refresh(act)
    assert act.percent_complete == 0.0
    init_prop = proposal_test_fixture["proposal"]
    prop = db_session.query(UpdateProposal).filter(UpdateProposal.id == init_prop.id).first()
    assert prop.status == "REJECTED"


def test_post_rejection_followup_revision(db_session: Session, proposal_test_fixture):
    """
    After a proposal is rejected, saying 'update it to 50%' should identify the active activity
    and stage a 50% proposal, instead of failing or updating to 100%.
    """
    conv = proposal_test_fixture["conversation"]
    act = proposal_test_fixture["activity"]
    proj = proposal_test_fixture["project"]

    # Step 1: Reject the 100% proposal
    TimeAgentService.process_message(
        db=db_session,
        project_id=proj.id,
        conversation_id=conv.id,
        user_content="cancel",
        caller_id="site-supervisor",
    )

    # Step 2: User says "update it to 50%"
    resp = TimeAgentService.process_message(
        db=db_session,
        project_id=proj.id,
        conversation_id=conv.id,
        user_content="update it to 50%",
        caller_id="site-supervisor",
    )

    db_session.refresh(act)
    assert act.percent_complete == 0.0, "Activity should not be mutated without confirmation"
    assert resp.action_card is not None
    assert resp.action_card.proposed_percent == 50.0
    assert resp.action_card.activity_code == "CIV-2010"


def test_pending_proposal_hinglish_revision(db_session: Session, proposal_test_fixture):
    """
    Saying '50% kardo' or 'change to 75%' while proposal is pending revises the proposal.
    """
    conv = proposal_test_fixture["conversation"]
    act = proposal_test_fixture["activity"]
    proj = proposal_test_fixture["project"]

    resp = TimeAgentService.process_message(
        db=db_session,
        project_id=proj.id,
        conversation_id=conv.id,
        user_content="50% kardo",
        caller_id="site-supervisor",
    )

    db_session.refresh(act)
    assert act.percent_complete == 0.0
    assert resp.action_card is not None
    assert resp.action_card.proposed_percent == 50.0


def test_endpoint_rejection_marks_proposal_rejected(db_session: Session, proposal_test_fixture):
    """
    Calling confirm_update_proposal endpoint with action='REJECT' marks proposal REJECTED
    and does not mutate schedule.
    """
    from app.api.agent import confirm_update_proposal
    from app.schemas.agent import ProposalConfirmRequest

    conv = proposal_test_fixture["conversation"]
    act = proposal_test_fixture["activity"]
    proj = proposal_test_fixture["project"]
    init_prop = proposal_test_fixture["proposal"]

    req = ProposalConfirmRequest(
        proposal_id=init_prop.id,
        action="REJECT",
    )
    resp = confirm_update_proposal(
        project_id=proj.id,
        conversation_id=conv.id,
        payload=req,
        x_user_id="site-supervisor",
        db=db_session,
    )

    db_session.refresh(act)
    assert act.percent_complete == 0.0
    assert resp.status == "REJECTED"
    db_session.refresh(init_prop)
    assert init_prop.status == "REJECTED"
