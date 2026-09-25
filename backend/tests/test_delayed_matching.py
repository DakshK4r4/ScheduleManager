from datetime import datetime
import pytest
from sqlalchemy.orm import Session

from app.domain.models import Activity, ExecutionEvent, Project, WBSNode
from app.services.matching_service import MatchingService


def test_delayed_in_progress_activity_matching(db_session: Session):
    # 1. Setup project
    proj = Project(
        id="proj-delayed-1",
        project_code="DELAYED_PROJECT",
        name="Delayed Project Demo",
        planned_start=datetime(2024, 1, 1, 8, 0),
        planned_finish=datetime(2024, 12, 31, 17, 0),
    )
    db_session.add(proj)

    # 2. Add WBS
    wbs = WBSNode(
        id="wbs-del-1",
        project_id="proj-delayed-1",
        code="WBS-CIVIL",
        name="Civil Works",
    )
    db_session.add(wbs)

    # 3. Add delayed activity (planned Jan, but delayed and still IN_PROGRESS in March)
    delayed_act = Activity(
        id="act-civ-excavation",
        project_id="proj-delayed-1",
        wbs_id="wbs-del-1",
        activity_code="CIV-1002",
        name="Deep Trench Excavation and Shoring",
        status="IN_PROGRESS",
        planned_start=datetime(2024, 1, 1, 8, 0),
        planned_finish=datetime(2024, 1, 20, 17, 0),  # >60 days before event!
        actual_start=datetime(2024, 1, 5, 8, 0),
        original_duration=19.0,
        percent_complete=50.0,
    )

    # 4. Add another activity whose planned window matches March
    on_time_act = Activity(
        id="act-ele-conduit",
        project_id="proj-delayed-1",
        wbs_id="wbs-del-1",
        activity_code="ELE-2001",
        name="Underground Electrical Conduit Pulling",
        status="NOT_STARTED",
        planned_start=datetime(2024, 3, 20, 8, 0),
        planned_finish=datetime(2024, 3, 30, 17, 0),
        original_duration=10.0,
        percent_complete=0.0,
    )
    db_session.add_all([delayed_act, on_time_act])
    db_session.commit()

    # 5. Field event arrives in late March describing excavation work (no activity code cited)
    event = ExecutionEvent(
        id="ev-del-101",
        project_id="proj-delayed-1",
        execution_date=datetime(2024, 3, 25, 0, 0),
        description="Continued trench excavation and shoring work with excavator crew",
        verbatim_excerpt="Continued trench excavation and shoring work with excavator crew on north sector",
        reported_activity_code=None,
        status="UNMATCHED",
    )
    db_session.add(event)
    db_session.commit()

    # 6. Verify candidates include delayed_act despite being >60 days past planned_finish
    candidates = MatchingService.retrieve_candidates(db_session, event)
    candidate_codes = [c.activity_code for c in candidates]
    assert "CIV-1002" in candidate_codes
    assert "ELE-2001" in candidate_codes

    # 7. Evaluate event matching
    routing = MatchingService.evaluate_event(db_session, event)
    assert routing.route in ("AUTO_LINK", "PLANNER_REVIEW")
    assert routing.selected_candidate is not None
    assert routing.selected_candidate.activity_code == "CIV-1002"
    assert routing.selected_candidate.match_score >= 0.50
