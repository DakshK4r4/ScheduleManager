from __future__ import annotations

import uuid
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.models import Activity, ActivityRelationship, ActualProgressLedger, ExecutionEvent, Project
from app.services.cross_project_benchmarking_service import CrossProjectBenchmarkingService
from app.services.monte_carlo_service import MonteCarloSimulationService


@pytest.fixture
def monte_carlo_project(db_session: Session):
    proj_id = f"proj-mc-{uuid.uuid4().hex[:8]}"
    project = Project(
        id=proj_id,
        project_code="MC-PROJECT",
        name="Monte Carlo Simulation Project",
        planned_start=datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc),
        planned_finish=datetime(2026, 5, 30, 17, 0, tzinfo=timezone.utc),
        data_date=datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc),
    )
    db_session.add(project)

    a1 = Activity(
        id=f"act-mc-1-{uuid.uuid4().hex[:8]}",
        project_id=proj_id,
        activity_code="MC-101",
        name="Excavation",
        discipline="Civil",
        status="NOT_STARTED",
        planned_start=datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc),
        planned_finish=datetime(2026, 5, 10, 17, 0, tzinfo=timezone.utc),
        original_duration=8.0,
    )
    a2 = Activity(
        id=f"act-mc-2-{uuid.uuid4().hex[:8]}",
        project_id=proj_id,
        activity_code="MC-102",
        name="Concrete Pour",
        discipline="Civil",
        status="NOT_STARTED",
        planned_start=datetime(2026, 5, 11, 8, 0, tzinfo=timezone.utc),
        planned_finish=datetime(2026, 5, 20, 17, 0, tzinfo=timezone.utc),
        original_duration=8.0,
    )
    db_session.add_all([a1, a2])
    db_session.flush()

    rel = ActivityRelationship(
        id=f"rel-mc-{uuid.uuid4().hex[:8]}",
        project_id=proj_id,
        predecessor_id=a1.id,
        successor_id=a2.id,
        relationship_type="FS",
        lag=0.0,
    )
    db_session.add(rel)
    db_session.commit()
    return project


def test_monte_carlo_simulation_determinism_and_percentiles(db_session, monte_carlo_project):
    # Run with seed=42
    res1 = MonteCarloSimulationService.run_simulation(
        db=db_session,
        project_id=monte_carlo_project.id,
        iterations=50,
        seed=42,
    )
    assert res1["project_id"] == monte_carlo_project.id
    assert res1["iterations"] == 50
    assert "p50_finish" in res1
    assert "p80_finish" in res1
    assert "p90_finish" in res1
    assert "criticality_index" in res1
    assert res1["read_only_guarantee"] is True

    # P80 finish should be >= P50 finish
    assert res1["p80_finish"] >= res1["p50_finish"]
    assert res1["p90_finish"] >= res1["p80_finish"]

    # Run with identical seed=42 -> MUST be 100% deterministic
    res2 = MonteCarloSimulationService.run_simulation(
        db=db_session,
        project_id=monte_carlo_project.id,
        iterations=50,
        seed=42,
    )
    assert res1["p50_finish"] == res2["p50_finish"]
    assert res1["p80_finish"] == res2["p80_finish"]
    assert res1["criticality_index"] == res2["criticality_index"]


def test_monte_carlo_api(client: TestClient, monte_carlo_project):
    res = client.get(f"/projects/{monte_carlo_project.id}/risk/monte-carlo?iterations=30&seed=100")
    assert res.status_code == 200
    data = res.json()
    assert data["project_id"] == monte_carlo_project.id
    assert "p50_finish" in data
    assert "p80_finish" in data
    assert data["iterations"] == 30


def test_cross_project_benchmarks_unit_consistency(db_session):
    # Setup two distinct projects with verified ledger progress
    p1 = Project(id="proj-bm-1", project_code="P-BM1", name="Benchmark Project 1")
    p2 = Project(id="proj-bm-2", project_code="P-BM2", name="Benchmark Project 2")
    db_session.add_all([p1, p2])

    a1 = Activity(id="act-bm-1", project_id="proj-bm-1", activity_code="CIV-01", name="Piping 1", discipline="Piping")
    a2 = Activity(id="act-bm-2", project_id="proj-bm-2", activity_code="CIV-02", name="Piping 2", discipline="Piping")
    a3 = Activity(id="act-bm-3", project_id="proj-bm-1", activity_code="ELE-01", name="Cable 1", discipline="Electrical")
    db_session.add_all([a1, a2, a3])
    db_session.flush()

    e1 = ExecutionEvent(id="ev-1", project_id="proj-bm-1", description="Pipe work 1", verbatim_excerpt="pipes", execution_date=datetime(2026, 1, 1))
    e2 = ExecutionEvent(id="ev-2", project_id="proj-bm-2", description="Pipe work 2", verbatim_excerpt="pipes", execution_date=datetime(2026, 1, 2))
    e3 = ExecutionEvent(id="ev-3", project_id="proj-bm-2", description="Pipe work 3", verbatim_excerpt="pipes", execution_date=datetime(2026, 1, 3))
    db_session.add_all([e1, e2, e3])
    db_session.flush()

    # Three verified ledger entries for Piping in meters (m) across 2 projects
    l1 = ActualProgressLedger(
        id="leg-1",
        project_id="proj-bm-1",
        activity_id=a1.id,
        execution_event_id=e1.id,
        installed_quantity=100.0,
        unit_of_measure="m",
        reporting_date=datetime(2026, 1, 1),
        cumulative_percent=30.0,
    )
    l2 = ActualProgressLedger(
        id="leg-2",
        project_id="proj-bm-2",
        activity_id=a2.id,
        execution_event_id=e2.id,
        installed_quantity=120.0,
        unit_of_measure="m",
        reporting_date=datetime(2026, 1, 2),
        cumulative_percent=60.0,
    )
    l3 = ActualProgressLedger(
        id="leg-3",
        project_id="proj-bm-2",
        activity_id=a2.id,
        execution_event_id=e3.id,
        installed_quantity=110.0,
        unit_of_measure="m",
        reporting_date=datetime(2026, 1, 3),
        cumulative_percent=90.0,
    )
    db_session.add_all([l1, l2, l3])
    db_session.commit()

    benchmarks = CrossProjectBenchmarkingService.get_benchmarks(db_session, discipline="Piping")
    assert benchmarks["unit_consistency_enforced"] is True
    assert len(benchmarks["benchmarks"]) >= 1

    pipe_bm = next(b for b in benchmarks["benchmarks"] if b["discipline"] == "Piping")
    assert pipe_bm["unit"] == "m/day"
    assert pipe_bm["sample_size"] == 3
    assert pipe_bm["project_count"] == 2
    assert pipe_bm["confidence"] in ("MEDIUM", "HIGH")
    assert pipe_bm["p50_median_rate"] == 110.0
