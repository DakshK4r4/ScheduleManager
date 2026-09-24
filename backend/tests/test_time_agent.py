import json
import os
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.models import (
    Activity,
    ActualProgressLedger,
    Conversation,
    ConversationMessage,
    DomainOutbox,
    ExecutionEvent,
    Project,
    ScheduleAuditLog,
    UpdateProposal,
    WBSNode,
)
from app.services.agent_parser import ConversationalParser
from app.services.matching_service import MatchingService
from app.services.schedule_update_service import ScheduleUpdateService
from app.services.agent_service import TimeAgentService
from app.services.validation_service import ValidationException


@pytest.fixture
def agent_test_project(db_session: Session):
    """Creates a sample project with activities for Time Agent testing."""
    proj = Project(
        id="proj-agent-demo",
        project_code="BOROUGE4_DEMO",
        name="Borouge 4 Petrochemical Expansion",
        planned_start=datetime(2024, 1, 1, 8, 0),
        planned_finish=datetime(2025, 12, 31, 17, 0),
        data_date=datetime(2024, 10, 1, 0, 0),
    )
    db_session.add(proj)

    wbs1 = WBSNode(
        id="wbs-agent-1",
        project_id="proj-agent-demo",
        code="WBS-CIV",
        name="Civil Works",
    )
    wbs2 = WBSNode(
        id="wbs-agent-2",
        project_id="proj-agent-demo",
        code="WBS-STR",
        name="Structural Works",
    )
    db_session.add_all([wbs1, wbs2])

    act1 = Activity(
        id="act-civ-1001",
        project_id="proj-agent-demo",
        wbs_id="wbs-agent-1",
        activity_code="CIV-1001",
        name="Foundation Concrete Pour F-204",
        status="NOT_STARTED",
        planned_start=datetime(2024, 8, 1, 8, 0),
        planned_finish=datetime(2024, 9, 30, 17, 0),
        original_duration=60.0,
        percent_complete=0.0,
        planned_quantity=100.0,
        quantity_unit="m3",
    )

    act2 = Activity(
        id="act-civ-1002",
        project_id="proj-agent-demo",
        wbs_id="wbs-agent-1",
        activity_code="CIV-1002",
        name="Foundation Concrete Pour F-205",
        status="NOT_STARTED",
        planned_start=datetime(2024, 8, 1, 8, 0),
        planned_finish=datetime(2024, 9, 30, 17, 0),
        original_duration=60.0,
        percent_complete=0.0,
        planned_quantity=100.0,
        quantity_unit="m3",
    )

    act3 = Activity(
        id="act-str-2001",
        project_id="proj-agent-demo",
        wbs_id="wbs-agent-2",
        activity_code="STR-2001",
        name="Structural Steel Erection Area B",
        status="IN_PROGRESS",
        planned_start=datetime(2024, 9, 1, 8, 0),
        planned_finish=datetime(2024, 11, 30, 17, 0),
        actual_start=datetime(2024, 9, 5, 8, 0),
        original_duration=90.0,
        percent_complete=40.0,
        planned_quantity=200.0,
        quantity_unit="tons",
    )

    wbs3 = WBSNode(
        id="wbs-agent-3",
        project_id="proj-agent-demo",
        code="WBS-MEC",
        name="Mechanical Equipment",
    )
    db_session.add(wbs3)

    act_mec1 = Activity(
        id="act-mec-1003",
        project_id="proj-agent-demo",
        wbs_id="wbs-agent-3",
        activity_code="MEC-1003",
        name="Mechanical Pump Installation Unit 3",
        discipline="Mechanical",
        location_code="Unit 3",
        status="NOT_STARTED",
        planned_start=datetime(2024, 9, 1, 8, 0),
        planned_finish=datetime(2024, 11, 30, 17, 0),
        original_duration=90.0,
        percent_complete=0.0,
        planned_quantity=1.0,
        quantity_unit="ea",
    )

    act_mec2 = Activity(
        id="act-mec-1004",
        project_id="proj-agent-demo",
        wbs_id="wbs-agent-3",
        activity_code="MEC-1004",
        name="Mechanical Pump Installation Unit 4",
        discipline="Mechanical",
        location_code="Unit 4",
        status="NOT_STARTED",
        planned_start=datetime(2024, 9, 1, 8, 0),
        planned_finish=datetime(2024, 11, 30, 17, 0),
        original_duration=90.0,
        percent_complete=0.0,
        planned_quantity=1.0,
        quantity_unit="ea",
    )

    db_session.add_all([act1, act2, act3, act_mec1, act_mec2])
    db_session.commit()
    return proj


# =========================================================================
# TEST SUITE A: Conversational Parser & Intent Classification
# =========================================================================

def test_intent_classification_and_offline_fallback():
    parser = ConversationalParser(gemini_api_key=None)
    ref_date = datetime(2024, 10, 1)

    # 1. Information query
    res_info = parser.parse("What is the status of CIV-1001?", reference_date=ref_date)
    assert res_info.intent == "INFORMATION_QUERY"
    assert res_info.reported_activity_code == "CIV-1001"

    # 2. Direct percentage update request
    res_pct = parser.parse("Update CIV-1001 to 80%.", reference_date=ref_date)
    assert res_pct.intent == "PROGRESS_UPDATE_REQUEST"
    assert res_pct.reported_activity_code == "CIV-1001"
    assert res_pct.override_percent == 80.0

    # 3. Progress report
    res_prog = parser.parse("We poured 35 m3 of concrete today for CIV-1001.", reference_date=ref_date)
    assert res_prog.intent == "PROGRESS_REPORT"
    assert res_prog.quantity == 35.0
    assert res_prog.unit == "m3"
    assert res_prog.reported_activity_code == "CIV-1001"
    assert res_prog.execution_date == "today"

    # 4. Clarification answer (activity code in clarification turn)
    res_clar = parser.parse("CIV-1002", reference_date=ref_date, is_clarification_turn=True)
    assert res_clar.intent == "CLARIFICATION_RESPONSE"
    assert res_clar.reported_activity_code == "CIV-1002"


# =========================================================================
# TEST SUITE B: Date Resolution
# =========================================================================

def test_date_resolution():
    ref_date = datetime(2024, 10, 1)  # A Tuesday

    dt_today = ConversationalParser.resolve_date("today", reference_date=ref_date)
    assert dt_today == datetime(2024, 10, 1)

    dt_yesterday = ConversationalParser.resolve_date("yesterday", reference_date=ref_date)
    assert dt_yesterday == datetime(2024, 9, 30)

    dt_explicit = ConversationalParser.resolve_date("2024-09-15", reference_date=ref_date)
    assert dt_explicit == datetime(2024, 9, 15)


# =========================================================================
# TEST SUITE C: Safe Non-Finalizing Evaluation (MatchingService)
# =========================================================================

def test_evaluate_event_for_agent_preserves_draft_status(
    agent_test_project: Project, db_session: Session
):
    # Create a draft conversational event
    event = ExecutionEvent(
        id="evt-safe-eval-1",
        project_id=agent_test_project.id,
        source_type="CONVERSATIONAL",
        reported_activity_code="CIV-1001",
        verbatim_excerpt="Poured 35 m3 for CIV-1001",
        description="Poured 35 m3 for CIV-1001",
        status="DRAFT",
        quantity=35.0,
        unit="m3",
        execution_date=datetime(2024, 10, 1),
    )
    db_session.add(event)
    db_session.commit()

    # Safe agent evaluation
    res = MatchingService.evaluate_event_for_agent(db=db_session, event=event)

    # Invariants:
    # 1. Status remains DRAFT
    assert event.status == "DRAFT"
    # 2. Matched activity is identified
    assert res.selected_candidate is not None
    assert res.selected_candidate.activity_id == "act-civ-1001"
    assert res.route == "AUTO_LINK"
    # 3. Top candidate score breakdown is available
    assert len(res.all_candidates) > 0
    top_cand = res.all_candidates[0]
    assert top_cand.activity_id == "act-civ-1001"
    assert top_cand.match_score >= 0.85

    # Check that the database event record still has status DRAFT
    db_session.expire_all()
    reloaded_event = db_session.query(ExecutionEvent).filter_by(id="evt-safe-eval-1").first()
    assert reloaded_event.status == "DRAFT"


# =========================================================================
# TEST SUITE D: Quantity Semantics & Schedule Update
# =========================================================================

def test_incremental_and_cumulative_quantity_semantics(
    agent_test_project: Project, db_session: Session
):
    act = db_session.query(Activity).filter_by(id="act-civ-1001").first()
    assert act.percent_complete == 0.0
    assert act.planned_quantity == 100.0

    # 1. Apply Incremental Progress: 35 m3 out of 100 m3 = 35%
    evt1 = ExecutionEvent(
        id="evt-quant-1",
        project_id=agent_test_project.id,
        matched_activity_id=act.id,
        verbatim_excerpt="Poured 35 m3",
        description="Poured 35 m3",
        status="APPROVED",
        quantity=35.0,
        unit="m3",
        execution_date=datetime(2024, 10, 1),
    )
    db_session.add(evt1)
    db_session.commit()

    res1 = ScheduleUpdateService.apply_event_progress(
        db=db_session,
        event_id=evt1.id,
        user_id="supervisor_test",
        quantity_semantics="INCREMENTAL",
        commit=True,
    )
    assert res1.percent_complete == 35.0
    assert res1.status == "IN_PROGRESS"

    # Verify ledger entry
    ledger1 = (
        db_session.query(ActualProgressLedger)
        .filter_by(execution_event_id=evt1.id)
        .first()
    )
    assert ledger1.installed_quantity == 35.0
    assert ledger1.incremental_percent == 35.0
    assert ledger1.cumulative_percent == 35.0

    # Verify idempotency when re-applying the same event with implicit matched_activity_id (activity_id=None)
    res1_reapply = ScheduleUpdateService.apply_event_progress(
        db=db_session,
        event_id=evt1.id,
        user_id="supervisor_test",
        quantity_semantics="INCREMENTAL",
        commit=True,
    )
    assert res1_reapply.percent_complete == 35.0
    ledger_count = (
        db_session.query(ActualProgressLedger)
        .filter_by(execution_event_id=evt1.id)
        .count()
    )
    assert ledger_count == 1

    # 2. Cumulative Progress: Report cumulative 60 m3 (delta = 60 - 35 = 25 m3 -> +25% = 60%)
    evt2 = ExecutionEvent(
        id="evt-quant-2",
        project_id=agent_test_project.id,
        matched_activity_id=act.id,
        verbatim_excerpt="Cumulative poured 60 m3",
        description="Cumulative poured 60 m3",
        status="APPROVED",
        quantity=60.0,
        unit="m3",
        execution_date=datetime(2024, 10, 2),
    )
    db_session.add(evt2)
    db_session.commit()

    res2 = ScheduleUpdateService.apply_event_progress(
        db=db_session,
        event_id=evt2.id,
        user_id="supervisor_test",
        quantity_semantics="CUMULATIVE",
        commit=True,
    )
    assert res2.percent_complete == 60.0

    # 3. Decreasing Cumulative Progress: Report cumulative 50 m3 (less than previous 60) -> REJECTED
    evt3 = ExecutionEvent(
        id="evt-quant-3",
        project_id=agent_test_project.id,
        matched_activity_id=act.id,
        verbatim_excerpt="Cumulative poured 50 m3",
        description="Cumulative poured 50 m3",
        status="APPROVED",
        quantity=50.0,
        unit="m3",
        execution_date=datetime(2024, 10, 3),
    )
    db_session.add(evt3)
    db_session.commit()

    with pytest.raises(ValidationException) as exc_info:
        ScheduleUpdateService.apply_event_progress(
            db=db_session,
            event_id=evt3.id,
            user_id="supervisor_test",
            quantity_semantics="CUMULATIVE",
            commit=True,
        )
    assert "cannot decrease progress" in str(exc_info.value)


# =========================================================================
# TEST SUITE E: Proposal Creation, Expiry, Baseline Conflict, and Confirmation
# =========================================================================

def test_proposal_lifecycle_and_conflict_handling(
    agent_test_project: Project, db_session: Session
):
    act = db_session.query(Activity).filter_by(id="act-str-2001").first()

    # Create conversation
    conv = Conversation(
        id="conv-prop-test",
        project_id=agent_test_project.id,
        user_id="lead_planner",
        status="ACTIVE",
    )
    db_session.add(conv)
    db_session.commit()

    # 1. Stage proposal: Update STR-2001 to 75%
    evt = ExecutionEvent(
        id="evt-prop-1",
        project_id=agent_test_project.id,
        conversation_id=conv.id,
        source_type="CONVERSATIONAL",
        reported_activity_code="STR-2001",
        verbatim_excerpt="Update STR-2001 to 75%",
        description="Update STR-2001 to 75%",
        status="DRAFT",
        execution_date=datetime(2024, 10, 1),
    )
    db_session.add(evt)
    db_session.commit()

    prop = TimeAgentService.stage_proposal(
        db=db_session,
        conversation=conv,
        event=evt,
        activity=act,
        proposed_percent=75.0,
        proposed_status="IN_PROGRESS",
        quantity_semantics="INCREMENTAL",
    )
    assert prop.status == "PENDING"
    assert prop.baseline_percent == 40.0
    assert prop.proposed_percent == 75.0
    assert prop.expires_at > datetime.now(timezone.utc).replace(tzinfo=None)

    # 2. Confirm Proposal
    res_confirm = TimeAgentService.confirm_proposal(
        db=db_session,
        project_id=agent_test_project.id,
        conversation_id=conv.id,
        proposal_id=prop.id,
        caller_id="lead_planner",
    )
    assert res_confirm.status == "APPLIED"
    assert res_confirm.new_percent == 75.0
    assert res_confirm.activity_code == "STR-2001"

    # Proposal state must be CONSUMED
    db_session.expire_all()
    consumed_prop = db_session.query(UpdateProposal).filter_by(id=prop.id).first()
    assert consumed_prop.status == "CONSUMED"
    assert consumed_prop.consumed_at is not None

    # 3. Re-confirming an already consumed proposal raises 409
    with pytest.raises(Exception) as exc_con:
        TimeAgentService.confirm_proposal(
            db=db_session,
            project_id=agent_test_project.id,
            conversation_id=conv.id,
            proposal_id=prop.id,
            caller_id="lead_planner",
        )
    assert "PROPOSAL_ALREADY_CONSUMED" in str(exc_con.value) or "409" in str(exc_con.value)


def test_stale_proposal_conflict(agent_test_project: Project, db_session: Session):
    act = db_session.query(Activity).filter_by(id="act-civ-1001").first()

    conv = Conversation(
        id="conv-stale-test",
        project_id=agent_test_project.id,
        user_id="planner_1",
        status="ACTIVE",
    )
    db_session.add(conv)
    db_session.commit()

    evt = ExecutionEvent(
        id="evt-stale-1",
        project_id=agent_test_project.id,
        conversation_id=conv.id,
        source_type="CONVERSATIONAL",
        verbatim_excerpt="Update CIV-1001 to 50%",
        description="Update CIV-1001 to 50%",
        status="DRAFT",
        execution_date=datetime(2024, 10, 1),
    )
    db_session.add(evt)
    db_session.commit()

    prop = TimeAgentService.stage_proposal(
        db=db_session,
        conversation=conv,
        event=evt,
        activity=act,
        proposed_percent=50.0,
        proposed_status="IN_PROGRESS",
    )
    assert prop.baseline_percent == 0.0

    # Simulate concurrent external update to the activity percent
    act.percent_complete = 20.0
    db_session.commit()

    # Attempt confirmation -> must detect baseline mismatch (stale proposal)
    with pytest.raises(Exception) as exc_stale:
        TimeAgentService.confirm_proposal(
            db=db_session,
            project_id=agent_test_project.id,
            conversation_id=conv.id,
            proposal_id=prop.id,
            caller_id="planner_1",
        )
    assert "STALE_ACTIVITY_BASELINE" in str(exc_stale.value) or "409" in str(exc_stale.value)


# =========================================================================
# TEST SUITE F: End-to-End Conversational Flow & API Integration
# =========================================================================

def test_conversational_clarification_and_confirmation_flow(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    project_id = agent_test_project.id

    # 1. Start a new conversation
    res = client.post(f"/api/v1/projects/{project_id}/agent/conversations")
    assert res.status_code == 200
    conv_data = res.json()
    conv_id = conv_data["conversation_id"]
    assert conv_data["project_id"] == project_id
    assert conv_data["status"] == "ACTIVE"

    # 2. Ambiguous report without exact activity: "We poured 35 m3 today."
    # Since both CIV-1001 and CIV-1002 are concrete pours, the system should generate clarification or matching candidates
    res_msg1 = client.post(
        f"/api/v1/projects/{project_id}/agent/conversations/{conv_id}/messages",
        json={"content": "We poured 35 m3 today."},
        headers={"X-User-ID": "supervisor_bob", "X-User-Role": "SUPERVISOR"},
    )
    assert res_msg1.status_code == 200
    resp1 = res_msg1.json()
    assert resp1["sender"] == "AGENT"
    assert resp1["action_card"] is None  # Conversational clarification without candidate list on turn 1
    assert len(resp1["reply_text"]) > 0

    # Check conversation active event is retained
    db_session.expire_all()
    conv = db_session.query(Conversation).filter_by(id=conv_id).first()
    assert conv.active_event_id is not None
    orig_event_id = conv.active_event_id

    # 3. Supervisor clarifies: "CIV-1001"
    res_msg2 = client.post(
        f"/api/v1/projects/{project_id}/agent/conversations/{conv_id}/messages",
        json={"content": "CIV-1001"},
        headers={"X-User-ID": "supervisor_bob", "X-User-Role": "SUPERVISOR"},
    )
    assert res_msg2.status_code == 200
    resp2 = res_msg2.json()

    # Same event should be enriched, not duplicated
    db_session.expire_all()
    conv = db_session.query(Conversation).filter_by(id=conv_id).first()
    assert conv.active_event_id == orig_event_id

    event = db_session.query(ExecutionEvent).filter_by(id=orig_event_id).first()
    assert event.reported_activity_code == "CIV-1001"

    # Proposal card should now be produced
    assert resp2["action_card"] is not None
    card = resp2["action_card"]
    print("DEBUG CARD:", card)
    assert card["type"] == "PROPOSAL_CONFIRMATION"
    assert card["activity_code"] == "CIV-1001"
    proposal_id = card["proposal_id"]
    assert proposal_id is not None

    # Verify activity in DB has NOT mutated yet (no unconfirmed write)
    db_session.expire_all()
    act = db_session.query(Activity).filter_by(id="act-civ-1001").first()
    assert act.percent_complete == 0.0

    # 4. Supervisor clicks [Confirm & Apply]
    res_conf = client.post(
        f"/api/v1/projects/{project_id}/agent/conversations/{conv_id}/confirm",
        json={"proposal_id": proposal_id},
        headers={"X-User-ID": "supervisor_bob", "X-User-Role": "SUPERVISOR"},
    )
    assert res_conf.status_code == 200
    conf_data = res_conf.json()
    assert conf_data["status"] == "APPLIED"
    assert conf_data["new_percent"] == 35.0
    assert conf_data["activity_code"] == "CIV-1001"

    # 5. Verify authoritative database mutations:
    db_session.expire_all()
    act_updated = db_session.query(Activity).filter_by(id="act-civ-1001").first()
    assert act_updated.percent_complete == 35.0
    assert act_updated.status == "IN_PROGRESS"
    assert act_updated.actual_start is not None

    # Verify ActualProgressLedger record
    ledger = (
        db_session.query(ActualProgressLedger)
        .filter_by(activity_id="act-civ-1001")
        .first()
    )
    assert ledger is not None
    assert ledger.installed_quantity == 35.0
    assert ledger.cumulative_percent == 35.0

    # Verify ScheduleAuditLog record
    audit = (
        db_session.query(ScheduleAuditLog)
        .filter_by(activity_id="act-civ-1001")
        .first()
    )
    assert audit is not None
    assert audit.user_id == "supervisor_bob"
    assert audit.action == "TIME_AGENT_CONVERSATIONAL_UPDATE"

    # Verify DomainOutbox event emitted
    outbox = (
        db_session.query(DomainOutbox)
        .filter_by(aggregate_id="act-civ-1001")
        .first()
    )
    assert outbox is not None
    assert outbox.event_type == "SCHEDULE_PROGRESS_UPDATED"


def test_gemini_credential_isolation(monkeypatch):
    """
    Verifies that Time Agent and Extraction Service can resolve credentials independently.
    """
    from app.services.agent_parser import ConversationalParser
    from app.services.extraction_service import ExtractionService

    # 1. Clear all keys
    monkeypatch.delenv("TIME_AGENT_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("EXTRACTION_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    p_none = ConversationalParser()
    assert p_none.gemini_api_key is None
    assert ExtractionService.extract_with_llm("test", "test.pdf") is None

    # 2. Configure Time Agent only
    monkeypatch.setenv("TIME_AGENT_GEMINI_API_KEY", "test-time-agent-key")
    p_time = ConversationalParser()
    assert p_time.gemini_api_key == "test-time-agent-key"
    # Extraction service remains None because it does not use TIME_AGENT_GEMINI_API_KEY
    assert ExtractionService.extract_with_llm("test", "test.pdf") is None

    # 3. Configure Extraction Service only
    monkeypatch.delenv("TIME_AGENT_GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("EXTRACTION_GEMINI_API_KEY", "test-extraction-key")
    p_none2 = ConversationalParser()
    assert p_none2.gemini_api_key is None

    # 4. Both configured independently
    monkeypatch.setenv("TIME_AGENT_GEMINI_API_KEY", "key-agent-123")
    monkeypatch.setenv("EXTRACTION_GEMINI_API_KEY", "key-extract-456")
    p_both = ConversationalParser()
    assert p_both.gemini_api_key == "key-agent-123"

    # 5. Shared fallback when dedicated keys are absent
    monkeypatch.delenv("TIME_AGENT_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("EXTRACTION_GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "shared-fallback-key")
    p_fallback = ConversationalParser()
    assert p_fallback.gemini_api_key == "shared-fallback-key"


def test_parse_with_gemini_prompt_formatting(monkeypatch):
    """
    Verifies that parse_with_gemini does not fail with ValueError due to unescaped f-string braces.
    """
    from app.services.agent_parser import ConversationalParser
    import httpx

    monkeypatch.setenv("TIME_AGENT_GEMINI_API_KEY", "dummy-api-key")

    def mock_post(url, *args, **kwargs):
        class MockResp:
            status_code = 200
            def json(self):
                return {
                    "candidates": [{
                        "content": {
                            "parts": [{
                                "text": '{"intent": "BULK_PROGRESS_REPORT", "is_bulk": true, "bulk_scope": {"discipline": "Electrical"}, "status_reported": "COMPLETED"}'
                            }]
                        }
                    }]
                }
        return MockResp()

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    parsed = ConversationalParser.parse_with_gemini(
        text="we have completed all the electrical activities update all of them",
        project_data_date_str="2024-09-30",
        active_activity_code=None,
        is_clarification_turn=False,
    )
    assert parsed is not None
    assert parsed.intent == "BULK_PROGRESS_REPORT"
    assert parsed.is_bulk is True
    assert parsed.bulk_scope == {"discipline": "Electrical"}


# =========================================================================
# TEST SUITE H: Schedule-Scoped Project Isolation & Chat History
# =========================================================================

def test_schedule_scoped_project_isolation_and_chat_history(
    client: TestClient, db_session: Session
):
    """
    Validates the 6 mandatory project-isolation criteria:
    Test 1: Project A conversations listing returns only A1, A2; Project B returns only B1.
    Test 2: Accessing Project B conversation via Project A URL path is rejected (404).
    Test 3: Search in Project A never returns Project B conversations.
    Test 4: Messages sent to Project A conversation remain attached to Project A.
    Test 5: Creating new conversation in Project A starts completely clean (no inherited messages).
    Test 6: New conversation in Project B has zero visibility of Project A messages.
    """
    # Setup Project A
    proj_a = Project(
        id="proj-alpha-iso",
        project_code="PROJECT_A",
        name="Schedule Alpha",
        planned_start=datetime(2024, 1, 1, 8, 0),
        planned_finish=datetime(2025, 12, 31, 17, 0),
        data_date=datetime(2024, 10, 1, 0, 0),
    )
    # Setup Project B
    proj_b = Project(
        id="proj-beta-iso",
        project_code="PROJECT_B",
        name="Schedule Beta",
        planned_start=datetime(2024, 1, 1, 8, 0),
        planned_finish=datetime(2025, 12, 31, 17, 0),
        data_date=datetime(2024, 10, 1, 0, 0),
    )
    db_session.add_all([proj_a, proj_b])
    db_session.commit()

    # Step 1: Create Conversation A1 in Project A
    res_a1 = client.post(
        f"/api/v1/projects/{proj_a.id}/agent/conversations",
        json={"force_new": True, "title": "F-204 Concrete Pour"},
    )
    assert res_a1.status_code == 200
    conv_a1 = res_a1.json()
    a1_id = conv_a1["conversation_id"]

    # Post message in A1
    msg_a1 = client.post(
        f"/api/v1/projects/{proj_a.id}/agent/conversations/{a1_id}/messages",
        json={"content": "We poured 35 m3 concrete for F-204 today."},
    )
    assert msg_a1.status_code == 200

    # Step 2: Create Conversation A2 in Project A
    res_a2 = client.post(
        f"/api/v1/projects/{proj_a.id}/agent/conversations",
        json={"force_new": True, "title": "Cable Tray Progress"},
    )
    assert res_a2.status_code == 200
    conv_a2 = res_a2.json()
    a2_id = conv_a2["conversation_id"]

    # Post message in A2
    msg_a2 = client.post(
        f"/api/v1/projects/{proj_a.id}/agent/conversations/{a2_id}/messages",
        json={"content": "Installed 18 meters of cable tray today."},
    )
    assert msg_a2.status_code == 200

    # Step 3: Create Conversation B1 in Project B
    res_b1 = client.post(
        f"/api/v1/projects/{proj_b.id}/agent/conversations",
        json={"force_new": True, "title": "Pump Installation"},
    )
    assert res_b1.status_code == 200
    conv_b1 = res_b1.json()
    b1_id = conv_b1["conversation_id"]

    # Post message in B1 (contains "pump")
    msg_b1 = client.post(
        f"/api/v1/projects/{proj_b.id}/agent/conversations/{b1_id}/messages",
        json={"content": "Installed centrifugal water pump today."},
    )
    assert msg_b1.status_code == 200

    # -------------------------------------------------------------
    # Test 1: GET Project A conversations -> only A1 and A2
    # -------------------------------------------------------------
    list_a = client.get(f"/api/v1/projects/{proj_a.id}/agent/conversations")
    assert list_a.status_code == 200
    items_a = list_a.json()
    ids_a = {item["id"] for item in items_a}
    assert ids_a == {a1_id, a2_id}
    assert b1_id not in ids_a

    list_b = client.get(f"/api/v1/projects/{proj_b.id}/agent/conversations")
    assert list_b.status_code == 200
    items_b = list_b.json()
    ids_b = {item["id"] for item in items_b}
    assert ids_b == {b1_id}
    assert a1_id not in ids_b
    assert a2_id not in ids_b

    # -------------------------------------------------------------
    # Test 2: Attempt to access Project B conversation via Project A path
    # -------------------------------------------------------------
    res_cross = client.get(f"/api/v1/projects/{proj_a.id}/agent/conversations/{b1_id}")
    assert res_cross.status_code == 404
    assert "not found in project" in res_cross.json()["detail"].lower()

    # -------------------------------------------------------------
    # Test 3: Search Project A for "pump" -> never returns Project B
    # -------------------------------------------------------------
    search_a_pump = client.get(f"/api/v1/projects/{proj_a.id}/agent/conversations?q=pump")
    assert search_a_pump.status_code == 200
    results_a_pump = search_a_pump.json()
    assert len(results_a_pump) == 0  # "pump" only exists in Project B!

    search_b_pump = client.get(f"/api/v1/projects/{proj_b.id}/agent/conversations?q=pump")
    assert search_b_pump.status_code == 200
    results_b_pump = search_b_pump.json()
    assert len(results_b_pump) == 1
    assert results_b_pump[0]["id"] == b1_id

    # Search Project A for "concrete" -> returns A1
    search_a_concrete = client.get(f"/api/v1/projects/{proj_a.id}/agent/conversations?q=concrete")
    assert search_a_concrete.status_code == 200
    results_a_concrete = search_a_concrete.json()
    assert any(item["id"] == a1_id for item in results_a_concrete)

    # -------------------------------------------------------------
    # Test 4: Open Project A conversation -> send message -> remains in A
    # -------------------------------------------------------------
    follow_up = client.post(
        f"/api/v1/projects/{proj_a.id}/agent/conversations/{a1_id}/messages",
        json={"content": "F-204 inspection completed."},
    )
    assert follow_up.status_code == 200
    get_a1 = client.get(f"/api/v1/projects/{proj_a.id}/agent/conversations/{a1_id}")
    assert get_a1.status_code == 200
    a1_history = get_a1.json()["history"]
    contents = [m["content"] for m in a1_history]
    assert any("F-204 inspection completed." in c for c in contents)

    # -------------------------------------------------------------
    # Test 5: Create new conversation in Project A -> starts clean
    # -------------------------------------------------------------
    res_a3 = client.post(
        f"/api/v1/projects/{proj_a.id}/agent/conversations",
        json={"force_new": True},
    )
    assert res_a3.status_code == 200
    conv_a3 = res_a3.json()
    assert conv_a3["history"] == []  # Completely clean, no messages inherited!
    assert conv_a3["active_event_id"] is None

    # -------------------------------------------------------------
    # Test 6: Check conversation in Project B -> no Project A messages visible
    # -------------------------------------------------------------
    get_b1 = client.get(f"/api/v1/projects/{proj_b.id}/agent/conversations/{b1_id}")
    assert get_b1.status_code == 200
    b1_history = get_b1.json()["history"]
    b1_contents = [m["content"] for m in b1_history]
    assert not any("F-204" in c for c in b1_contents)
    assert not any("cable tray" in c.lower() for c in b1_contents)


def test_deterministic_conversation_title_generation():
    """Validates deterministic 3-7 word conversation title generation."""
    # Pattern 1: Concrete Pour with tag
    t1 = TimeAgentService._generate_conversation_title(
        "We poured 35 m3 of concrete for F-204 today."
    )
    assert "F-204" in t1 and "Concrete" in t1

    # Pattern 2: Progress update with activity tag
    t2 = TimeAgentService._generate_conversation_title(
        "Update cable tray CT-07 to 80%."
    )
    assert "CT-07" in t2

    # Pattern 3: Information query
    t3 = TimeAgentService._generate_conversation_title(
        "Show upcoming civil activities."
    )
    assert "Upcoming Civil Activities" in t3

    # Pattern 4: Pump Installation
    t4 = TimeAgentService._generate_conversation_title(
        "Installed water pump today."
    )
    assert "Pump Installation" in t4


# =========================================================================
# TEST SUITE H: Dynamic Multi-Choice Clarification & Explicit Bulk Intent
# =========================================================================

def test_dynamic_clarification_two_candidates_preserves_ab_and_none_of_these(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    Validates that when candidates compete with close scores,
    turns 1 and 2 ask focused conversational clarification without candidate lists,
    and turn 3 provides the fallback CLARIFICATION_CHOICE card with 'None of these'.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    # Turn 1: Conversational distinguishing question, NO candidate card
    res_msg = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "We completed the foundation work today."},
    )
    assert res_msg.status_code == 200
    data = res_msg.json()
    assert data["action_card"] is None
    assert len(data["reply_text"]) > 0

    # Turn 2: Still ambiguous, conversational
    res_msg2 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Civil foundation"},
    )
    assert res_msg2.status_code == 200
    assert res_msg2.json()["action_card"] is None

    # Turn 3: Reaches turn >= 3 fallback threshold -> shows CLARIFICATION_CHOICE card
    res_msg3 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Foundation pour"},
    )
    assert res_msg3.status_code == 200
    data3 = res_msg3.json()
    card = data3["action_card"]
    assert card is not None
    assert card["type"] == "CLARIFICATION_CHOICE"
    options = card["options"]
    assert len(options) >= 3  # Candidates + None of these
    labels = [o["label"] for o in options]
    values = [o["value"] for o in options]
    assert any("CIV-1001" in l for l in labels)
    assert any("CIV-1002" in l for l in labels)
    assert "NONE_OF_THESE" in values


def test_dynamic_clarification_multi_candidates_and_none_of_these(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    Validates that when 3-4 candidates compete, turns 1 and 2 ask focused conversational
    questions without showing candidate cards, and turn >= 3 provides the fallback
    multi-choice clarification card with candidates plus 'None of these'.
    """
    act3 = Activity(
        id="act-civ-1003",
        project_id=agent_test_project.id,
        wbs_id="wbs-agent-1",
        activity_code="CIV-1003",
        name="Foundation Concrete Pour F-206",
        status="NOT_STARTED",
        percent_complete=0.0,
    )
    act4 = Activity(
        id="act-civ-1004",
        project_id=agent_test_project.id,
        wbs_id="wbs-agent-1",
        activity_code="CIV-1004",
        name="Foundation Concrete Pour F-207",
        status="NOT_STARTED",
        percent_complete=0.0,
    )
    db_session.add_all([act3, act4])
    db_session.commit()

    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    # Turn 1: Conversational question, no candidate buttons
    res_msg = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "We poured foundation concrete today."},
    )
    assert res_msg.status_code == 200
    data = res_msg.json()
    assert data["action_card"] is None
    assert len(data["reply_text"]) > 0

    # Turn 2: Still ambiguous, conversational
    res_msg2 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Civil foundation"},
    )
    assert res_msg2.status_code == 200
    assert res_msg2.json()["action_card"] is None

    # Turn 3: Reaches turn >= 3 -> fallback candidate choice card
    res_msg3 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Concrete pour"},
    )
    assert res_msg3.status_code == 200
    data3 = res_msg3.json()
    card = data3["action_card"]
    assert card is not None
    assert card["type"] == "CLARIFICATION_CHOICE"
    options = card["options"]
    assert len(options) >= 4  # 3 or 4 candidates + None of these
    values = [o["value"] for o in options]
    assert "NONE_OF_THESE" in values


def test_clarification_none_of_these_handling(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    Validates that selecting 'None of these' prompts the user to provide an explicit code.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    # Trigger clarification
    client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "We completed foundation work."},
    )

    # Respond with None of these
    res_none = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "None of these"},
    )
    assert res_none.status_code == 200
    reply = res_none.json()["reply_text"]
    assert "specify" in reply.lower() and "activity code" in reply.lower()


def test_bulk_intent_detection_and_scoped_proposal_presentation(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    Validates that 'we have completed all the electrical activities update all of them':
    1. Extracts BULK_PROGRESS_REPORT intent.
    2. Queries authoritative DB for matching activities (does not let LLM hallucinate).
    3. Presents a BULK_SCOPE_PROPOSAL action card with total count and update button.
    """
    # Create 3 electrical activities in the project
    ele1 = Activity(
        id="act-ele-1001",
        project_id=agent_test_project.id,
        activity_code="ELE-1001",
        name="Cable Tray Installation Level 1",
        discipline="Electrical",
        status="NOT_STARTED",
        percent_complete=0.0,
    )
    ele2 = Activity(
        id="act-ele-1002",
        project_id=agent_test_project.id,
        activity_code="ELE-1002",
        name="Cable Tray Installation Level 2",
        discipline="Electrical",
        status="NOT_STARTED",
        percent_complete=0.0,
    )
    ele3 = Activity(
        id="act-ele-1003",
        project_id=agent_test_project.id,
        activity_code="ELE-1003",
        name="Main Switchgear Wiring",
        discipline="Electrical",
        status="NOT_STARTED",
        percent_complete=0.0,
    )
    db_session.add_all([ele1, ele2, ele3])
    db_session.commit()

    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    res_msg = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "we have completed all the electrical activities update all of them"},
    )
    assert res_msg.status_code == 200
    data = res_msg.json()

    # Agent should identify all 3 activities and present a BULK_SCOPE_PROPOSAL card
    card = data["action_card"]
    assert card is not None
    assert card["type"] == "BULK_SCOPE_PROPOSAL"
    assert card["bulk_count"] == 3
    assert len(card["bulk_activities"]) == 3
    act_codes = [a["activity_code"] for a in card["bulk_activities"]]
    assert "ELE-1001" in act_codes
    assert "ELE-1002" in act_codes
    assert "ELE-1003" in act_codes

    # Options should have CONFIRM_ALL_BULK
    values = [o["value"] for o in card["options"]]
    assert "CONFIRM_ALL_BULK" in values
    assert "Cancel" in [o["label"] for o in card["options"]]


def test_clarification_turn_all_of_them_interception(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    Validates that when the agent asks a clarification question between activities,
    and the user replies 'all of them' or 'update all of them',
    the agent intercepts this and transitions to the bulk scope workflow instead of looping!
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    # Trigger clarification between civil foundation activities
    res1 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Foundation pour completed."},
    )
    assert res1.json().get("action_card") is None
    assert len(res1.json()["reply_text"]) > 0

    # Supervisor responds 'update all of them'
    res_bulk = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "update all of them"},
    )
    assert res_bulk.status_code == 200
    data_bulk = res_bulk.json()

    # Must NOT re-ask between 2 choices! Must transition to BULK_SCOPE_PROPOSAL
    card = data_bulk["action_card"]
    assert card is not None
    assert card["type"] == "BULK_SCOPE_PROPOSAL"
    assert card["bulk_count"] >= 2


def test_bulk_proposal_confirmation_executes_atomic_schedule_mutation(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    Validates that confirming a bulk proposal transactionally mutates each activity in the database,
    creates actual progress ledger records, and creates schedule audit logs in a single atomic commit.
    """
    act_a = db_session.query(Activity).filter_by(id="act-civ-1001").first()
    act_b = db_session.query(Activity).filter_by(id="act-civ-1002").first()
    assert act_a.percent_complete == 0.0
    assert act_b.percent_complete == 0.0

    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    # Confirm bulk update for both activities to 100%
    res_confirm = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/bulk-confirm",
        json={
            "activity_ids": ["act-civ-1001", "act-civ-1002"],
            "action": "CONFIRM",
            "target_percent": 100.0,
            "status_reported": "COMPLETED",
        },
    )
    assert res_confirm.status_code == 200
    confirm_data = res_confirm.json()
    assert confirm_data["status"] == "APPLIED"
    assert confirm_data["updated_count"] == 2

    # Verify both activities in DB are updated
    db_session.refresh(act_a)
    db_session.refresh(act_b)
    assert act_a.percent_complete == 100.0
    assert act_a.status == "COMPLETED"
    assert act_b.percent_complete == 100.0
    assert act_b.status == "COMPLETED"

    # Verify ledger entries created
    ledgers_a = db_session.query(ActualProgressLedger).filter_by(activity_id="act-civ-1001").all()
    ledgers_b = db_session.query(ActualProgressLedger).filter_by(activity_id="act-civ-1002").all()
    assert len(ledgers_a) >= 1
    assert len(ledgers_b) >= 1
    assert ledgers_a[0].cumulative_percent == 100.0
    assert ledgers_b[0].cumulative_percent == 100.0

    # Verify audit logs created with TIME_AGENT_BULK_UPDATE action
    audits = db_session.query(ScheduleAuditLog).filter_by(project_id=agent_test_project.id).all()
    bulk_audits = [a for a in audits if a.action == "TIME_AGENT_BULK_UPDATE"]
    assert len(bulk_audits) >= 2


def test_multilingual_language_detection():
    """
    Validates that ConversationalParser correctly classifies Hindi (Devanagari),
    Hinglish (Romanized Hindi lexicon), and English text.
    """
    # Hindi (Devanagari)
    assert ConversationalParser.detect_language("आज F-204 में 35 क्यूबिक मीटर कंक्रीट डाला है") in ("hi", "hi-IN")
    assert ConversationalParser.detect_language("फाउंडेशन का काम पूरा हो गया") in ("hi", "hi-IN")

    # Hinglish (Romanized Hindi)
    assert ConversationalParser.detect_language("Aaj F-204 mein 35 cubic meter concrete dala hai") in ("hinglish", "hi-IN")
    assert ConversationalParser.detect_language("Foundation ka kaam complete ho gaya") in ("hinglish", "hi-IN")
    assert ConversationalParser.detect_language("Pichle project mein concrete ki productivity kya thi?") in ("hinglish", "hi-IN")

    # English
    assert ConversationalParser.detect_language("We poured 35 m3 concrete today for F-204") in ("en", "en-IN")
    assert ConversationalParser.detect_language("Update CIV-1001 to 80 percent") in ("en", "en-IN")
    assert ConversationalParser.detect_language("Show me upcoming civil activities") in ("en", "en-IN")


def test_hindi_progress_report_matching_and_proposal(
    client: TestClient, agent_test_project: Project
):
    """
    Validates end-to-end processing of a Hindi progress report in Devanagari script:
    'आज F-204 में 35 क्यूबिक मीटर कंक्रीट डाला है'
    Must:
    1. Detect language as Hindi
    2. Extract quantity 35 m3 and location F-204
    3. Deterministically match activity CIV-1001
    4. Stage a proposal with proposed_percent = 35%
    5. Return a respectful, engineering-grade Hindi response acknowledging the update.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    res_msg = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "आज F-204 में 35 क्यूबिक मीटर कंक्रीट डाला है"},
    )
    assert res_msg.status_code == 200
    data = res_msg.json()

    card = data.get("action_card")
    assert card is not None, f"Expected action card, got reply: {data.get('reply_text')}"
    assert card["type"] == "PROPOSAL_CONFIRMATION"
    assert card["activity_code"] == "CIV-1001"
    assert card["proposed_percent"] == 35.0
    assert card["incremental_quantity"] == 35.0

    # Ensure technical codes are preserved verbatim and Hindi acknowledgement is present
    reply = data["reply_text"]
    assert "CIV-1001" in reply
    assert "F-204" in reply
    assert ("समझ गया" in reply or "कंक्रीट" in reply or "35" in reply)


def test_hinglish_progress_report_matching_and_proposal(
    client: TestClient, agent_test_project: Project
):
    """
    Validates end-to-end processing of a Hinglish progress report:
    'Aaj F-204 mein 35 cubic meter concrete dala hai'
    Must:
    1. Detect language as Hinglish
    2. Extract quantity 35 m3 and location F-204
    3. Deterministically match activity CIV-1001
    4. Stage a proposal with proposed_percent = 35%
    5. Return an authoritative Hinglish response preserving technical codes.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    res_msg = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Aaj F-204 mein 35 cubic meter concrete dala hai"},
    )
    assert res_msg.status_code == 200
    data = res_msg.json()

    card = data.get("action_card")
    assert card is not None, f"Expected action card, got reply: {data.get('reply_text')}"
    assert card["type"] == "PROPOSAL_CONFIRMATION"
    assert card["activity_code"] == "CIV-1001"
    assert card["proposed_percent"] == 35.0

    reply = data["reply_text"]
    assert "CIV-1001" in reply
    assert "F-204" in reply
    assert ("Samajh gaya" in reply or "match kiya hai" in reply or "update" in reply)


def test_hindi_hinglish_information_query(
    client: TestClient, agent_test_project: Project
):
    """
    Validates that Hindi / Hinglish status queries return informational details
    about target activities without staging an update proposal or mutating schedule.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    res_msg = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "F-204 ka current progress kya hai?"},
    )
    assert res_msg.status_code == 200
    data = res_msg.json()

    # Informational query should NOT create an unconfirmed proposal
    assert data.get("action_card") is None or data.get("action_card", {}).get("type") == "INFORMATIONAL"
    reply = data["reply_text"]
    assert "CIV-1001" in reply
    assert "0%" in reply or "0.0%" in reply


def test_hindi_hinglish_ambiguous_clarification(
    client: TestClient, agent_test_project: Project
):
    """
    Validates that ambiguous Hindi/Hinglish updates (e.g. 'Foundation ka kaam ho gaya')
    trigger conversational clarification questions in the locked language rather than
    guessing or immediately dumping a numbered candidates list.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    res_msg = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Foundation ka kaam complete ho gaya"},
    )
    assert res_msg.status_code == 200
    data = res_msg.json()

    # Conversational clarification question on turn 1 without candidate buttons
    assert data.get("action_card") is None
    reply = data["reply_text"]
    assert len(reply) > 0
    # Must preserve locked language context (Hinglish/Hindi phrasing)
    assert any(term in reply for term in ["location", "activity", "kaun", "kripya", "batayein", "bata"])


def test_pin_and_unpin_conversation_lifecycle(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    Validates persistent pinning and unpinning of conversations,
    verifying immediate state updates, DB reflection, and order prioritization in listings.
    """
    # Create two conversations
    res1 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True, "title": "Chat 1 Regular"},
    )
    conv1_id = res1.json()["conversation_id"]

    res2 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True, "title": "Chat 2 To Pin"},
    )
    conv2_id = res2.json()["conversation_id"]

    # Initial state should have is_pinned False
    assert res1.json().get("is_pinned") is False
    assert res2.json().get("is_pinned") is False

    # Pin Chat 2
    pin_res = client.patch(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv2_id}/pin",
        json={"is_pinned": True},
    )
    assert pin_res.status_code == 200
    assert pin_res.json()["is_pinned"] is True

    # Check conversation list: pinned chat should be flagged and appear first
    list_res = client.get(f"/api/v1/projects/{agent_test_project.id}/agent/conversations")
    assert list_res.status_code == 200
    items = list_res.json()
    assert len(items) >= 2
    pinned_item = next(i for i in items if i["id"] == conv2_id)
    assert pinned_item["is_pinned"] is True
    # The first item should be the pinned conversation
    assert items[0]["id"] == conv2_id

    # Unpin Chat 2
    unpin_res = client.patch(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv2_id}/pin",
        json={"is_pinned": False},
    )
    assert unpin_res.status_code == 200
    assert unpin_res.json()["is_pinned"] is False

    # Verify list reflects unpinned state
    list_res2 = client.get(f"/api/v1/projects/{agent_test_project.id}/agent/conversations")
    items2 = list_res2.json()
    unpinned_item = next(i for i in items2 if i["id"] == conv2_id)
    assert unpinned_item["is_pinned"] is False


def test_delete_conversation_with_associated_records(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    Validates safe deletion of a conversation, verifying that messages cascade,
    execution events are cleanly unlinked, and subsequent fetches return 404.
    """
    # Create conversation and send a message
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True, "title": "Chat To Delete"},
    )
    conv_id = res_conv.json()["conversation_id"]

    client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Test message before delete"},
    )

    # Verify conversation exists in DB
    conv_in_db = db_session.query(Conversation).filter(Conversation.id == conv_id).first()
    assert conv_in_db is not None
    msg_count = db_session.query(ConversationMessage).filter(ConversationMessage.conversation_id == conv_id).count()
    assert msg_count >= 2  # user message + agent response

    # Delete conversation
    del_res = client.delete(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}"
    )
    assert del_res.status_code == 200
    assert del_res.json()["success"] is True

    # Verify conversation is gone from DB
    conv_after = db_session.query(Conversation).filter(Conversation.id == conv_id).first()
    assert conv_after is None

    # Verify messages were cascaded
    msg_count_after = db_session.query(ConversationMessage).filter(ConversationMessage.conversation_id == conv_id).count()
    assert msg_count_after == 0

    # Fetching deleted conversation should return 404
    fetch_res = client.get(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}"
    )
    assert fetch_res.status_code == 404


def test_project_scoped_isolation_for_pin_and_delete(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    Validates strict project isolation:
    Mutations (pin, delete) attempted against Conversation A using Project B's scope MUST be rejected (404),
    leaving the original conversation completely intact.
    """
    # Create Project B
    proj_b = Project(
        id="proj-agent-other",
        project_code="OTHER_PROJECT",
        name="Other Project",
        planned_start=datetime(2024, 1, 1),
        planned_finish=datetime(2025, 1, 1),
        data_date=datetime(2024, 6, 1),
    )
    db_session.add(proj_b)
    db_session.commit()

    # Create conversation in Project A
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True, "title": "Project A Chat"},
    )
    conv_id = res_conv.json()["conversation_id"]

    # Attempt to pin Conversation A using Project B endpoint
    cross_pin_res = client.patch(
        f"/api/v1/projects/{proj_b.id}/agent/conversations/{conv_id}/pin",
        json={"is_pinned": True},
    )
    assert cross_pin_res.status_code == 404

    # Verify Conversation A was NOT modified
    conv_a = db_session.query(Conversation).filter(Conversation.id == conv_id).first()
    assert conv_a.is_pinned is False

    # Attempt to delete Conversation A using Project B endpoint
    cross_del_res = client.delete(
        f"/api/v1/projects/{proj_b.id}/agent/conversations/{conv_id}"
    )
    assert cross_del_res.status_code == 404

    # Verify Conversation A still exists in Project A
    conv_a_after = db_session.query(Conversation).filter(Conversation.id == conv_id).first()
    assert conv_a_after is not None
    assert conv_a_after.project_id == agent_test_project.id


# =========================================================================
# TEST SUITE K: Strict Conversation Language Lock & Cross-Language Input
# =========================================================================

def test_conversation_language_lock_hindi_with_cross_language_queries(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    TEST 1 & TEST 4:
    Conversation starts in Hindi.
    Subsequent queries in English, Hinglish, Spanish must all receive responses in Hindi.
    Language must remain locked to 'hi'.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    # 1. First meaningful message in Hindi (progress report)
    res_msg1 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "आज F-204 में 35 क्यूबिक मीटर कंक्रीट डाला गया है।"},
    )
    assert res_msg1.status_code == 200
    db_session.expire_all()
    conv = db_session.query(Conversation).filter(Conversation.id == conv_id).first()
    assert conv.language in ("hi", "hi-IN")

    # Verify response contains Hindi proposal structure and unchanged technical tokens
    reply1 = res_msg1.json()["reply_text"]
    assert "F-204" in reply1 or "CIV-1001" in reply1
    assert "35" in reply1
    assert "प्रगति" in reply1 or "प्रस्तावित" in reply1 or "लागू" in reply1

    # 2. Subsequent user query in English
    res_msg2 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "What is the current progress of F-204?"},
    )
    assert res_msg2.status_code == 200
    reply2 = res_msg2.json()["reply_text"]
    # Conversation is locked to Hindi: response must be in Hindi!
    assert "की वर्तमान प्रगति" in reply2 or "प्रगति" in reply2
    assert "The current progress of" not in reply2
    assert "CIV-1001" in reply2 or "F-204" in reply2

    # Verify conversation language in DB still remains 'hi' or 'hi-IN'
    db_session.expire_all()
    conv_recheck = db_session.query(Conversation).filter(Conversation.id == conv_id).first()
    assert conv_recheck.language in ("hi", "hi-IN")

    # 3. Another query in Hinglish
    res_msg3 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "F-204 ka status batao"},
    )
    assert res_msg3.status_code == 200
    reply3 = res_msg3.json()["reply_text"]
    # Must STILL be in Hindi
    assert "प्रगति" in reply3 or "की वर्तमान प्रगति" in reply3


def test_conversation_language_lock_english_with_cross_language_queries(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    TEST 2:
    Conversation starts in English.
    Subsequent user query in Hindi must still receive response in English.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    # 1. First meaningful message in English
    res_msg1 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "We poured 35 m3 of concrete at F-204 today."},
    )
    assert res_msg1.status_code == 200
    db_session.expire_all()
    conv = db_session.query(Conversation).filter(Conversation.id == conv_id).first()
    assert conv.language in ("en", "en-IN")

    # 2. Subsequent user message in Hindi
    res_msg2 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "आज F-204 का progress कितना है?"},
    )
    assert res_msg2.status_code == 200
    reply2 = res_msg2.json()["reply_text"]
    # Must respond in English!
    assert "Current progress for" in reply2 or "progress" in reply2.lower()
    assert "की वर्तमान प्रगति" not in reply2
    assert "CIV-1001" in reply2 or "F-204" in reply2


def test_conversation_language_lock_hinglish_with_cross_language_queries(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    TEST 3:
    Conversation starts in Hinglish.
    Subsequent query in English must receive Hinglish response.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    # 1. First meaningful message in Hinglish
    res_msg1 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Aaj F-204 ka concreting complete ho gaya hai."},
    )
    assert res_msg1.status_code == 200
    db_session.expire_all()
    conv = db_session.query(Conversation).filter(Conversation.id == conv_id).first()
    assert conv.language in ("hinglish", "hi-IN")
    if conv.language == "hi-IN":
        assert conv.language_style == "hinglish"

    # 2. Subsequent user message in English
    res_msg2 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "What is the current progress of F-204?"},
    )
    assert res_msg2.status_code == 200
    reply2 = res_msg2.json()["reply_text"]
    # Must respond in Hinglish
    assert "ka current progress" in reply2 or "hai" in reply2
    assert "की वर्तमान प्रगति" not in reply2


def test_trivial_greeting_does_not_prematurely_lock_language(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    A short trivial greeting ('Hello', 'Hi', 'Ok') does NOT lock the conversation language.
    The language is locked when the first meaningful domain message arrives.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    # 1. Send trivial greeting
    res_hi = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Hello!"},
    )
    assert res_hi.status_code == 200
    db_session.expire_all()
    conv = db_session.query(Conversation).filter(Conversation.id == conv_id).first()
    # Should NOT have locked language on 'Hello!'
    assert conv.language is None

    # 2. Send substantive Hindi message
    res_hindi = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "आज F-204 में 35 क्यूबिक मीटर कंक्रीट डाला गया है।"},
    )
    assert res_hindi.status_code == 200
    db_session.expire_all()
    conv_locked = db_session.query(Conversation).filter(Conversation.id == conv_id).first()
    # Now it is locked to Hindi
    assert conv_locked.language in ("hi", "hi-IN")


def test_explicit_language_switch_request_rejected_in_locked_language(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    Requirement 22:
    If the user explicitly asks 'Please answer this one in English' or 'Switch to English'
    in a Hindi-locked conversation, the Time Agent must refuse the automatic switch and
    explain politely in Hindi that the conversation language is locked.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    # 1. Start in Hindi
    client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "आज F-204 में 35 क्यूबिक मीटर कंक्रीट डाला गया है।"},
    )
    db_session.expire_all()
    conv = db_session.query(Conversation).filter(Conversation.id == conv_id).first()
    assert conv.language in ("hi", "hi-IN")

    # 2. Attempt explicit language switch
    res_switch = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Please switch this conversation to English."},
    )
    assert res_switch.status_code == 200
    reply = res_switch.json()["reply_text"]
    # Refusal and explanation in Hindi
    assert "यह बातचीत हिंदी में लॉक है" in reply
    # Language remains 'hi'
    db_session.expire_all()
    assert db_session.query(Conversation).filter(Conversation.id == conv_id).first().language in ("hi", "hi-IN")


def test_per_conversation_language_isolation(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    TEST 5:
    Conversation A is locked to Hindi.
    Conversation B is locked to English.
    Conversation C is locked to Hinglish.
    Switching between them maintains strictly isolated language states.
    """
    # Create Conv A (Hindi)
    res_a = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True, "title": "Hindi Chat"},
    )
    conv_a = res_a.json()["conversation_id"]
    client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_a}/messages",
        json={"content": "आज F-204 में 35 क्यूबिक मीटर कंक्रीट डाला गया है।"},
    )

    # Create Conv B (English)
    res_b = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True, "title": "English Chat"},
    )
    conv_b = res_b.json()["conversation_id"]
    client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_b}/messages",
        json={"content": "We poured 35 m3 of concrete at F-204 today."},
    )

    # Create Conv C (Hinglish)
    res_c = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True, "title": "Hinglish Chat"},
    )
    conv_c = res_c.json()["conversation_id"]
    client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_c}/messages",
        json={"content": "Aaj F-204 ka concreting complete hua."},
    )

    # Verify GET /conversations returns respective languages
    list_res = client.get(f"/api/v1/projects/{agent_test_project.id}/agent/conversations")
    assert list_res.status_code == 200
    summaries = {c["id"]: c.get("language") for c in list_res.json()}
    assert summaries[conv_a] in ("hi", "hi-IN")
    assert summaries[conv_b] in ("en", "en-IN")
    assert summaries[conv_c] in ("hinglish", "hi-IN")

    # Send identical cross-language prompt to all three: "What is the status of F-204?"
    q = "What is the status of F-204?"

    ans_a = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_a}/messages",
        json={"content": q},
    ).json()["reply_text"]
    assert "की वर्तमान प्रगति" in ans_a or "प्रगति" in ans_a

    ans_b = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_b}/messages",
        json={"content": q},
    ).json()["reply_text"]
    assert "Current progress for" in ans_b or "progress" in ans_b.lower()

    ans_c = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_c}/messages",
        json={"content": q},
    ).json()["reply_text"]
    assert "ka current progress" in ans_c or "hai" in ans_c


def test_proposal_action_card_localized_buttons(
    client: TestClient, agent_test_project: Project
):
    """
    Requirement 12 & 14:
    Proposal cards dynamically contain localized button labels matching the conversation language.
    """
    # Hindi proposal
    res_conv_hi = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_hi = res_conv_hi.json()["conversation_id"]
    msg_hi = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_hi}/messages",
        json={"content": "आज F-204 में 35 क्यूबिक मीटर कंक्रीट डाला गया है।"},
    ).json()

    card_hi = msg_hi.get("action_card")
    assert card_hi is not None
    assert card_hi.get("confirm_label") == "अपडेट की पुष्टि करें"
    assert card_hi.get("reject_label") in ("रद्द करें", "अस्वीकार करें")
    assert card_hi.get("review_label") == "समीक्षा करें"

    # Hinglish proposal
    res_conv_hing = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_hing = res_conv_hing.json()["conversation_id"]
    msg_hing = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_hing}/messages",
        json={"content": "F-204 me aaj 35 m3 concrete daala gaya hai."},
    ).json()

    card_hing = msg_hing.get("action_card")
    assert card_hing is not None
    assert card_hing.get("confirm_label") == "Update Confirm Karein"
    assert card_hing.get("reject_label") == "Reject Karein"


def test_instant_cancellation_bypasses_llm_and_clears_pending_proposals(client, db_session, agent_test_project):
    """
    Verify that explicit cancellation requests ("cancel", "radd karo", "CANCEL") execute
    instantaneously without invoking Gemini LLM, reject pending proposals in DB, and reset conversation.
    """
    import time
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    # 1. Stage a proposal
    res_msg1 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Report 35 m3 concrete poured for F-204 today"},
    )
    assert res_msg1.status_code == 200
    card = res_msg1.json().get("action_card")
    assert card is not None
    assert card["type"] == "PROPOSAL_CONFIRMATION"
    proposal_id = card["proposal_id"]

    # Verify proposal is pending in DB
    db_session.expire_all()
    prop = db_session.query(UpdateProposal).filter(UpdateProposal.id == proposal_id).first()
    assert prop.status == "PENDING"

    # 2. Cancel with "radd karo" - measure execution time
    t0 = time.time()
    res_cancel = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "radd karo"},
    )
    t1 = time.time()
    assert res_cancel.status_code == 200
    # Must be practically instant (< 500ms even in busy test container)
    assert (t1 - t0) < 0.5

    reply_cancel = res_cancel.json()["reply_text"]
    assert "cancel" in reply_cancel.lower() or "रद्द" in reply_cancel

    # Verify proposal and active event in DB are REJECTED / cleared
    db_session.expire_all()
    prop_after = db_session.query(UpdateProposal).filter(UpdateProposal.id == proposal_id).first()
    assert prop_after.status == "REJECTED"

    conv_after = db_session.query(Conversation).filter(Conversation.id == conv_id).first()
    assert conv_after.active_event_id is None
    assert conv_after.status == "ACTIVE"


def test_bulk_proposal_fast_cancellation_and_decision_guard(client, db_session, agent_test_project):
    """
    Verify that when a bulk update proposal is pending:
    1. Random chat messages are blocked with a decision required guidance prompt.
    2. Cancellation executes instantaneously and cancels the bulk proposal.
    """
    import time
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    # 1. Trigger bulk scope proposal via clarification sequence
    res1 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Foundation pour completed."},
    )
    assert res1.json().get("action_card") is None
    assert len(res1.json()["reply_text"]) > 0

    res_bulk = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "update all of them"},
    )
    assert res_bulk.status_code == 200
    bulk_card = res_bulk.json().get("action_card")
    assert bulk_card is not None
    assert bulk_card["type"] == "BULK_SCOPE_PROPOSAL"
    assert bulk_card["proposal_status"] == "PENDING"

    # 2. Try sending an unrelated message while bulk proposal is awaiting decision
    res_chat = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "What is the capital of France?"},
    )
    assert res_chat.status_code == 200
    chat_reply = res_chat.json()["reply_text"]
    assert "bulk update proposal is currently awaiting your decision" in chat_reply or "निर्णय की प्रतीक्षा" in chat_reply

    # 3. Send instant cancellation
    t0 = time.time()
    res_cancel = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "cancel"},
    )
    t1 = time.time()
    assert res_cancel.status_code == 200
    assert (t1 - t0) < 0.5
    assert "cancel" in res_cancel.json()["reply_text"].lower()

    # 4. Verify message metadata in conversation reflects CANCELLED
    db_session.expire_all()
    conv = db_session.query(Conversation).filter(Conversation.id == conv_id).first()
    assert conv.active_event_id is None
    assert conv.status == "ACTIVE"


def test_bulk_proposal_persistent_chat_lock_across_reopen(
    client: TestClient,
    agent_test_project: Project,
    db_session: Session,
):
    """
    Test Case Bulk Test D:
    Verify that when a conversation has a pending bulk update proposal,
    fetching the conversation (as occurs upon browser refresh or reopen)
    reconstructs pending_action with status=PENDING, ensuring the chat remains locked.
    """
    # 1. Start conversation
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    # 2. Trigger bulk scope proposal
    client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Foundation pour completed."},
    )
    res_bulk = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "update all of them"},
    )
    assert res_bulk.status_code == 200

    # 3. Simulate browser refresh by fetching conversation details from backend
    res_get = client.get(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}",
    )
    assert res_get.status_code == 200
    conv_data = res_get.json()
    assert conv_data["status"] == "WAITING_FOR_BULK_UPDATE_CONFIRMATION"
    assert conv_data["pending_action"] is not None
    assert conv_data["pending_action"]["type"] == "BULK_UPDATE_CONFIRMATION"
    assert conv_data["pending_action"]["status"] == "PENDING"
    assert conv_data["pending_action"]["activity_count"] == 2

    # 4. Cancel the proposal via fast-path
    res_cancel = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/bulk-confirm",
        json={"action": "CANCEL", "activity_ids": []},
    )
    assert res_cancel.status_code == 200
    assert res_cancel.json()["status"] == "CANCELLED"

    # 5. Verify refresh after cancellation shows active and unlocked
    res_get2 = client.get(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}",
    )
    assert res_get2.status_code == 200
    conv_data2 = res_get2.json()
    assert conv_data2["status"] == "ACTIVE"
    assert conv_data2["pending_action"] is None


def test_bulk_proposal_conversation_isolation(
    client: TestClient,
    agent_test_project: Project,
    db_session: Session,
):
    """
    Test Case Bulk Test E:
    Verify that chat lock is conversation-specific.
    Conversation A: bulk update pending -> locked.
    Conversation B: normal conversation -> unlocked and responsive.
    """
    # 1. Create Conversation A and trigger pending bulk update
    res_conv_a = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_a_id = res_conv_a.json()["conversation_id"]

    client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_a_id}/messages",
        json={"content": "Foundation pour completed."},
    )
    client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_a_id}/messages",
        json={"content": "update all"},
    )

    # Verify Conversation A is locked
    res_get_a = client.get(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_a_id}",
    )
    assert res_get_a.json()["status"] == "WAITING_FOR_BULK_UPDATE_CONFIRMATION"
    assert res_get_a.json()["pending_action"] is not None

    # 2. Create Conversation B (completely clean conversation)
    res_conv_b = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_b_id = res_conv_b.json()["conversation_id"]

    # Verify Conversation B is NOT locked
    res_get_b = client.get(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_b_id}",
    )
    assert res_get_b.json()["status"] == "ACTIVE"
    assert res_get_b.json()["pending_action"] is None

    # Conversation B accepts standard queries
    res_msg_b = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_b_id}/messages",
        json={"content": "What is the progress of CIV-1001?"},
    )
    assert res_msg_b.status_code == 200
    assert "awaiting your decision" not in res_msg_b.json()["reply_text"]

    # Meanwhile, Conversation A still rejects unrelated queries
    res_msg_a = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_a_id}/messages",
        json={"content": "What is the progress of CIV-1001?"},
    )
    assert res_msg_a.status_code == 200
    assert "awaiting your decision" in res_msg_a.json()["reply_text"] or "निर्णय की प्रतीक्षा" in res_msg_a.json()["reply_text"]


def test_bulk_cancellation_variants_fast_path(
    client: TestClient,
    agent_test_project: Project,
    db_session: Session,
):
    """
    Test Case Bulk Test B:
    Verify that variants of cancellation ("no", "don't update", "no, don't update them")
    all trigger deterministic fast-path cancellation in < 0.2s without LLM call.
    """
    import time

    cancellation_phrases = ["no", "don't update", "no, don't update them", "cancel"]

    for phrase in cancellation_phrases:
        # Create new conversation
        res_conv = client.post(
            f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
            json={"force_new": True},
        )
        conv_id = res_conv.json()["conversation_id"]

        # Trigger bulk proposal
        client.post(
            f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
            json={"content": "Foundation pour completed."},
        )
        res_bulk = client.post(
            f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
            json={"content": "update all"},
        )
        assert res_bulk.json()["action_card"]["proposal_status"] == "PENDING"

        # Time the cancellation response
        t0 = time.time()
        res_cancel = client.post(
            f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
            json={"content": phrase},
        )
        elapsed = time.time() - t0

        assert res_cancel.status_code == 200
        assert elapsed < 0.3, f"Cancellation for '{phrase}' took {elapsed:.3f}s, expected < 0.3s (fast-path)"
        reply = res_cancel.json()["reply_text"].lower()
        assert "cancel" in reply or "radd" in reply or "रद्द" in reply

        # Verify unlocked
        db_session.expire_all()
        conv = db_session.query(Conversation).filter(Conversation.id == conv_id).first()
        assert conv.status == "ACTIVE"
        assert conv.active_event_id is None


def test_bulk_proposal_double_cancellation_and_confirmation_protection(
    client: TestClient,
    agent_test_project: Project,
    db_session: Session,
):
    """
    Test Cases Bulk Test G & H:
    Verify that rapid repeated Cancel or Confirm actions are idempotent
    and do not throw 500 errors or duplicate schedule mutations.
    """
    # 1. Test Double Cancel
    res_conv1 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv1_id = res_conv1.json()["conversation_id"]
    client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv1_id}/messages",
        json={"content": "Foundation pour completed."},
    )
    client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv1_id}/messages",
        json={"content": "update all"},
    )

    # Cancel 1st time
    cancel1 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv1_id}/bulk-confirm",
        json={"action": "CANCEL", "activity_ids": []},
    )
    assert cancel1.status_code == 200
    assert cancel1.json()["status"] == "CANCELLED"

    # Cancel 2nd time immediately
    cancel2 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv1_id}/bulk-confirm",
        json={"action": "CANCEL", "activity_ids": []},
    )
    assert cancel2.status_code == 200
    assert cancel2.json()["status"] == "CANCELLED"

    # 2. Test Double Confirm
    res_conv2 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv2_id = res_conv2.json()["conversation_id"]
    client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv2_id}/messages",
        json={"content": "Foundation pour completed."},
    )
    res_b2 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv2_id}/messages",
        json={"content": "update all"},
    )
    card = res_b2.json()["action_card"]
    act_ids = [a["activity_id"] for a in card["bulk_activities"]]

    # Confirm 1st time
    conf1 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv2_id}/bulk-confirm",
        json={"action": "CONFIRM", "activity_ids": act_ids, "target_percent": 100.0},
    )
    assert conf1.status_code == 200
    assert conf1.json()["status"] == "APPLIED"
    assert conf1.json()["updated_count"] == 2

    # Check database state: both activities are 100%
    db_session.expire_all()
    acts = db_session.query(Activity).filter(Activity.id.in_(act_ids)).all()
    for a in acts:
        assert a.percent_complete == 100.0

    # Confirm 2nd time immediately
    conf2 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv2_id}/bulk-confirm",
        json={"action": "CONFIRM", "activity_ids": act_ids, "target_percent": 100.0},
    )
    assert conf2.status_code == 200
    assert conf2.json()["status"] == "APPLIED"

    # Verify no corruption in ledger or status
    db_session.expire_all()
    acts_after = db_session.query(Activity).filter(Activity.id.in_(act_ids)).all()
    for a in acts_after:
        assert a.percent_complete == 100.0


# =========================================================================
# TEST SUITE I: Interactive Activity Update Clarification Loop (Section 33 & 36)
# =========================================================================

def test_clarification_loop_section_36_full_conversation_flow(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    Validates Section 36 exact multi-turn conversation flow:
    Turn 1: User: "I want to update an activity." -> Agent asks which type (action_card=None)
    Turn 2: User: "Mechanical activity." -> Agent asks which mechanical (action_card=None)
    Turn 3: User: "Pump installation." -> Agent asks location/equipment (action_card=None)
    Turn 4: User: "Unit 4." -> Agent identifies activity & asks for update value (action_card=None)
    Turn 5: User: "Set progress to 75%." -> Agent stages proposal, returns PROPOSAL_CONFIRMATION card
    Turn 6: User confirms -> Schedule update applied to authoritative database.
    """
    project_id = agent_test_project.id
    res_conv = client.post(
        f"/api/v1/projects/{project_id}/agent/conversations",
        json={"force_new": True},
    )
    assert res_conv.status_code == 200
    conv_id = res_conv.json()["conversation_id"]

    # Turn 1: User: "I want to update an activity."
    res1 = client.post(
        f"/api/v1/projects/{project_id}/agent/conversations/{conv_id}/messages",
        json={"content": "I want to update an activity."},
    )
    assert res1.status_code == 200
    d1 = res1.json()
    assert d1["action_card"] is None
    assert "Which activity would you like to update?" in d1["reply_text"] or "type" in d1["reply_text"].lower()

    # Turn 2: User: "Mechanical activity."
    res2 = client.post(
        f"/api/v1/projects/{project_id}/agent/conversations/{conv_id}/messages",
        json={"content": "Mechanical activity."},
    )
    assert res2.status_code == 200
    d2 = res2.json()
    assert d2["action_card"] is None
    assert "mechanical" in d2["reply_text"].lower()
    assert any(term in d2["reply_text"].lower() for term in ["name", "equipment", "location", "id"])

    # Turn 3: User: "Pump installation."
    res3 = client.post(
        f"/api/v1/projects/{project_id}/agent/conversations/{conv_id}/messages",
        json={"content": "Pump installation."},
    )
    assert res3.status_code == 200
    d3 = res3.json()
    assert d3["action_card"] is None
    assert any(term in d3["reply_text"].lower() for term in ["location", "unit", "which", "pump"])

    # Turn 4: User: "Unit 4."
    res4 = client.post(
        f"/api/v1/projects/{project_id}/agent/conversations/{conv_id}/messages",
        json={"content": "Unit 4."},
    )
    assert res4.status_code == 200
    d4 = res4.json()
    assert d4["action_card"] is None
    assert "what update would you like to make" in d4["reply_text"].lower() or "identified" in d4["reply_text"].lower()

    # Turn 5: User: "Set progress to 75%."
    res5 = client.post(
        f"/api/v1/projects/{project_id}/agent/conversations/{conv_id}/messages",
        json={"content": "Set progress to 75%."},
    )
    assert res5.status_code == 200
    d5 = res5.json()
    assert d5["action_card"] is not None
    card = d5["action_card"]
    assert card["type"] == "PROPOSAL_CONFIRMATION"
    assert card["activity_code"] == "MEC-1004"
    assert card["proposed_percent"] == 75.0
    proposal_id = card["proposal_id"]

    # Verify activity in DB has NOT updated yet
    db_session.expire_all()
    act = db_session.query(Activity).filter_by(activity_code="MEC-1004").first()
    assert act.percent_complete == 0.0

    # Turn 6: Confirm update
    res_conf = client.post(
        f"/api/v1/projects/{project_id}/agent/conversations/{conv_id}/confirm",
        json={"proposal_id": proposal_id},
        headers={"X-User-ID": "supervisor_bob", "X-User-Role": "SUPERVISOR"},
    )
    assert res_conf.status_code == 200
    assert res_conf.json()["status"] == "APPLIED"
    assert res_conf.json()["new_percent"] == 75.0

    # Verify authoritative database update
    db_session.expire_all()
    act_updated = db_session.query(Activity).filter_by(activity_code="MEC-1004").first()
    assert act_updated.percent_complete == 75.0
    assert act_updated.status == "IN_PROGRESS"


def test_section_33_test_1_generic_update_request_no_activity_list(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    Test 1: User says 'I want to update an activity.'
    Expected: WAITING_FOR_ACTIVITY_CLARIFICATION and NO activity list card.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    res = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "I want to update an activity."},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["action_card"] is None
    assert "Which activity would you like to update?" in data["reply_text"] or "type" in data["reply_text"].lower()

    db_session.expire_all()
    conv = db_session.query(Conversation).filter_by(id=conv_id).first()
    assert conv.status == "WAITING_FOR_USER"
    assert conv.active_event_id is not None
    ev = db_session.query(ExecutionEvent).filter_by(id=conv.active_event_id).first()
    meta = json.loads(ev.match_metadata)
    assert meta["update_context"]["status"] == "WAITING_FOR_ACTIVITY_CLARIFICATION"


def test_section_33_test_2_broad_discipline_mechanical(
    client: TestClient, agent_test_project: Project
):
    """
    Test 2: Broad discipline ('Mechanical activity.')
    Expected: another clarification question if multiple mechanical activities exist, NO candidate card.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    res = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Mechanical activity."},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["action_card"] is None
    assert "mechanical" in data["reply_text"].lower()


def test_section_33_test_3_additional_information_pump(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    Test 3: Additional information ('Pump installation.')
    Expected: context accumulated, another clarification question without candidate card.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Mechanical activity."},
    )
    res2 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Pump installation."},
    )
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["action_card"] is None

    db_session.expire_all()
    conv = db_session.query(Conversation).filter_by(id=conv_id).first()
    ev = db_session.query(ExecutionEvent).filter_by(id=conv.active_event_id).first()
    meta = json.loads(ev.match_metadata)
    assert meta["update_context"]["discipline"] == "Mechanical"
    assert meta["update_context"]["equipment"] == "Pump"


def test_section_33_test_4_unique_activity_identification(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    Test 4: Unique activity identification.
    Expected: ACTIVITY_IDENTIFIED status and asks for update value.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    res = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Mechanical pump installation at Unit 4."},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["action_card"] is None
    assert "MEC-1004" in data["reply_text"] or "what update would you like to make" in data["reply_text"].lower()

    db_session.expire_all()
    conv = db_session.query(Conversation).filter_by(id=conv_id).first()
    ev = db_session.query(ExecutionEvent).filter_by(id=conv.active_event_id).first()
    meta = json.loads(ev.match_metadata)
    assert meta["update_context"]["status"] == "WAITING_FOR_UPDATE_VALUE"
    assert meta["update_context"]["activity_code"] == "MEC-1004"


def test_section_33_test_5_update_value_included_early(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    Test 5: Update value included early:
    User: "Update the mechanical pump installation at Unit 4 to 75%."
    Expected: activity identified + update value extracted + existing proposal flow without unnecessary questions.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    res = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Update the mechanical pump installation at Unit 4 to 75%."},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["action_card"] is not None
    card = data["action_card"]
    assert card["type"] == "PROPOSAL_CONFIRMATION"
    assert card["activity_code"] == "MEC-1004"
    assert card["proposed_percent"] == 75.0


def test_section_33_test_6_no_matching_activity(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    Test 6: No matching activity found.
    User: "Mechanical pump installation at Unit 9."
    Expected: "I couldn't find a matching activity for that description in this project..."
    No schedule mutation.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    res = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Mechanical pump installation at Unit 9."},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["action_card"] is None
    assert "couldn't find a matching activity" in data["reply_text"].lower() or "no matching activity" in data["reply_text"].lower()

    # Verify no activities modified
    db_session.expire_all()
    acts = db_session.query(Activity).filter_by(project_id=agent_test_project.id).all()
    for a in acts:
        assert a.percent_complete in (0.0, 40.0)


def test_section_33_test_7_multiple_matches_not_candidate_list(
    client: TestClient, agent_test_project: Project
):
    """
    Test 7: Multiple matches produce clarification question, NOT an immediate numbered candidate list.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    res = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Pump installation."},
    )
    assert res.status_code == 200
    data = res.json()
    # Must NOT return candidate choice card on initial turns
    assert data["action_card"] is None
    # Must ask semantic distinguishing question
    assert len(data["reply_text"]) > 0


def test_section_33_test_8_context_accumulation(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    Test 8: Context accumulation across turns:
    Turn 1: Mechanical
    Turn 2: Pump
    Turn 3: Unit 4
    Expected: Final context has Mechanical, Pump, and Unit 4.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Mechanical"},
    )
    client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Pump"},
    )
    client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Unit 4"},
    )

    db_session.expire_all()
    conv = db_session.query(Conversation).filter_by(id=conv_id).first()
    ev = db_session.query(ExecutionEvent).filter_by(id=conv.active_event_id).first()
    meta = json.loads(ev.match_metadata)
    ctx = meta["update_context"]
    assert ctx["discipline"] == "Mechanical"
    assert ctx["equipment"] == "Pump"
    assert ctx["location"] == "Unit 4"


def test_section_33_test_9_context_preservation_after_refresh(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    Test 9: Multi-turn clarification state survives refresh/reload.
    Re-querying conversation and sending next message retains accumulated state.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    # Turn 1
    client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Mechanical"},
    )

    # Simulate reload: clear session cache and re-fetch conversation
    db_session.expire_all()
    get_res = client.get(f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}")
    assert get_res.status_code == 200

    # Turn 2: Provide pump installation
    res2 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Pump installation at Unit 4"},
    )
    assert res2.status_code == 200

    # State retains mechanical discipline from Turn 1!
    db_session.expire_all()
    conv = db_session.query(Conversation).filter_by(id=conv_id).first()
    ev = db_session.query(ExecutionEvent).filter_by(id=conv.active_event_id).first()
    meta = json.loads(ev.match_metadata)
    assert meta["update_context"]["discipline"] == "Mechanical"
    assert meta["update_context"]["activity_code"] == "MEC-1004"


def test_section_33_test_10_multilingual_clarification_hindi(
    client: TestClient, agent_test_project: Project
):
    """
    Test 10: Entire clarification flow remains in Hindi.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    res1 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "मुझे एक activity update करनी है।"},
    )
    assert res1.status_code == 200
    d1 = res1.json()
    assert d1["action_card"] is None
    assert "ज़रूर" in d1["reply_text"] or "किस प्रकार" in d1["reply_text"]

    res2 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Mechanical activity."},
    )
    assert res2.status_code == 200
    d2 = res2.json()
    assert d2["action_card"] is None
    assert "कृपया बताइए" in d2["reply_text"] or "बताएं" in d2["reply_text"]


def test_section_33_test_11_multilingual_clarification_hinglish(
    client: TestClient, agent_test_project: Project
):
    """
    Test 11: Entire clarification flow remains in Hinglish.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    res1 = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "activity update karni hai"},
    )
    assert res1.status_code == 200
    d1 = res1.json()
    assert d1["action_card"] is None
    assert any(term in d1["reply_text"].lower() for term in ["karna chahte hain", "type", "activity"])


def test_section_33_test_12_cross_language_clarification_input(
    client: TestClient, agent_test_project: Project
):
    """
    Test 12: Conversation locked to Hindi; user answers clarification in English.
    Expected: Understand English + respond in Hindi.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    # Start in Hindi
    client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "मुझे एक activity update करनी है।"},
    )

    # Answer in English
    res_eng = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Mechanical pump installation at Unit 4"},
    )
    assert res_eng.status_code == 200
    d_eng = res_eng.json()
    # Agent understands English input and identifies MEC-1004, but responds in Hindi!
    reply = d_eng["reply_text"]
    assert "मैंने" in reply or "पहचान" in reply or "update करना चाहते हैं" in reply


def test_section_33_test_13_pending_proposal_chat_lock_guard(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    Test 13: Proposal lock:
    Once activity is identified and proposal is waiting:
    Chat input is guarded against unrelated commands until Confirm or Cancel.
    Informational queries are still answered.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    # Stage proposal
    res_prop = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Update MEC-1004 to 80%."},
    )
    assert res_prop.status_code == 200
    assert res_prop.json()["action_card"]["type"] == "PROPOSAL_CONFIRMATION"

    # Try sending unrelated update command
    res_block = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Update STR-2001 to 90%"},
    )
    assert res_block.status_code == 200
    block_reply = res_block.json()["reply_text"]
    assert "awaiting your decision" in block_reply or "निर्णय की प्रतीक्षा" in block_reply

    # Informational query is permitted
    res_query = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "What is the status of F-204?"},
    )
    assert res_query.status_code == 200
    assert "CIV-1001" in res_query.json()["reply_text"]

    # Cancel unlocks the proposal
    res_cancel = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "cancel"},
    )
    assert res_cancel.status_code == 200
    assert "cancel" in res_cancel.json()["reply_text"].lower()


def test_section_33_test_14_no_accidental_update_during_clarification(
    client: TestClient, agent_test_project: Project, db_session: Session
):
    """
    Test 14: No accidental update:
    During clarification turns, database activity remains unchanged.
    """
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    # 3 clarification turns
    client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "I want to update an activity."},
    )
    client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Mechanical activity."},
    )
    client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Pump installation."},
    )

    # Check database: MEC-1003 and MEC-1004 must still be 0.0%
    db_session.expire_all()
    mec3 = db_session.query(Activity).filter_by(activity_code="MEC-1003").first()
    mec4 = db_session.query(Activity).filter_by(activity_code="MEC-1004").first()
    assert mec3.percent_complete == 0.0
    assert mec4.percent_complete == 0.0


def test_information_query_unknown_activity(client, agent_test_project, db_session):
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"title": "Unknown Activity Test"},
    )
    conv_id = res_conv.json()["conversation_id"]

    res_msg = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "What is the status of CIV-9999?"},
    )
    assert res_msg.status_code == 200
    reply = res_msg.json()["reply_text"]
    assert "CIV-9999" in reply
    assert "not found" in reply.lower()


def test_activity_creation_and_deletion_guidance(client, agent_test_project, db_session):
    # Test creation guidance in English
    res_conv = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"title": "Creation Test", "force_new": True},
    )
    conv_id = res_conv.json()["conversation_id"]

    res_create = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Please create a new activity for foundation excavation."},
    )
    assert res_create.status_code == 200
    reply_create = res_create.json()["reply_text"]
    assert any(term in reply_create.lower() for term in ["primavera", "wbs", "baseline", "activities table"])

    # Test deletion guidance in Hinglish
    res_conv_hi = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations",
        json={"title": "Deletion Test", "force_new": True},
    )
    conv_id_hi = res_conv_hi.json()["conversation_id"]

    res_del = client.post(
        f"/api/v1/projects/{agent_test_project.id}/agent/conversations/{conv_id_hi}/messages",
        json={"content": "Activity CIV-1001 ko delete kardo"},
    )
    assert res_del.status_code == 200
    reply_del = res_del.json()["reply_text"]
    assert any(term in reply_del.lower() for term in ["primavera", "wbs", "baseline", "activities table", "schedule"])







