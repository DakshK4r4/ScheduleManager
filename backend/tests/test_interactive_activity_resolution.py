import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.models import Activity, ExecutionEvent, Project, UpdateProposal, WBSNode
from app.schemas.agent import ActionCardDTO
from app.services.activity_reference_resolver import ActivityReferenceResolver
from app.services.agent_service import TimeAgentService
from app.services.interactive_activity_resolver import (
    ActivityResolutionSession,
    InteractiveActivityResolver,
    ResolutionSessionStatus,
    ResolutionStepResult,
)


@pytest.fixture
def interactive_project(db_session: Session) -> Project:
    """Project fixture for testing interactive activity resolution."""
    proj = Project(
        id="proj-interactive-test",
        project_code="INTERACTIVE_001",
        name="Interactive Disambiguation Facility",
        planned_start=datetime(2024, 1, 1, 8, 0),
        planned_finish=datetime(2025, 12, 31, 17, 0),
        data_date=datetime(2024, 10, 1, 0, 0),
    )
    db_session.add(proj)

    wbs_mec = WBSNode(id="wbs-inter-mec", project_id=proj.id, code="WBS-MEC", name="Mechanical Systems")
    wbs_civ = WBSNode(id="wbs-inter-civ", project_id=proj.id, code="WBS-CIV", name="Civil Works")
    db_session.add_all([wbs_mec, wbs_civ])

    # 5 Mechanical activities with distinct components
    mec_activities = [
        Activity(
            id="act-mec-1001",
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
            id="act-mec-1002",
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
            id="act-mec-1003",
            project_id=proj.id,
            wbs_id=wbs_mec.id,
            activity_code="MEC-1003",
            name="Mechanical Pump Installation",
            discipline="Mechanical",
            status="IN_PROGRESS",
            percent_complete=30.0,
            planned_quantity=4.0,
            quantity_unit="units",
        ),
        Activity(
            id="act-mec-1004",
            project_id=proj.id,
            wbs_id=wbs_mec.id,
            activity_code="MEC-1004",
            name="Mechanical Testing",
            discipline="Mechanical",
            status="NOT_STARTED",
            percent_complete=0.0,
            planned_quantity=1.0,
            quantity_unit="pkg",
        ),
        Activity(
            id="act-mec-1005",
            project_id=proj.id,
            wbs_id=wbs_mec.id,
            activity_code="MEC-1005",
            name="Mechanical Commissioning",
            discipline="Mechanical",
            status="NOT_STARTED",
            percent_complete=0.0,
            planned_quantity=1.0,
            quantity_unit="pkg",
        ),
    ]

    # 2 Civil activities in distinct locations (Unit 4 vs Unit 5)
    civ_activities = [
        Activity(
            id="act-civ-2001",
            project_id=proj.id,
            wbs_id=wbs_civ.id,
            activity_code="CIV-2001",
            name="Foundation Concrete Pour Unit 4",
            discipline="Civil",
            location_code="Unit 4",
            status="NOT_STARTED",
            percent_complete=0.0,
            planned_quantity=100.0,
            quantity_unit="m3",
        ),
        Activity(
            id="act-civ-2002",
            project_id=proj.id,
            wbs_id=wbs_civ.id,
            activity_code="CIV-2002",
            name="Foundation Concrete Pour Unit 5",
            discipline="Civil",
            location_code="Unit 5",
            status="NOT_STARTED",
            percent_complete=0.0,
            planned_quantity=100.0,
            quantity_unit="m3",
        ),
    ]

    # Activity belonging to another project for cross-project safety test
    other_proj = Project(
        id="proj-other-foreign",
        project_code="FOREIGN_001",
        name="Foreign Facility",
        planned_start=datetime(2024, 1, 1, 8, 0),
        planned_finish=datetime(2025, 12, 31, 17, 0),
    )
    db_session.add(other_proj)
    foreign_act = Activity(
        id="act-foreign-9999",
        project_id=other_proj.id,
        activity_code="MEC-9999",
        name="Foreign Mechanical Work",
        discipline="Mechanical",
        status="NOT_STARTED",
        percent_complete=0.0,
    )

    for a in mec_activities + civ_activities:
        db_session.add(a)
    db_session.add(foreign_act)
    db_session.commit()
    return proj


# =========================================================================
# TEST 1: Ambiguous statement triggers clarification, NOT bulk update
# =========================================================================

def test_1_ambiguous_mechanical_work_triggers_clarification_not_bulk(
    client: TestClient, interactive_project: Project, db_session: Session
):
    """
    'update the mechanical work to 80%' with 5 mechanical activities:
    Must trigger structured clarification choice, NEVER bulk update or schedule mutation.
    """
    conv_res = client.post(f"/api/v1/projects/{interactive_project.id}/agent/conversations")
    assert conv_res.status_code == 200
    conv_id = conv_res.json()["conversation_id"]

    resp = client.post(
        f"/api/v1/projects/{interactive_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "update the mechanical work to 80%"},
    )
    assert resp.status_code == 200
    data = resp.json()

    # Invariant: Must NOT be bulk proposal
    assert data.get("action_card") is not None
    assert data["action_card"]["type"] == "CLARIFICATION_CHOICE"
    assert data["action_card"]["type"] != "BULK_SCOPE_PROPOSAL"
    assert "Which mechanical activity do you mean?" in data["reply_text"] or "mechanical" in data["reply_text"].lower()

    # Invariant: Options must present real activities
    options = data["action_card"]["options"]
    assert len(options) >= 5
    codes = [opt.get("value") for opt in options]
    assert "MEC-1001" in codes
    assert "MEC-1002" in codes

    # Safety Invariant: No database mutation occurred
    acts = db_session.query(Activity).filter(Activity.project_id == interactive_project.id).all()
    for a in acts:
        assert a.percent_complete in (0.0, 20.0, 30.0)


# =========================================================================
# TEST 2: Explicit bulk intent routes to bulk
# =========================================================================

def test_2_explicit_bulk_intent_routes_to_bulk(
    client: TestClient, interactive_project: Project
):
    """'update all mechanical activities to 80%' routes to existing explicit bulk path."""
    conv_res = client.post(f"/api/v1/projects/{interactive_project.id}/agent/conversations")
    conv_id = conv_res.json()["conversation_id"]

    resp = client.post(
        f"/api/v1/projects/{interactive_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "update all mechanical activities to 80%"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action_card"]["type"] == "BULK_SCOPE_PROPOSAL"
    assert data["action_card"]["bulk_count"] == 5


# =========================================================================
# TEST 3: Ambiguous candidates -> Question -> 'piping' -> Exactly 1 resolved
# =========================================================================

def test_3_clarification_keyword_piping_resolves_single_activity(
    client: TestClient, interactive_project: Project, db_session: Session
):
    """Answering 'piping' resolves specifically to MEC-1002 with original 80% attached."""
    conv_res = client.post(f"/api/v1/projects/{interactive_project.id}/agent/conversations")
    conv_id = conv_res.json()["conversation_id"]

    # Turn 1: ambiguous request
    client.post(
        f"/api/v1/projects/{interactive_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "update the mechanical work to 80%"},
    )

    # Turn 2: clarification answer 'piping'
    resp = client.post(
        f"/api/v1/projects/{interactive_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "piping"},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["action_card"] is not None
    assert data["action_card"]["type"] == "PROPOSAL_CONFIRMATION"
    assert data["action_card"]["activity_code"] == "MEC-1002"
    assert data["action_card"]["proposed_percent"] == 80.0
    assert "MEC-1002" in data["reply_text"]


# =========================================================================
# TEST 4: Ambiguous candidates -> Question -> 'second one' -> Ordinal selected
# =========================================================================

def test_4_clarification_ordinal_second_one_resolves(
    client: TestClient, interactive_project: Project
):
    """Answering 'second one' resolves to the 2nd presented option (MEC-1002)."""
    conv_res = client.post(f"/api/v1/projects/{interactive_project.id}/agent/conversations")
    conv_id = conv_res.json()["conversation_id"]

    # Turn 1: ambiguous request
    t1 = client.post(
        f"/api/v1/projects/{interactive_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "update the mechanical work to 80%"},
    )
    opts = t1.json()["action_card"]["options"]
    second_code = opts[1]["value"]

    # Turn 2: clarification answer 'second one'
    resp = client.post(
        f"/api/v1/projects/{interactive_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "second one"},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["action_card"]["type"] == "PROPOSAL_CONFIRMATION"
    assert data["action_card"]["activity_code"] == second_code
    assert data["action_card"]["proposed_percent"] == 80.0


# =========================================================================
# TEST 5: Yes/No Identification -> 'yes' resolves candidate (no mutation yet)
# =========================================================================

def test_5_yes_no_identification_affirmative_resolves_candidate(
    client: TestClient, interactive_project: Project, db_session: Session
):
    """Affirmative answer to identification question resolves identity without mutating schedule."""
    all_acts = db_session.query(Activity).filter(Activity.project_id == interactive_project.id).all()
    pump_act = next(a for a in all_acts if a.activity_code == "MEC-1003")

    session = ActivityResolutionSession(
        session_id="test-yesno-session",
        project_id=interactive_project.id,
        conversation_id="conv-yesno-test",
        original_user_reference="pump",
        candidate_activity_ids=["act-mec-1003"],
        extracted_clues={"reported_percent": 90.0},
        current_question={
            "question_text": "Is this the Mechanical Pump Installation activity (MEC-1003)?",
            "question_type": "yes_no",
            "target_activity_id": pump_act.id,
        },
    )

    step = InteractiveActivityResolver.process_answer(session, "yes", all_acts)
    assert step.status == ResolutionSessionStatus.RESOLVED
    assert step.resolved_activity.activity_code == "MEC-1003"
    assert session.resolved_activity_id == pump_act.id

    # Invariant: DB schedule not mutated merely by saying 'yes'
    assert pump_act.percent_complete == 30.0


# =========================================================================
# TEST 6: Yes/No Identification -> 'no' eliminates candidate and continues
# =========================================================================

def test_6_yes_no_identification_negative_eliminates_candidate(
    db_session: Session, interactive_project: Project
):
    """Negative answer eliminates targeted candidate and generates next question."""
    all_acts = db_session.query(Activity).filter(Activity.project_id == interactive_project.id).all()
    mec_acts = [a for a in all_acts if a.activity_code.startswith("MEC-")]

    session = ActivityResolutionSession(
        session_id="test-no-session",
        project_id=interactive_project.id,
        conversation_id="conv-no-test",
        original_user_reference="mechanical",
        candidate_activity_ids=[a.id for a in mec_acts],
        extracted_clues={"reported_percent": 80.0},
        current_question={
            "question_text": "Is this the Mechanical Equipment Installation activity?",
            "question_type": "yes_no",
            "target_activity_id": "act-mec-1001",
        },
    )

    step = InteractiveActivityResolver.process_answer(session, "no", all_acts)
    assert step.status == ResolutionSessionStatus.ACTIVE
    assert "act-mec-1001" not in session.candidate_activity_ids
    assert len(session.candidate_activity_ids) == 4


# =========================================================================
# TEST 7: User answers with exact activity code -> Candidate resolved
# =========================================================================

def test_7_user_answers_with_exact_activity_code(
    client: TestClient, interactive_project: Project
):
    """Answering with exact code 'MEC-1004' directly resolves to MEC-1004."""
    conv_res = client.post(f"/api/v1/projects/{interactive_project.id}/agent/conversations")
    conv_id = conv_res.json()["conversation_id"]

    client.post(
        f"/api/v1/projects/{interactive_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "update the mechanical work to 80%"},
    )
    resp = client.post(
        f"/api/v1/projects/{interactive_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "MEC-1004"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action_card"]["activity_code"] == "MEC-1004"
    assert data["action_card"]["proposed_percent"] == 80.0


# =========================================================================
# TEST 8: User answers with typo -> Controlled typo tolerance
# =========================================================================

def test_8_user_answers_with_typo(
    client: TestClient, interactive_project: Project
):
    """Answering 'pipng' (typo for piping) resolves to MEC-1002."""
    conv_res = client.post(f"/api/v1/projects/{interactive_project.id}/agent/conversations")
    conv_id = conv_res.json()["conversation_id"]

    client.post(
        f"/api/v1/projects/{interactive_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "update the mechanical work to 80%"},
    )
    resp = client.post(
        f"/api/v1/projects/{interactive_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "pipng"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action_card"]["activity_code"] == "MEC-1002"


# =========================================================================
# TEST 9: Clarification answer 'Unit 4' -> Filters to matching candidate
# =========================================================================

def test_9_clarification_answer_unit_4(
    db_session: Session, interactive_project: Project
):
    """Answering 'Unit 4' filters civil foundation candidates to Unit 4."""
    all_acts = db_session.query(Activity).filter(Activity.project_id == interactive_project.id).all()
    civ_acts = [a for a in all_acts if a.discipline == "Civil"]

    session, _ = InteractiveActivityResolver.start_session(
        project_id=interactive_project.id,
        conversation_id="conv-u4",
        original_reference="foundation pour",
        candidate_activities=civ_acts,
        extracted_clues={"reported_percent": 50.0},
    )

    step = InteractiveActivityResolver.process_answer(session, "Unit 4", all_acts)
    assert step.status == ResolutionSessionStatus.RESOLVED
    assert step.resolved_activity.activity_code == "CIV-2001"
    assert "Unit 4" in step.resolved_activity.name


# =========================================================================
# TEST 10: 3 Unresolved rounds -> Controlled failure response, no mutation
# =========================================================================

def test_10_three_unresolved_rounds_terminates_gracefully(
    db_session: Session, interactive_project: Project
):
    """3 failed clarification rounds result in controlled FAILED status without infinite loop."""
    all_acts = db_session.query(Activity).filter(Activity.project_id == interactive_project.id).all()
    mec_acts = [a for a in all_acts if a.discipline == "Mechanical"]

    session, _ = InteractiveActivityResolver.start_session(
        project_id=interactive_project.id,
        conversation_id="conv-fail",
        original_reference="mechanical",
        candidate_activities=mec_acts,
        extracted_clues={"reported_percent": 80.0},
    )
    assert session.round_number == 1

    # Round 1 answer: nonsense
    step1 = InteractiveActivityResolver.process_answer(session, "something totally unrelated", all_acts)
    assert step1.status == ResolutionSessionStatus.ACTIVE
    assert session.round_number == 2

    # Round 2 answer: nonsense
    step2 = InteractiveActivityResolver.process_answer(session, "still unrelated random words", all_acts)
    assert step2.status == ResolutionSessionStatus.ACTIVE
    assert session.round_number == 3

    # Round 3 answer: nonsense
    step3 = InteractiveActivityResolver.process_answer(session, "cannot find it", all_acts)
    assert step3.status == ResolutionSessionStatus.FAILED
    assert "couldn't uniquely identify" in step3.question_text.lower()


# =========================================================================
# TEST 11: Single activity resolution 'mechanical activity 2 98%'
# =========================================================================

def test_11_single_activity_resolution_mechanical_2_98_percent(
    client: TestClient, interactive_project: Project
):
    """'mechanical activity 2 98%' directly resolves to MEC-1002 at 98.0%."""
    conv_res = client.post(f"/api/v1/projects/{interactive_project.id}/agent/conversations")
    conv_id = conv_res.json()["conversation_id"]

    resp = client.post(
        f"/api/v1/projects/{interactive_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "mechanical activity 2 98%"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action_card"]["activity_code"] == "MEC-1002"
    assert data["action_card"]["proposed_percent"] == 98.0


# =========================================================================
# TEST 12: 'completed mechanical activity 2' infers 100%
# =========================================================================

def test_12_completed_mechanical_activity_2_infers_100_percent(
    client: TestClient, interactive_project: Project
):
    """'completed mechanical activity 2' without explicit percentage infers 100%."""
    conv_res = client.post(f"/api/v1/projects/{interactive_project.id}/agent/conversations")
    conv_id = conv_res.json()["conversation_id"]

    resp = client.post(
        f"/api/v1/projects/{interactive_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "completed mechanical activity 2"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action_card"]["activity_code"] == "MEC-1002"
    assert data["action_card"]["proposed_percent"] == 100.0


# =========================================================================
# TEST 13: Percentage remains attached throughout clarification session
# =========================================================================

def test_13_percentage_remains_attached_throughout_clarification(
    client: TestClient, interactive_project: Project
):
    """98% stated in turn 1 remains attached when user clarifies with 'piping' on turn 2."""
    conv_res = client.post(f"/api/v1/projects/{interactive_project.id}/agent/conversations")
    conv_id = conv_res.json()["conversation_id"]

    client.post(
        f"/api/v1/projects/{interactive_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "mechanical work 98%"},
    )
    resp = client.post(
        f"/api/v1/projects/{interactive_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "piping"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action_card"]["activity_code"] == "MEC-1002"
    assert data["action_card"]["proposed_percent"] == 98.0


# =========================================================================
# TEST 14: Multi-activity message with one ambiguous activity
# =========================================================================

def test_14_multi_activity_one_ambiguous_one_unambiguous(
    client: TestClient, interactive_project: Project
):
    """
    'mechanical activity 1 75% and mechanical work 90%':
    MEC-1001 is resolved, clarification asked for 'mechanical work' with 90% retained.
    """
    conv_res = client.post(f"/api/v1/projects/{interactive_project.id}/agent/conversations")
    conv_id = conv_res.json()["conversation_id"]

    # Turn 1: Multi-activity input with 1 ambiguous clause
    t1 = client.post(
        f"/api/v1/projects/{interactive_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "mechanical activity 1 75% and mechanical work 90%"},
    )
    assert t1.status_code == 200
    d1 = t1.json()
    assert d1["action_card"]["type"] == "CLARIFICATION_CHOICE"

    # Turn 2: Clarify the ambiguous clause
    t2 = client.post(
        f"/api/v1/projects/{interactive_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "piping"},
    )
    assert t2.status_code == 200
    d2 = t2.json()

    # Both activities should now be presented in multi-activity proposal
    assert d2["action_card"]["type"] == "BULK_SCOPE_PROPOSAL"
    assert d2["action_card"]["is_multi_activity"] is True
    acts = d2["action_card"]["bulk_activities"]
    assert len(acts) == 2

    act1 = next(a for a in acts if a["activity_code"] == "MEC-1001")
    act2 = next(a for a in acts if a["activity_code"] == "MEC-1002")
    assert act1["proposed_percent"] == 75.0
    assert act2["proposed_percent"] == 90.0


# =========================================================================
# TEST 15: Cross-project candidate rejection
# =========================================================================

def test_15_cross_project_candidate_attempt_rejected(
    db_session: Session, interactive_project: Project
):
    """Candidates from another project cannot enter the resolution session."""
    foreign_act = db_session.query(Activity).filter(Activity.id == "act-foreign-9999").first()
    assert foreign_act is not None

    session, step = InteractiveActivityResolver.start_session(
        project_id=interactive_project.id,
        conversation_id="conv-safe",
        original_reference="foreign work",
        candidate_activities=[foreign_act],
        extracted_clues={"reported_percent": 10.0},
    )
    # Foreign candidate must have been rejected
    assert len(session.candidate_activity_ids) == 0
    assert step.status == ResolutionSessionStatus.FAILED


# =========================================================================
# TEST 16: Clarification session followed by cancellation -> No accidental mutation
# =========================================================================

def test_16_clarification_session_followed_by_cancellation(
    client: TestClient, interactive_project: Project, db_session: Session
):
    """User cancels during clarification -> Session terminates safely without schedule mutation."""
    conv_res = client.post(f"/api/v1/projects/{interactive_project.id}/agent/conversations")
    conv_id = conv_res.json()["conversation_id"]

    client.post(
        f"/api/v1/projects/{interactive_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "update the mechanical work to 80%"},
    )
    resp = client.post(
        f"/api/v1/projects/{interactive_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "cancel"},
    )
    assert resp.status_code == 200
    assert "cancel" in resp.json()["reply_text"].lower()

    # Verify no proposals pending and no progress mutated
    props = db_session.query(UpdateProposal).filter(UpdateProposal.conversation_id == conv_id).all()
    assert len(props) == 0
    acts = db_session.query(Activity).filter(Activity.project_id == interactive_project.id).all()
    for a in acts:
        assert a.percent_complete in (0.0, 20.0, 30.0)


# =========================================================================
# TEST 17: Repeated identical clarification answer does not infinite-loop
# =========================================================================

def test_17_repeated_identical_clarification_answer_bounded(
    db_session: Session, interactive_project: Project
):
    """Repeatedly typing the same unrecognized string advances turns and terminates safely."""
    all_acts = db_session.query(Activity).filter(Activity.project_id == interactive_project.id).all()
    mec_acts = [a for a in all_acts if a.discipline == "Mechanical"]

    session, _ = InteractiveActivityResolver.start_session(
        project_id=interactive_project.id,
        conversation_id="conv-loop",
        original_reference="mechanical",
        candidate_activities=mec_acts,
        extracted_clues={"reported_percent": 80.0},
    )

    # 3 repetitions of identical invalid input
    InteractiveActivityResolver.process_answer(session, "repeat", all_acts)
    assert session.round_number == 2
    InteractiveActivityResolver.process_answer(session, "repeat", all_acts)
    assert session.round_number == 3
    final_step = InteractiveActivityResolver.process_answer(session, "repeat", all_acts)

    assert final_step.status == ResolutionSessionStatus.FAILED
    assert session.status == ResolutionSessionStatus.FAILED.value


# =========================================================================
# TEST 18: Invariant - No mutation before confirmation
# =========================================================================

def test_18_invariant_no_mutation_before_confirmation(
    client: TestClient, interactive_project: Project, db_session: Session
):
    """Schedule in DB remains completely unchanged until user confirms the proposal."""
    conv_res = client.post(f"/api/v1/projects/{interactive_project.id}/agent/conversations")
    conv_id = conv_res.json()["conversation_id"]

    # Turn 1: ambiguous
    client.post(
        f"/api/v1/projects/{interactive_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "mechanical work 80%"},
    )
    # Turn 2: clarified to MEC-1002
    res2 = client.post(
        f"/api/v1/projects/{interactive_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "piping"},
    )
    proposal_id = res2.json()["action_card"]["proposal_id"]
    assert proposal_id is not None

    # Verify activity percent complete in DB is STILL 20.0 (unmutated!)
    act_db = db_session.query(Activity).filter(Activity.activity_code == "MEC-1002").first()
    assert act_db.percent_complete == 20.0

    # Turn 3: Confirm the update
    conf_res = client.post(
        f"/api/v1/projects/{interactive_project.id}/agent/conversations/{conv_id}/confirm",
        json={"proposal_id": proposal_id, "action": "CONFIRM"},
    )
    assert conf_res.status_code == 200
    assert conf_res.json()["status"] == "APPLIED"

    # Now verify DB percent complete was mutated to 80.0
    db_session.refresh(act_db)
    assert act_db.percent_complete == 80.0
