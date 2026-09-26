import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.models import Activity, ExecutionEvent, Project, UpdateProposal, WBSNode
from app.services.activity_reference_resolver import ActivityReferenceResolver, ResolutionResult
from app.services.agent_service import TimeAgentService


@pytest.fixture
def no_match_project(db_session: Session) -> Project:
    """Project fixture for testing Section 32: Non-existent / NO-MATCH activities."""
    proj = Project(
        id="proj-no-match-test",
        project_code="NOMATCH_001",
        name="Industrial Processing Facility",
        planned_start=datetime(2024, 1, 1, 8, 0),
        planned_finish=datetime(2025, 12, 31, 17, 0),
        data_date=datetime(2024, 10, 1, 0, 0),
    )
    db_session.add(proj)

    wbs_mec = WBSNode(id="wbs-nm-mec", project_id=proj.id, code="WBS-MEC", name="Mechanical Systems")
    wbs_ele = WBSNode(id="wbs-nm-ele", project_id=proj.id, code="WBS-ELE", name="Electrical Systems")
    db_session.add_all([wbs_mec, wbs_ele])

    activities = [
        Activity(
            id="act-nm-1001",
            project_id=proj.id,
            wbs_id=wbs_mec.id,
            activity_code="MEC-1001",
            name="Mechanical Equipment Installation",
            discipline="Mechanical",
            status="NOT_STARTED",
            percent_complete=0.0,
            planned_quantity=10.0,
            quantity_unit="units",
        ),
        Activity(
            id="act-nm-1002",
            project_id=proj.id,
            wbs_id=wbs_mec.id,
            activity_code="MEC-1002",
            name="Mechanical Piping Installation",
            discipline="Mechanical",
            status="IN_PROGRESS",
            percent_complete=20.0,
            planned_quantity=500.0,
            quantity_unit="m",
        ),
        Activity(
            id="act-nm-1003",
            project_id=proj.id,
            wbs_id=wbs_ele.id,
            activity_code="ELE-1001",
            name="Electrical Cable Tray Installation",
            discipline="Electrical",
            status="NOT_STARTED",
            percent_complete=0.0,
            planned_quantity=200.0,
            quantity_unit="m",
        ),
    ]
    db_session.add_all(activities)
    db_session.commit()
    return proj


# =========================================================================
# 1. SECTION 32 EXAMPLE: "I completed the mechanical welding activity to 80%."
# =========================================================================

def test_mechanical_welding_no_match_with_suggestions(
    client: TestClient, no_match_project: Project, db_session: Session
):
    """
    User: 'I completed the mechanical welding activity to 80%.'
    In a project containing Mechanical Piping and Equipment, but NO welding activity:
    - resolution_status = NO_MATCH
    - No auto-selection of closest match
    - No selection merely because of discipline
    - No proposal created
    - No schedule mutated
    - Clear statement referencing the current project schedule
    - Plausible alternatives presented as suggestions, NOT resolved matches
    """
    conv_res = client.post(f"/api/v1/projects/{no_match_project.id}/agent/conversations")
    conv_id = conv_res.json()["conversation_id"]

    # Send the exact Section 32 example query
    res = client.post(
        f"/api/v1/projects/{no_match_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "I completed the mechanical welding activity to 80%."},
    )
    assert res.status_code == 200
    data = res.json()
    reply = data["reply_text"]

    # 1. Clear statement mentioning reference and CURRENT PROJECT'S SCHEDULE
    assert "in this project's schedule" in reply
    assert "mechanical welding activity" in reply or "welding" in reply

    # 2. Plausible candidates presented as suggestions, NOT resolved matches
    assert "These scheduled activities may be related" in reply
    assert "Mechanical Piping Installation" in reply or "Mechanical Equipment Installation" in reply
    assert "possible suggestions, not confirmed matches" in reply

    # 3. ActionCard invariant: Must NOT be a proposal confirmation
    card = data.get("action_card")
    if card:
        assert card.get("type") != "PROPOSAL_CONFIRMATION"
        assert card.get("proposal_id") is None

    # 4. Invariant: ZERO proposals staged in database
    pending_proposals = db_session.query(UpdateProposal).filter(
        UpdateProposal.conversation_id == conv_id,
        UpdateProposal.status == "PENDING",
    ).all()
    assert len(pending_proposals) == 0

    # 5. Invariant: ZERO schedule mutations
    for act in db_session.query(Activity).filter(Activity.project_id == no_match_project.id).all():
        if act.activity_code == "MEC-1002":
            assert act.percent_complete == 20.0  # unchanged
        else:
            assert act.percent_complete == 0.0  # unchanged


# =========================================================================
# 2. COMPLETELY NON-EXISTENT ACTIVITY: No suggestions above threshold
# =========================================================================

def test_completely_non_existent_activity_no_suggestions(
    client: TestClient, no_match_project: Project, db_session: Session
):
    """
    User refers to an activity with 0 plausible match in the schedule.
    - resolution_status = NO_MATCH
    - No suggestions presented
    - Prompt asks for activity code or specific description
    - Zero proposals, zero mutations
    """
    conv_res = client.post(f"/api/v1/projects/{no_match_project.id}/agent/conversations")
    conv_id = conv_res.json()["conversation_id"]

    res = client.post(
        f"/api/v1/projects/{no_match_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "I finished the lunar rover telemetrist calibration to 100%."},
    )
    assert res.status_code == 200
    data = res.json()
    reply = data["reply_text"]

    # Wording refers to current project's schedule
    assert "in this project's schedule" in reply
    assert "Please provide the activity code or a more specific description" in reply

    # No suggestions section
    assert "These scheduled activities may be related" not in reply

    # ActionCard invariant: No proposal card
    card = data.get("action_card")
    assert card is None or card.get("type") != "PROPOSAL_CONFIRMATION"

    # Zero proposals
    pending_proposals = db_session.query(UpdateProposal).filter(
        UpdateProposal.conversation_id == conv_id,
    ).all()
    assert len(pending_proposals) == 0


# =========================================================================
# 3. DISCIPLINE MATCHING MUST NOT AUTO-RESOLVE NON-EXISTENT ACTIVITY
# =========================================================================

def test_discipline_does_not_auto_resolve_non_existent_activity(
    client: TestClient, no_match_project: Project, db_session: Session
):
    """
    User specifies 'mechanical turbine generator to 50%'.
    Even though 'mechanical' matches the discipline, turbine generator does NOT exist.
    The system must NOT auto-resolve to MEC-1001 or MEC-1002.
    """
    conv_res = client.post(f"/api/v1/projects/{no_match_project.id}/agent/conversations")
    conv_id = conv_res.json()["conversation_id"]

    res = client.post(
        f"/api/v1/projects/{no_match_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Update mechanical turbine generator to 50%."},
    )
    assert res.status_code == 200
    data = res.json()
    reply = data["reply_text"]

    assert "in this project's schedule" in reply

    # Must NOT have created a proposal for MEC-1001 or MEC-1002
    pending = db_session.query(UpdateProposal).filter(
        UpdateProposal.conversation_id == conv_id,
    ).all()
    assert len(pending) == 0

    # Activities in DB remain unmutated
    m1 = db_session.query(Activity).filter(Activity.activity_code == "MEC-1001").first()
    assert m1.percent_complete == 0.0


# =========================================================================
# 4. DETERMINISTIC THRESHOLDS AND RESOLUTION_STATUS CONTRACT
# =========================================================================

def test_deterministic_thresholds_and_status_contract(
    db_session: Session, no_match_project: Project
):
    """
    Directly tests ActivityReferenceResolver deterministic thresholds and Section 32 contract:
    - RESOLVED: exactly one sufficiently confident activity (>= 0.65 with margin)
    - AMBIGUOUS: multiple plausible activities
    - NO_MATCH: no sufficiently plausible activity (< 0.65)
    """
    acts = db_session.query(Activity).filter(Activity.project_id == no_match_project.id).all()

    # Threshold constants verification
    assert ActivityReferenceResolver.RESOLUTION_THRESHOLD == 0.65
    assert ActivityReferenceResolver.SUGGESTION_THRESHOLD == 0.35
    assert ActivityReferenceResolver.MARGIN_DELTA_THRESHOLD == 0.25

    # Case A: Exact code match -> RESOLVED
    res_code = ActivityReferenceResolver.resolve(
        project_id=no_match_project.id,
        catalog=acts,
        raw_reference="MEC-1001",
    )
    assert res_code.resolution_status == "RESOLVED"
    assert res_code.resolved_activity.activity_code == "MEC-1001"
    assert res_code.confidence == 1.0

    # Case B: Broad discipline -> AMBIGUOUS
    res_broad = ActivityReferenceResolver.resolve(
        project_id=no_match_project.id,
        catalog=acts,
        raw_reference="mechanical activity",
        explicit_discipline="Mechanical",
    )
    assert res_broad.resolution_status == "AMBIGUOUS"
    assert res_broad.resolved_activity is None
    assert len(res_broad.candidates) >= 2

    # Case C: Non-existent activity with suggestions (0.35 <= score < 0.65) -> NO_MATCH
    res_weld = ActivityReferenceResolver.resolve(
        project_id=no_match_project.id,
        catalog=acts,
        raw_reference="mechanical welding activity",
        explicit_discipline="Mechanical",
    )
    assert res_weld.resolution_status == "NO_MATCH"
    assert res_weld.method == "no_match"
    assert res_weld.resolved_activity is None
    assert len(res_weld.candidates) > 0  # suggestions present
    assert any(c.activity_code == "MEC-1002" for c in res_weld.candidates)

    # Case D: Completely foreign activity (score < 0.35) -> NO_MATCH with empty candidates
    res_alien = ActivityReferenceResolver.resolve(
        project_id=no_match_project.id,
        catalog=acts,
        raw_reference="orbital satellite launcher",
    )
    assert res_alien.resolution_status == "NO_MATCH"
    assert res_alien.method == "no_match"
    assert res_alien.resolved_activity is None
    assert len(res_alien.candidates) == 0  # no suggestions


# =========================================================================
# 5. MULTILINGUAL PROJECT SCHEDULE WORDING
# =========================================================================

def test_multilingual_project_schedule_wording(
    client: TestClient, no_match_project: Project
):
    """
    Verifies that the agent response explicitly references the current project's schedule
    in Hindi and Hinglish, quotes the clean activity noun phrase, and provides suggestions.
    """
    # Hindi conversation
    conv_hi_res = client.post(
        f"/api/v1/projects/{no_match_project.id}/agent/conversations",
        json={"title": "Hindi Test"},
    )
    conv_hi_id = conv_hi_res.json()["conversation_id"]

    res_hi = client.post(
        f"/api/v1/projects/{no_match_project.id}/agent/conversations/{conv_hi_id}/messages",
        json={"content": "मैंने मैकेनिकल वेल्डिंग एक्टिविटी को 80% पूरा किया।"},
    )
    assert res_hi.status_code == 200
    reply_hi = res_hi.json()["reply_text"]
    assert "प्रोजेक्ट के शेड्यूल में" in reply_hi
    assert "'मैकेनिकल वेल्डिंग एक्टिविटी'" in reply_hi
    assert "ये निर्धारित गतिविधियां संबंधित हो सकती हैं:" in reply_hi

    # Hinglish query: 'Mechanical welding activity 80% complete ho gayi hai.'
    conv_hing_res = client.post(
        f"/api/v1/projects/{no_match_project.id}/agent/conversations",
        json={"title": "Hinglish Test"},
    )
    conv_hing_id = conv_hing_res.json()["conversation_id"]

    res_hing = client.post(
        f"/api/v1/projects/{no_match_project.id}/agent/conversations/{conv_hing_id}/messages",
        json={"content": "Mechanical welding activity 80% complete ho gayi hai."},
    )
    assert res_hing.status_code == 200
    reply_hing = res_hing.json()["reply_text"]
    assert "'Mechanical welding activity'" in reply_hing
    assert "activity 80" not in reply_hing


# =========================================================================
# 6. SUBSEQUENT CLARIFICATION AFTER NO-MATCH RESOLVES SAFELY
# =========================================================================

def test_subsequent_clarification_after_no_match(
    client: TestClient, no_match_project: Project, db_session: Session
):
    """
    1. Turn 1: User specifies non-existent activity -> receives NO_MATCH suggestions.
    2. Turn 2: User provides valid activity code MEC-1002.
    3. Verifies that MEC-1002 is now identified and stages proposal only after confirmation.
    """
    conv_res = client.post(f"/api/v1/projects/{no_match_project.id}/agent/conversations")
    conv_id = conv_res.json()["conversation_id"]

    # Turn 1: non-existent activity
    res1 = client.post(
        f"/api/v1/projects/{no_match_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "I completed the mechanical welding activity to 80%."},
    )
    assert "in this project's schedule" in res1.json()["reply_text"]
    assert res1.json().get("action_card") is None or res1.json()["action_card"].get("type") != "PROPOSAL_CONFIRMATION"

    # Turn 2: user clarifies with exact scheduled activity code
    res2 = client.post(
        f"/api/v1/projects/{no_match_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "I meant MEC-1002, please update to 80%"},
    )
    assert res2.status_code == 200
    data2 = res2.json()

    # Now a proposal should be staged for MEC-1002
    card = data2.get("action_card")
    assert card is not None
    assert card.get("type") == "PROPOSAL_CONFIRMATION"
    assert card.get("activity_code") == "MEC-1002"
    assert card.get("proposed_percent") == 80.0
