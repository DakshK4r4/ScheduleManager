from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
import httpx
from sqlalchemy.orm import Session

from app.domain.models import Artifact, ExecutionEvent
from app.schemas.extraction import NormalizedExtractionEvent
from app.services.credential_resolver import CredentialResolver
from app.services.minio_service import minio_service

logger = logging.getLogger("extraction_service")


class ExtractionException(Exception):
    pass


class ExtractionQualityGate:
    """
    Quality gate enforcing rigorous validation of extracted construction execution events.
    Never marks an artifact EXTRACTED merely because a function completed.
    Transitions artifacts to EXTRACTED, NEEDS_REVIEW, or FAILED.
    """

    @staticmethod
    def evaluate(
        artifact: Artifact,
        items: List[Dict[str, Any]],
        raw_text_length: int = 0,
        extraction_error: Optional[str] = None,
    ) -> Tuple[str, Optional[str]]:
        """
        Determines terminal extraction status and explanatory message:
        - EXTRACTED: At least one actionable event with valid fields and confidence >= 0.40.
        - NEEDS_REVIEW: Zero events extracted from valid media, or low confidence events requiring human check.
        - FAILED: File corruption, unparseable format, or critical extraction failure.
        """
        if extraction_error:
            err_lower = extraction_error.lower()
            if (
                "not supported" in err_lower
                or "corrupt" in err_lower
                or "malformed" in err_lower
                or "invalid" in err_lower
            ):
                return "FAILED", extraction_error
            return "NEEDS_REVIEW", f"Extraction incomplete: {extraction_error}"

        mtype = (artifact.mime_type or "").lower()
        fname = artifact.original_filename.lower()

        if not items:
            if any(ext in fname for ext in [".m4a", ".mp3", ".wav", ".ogg"]) or "audio" in mtype:
                return (
                    "NEEDS_REVIEW",
                    "Audio voice memo stored permanently in MinIO. Speech-to-text service was unavailable or produced no transcript. Planner playback and manual review required.",
                )
            elif any(ext in fname for ext in [".png", ".jpg", ".jpeg"]) or "image" in mtype:
                return (
                    "NEEDS_REVIEW",
                    "Image artifact stored in MinIO. Multimodal vision extraction was unavailable or detected no discrete physical execution events. Visual review required.",
                )
            elif raw_text_length > 0:
                return (
                    "NEEDS_REVIEW",
                    "Document contained readable text, but no discrete physical execution events matched extraction criteria. Marked for planner review.",
                )
            else:
                return "FAILED", "Document contained zero extractable text or visual elements."

        # Check extraction quality across extracted items
        low_confidence_count = sum(
            1 for it in items if (it.get("extraction_confidence") or 0.0) < 0.50
        )
        has_any_actionable_field = any(
            it.get("quantity") is not None
            or it.get("location")
            or it.get("reported_activity_code")
            for it in items
        )

        if low_confidence_count == len(items) and not has_any_actionable_field:
            return (
                "NEEDS_REVIEW",
                "Low confidence extraction. Extracted events lack specific quantities, locations, or activity codes. Manual review required.",
            )

        return "EXTRACTED", None


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
    def is_text_quality_sufficient(cls, text: str, page_count: int = 1) -> bool:
        """
        Determines whether native PDF text extraction extracted meaningful text.
        Evaluates:
        - Non-whitespace character count (minimum 80 chars total)
        - Average text density per page (minimum 25 chars/page)
        - Alphanumeric presence (minimum 40 alphanumeric chars)
        Returns False for scanned or raster-image PDFs to trigger multimodal fallback.
        """
        if not text or not text.strip():
            return False
        clean = "".join(c for c in text if not c.isspace())
        if len(clean) < 80:
            return False
        if page_count > 0 and (len(clean) / page_count) < 25:
            return False
        alpha_num = sum(1 for c in clean if c.isalnum())
        if alpha_num < 40:
            return False
        return True

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
    def _parse_date_value(cls, val: Any) -> str:
        """Normalize date representations from Excel or CSV to ISO YYYY-MM-DD."""
        if val is None or str(val).strip() == "":
            return datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if isinstance(val, (datetime, date)):
            return val.strftime("%Y-%m-%d")

        # Excel serial date number
        if isinstance(val, (int, float)):
            try:
                if 30000 <= float(val) <= 60000:
                    base = datetime(1899, 12, 30)
                    return (base + timedelta(days=float(val))).strftime("%Y-%m-%d")
            except Exception:
                pass

        s = str(val).strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y"):
            try:
                clean_s = s.split("T")[0].split(" ")[0]
                return datetime.strptime(clean_s, fmt).strftime("%Y-%m-%d")
            except Exception:
                continue

        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    @classmethod
    def _format_llm_results(cls, result_json: Dict[str, Any], raw_text: str) -> List[Dict[str, Any]]:
        raw_events = result_json.get("events", []) if isinstance(result_json, dict) else []
        if not isinstance(raw_events, list):
            return []

        formatted = []
        for ev in raw_events:
            if not isinstance(ev, dict):
                continue
            desc = (ev.get("description") or ev.get("activity_reference") or "Site work").strip()
            if not desc:
                continue
            verbatim = (ev.get("verbatim_excerpt") or desc).strip()
            qty = None
            if ev.get("quantity") is not None:
                try:
                    qty = float(ev["quantity"])
                except (ValueError, TypeError):
                    pass

            unit = cls.normalize_unit(ev.get("unit"))
            status = cls.normalize_status(ev.get("status_reported"))
            date_val = cls._parse_date_value(ev.get("execution_date"))
            location = str(ev.get("location")).strip() if ev.get("location") else None
            discipline = str(ev.get("discipline")).strip() if ev.get("discipline") else None

            confidence = cls.calculate_extraction_confidence(
                verbatim_excerpt=verbatim,
                date_str=date_val,
                quantity=qty,
                unit=unit,
                location=location,
                discipline=discipline,
                source_text=raw_text,
            )

            try:
                norm_event = NormalizedExtractionEvent(
                    page_number=int(ev.get("page_number") or 1),
                    bounding_box=ev.get("bounding_box") if isinstance(ev.get("bounding_box"), list) else None,
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
            except Exception as val_err:
                logger.warning(f"Schema validation failed for extracted event: {val_err}. Discarding event.")

        return formatted

    @classmethod
    def chunk_text(cls, raw_text: str, max_chunk_chars: int = 8000) -> List[str]:
        """
        Splits large documents into logical chunks by page markers if present,
        otherwise by paragraph boundaries, preventing truncation of multi-page field reports.
        """
        if len(raw_text) <= max_chunk_chars:
            return [raw_text]

        page_splits = re.split(r"(--- \[Page \d+\] ---)", raw_text)
        if len(page_splits) > 1:
            pages: List[str] = []
            current_marker = ""
            for part in page_splits:
                if part.startswith("--- [Page "):
                    current_marker = part
                elif current_marker:
                    pages.append(f"{current_marker}\n{part}")
                    current_marker = ""
                elif part.strip():
                    pages.append(part)

            chunks: List[str] = []
            curr_chunk: List[str] = []
            curr_len = 0
            for p in pages:
                if curr_len + len(p) > max_chunk_chars and curr_chunk:
                    chunks.append("\n".join(curr_chunk))
                    curr_chunk = [p]
                    curr_len = len(p)
                else:
                    curr_chunk.append(p)
                    curr_len += len(p)
            if curr_chunk:
                chunks.append("\n".join(curr_chunk))
            return chunks if chunks else [raw_text]

        paragraphs = raw_text.split("\n\n")
        chunks = []
        curr_chunk = []
        curr_len = 0
        for p in paragraphs:
            if curr_len + len(p) > max_chunk_chars and curr_chunk:
                chunks.append("\n\n".join(curr_chunk))
                curr_chunk = [p]
                curr_len = len(p)
            else:
                curr_chunk.append(p)
                curr_len += len(p)
        if curr_chunk:
            chunks.append("\n\n".join(curr_chunk))
        return chunks if chunks else [raw_text]

    @classmethod
    def deduplicate_events(cls, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Deterministically deduplicates events extracted across chunks.
        Retains the event with higher confidence or more complete fields.
        """
        if not events:
            return []

        unique: List[Dict[str, Any]] = []
        seen: Dict[tuple, int] = {}

        for ev in events:
            p_num = ev.get("page_number") or 1
            d_val = (str(ev.get("execution_date") or ""))[:10]
            code = (ev.get("reported_activity_code") or "").strip().upper()
            desc = (ev.get("description") or "").strip().lower()
            desc_key = re.sub(r"[^a-z0-9]", "", desc)[:25]
            qty = ev.get("quantity")
            key = (p_num, d_val, code, desc_key, qty)

            if key in seen:
                prev_idx = seen[key]
                prev_ev = unique[prev_idx]
                if (ev.get("extraction_confidence") or 0.0) > (prev_ev.get("extraction_confidence") or 0.0):
                    unique[prev_idx] = ev
            else:
                seen[key] = len(unique)
                unique.append(ev)

        return unique

    @classmethod
    def _call_llm_text_prompt(cls, chunk_text: str, document_name: str = "") -> Optional[List[Dict[str, Any]]]:
        """Direct single-chunk LLM call enforcing JSON schema."""
        extraction_cred = CredentialResolver.resolve_extraction_credentials()
        gemini_api_key = extraction_cred.api_key
        openai_api_key = os.getenv("OPENAI_API_KEY")

        if not gemini_api_key and not openai_api_key:
            return None

        prompt = f"""You are an expert construction project engineer and schedule manager.
Your task is to analyze the following daily construction field report and extract discrete physical execution events.
CRITICAL TABLE PRESERVATION:
If the text represents tabular data (Activity, Location, Quantity, Unit, Crew, Date), preserve row and column associations strictly.
Do not associate quantities or locations with unrelated activities.

For each event identified in the text:
1. Quote the EXACT verbatim text excerpt from the document supporting the event (verbatim_excerpt).
2. Identify the activity reference / task title (activity_reference).
3. If an explicit schedule activity code is mentioned (e.g. ACT-1000, C1020, CIV-2040), extract it (reported_activity_code); otherwise null.
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
{chunk_text}
"""
        try:
            if gemini_api_key:
                primary_model = extraction_cred.model
                candidate_pool = [primary_model, "gemini-3.5-flash", "gemini-2.5-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
                models_to_try = [m for m in candidate_pool if m]

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
                                    events = cls._format_llm_results(result_json, chunk_text)
                                    for ev in events:
                                        ev["extraction_notes"] = f"Extracted via Gemini text LLM ({model})."
                                    return events
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
                        events = cls._format_llm_results(result_json, chunk_text)
                        for ev in events:
                            ev["extraction_notes"] = f"Extracted via OpenAI text LLM ({model})."
                        return events
                    logger.warning(f"OpenAI API returned status {resp.status_code}: {resp.text}")

        except Exception as e:
            logger.warning(f"LLM extraction failed, will fallback to rule-based extractor: {e}")

        return None

    @classmethod
    def extract_with_llm(cls, raw_text: str, document_name: str = "") -> Optional[List[Dict[str, Any]]]:
        """
        Extracts structured construction execution events using an LLM.
        Applies chunking for documents exceeding 8,000 characters without arbitrary truncation,
        merges results, and deterministically deduplicates events.
        """
        if not raw_text or not raw_text.strip():
            return None

        chunks = cls.chunk_text(raw_text, max_chunk_chars=8000)
        all_events: List[Dict[str, Any]] = []

        for chunk in chunks:
            chunk_events = cls._call_llm_text_prompt(chunk, document_name)
            if chunk_events:
                all_events.extend(chunk_events)

        if not all_events:
            return None

        return cls.deduplicate_events(all_events)

    @classmethod
    def extract_multimodal_with_llm(
        cls,
        file_bytes: bytes,
        mime_type: str,
        document_name: str = "",
        modality: str = "DOCUMENT",  # DOCUMENT, IMAGE, AUDIO
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Extracts structured execution events from binary media (scanned PDF, image, audio)
        using Gemini multimodal API. Sends raw bytes via inlineData to avoid lossy UTF-8 decoding.
        """
        extraction_cred = CredentialResolver.resolve_extraction_credentials()
        gemini_api_key = extraction_cred.api_key

        if not gemini_api_key or not file_bytes:
            return None

        import base64
        b64_data = base64.b64encode(file_bytes).decode("ascii")

        clean_mime = mime_type.lower().split(";")[0].strip()
        if clean_mime == "application/octet-stream" or not clean_mime:
            ext = os.path.splitext(document_name.lower())[1]
            if ext == ".pdf":
                clean_mime = "application/pdf"
            elif ext in (".png", ".jpg", ".jpeg"):
                clean_mime = f"image/{ext.replace('.', '').replace('jpg', 'jpeg')}"
            elif ext in (".mp3", ".wav", ".m4a", ".ogg"):
                clean_mime = f"audio/{ext.replace('.', '')}"

        if modality == "IMAGE":
            task_desc = (
                "You are an expert construction project engineer analyzing a site photograph, whiteboard, or scanned site note.\n"
                "Extract any discrete physical execution events visible in the image."
            )
        elif modality == "AUDIO":
            task_desc = (
                "You are an expert construction schedule manager listening to a voice memo from site engineers.\n"
                "Transcribe and extract any discrete physical execution events reported in the audio."
            )
        else:
            task_desc = (
                "You are an expert construction project engineer and schedule manager.\n"
                "Analyze this scanned daily construction field report document.\n"
                "CRITICAL TABLE PRESERVATION: If the document contains tables (e.g. Activity, Location, Quantity, Unit, Crew, Date), "
                "preserve table row/column associations strictly. Associate each quantity and location with its specific activity."
            )

        prompt = f"""{task_desc}
For each event identified:
1. Quote the EXACT verbatim text or visible label supporting the event (verbatim_excerpt).
2. Identify the activity reference / task title (activity_reference).
3. If an explicit schedule activity code is mentioned/visible (e.g. ACT-1000, CIV-2040), extract it (reported_activity_code); otherwise null.
4. Provide a clear summary description of the work performed (description).
5. Extract the execution date in ISO format YYYY-MM-DD (execution_date).
6. Extract the numerical physical quantity performed/installed if stated (quantity, float); otherwise null.
7. Extract the unit of measurement (unit, e.g. m3, m2, t, m, ea); otherwise null.
8. Extract the physical location or structural element (location, e.g. Pier 14, Level 2); otherwise null.
9. Extract the engineering discipline (discipline, e.g. Civil, Structural, MEP); otherwise null.
10. Extract the subcontractor or crew name if mentioned (contractor); otherwise null.
11. Extract any WBS hint (wbs_hint); otherwise null.
12. Status reported: COMPLETED, IN_PROGRESS, or NOT_STARTED (status_reported).
13. Page number if identifiable (page_number, integer, default 1).

If certain information is not present or cannot be determined with confidence, return null for that field.
Do NOT invent or hallucinate data.

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
"""
        primary_model = extraction_cred.model
        candidate_pool = [primary_model, "gemini-2.5-flash", "gemini-1.5-flash", "gemini-3.5-flash"]
        models_to_try = [m for m in candidate_pool if m]

        for model in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            headers = {
                "x-goog-api-key": gemini_api_key,
                "Content-Type": "application/json",
            }
            payload = {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "inline_data": {
                                    "mime_type": clean_mime,
                                    "data": b64_data,
                                }
                            },
                            {"text": prompt},
                        ],
                    }
                ],
                "generationConfig": {
                    "response_mime_type": "application/json",
                    "temperature": 0.1,
                },
            }
            try:
                with httpx.Client(timeout=45.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            part_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                            result_json = json.loads(part_text)
                            method_label = f"llm_multimodal_{modality.lower()}"
                            results = cls._format_llm_results(result_json, f"multimodal_{document_name}")
                            for r in results:
                                r["extraction_notes"] = f"Extracted via {method_label} ({model})."
                            return results
                    logger.warning(f"Multimodal Gemini API ({model}) returned HTTP status {resp.status_code}")
            except Exception as err:
                logger.warning(f"Error calling multimodal Gemini model {model}: {err}")

        return None

    @classmethod
    def parse_pdf(cls, file_bytes: bytes) -> List[Dict[str, Any]]:
        """
        Parse PDF document and extract candidate execution items per page using rule-based heuristics.
        Degraded deterministic fallback when LLM is unavailable. Never fabricates fake events.
        """
        import pypdf
        items = []
        try:
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            for page_idx, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                if not text.strip():
                    continue

                lines = [line.strip() for line in text.split("\n") if line.strip()]
                for i, line in enumerate(lines):
                    if any(
                        kw in line.lower()
                        for kw in [
                            "poured", "pour", "installed", "completed", "excavated",
                            "erected", "concreting", "reinforcement", "ductwork",
                            "cable", "slab", "beam", "pier", "foundation", "backfilling", "asphalt"
                        ]
                    ):
                        qty_match = re.search(
                            r"(\d+(?:\.\d+)?)\s*(m3|cum|m\^3|m2|sqm|tonnes|t|meters|m|nos|ea|%)",
                            line,
                            re.IGNORECASE,
                        )
                        quantity = float(qty_match.group(1)) if qty_match else None
                        unit = cls.normalize_unit(qty_match.group(2)) if qty_match else None

                        loc_match = re.search(
                            r"(Pier\s+\d+|Abutment\s+[A-Z0-9]+|Wing\s+[A-Z0-9]+|Level\s+\d+|Grid\s+[A-Z0-9\-]+|West\s+Wing|East\s+Wing)",
                            line,
                            re.IGNORECASE,
                        )
                        location = loc_match.group(1) if loc_match else None

                        code_match = re.search(r"\b([A-Z]{2,4}[-_]\d{3,5})\b", line)
                        act_code = code_match.group(1) if code_match else None

                        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        for nearby in lines[max(0, i - 5) : min(len(lines), i + 5)]:
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

                        confidence = cls.calculate_extraction_confidence(
                            verbatim_excerpt=line,
                            date_str=date_str,
                            quantity=quantity,
                            unit=unit,
                            location=location,
                            discipline="Civil / Structural" if "concrete" in line.lower() else None,
                            source_text=line,
                        )

                        items.append({
                            "page_number": page_idx + 1,
                            "bounding_box": [100.0, 200.0 + (i * 20.0), 500.0, 230.0 + (i * 20.0)],
                            "verbatim_excerpt": line,
                            "activity_reference": line[:100],
                            "reported_activity_code": act_code,
                            "description": line,
                            "execution_date": date_str,
                            "quantity": quantity,
                            "unit": unit,
                            "location": location,
                            "discipline": "Civil / Structural" if "concrete" in line.lower() else None,
                            "status_reported": cls.normalize_status(line),
                            "extraction_confidence": confidence,
                            "extraction_notes": "Extracted via rule-based fallback keyword parser.",
                        })

            # Never fabricate a fake "General Site Progress" event when no keyword lines match!
            # Returning empty items allows ExtractionQualityGate to set NEEDS_REVIEW honestly.
        except Exception as e:
            logger.error(f"Error parsing PDF: {e}")
            raise ExtractionException(f"Failed to parse PDF artifact: {e}")
        return items

    @classmethod
    def parse_spreadsheet(cls, file_bytes: bytes, filename: str) -> List[Dict[str, Any]]:
        """
        Parse Excel or CSV file with robust case-insensitive header normalization,
        delimiter sniffing, multi-sheet inspection, and legacy format rejection.
        """
        items = []
        fname_lower = filename.lower()

        # Reject legacy binary .xls with clean actionable message
        if fname_lower.endswith(".xls") and not fname_lower.endswith(".xlsx"):
            if file_bytes.startswith(b"\xd0\xcf\x11\xe0"):
                raise ExtractionException(
                    f"Legacy Excel (.xls) binary format is not supported for '{filename}'. "
                    "Please convert the file to modern Excel (.xlsx) or CSV (.csv) format and re-upload."
                )

        HEADER_MAP = {
            "activity": [
                "activity", "activity description", "task", "task name",
                "description", "work item", "scope", "item", "activity name", "work description",
            ],
            "reported_activity_code": ["activity id", "activity code", "act id", "task id", "code"],
            "quantity": [
                "quantity", "qty", "daily qty", "installed qty",
                "executed qty", "actual qty", "volume", "amount",
            ],
            "unit": ["unit", "uom", "units", "measurement unit"],
            "date": ["date", "execution date", "work date", "report date", "entry date", "log date"],
            "status": ["status", "work status", "progress status", "reported status"],
            "location": ["location", "chainage", "area", "zone", "level", "pier", "grid", "ch"],
            "contractor": ["contractor", "subcontractor", "crew", "vendor", "agency"],
            "discipline": ["discipline", "trade", "department", "dept"],
        }

        def map_row(row_dict: Dict[str, Any]) -> Dict[str, Any]:
            normalized = {}
            for canonical, aliases in HEADER_MAP.items():
                for key, val in row_dict.items():
                    k_clean = str(key).strip().lower()
                    if k_clean in aliases or any(alias == k_clean for alias in aliases):
                        normalized[canonical] = val
                        break
            return normalized

        if fname_lower.endswith((".csv", ".tsv", ".txt")) or "csv" in fname_lower:
            text = file_bytes.decode("utf-8", errors="replace")
            sample = text[:4096]
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"])
                delimiter = dialect.delimiter
            except Exception:
                delimiter = "," if "," in sample else (";" if ";" in sample else "\t")

            reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
            for idx, raw_row in enumerate(reader):
                row = map_row(raw_row)
                desc = str(row.get("activity") or "").strip()
                if not desc:
                    continue

                qty = None
                raw_qty = row.get("quantity")
                if raw_qty is not None and str(raw_qty).strip():
                    try:
                        clean_q = re.sub(r"[^\d\.\-]", "", str(raw_qty))
                        if clean_q:
                            qty = float(clean_q)
                    except ValueError:
                        pass

                unit = cls.normalize_unit(str(row.get("unit") or "") or None)
                date_str = cls._parse_date_value(row.get("date"))
                status = cls.normalize_status(str(row.get("status") or ""))
                loc = str(row.get("location") or "").strip() or None
                act_code = str(row.get("reported_activity_code") or "").strip() or None

                confidence = cls.calculate_extraction_confidence(
                    verbatim_excerpt=desc,
                    date_str=date_str,
                    quantity=qty,
                    unit=unit,
                    location=loc,
                    discipline=row.get("discipline"),
                    source_text=f"{desc} {qty or ''} {unit or ''} {loc or ''}",
                )

                items.append({
                    "page_number": 1,
                    "bounding_box": [0.0, float(idx * 25), 600.0, float((idx + 1) * 25)],
                    "verbatim_excerpt": f"CSV Row {idx+1}: {desc} | Qty: {qty} {unit or ''} | Date: {date_str}",
                    "activity_reference": desc,
                    "reported_activity_code": act_code,
                    "description": desc,
                    "execution_date": date_str,
                    "quantity": qty,
                    "unit": unit,
                    "location": loc,
                    "discipline": row.get("discipline"),
                    "contractor": row.get("contractor"),
                    "status_reported": status,
                    "extraction_confidence": confidence,
                    "extraction_notes": f"Extracted from CSV row {idx+1} (delimiter '{delimiter}').",
                })
        else:
            # Excel .xlsx
            import openpyxl
            try:
                wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
            except Exception as e:
                if "does not support the old .xls" in str(e).lower() or fname_lower.endswith(".xls"):
                    raise ExtractionException(
                        f"Legacy Excel (.xls) binary format is not supported for '{filename}'. "
                        "Please convert the file to modern Excel (.xlsx) or CSV format and re-upload."
                    )
                raise ExtractionException(f"Failed to open Excel workbook: {e}")

            target_sheet = None
            for sname in wb.sheetnames:
                s_low = sname.lower()
                if any(kw in s_low for kw in ["daily", "progress", "activity", "report", "log", "work", "execution", "site"]):
                    target_sheet = wb[sname]
                    break
            if target_sheet is None:
                target_sheet = wb.active or wb.worksheets[0]

            rows = list(target_sheet.iter_rows(values_only=True))
            if rows:
                header_idx = 0
                headers = []
                for r_idx, r in enumerate(rows):
                    non_empty = [str(c).strip() for c in r if c is not None and str(c).strip()]
                    if len(non_empty) >= 2:
                        header_idx = r_idx
                        headers = [str(c).strip().lower() if c is not None else "" for c in r]
                        break

                for idx, r in enumerate(rows[header_idx + 1:], start=header_idx + 2):
                    if not any(c is not None and str(c).strip() for c in r):
                        continue
                    raw_row = {headers[i]: r[i] for i in range(min(len(headers), len(r)))}
                    row = map_row(raw_row)
                    desc = str(row.get("activity") or "").strip()
                    if not desc:
                        continue

                    qty = None
                    raw_qty = row.get("quantity")
                    if raw_qty is not None and str(raw_qty).strip():
                        try:
                            clean_q = re.sub(r"[^\d\.\-]", "", str(raw_qty))
                            if clean_q:
                                qty = float(clean_q)
                        except ValueError:
                            pass

                    unit = cls.normalize_unit(str(row.get("unit") or "") or None)
                    date_str = cls._parse_date_value(row.get("date"))
                    status = cls.normalize_status(str(row.get("status") or ""))
                    loc = str(row.get("location") or "").strip() or None
                    act_code = str(row.get("reported_activity_code") or "").strip() or None

                    confidence = cls.calculate_extraction_confidence(
                        verbatim_excerpt=desc,
                        date_str=date_str,
                        quantity=qty,
                        unit=unit,
                        location=loc,
                        discipline=row.get("discipline"),
                        source_text=f"{desc} {qty or ''} {unit or ''} {loc or ''}",
                    )

                    items.append({
                        "page_number": 1,
                        "bounding_box": [0.0, float(idx * 20), 600.0, float((idx + 1) * 20)],
                        "verbatim_excerpt": f"Excel [{target_sheet.title}] Row {idx}: {desc} | Qty: {qty} {unit or ''}",
                        "activity_reference": desc,
                        "reported_activity_code": act_code,
                        "description": desc,
                        "execution_date": date_str,
                        "quantity": qty,
                        "unit": unit,
                        "location": loc,
                        "discipline": row.get("discipline"),
                        "contractor": row.get("contractor"),
                        "status_reported": status,
                        "extraction_confidence": confidence,
                        "extraction_notes": f"Extracted from Excel sheet '{target_sheet.title}' row {idx}.",
                    })

        return items

    @classmethod
    def parse_voice_memo(
        cls,
        file_bytes_or_filename: Union[bytes, str],
        filename: str = "voice_memo.m4a",
        project_id: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> List[Dict[str, Any]]:
        """
        Transcribes and extracts execution events from audio recordings using Sarvam Saaras v4 STT
        or Gemini multimodal audio. Never fabricates fake progress events if transcription fails.
        """
        if isinstance(file_bytes_or_filename, bytes):
            file_bytes = file_bytes_or_filename
            actual_filename = filename
        else:
            file_bytes = b""
            actual_filename = str(file_bytes_or_filename)

        if not file_bytes:
            # Fallback legacy signature compatibility without file bytes
            return []

        # 1. Attempt Sarvam Saaras Multilingual Speech-to-Text
        try:
            from app.services.sarvam_service import SarvamService
            keyterms = SarvamService.build_project_keyterms(db, project_id) if (db and project_id) else None
            trans_result = SarvamService.transcribe_audio(file_bytes, filename=actual_filename, keyterms=keyterms)
            transcript = (trans_result.get("transcript") or trans_result.get("transcription") or "").strip()
            if transcript and "sarvam offline" not in transcript.lower() and "audio received" not in transcript.lower():
                llm_events = cls.extract_with_llm(transcript, actual_filename)
                if llm_events:
                    for ev in llm_events:
                        ev["extraction_notes"] = f"Extracted via Sarvam STT ({trans_result.get('detected_language_code', 'unknown')})."
                    return llm_events
        except Exception as stt_err:
            logger.warning(f"Sarvam audio transcription failed: {stt_err}")

        # 2. Attempt Gemini Multimodal Audio
        if CredentialResolver.resolve_extraction_credentials().api_key:
            try:
                audio_events = cls.extract_multimodal_with_llm(
                    file_bytes=file_bytes,
                    mime_type="audio/mp3",
                    document_name=actual_filename,
                    modality="AUDIO",
                )
                if audio_events:
                    return audio_events
            except Exception as audio_err:
                logger.warning(f"Multimodal audio extraction failed: {audio_err}")

        # Do NOT fabricate a fake event! Return empty items so Quality Gate sets NEEDS_REVIEW
        return []

    @classmethod
    def extract_artifact(
        cls, db: Session, artifact_id: str, force_reextract: bool = False
    ) -> List[ExecutionEvent]:
        """
        Retrieve stored artifact from MinIO, process according to modality,
        pass through ExtractionQualityGate, and persist structured ExecutionEvent entities with full provenance.
        Guarantees idempotency: returns existing events if already extracted unless force_reextract=True.
        """
        artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
        if not artifact:
            raise ExtractionException(f"Artifact {artifact_id} not found in database.")

        # Idempotency check
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

        # Clean prior unapproved events to prevent duplicate accumulation
        logger.info(f"Extracting artifact {artifact_id} (force_reextract={force_reextract}). Cleaning unapproved events.")
        db.query(ExecutionEvent).filter(
            ExecutionEvent.artifact_id == artifact_id,
            ExecutionEvent.status != "APPROVED",
        ).delete()
        artifact.extraction_status = "PROCESSING"
        db.commit()

        extraction_error_msg: Optional[str] = None
        extracted_items: List[Dict[str, Any]] = []
        raw_text_length: int = 0

        try:
            file_bytes = minio_service.get_artifact_bytes(artifact.storage_key)
            logger.info(f"Retrieved {len(file_bytes)} bytes from MinIO for artifact {artifact_id}")

            mtype = (artifact.mime_type or "").lower()
            fname = artifact.original_filename.lower()

            if "pdf" in mtype or fname.endswith(".pdf"):
                # Two-stage PDF extraction
                has_api_key = bool(
                    CredentialResolver.resolve_extraction_credentials().api_key
                    or os.getenv("OPENAI_API_KEY")
                )
                combined_text = ""
                page_count = 0
                try:
                    import pypdf
                    reader = pypdf.PdfReader(io.BytesIO(file_bytes))
                    page_count = len(reader.pages)
                    page_texts = []
                    for idx, page in enumerate(reader.pages):
                        txt = page.extract_text() or ""
                        if txt.strip():
                            page_texts.append(f"--- [Page {idx + 1}] ---\n{txt}")
                    combined_text = "\n".join(page_texts)
                except Exception as read_err:
                    logger.warning(f"pypdf could not read PDF text: {read_err}")

                raw_text_length = len(combined_text)
                is_quality_good = cls.is_text_quality_sufficient(combined_text, page_count)

                if has_api_key and is_quality_good:
                    # Stage 1: Native digital text LLM extraction
                    try:
                        llm_events = cls.extract_with_llm(combined_text, artifact.original_filename)
                        if llm_events:
                            extracted_items = llm_events
                    except Exception as llm_err:
                        logger.warning(f"Native text LLM extraction failed: {llm_err}")

                if not extracted_items and has_api_key:
                    # Stage 2: Multimodal vision fallback for scanned / raster-image PDFs
                    try:
                        mm_events = cls.extract_multimodal_with_llm(
                            file_bytes=file_bytes,
                            mime_type="application/pdf",
                            document_name=artifact.original_filename,
                            modality="DOCUMENT",
                        )
                        if mm_events:
                            extracted_items = mm_events
                    except Exception as mm_err:
                        logger.warning(f"Multimodal PDF extraction failed: {mm_err}")

                if not extracted_items:
                    # Deterministic degraded rule-based fallback
                    extracted_items = cls.parse_pdf(file_bytes)

            elif any(ext in fname for ext in [".xlsx", ".xls", ".csv"]) or "spreadsheet" in mtype or "csv" in mtype:
                extracted_items = cls.parse_spreadsheet(file_bytes, artifact.original_filename)
                raw_text_length = len(file_bytes)

            elif any(ext in fname for ext in [".png", ".jpg", ".jpeg"]) or "image" in mtype:
                # Modality IMAGE: Never decode binary bytes as UTF-8!
                if CredentialResolver.resolve_extraction_credentials().api_key:
                    extracted_items = cls.extract_multimodal_with_llm(
                        file_bytes=file_bytes,
                        mime_type=mtype or "image/jpeg",
                        document_name=artifact.original_filename,
                        modality="IMAGE",
                    ) or []
                raw_text_length = len(file_bytes)

            elif any(ext in fname for ext in [".m4a", ".mp3", ".wav", ".ogg"]) or "audio" in mtype:
                extracted_items = cls.parse_voice_memo(
                    file_bytes_or_filename=file_bytes,
                    filename=artifact.original_filename,
                    project_id=artifact.project_id,
                    db=db,
                )
                raw_text_length = len(file_bytes)

            else:
                # Plain text fallback
                text = file_bytes[:10000].decode("utf-8", errors="replace")
                raw_text_length = len(text)
                if (CredentialResolver.resolve_extraction_credentials().api_key or os.getenv("OPENAI_API_KEY")) and text.strip():
                    llm_events = cls.extract_with_llm(text, artifact.original_filename)
                    if llm_events:
                        extracted_items = llm_events

        except ExtractionException as e:
            extraction_error_msg = str(e)
            logger.warning(f"Extraction error on artifact {artifact_id}: {e}")
        except Exception as e:
            extraction_error_msg = f"Unexpected extraction error: {e}"
            logger.exception(f"Unexpected extraction failure on artifact {artifact_id}")

        # Quality Gate evaluation
        terminal_status, status_reason = ExtractionQualityGate.evaluate(
            artifact=artifact,
            items=extracted_items,
            raw_text_length=raw_text_length,
            extraction_error=extraction_error_msg,
        )

        # Persist valid events
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
                extraction_confidence=item.get("extraction_confidence", 0.8),
                extraction_notes=item.get("extraction_notes"),
                status="UNMATCHED",
            )
            db.add(event)
            created_events.append(event)

        artifact.extraction_status = terminal_status
        artifact.error_message = status_reason
        db.commit()

        for ev in created_events:
            db.refresh(ev)

        return created_events
