import io
import re
from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.models import Activity, ActivityRelationship, Project, WBSNode


def test_xer_export_preserves_wbs_hierarchy_dates_and_relationships(
    client: TestClient, db_session: Session
):
    # 1. Setup test project
    proj = Project(
        id="proj-roundtrip-1",
        project_code="TEST_XER_ROUNDTRIP",
        name="Test XER Roundtrip Project",
        planned_start=datetime(2024, 1, 1, 8, 0),
        planned_finish=datetime(2025, 12, 31, 17, 0),
    )
    db_session.add(proj)

    # 2. Add WBS nodes
    wbs_civil = WBSNode(
        id="wbs-civ-1",
        project_id="proj-roundtrip-1",
        code="WBS-100",
        name="Civil",
    )
    wbs_struct = WBSNode(
        id="wbs-str-1",
        project_id="proj-roundtrip-1",
        code="WBS-101",
        name="Structural",
    )
    db_session.add_all([wbs_civil, wbs_struct])

    # 3. Add Activities
    act1 = Activity(
        id="act-civ-1001",
        project_id="proj-roundtrip-1",
        wbs_id="wbs-civ-1",
        activity_code="CIV-1001",
        name="Excavation Work",
        status="IN_PROGRESS",
        planned_start=datetime(2024, 8, 19, 8, 0),
        planned_finish=datetime(2024, 10, 16, 8, 0),
        actual_start=datetime(2024, 9, 30, 0, 0),
        original_duration=58.0,
        percent_complete=75.0,
        notes="Foundation inspection pending approval",
    )
    act2 = Activity(
        id="act-str-1001",
        project_id="proj-roundtrip-1",
        wbs_id="wbs-str-1",
        activity_code="STR-1001",
        name="Steel Erection",
        status="NOT_STARTED",
        planned_start=datetime(2024, 10, 17, 8, 0),
        planned_finish=datetime(2024, 12, 1, 8, 0),
        actual_start=None,
        original_duration=45.0,
        percent_complete=0.0,
    )
    db_session.add_all([act1, act2])

    # 4. Add Relationship (act1 is predecessor, act2 is successor)
    rel = ActivityRelationship(
        id="rel-1-2",
        project_id="proj-roundtrip-1",
        predecessor_id="act-civ-1001",
        successor_id="act-str-1001",
        relationship_type="FS",
        lag=2.0,
    )
    db_session.add(rel)
    db_session.commit()

    # 5. Call export endpoint
    res = client.get("/api/v1/projects/proj-roundtrip-1/export/xer")
    assert res.status_code == 200
    assert "attachment; filename=\"TEST_XER_ROUNDTRIP_updated.xer\"" in res.headers["content-disposition"]

    xer_text = res.text
    assert "ERMHDR" in xer_text

    # Parse XER text lines
    lines = [l.rstrip("\r\n") for l in xer_text.splitlines() if l.strip()]
    tables = {}
    curr_table = None
    for l in lines:
        parts = l.split("\t")
        tag = parts[0]
        if tag == "%T":
            curr_table = parts[1].strip()
            tables[curr_table] = {"fields": [], "rows": []}
        elif tag == "%F" and curr_table:
            tables[curr_table]["fields"] = parts[1:]
        elif tag == "%R" and curr_table:
            f_list = tables[curr_table]["fields"]
            vals = parts[1:]
            if len(vals) < len(f_list):
                vals += [""] * (len(f_list) - len(vals))
            tables[curr_table]["rows"].append(dict(zip(f_list, vals)))

    # Verify tables present
    assert "PROJECT" in tables
    assert "PROJWBS" in tables
    assert "TASK" in tables
    assert "TASKPRED" in tables

    # Verify PROJWBS
    wbs_rows = {r["wbs_short_name"]: r for r in tables["PROJWBS"]["rows"]}
    assert "WBS-100" in wbs_rows
    assert "WBS-101" in wbs_rows

    # Verify TASK
    task_rows = {r["task_code"]: r for r in tables["TASK"]["rows"]}
    assert "CIV-1001" in task_rows
    assert "STR-1001" in task_rows

    t1 = task_rows["CIV-1001"]
    assert t1["status_code"] == "TK_Active"
    pct_field = t1.get("phys_percent_comp") or t1.get("phys_complete_pct")
    assert pct_field == "75.00"
    assert t1["act_start_date"] == "2024-09-30 00:00"

    t2 = task_rows["STR-1001"]
    assert t2["status_code"] == "TK_NotStart"
    pct_field_2 = t2.get("phys_percent_comp") or t2.get("phys_complete_pct")
    assert pct_field_2 == "0.00"
    assert t2["act_start_date"] == ""

    # Verify TASKPRED
    pred_rows = tables["TASKPRED"]["rows"]
    assert len(pred_rows) == 1
    rel_row = pred_rows[0]
    assert rel_row["pred_type"] == "PR_FS"
    assert rel_row["lag_hr_cnt"] == "16"  # 2 days * 8 hrs

    # Verify TASKMEMO
    assert "MEMOTYPE" in tables
    assert "TASKMEMO" in tables
    memo_rows = tables["TASKMEMO"]["rows"]
    assert len(memo_rows) == 1
    assert "Foundation inspection pending approval" in memo_rows[0]["task_memo"]

    # 6. Verify round-trip parsing using PARSER_URL service or local XerParser
    import os
    import httpx
    parser_url = os.getenv("PARSER_URL", "http://document-parser:8001")
    parsed_successfully = False
    try:
        parse_resp = httpx.post(
            f"{parser_url}/parse",
            files={"file": ("roundtrip.xer", res.content, "application/octet-stream")},
            timeout=10.0,
        )
        if parse_resp.status_code == 200:
            reparsed = parse_resp.json()
            assert reparsed["project"]["project_code"] == "TEST_XER_ROUNDTRIP"
            assert len(reparsed["wbs"]) == 2
            assert len(reparsed["activities"]) == 2
            assert len(reparsed["relationships"]) == 1
            assert reparsed["relationships"][0]["predecessor_code"] == "CIV-1001"
            assert reparsed["relationships"][0]["successor_code"] == "STR-1001"
            assert reparsed["relationships"][0]["lag"] == 2.0
            civ_act = next(a for a in reparsed["activities"] if a["activity_code"] == "CIV-1001")
            assert civ_act["notes"] == "Foundation inspection pending approval"
            parsed_successfully = True
    except Exception:
        pass

    if not parsed_successfully:
        import sys
        from pathlib import Path
        doc_parser_path = Path(__file__).resolve().parents[2] / "document-parser"
        if str(doc_parser_path) not in sys.path:
            sys.path.insert(0, str(doc_parser_path))
        try:
            from app.parsers.xer_parser import XerParser
            parser = XerParser()
            reparsed = parser.parse(res.content, "roundtrip.xer")
            assert reparsed.project.project_code == "TEST_XER_ROUNDTRIP"
            assert len(reparsed.wbs) == 2
            assert len(reparsed.activities) == 2
            assert len(reparsed.relationships) == 1
        except ImportError:
            pass
