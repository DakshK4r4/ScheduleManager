import pytest
from datetime import datetime
from app.services.matching_service import MatchingService
from app.services.extraction_service import ExtractionService
from app.domain.models import Activity, ExecutionEvent, WBSNode

from pathlib import Path

def test_demo_matching_produces_3_auto_link_1_review():
    pdf_path = Path(__file__).resolve().parent.parent.parent / "samples" / "ScheduleManager_Demo_Field_Report_4_Cases.pdf"
    if not pdf_path.exists():
        pdf_path = Path("/samples/ScheduleManager_Demo_Field_Report_4_Cases.pdf")
    if not pdf_path.exists():
        pdf_path = Path("samples/ScheduleManager_Demo_Field_Report_4_Cases.pdf")

    with open(pdf_path, 'rb') as f:
        items = ExtractionService.parse_pdf(f.read())

    assert len(items) == 4, f"Expected exactly 4 events, got {len(items)}"

    wbs_piers = WBSNode(id='wbs-1', code='1.1.2.1', name='Substructure / Piers')
    wbs_piping = WBSNode(id='wbs-2', code='1.5', name='Piping / Drainage')

    acts = [
        Activity(id='1', activity_code='CIV-2010', name='Site Mobilization', location_code=None, planned_start=datetime(2026,9,1), planned_finish=datetime(2026,9,4), planned_quantity=1.0, quantity_unit='LS'),
        Activity(id='2', activity_code='CIV-2020', name='Pier 13 Foundation Excavation', location_code='Pier 13', planned_start=datetime(2026,9,5), planned_finish=datetime(2026,9,10), planned_quantity=850.0, quantity_unit='m3'),
        Activity(id='3', activity_code='CIV-2030', name='Pier 14 Reinforcement & Formwork', location_code='Pier 14', planned_start=datetime(2026,9,8), planned_finish=datetime(2026,9,14), planned_quantity=32.0, quantity_unit='t'),
        Activity(id='4', activity_code='CIV-2040', name='Pier 14 Cap Beam Concrete Pour', location_code='Pier 14', planned_start=datetime(2026,9,10), planned_finish=datetime(2026,9,25), planned_quantity=140.0, quantity_unit='m3'),
        Activity(id='5', activity_code='CIV-2041', name='Pier 15 Cap Beam Concrete Pour', location_code='Pier 15', planned_start=datetime(2026,9,12), planned_finish=datetime(2026,9,27), planned_quantity=140.0, quantity_unit='m3'),
        Activity(id='6', activity_code='CIV-2050', name='Pier 14 Concrete Curing & Inspection', location_code='Pier 14', planned_start=datetime(2026,9,16), planned_finish=datetime(2026,9,29), planned_quantity=1.0, quantity_unit='LS'),
        Activity(id='7', activity_code='STR-3010', name='Pier 14 Bearing Pedestal Installation', location_code='Pier 14', planned_start=datetime(2026,9,24), planned_finish=datetime(2026,10,2), planned_quantity=4.0, quantity_unit='EA'),
        Activity(id='8', activity_code='MEC-4010', name='Temporary Access Platform Installation', location_code=None, planned_start=datetime(2026,9,18), planned_finish=datetime(2026,9,22), planned_quantity=1.0, quantity_unit='LS'),
        Activity(id='9', activity_code='ELE-5010', name='Temporary Site Power Distribution', location_code=None, planned_start=datetime(2026,9,3), planned_finish=datetime(2026,9,12), planned_quantity=1.0, quantity_unit='LS'),
        Activity(id='10', activity_code='PIP-6010', name='Drainage Header Installation', location_code=None, planned_start=datetime(2026,9,20), planned_finish=datetime(2026,10,5), planned_quantity=180.0, quantity_unit='m'),
    ]

    results = []
    for idx, item in enumerate(items, 1):
        ev = ExecutionEvent(
            id=f'e{idx}',
            description=item['description'],
            verbatim_excerpt=item['verbatim_excerpt'],
            activity_reference=item['activity_reference'],
            location=item['location'],
            quantity=item['quantity'],
            unit=item['unit'],
            execution_date=datetime.strptime(item['execution_date'], '%Y-%m-%d'),
            extraction_confidence=item['extraction_confidence']
        )
        scored = []
        for a in acts:
            wbs = wbs_piers if 'Pier' in a.name else (wbs_piping if 'Drainage' in a.name else None)
            score, bd = MatchingService.score_activity(ev, a, wbs)
            scored.append((a.activity_code, a.name, score, bd))
        scored.sort(key=lambda x: x[2], reverse=True)
        top = scored[0]
        second = scored[1] if len(scored) > 1 else None
        margin = (top[2] - second[2]) if second else top[2]
        is_auto_link = (top[2] >= 0.85 and margin >= 0.15 and (ev.extraction_confidence or 0.0) >= 0.80)
        route = "AUTO_LINK" if is_auto_link else "PLANNER_REVIEW"
        results.append({
            "event_num": idx,
            "top_code": top[0],
            "score": top[2],
            "margin": margin,
            "route": route,
            "confidence": ev.extraction_confidence
        })

    # Event 1: CIV-2040, score >= 0.95, margin >= 0.15, AUTO_LINK
    e1 = results[0]
    assert e1["top_code"] == "CIV-2040"
    assert e1["score"] >= 0.95, f"Event 1 score {e1['score']} < 0.95"
    assert e1["margin"] >= 0.15, f"Event 1 margin {e1['margin']} < 0.15"
    assert e1["route"] == "AUTO_LINK"

    # Event 2: CIV-2030, score >= 0.90, margin >= 0.15, AUTO_LINK
    e2 = results[1]
    assert e2["top_code"] == "CIV-2030"
    assert e2["score"] >= 0.90, f"Event 2 score {e2['score']} < 0.90"
    assert e2["margin"] >= 0.15, f"Event 2 margin {e2['margin']} < 0.15"
    assert e2["route"] == "AUTO_LINK"

    # Event 3: PIP-6010, score >= 0.90, margin >= 0.15, AUTO_LINK
    e3 = results[2]
    assert e3["top_code"] == "PIP-6010"
    assert e3["score"] >= 0.90, f"Event 3 score {e3['score']} < 0.90"
    assert e3["margin"] >= 0.15, f"Event 3 margin {e3['margin']} < 0.15"
    assert e3["route"] == "AUTO_LINK"

    # Event 4: PLANNER_REVIEW, top candidate does NOT satisfy confidence/margin
    e4 = results[3]
    assert e4["route"] == "PLANNER_REVIEW"

    auto_linked = sum(1 for r in results if r["route"] == "AUTO_LINK")
    in_review = sum(1 for r in results if r["route"] == "PLANNER_REVIEW")
    assert auto_linked == 3
    assert in_review == 1
