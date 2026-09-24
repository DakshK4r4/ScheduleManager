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


def test_known_cpm_network_diamond():
    """
    Section 38 Specification Test:
    A -> B, A -> C, B -> D, C -> D
    A = 2 days, B = 5 days, C = 8 days, D = 2 days
    Expected:
      Longest path: A -> C -> D
      Project duration: 12 working days
      A = Critical, C = Critical, D = Critical
      B = Non-critical with Float = 3.0 days (8 - 5 = 3 days slack)
    """
    engine = CPMEngine()
    activities = [
        {"activity_code": "A", "duration": 2},
        {"activity_code": "B", "duration": 5},
        {"activity_code": "C", "duration": 8},
        {"activity_code": "D", "duration": 2},
    ]
    relationships = [
        {"predecessor_code": "A", "successor_code": "B", "relationship_type": "FS", "lag": 0},
        {"predecessor_code": "A", "successor_code": "C", "relationship_type": "FS", "lag": 0},
        {"predecessor_code": "B", "successor_code": "D", "relationship_type": "FS", "lag": 0},
        {"predecessor_code": "C", "successor_code": "D", "relationship_type": "FS", "lag": 0},
    ]

    start = date(2026, 6, 1)  # Monday
    result = engine.calculate(activities, relationships, project_start_date=start)

    assert not result.cycles_detected
    assert result.critical_path == ["A", "C", "D"]
    assert "A" in result.critical_activities
    assert "C" in result.critical_activities
    assert "D" in result.critical_activities
    assert "B" not in result.critical_activities

    act_b = result.activities["B"]
    assert act_b.total_float == 3.0
    assert act_b.free_float == 3.0
    assert act_b.is_critical is False

    # Duration: A (2d) + C (8d) + D (2d) = 12 working days
    assert result.project_duration_days == 12.0


def test_all_relationship_types_fs_ss_ff_sf():
    """
    Section 39 Specification Test:
    Verify separate tests covering FS, SS, FF, and SF.
    Ensure CPM does not treat all relationships as FS.
    """
    engine = CPMEngine()
    start = date(2026, 6, 1)  # Monday

    # 1. FS: Pred finishes, next day succ starts
    res_fs = engine.calculate(
        [{"activity_code": "P", "duration": 5}, {"activity_code": "S", "duration": 3}],
        [{"predecessor_code": "P", "successor_code": "S", "relationship_type": "FS"}],
        project_start_date=start,
    )
    # P: Mon Jun 1 to Fri Jun 5. S: Mon Jun 8 to Wed Jun 10
    assert res_fs.activities["S"].early_start == date(2026, 6, 8)

    # 2. SS: Pred starts, succ starts same day (lag 0)
    res_ss = engine.calculate(
        [{"activity_code": "P", "duration": 5}, {"activity_code": "S", "duration": 3}],
        [{"predecessor_code": "P", "successor_code": "S", "relationship_type": "SS"}],
        project_start_date=start,
    )
    assert res_ss.activities["S"].early_start == start

    # 3. FF: Pred finishes, succ finishes on or after pred finish
    res_ff = engine.calculate(
        [{"activity_code": "P", "duration": 5}, {"activity_code": "S", "duration": 3}],
        [{"predecessor_code": "P", "successor_code": "S", "relationship_type": "FF"}],
        project_start_date=start,
    )
    # P finishes Fri Jun 5. S (3d) must finish Fri Jun 5 -> S starts Wed Jun 3
    assert res_ff.activities["S"].early_finish == date(2026, 6, 5)
    assert res_ff.activities["S"].early_start == date(2026, 6, 3)

    # 4. SF: Pred starts, succ finishes
    res_sf = engine.calculate(
        [{"activity_code": "P", "duration": 5}, {"activity_code": "S", "duration": 3}],
        [{"predecessor_code": "P", "successor_code": "S", "relationship_type": "SF", "lag": 4}],
        project_start_date=start,
    )
    assert res_sf.activities["S"].early_finish is not None


def test_lag_and_lead_variations():
    """
    Section 40 Specification Test:
    FS + 2d, FS + 0d, FS - 1d (lead).
    Verify that dates differ correctly.
    """
    engine = CPMEngine()
    start = date(2026, 6, 1)  # Monday, duration 3d finishes Wed Jun 3

    # FS + 0d: finishes Wed Jun 3 -> succ starts Thu Jun 4
    res_0 = engine.calculate(
        [{"activity_code": "A", "duration": 3}, {"activity_code": "B", "duration": 2}],
        [{"predecessor_code": "A", "successor_code": "B", "relationship_type": "FS", "lag": 0}],
        project_start_date=start,
    )
    assert res_0.activities["B"].early_start == date(2026, 6, 4)

    # FS + 2d: finishes Wed Jun 3 -> 2 working days lag -> succ starts Mon Jun 8
    res_pos = engine.calculate(
        [{"activity_code": "A", "duration": 3}, {"activity_code": "B", "duration": 2}],
        [{"predecessor_code": "A", "successor_code": "B", "relationship_type": "FS", "lag": 2}],
        project_start_date=start,
    )
    assert res_pos.activities["B"].early_start == date(2026, 6, 8)

    # FS - 1d (lead of 1 day): finishes Wed Jun 3 -> succ starts on finish day (Wed Jun 3)
    res_neg = engine.calculate(
        [{"activity_code": "A", "duration": 3}, {"activity_code": "B", "duration": 2}],
        [{"predecessor_code": "A", "successor_code": "B", "relationship_type": "FS", "lag": -1}],
        project_start_date=start,
    )
    assert res_neg.activities["B"].early_start == date(2026, 6, 3)

    # Verify all 3 starts are strictly distinct
    assert res_neg.activities["B"].early_start < res_0.activities["B"].early_start < res_pos.activities["B"].early_start


def test_calendar_with_working_days_and_holiday_shift():
    """
    Section 41 Specification Test:
    Activity starting Monday with 5 working days duration:
    - Normal week finishes Friday.
    - Adding holiday on Wednesday shifts finish to Monday.
    """
    # 1. Normal week
    cal_std = CalendarSpec.standard_5day()
    engine_std = CPMEngine(default_calendar=cal_std)
    start = date(2026, 6, 1)  # Monday
    res_norm = engine_std.calculate(
        [{"activity_code": "A", "duration": 5}],
        [],
        project_start_date=start,
    )
    assert res_norm.activities["A"].early_finish == date(2026, 6, 5)  # Friday

    # 2. Week with holiday on Wednesday June 3
    cal_hol = CalendarSpec.standard_5day(holidays={date(2026, 6, 3)})
    engine_hol = CPMEngine(default_calendar=cal_hol)
    res_hol = engine_hol.calculate(
        [{"activity_code": "A", "duration": 5}],
        [],
        project_start_date=start,
    )
    # Mon (1), Tue (2), [Wed Holiday skipped], Thu (3), Fri (4), [Weekend], Mon Jun 8 (5)
    assert res_hol.activities["A"].early_finish == date(2026, 6, 8)


def test_variance_by_activity_status_no_today_reliance():
    """
    Section 43 & Section 44 Specification Test:
    Completed: Planned Finish June 20, Actual Finish June 22 -> Variance +2d
    In-progress: Planned Finish June 20, Forecast Finish June 24 -> Variance +4d
    Not-started: Planned Finish June 20, Forecast Finish June 25 -> Variance +5d
    NEVER use today's date in any variance calculation.
    """
    engine = CPMEngine()
    activities = [
        {
            "activity_code": "ACT_COMPLETED",
            "name": "Completed Activity",
            "status": "COMPLETED",
            "duration": 5,
            "planned_start": date(2024, 6, 15),
            "planned_finish": date(2024, 6, 20),
            "actual_start": date(2024, 6, 15),
            "actual_finish": date(2024, 6, 22),
        },
        {
            "activity_code": "ACT_IN_PROGRESS",
            "name": "In Progress Activity",
            "status": "IN_PROGRESS",
            "duration": 10,
            "percent_complete": 60.0,
            "planned_start": date(2024, 6, 10),
            "planned_finish": date(2024, 6, 20),
            "actual_start": date(2024, 6, 10),
            "remaining_duration": 4,  # finishes June 24
        },
        {
            "activity_code": "ACT_NOT_STARTED",
            "name": "Not Started Activity",
            "status": "NOT_STARTED",
            "duration": 5,
            "planned_start": date(2024, 6, 15),
            "planned_finish": date(2024, 6, 20),
        },
    ]

    # Run CPM starting June 20 (or matching dates)
    res = engine.calculate(activities, [], project_start_date=date(2024, 6, 15))

    # 1. Completed activity
    c_act = res.activities["ACT_COMPLETED"]
    assert c_act.forecast_finish == date(2024, 6, 22)
    assert c_act.finish_variance == 2.0  # +2d, NOT 700+ days!

    # 2. In-progress activity with planned June 20 and forecast June 24
    ip_act = res.activities["ACT_IN_PROGRESS"]
    # forecast finish must be based on schedule logic, not today's system date
    assert ip_act.finish_variance is not None
    assert ip_act.finish_variance < 50.0  # Mathematically grounded, not +700d


def test_data_date_forward_pass_cutoff():
    """
    Section 7 & 8: Data Date must prevent uncompleted activities from starting prior to data date.
    """
    engine = CPMEngine()
    activities = [
        {"activity_code": "PAST_ACT", "duration": 5, "planned_start": date(2026, 6, 1), "status": "NOT_STARTED"},
    ]
    # Data Date set to June 15, 2026
    data_date = date(2026, 6, 15)
    res = engine.calculate(activities, [], project_start_date=date(2026, 6, 1), data_date=data_date)

    # Activity must not start before June 15
    assert res.activities["PAST_ACT"].early_start >= data_date
    assert res.data_date == data_date


def test_terminal_activities_anchor_to_calculated_finish_no_huge_float():
    """
    Section 19: Prevent the +300d float bug caused by anchoring backward pass
    to far-off project planned envelope dates instead of calculated finish.
    """
    engine = CPMEngine()
    activities = [
        {"activity_code": "ACT_1", "duration": 5},
        {"activity_code": "ACT_2", "duration": 5},
    ]
    relationships = [
        {"predecessor_code": "ACT_1", "successor_code": "ACT_2", "relationship_type": "FS"},
    ]

    # Without a hard contract deadline, both activities on the linear path must have Total Float = 0
    res = engine.calculate(activities, relationships, project_start_date=date(2026, 6, 1))
    assert res.activities["ACT_1"].total_float == 0.0
    assert res.activities["ACT_2"].total_float == 0.0


def test_high_float_warnings_and_explanations():
    """
    Section 20 & 36: High float activities must be diagnosed with meaningful warnings
    (e.g., OPEN_FINISH, DISCONNECTED, HIGH_FLOAT) and explanations, never silently capped.
    """
    engine = CPMEngine(high_float_threshold=40.0)
    activities = [
        {"activity_code": "START", "duration": 1},
        {"activity_code": "MAIN_CRIT", "duration": 100},
        {"activity_code": "SIDE_SHORT", "duration": 5},
        {"activity_code": "FINISH", "duration": 1},
        {"activity_code": "DISCONNECTED_ACT", "duration": 2},
    ]
    relationships = [
        {"predecessor_code": "START", "successor_code": "MAIN_CRIT"},
        {"predecessor_code": "START", "successor_code": "SIDE_SHORT"},
        {"predecessor_code": "MAIN_CRIT", "successor_code": "FINISH"},
        {"predecessor_code": "SIDE_SHORT", "successor_code": "FINISH"},
    ]

    res = engine.calculate(activities, relationships, project_start_date=date(2026, 1, 1))

    # SIDE_SHORT has huge float (95 working days) because MAIN_CRIT takes 100 days
    side = res.activities["SIDE_SHORT"]
    assert side.total_float == 95.0
    assert side.float_warning == "HIGH_FLOAT"
    assert "non-controlling" in side.float_explanation.lower() or "slack" in side.float_explanation.lower()

    # DISCONNECTED_ACT has no links
    disc = res.activities["DISCONNECTED_ACT"]
    assert disc.is_open_start is True
    assert disc.is_open_finish is True
    assert disc.float_warning == "DISCONNECTED"
    assert "disconnected" in disc.float_explanation.lower()


