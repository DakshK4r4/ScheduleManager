from __future__ import annotations

import uuid
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from app.domain.models import Activity, ActivityRelationship, Project
from app.main import app
from app.services.schedule_health_service import ScheduleHealthService


@pytest.fixture
def health_test_project(db_session):
    proj_id = f"proj-{uuid.uuid4().hex[:8]}"
    project = Project(
        id=proj_id,
        project_code="HEALTH-TEST",
        name="Schedule Health Test Project",
        planned_start=datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc),
        planned_finish=datetime(2026, 3, 31, 17, 0, tzinfo=timezone.utc),
        data_date=datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc),
    )
    db_session.add(project)

    # Activity 1: A100 (Clean start)
    a1 = Activity(
        id=f"act-{uuid.uuid4().hex[:8]}",
        project_id=proj_id,
        activity_code="A100",
        name="Excavation",
        status="NOT_STARTED",
        planned_start=datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc),
        planned_finish=datetime(2026, 3, 5, 17, 0, tzinfo=timezone.utc),
        original_duration=5.0,
    )
    # Activity 2: A200 (Successor with FS)
    a2 = Activity(
        id=f"act-{uuid.uuid4().hex[:8]}",
        project_id=proj_id,
        activity_code="A200",
        name="Foundation Pouring",
        status="NOT_STARTED",
        planned_start=datetime(2026, 3, 6, 8, 0, tzinfo=timezone.utc),
        planned_finish=datetime(2026, 3, 12, 17, 0, tzinfo=timezone.utc),
        original_duration=5.0,
        constraint_type="MANDATORY_START",
        constraint_date=datetime(2026, 3, 6, 8, 0, tzinfo=timezone.utc),
    )
    # Activity 3: A300 (Isolated activity - missing logic)
    a3 = Activity(
        id=f"act-{uuid.uuid4().hex[:8]}",
        project_id=proj_id,
        activity_code="A300",
        name="Floating Task",
        status="NOT_STARTED",
        planned_start=datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc),
        planned_finish=datetime(2026, 3, 2, 17, 0, tzinfo=timezone.utc),
        original_duration=2.0,
    )
    db_session.add_all([a1, a2, a3])
    db_session.flush()

    # Relationship A100 -> A200 with lead (-1 day)
    rel = ActivityRelationship(
        id=f"rel-{uuid.uuid4().hex[:8]}",
        project_id=proj_id,
        predecessor_id=a1.id,
        successor_id=a2.id,
        relationship_type="FS",
        lag=-1.0,
    )
    db_session.add(rel)
    db_session.commit()

    return project


def test_schedule_health_service(db_session, health_test_project):
    health = ScheduleHealthService.evaluate_project_health(db_session, health_test_project.id)
    assert health["project_id"] == health_test_project.id
    assert health["total_activities"] == 3
    assert health["total_relationships"] == 1
    assert "health_score" in health
    assert "grade" in health
    assert "metrics" in health

    metrics = health["metrics"]
    # Missing logic: A300 is isolated, A100 has no predecessor, A200 has no successor
    assert metrics["missing_logic"]["total_missing_logic"] >= 1
    # Leads: 1 lead (-1.0 lag)
    assert metrics["leads"]["count"] == 1
    assert metrics["leads"]["status"] == "FAIL"
    # Hard constraints: A200 has MANDATORY_START
    assert metrics["hard_constraints"]["count"] == 1


def test_schedule_health_api(client, health_test_project):
    response = client.get(f"/projects/{health_test_project.id}/schedule-health")
    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == health_test_project.id
    assert "metrics" in data
    assert "recommendations" in data
    assert data["metrics"]["leads"]["count"] == 1


def test_schedule_health_not_found(client):
    response = client.get("/projects/non-existent-proj/schedule-health")
    assert response.status_code == 404
