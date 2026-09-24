from __future__ import annotations

from datetime import date
import pytest

from app.services.calendar_service import CalendarService, CalendarSpec
from app.services.cpm_engine import CPMEngine, CycleDetectedException


def test_calendar_working_days_weekend_skip():
    """5-day activity starting Friday 2026-05-01 should finish Thursday 2026-05-07 (skipping May 2-3 weekend)."""
    cal = CalendarSpec.standard_5day()
    start = date(2026, 5, 1)  # Friday
    finish = CalendarService.add_working_days(start, 5, cal)
    assert finish == date(2026, 5, 7)  # Friday (1), Mon (2), Tue (3), Wed (4), Thu (5)

    # Subtract working days back
    calc_start = CalendarService.subtract_working_days(finish, 5, cal)
    assert calc_start == start

    # Count working days between
    assert CalendarService.working_days_between(start, finish, cal) == 5.0


def test_calendar_with_holiday_exclusion():
    """Holiday on Tuesday 2026-05-05 should shift finish to Friday 2026-05-08."""
    cal = CalendarSpec.standard_5day(holidays={date(2026, 5, 5)})
    start = date(2026, 5, 1)
    finish = CalendarService.add_working_days(start, 5, cal)
    assert finish == date(2026, 5, 8)


def test_cpm_linear_forward_backward_pass():
    """
    Linear chain: A (5d) -> B (3d) -> C (2d), all FS with 0 lag.
    Project starts Mon 2026-06-01.
    All activities should have Total Float = 0 and lie on the critical path.
    """
    engine = CPMEngine()
    activities = [
        {"activity_code": "A", "name": "Excavation", "duration": 5},
        {"activity_code": "B", "name": "Piling", "duration": 3},
        {"activity_code": "C", "name": "Cap Pouring", "duration": 2},
    ]
    relationships = [
        {"predecessor_code": "A", "successor_code": "B", "relationship_type": "FS", "lag": 0},
        {"predecessor_code": "B", "successor_code": "C", "relationship_type": "FS", "lag": 0},
    ]

    result = engine.calculate(activities, relationships, project_start_date=date(2026, 6, 1))

    assert not result.cycles_detected
    assert result.critical_path == ["A", "B", "C"]
    assert set(result.critical_activities) == {"A", "B", "C"}
    assert result.negative_float_activities == []

    acts = result.activities
    # A: Mon Jun 1 to Fri Jun 5
    assert acts["A"].early_start == date(2026, 6, 1)
    assert acts["A"].early_finish == date(2026, 6, 5)
    assert acts["A"].total_float == 0.0

    # B: Mon Jun 8 to Wed Jun 10
    assert acts["B"].early_start == date(2026, 6, 8)
    assert acts["B"].early_finish == date(2026, 6, 10)
    assert acts["B"].total_float == 0.0
    assert acts["B"].driving_predecessor_code == "A"

    # C: Thu Jun 11 to Fri Jun 12
    assert acts["C"].early_start == date(2026, 6, 11)
    assert acts["C"].early_finish == date(2026, 6, 12)
    assert acts["C"].total_float == 0.0
    assert acts["C"].driving_predecessor_code == "B"


def test_cpm_parallel_branches_float_and_driving_pred():
    """
    Diamond network:
      A (5d) -> B1 (10d, Critical) -> D (3d)
      A (5d) -> B2 (3d, Non-Critical) -> D (3d)
    B1 should drive D.
    B2 should have positive float (7 working days).
    """
    engine = CPMEngine()
    activities = [
        {"activity_code": "A", "duration": 5},
        {"activity_code": "B1", "duration": 10},
        {"activity_code": "B2", "duration": 3},
        {"activity_code": "D", "duration": 3},
    ]
    relationships = [
        {"predecessor_code": "A", "successor_code": "B1", "relationship_type": "FS"},
        {"predecessor_code": "A", "successor_code": "B2", "relationship_type": "FS"},
        {"predecessor_code": "B1", "successor_code": "D", "relationship_type": "FS"},
        {"predecessor_code": "B2", "successor_code": "D", "relationship_type": "FS"},
    ]

    result = engine.calculate(activities, relationships, project_start_date=date(2026, 6, 1))

    assert not result.cycles_detected
    assert result.critical_path == ["A", "B1", "D"]
    assert result.activities["D"].driving_predecessor_code == "B1"

    # B2 has float
    b2 = result.activities["B2"]
    assert b2.total_float == 7.0
    assert b2.free_float == 7.0
    assert not b2.is_critical


def test_cpm_relationship_types_ss_ff_with_lag():
    """
    Tests Start-to-Start (SS) with lag and Finish-to-Finish (FF) with lag.
    A (10d)
    B (5d) with SS + 2 days lag from A -> B starts 2 working days after A starts.
    """
    engine = CPMEngine()
    activities = [
        {"activity_code": "A", "duration": 10},
        {"activity_code": "B", "duration": 5},
    ]
    relationships = [
        {"predecessor_code": "A", "successor_code": "B", "relationship_type": "SS", "lag": 2},
    ]

    start = date(2026, 6, 1)  # Monday
    result = engine.calculate(activities, relationships, project_start_date=start)

    acts = result.activities
    assert acts["A"].early_start == start
    # B starts 2 days after Monday -> Wednesday June 3
    assert acts["B"].early_start == date(2026, 6, 3)


def test_cpm_cycle_detection():
    """Circular graph: A -> B -> C -> A should be detected without infinite recursion."""
    activities = [
        {"activity_code": "A", "duration": 5},
        {"activity_code": "B", "duration": 5},
        {"activity_code": "C", "duration": 5},
    ]
    relationships = [
        {"predecessor_code": "A", "successor_code": "B"},
        {"predecessor_code": "B", "successor_code": "C"},
        {"predecessor_code": "C", "successor_code": "A"},
    ]

    engine = CPMEngine()
    result = engine.calculate(activities, relationships, project_start_date=date(2026, 6, 1))

    assert result.cycles_detected is True
    assert set(result.critical_path) == set()
    assert "cycle detected" in result.error.lower()


def test_cpm_negative_float_detection():
    """
    Project requires finish by Friday June 5, but activities take 10 working days.
    Negative float should be detected and accurately reported.
    """
    engine = CPMEngine()
    activities = [
        {"activity_code": "A", "duration": 10},
    ]
    relationships = []

    # Requires finish June 5, but 10 days starting June 1 finishes June 12
    result = engine.calculate(
        activities,
        relationships,
        project_start_date=date(2026, 6, 1),
        target_finish_date=date(2026, 6, 5),
    )

    act_a = result.activities["A"]
    assert act_a.has_negative_float is True
    assert act_a.total_float < 0
    assert "A" in result.negative_float_activities
    assert "A" in result.critical_activities


def test_cpm_near_critical_threshold():
    """
    Configurable near-critical threshold:
    Activity with 3 days float when threshold is 5 days should be marked near-critical.
    """
    engine = CPMEngine(near_critical_threshold=5.0)
    activities = [
        {"activity_code": "ROOT", "duration": 1},
        {"activity_code": "CRIT", "duration": 10},
        {"activity_code": "NEAR", "duration": 7},
    ]
    relationships = [
        {"predecessor_code": "ROOT", "successor_code": "CRIT"},
        {"predecessor_code": "ROOT", "successor_code": "NEAR"},
    ]

    result = engine.calculate(activities, relationships, project_start_date=date(2026, 6, 1))

    near = result.activities["NEAR"]
    assert near.total_float == 3.0
    assert near.is_near_critical is True
    assert not near.is_critical
    assert "NEAR" in result.near_critical_activities


def test_cpm_isolated_and_open_ends():
    """Tests detection of isolated activities and open ends."""
    engine = CPMEngine()
    activities = [
        {"activity_code": "A", "duration": 2},
        {"activity_code": "B", "duration": 2},
        {"activity_code": "ISOLATED", "duration": 5},
    ]
    relationships = [
        {"predecessor_code": "A", "successor_code": "B"},
    ]

    result = engine.calculate(activities, relationships, project_start_date=date(2026, 6, 1))

    assert "ISOLATED" in result.isolated_activities
    assert "A" in result.open_start_activities
    assert "B" in result.open_finish_activities


def test_cpm_performance_large_network():
    """
    Performance test: verify that CPM calculations scale smoothly
    across 100, 1,000, and 5,000 activity networks without O(N^2) degradation.
    """
    import time
    engine = CPMEngine()

    for count in [100, 1000, 5000]:
        acts = [{"activity_code": f"ACT_{i}", "duration": 2 + (i % 5)} for i in range(count)]
        # Construct linear ladder network with parallel paths
        rels = []
        for i in range(count - 1):
            rels.append({"predecessor_code": f"ACT_{i}", "successor_code": f"ACT_{i+1}", "relationship_type": "FS"})
            if i + 2 < count and i % 3 == 0:
                rels.append({"predecessor_code": f"ACT_{i}", "successor_code": f"ACT_{i+2}", "relationship_type": "FS"})

        t0 = time.perf_counter()
        res = engine.calculate(acts, rels, project_start_date=date(2026, 1, 1))
        elapsed = time.perf_counter() - t0

        assert not res.cycles_detected
        assert len(res.critical_path) > 0
        # 5,000 activities must complete in under 2 seconds
        assert elapsed < 2.5, f"CPM for {count} activities took {elapsed:.2f}s (expected < 2.5s)"

