import io
import json
import pytest
from datetime import datetime
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.models import (
    Activity,
    ActualProgressLedger,
    Artifact,
    DomainOutbox,
    ExecutionEvent,
    Project,
    ScheduleAuditLog,
    WBSNode,
)
from app.services.minio_service import minio_service
from app.services.matching_service import MatchingService
from app.services.extraction_service import ExtractionService


@pytest.fixture(autouse=True)
def disable_live_llm_extraction(monkeypatch):
    """Ensure tests run hermetically without making live external LLM API calls."""
    monkeypatch.setattr(ExtractionService, "extract_with_llm", classmethod(lambda cls, *args, **kwargs: None))



SAMPLE_PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
    b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
    b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
    b"4 0 obj << /Length 135 >> stream\n"
    b"BT /F1 12 Tf 72 712 Td (Daily Site Progress Report: Poured 140 m3 concrete for Pier 14 cap beam between 08:00 and 16:30.) Tj ET\n"
    b"endstream endobj\n"
    b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
    b"xref\n0 6\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000261 00000 n \n0000000448 00000 n \n"
    b"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n525\n%%EOF\n"
)


def create_test_project_and_activities(db: Session) -> tuple[Project, Activity, Activity]:
    proj = Project(
        id="proj-101",
        project_code="BRIDGE-2026",
        name="Metro Bridge Construction",
        planned_start=datetime(2026, 1, 1),
        planned_finish=datetime(2026, 12, 31),
    )
    db.add(proj)

    wbs = WBSNode(
        id="wbs-substructure",
        project_id="proj-101",
        code="WBS-1.2",
        name="Substructure - Bridge Piers",
    )
    db.add(wbs)

    # High match activity
    act_pier14 = Activity(
        id="act-pier14",
        project_id="proj-101",
        wbs_id="wbs-substructure",
        activity_code="CIV-2040",
        name="Pier 14 Cap Beam Concrete Pour",
        status="NOT_STARTED",
        planned_start=datetime(2026, 9, 1),
        planned_finish=datetime(2026, 9, 30),
        percent_complete=0.0,
        planned_quantity=200.0,
        quantity_unit="m3",
        location_code="Pier 14",
        discipline="Civil / Structural",
    )
    db.add(act_pier14)

    # Distractor activity (Low / Ambiguous match)
    act_pier15 = Activity(
        id="act-pier15",
        project_id="proj-101",
        wbs_id="wbs-substructure",
        activity_code="CIV-2050",
        name="Pier 15 Foundation Excavation",
        status="NOT_STARTED",
        planned_start=datetime(2026, 9, 1),
        planned_finish=datetime(2026, 9, 30),
        percent_complete=0.0,
        planned_quantity=500.0,
        quantity_unit="m3",
        location_code="Pier 15",
        discipline="Civil / Structural",
    )
    db.add(act_pier15)

    db.commit()
    db.refresh(proj)
    db.refresh(act_pier14)
    db.refresh(act_pier15)
    return proj, act_pier14, act_pier15


# -------------------------------------------------------------
# 1. ARTIFACT INGESTION & STORAGE TESTS
# -------------------------------------------------------------
def test_artifact_upload_and_minio_storage(client: TestClient, db_session: Session):
    proj, _, _ = create_test_project_and_activities(db_session)

    files = {"file": ("Daily_Site_Report_Pier14.pdf", SAMPLE_PDF_BYTES, "application/pdf")}
    data = {"report_id": "rep-501", "uploaded_by": "site-engineer-1"}

    response = client.post(f"/api/v1/projects/{proj.id}/artifacts/upload", files=files, data=data)
    assert response.status_code == 201
    res_json = response.json()
    assert res_json["is_duplicate"] is False
    artifact = res_json["artifact"]
    assert artifact["project_id"] == proj.id
    assert artifact["report_id"] == "rep-501"
    assert artifact["original_filename"] == "Daily_Site_Report_Pier14.pdf"
    assert artifact["mime_type"] == "application/pdf"
    assert artifact["size_bytes"] == len(SAMPLE_PDF_BYTES)
    assert len(artifact["sha256"]) == 64
    assert artifact["extraction_status"] == "UPLOADED"

    # Verify binary stored and retrievable
    retrieved_bytes = minio_service.get_artifact_bytes(artifact["storage_key"])
    assert retrieved_bytes == SAMPLE_PDF_BYTES


def test_artifact_duplicate_detection(client: TestClient, db_session: Session):
    proj, _, _ = create_test_project_and_activities(db_session)

    files = {"file": ("Daily_Site_Report_Pier14.pdf", SAMPLE_PDF_BYTES, "application/pdf")}
    # First upload
    res1 = client.post(f"/api/v1/projects/{proj.id}/artifacts/upload", files=files)
    assert res1.status_code == 201
    assert res1.json()["is_duplicate"] is False

    # Second upload with identical bytes
    files2 = {"file": ("Duplicate_Copy.pdf", SAMPLE_PDF_BYTES, "application/pdf")}
    res2 = client.post(f"/api/v1/projects/{proj.id}/artifacts/upload", files=files2)
    assert res2.status_code == 201
    res2_json = res2.json()
    assert res2_json["is_duplicate"] is True
    assert res2_json["artifact"]["sha256"] == res1.json()["artifact"]["sha256"]


def test_artifact_presigned_url(client: TestClient, db_session: Session):
    proj, _, _ = create_test_project_and_activities(db_session)
    files = {"file": ("Daily_Site_Report.pdf", SAMPLE_PDF_BYTES, "application/pdf")}
    upload_res = client.post(f"/api/v1/projects/{proj.id}/artifacts/upload", files=files).json()
    art_id = upload_res["artifact"]["artifact_id"]

    res = client.get(f"/api/v1/artifacts/{art_id}/view-url")
    assert res.status_code == 200
    assert "url" in res.json()
    assert res.json()["expires_in_seconds"] == 900


# -------------------------------------------------------------
# 2. EXTRACTION & PROVENANCE TESTS
# -------------------------------------------------------------
def test_extraction_creates_structured_events_with_provenance(client: TestClient, db_session: Session):
    proj, _, _ = create_test_project_and_activities(db_session)
    files = {"file": ("Daily_Site_Report_Pier14.pdf", SAMPLE_PDF_BYTES, "application/pdf")}
    art_id = client.post(f"/api/v1/projects/{proj.id}/artifacts/upload", files=files).json()["artifact"]["artifact_id"]

    # Trigger extraction
    ext_res = client.post(f"/api/v1/artifacts/{art_id}/extract")
    assert ext_res.status_code == 200
    ext_json = ext_res.json()
    assert ext_json["events_extracted"] >= 1

    event = ext_json["events"][0]
    assert event["artifact_id"] == art_id
    assert event["source_document_name"] == "Daily_Site_Report_Pier14.pdf"
    assert "Pier 14" in event["verbatim_excerpt"]
    assert event["quantity"] == 140.0
    assert event["unit"] == "m3"
    assert event["location"] == "Pier 14"
    assert event["extraction_confidence"] >= 0.80

    # Verify original artifact remains EXTRACTED
    db_art = db_session.query(Artifact).filter(Artifact.id == art_id).first()
    assert db_art.extraction_status == "EXTRACTED"


def test_voice_memo_storage_and_metadata(client: TestClient, db_session: Session):
    proj, _, _ = create_test_project_and_activities(db_session)
    dummy_audio_bytes = b"ID3\x03\x00\x00\x00\x00\x00#AUDIO_BYTES_TEST"
    files = {"file": ("foreman_shift_update.m4a", dummy_audio_bytes, "audio/mp4")}

    res = client.post(f"/api/v1/projects/{proj.id}/artifacts/upload", files=files)
    assert res.status_code == 201
    art_json = res.json()["artifact"]
    assert art_json["artifact_type"] == "VOICE_MEMO"

    # Extracting voice memo preserves evidence and sets NEEDS_REVIEW without fabricating fake events
    ext_res = client.post(f"/api/v1/artifacts/{art_json['artifact_id']}/extract")
    assert ext_res.status_code == 200
    assert ext_res.json()["status"] == "NEEDS_REVIEW"
    events = ext_res.json()["events"]
    assert len(events) == 0  # CRITICAL: Never fabricate a fake ExecutionEvent when STT fails or produces no audio

def test_voice_memo_stt_success_flow(client: TestClient, db_session: Session, monkeypatch):
    proj, _, _ = create_test_project_and_activities(db_session)
    dummy_audio_bytes = b"ID3\x03\x00\x00\x00\x00\x00#AUDIO_BYTES_TEST"
    files = {"file": ("foreman_shift_update.m4a", dummy_audio_bytes, "audio/mp4")}

    res = client.post(f"/api/v1/projects/{proj.id}/artifacts/upload", files=files)
    art_json = res.json()["artifact"]

    # Mock Sarvam STT returning a valid transcription
    from app.services.sarvam_service import SarvamService
    monkeypatch.setattr(
        SarvamService,
        "transcribe_audio",
        classmethod(lambda cls, *args, **kwargs: {
            "transcript": "Today we completed 140 m3 concrete pour for Pier 14 cap beam.",
            "detected_language_code": "en-IN",
        })
    )
    # Mock LLM extraction returning structured event from transcript
    monkeypatch.setattr(
        ExtractionService,
        "extract_with_llm",
        classmethod(lambda cls, text, *args, **kwargs: [{
            "verbatim_excerpt": "completed 140 m3 concrete pour for Pier 14 cap beam",
            "activity_reference": "Pier 14 Cap Beam",
            "reported_activity_code": "CIV-2040",
            "description": "Concrete pour for Pier 14 cap beam",
            "execution_date": "2026-09-15",
            "quantity": 140.0,
            "unit": "m3",
            "location": "Pier 14",
            "discipline": "Civil / Structural",
            "status_reported": "COMPLETED",
            "page_number": 1,
            "extraction_confidence": 0.95,
        }])
    )

    ext_res = client.post(f"/api/v1/artifacts/{art_json['artifact_id']}/extract")
    assert ext_res.status_code == 200
    assert ext_res.json()["status"] == "EXTRACTED"
    events = ext_res.json()["events"]
    assert len(events) == 1
    assert events[0]["quantity"] == 140.0
    assert events[0]["reported_activity_code"] == "CIV-2040"

def test_extraction_idempotency_prevents_duplicate_events(client: TestClient, db_session: Session):
    proj, _, _ = create_test_project_and_activities(db_session)
    csv_content = (
        "Activity,Quantity,Unit,Date,Status,Location\n"
        "Foundation excavation,100,m3,2024-03-01,COMPLETED,Pier 14\n"
    ).encode("utf-8")
    files = {"file": ("daily_work.csv", csv_content, "text/csv")}
    art_id = client.post(f"/api/v1/projects/{proj.id}/artifacts/upload", files=files).json()["artifact"]["artifact_id"]

    # First extraction
    res1 = client.post(f"/api/v1/artifacts/{art_id}/extract")
    assert res1.status_code == 200
    assert res1.json()["events_extracted"] == 1

    events_in_db_1 = db_session.query(ExecutionEvent).filter(ExecutionEvent.artifact_id == art_id).count()
    assert events_in_db_1 == 1

    # Second extraction without force_reextract should be completely idempotent (no duplicates created)
    res2 = client.post(f"/api/v1/artifacts/{art_id}/extract")
    assert res2.status_code == 200
    assert res2.json()["events_extracted"] == 1
    events_in_db_2 = db_session.query(ExecutionEvent).filter(ExecutionEvent.artifact_id == art_id).count()
    assert events_in_db_2 == 1

    # Re-extraction with force_reextract=True cleans up unapproved events and recreates fresh
    res3 = client.post(f"/api/v1/artifacts/{art_id}/extract?force_reextract=true")
    assert res3.status_code == 200
    assert res3.json()["events_extracted"] == 1
    events_in_db_3 = db_session.query(ExecutionEvent).filter(ExecutionEvent.artifact_id == art_id).count()
    assert events_in_db_3 == 1


# -------------------------------------------------------------
# 3. MATCHING & CONFIDENCE ROUTING TESTS
# -------------------------------------------------------------
def test_matching_confidence_routing_high_and_auto_link(client: TestClient, db_session: Session):
    proj, act_pier14, _ = create_test_project_and_activities(db_session)
    files = {"file": ("Daily_Site_Report_Pier14.pdf", SAMPLE_PDF_BYTES, "application/pdf")}
    art_id = client.post(f"/api/v1/projects/{proj.id}/artifacts/upload", files=files).json()["artifact"]["artifact_id"]
    client.post(f"/api/v1/artifacts/{art_id}/extract")

    # Evaluate matching
    eval_res = client.post(
        "/api/v1/matching/evaluate",
        json={"project_id": proj.id},
    )
    assert eval_res.status_code == 200
    eval_json = eval_res.json()
    assert eval_json["evaluated_count"] >= 1
    assert eval_json["auto_linked_count"] == 1

    top_result = eval_json["results"][0]
    assert top_result["route"] == "AUTO_LINK"
    assert top_result["selected_candidate"]["activity_id"] == act_pier14.id
    assert top_result["selected_candidate"]["match_score"] >= 0.85

    # Confirm schedule state updated automatically
    db_act = db_session.query(Activity).filter(Activity.id == act_pier14.id).first()
    assert db_act.status == "IN_PROGRESS"
    assert db_act.percent_complete > 0.0

    # Confirm ledger and audit log created
    ledger = db_session.query(ActualProgressLedger).filter(ActualProgressLedger.activity_id == act_pier14.id).first()
    assert ledger is not None
    assert ledger.installed_quantity == 140.0
    assert ledger.unit_of_measure == "m3"

    audit = db_session.query(ScheduleAuditLog).filter(ScheduleAuditLog.activity_id == act_pier14.id).first()
    assert audit is not None
    assert audit.artifact_id == art_id
    assert audit.action == "AUTO_LINK_PROGRESS"


def test_matching_low_confidence_routes_to_planner_review(client: TestClient, db_session: Session):
    proj, _, _ = create_test_project_and_activities(db_session)

    # Create an ambiguous event manually
    art = Artifact(
        id="art-ambiguous",
        project_id=proj.id,
        report_id="rep-amb",
        artifact_type="PDF_REPORT",
        original_filename="Ambiguous_Site_Memo.pdf",
        mime_type="application/pdf",
        size_bytes=100,
        sha256="abc123sha",
        storage_bucket="sih-artifacts",
        storage_key=f"projects/{proj.id}/reports/rep-amb/artifacts/art-ambiguous/memo.pdf",
        uploaded_by="site-eng",
        extraction_status="EXTRACTED",
    )
    db_session.add(art)

    amb_event = ExecutionEvent(
        id="ee-ambiguous",
        artifact_id=art.id,
        project_id=proj.id,
        source_report_id="rep-amb",
        source_document_name="Ambiguous_Site_Memo.pdf",
        storage_key=art.storage_key,
        file_sha256="abc123sha",
        page_number=1,
        verbatim_excerpt="Conducted miscellaneous concrete patch work across site.",
        description="Miscellaneous concrete patch work",
        execution_date=datetime(2026, 9, 15),
        status_reported="IN_PROGRESS",
        extraction_confidence=0.85,
        status="UNMATCHED",
    )
    db_session.add(amb_event)
    db_session.commit()

    # Evaluate matching
    eval_res = client.post(
        "/api/v1/matching/evaluate",
        json={"project_id": proj.id, "event_ids": [amb_event.id]},
    )
    assert eval_res.status_code == 200
    res_data = eval_res.json()
    assert res_data["review_queued_count"] == 1
    assert res_data["results"][0]["route"] == "PLANNER_REVIEW"

    # Verify event is in review queue
    queue_res = client.get(f"/api/v1/review/queue?project_id={proj.id}")
    assert queue_res.status_code == 200
    q_items = queue_res.json()["items"]
    found = any(item["event"]["event_id"] == amb_event.id for item in q_items)
    assert found is True


# -------------------------------------------------------------
# 4. PLANNER REVIEW APPROVAL & REJECTION TESTS
# -------------------------------------------------------------
def test_planner_review_approval_and_schedule_update(client: TestClient, db_session: Session):
    proj, act_pier14, _ = create_test_project_and_activities(db_session)

    art = Artifact(
        id="art-rev-1",
        project_id=proj.id,
        report_id="rep-rev-1",
        artifact_type="PDF_REPORT",
        original_filename="Site_Log.pdf",
        mime_type="application/pdf",
        size_bytes=100,
        sha256="rev123",
        storage_bucket="sih-artifacts",
        storage_key=f"projects/{proj.id}/reports/rep-rev-1/artifacts/art-rev-1/log.pdf",
        uploaded_by="site-eng",
        extraction_status="EXTRACTED",
    )
    db_session.add(art)

    event = ExecutionEvent(
        id="ee-rev-1",
        artifact_id=art.id,
        project_id=proj.id,
        source_report_id="rep-rev-1",
        source_document_name="Site_Log.pdf",
        storage_key=art.storage_key,
        file_sha256="rev123",
        page_number=1,
        verbatim_excerpt="Completed cap beam pour for Pier 14.",
        description="Cap beam pour for Pier 14",
        execution_date=datetime(2026, 9, 20),
        status_reported="COMPLETED",
        extraction_confidence=0.85,
        status="IN_REVIEW",
        matched_activity_id=act_pier14.id,
    )
    db_session.add(event)
    db_session.commit()

    # Planner approves with adjustment_percent = 100.0
    dec_payload = {
        "event_id": event.id,
        "decision": "APPROVED",
        "activity_id": act_pier14.id,
        "adjustment_percent": 100.0,
        "reviewer_id": "planner-john",
        "notes": "Verified against subcontractor delivery card.",
    }
    dec_res = client.post("/api/v1/review/decisions", json=dec_payload)
    assert dec_res.status_code == 200
    assert dec_res.json()["status"] == "APPROVED"
    assert dec_res.json()["applied_progress_percent"] == 100.0

    # Verify Activity updated to COMPLETED with 100%
    db_session.refresh(act_pier14)
    assert act_pier14.status == "COMPLETED"
    assert act_pier14.percent_complete == 100.0
    assert act_pier14.actual_start is not None
    assert act_pier14.actual_finish is not None

    # Verify Outbox task created
    outbox = db_session.query(DomainOutbox).filter(DomainOutbox.aggregate_id == act_pier14.id).first()
    assert outbox is not None
    assert outbox.event_type == "SCHEDULE_PROGRESS_UPDATED"
    payload = json.loads(outbox.payload)
    assert payload["percent_complete"] == 100.0


def test_planner_review_rejection(client: TestClient, db_session: Session):
    proj, act_pier14, _ = create_test_project_and_activities(db_session)

    art = Artifact(
        id="art-rej-1",
        project_id=proj.id,
        report_id="rep-rej-1",
        artifact_type="PDF_REPORT",
        original_filename="Mistaken_Entry.pdf",
        mime_type="application/pdf",
        size_bytes=100,
        sha256="rej123",
        storage_bucket="sih-artifacts",
        storage_key="dummy_key",
        uploaded_by="site-eng",
        extraction_status="EXTRACTED",
    )
    db_session.add(art)

    event = ExecutionEvent(
        id="ee-rej-1",
        artifact_id=art.id,
        project_id=proj.id,
        source_report_id="rep-rej-1",
        source_document_name="Mistaken_Entry.pdf",
        storage_key=art.storage_key,
        file_sha256="rej123",
        verbatim_excerpt="Activity not related to this project.",
        description="Erroneous work log",
        execution_date=datetime(2026, 9, 21),
        status_reported="IN_PROGRESS",
        status="IN_REVIEW",
    )
    db_session.add(event)
    db_session.commit()

    # Planner rejects
    dec_payload = {
        "event_id": event.id,
        "decision": "REJECTED",
        "reviewer_id": "planner-john",
        "notes": "Work belongs to adjacent Package 2 contract.",
    }
    dec_res = client.post("/api/v1/review/decisions", json=dec_payload)
    assert dec_res.status_code == 200
    assert dec_res.json()["status"] == "REJECTED"

    # Activity must NOT be mutated
    db_session.refresh(act_pier14)
    assert act_pier14.percent_complete == 0.0
    assert act_pier14.status == "NOT_STARTED"

    # Original artifact remains intact
    db_art = db_session.query(Artifact).filter(Artifact.id == art.id).first()
    assert db_art is not None


# -------------------------------------------------------------
# 5. END-TO-END AUDIT TRAIL & P6 XER EXPORT TESTS
# -------------------------------------------------------------
def test_full_pipeline_audit_trail_and_p6_export(client: TestClient, db_session: Session):
    proj, act_pier14, _ = create_test_project_and_activities(db_session)

    # 1. Upload
    files = {"file": ("Final_Bridge_Pier14_Report.pdf", SAMPLE_PDF_BYTES, "application/pdf")}
    upload_res = client.post(f"/api/v1/projects/{proj.id}/artifacts/upload", files=files).json()
    art_id = upload_res["artifact"]["artifact_id"]

    # 2. Extract
    client.post(f"/api/v1/artifacts/{art_id}/extract")

    # 3. Match & Auto-link
    client.post("/api/v1/matching/evaluate", json={"project_id": proj.id})

    # 4. Query Audit Trail
    audit_res = client.get(f"/api/v1/projects/{proj.id}/audit-trail")
    assert audit_res.status_code == 200
    audit_json = audit_res.json()
    assert audit_json["total_records"] >= 1
    log = audit_json["audit_trail"][0]
    assert log["activity_id"] == act_pier14.id
    assert log["artifact"]["id"] == art_id
    assert log["artifact"]["original_filename"] == "Final_Bridge_Pier14_Report.pdf"
    assert "storage_key" in log["artifact"]
    assert log["execution_event"]["id"] is not None

    # 5. Export P6 .XER file
    xer_res = client.get(f"/api/v1/projects/{proj.id}/export/xer")
    assert xer_res.status_code == 200
    xer_text = xer_res.text
    assert "ERMHDR" in xer_text
    assert "%T\tTASK" in xer_text
    assert "CIV-2040" in xer_text
    assert "TK_Active" in xer_text or "TK_Complete" in xer_text


# -------------------------------------------------------------
# 6. LLM-BASED EVENT EXTRACTION & CONFIDENCE FORMULATION TESTS
# -------------------------------------------------------------
def test_extraction_confidence_calculation():
    from app.services.extraction_service import ExtractionService

    # Full fidelity event with verbatim excerpt, date, and all 4 fields
    c_high = ExtractionService.calculate_extraction_confidence(
        verbatim_excerpt="Poured 140 m3 concrete for Pier 14 cap beam",
        date_str="2026-09-15",
        quantity=140.0,
        unit="m3",
        location="Pier 14",
        discipline="Civil / Structural",
        source_text="Daily Report: Poured 140 m3 concrete for Pier 14 cap beam on 2026-09-15",
    )
    # Expected: 0.35*1.0 + 0.25*1.0 + 0.20*1.0 + 0.20*1.0 = 1.0
    assert c_high == 1.0

    # Partial event missing location and quantity
    c_med = ExtractionService.calculate_extraction_confidence(
        verbatim_excerpt="Concreting work ongoing",
        date_str="2026-09-15",
        quantity=None,
        unit=None,
        location=None,
        discipline="Civil",
        source_text="Concreting work ongoing",
    )
    assert 0.80 <= c_med <= 0.90


def test_format_llm_results_schema_conformance():
    from app.services.extraction_service import ExtractionService

    mock_llm_json = {
        "events": [
            {
                "verbatim_excerpt": "Completed 140 m3 concrete pouring for Pier 14 cap beam",
                "activity_reference": "Pier 14 Cap Beam",
                "reported_activity_code": "CIV-2040",
                "description": "Concrete pour for bridge pier cap beam",
                "execution_date": "2026-09-15",
                "quantity": 140.0,
                "unit": "cum",  # should normalize to m3
                "location": "Pier 14",
                "discipline": "Civil / Structural",
                "contractor": "Apex Civil",
                "wbs_hint": "Substructure / Piers",
                "status_reported": "COMPLETED",
                "page_number": 2,
            }
        ]
    }

    formatted = ExtractionService._format_llm_results(
        mock_llm_json, "Completed 140 m3 concrete pouring for Pier 14 cap beam"
    )
    assert len(formatted) == 1
    ev = formatted[0]
    assert ev["verbatim_excerpt"] == "Completed 140 m3 concrete pouring for Pier 14 cap beam"
    assert ev["unit"] == "m3"
    assert ev["status_reported"] == "COMPLETED"
    assert ev["reported_activity_code"] == "CIV-2040"
    assert ev["page_number"] == 2
    assert ev["extraction_confidence"] >= 0.90


def test_extract_with_llm_graceful_fallback(monkeypatch):
    from app.services.extraction_service import ExtractionService

    monkeypatch.delenv("EXTRACTION_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("TIME_AGENT_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = ExtractionService.extract_with_llm("Some field report text", "report.pdf")
    assert result is None
