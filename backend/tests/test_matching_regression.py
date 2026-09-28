import pytest
from datetime import datetime
from sqlalchemy.orm import Session

from app.domain.models import Activity, ExecutionEvent, Project, WBSNode
from app.services.matching_service import MatchingService


def test_preposition_stopword_not_matching_morphological_substrings():
    """
    Test A: The preposition 'for' must NOT match 'formwork' or 'reinforcement'.
    Evidence: 'poured concrete for the Pier 14 cap beam'
    Candidate: 'Pier 14 Reinforcement & Formwork'
    """
    text_event = "poured concrete for the Pier 14 cap beam"
    candidate_name = "Pier 14 Reinforcement & Formwork"

    sim = MatchingService.calculate_text_similarity(text_event, candidate_name)
    # With 4 tokens in candidate ('pier', '14', 'reinforcement', 'formwork'),
    # only 'pier' and '14' should match (coverage = 2/4 = 0.50, jaccard ~ 0.08).
    # Similarity should be ~0.395, definitely <= 0.50.
    # Prior buggy behavior produced 0.77 because 'for' matched 'formwork' and 'reinforcement'.
    assert sim <= 0.50, f"Expected text similarity <= 0.50, got {sim} (false 'for' substring match)"


def test_pier14_concrete_pour_scores_substantially_higher_than_distractors(db_session: Session):
    """
    Test B: Pier 14 Cap Beam Concrete Pour must score substantially higher than
    formwork, curing, and pedestal installation. Margin must be >= 0.15.
    """
    proj = Project(
        id="proj-regr-1",
        project_code="REGR-01",
        name="Bridge Pier Project",
        planned_start=datetime(2026, 1, 1),
        planned_finish=datetime(2026, 12, 31),
    )
    db_session.add(proj)

    wbs = WBSNode(
        id="wbs-regr-1",
        project_id="proj-regr-1",
        code="WBS-1.2",
        name="Substructure - Bridge Piers",
    )
    db_session.add(wbs)

    act_pour = Activity(
        id="act-civ-2040",
        project_id="proj-regr-1",
        wbs_id="wbs-regr-1",
        activity_code="CIV-2040",
        name="Pier 14 Cap Beam Concrete Pour",
        status="NOT_STARTED",
        planned_start=datetime(2026, 9, 1),
        planned_finish=datetime(2026, 9, 30),
        percent_complete=0.0,
        planned_quantity=200.0,
        quantity_unit="m3",
        location_code="Pier 14",
        discipline="Civil / Structural",
    )
    act_formwork = Activity(
        id="act-civ-2030",
        project_id="proj-regr-1",
        wbs_id="wbs-regr-1",
        activity_code="CIV-2030",
        name="Pier 14 Reinforcement & Formwork",
        status="NOT_STARTED",
        planned_start=datetime(2026, 9, 1),
        planned_finish=datetime(2026, 9, 30),
        percent_complete=0.0,
        planned_quantity=15.0,
        quantity_unit="tons",
        location_code="Pier 14",
        discipline="Civil / Structural",
    )
    act_curing = Activity(
        id="act-civ-2050",
        project_id="proj-regr-1",
        wbs_id="wbs-regr-1",
        activity_code="CIV-2050",
        name="Pier 14 Concrete Curing & Inspection",
        status="NOT_STARTED",
        planned_start=datetime(2026, 9, 1),
        planned_finish=datetime(2026, 9, 30),
        percent_complete=0.0,
        location_code="Pier 14",
        discipline="Civil / Structural",
    )
    act_pedestal = Activity(
        id="act-str-3010",
        project_id="proj-regr-1",
        wbs_id="wbs-regr-1",
        activity_code="STR-3010",
        name="Pier 14 Bearing Pedestal Installation",
        status="NOT_STARTED",
        planned_start=datetime(2026, 9, 1),
        planned_finish=datetime(2026, 9, 30),
        percent_complete=0.0,
        location_code="Pier 14",
        discipline="Civil / Structural",
    )
    db_session.add_all([act_pour, act_formwork, act_curing, act_pedestal])
    db_session.commit()

    event = ExecutionEvent(
        id="ev-regr-pier14",
        project_id="proj-regr-1",
        verbatim_excerpt="On 15 September 2026, the civil team poured 140 m3 of concrete for the Pier 14 cap beam between 08:00 and 16:30.",
        description="Poured 140 m3 of concrete for the Pier 14 cap beam",
        activity_reference="Pier 14 cap beam",
        execution_date=datetime(2026, 9, 15),
        quantity=140.0,
        unit="m3",
        location="Pier 14",
        discipline="Civil / Structural",
        status_reported="IN_PROGRESS",
        extraction_confidence=0.95,
        status="UNMATCHED",
    )
    db_session.add(event)
    db_session.commit()

    routing = MatchingService.evaluate_event(db_session, event)

    assert routing.selected_candidate is not None
    assert routing.selected_candidate.activity_code == "CIV-2040"
    assert routing.selected_candidate.match_score >= 0.85

    cand_scores = {c.activity_code: c.match_score for c in routing.all_candidates}
    pour_score = cand_scores["CIV-2040"]
    formwork_score = cand_scores["CIV-2030"]

    # The second candidate (formwork) must not score ~0.90
    assert formwork_score <= 0.75, f"CIV-2030 formwork scored {formwork_score}, expected <= 0.75"
    
    # Margin must satisfy >= 0.15
    margin = round(pour_score - formwork_score, 3)
    assert margin >= 0.15, f"Expected margin >= 0.15, got {margin}"
    
    # Confident match with clean margin and high extraction confidence must route to AUTO_LINK
    assert routing.route == "AUTO_LINK", f"Expected AUTO_LINK, got {routing.route}"


def test_location_alone_does_not_guarantee_88_score(db_session: Session):
    """
    Test C: Location alone must NOT guarantee an 0.88+ score.
    An unrelated activity at the same location must not be floored at 0.88.
    """
    proj = Project(
        id="proj-regr-2",
        project_code="REGR-02",
        name="Bridge Pier Project",
        planned_start=datetime(2026, 1, 1),
        planned_finish=datetime(2026, 12, 31),
    )
    db_session.add(proj)

    wbs = WBSNode(
        id="wbs-regr-2",
        project_id="proj-regr-2",
        code="WBS-2.1",
        name="Electrical & Instrumentation",
    )
    db_session.add(wbs)

    act_electrical = Activity(
        id="act-elec-4010",
        project_id="proj-regr-2",
        wbs_id="wbs-regr-2",
        activity_code="ELE-4010",
        name="Pier 14 Navigational Lighting & Conduit Installation",
        status="NOT_STARTED",
        planned_start=datetime(2026, 9, 1),
        planned_finish=datetime(2026, 9, 30),
        percent_complete=0.0,
        location_code="Pier 14",
        discipline="Electrical",
    )
    db_session.add(act_electrical)
    db_session.commit()

    # Evidence is concrete pour
    event = ExecutionEvent(
        id="ev-regr-elec",
        project_id="proj-regr-2",
        verbatim_excerpt="Poured concrete for the cap beam at Pier 14.",
        description="Poured concrete for the cap beam",
        execution_date=datetime(2026, 9, 15),
        location="Pier 14",
        discipline="Civil / Structural",
        status_reported="IN_PROGRESS",
        extraction_confidence=0.90,
        status="UNMATCHED",
    )

    score, breakdown = MatchingService.score_activity(event, act_electrical, wbs)
    # The score should reflect the weak semantic alignment, NOT be forced to 0.88
    assert score < 0.80, f"Unrelated electrical activity scored {score}, expected < 0.80 (should not be floored at 0.88)"


def test_location_preserves_discriminating_signal(db_session: Session):
    """
    Test D: Location must remain an effective discriminating signal between identical
    activities located at different piers.
    """
    proj = Project(
        id="proj-regr-3",
        project_code="REGR-03",
        name="Bridge Pier Project",
        planned_start=datetime(2026, 1, 1),
        planned_finish=datetime(2026, 12, 31),
    )
    db_session.add(proj)

    wbs = WBSNode(
        id="wbs-regr-3",
        project_id="proj-regr-3",
        code="WBS-1.2",
        name="Substructure - Bridge Piers",
    )
    db_session.add(wbs)

    act_pier14 = Activity(
        id="act-civ-p14",
        project_id="proj-regr-3",
        wbs_id="wbs-regr-3",
        activity_code="CIV-2040",
        name="Pier 14 Cap Beam Concrete Pour",
        status="NOT_STARTED",
        planned_start=datetime(2026, 9, 1),
        planned_finish=datetime(2026, 9, 30),
        percent_complete=0.0,
        location_code="Pier 14",
        discipline="Civil / Structural",
    )
    act_pier15 = Activity(
        id="act-civ-p15",
        project_id="proj-regr-3",
        wbs_id="wbs-regr-3",
        activity_code="CIV-2041",
        name="Pier 15 Cap Beam Concrete Pour",
        status="NOT_STARTED",
        planned_start=datetime(2026, 9, 1),
        planned_finish=datetime(2026, 9, 30),
        percent_complete=0.0,
        location_code="Pier 15",
        discipline="Civil / Structural",
    )
    db_session.add_all([act_pier14, act_pier15])
    db_session.commit()

    event = ExecutionEvent(
        id="ev-regr-loc",
        project_id="proj-regr-3",
        verbatim_excerpt="Poured concrete for cap beam at Pier 14.",
        description="Poured concrete for cap beam",
        execution_date=datetime(2026, 9, 15),
        location="Pier 14",
        discipline="Civil / Structural",
        status_reported="IN_PROGRESS",
        extraction_confidence=0.90,
        status="UNMATCHED",
    )

    score_14, _ = MatchingService.score_activity(event, act_pier14, wbs)
    score_15, _ = MatchingService.score_activity(event, act_pier15, wbs)

    assert score_14 > score_15 + 0.10, f"Pier 14 ({score_14}) should significantly outscore Pier 15 ({score_15})"


def test_confidence_firewall_ambiguous_case_routes_to_planner_review(db_session: Session):
    """
    Test E: Ambiguous case with competing candidates (margin < 0.15) must
    preserve the confidence firewall and route to PLANNER_REVIEW.
    """
    proj = Project(
        id="proj-regr-4",
        project_code="REGR-04",
        name="Bridge Pier Project",
        planned_start=datetime(2026, 1, 1),
        planned_finish=datetime(2026, 12, 31),
    )
    db_session.add(proj)

    wbs = WBSNode(
        id="wbs-regr-4",
        project_id="proj-regr-4",
        code="WBS-1.2",
        name="Substructure - Bridge Piers",
    )
    db_session.add(wbs)

    # Two identical trade activities where evidence does not specify North vs South
    act_a = Activity(
        id="act-civ-north",
        project_id="proj-regr-4",
        wbs_id="wbs-regr-4",
        activity_code="CIV-1101",
        name="Pier 14 Cap Beam Concrete Pour - North Half",
        status="NOT_STARTED",
        planned_start=datetime(2026, 9, 1),
        planned_finish=datetime(2026, 9, 30),
        percent_complete=0.0,
        location_code="Pier 14",
        discipline="Civil / Structural",
    )
    act_b = Activity(
        id="act-civ-south",
        project_id="proj-regr-4",
        wbs_id="wbs-regr-4",
        activity_code="CIV-1102",
        name="Pier 14 Cap Beam Concrete Pour - South Half",
        status="NOT_STARTED",
        planned_start=datetime(2026, 9, 1),
        planned_finish=datetime(2026, 9, 30),
        percent_complete=0.0,
        location_code="Pier 14",
        discipline="Civil / Structural",
    )
    db_session.add_all([act_a, act_b])
    db_session.commit()

    event = ExecutionEvent(
        id="ev-regr-ambig",
        project_id="proj-regr-4",
        verbatim_excerpt="Poured concrete for cap beam at Pier 14.",
        description="Poured concrete for cap beam",
        execution_date=datetime(2026, 9, 15),
        location="Pier 14",
        discipline="Civil / Structural",
        status_reported="IN_PROGRESS",
        extraction_confidence=0.90,
        status="UNMATCHED",
    )
    db_session.add(event)
    db_session.commit()

    routing = MatchingService.evaluate_event(db_session, event)
    # Both candidates should score similarly high, causing margin < 0.15
    assert routing.route == "PLANNER_REVIEW", f"Expected PLANNER_REVIEW for ambiguous split activities, got {routing.route}"
