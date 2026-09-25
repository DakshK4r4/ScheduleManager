from __future__ import annotations

import uuid
from datetime import datetime, timezone
import pytest

from app.domain.models import Activity, ActivityRelationship, Project
from app.services.scenario_simulation_service import ScenarioSimulationService


@pytest.fixture
def sim_project(db_session):
    proj_id = f"proj-sim-{uuid.uuid4().hex[:8]}"
    project = Project(
        id=proj_id,
        project_code="SIM-TEST",
        name="Simulation Test Project",
        planned_start=datetime(2026, 4, 1, 8, 0, tzinfo=timezone.utc),
        planned_finish=datetime(2026, 4, 30, 17, 0, tzinfo=timezone.utc),
        data_date=datetime(2026, 4, 1, 8, 0, tzinfo=timezone.utc),
    )
    db_session.add(project)

    # Activity 1: A10 (Critical path start, 5 days)
    a1 = Activity(
        id=f"act-a10-{uuid.uuid4().hex[:8]}",
        project_id=proj_id,
        activity_code="A10",
        name="Engineering Design",
        status="NOT_STARTED",
        planned_start=datetime(2026, 4, 1, 8, 0, tzinfo=timezone.utc),
        planned_finish=datetime(2026, 4, 7, 17, 0, tzinfo=timezone.utc),
        original_duration=5.0,
    )
    # Activity 2: A20 (Critical successor, 10 days)
    a2 = Activity(
        id=f"act-a20-{uuid.uuid4().hex[:8]}",
        project_id=proj_id,
        activity_code="A20",
        name="Procurement",
        status="NOT_STARTED",
        planned_start=datetime(2026, 4, 8, 8, 0, tzinfo=timezone.utc),
        planned_finish=datetime(2026, 4, 21, 17, 0, tzinfo=timezone.utc),
        original_duration=10.0,
    )
    # Activity 3: A30 (Non-critical parallel activity with 10 days float)
    a3 = Activity(
        id=f"act-a30-{uuid.uuid4().hex[:8]}",
        project_id=proj_id,
        activity_code="A30",
        name="Site Clearing",
        status="NOT_STARTED",
        planned_start=datetime(2026, 4, 1, 8, 0, tzinfo=timezone.utc),
        planned_finish=datetime(2026, 4, 5, 17, 0, tzinfo=timezone.utc),
        original_duration=3.0,
    )
    db_session.add_all([a1, a2, a3])
    db_session.flush()

    rel = ActivityRelationship(
        id=f"rel-a1-a2-{uuid.uuid4().hex[:8]}",
        project_id=proj_id,
        predecessor_id=a1.id,
        successor_id=a2.id,
        relationship_type="FS",
        lag=0.0,
    )
    db_session.add(rel)
    db_session.commit()

    return project


def test_scenario_simulation_delay_and_db_immutability(db_session, sim_project):
    # Snapshot database state before simulation
    acts_before = {
        a.activity_code: (a.original_duration, a.planned_start, a.planned_finish, a.status)
        for a in db_session.query(Activity).filter(Activity.project_id == sim_project.id).all()
    }

    # Simulate 5-day delay on critical activity A10
    result = ScenarioSimulationService.simulate_activity_delay(
        db=db_session,
        project=sim_project,
        activity_code="A10",
        delay_days=5.0,
        delay_category="Design",
        notes="Architect revision pending",
    )

    # 1. Verify simulation outputs
    assert result["project_id"] == sim_project.id
    assert result["activity_code"] == "A10"
    assert result["delay_days"] == 5.0
    assert result["impact_days"] > 0
    assert result["read_only_guarantee"] is True
    assert "affected_activities" in result
    assert result["affected_activities_count"] >= 1

    # 2. Verify official database is 100% untouched
    acts_after = {
        a.activity_code: (a.original_duration, a.planned_start, a.planned_finish, a.status)
        for a in db_session.query(Activity).filter(Activity.project_id == sim_project.id).all()
    }
    assert acts_before == acts_after, "Database MUST NOT mutate during what-if simulation!"


def test_multi_activity_scenario_simulation(db_session, sim_project):
    # Multi-activity change: delay A10 by 2 days, and delay A30 by 2 days
    changes = [
        {"activity_code": "A10", "delay_days": 2.0, "delay_category": "Material"},
        {"activity_code": "A30", "delay_days": 2.0, "delay_category": "Labour"},
    ]
    res = ScenarioSimulationService.simulate_scenario(
        db=db_session,
        project=sim_project,
        changes=changes,
    )
    assert res["project_id"] == sim_project.id
    assert len(res["applied_changes"]) == 2
    assert res["impact_days"] > 0
    assert "float_changes" in res


def test_simulate_scenario_api(client, sim_project):
    payload = {
        "changes": [
            {
                "activity_code": "A10",
                "delay_days": 3.0,
                "delay_category": "Weather",
                "notes": "Heavy rains delayed foundation",
            }
        ]
    }
    response = client.post(f"/projects/{sim_project.id}/simulate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == sim_project.id
    assert data["impact_days"] > 0
    assert data["read_only_guarantee"] is True


def test_delay_event_impact_distinction(db_session, sim_project):
    from app.domain.models import DelayEvent

    act1 = db_session.query(Activity).filter(Activity.project_id == sim_project.id, Activity.activity_code == "A10").first()

    event_id = f"delay-{uuid.uuid4().hex[:8]}"
    delay_event = DelayEvent(
        id=event_id,
        project_id=sim_project.id,
        activity_id=act1.id,
        category="Material",
        delay_days=5.0,
        confidence=0.9,
        evidence_text="Steel rebar shipment delayed by supplier",
        status="IDENTIFIED",
    )
    db_session.add(delay_event)
    db_session.commit()

    eval_res = ScenarioSimulationService.evaluate_delay_event_impact(
        db=db_session,
        delay_event_id=event_id,
        persist_simulated_impact=True,
    )

    assert eval_res["delay_event_id"] == event_id
    assert eval_res["category"] == "Material"
    assert eval_res["reported_physical_delay_days"] == 5.0
    assert eval_res["simulated_schedule_impact_days"] > 0
    assert "Reported physical delay of 5.0 days in Material" in eval_res["notes"]

    # Verify status on DelayEvent is updated to SIMULATED
    refreshed_event = db_session.query(DelayEvent).filter(DelayEvent.id == event_id).first()
    assert refreshed_event.status == "SIMULATED"
    assert refreshed_event.simulated_impact_days == eval_res["simulated_schedule_impact_days"]


