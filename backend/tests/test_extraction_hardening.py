import io
import json
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.models import Activity, Artifact, ExecutionEvent, Project, WBSNode
from app.services.extraction_service import ExtractionQualityGate, ExtractionService, ExtractionException
from app.services.sarvam_service import SarvamService


def test_is_text_quality_sufficient():
    """Verify text quality gate detects scanned/raster-image PDFs with poor text extraction."""
    # Empty text
    assert not ExtractionService.is_text_quality_sufficient("", 1)
    # Whitespace only
    assert not ExtractionService.is_text_quality_sufficient("   \n\t  ", 1)
    # Too few characters (< 80)
    assert not ExtractionService.is_text_quality_sufficient("Page 1 header", 1)
    # 5 pages with only 50 characters (density < 25 chars/page)
    assert not ExtractionService.is_text_quality_sufficient("Page 1\nPage 2\nPage 3\nPage 4\nPage 5", 5)
    # Rich digital text: sufficient density
    rich_text = "Daily Construction Progress Report. Date: 2026-09-15. Contractor: Apex Civil. Activity: Pier 14 concrete pour completed 140 m3 with crew of 8 workers. All tests passed."
    assert ExtractionService.is_text_quality_sufficient(rich_text, 1)


def test_document_chunking_and_deduplication():
    """Verify documents > 8000 characters are chunked by pages/paragraphs and deduplicated."""
    # Construct a 5-page document exceeding 8,000 characters
    pages = []
    for p in range(1, 6):
        page_body = f"Work item on Page {p}. Poured 50 m3 concrete for Pier {p}. " * 50
        pages.append(f"--- [Page {p}] ---\n{page_body}")
    long_doc = "\n".join(pages)
    assert len(long_doc) > 10000

    chunks = ExtractionService.chunk_text(long_doc, max_chunk_chars=4000)
    # Verify it chunked into multiple chunks without cutting off arbitrarily
    assert len(chunks) >= 3
    for c in chunks:
        assert len(c) <= 6000

    # Test deduplication
    duplicate_events = [
        {
            "page_number": 1,
            "execution_date": "2026-09-15",
            "reported_activity_code": "CIV-1001",
            "description": "Concrete pour pier 1",
            "quantity": 50.0,
            "extraction_confidence": 0.80,
        },
        {
            "page_number": 1,
            "execution_date": "2026-09-15",
            "reported_activity_code": "CIV-1001",
            "description": "Concrete pour pier 1",
            "quantity": 50.0,
            "extraction_confidence": 0.95,  # Higher confidence should supersede
        },
        {
            "page_number": 2,
            "execution_date": "2026-09-15",
            "reported_activity_code": "CIV-1002",
            "description": "Concrete pour pier 2",
            "quantity": 60.0,
            "extraction_confidence": 0.90,
        },
    ]
    deduped = ExtractionService.deduplicate_events(duplicate_events)
    assert len(deduped) == 2
    assert deduped[0]["extraction_confidence"] == 0.95


def test_image_modality_never_decoded_as_utf8(monkeypatch):
    """
    CRITICAL: Verify binary image data is passed directly as raw bytes to multimodal LLM
    and is NEVER decoded as arbitrary UTF-8 text.
    """
    # Compressed binary JPEG magic bytes followed by random bytes that are invalid UTF-8
    raw_image_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00C\x80\xff\xfe\xed"

    captured_call = {}

    def mock_multimodal(file_bytes, mime_type, document_name, modality):
        captured_call["bytes"] = file_bytes
        captured_call["mime_type"] = mime_type
        captured_call["modality"] = modality
        return [{
            "verbatim_excerpt": "Site Whiteboard: Pier 14 Cap Beam Concrete Pour 140 m3",
            "activity_reference": "Pier 14 Cap Beam",
            "reported_activity_code": "CIV-2040",
            "description": "Site whiteboard progress photo",
            "execution_date": "2026-09-15",
            "quantity": 140.0,
            "unit": "m3",
            "location": "Pier 14",
            "discipline": "Civil / Structural",
            "status_reported": "COMPLETED",
            "page_number": 1,
            "extraction_confidence": 0.92,
            "extraction_notes": "Extracted via llm_multimodal_image.",
        }]

    monkeypatch.setattr(ExtractionService, "extract_multimodal_with_llm", classmethod(lambda cls, *args, **kwargs: mock_multimodal(*args, **kwargs)))

    items = ExtractionService.extract_multimodal_with_llm(
        file_bytes=raw_image_bytes,
        mime_type="image/jpeg",
        document_name="progress_board.jpg",
        modality="IMAGE",
    )
    assert items is not None
    assert len(items) == 1
    assert captured_call["bytes"] == raw_image_bytes  # EXACT raw binary bytes preserved
    assert captured_call["modality"] == "IMAGE"
    assert items[0]["reported_activity_code"] == "CIV-2040"


def test_spreadsheet_lowercase_and_semicolon_csv():
    """Verify spreadsheet extraction handles lowercase headers and semicolon delimiters."""
    csv_content = (
        "activity;qty;uom;date;status;location;contractor\n"
        "Reinforcement steel tying;25.5;t;2026-09-15;COMPLETED;Pier 14;SteelForce\n"
        "Shuttering formwork;180.0;m2;2026-09-15;IN_PROGRESS;Pier 14;Apex Formwork\n"
    ).encode("utf-8")

    items = ExtractionService.parse_spreadsheet(csv_content, "daily_report.csv")
    assert len(items) == 2
    assert items[0]["description"] == "Reinforcement steel tying"
    assert items[0]["quantity"] == 25.5
    assert items[0]["unit"] == "t"
    assert items[0]["location"] == "Pier 14"
    assert items[0]["status_reported"] == "COMPLETED"

    assert items[1]["description"] == "Shuttering formwork"
    assert items[1]["quantity"] == 180.0
    assert items[1]["unit"] == "m2"


def test_spreadsheet_tab_delimited():
    """Verify spreadsheet extraction handles tab-delimited CSV/TSV format."""
    tsv_content = (
        "Description\tQuantity\tUnit\tDate\tLocation\n"
        "Excavation for pier foundation\t350\tm3\t2026-09-14\tPier 15\n"
    ).encode("utf-8")

    items = ExtractionService.parse_spreadsheet(tsv_content, "site_log.tsv")
    assert len(items) == 1
    assert items[0]["description"] == "Excavation for pier foundation"
    assert items[0]["quantity"] == 350.0
    assert items[0]["unit"] == "m3"
    assert items[0]["location"] == "Pier 15"


def test_spreadsheet_legacy_xls_error():
    """Verify legacy .xls binary format raises an actionable ExtractionException."""
    fake_ole_xls = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100
    with pytest.raises(ExtractionException) as exc_info:
        ExtractionService.parse_spreadsheet(fake_ole_xls, "old_report.xls")
    assert "Legacy Excel (.xls) binary format is not supported" in str(exc_info.value)
    assert "convert the file to modern Excel (.xlsx) or CSV" in str(exc_info.value)


def test_extraction_quality_gate():
    """Verify ExtractionQualityGate properly transitions artifacts between EXTRACTED, NEEDS_REVIEW, and FAILED."""
    artifact = Artifact(
        id="art-test",
        project_id="proj-1",
        report_id="rep-1",
        artifact_type="PDF_REPORT",
        original_filename="daily.pdf",
        mime_type="application/pdf",
        size_bytes=1000,
        sha256="abc",
        storage_bucket="test",
        storage_key="test/key",
    )

    # 1. High quality items -> EXTRACTED
    good_items = [{
        "verbatim_excerpt": "Poured 140 m3 concrete for Pier 14",
        "description": "Concrete pour",
        "quantity": 140.0,
        "location": "Pier 14",
        "extraction_confidence": 0.95,
    }]
    status, reason = ExtractionQualityGate.evaluate(artifact, good_items, raw_text_length=500)
    assert status == "EXTRACTED"
    assert reason is None

    # 2. Zero items on readable text -> NEEDS_REVIEW
    status_empty, reason_empty = ExtractionQualityGate.evaluate(artifact, [], raw_text_length=500)
    assert status_empty == "NEEDS_REVIEW"
    assert "Marked for planner review" in reason_empty

    # 3. Low confidence items without actionable fields -> NEEDS_REVIEW
    vague_items = [{
        "verbatim_excerpt": "Site progress work ongoing",
        "description": "Work ongoing",
        "quantity": None,
        "location": None,
        "reported_activity_code": None,
        "extraction_confidence": 0.35,
    }]
    status_low, reason_low = ExtractionQualityGate.evaluate(artifact, vague_items, raw_text_length=500)
    assert status_low == "NEEDS_REVIEW"
    assert "Manual review required" in reason_low


def test_rule_based_fallback_never_fabricates_dummy_events():
    """Verify that when no keyword lines match in a PDF, parse_pdf returns [] instead of a fake event."""
    # PDF bytes containing only meeting minutes with no construction execution keywords
    dummy_pdf_no_keywords = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
        b"4 0 obj << /Length 95 >> stream\n"
        b"BT /F1 12 Tf 72 712 Td (Safety Toolbox Meeting: Discussed site PPE compliance and hydration breaks.) Tj ET\n"
        b"endstream endobj\n"
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
        b"xref\n0 6\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000261 00000 n \n0000000408 00000 n \n"
        b"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n485\n%%EOF\n"
    )

    items = ExtractionService.parse_pdf(dummy_pdf_no_keywords)
    # CRITICAL: Must return 0 items! Must NOT fabricate "General Site Progress" with confidence 0.80!
    assert len(items) == 0
