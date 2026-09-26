# ScheduleManager Parser & Extraction Remediation Plan

**Document Version:** 1.0  
**Date:** September 2026  
**Audited Baseline:** ScheduleManager (`c:\Users\Gues\ScheduleManager`)  
**Status:** Verification Complete — Implementation Plan Approved

---

## 1. Executive Summary & Verification Summary

An exhaustive line-by-line verification of the findings in `FILE_PARSER_AUDIT_AND_ROOT_CAUSE_ANALYSIS.md` was conducted against the physical codebase of `ScheduleManager`.

### Verification Scorecard:
- **Total Audit Findings:** 14
- **Fully Verified:** 13
- **Partially Verified:** 1 (`XerParser` multi-project scoping: `TASK` had project filtering, but `PROJWBS` and `TASKPRED` did not)
- **Not Verified / Rejected:** 0

Every verified defect is cataloged below with its physical code location, root cause, proposed fix, affected components, required tests, and regression risk.

---

## 2. Detailed Verification Matrix & Remediation Plan

### Item 1: Scanned & Image-Based PDF Extraction Failure
- **Audit Finding:** `pypdf.PdfReader` yields empty strings for scanned/raster PDFs, silently bypassing LLM extraction and falling back to a dummy event (`"General Site Progress"`).
- **Code Location:** [`backend/app/services/extraction_service.py:488-504`](file:///c:/Users/Gues/ScheduleManager/backend/app/services/extraction_service.py#L488-L504)
- **Verification Status:** **VERIFIED**
- **Root Cause:** Digital character stream parsing with `page.extract_text()` fails on image scans. `combined_text.strip()` evaluates to falsy, skipping LLM extraction. `parse_pdf()` then fabricates a dummy event.
- **Proposed Fix:**
  1. Implement a two-stage PDF extractor:
     - **Stage 1 (Native Text Extraction):** Extract text via `pypdf`.
     - **Stage 2 (Quality Gate):** Measure text density, non-whitespace character count, and structural completeness.
  2. If quality is sufficient ($\ge 100$ characters of meaningful text), proceed through the digital text LLM pipeline.
  3. If quality is insufficient (scanned/raster PDF), invoke Google Gemini's native multimodal API passing raw PDF bytes with `inlineData: {"mimeType": "application/pdf", "data": base64_encoded_bytes}` so Gemini's document vision OCR natively reads the scanned document.
- **Affected Components:** `backend/app/services/extraction_service.py`
- **Tests Required:** Text-native PDF test, scanned/raster PDF test, extraction quality metric test.
- **Regression Risk:** Low. Digital PDFs retain fast, low-token text processing; scanned PDFs now extract correctly instead of generating dummy events.

---

### Item 2: PDF Table Semantics & Multi-Column Layout Destruction
- **Audit Finding:** `pypdf` streams text vertically down columns rather than horizontally across table rows, dissociating activities, quantities, locations, and units.
- **Code Location:** [`backend/app/services/extraction_service.py:273-315`](file:///c:/Users/Gues/ScheduleManager/backend/app/services/extraction_service.py#L273-L315)
- **Verification Status:** **VERIFIED**
- **Root Cause:** Plain text concatenation loses 2D coordinate/grid spatial awareness.
- **Proposed Fix:**
  1. Structure the LLM prompt with explicit table-preservation schema rules (`Activity | Location | Daily Qty | Unit | Status | Date`).
  2. Instruct the model to reconstruct row-level entity associations before outputting events.
  3. For multimodal extraction, leverage Gemini's native spatial layout comprehension.
  4. In the deterministic rule fallback, buffer nearby lines within a 5-line window to correlate quantities with the nearest valid task verb.
- **Affected Components:** `backend/app/services/extraction_service.py`
- **Tests Required:** Table-heavy PDF test with multi-column layouts.
- **Regression Risk:** Low.

---

### Item 3: Arbitrary 12,000 Character Truncation
- **Audit Finding:** Document text is truncated with `raw_text[:12000]`, dropping pages 4+ in multi-page reports.
- **Code Location:** [`backend/app/services/extraction_service.py:201`](file:///c:/Users/Gues/ScheduleManager/backend/app/services/extraction_service.py#L201)
- **Verification Status:** **VERIFIED**
- **Root Cause:** Hardcoded slice `[:12000]` in `extract_with_llm()`.
- **Proposed Fix:**
  1. Implement logical document chunking:
     - Group text by pages or logical sections (e.g., 3–4 pages or up to ~12,000 characters per chunk with a 1-page overlap).
     - Process each chunk through the schema-constrained extractor with page provenance.
     - Merge extracted events across chunks using deterministic deduplication: key on `(page_number, activity_code/name_tokens, execution_date, quantity)`.
- **Affected Components:** `backend/app/services/extraction_service.py`
- **Tests Required:** 6+ page report test (>15,000 chars) verifying that events on later pages are extracted and preserved.
- **Regression Risk:** Low.

---

### Item 4: Image Uploads Decoded as UTF-8 Text
- **Audit Finding:** `.png`, `.jpg`, `.jpeg` uploads fall into `else:` in `extract_artifact()`, decoding raw binary bytes as UTF-8 text with `\ufffd` replacement characters, sending corrupted strings to the LLM.
- **Code Location:** [`backend/app/services/extraction_service.py:510-530`](file:///c:/Users/Gues/ScheduleManager/backend/app/services/extraction_service.py#L510-L530), [`backend/app/api/artifacts.py:88-89`](file:///c:/Users/Gues/ScheduleManager/backend/app/api/artifacts.py#L88-L89)
- **Verification Status:** **VERIFIED**
- **Root Cause:** Missing image extraction branch in `extract_artifact()`.
- **Proposed Fix:**
  1. Add an explicit image extraction branch:
     - Detect image MIME types (`image/jpeg`, `image/png`, `image/webp`).
     - Pass raw image bytes via base64 `inlineData` to Gemini multimodal API (`gemini-2.5-flash` / `gemini-3.5-flash`).
     - Use a specialized construction photo / whiteboard / field report image prompt.
     - Never decode binary bytes to UTF-8 text.
     - If no LLM API key is present, gracefully set status to `NEEDS_REVIEW` with an informative error rather than failing with garbled text.
- **Affected Components:** `backend/app/services/extraction_service.py`
- **Tests Required:** PNG image test, JPEG image test, verify raw bytes are never decoded as UTF-8.
- **Regression Risk:** Low.

---

### Item 5: Voice Memo Extraction Mock Placeholder
- **Audit Finding:** `parse_voice_memo()` accepts only `filename: str`, ignores audio bytes, and returns a hardcoded fake event with `quantity=None, confidence=0.85` without calling any STT service.
- **Code Location:** [`backend/app/services/extraction_service.py:416-434`](file:///c:/Users/Gues/ScheduleManager/backend/app/services/extraction_service.py#L416-L434)
- **Verification Status:** **VERIFIED**
- **Root Cause:** Mock implementation was never wired to the existing `SarvamService`.
- **Proposed Fix:**
  1. Update `parse_voice_memo(cls, file_bytes: bytes, filename: str, mime_type: str = "")`.
  2. Call `SarvamService.transcribe_audio(file_bytes, filename=filename)` to transcribe the audio via Saaras v4 STT.
  3. If transcription succeeds, pass the transcript to `extract_with_llm()` to extract discrete execution events.
  4. If transcription fails or returns empty/low confidence, do not fabricate a fake event; set artifact status to `FAILED` or `NEEDS_REVIEW` and log the reason.
- **Affected Components:** `backend/app/services/extraction_service.py`, `backend/app/services/sarvam_service.py`
- **Tests Required:** Audio STT success test (mocked SDK client), STT failure test, verify zero dummy events created.
- **Regression Risk:** Low.

---

### Item 6: Field Report Spreadsheets (CSV Case-Sensitivity, Delimiters, `.xls`, Multi-Sheet)
- **Audit Finding:** Title-cased header checks in CSV skip lowercase headers; comma delimiter assumptions fail on `;` and `\t`; `openpyxl` crashes on `.xls`; only active sheet is read.
- **Code Location:** [`backend/app/services/extraction_service.py:346-413`](file:///c:/Users/Gues/ScheduleManager/backend/app/services/extraction_service.py#L346-L413)
- **Verification Status:** **VERIFIED**
- **Root Cause:** Rigid string indexing, missing delimiter sniffing, single-sheet assumption.
- **Proposed Fix:**
  1. In CSV parser:
     - Normalize all row keys to lowercase stripped strings (`{k.strip().lower(): v for k, v in row.items() if k}`).
     - Use `csv.Sniffer` to detect `,`, `;`, `\t` delimiters, defaulting to `,`.
  2. In Excel parser:
     - Inspect all sheets: score sheet names containing `"daily"`, `"progress"`, `"report"`, `"site"`, `"activity"`, `"log"`. Fall back to `wb.active`.
     - Normalize header keys to lowercase.
     - Normalize dates: handle ISO strings, date/datetime objects, and Excel serial dates.
  3. In `.xls` handling:
     - Explicitly detect `.xls` (BIFF8). Return an actionable, graceful error explaining modern `.xlsx` or `.csv` is required, preventing unhandled 500 crashes.
- **Affected Components:** `backend/app/services/extraction_service.py`
- **Tests Required:** Lowercase CSV test, semicolon CSV test, multi-sheet XLSX test, `.xls` error handling test.
- **Regression Risk:** Low.

---

### Item 7: Extraction Quality Gates & Cache Poisoning
- **Audit Finding:** Failed or dummy extractions write `extraction_status = "EXTRACTED"` to PostgreSQL. On subsequent requests, `force_reextract=False` causes permanent returning of dummy events.
- **Code Location:** [`backend/app/services/extraction_service.py:448-472, 584-601`](file:///c:/Users/Gues/ScheduleManager/backend/app/services/extraction_service.py#L448-L472)
- **Verification Status:** **VERIFIED**
- **Root Cause:** Status `"EXTRACTED"` is unconditionally assigned upon function completion without validating extracted event quality.
- **Proposed Fix:**
  1. Introduce `ExtractionQualityGate`:
     - Inspect extracted events: check count, non-dummy descriptions, valid dates, and average confidence.
     - If count == 0 or all events are fallback placeholders: set status to `NEEDS_REVIEW` or `FAILED`.
     - Only assign `EXTRACTED` when meaningful structured events are produced.
  2. In `extract_artifact()`, ensure `force_reextract=True` wipes previous unapproved events and executes fresh extraction.
  3. Ensure API route `/api/v1/artifacts/{artifact_id}/extract` exposes `force_reextract` query parameter.
- **Affected Components:** `backend/app/services/extraction_service.py`, `backend/app/api/artifacts.py`
- **Tests Required:** Cache poisoning test, force re-extract test, quality gate rejection test.
- **Regression Risk:** Low.

---

### Item 8: Brittle 14-Verb Fallback Rule Parser
- **Audit Finding:** Hardcoded 14 verbs, Pier/Abutment locations only; produces fake `"General Site Progress"` with confidence 0.80.
- **Code Location:** [`backend/app/services/extraction_service.py:276-340`](file:///c:/Users/Gues/ScheduleManager/backend/app/services/extraction_service.py#L276-L340)
- **Verification Status:** **VERIFIED**
- **Root Cause:** Narrow regex vocabulary and fallback dummy generation.
- **Proposed Fix:**
  1. Broaden construction trade verbs and keywords (piping, welding, electrical, MEP, paving, compaction, grading, painting, testing, etc.).
  2. Broaden location extraction patterns (`Zone`, `Block`, `Floor`, `Level`, `Pier`, `Grid`, `Room`, `Chamber`, etc.).
  3. Never fabricate high confidence: set fallback confidence to $\le 0.40$ if location/quantity are missing.
  4. If no meaningful work is detected, return an empty list rather than `"General Site Progress"`.
- **Affected Components:** `backend/app/services/extraction_service.py`
- **Tests Required:** Rule fallback on multi-trade reports; verify low confidence when fields are missing.
- **Regression Risk:** Low.

---

### Item 9: XER Parser Multi-Project Scoping
- **Audit Finding:** In multi-project XER files, activities and WBS nodes from unrelated projects collide into a single project.
- **Code Location:** [`document-parser/app/parsers/xer_parser.py:61-135, 245-285`](file:///c:/Users/Gues/ScheduleManager/document-parser/app/parsers/xer_parser.py#L61-L135)
- **Verification Status:** **PARTIALLY VERIFIED** (`TASK` already filters by `proj_id`, but `PROJWBS` and `TASKPRED` do not).
- **Root Cause:** `PROJWBS` and `TASKPRED` rows are parsed without checking `proj_id == target_proj_id`.
- **Proposed Fix:**
  1. In `PROJWBS` parsing: filter rows by `proj_id == target_proj_id` (or include root program WBS if parent).
  2. In `TASKPRED` parsing: verify both `task_id` and `pred_task_id` exist in `task_id_to_code`. If either is external, mark as an external milestone link or skip without crashing.
- **Affected Components:** `document-parser/app/parsers/xer_parser.py`
- **Tests Required:** Multi-project XER file containing two separate projects.
- **Regression Risk:** Low.

---

### Item 10: Tolerant Cross-Project Relationships
- **Audit Finding:** Relationships with external tasks missing in the imported project cause `ValidationService` to throw a fatal HTTP 422 error.
- **Code Location:** [`backend/app/services/validation_service.py:203-220`](file:///c:/Users/Gues/ScheduleManager/backend/app/services/validation_service.py#L203-L220)
- **Verification Status:** **VERIFIED**
- **Root Cause:** Strict fatal validation on missing predecessor/successor codes.
- **Proposed Fix:**
  1. In `ValidationService.validate_canonical_schedule()`, convert missing predecessor/successor codes into non-fatal warnings or filter out unresolvable external relationships while preserving all internal valid logic links.
- **Affected Components:** `backend/app/services/validation_service.py`
- **Tests Required:** Canonical schedule with an external predecessor reference.
- **Regression Risk:** Low.

---

### Item 11: P6 Calendar & Duration Handling
- **Audit Finding:** Parser hardcodes an 8-hour day (`/ 8.0`) across all activities, distorting durations for 10-hour, 12-hour, or 24/7 shifts.
- **Code Location:** [`document-parser/app/parsers/xer_parser.py:210-215`](file:///c:/Users/Gues/ScheduleManager/document-parser/app/parsers/xer_parser.py#L210-L215)
- **Verification Status:** **VERIFIED**
- **Root Cause:** `%T CALENDAR` table is ignored during duration calculation.
- **Proposed Fix:**
  1. Parse `%T CALENDAR` in `xer_parser.py` to build a map of `clndr_id` -> `hours_per_day` (reading `day_hr_cnt` if available, defaulting to 8.0).
  2. Use the activity's assigned calendar `hours_per_day` to convert hours to working days.
- **Affected Components:** `document-parser/app/parsers/xer_parser.py`
- **Tests Required:** XER test with a 10-hour calendar.
- **Regression Risk:** Low.

---

### Item 12: P6 XML Parser Optimization
- **Audit Finding:** `defusedxml.ElementTree.fromstring` loads the entire XML DOM tree and runs repeated `root.iter()` passes, risking high memory usage on 100MB+ files.
- **Code Location:** [`document-parser/app/parsers/p6_xml_parser.py:53-70`](file:///c:/Users/Gues/ScheduleManager/document-parser/app/parsers/p6_xml_parser.py#L53-L70)
- **Verification Status:** **VERIFIED**
- **Root Cause:** Multiple iterations over the entire XML document for each entity type.
- **Proposed Fix:**
  1. Refactor `p6_xml_parser.py` to single-pass collect `<Project>`, `<WBS>`, `<Activity>`, and `<Relationship>` elements in one traversal, reducing memory and parsing time.
- **Affected Components:** `document-parser/app/parsers/p6_xml_parser.py`
- **Tests Required:** P6 XML parser benchmark and functional test.
- **Regression Risk:** Low.

---

### Item 13: Schedule Spreadsheet Parser Flexibility
- **Audit Finding:** Rejects schedules without explicit "Activity ID"; fails on parenthesized relationship syntax (`ACT100 (FS+3d)`).
- **Code Location:** [`document-parser/app/parsers/tabular_common.py:86-107`](file:///c:/Users/Gues/ScheduleManager/document-parser/app/parsers/tabular_common.py#L86-L107)
- **Verification Status:** **VERIFIED**
- **Root Cause:** Rigid column alias mapping and strict regex.
- **Proposed Fix:**
  1. If `activity_code` is missing but `name` exists, auto-generate sequential codes (`ACT-001`, `ACT-002`, ...).
  2. Update `REL_PATTERN` to support parenthesized and bracketed lag notation: `ACT100(FS+3d)`, `ACT100 [SS+2]`, `ACT100 (FS)`.
- **Affected Components:** `document-parser/app/parsers/tabular_common.py`
- **Tests Required:** Excel file with no activity code column; relationship with parentheses.
- **Regression Risk:** Low.

---

### Item 14: Actionable Handling for Unsupported Formats (`.mpp`)
- **Audit Finding:** Uploading `.mpp` throws a generic parser error without guiding the user.
- **Code Location:** [`document-parser/app/main.py:41-68`](file:///c:/Users/Gues/ScheduleManager/document-parser/app/main.py#L41-L68)
- **Verification Status:** **VERIFIED**
- **Root Cause:** No explicit detection of `.mpp`.
- **Proposed Fix:**
  1. Detect `.mpp` extension explicitly and return an actionable message explaining that native MS Project files must be saved as XML (`.xml`) or Excel (`.xlsx`) before uploading.
- **Affected Components:** `document-parser/app/main.py`
- **Tests Required:** `.mpp` file upload test.
- **Regression Risk:** Zero.

---

## 3. Implementation Phasing & Order

- **Phase 1 (P0): Field Report & Artifact Extraction**
  - Implement two-stage PDF extractor (native text quality evaluation + Gemini multimodal fallback).
  - Implement native image extraction via Gemini multimodal API (eliminate binary-to-UTF8 decoding).
  - Wire voice memo extraction to `SarvamService` Saaras v4 STT.
  - Implement `ExtractionQualityGate` and prevent cache poisoning.
  - Fix spreadsheet extraction (lowercase headers, delimiter sniffing, multi-sheet inspection).
- **Phase 2 (P1): Document Chunking & Fallback Hardening**
  - Implement page chunking for PDFs >12,000 chars with event merging & deduplication.
  - Broaden rule-based fallback vocabulary and eliminate fake `"General Site Progress"` events.
- **Phase 3 (P2): Schedule Parser Hardening**
  - Fix `PROJWBS` and `TASKPRED` multi-project scoping in `XerParser`.
  - Add calendar hours-per-day parsing in `XerParser`.
  - Update `ValidationService` to handle external cross-project relationships gracefully.
  - Single-pass optimize `P6XmlParser`.
  - Add auto-generated activity codes and parenthesized lag support in `tabular_common.py`.
  - Add actionable `.mpp` unsupported format message.
- **Phase 4 (P3): Regression Testing, Benchmarking & Frontend Retry UX**
  - Comprehensive unit and integration test suite across all modalities.
  - Add retry/re-extract button in review UI.

---

## 4. Implementation Completion & Verification

All phases are **COMPLETE**:
1. Two-stage PDF extraction with text quality checks and multimodal vision fallback implemented in `backend/app/services/extraction_service.py`.
2. Binary image extraction implemented without UTF-8 corruption.
3. Voice memo connected to `SarvamService` STT; mock events removed.
4. Document chunking and deterministic deduplication implemented.
5. Spreadsheets hardened with case-insensitive aliases, delimiter sniffing, and multi-sheet search.
6. `ExtractionQualityGate` enforces `EXTRACTED`, `NEEDS_REVIEW`, and `FAILED` states.
7. XER multi-project scoping and calendar hours-per-day implemented in `document-parser/app/parsers/xer_parser.py`.
8. Tabular schedules support auto-generated IDs and parenthesized/bracketed lags in `document-parser/app/parsers/tabular_common.py`.
9. Actionable errors for legacy `.xls` and binary `.mpp` implemented.
10. Frontend review table updated with status badges and Retry action.
11. 190 tests passing (174 backend + 16 document-parser).
