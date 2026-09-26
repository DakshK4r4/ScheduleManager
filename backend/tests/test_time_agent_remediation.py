import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.models import Activity, ExecutionEvent, Project, ScheduleAuditLog, UpdateProposal, WBSNode
from app.schemas.agent import BulkProposalConfirmRequest, ProposalConfirmRequest
from app.services.activity_reference_resolver import ActivityReferenceResolver
from app.services.agent_parser import ConversationalParser
from app.services.agent_service import TimeAgentService


@pytest.fixture
def remediation_project(db_session: Session) -> Project:
    """Authoritative project fixture for remediation verification."""
    proj = Project(
        id="proj-remediation-test",
        project_code="REMED_001",
        name="Remediation Testing Facility",
        planned_start=datetime(2024, 1, 1, 8, 0),
        planned_finish=datetime(2025, 12, 31, 17, 0),
        data_date=datetime(2024, 10, 1, 0, 0),
    )
    db_session.add(proj)

    wbs_mec = WBSNode(id="wbs-remed-mec", project_id=proj.id, code="WBS-MEC", name="Mechanical Package")
    wbs_civ = WBSNode(id="wbs-remed-civ", project_id=proj.id, code="WBS-CIV", name="Civil Package")
    db_session.add_all([wbs_mec, wbs_civ])

    mec_activities = [
        Activity(
            id=f"act-mec-{i:04d}",
            project_id=proj.id,
            wbs_id=wbs_mec.id,
            activity_code=f"MEC-{i:04d}",
            name=f"Mechanical Activity {i-1000:02d}",
            status="IN_PROGRESS" if i > 1001 else "NOT_STARTED",
            planned_start=datetime(2024, 8, 1, 8, 0),
            planned_finish=datetime(2024, 10, 31, 17, 0),
            original_duration=90.0,
            percent_complete=0.0 if i == 1001 else (10.0 * (i - 1000)),
            planned_quantity=100.0,
            quantity_unit="nos",
        )
        for i in range(1001, 1006)
    ]
    civ_activity = Activity(
        id="act-civ-1001",
        project_id=proj.id,
        wbs_id=wbs_civ.id,
        activity_code="CIV-1001",
        name="Foundation Concrete Pour F-204",
        status="NOT_STARTED",
        planned_start=datetime(2024, 8, 1, 8, 0),
        planned_finish=datetime(2024, 9, 30, 17, 0),
        original_duration=60.0,
        percent_complete=0.0,
        planned_quantity=50.0,
        quantity_unit="m3",
    )
    for a in mec_activities:
        db_session.add(a)
    db_session.add(civ_activity)
    db_session.commit()
    return proj


# =========================================================================
# 1. PARSER DETERMINISTIC REPRODUCTION & PRECEDENCE TESTS
# =========================================================================

def test_parser_multi_activity_distinct_percentages():
    """Verify multi-activity parsing extracts individual references and percentages."""
    text = "i have done mechanical activity 1 75% and mechanical acitivity 2 98%"
    parsed = ConversationalParser.parse_with_rules(text)
    assert len(parsed.activity_updates) == 2
    assert parsed.is_explicit_bulk is False
    assert parsed.is_bulk is False

    upd1, upd2 = parsed.activity_updates
    assert "mechanical activity 1" in upd1.activity_reference.lower()
    assert upd1.reported_percent == 75.0
    assert "mechanical acitivity 2" in upd2.activity_reference.lower()
    assert upd2.reported_percent == 98.0


def test_parser_single_activity_completed_does_not_overwrite_percentage():
    """Verify 'completed' alongside explicit 98% keeps 98% and does not trigger bulk."""
    text = "i have completed mechanical activity 02 98%"
    parsed = ConversationalParser.parse_with_rules(text)
    assert parsed.override_percent == 98.0
    assert parsed.is_explicit_bulk is False
    assert parsed.is_bulk is False
    assert len(parsed.activity_updates) == 1
    assert parsed.activity_updates[0].reported_percent == 98.0


def test_parser_activity_number_not_hijacked_as_quantity():
    """Verify 'activity 1' is not hijacked into physical quantity."""
    text = "mechanical activity 1 75%"
    parsed = ConversationalParser.parse_with_rules(text)
    assert parsed.override_percent == 75.0
    assert parsed.quantity is None
    assert len(parsed.activity_updates) == 1
    assert parsed.activity_updates[0].reported_percent == 75.0


def test_parser_explicit_bulk_intent_requires_all_marker():
    """Verify bulk intent only fires when explicit bulk keyword like 'all' is present."""
    text_bulk = "update all mechanical activities to 100%"
    parsed_bulk = ConversationalParser.parse_with_rules(text_bulk)
    assert parsed_bulk.is_explicit_bulk is True
    assert parsed_bulk.intent == "BULK_PROGRESS_REPORT"

    text_non_bulk = "mechanical activities"
    parsed_non_bulk = ConversationalParser.parse_with_rules(text_non_bulk)
    assert parsed_non_bulk.is_explicit_bulk is False


# =========================================================================
# 2. ACTIVITY REFERENCE RESOLVER DETERMINISTIC STAGES
# =========================================================================

def test_resolver_exact_code_variants(remediation_project: Project, db_session: Session):
    acts = db_session.query(Activity).filter(Activity.project_id == remediation_project.id).all()
    for variant in ["MEC-1002", "mec-1002", "MEC 1002", "MEC1002"]:
        res = ActivityReferenceResolver.resolve(remediation_project.id, acts, variant)
        assert res.resolved_activity is not None
        assert res.resolved_activity.activity_code == "MEC-1002"
        assert res.method == "exact_code"


def test_resolver_code_suffix_numeric(remediation_project: Project, db_session: Session):
    acts = db_session.query(Activity).filter(Activity.project_id == remediation_project.id).all()
    res = ActivityReferenceResolver.resolve(remediation_project.id, acts, "1002")
    assert res.resolved_activity is not None
    assert res.resolved_activity.activity_code == "MEC-1002"
    assert res.method == "suffix_numeric"


def test_resolver_numeric_padding_equivalence(remediation_project: Project, db_session: Session):
    acts = db_session.query(Activity).filter(Activity.project_id == remediation_project.id).all()
    # 'mechanical activity 2' <-> 'Mechanical Activity 02'
    res1 = ActivityReferenceResolver.resolve(remediation_project.id, acts, "mechanical activity 2")
    assert res1.resolved_activity is not None
    assert res1.resolved_activity.activity_code == "MEC-1002"

    res2 = ActivityReferenceResolver.resolve(remediation_project.id, acts, "mechanical activity 02")
    assert res2.resolved_activity is not None
    assert res2.resolved_activity.activity_code == "MEC-1002"


def test_resolver_controlled_typo_tolerance(remediation_project: Project, db_session: Session):
    acts = db_session.query(Activity).filter(Activity.project_id == remediation_project.id).all()
    res = ActivityReferenceResolver.resolve(remediation_project.id, acts, "mechanical acitivity 2")
    assert res.resolved_activity is not None
    assert res.resolved_activity.activity_code == "MEC-1002"


def test_resolver_ambiguous_does_not_auto_bulk(remediation_project: Project, db_session: Session):
    acts = db_session.query(Activity).filter(Activity.project_id == remediation_project.id).all()
    # Broad 'mechanical' without number
    res = ActivityReferenceResolver.resolve(remediation_project.id, acts, "mechanical", explicit_discipline="Mechanical")
    assert res.resolved_activity is None
    assert res.method == "ambiguous"
    assert len(res.candidates) >= 2


def test_resolver_cross_project_isolation(db_session: Session):
    p1 = Project(id="p-iso-1", project_code="ISO_1", name="Project 1")
    p2 = Project(id="p-iso-2", project_code="ISO_2", name="Project 2")
    a1 = Activity(id="a-1", project_id=p1.id, activity_code="MEC-1001", name="Pump Alignment")
    a2 = Activity(id="a-2", project_id=p2.id, activity_code="MEC-1001", name="Pump Alignment")
    db_session.add_all([p1, p2, a1, a2])
    db_session.commit()

    # Querying p1 must strictly resolve a1, never a2
    res = ActivityReferenceResolver.resolve(p1.id, [a1, a2], "MEC-1001")
    assert res.resolved_activity.id == a1.id


# =========================================================================
# 3. END-TO-END CONVERSATIONAL FLOW & PROPOSAL SAFETY TESTS
# =========================================================================

def test_e2e_multi_activity_proposal_and_confirm(
    client: TestClient, remediation_project: Project, db_session: Session
):
    """
    Test that 'i have done mechanical activity 1 75% and mechanical acitivity 2 98%'
    proposes both activities with their exact reported percentages and applies them accurately.
    """
    conv_res = client.post(
        f"/api/v1/projects/{remediation_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = conv_res.json()["conversation_id"]

    msg_res = client.post(
        f"/api/v1/projects/{remediation_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "i have done mechanical activity 1 75% and mechanical acitivity 2 98%"},
    )
    assert msg_res.status_code == 200
    data = msg_res.json()
    card = data.get("action_card")
    assert card is not None
    assert card["type"] == "BULK_SCOPE_PROPOSAL"
    assert card["is_multi_activity"] is True
    assert card["bulk_count"] == 2

    bulk_acts = {a["activity_code"]: a["proposed_percent"] for a in card["bulk_activities"]}
    assert bulk_acts["MEC-1001"] == 75.0
    assert bulk_acts["MEC-1002"] == 98.0

    # Confirm via API
    conf_res = client.post(
        f"/api/v1/projects/{remediation_project.id}/agent/conversations/{conv_id}/bulk-confirm",
        json={
            "action": "CONFIRM",
            "activity_ids": [a["activity_id"] for a in card["bulk_activities"]],
        },
    )
    assert conf_res.status_code == 200
    conf_data = conf_res.json()
    assert conf_data["status"] == "APPLIED"
    assert conf_data["updated_count"] == 2

    # Verify authoritative database state
    act1 = db_session.query(Activity).filter(Activity.activity_code == "MEC-1001").first()
    act2 = db_session.query(Activity).filter(Activity.activity_code == "MEC-1002").first()
    assert act1.percent_complete == 75.0
    assert act2.percent_complete == 98.0


def test_e2e_single_activity_completed_98_percent(
    client: TestClient, remediation_project: Project, db_session: Session
):
    """
    Test that 'i have completed mechanical activity 02 98%' resolves specifically
    to MEC-1002 and stages single proposal with 98% (NOT 100%, and NOT 5 activities).
    """
    conv_res = client.post(
        f"/api/v1/projects/{remediation_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = conv_res.json()["conversation_id"]

    msg_res = client.post(
        f"/api/v1/projects/{remediation_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "i have completed mechanical activity 02 98%"},
    )
    assert msg_res.status_code == 200
    card = msg_res.json().get("action_card")
    assert card is not None
    assert card["type"] == "PROPOSAL_CONFIRMATION"
    assert card["activity_code"] == "MEC-1002"
    assert card["proposed_percent"] == 98.0

    # Confirm proposal
    conf_res = client.post(
        f"/api/v1/projects/{remediation_project.id}/agent/conversations/{conv_id}/confirm",
        json={"proposal_id": card["proposal_id"], "action": "CONFIRM"},
    )
    assert conf_res.status_code == 200
    act2 = db_session.query(Activity).filter(Activity.activity_code == "MEC-1002").first()
    assert act2.percent_complete == 98.0


def test_e2e_pending_proposal_informational_query(
    client: TestClient, remediation_project: Project, db_session: Session
):
    """Verify that while a single proposal is waiting, informational queries are answered."""
    conv_res = client.post(
        f"/api/v1/projects/{remediation_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = conv_res.json()["conversation_id"]

    # Stage proposal
    res1 = client.post(
        f"/api/v1/projects/{remediation_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Update MEC-1004 to 80%."},
    )
    assert res1.json()["action_card"]["type"] == "PROPOSAL_CONFIRMATION"

    # Ask informational query
    res2 = client.post(
        f"/api/v1/projects/{remediation_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "What is the status of F-204?"},
    )
    assert res2.status_code == 200
    assert "CIV-1001" in res2.json()["reply_text"]


def test_e2e_pending_proposal_fast_cancellation(
    client: TestClient, remediation_project: Project, db_session: Session
):
    """Verify that sending 'cancel' cancels pending proposal cleanly without modifying schedule."""
    conv_res = client.post(
        f"/api/v1/projects/{remediation_project.id}/agent/conversations",
        json={"force_new": True},
    )
    conv_id = conv_res.json()["conversation_id"]

    # Stage proposal
    client.post(
        f"/api/v1/projects/{remediation_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Update MEC-1004 to 80%."},
    )

    # Cancel via chat
    cancel_res = client.post(
        f"/api/v1/projects/{remediation_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "cancel"},
    )
    assert cancel_res.status_code == 200
    assert "cancelled" in cancel_res.json()["reply_text"].lower()

    # Verify no schedule change
    act = db_session.query(Activity).filter(Activity.activity_code == "MEC-1004").first()
    assert act.percent_complete == 40.0
