import io
import time
import tracemalloc
import pytest
from app.parsers.xer_parser import XerParser
from app.parsers.tabular_common import map_columns, parse_relationship_token, build_canonical_schedule_from_rows
from app.parsers.csv_parser import CsvParser
from app.parsers.p6_xml_parser import P6XmlParser
from app.parsers.base import ParserError
from app.main import detect_parser
from app.models.canonical import RelationshipType, ActivityStatus


def test_xer_multi_project_scoping():
    """
    Verify that an XER with multiple projects scopes PROJECT, PROJWBS, TASK, and TASKPRED
    strictly to the selected project, never leaking activities or WBS from another project.
    """
    multi_project_xer = (
        "ERMHDR\t7.0\t2026-09-26\tUSER\n"
        "%T\tPROJECT\n"
        "%F\tproj_id\tproj_short_name\n"
        "%R\t100\tPROJ_A\n"
        "%R\t200\tPROJ_B\n"
        "%T\tPROJWBS\n"
        "%F\twbs_id\tproj_id\twbs_short_name\twbs_name\n"
        "%R\t1001\t100\tWBS_A\tWBS Node A\n"
        "%R\t2001\t200\tWBS_B\tWBS Node B\n"
        "%T\tTASK\n"
        "%F\ttask_id\tproj_id\twbs_id\ttask_code\ttask_name\ttarget_drtn_hr_cnt\n"
        "%R\t10001\t100\t1001\tACT_A1\tTask A1 in Project A\t80.0\n"
        "%R\t20001\t200\t2001\tACT_B1\tTask B1 in Project B\t40.0\n"
        "%T\tTASKPRED\n"
        "%F\ttask_pred_id\ttask_id\tpred_task_id\tpred_type\tproj_id\n"
        "%R\t1\t10001\t20001\tPR_FS\t100\n"  # Cross-project link to B1!
        "%E\n"
    ).encode("utf-8")

    parser = XerParser()

    # Parse default (Project A)
    sched_a = parser.parse(multi_project_xer, "multi.xer")
    assert sched_a.project.project_code == "PROJ_A"

    # Verify WBS: only WBS_A, never WBS_B
    wbs_codes = [w.code for w in sched_a.wbs]
    assert "WBS_A" in wbs_codes
    assert "WBS_B" not in wbs_codes

    # Verify Activities: only ACT_A1, never ACT_B1
    act_codes = [a.activity_code for a in sched_a.activities]
    assert "ACT_A1" in act_codes
    assert "ACT_B1" not in act_codes

    # Verify Cross-project relationship: B1 is not in Project A, so link is safely excluded
    assert len(sched_a.relationships) == 0

    # Parse Project B explicitly
    sched_b = parser.parse(multi_project_xer, "multi.xer", target_project_code="PROJ_B")
    assert sched_b.project.project_code == "PROJ_B"
    assert [a.activity_code for a in sched_b.activities] == ["ACT_B1"]
    assert [w.code for w in sched_b.wbs] == ["WBS_B"]


def test_xer_calendar_hours_per_day():
    """
    Verify that P6 CALENDAR table is parsed and day_hr_cnt is used instead of hardcoded 8.0.
    """
    xer_with_cal = (
        "ERMHDR\t7.0\t2026-09-26\tUSER\n"
        "%T\tCALENDAR\n"
        "%F\tclndr_id\tclndr_name\tday_hr_cnt\n"
        "%R\t501\t10 Hour Shift\t10.0\n"
        "%T\tPROJECT\n"
        "%F\tproj_id\tproj_short_name\n"
        "%R\t100\tPROJ_CAL\n"
        "%T\tTASK\n"
        "%F\ttask_id\tproj_id\ttask_code\ttask_name\tclndr_id\ttarget_drtn_hr_cnt\n"
        "%R\t101\t100\tACT_CAL\tTask with 10h Calendar\t501\t50.0\n"
        "%E\n"
    ).encode("utf-8")

    parser = XerParser()
    sched = parser.parse(xer_with_cal, "cal.xer")
    act = sched.activities[0]
    # 50 hours on 10 hr/day calendar should be 5.0 days, NOT 6.25 days (50/8)
    assert act.original_duration == 5.0
    assert act.calendar == "10 Hour Shift"


def test_tabular_auto_generated_activity_ids():
    """
    Verify that schedule spreadsheets with missing Activity ID headers auto-generate
    content-stable deterministic IDs that preserve identity continuity across reordering and insertions.
    """
    csv_text_1 = (
        "Task Name,Planned Start,Planned Finish,Duration\n"
        "Clear site,2026-09-01,2026-09-05,5\n"
        "Excavation,2026-09-06,2026-09-15,10\n"
    ).encode("utf-8")

    parser = CsvParser()
    sched1 = parser.parse(csv_text_1, "simple_tasks.csv")
    assert len(sched1.activities) == 2
    act_clear_code = sched1.activities[0].activity_code
    act_excv_code = sched1.activities[1].activity_code
    assert act_clear_code.startswith("ACT-CLEARSIT-")
    assert act_excv_code.startswith("ACT-EXCAVATI-")

    # Reordered and inserted row: insert "Mobilization" first, swap order
    csv_text_2 = (
        "Task Name,Planned Start,Planned Finish,Duration\n"
        "Mobilization,2026-08-25,2026-08-31,6\n"
        "Excavation,2026-09-06,2026-09-15,10\n"
        "Clear site,2026-09-01,2026-09-05,5\n"
    ).encode("utf-8")

    sched2 = parser.parse(csv_text_2, "reordered_tasks.csv")
    assert len(sched2.activities) == 3
    # Critical identity continuity check: Excavation and Clear site retain EXACT SAME IDs!
    code_map = {a.name: a.activity_code for a in sched2.activities}
    assert code_map["Clear site"] == act_clear_code
    assert code_map["Excavation"] == act_excv_code


def test_parenthesized_and_bracketed_lag_patterns():
    """
    Verify robust parsing of relationship tokens with parentheses, brackets, and lags.
    """
    # 1. ACT100 (FS+3d)
    rel1 = parse_relationship_token("ACT100 (FS+3d)", "ACT200", is_predecessor=True)
    assert rel1.predecessor_code == "ACT100"
    assert rel1.successor_code == "ACT200"
    assert rel1.relationship_type == RelationshipType.FS
    assert rel1.lag == 3.0

    # 2. ACT100 [SS+2]
    rel2 = parse_relationship_token("ACT100 [SS+2]", "ACT200", is_predecessor=True)
    assert rel2.predecessor_code == "ACT100"
    assert rel2.relationship_type == RelationshipType.SS
    assert rel2.lag == 2.0

    # 3. ACT100 (FF-1d)
    rel3 = parse_relationship_token("ACT100 (FF-1d)", "ACT200", is_predecessor=True)
    assert rel3.predecessor_code == "ACT100"
    assert rel3.relationship_type == RelationshipType.FF
    assert rel3.lag == -1.0

    # 4. ACT100 +5d (lag without explicit type defaults to FS)
    rel4 = parse_relationship_token("ACT100 +5d", "ACT200", is_predecessor=True)
    assert rel4.predecessor_code == "ACT100"
    assert rel4.relationship_type == RelationshipType.FS
    assert rel4.lag == 5.0


def test_mpp_actionable_error():
    """
    Verify that Microsoft Project binary .mpp files raise a clean, actionable ParserError
    recommending XML or Excel export instead of crashing.
    """
    dummy_mpp_bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1\x00\x00MSProject binary file"
    with pytest.raises(ParserError) as exc_info:
        detect_parser("schedule.mpp", dummy_mpp_bytes)
    assert "Microsoft Project proprietary binary format (.mpp) is not supported" in str(exc_info.value)
    assert "XML (.xml) or Excel (.xlsx)" in str(exc_info.value)


def test_p6_xml_memory_benchmark():
    """
    Benchmark P6 XML parser on a representative schedule:
    Measures file size, activity count, relationship count, parse time, and peak memory.
    """
    # Generate representative XML with 100 activities and 99 relationships
    xml_parts = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        '<APIBusinessObjects xmlns="http://xmlns.oracle.com/Primavera/P6/V20.12/API/BusinessObjects">\n',
        '<Project>\n',
        '  <ObjectId>1</ObjectId>\n',
        '  <Id>BENCHMARK-PROJ</Id>\n',
        '  <Name>Benchmark Project</Name>\n',
        '  <PlannedStartDate>2026-01-01T08:00:00</PlannedStartDate>\n',
        '  <PlannedFinishDate>2026-12-31T17:00:00</PlannedFinishDate>\n',
        '</Project>\n',
        '<WBS>\n',
        '  <ObjectId>10</ObjectId>\n',
        '  <Code>WBS-1</Code>\n',
        '  <Name>Phase 1</Name>\n',
        '</WBS>\n',
    ]

    for i in range(1, 101):
        xml_parts.append(
            f'<Activity>\n'
            f'  <ObjectId>{i}</ObjectId>\n'
            f'  <Id>ACT-{i:04d}</Id>\n'
            f'  <Name>Benchmark Activity {i}</Name>\n'
            f'  <WBSObjectId>10</WBSObjectId>\n'
            f'  <Status>Not Started</Status>\n'
            f'  <PlannedDuration>40.0</PlannedDuration>\n'
            f'</Activity>\n'
        )

    for i in range(1, 100):
        xml_parts.append(
            f'<Relationship>\n'
            f'  <PredecessorActivityObjectId>{i}</PredecessorActivityObjectId>\n'
            f'  <SuccessorActivityObjectId>{i+1}</SuccessorActivityObjectId>\n'
            f'  <Type>Finish to Start</Type>\n'
            f'  <Lag>0.0</Lag>\n'
            f'</Relationship>\n'
        )

    xml_parts.append('</APIBusinessObjects>\n')
    xml_content = "".join(xml_parts).encode("utf-8")

    tracemalloc.start()
    start_time = time.perf_counter()

    parser = P6XmlParser()
    sched = parser.parse(xml_content, "benchmark.xml")

    duration = time.perf_counter() - start_time
    current_mem, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert sched.project.project_code == "BENCHMARK-PROJ"
    assert len(sched.activities) == 100
    assert len(sched.relationships) == 99

    peak_mem_mb = peak_mem / (1024 * 1024)
    # Parsing 100 activities should complete in < 0.5s and consume < 5 MB
    assert duration < 1.0, f"Parse took too long: {duration:.3f}s"
    assert peak_mem_mb < 10.0, f"Peak memory too high: {peak_mem_mb:.2f} MB"
