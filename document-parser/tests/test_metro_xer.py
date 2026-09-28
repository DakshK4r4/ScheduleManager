import pytest
from app.models.canonical import ActivityStatus, RelationshipType
from app.parsers.xer_parser import XerParser

METRO_XER = """ERMHDR\t20.12\t2026-09-28 00:00:00\tPM\tadmin\tMetro Bridge Demo
%T\tPROJECT
%F\tproj_id\tproj_short_name\tproj_name\tplan_start_date\tplan_end_date
%R\tBRIDGE-2026\tMetro Bridge Construction\tMetro Bridge Construction\t2026-09-01 00:00:00\t2026-10-05 00:00:00
%T\tPROJWBS
%F\tproj_id\twbs_code\twbs_name\tparent_wbs_code
%R\tBRIDGE-2026\t1\tMetro Bridge Construction\t
%R\tBRIDGE-2026\t1.1\tCivil Works\t1
%R\tBRIDGE-2026\t1.1.1\tPreliminaries\t1.1
%R\tBRIDGE-2026\t1.1.2\tSubstructure\t1.1
%R\tBRIDGE-2026\t1.1.2.1\tPiers\t1.1.2
%R\tBRIDGE-2026\t1.2\tStructural Works\t1
%R\tBRIDGE-2026\t1.3\tMechanical\t1
%R\tBRIDGE-2026\t1.4\tElectrical\t1
%R\tBRIDGE-2026\t1.5\tPiping\t1
%T\tTASK
%F\ttask_id\ttask_code\ttask_name\tstart_date\tend_date\tplanned_qty\tqty_unit\tproj_id\twbs_code\tphys_complete_pct
%R\t1\tCIV-2010\tSite Mobilization\t2026-09-01 08:00:00\t2026-09-04 17:00:00\t1\tLS\tTK_1\tCIV-2010\t0
%R\t2\tCIV-2020\tPier 13 Foundation Excavation\t2026-09-05 08:00:00\t2026-09-10 17:00:00\t850\tm3\tTK_1\tCIV-2020\t0
%R\t3\tCIV-2030\tPier 14 Reinforcement & Formwork\t2026-09-08 08:00:00\t2026-09-14 17:00:00\t32\tt\tTK_1\tCIV-2030\t0
%R\t4\tCIV-2040\tPier 14 Cap Beam Concrete Pour\t2026-09-10 08:00:00\t2026-09-25 17:00:00\t140\tm3\tTK_1\tCIV-2040\t0
%R\t5\tCIV-2041\tPier 15 Cap Beam Concrete Pour\t2026-09-12 08:00:00\t2026-09-27 17:00:00\t140\tm3\tTK_1\tCIV-2041\t0
%R\t6\tCIV-2050\tPier 14 Concrete Curing & Inspection\t2026-09-16 08:00:00\t2026-09-29 17:00:00\t1\tLS\tTK_1\tCIV-2050\t0
%R\t7\tSTR-3010\tPier 14 Bearing Pedestal Installation\t2026-09-24 08:00:00\t2026-10-02 17:00:00\t4\tEA\tTK_1\tSTR-3010\t0
%R\t8\tMEC-4010\tTemporary Access Platform Installation\t2026-09-18 08:00:00\t2026-09-22 17:00:00\t1\tLS\tTK_1\tMEC-4010\t0
%R\t9\tELE-5010\tTemporary Site Power Distribution\t2026-09-03 08:00:00\t2026-09-12 17:00:00\t1\tLS\tTK_1\tELE-5010\t0
%R\t10\tPIP-6010\tDrainage Header Installation\t2026-09-20 08:00:00\t2026-10-05 17:00:00\t180\tm\tTK_1\tPIP-6010\t0
%T\tTASKPRED
%F\tpred_id\ttask_id\tpred_task_id\tpred_type\tlag_hr_cnt
%R\t1\t2\t1\tPR_FS\t0
%R\t2\t3\t2\tPR_FS\t0
%R\t3\t4\t3\tPR_FS\t0
%R\t4\t5\t3\tPR_FS\t0
%R\t5\t6\t4\tPR_FS\t0
%R\t6\t7\t6\tPR_FS\t0
%R\t7\t8\t3\tPR_FS\t0
%R\t8\t9\t1\tPR_FS\t0
%R\t9\t10\t1\tPR_FS\t0
%E
"""

def test_metro_xer_parsing():
    parser = XerParser()
    result = parser.parse(METRO_XER.encode("utf-8"), "Metro_Bridge_Demo_Schedule.xer")

    assert result.project.project_code == "Metro Bridge Construction" or result.project.name == "Metro Bridge Construction"
    assert len(result.wbs) == 9
    assert result.wbs[0].code == "1"
    assert result.wbs[1].code == "1.1"
    assert result.wbs[1].parent_code == "1"

    assert len(result.activities) == 10
    act_codes = [a.activity_code for a in result.activities]
    assert "CIV-2010" in act_codes
    assert "CIV-2020" in act_codes
    assert "STR-3010" in act_codes

    assert len(result.relationships) == 9
    rel_pairs = [(r.predecessor_code, r.successor_code) for r in result.relationships]
    assert ("CIV-2010", "CIV-2020") in rel_pairs
    assert ("CIV-2020", "CIV-2030") in rel_pairs

    # Verify quantities and units parsed from XER
    civ_2040 = next(a for a in result.activities if a.activity_code == "CIV-2040")
    assert civ_2040.planned_quantity == 140.0
    assert civ_2040.quantity_unit == "m3"
    assert civ_2040.wbs_code in ("1.1.2.1", "1.1.2", "1.1")  # Smart WBS matched to Piers/Substructure/Civil

    pip_6010 = next(a for a in result.activities if a.activity_code == "PIP-6010")
    assert pip_6010.planned_quantity == 180.0
    assert pip_6010.quantity_unit == "m"
    assert pip_6010.discipline == "Piping"


METRO_CSV = """activity_code,activity_name,wbs,planned_start,planned_finish,planned_quantity,unit,discipline,predecessor
CIV-2010,Site Mobilization,Civil Works / Preliminaries,2026-09-01,2026-09-04,1,LS,Civil,
CIV-2020,Pier 13 Foundation Excavation,Civil Works / Substructure / Piers,2026-09-05,2026-09-10,850,m3,Civil,CIV-2010
CIV-2030,Pier 14 Reinforcement & Formwork,Civil Works / Substructure / Piers,2026-09-08,2026-09-14,32,t,Civil,CIV-2020
CIV-2040,Pier 14 Cap Beam Concrete Pour,Civil Works / Substructure / Piers,2026-09-10,2026-09-25,140,m3,Civil,CIV-2030
CIV-2041,Pier 15 Cap Beam Concrete Pour,Civil Works / Substructure / Piers,2026-09-12,2026-09-27,140,m3,Civil,CIV-2030
CIV-2050,Pier 14 Concrete Curing & Inspection,Civil Works / Substructure / Piers,2026-09-16,2026-09-29,1,LS,Civil,CIV-2040
STR-3010,Pier 14 Bearing Pedestal Installation,Structural Works / Bearings,2026-09-24,2026-10-02,4,EA,Structural,CIV-2050
MEC-4010,Temporary Access Platform Installation,Mechanical / Access,2026-09-18,2026-09-22,1,LS,Mechanical,CIV-2030
ELE-5010,Temporary Site Power Distribution,Electrical / Temporary Works,2026-09-03,2026-09-12,1,LS,Electrical,CIV-2010
PIP-6010,Drainage Header Installation,Piping / Drainage,2026-09-20,2026-10-05,180,m,Piping,CIV-2010
"""

def test_metro_csv_parsing():
    from app.parsers.csv_parser import CsvParser
    parser = CsvParser()
    result = parser.parse(METRO_CSV.encode("utf-8"), "Metro_Bridge_Demo_Schedule.csv")

    assert len(result.activities) == 10
    assert len(result.relationships) == 9

    # Verify hierarchical WBS nodes were generated from slash-separated paths
    wbs_codes = [w.code for w in result.wbs]
    assert "Civil Works" in wbs_codes
    assert "Civil Works / Substructure" in wbs_codes
    assert "Civil Works / Substructure / Piers" in wbs_codes

    # Check child-parent linking
    sub_node = next(w for w in result.wbs if w.code == "Civil Works / Substructure")
    assert sub_node.parent_code == "Civil Works"
    piers_node = next(w for w in result.wbs if w.code == "Civil Works / Substructure / Piers")
    assert piers_node.parent_code == "Civil Works / Substructure"

    # Verify quantity, unit, discipline, and auto-computed duration
    civ_2040 = next(a for a in result.activities if a.activity_code == "CIV-2040")
    assert civ_2040.planned_quantity == 140.0
    assert civ_2040.quantity_unit == "m3"
    assert civ_2040.discipline == "Civil"
    assert civ_2040.original_duration is not None and civ_2040.original_duration > 0

    pip_6010 = next(a for a in result.activities if a.activity_code == "PIP-6010")
    assert pip_6010.planned_quantity == 180.0
    assert pip_6010.quantity_unit == "m"
    assert pip_6010.discipline == "Piping"
