from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx
from sqlalchemy.orm import Session

from app.domain.models import Artifact, ExecutionEvent
from app.schemas.extraction import NormalizedExtractionEvent
from app.services.credential_resolver import CredentialResolver
from app.services.minio_service import minio_service

logger = logging.getLogger("extraction_service")


class ExtractionException(Exception):
    pass


class ExtractionService:
    @staticmethod
    def normalize_unit(raw_unit: Optional[str]) -> Optional[str]:
        if not raw_unit:
            return None
        u = raw_unit.strip().lower()
        if u in ("cum", "m^3", "cu.m", "cubic meter", "cubic meters", "m3"):
            return "m3"
        if u in ("sqm", "m^2", "sq.m", "square meter", "m2"):
            return "m2"
        if u in ("mt", "tonnes", "tons", "tonne", "t"):
            return "t"
        if u in ("mtr", "meters", "meter", "m"):
            return "m"
        if u in ("nos", "no", "ea", "each"):
            return "ea"
        return raw_unit.strip()

    @staticmethod
    def normalize_status(raw_status: Optional[str]) -> str:
        if not raw_status:
            return "IN_PROGRESS"
        s = raw_status.strip().upper()
        if any(term in s for term in ["COMPLETE", "COMPLETED", "DONE", "FINISHED", "100%"]):
            return "COMPLETED"
        if any(term in s for term in ["START", "STARTED", "IN PROGRESS", "PROGRESS", "ONGOING", "POURING"]):
            return "IN_PROGRESS"
        if any(term in s for term in ["NOT STARTED", "PENDING", "PLANNED"]):
            return "NOT_STARTED"
        return "IN_PROGRESS"

    @classmethod
    def calculate_extraction_confidence(
        cls,
        verbatim_excerpt: str,
        date_str: Optional[str],
        quantity: Optional[float],
        unit: Optional[str],
        location: Optional[str],
        discipline: Optional[str],
        source_text: str = "",
    ) -> float:
        """
        Calculates extraction confidence C_ext as formulated in Section 4.2 of specification:
        C_ext = 0.35 * S_verbatim + 0.25 * S_date + 0.20 * S_fields + 0.20 * S_ocr
        """
        if verbatim_excerpt and (verbatim_excerpt in source_text or len(verbatim_excerpt.strip()) >= 15):
            s_verbatim = 1.0
        elif verbatim_excerpt:
            s_verbatim = 0.7
        else:
            s_verbatim = 0.3

        s_date = 1.0 if date_str and len(str(date_str)) >= 8 else 0.5

        present_count = sum(
            1 for f in [quantity, unit, location, discipline] if f is not None and str(f).strip() != ""
        )
        s_fields = present_count / 4.0

        s_ocr = 1.0

        c_ext = (0.35 * s_verbatim) + (0.25 * s_date) + (0.20 * s_fields) + (0.20 * s_ocr)
        return round(max(0.1, min(1.0, c_ext)), 3)

    @classmethod
    def _format_llm_results(cls, result_json: Dict[str, Any], raw_text: str) -> List[Dict[str, Any]]:
        raw_events = result_json.get("events", []) if isinstance(result_json, dict) else []
        if not isinstance(raw_events, list):
            return []

        formatted = []
        for ev in raw_events:
            if not isinstance(ev, dict):
                continue
            desc = ev.get("description") or ev.get("activity_reference") or "Site work"
            verbatim = ev.get("verbatim_excerpt") or desc
            qty = None
            if ev.get("quantity") is not None:
                try:
                    qty = float(ev["quantity"])
                except (ValueError, TypeError):
                    pass

            unit = cls.normalize_unit(ev.get("unit"))
            status = cls.normalize_status(ev.get("status_reported"))
            date_val = ev.get("execution_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
            location = ev.get("location")
            discipline = ev.get("discipline")

            confidence = cls.calculate_extraction_confidence(
                verbatim_excerpt=verbatim,
                date_str=date_val,
                quantity=qty,
                unit=unit,
                location=location,
                discipline=discipline,
                source_text=raw_text,
            )

            norm_event = NormalizedExtractionEvent(
                page_number=int(ev.get("page_number") or 1),
                bounding_box=ev.get("bounding_box"),
                verbatim_excerpt=verbatim,
                activity_reference=ev.get("activity_reference"),
                reported_activity_code=ev.get("reported_activity_code"),
                description=desc,
                execution_date=date_val,
                quantity=qty,
                unit=unit,
                location=location,
                discipline=discipline,
                contractor=ev.get("contractor"),
                asset=ev.get("asset"),
                wbs_hint=ev.get("wbs_hint"),
                status_reported=status,
                extraction_confidence=confidence,
                extraction_notes=ev.get("extraction_notes") or "Extracted via schema-constrained LLM.",
            )
            formatted.append(norm_event.model_dump())
        return formatted

    @classmethod
    def extract_with_llm(cls, raw_text: str, document_name: str = "") -> Optional[List[Dict[str, Any]]]:
        """
        Extracts structured construction execution events using an LLM (Google Gemini or OpenAI).
        Enforces strict JSON schema as defined in EXTRACTION_MATCHING_SCHEDULE_INTEGRATION.md Section 3.2.
        Returns None if no API key is set or if the LLM call fails, allowing seamless fallback.
        """
        extraction_cred = CredentialResolver.resolve_extraction_credentials()
        gemini_api_key = extraction_cred.api_key
        openai_api_key = os.getenv("OPENAI_API_KEY")

        if not gemini_api_key and not openai_api_key:
            return None

        prompt = f"""You are an expert construction project engineer and schedule manager.
Your task is to analyze the following daily construction field report and extract discrete physical execution events.
For each event identified in the text:
1. Quote the EXACT verbatim text excerpt from the document supporting the event (verbatim_excerpt).
2. Identify the activity reference / task title (activity_reference).
3. If an explicit schedule activity code is mentioned (e.g. ACT-1000, C1020), extract it (reported_activity_code); otherwise null.
4. Provide a clear summary description of the work performed (description).
5. Extract the execution date in ISO format YYYY-MM-DD (execution_date).
6. Extract the numerical physical quantity performed/installed if stated (quantity, float); otherwise null.
7. Extract the unit of measurement (unit, e.g. m3, m2, t, m, ea); otherwise null.
8. Extract the physical location or structural element (location, e.g. Pier 14, Level 2, Grid A-C); otherwise null.
9. Extract the engineering discipline (discipline, e.g. Civil, Structural, MEP, Earthworks); otherwise null.
10. Extract the subcontractor or crew name if mentioned (contractor); otherwise null.
11. Extract any WBS or work package hint (wbs_hint); otherwise null.
12. Status reported: COMPLETED, IN_PROGRESS, or NOT_STARTED (status_reported).
13. Page number where this event occurred (page_number, integer).

Return ONLY valid JSON with this exact structure:
{{
  "events": [
    {{
      "verbatim_excerpt": "string",
      "activity_reference": "string",
      "reported_activity_code": "string or null",
      "description": "string",
      "execution_date": "YYYY-MM-DD",
      "quantity": 140.0,
      "unit": "m3",
      "location": "Pier 14",
      "discipline": "Civil / Structural",
      "contractor": "Apex Civil",
      "wbs_hint": "Substructure / Piers",
      "status_reported": "COMPLETED",
      "page_number": 1
    }}
  ]
}}

FIELD REPORT TEXT:
{raw_text[:12000]}
"""
        try:
            if gemini_api_key:
                primary_model = extraction_cred.model
                # Cascade order: primary model, then high-availability models if demand spikes occur
                candidate_pool = [primary_model, "gemini-3.5-flash", "gemini-2.5-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
                models_to_try = []
                for m in candidate_pool:
                    if m and m not in models_to_try:
                        models_to_try.append(m)

                for model in models_to_try:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                    headers = {
                        "x-goog-api-key": gemini_api_key,
                        "Content-Type": "application/json",
                    }
                    payload = {
                        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                        "generationConfig": {
                            "response_mime_type": "application/json",
                            "temperature": 0.1,
                        },
                    }
                    try:
                        with httpx.Client(timeout=30.0) as client:
                            resp = client.post(url, headers=headers, json=payload)
                            if resp.status_code == 200:
                                data = resp.json()
                                candidates = data.get("candidates", [])
                                if candidates:
                                    part_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                                    result_json = json.loads(part_text)
                                    return cls._format_llm_results(result_json, raw_text)
                            logger.warning(f"Gemini API ({model}) returned HTTP status {resp.status_code}")
                    except Exception as model_err:
                        logger.warning(f"Error calling Gemini model {model}: {model_err}")

            elif openai_api_key:
                model = os.getenv("LLM_MODEL", "gpt-4o-mini")
                url = "https://api.openai.com/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {openai_api_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You are a civil engineering schedule assistant extracting structured progress events."},
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.1,
                }
                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                        result_json = json.loads(content)
                        return cls._format_llm_results(result_json, raw_text)
                    logger.warning(f"OpenAI API returned status {resp.status_code}: {resp.text}")

        except Exception as e:
            logger.warning(f"LLM extraction failed, will fallback to rule-based extractor: {e}")

        return None

    @classmethod
    def parse_pdf(cls, file_bytes: bytes) -> List[Dict[str, Any]]:
        """Parse PDF document and extract candidate execution items per page."""
        import pypdf
        items = []
        try:
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            for page_idx, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                if not text.strip():
                    continue

                lines = [line.strip() for line in text.split("\n") if line.strip()]
                # Scan lines for daily work reports
                for i, line in enumerate(lines):
                    # Check for keywords indicating work execution
                    if any(kw in line.lower() for kw in ["poured", "pour", "installed", "completed", "excavated", "erected", "concreting", "reinforcement", "ductwork", "cable", "slab", "beam", "pier", "foundation"]):
                        # Extract quantity and unit
                        qty_match = re.search(r"(\d+(?:\.\d+)?)\s*(m3|cum|m\^3|m2|sqm|tonnes|t|meters|m|nos|ea|%)", line, re.IGNORECASE)
                        quantity = float(qty_match.group(1)) if qty_match else None
                        unit = cls.normalize_unit(qty_match.group(2)) if qty_match else None

                        # Extract location hint
                        loc_match = re.search(r"(Pier\s+\d+|Abutment\s+[A-Z0-9]+|Wing\s+[A-Z0-9]+|Level\s+\d+|Grid\s+[A-Z0-9\-]+|West\s+Wing|East\s+Wing)", line, re.IGNORECASE)
                        location = loc_match.group(1) if loc_match else None

                        # Extract date hint in nearby lines or default to today
                        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        for nearby in lines[max(0, i - 5):min(len(lines), i + 5)]:
                            d_match = re.search(r"(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})", nearby)
                            if d_match:
                                raw_d = d_match.group(1)
                                try:
                                    if "/" in raw_d:
                                        date_str = datetime.strptime(raw_d, "%d/%m/%Y").strftime("%Y-%m-%d")
                                    else:
                                        date_str = raw_d
                                    break
                                except Exception:
                                    pass

                        items.append({
                            "page_number": page_idx + 1,
                            "bounding_box": [100.0, 200.0 + (i * 20.0), 500.0, 230.0 + (i * 20.0)],
                            "verbatim_excerpt": line,
                            "description": line,
                            "execution_date": date_str,
                            "quantity": quantity,
                            "unit": unit,
                            "location": location,
                            "status_reported": cls.normalize_status(line),
                            "extraction_confidence": 0.94 if (quantity and location) else 0.85,
                        })

            # If no keyword line triggered, but text exists, construct a structured event from overall text
            if not items and len(reader.pages) > 0:
                first_text = reader.pages[0].extract_text() or "General Site Progress"
                snippet = first_text[:300].strip()
                items.append({
                    "page_number": 1,
                    "bounding_box": [50.0, 50.0, 550.0, 200.0],
                    "verbatim_excerpt": snippet,
                    "description": snippet,
                    "execution_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "quantity": None,
                    "unit": None,
                    "location": None,
                    "status_reported": "IN_PROGRESS",
                    "extraction_confidence": 0.80,
                })
        except Exception as e:
            logger.error(f"Error parsing PDF: {e}")
            raise ExtractionException(f"Failed to parse PDF artifact: {e}")
        return items

    @classmethod
    def parse_spreadsheet(cls, file_bytes: bytes, filename: str) -> List[Dict[str, Any]]:
        """Parse Excel or CSV file."""
        items = []
        if filename.lower().endswith(".csv"):
            text = file_bytes.decode("utf-8", errors="replace")
            reader = csv.DictReader(io.StringIO(text))
            for idx, row in enumerate(reader):
                desc = row.get("Activity") or row.get("Description") or row.get("Task") or ""
                if not desc:
                    continue
                qty = None
                raw_qty = row.get("Quantity") or row.get("Qty")
                if raw_qty:
                    try:
                        qty = float(raw_qty)
                    except ValueError:
                        pass
                unit = cls.normalize_unit(row.get("Unit") or row.get("UOM"))
                date_str = row.get("Date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
                status = cls.normalize_status(row.get("Status") or "IN_PROGRESS")
                items.append({
                    "page_number": 1,
                    "bounding_box": [0.0, float(idx * 25), 600.0, float((idx + 1) * 25)],
                    "verbatim_excerpt": f"Row {idx+1}: {desc} | Qty: {qty} {unit or ''} | Date: {date_str}",
                    "description": desc,
                    "execution_date": date_str,
                    "quantity": qty,
                    "unit": unit,
                    "location": row.get("Location"),
                    "status_reported": status,
                    "extraction_confidence": 0.95,
                })
        else:
            # Excel .xlsx
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
            sheet = wb.active
            rows = list(sheet.iter_rows(values_only=True))
            if rows:
                headers = [str(cell).strip().lower() if cell is not None else "" for cell in rows[0]]
                for idx, row in enumerate(rows[1:], start=2):
                    row_dict = {headers[i]: str(row[i]) if i < len(row) and row[i] is not None else "" for i in range(len(headers))}
                    desc = row_dict.get("activity") or row_dict.get("description") or row_dict.get("task") or ""
                    if not desc:
                        continue
                    qty = None
                    raw_qty = row_dict.get("quantity") or row_dict.get("qty")
                    if raw_qty:
                        try:
                            qty = float(raw_qty)
                        except ValueError:
                            pass
                    unit = cls.normalize_unit(row_dict.get("unit") or row_dict.get("uom"))
                    date_str = row_dict.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    status = cls.normalize_status(row_dict.get("status") or "IN_PROGRESS")
                    items.append({
                        "page_number": 1,
                        "bounding_box": [0.0, float(idx * 20), 600.0, float((idx + 1) * 20)],
                        "verbatim_excerpt": f"Excel Row {idx}: {desc} | Qty: {qty} {unit or ''}",
                        "description": desc,
                        "execution_date": date_str,
                        "quantity": qty,
                        "unit": unit,
                        "location": row_dict.get("location"),
                        "status_reported": status,
                        "extraction_confidence": 0.95,
                    })
        return items

    @classmethod
    def parse_voice_memo(cls, filename: str) -> List[Dict[str, Any]]:
        """
        Handle voice memo artifact:
        Format-agnostic storage preserved in MinIO for planner review and future speech-to-text.
        """
        clean_name = minio_service.sanitize_filename(filename)
        return [{
            "page_number": 1,
            "bounding_box": None,
            "verbatim_excerpt": f"Audio Voice Recording: {clean_name}. Stored permanently in MinIO for planner playback and forensic record.",
            "description": f"Verbal Site Progress Update from audio recording ({clean_name})",
            "execution_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "quantity": None,
            "unit": None,
            "location": None,
            "status_reported": "IN_PROGRESS",
            "extraction_confidence": 0.85,
            "extraction_notes": "Audio evidence stored in MinIO. Presigned playback URL available in Review workspace.",
        }]

    @classmethod
    def extract_artifact(cls, db: Session, artifact_id: str, force_reextract: bool = False) -> List[ExecutionEvent]:
        """
        Retrieve stored artifact from MinIO, process according to MIME type,
        and generate structured ExecutionEvent entities with full provenance.
        Guarantees idempotency: returns existing events if already extracted unless force_reextract=True.
        """
        artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
        if not artifact:
            raise ExtractionException(f"Artifact {artifact_id} not found in database.")

        # Idempotency check: if already extracted and not forcing re-extraction, return existing events
        if artifact.extraction_status == "EXTRACTED" and not force_reextract:
            existing_events = (
                db.query(ExecutionEvent)
                .filter(ExecutionEvent.artifact_id == artifact_id)
                .order_by(ExecutionEvent.page_number, ExecutionEvent.created_at)
                .all()
            )
            if existing_events:
                logger.info(
                    f"Artifact {artifact_id} is already extracted ({len(existing_events)} events). "
                    "Returning existing events idempotently without re-calling LLM."
                )
                return existing_events

        # If forcing re-extraction, clean up prior unapproved events to prevent duplicate accumulation
        if force_reextract:
            logger.info(f"Force re-extraction requested for artifact {artifact_id}. Cleaning up prior unapproved events.")
            db.query(ExecutionEvent).filter(
                ExecutionEvent.artifact_id == artifact_id,
                ExecutionEvent.status != "APPROVED",
            ).delete()
            db.commit()

        artifact.extraction_status = "PROCESSING"
        db.commit()

        try:
            # 1. Fetch raw binary from MinIO
            file_bytes = minio_service.get_artifact_bytes(artifact.storage_key)
            logger.info(f"Retrieved {len(file_bytes)} bytes from MinIO for artifact {artifact_id}")

            # 2. Extract items by format
            extracted_items: List[Dict[str, Any]] = []
            mtype = (artifact.mime_type or "").lower()
            fname = artifact.original_filename.lower()

            if "pdf" in mtype or fname.endswith(".pdf"):
                # Check for LLM extraction first if API key configured
                if CredentialResolver.resolve_extraction_credentials().api_key or os.getenv("OPENAI_API_KEY"):
                    try:
                        import pypdf
                        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
                        page_texts = []
                        for idx, page in enumerate(reader.pages):
                            txt = page.extract_text() or ""
                            if txt.strip():
                                page_texts.append(f"--- [Page {idx + 1}] ---\n{txt}")
                        combined_text = "\n".join(page_texts)
                        if combined_text.strip():
                            llm_events = cls.extract_with_llm(combined_text, artifact.original_filename)
                            if llm_events:
                                extracted_items = llm_events
                    except Exception as exc:
                        logger.warning(f"PDF LLM extraction failed, falling back to rule parser: {exc}")

                if not extracted_items:
                    extracted_items = cls.parse_pdf(file_bytes)
            elif any(ext in fname for ext in [".xlsx", ".xls", ".csv"]) or "spreadsheet" in mtype or "csv" in mtype:
                extracted_items = cls.parse_spreadsheet(file_bytes, artifact.original_filename)
            elif any(ext in fname for ext in [".m4a", ".mp3", ".wav", ".ogg"]) or "audio" in mtype:
                extracted_items = cls.parse_voice_memo(artifact.original_filename)
            else:
                # Text or generic binary fallback
                text = file_bytes[:5000].decode("utf-8", errors="replace")
                if (CredentialResolver.resolve_extraction_credentials().api_key or os.getenv("OPENAI_API_KEY")) and text.strip():
                    llm_events = cls.extract_with_llm(text, artifact.original_filename)
                    if llm_events:
                        extracted_items = llm_events

                if not extracted_items:
                    extracted_items = [{
                        "page_number": 1,
                        "bounding_box": None,
                        "verbatim_excerpt": text[:300],
                        "description": text[:200],
                        "execution_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        "quantity": None,
                        "unit": None,
                        "location": None,
                        "status_reported": "IN_PROGRESS",
                        "extraction_confidence": 0.80,
                    }]

            if not extracted_items:
                extracted_items = [{
                    "page_number": 1,
                    "bounding_box": None,
                    "verbatim_excerpt": f"File: {artifact.original_filename}",
                    "description": f"Site artifact: {artifact.original_filename}",
                    "execution_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "quantity": None,
                    "unit": None,
                    "location": None,
                    "status_reported": "IN_PROGRESS",
                    "extraction_confidence": 0.80,
                }]

            # 3. Create ExecutionEvent entities in DB
            created_events: List[ExecutionEvent] = []
            for item in extracted_items:
                exec_date = datetime.now(timezone.utc).replace(tzinfo=None)
                if item.get("execution_date"):
                    try:
                        exec_date = datetime.strptime(str(item["execution_date"])[:10], "%Y-%m-%d")
                    except Exception:
                        exec_date = datetime.now(timezone.utc).replace(tzinfo=None)

                event = ExecutionEvent(
                    artifact_id=artifact.id,
                    project_id=artifact.project_id,
                    source_report_id=artifact.report_id,
                    source_document_name=artifact.original_filename,
                    storage_key=artifact.storage_key,
                    file_sha256=artifact.sha256,
                    page_number=item.get("page_number", 1),
                    bounding_box=json.dumps(item["bounding_box"]) if item.get("bounding_box") else None,
                    verbatim_excerpt=item.get("verbatim_excerpt") or artifact.original_filename,
                    activity_reference=item.get("activity_reference"),
                    reported_activity_code=item.get("reported_activity_code"),
                    description=item.get("description") or artifact.original_filename,
                    execution_date=exec_date,
                    status_reported=item.get("status_reported", "IN_PROGRESS"),
                    quantity=item.get("quantity"),
                    unit=item.get("unit"),
                    location=item.get("location"),
                    discipline=item.get("discipline"),
                    contractor=item.get("contractor"),
                    asset=item.get("asset"),
                    wbs_hint=item.get("wbs_hint"),
                    extraction_confidence=item.get("extraction_confidence", 1.0),
                    extraction_notes=item.get("extraction_notes"),
                    status="UNMATCHED",
                )
                db.add(event)
                created_events.append(event)

            artifact.extraction_status = "EXTRACTED"
            artifact.error_message = None
            db.commit()

            for ev in created_events:
                db.refresh(ev)

            return created_events

        except Exception as e:
            logger.error(f"Extraction failed for artifact {artifact_id}: {e}")
            db.rollback()
            # Update status to FAILED while preserving the original artifact in MinIO!
            artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
            if artifact:
                artifact.extraction_status = "FAILED"
                artifact.error_message = str(e)
                db.commit()
            raise ExtractionException(f"Extraction failed: {e}")
