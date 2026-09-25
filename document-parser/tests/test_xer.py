import pytest
from app.models.canonical import ActivityStatus, RelationshipType
from app.parsers.xer_parser import XerParser

SAMPLE_XER = """ERMHDR\t19.12\t2024-01-01\tProject\tadmin\tDemo\tPROJECT_01\tUSD\tDD/MM/YYYY\t1\t0\t0
%T\tPROJECT
%F\tproj_id\tproj_short_name\tplan_start_date\tplan_end_date
%R\t1\tPROJ-01\t2024-01-01 08:00\t2024-12-31 17:00
%T\tPROJWBS
%F\twbs_id\tproj_id\twbs_short_name\twbs_name\tparent_wbs_id
%R\t10\t1\tWBS.1\tEngineering\t
%R\t11\t1\tWBS.1.1\tCivil\t10
%T\tTASK
%F\ttask_id\tproj_id\twbs_id\ttask_code\ttask_name\ttask_type\tstatus_code\ttarget_drtn_hr_cnt\ttarget_start_date\ttarget_end_date\tphys_percent_comp
%R\t101\t1\t11\tCIV-01\tSite Prep\tTT_Task\tTK_Complete\t80\t2024-01-01 08:00\t2024-01-10 17:00\t100
%R\t102\t1\t11\tCIV-02\tExcavation\tTT_Task\tTK_Active\t160\t2024-01-11 08:00\t2024-01-31 17:00\t40
%T\tTASKPRED
%F\ttask_pred_id\ttask_id\tpred_task_id\tpred_type\tlag_hr_cnt
%R\t501\t102\t101\tPR_FS\t0
%E
"""

def test_parse_valid_xer():
    parser = XerParser()
    result = parser.parse(SAMPLE_XER.encode("utf-8"), "test.xer")

    assert result.project.project_code == "PROJ-01"
    assert len(result.wbs) == 2
    assert result.wbs[0].code == "WBS.1"
    assert result.wbs[1].parent_code == "WBS.1"

    assert len(result.activities) == 2
    act1 = result.activities[0]
    assert act1.activity_code == "CIV-01"
    assert act1.status == ActivityStatus.COMPLETED
    assert act1.original_duration == 10.0  # 80 hrs / 8 = 10 days
    assert act1.percent_complete == 100.0

    act2 = result.activities[1]
    assert act2.activity_code == "CIV-02"
    assert act2.status == ActivityStatus.IN_PROGRESS
    assert act2.original_duration == 20.0  # 160 hrs / 8 = 20 days
    assert act2.percent_complete == 40.0

    assert len(result.relationships) == 1
    rel = result.relationships[0]
    assert rel.predecessor_code == "CIV-01"
    assert rel.successor_code == "CIV-02"
    assert rel.relationship_type == RelationshipType.FS


SAMPLE_XER_WITH_CONSTRAINTS_AND_CODES = """ERMHDR\t19.12\t2024-01-01\tProject\tadmin\tDemo\tPROJECT_02\tUSD\tDD/MM/YYYY\t1\t0\t0
%T\tPROJECT
%F\tproj_id\tproj_short_name
%R\t1\tPROJ-02
%T\tPROJWBS
%F\twbs_id\tproj_id\twbs_short_name\twbs_name
%R\t10\t1\tWBS.1\tGeneral
%T\tACTVTYPE
%F\tactv_code_type_id\tactv_code_type_name
%R\t101\tDiscipline
%T\tACTVCODE
%F\tactv_code_id\tactv_code_type_id\tshort_name\tactv_code_name
%R\t501\t101\tCIV\tCivil Engineering
%T\tTASK
%F\ttask_id\tproj_id\twbs_id\ttask_code\ttask_name\tstatus_code\tcstr_type\tcstr_date
%R\t101\t1\t10\tCIV-10\tFoundation\tTK_NotStart\tCS_MS\t2024-05-15 08:00
%T\tTASKACTV
%F\ttask_id\tactv_code_type_id\tactv_code_id
%R\t101\t101\t501
%E
"""


def test_parse_xer_with_constraints_and_activity_codes():
    parser = XerParser()
    result = parser.parse(SAMPLE_XER_WITH_CONSTRAINTS_AND_CODES.encode("utf-8"), "test_codes.xer")

    assert len(result.activities) == 1
    act = result.activities[0]
    assert act.activity_code == "CIV-10"
    assert act.constraint_type == "MANDATORY_START"
    assert act.constraint_date is not None
    assert act.constraint_date.year == 2024
    assert act.constraint_date.month == 5
    assert act.constraint_date.day == 15
    assert act.activity_codes == {"Discipline": "Civil Engineering"}

