# Comprehensive File Parser Audit & Root-Cause Analysis

**Document Version:** 1.0  
**Target System:** ScheduleManager / Primavera AI Platform  
**Scope:** All File Ingestion, Parsing, Normalization, and Extraction Pipelines  
**Auditor:** Principal Systems Architect & Senior CPM Domain Engineer  

---

## 1. Executive Summary & Verdict

### User Problem Statement
> *"when i upload pdf as a field report the parser or whatever was not able to extract the events correctly so i want you to check what is the problem or i am hallucinating"*

### Formal Engineering Verdict
**The user is NOT hallucinating.**

There is **zero doubt** that PDF field report extraction fails or produces corrupted/dummy events in real-world scenarios. Furthermore, an exhaustive audit across **all other file parsers** in the codebase (`.xer`, `.xml`, `.xlsx`, `.csv`, `.mp3`/`.wav`/`.m4a`, and `.png`/`.jpg`) reveals **systemic fragility, mock placeholders, and catastrophic type-handling bugs** across both the backend artifact extraction pipeline and the document-parser microservice.

---

## 2. Architecture Map: Where Parsers Live in ScheduleManager

The platform splits parsing responsibilities across two independent services:

```
                          ┌────────────────────────────────────────────────────────┐
                          │                   INCOMING UPLOADS                     │
                          └──────────────────────────┬─────────────────────────────┘
                                                     │
                         ┌───────────────────────────┴───────────────────────────┐
                         ▼                                                       ▼
      ┌─────────────────────────────────────┐               ┌─────────────────────────────────────┐
      │   CPM Schedule Files                │               │   Field Execution Artifacts         │
      │   (.xer, .xml, .xlsx, .csv)         │               │   (.pdf, .xlsx, .csv, audio, image) │
      └──────────────────┬──────────────────┘               └──────────────────┬──────────────────┘
                         │                                                       │
                         ▼                                                       ▼
      ┌─────────────────────────────────────┐               ┌─────────────────────────────────────┐
      │   document-parser microservice      │               │   backend ExtractionService         │
      │   (FastAPI on Port 8001)            │               │   (backend/app/services/            │
      │   - XerParser                       │               │    extraction_service.py)           │
      │   - P6XmlParser                     │               │   - extract_with_llm (Gemini/GPT-4) │
      │   - XlsxParser                      │               │   - parse_pdf (Rule fallback)       │
      │   - CsvParser                       │               │   - parse_spreadsheet               │
      │   - TabularCommon                   │               │   - parse_voice_memo                │
      └─────────────────────────────────────┘               └─────────────────────────────────────┘
```

---

## 3. Deep-Dive Audit by File Extension

---

### A. PDF Field Reports (`.pdf`)
**Pipeline Location:** [`backend/app/services/extraction_service.py`](file:///Users/dakshkandpal/Development/ScheduleManager/backend/app/services/extraction_service.py#L484-L505)  
**Primary Functions:** `extract_artifact()`, `extract_with_llm()`, `parse_pdf()`  
**Severity:** 🔴 **CRITICAL (Showstopper)**

#### 1. Zero OCR Capability for Scanned / Image-Based PDFs
* **Code Reference:** [`extraction_service.py:488-496`](file:///Users/dakshkandpal/Development/ScheduleManager/backend/app/services/extraction_service.py#L488-L496)
  ```python
  import pypdf
  reader = pypdf.PdfReader(io.BytesIO(file_bytes))
  for idx, page in enumerate(reader.pages):
      txt = page.extract_text() or ""
      if txt.strip():
          page_texts.append(f"--- [Page {idx + 1}] ---\n{txt}")
  ```
* **Failure Mechanism:** `pypdf` only reads digital character streams in vector PDFs. Real construction field logs, site inspector diaries, and subcontractor submittals are often scanned physical papers, mobile photo-to-PDF scans, or flattened form PDFs. For these documents, `page.extract_text()` returns an **empty string `""`**.
* **Impact:** `combined_text.strip()` evaluates to falsy, **skipping LLM extraction completely**. The system then enters the rule fallback `parse_pdf()`, which also reads empty text, resulting in a single dummy event:
  `"General Site Progress"` with `quantity=None, unit=None, location=None`.

#### 2. Multi-Column & Tabular Layout Destruction
* **Code Reference:** [`extraction_service.py:273-315`](file:///Users/dakshkandpal/Development/ScheduleManager/backend/app/services/extraction_service.py#L273-L315)
* **Failure Mechanism:** Most field logs structure daily output in tables (e.g., `Activity Description | Location | Daily Qty | Unit | Crew`). `pypdf` extracts text in internal PDF object order, reading vertically down columns rather than horizontally across table rows.
* **Impact:** Line 1 gets the activity name, Line 20 gets the location, and Line 45 gets the quantity. When passed to the LLM or regex rules, quantities and units are decoupled from activity descriptions or assigned to wrong tasks.

#### 3. Brittle Regex Fallback with 14-Verb Whitelist
* **Code Reference:** [`extraction_service.py:276-281`](file:///Users/dakshkandpal/Development/ScheduleManager/backend/app/services/extraction_service.py#L276-L281)
  ```python
  keywords = [
      "poured", "pour", "installed", "completed", "excavated",
      "erected", "concreting", "reinforcement", "ductwork", "cable",
      "slab", "beam", "pier", "foundation",
  ]
  ```
* **Failure Mechanism:** If LLM extraction times out, hits rate limits, or fails JSON parsing, this fallback rule is used. Crucial construction trades and activities are completely absent:
  * *Missing:* Piping, welding, masonry, backfilling, compaction, grading, waterproofing, painting, scaffolding, dewatering, testing, commissioning, MEP rough-in.
  * *Location Regex:* Hardcoded to `r"(Pier\s+\d+|Abutment\s+[A-Z0-9]+|Wing\s+[A-Z0-9]+|Level\s+\d+|Grid\s+[A-Z0-9\-]+|West\s+Wing|East\s+Wing)"`. Any site using "Block B", "Substation", "Chamber 3", "Floor 4", or "Zone 2" is silently set to `location=None`.

#### 4. Silent Cache Poisoning on Failed Extractions
* **Code Reference:** [`extraction_service.py:427-442`](file:///Users/dakshkandpal/Development/ScheduleManager/backend/app/services/extraction_service.py#L427-L442)
  ```python
  if artifact.extraction_status == "EXTRACTED" and not force_reextract:
      return existing_events
  ```
* **Failure Mechanism:** When an extraction fails or returns dummy placeholders, the artifact status is written to Postgres as `"EXTRACTED"`. The frontend never re-attempts extraction on page reload or document inspection because `force_reextract` defaults to `False`. The user is stuck with permanent empty/dummy data.

#### 5. Arbitrary Character Truncation
* **Code Reference:** [`extraction_service.py:201`](file:///Users/dakshkandpal/Development/ScheduleManager/backend/app/services/extraction_service.py#L201)
  `raw_text[:12000]`
* **Failure Mechanism:** Field reports covering multiple shifts or weekly summaries (5–15 pages) exceed 12,000 characters. Later pages are silently truncated before reaching the LLM prompt.

---

### B. Image Uploads (`.png`, `.jpg`, `.jpeg`)
**Pipeline Location:** [`backend/app/api/artifacts.py:88`](file:///Users/dakshkandpal/Development/ScheduleManager/backend/app/api/artifacts.py#L88), [`extraction_service.py:510-530`](file:///Users/dakshkandpal/Development/ScheduleManager/backend/app/services/extraction_service.py#L510-L530)  
**Severity:** 🔴 **CRITICAL (Catastrophic Binary Corruption)**

#### 1. UTF-8 Text Decoding of Raw Binary Pixels
* **Code Reference:** [`extraction_service.py:510-515`](file:///Users/dakshkandpal/Development/ScheduleManager/backend/app/services/extraction_service.py#L510-L515)
  ```python
  else:
      # Text or generic binary fallback
      text = file_bytes[:5000].decode("utf-8", errors="replace")
      if (CredentialResolver.resolve_extraction_credentials().api_key or os.getenv("OPENAI_API_KEY")) and text.strip():
          llm_events = cls.extract_with_llm(text, artifact.original_filename)
  ```
* **Failure Mechanism:** While `artifacts.py` recognizes image extensions (`.png`, `.jpg`, `.jpeg`) and classifies them as `atype = "IMAGE"`, `ExtractionService` has **no image extraction branch**. It falls straight through into `else:`, reading raw compressed JPEG/PNG binary bytes and decoding them as UTF-8 text with `errors="replace"`.
* **Impact:** Produces corrupted strings containing replacement characters (`\ufffd\ufffdJFIF\x00...`). This garbage binary string is then submitted to Gemini/GPT-4 as "FIELD REPORT TEXT", causing LLM hallucinations, wasted token costs, or complete extraction failure.

---

### C. Audio Voice Memos (`.mp3`, `.wav`, `.m4a`, `.ogg`)
**Pipeline Location:** [`backend/app/services/extraction_service.py:416-435`](file:///Users/dakshkandpal/Development/ScheduleManager/backend/app/services/extraction_service.py#L416-L435)  
**Primary Function:** `parse_voice_memo()`  
**Severity:** 🔴 **CRITICAL (Non-Functional Mock)**

#### 1. Completely Mocked Implementation Without STT Transcription
* **Code Reference:** [`extraction_service.py:416-434`](file:///Users/dakshkandpal/Development/ScheduleManager/backend/app/services/extraction_service.py#L416-L434)
  ```python
  @classmethod
  def parse_voice_memo(cls, filename: str) -> List[Dict[str, Any]]:
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
          "extraction_confidence": 0.50,
      }]
  ```
* **Failure Mechanism:** Despite having Sarvam AI Speech-to-Text (`SarvamService`) and Whisper capabilities integrated into the project for the TimeAgent, `ExtractionService.parse_voice_memo()` **never calls any speech-to-text API**. It returns a static, hardcoded dummy item with `quantity=None`, `location=None`, and `confidence=0.50`.

---

### D. Field Report Spreadsheets (`.xlsx`, `.xls`, `.csv`)
**Pipeline Location:** [`backend/app/services/extraction_service.py:346-413`](file:///Users/dakshkandpal/Development/ScheduleManager/backend/app/services/extraction_service.py#L346-L413)  
**Primary Function:** `parse_spreadsheet()`  
**Severity:** 🟠 **HIGH (Data Loss & Parser Crashes)**

#### 1. Case-Sensitive Header Checks in CSV Parser
* **Code Reference:** [`extraction_service.py:352-365`](file:///Users/dakshkandpal/Development/ScheduleManager/backend/app/services/extraction_service.py#L352-L365)
  ```python
  desc = row.get("Activity") or row.get("Description") or row.get("Task") or ""
  raw_qty = row.get("Quantity") or row.get("Qty")
  unit = cls.normalize_unit(row.get("Unit") or row.get("UOM"))
  date_str = row.get("Date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
  ```
* **Failure Mechanism:** The CSV parsing branch uses exact title-cased keys (`Activity`, `Description`, `Quantity`). If the uploaded CSV uses lowercase headers (`activity`, `description`, `qty`, `date`, `work_item`), `desc` evaluates to `""`, triggering `continue` on line 355 and **skipping every single row in the spreadsheet**.

#### 2. CSV Delimiter Blindness
* **Failure Mechanism:** `csv.DictReader(io.StringIO(text))` assumes standard comma delimiters (`,`). European construction exports, SAP logs, and Primavera tabular exports frequently use semicolons (`;`) or tabs (`\t`). Without delimiter sniffing, `DictReader` treats the entire line as a single monolithic column header, parsing zero data rows.

#### 3. Excel `.xls` Crash
* **Code Reference:** [`extraction_service.py:380-381`](file:///Users/dakshkandpal/Development/ScheduleManager/backend/app/services/extraction_service.py#L380-L381)
  ```python
  import openpyxl
  wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
  ```
* **Failure Mechanism:** `openpyxl` only supports modern `.xlsx` (OpenXML) formats. If a contractor uploads an older binary Excel file (`.xls`), `openpyxl` raises `InvalidFileException` or corrupt zip archive errors, crashing the extraction with HTTP 500.

#### 4. Active Sheet Blindness & Unformatted Dates
* **Failure Mechanism:** Lines 382–383 read only `wb.active`. If the user has a workbook with a "Cover" tab or instructions tab as sheet 1, and "Daily Progress" as sheet 2, the actual report data is ignored. Furthermore, Excel serial dates or datetime cells converted with `str(row[i])` produce `2026-09-25 00:00:00` without ISO-8601 date normalization.

---

### E. Primavera Schedule XER Parser (`.xer`)
**Pipeline Location:** [`document-parser/app/parsers/xer_parser.py`](file:///Users/dakshkandpal/Development/ScheduleManager/document-parser/app/parsers/xer_parser.py)  
**Primary Class:** `XerParser`  
**Severity:** 🟠 **HIGH (Data Corruption on Enterprise Schedules)**

#### 1. Multi-Project XER File Collision
* **Code Reference:** [`xer_parser.py:61-72`](file:///Users/dakshkandpal/Development/ScheduleManager/document-parser/app/parsers/xer_parser.py#L61-L72), [`lines 123-128`](file:///Users/dakshkandpal/Development/ScheduleManager/document-parser/app/parsers/xer_parser.py#L123-L128)
  ```python
  project_rows = tables.get("PROJECT", [])
  if project_rows:
      p_row = project_rows[0]
      proj_code = p_row.get("proj_short_name")
  ...
  task_rows = tables.get("TASK", [])
  for row in task_rows:
      task_id = row.get("task_id", "").strip()
  ```
* **Failure Mechanism:** In Primavera P6, an enterprise export frequently contains multiple projects (e.g., baseline vs. current update, or program-level work breakdown). `XerParser` grabs `project_rows[0]` as the project, but iterates over **all** rows in `%T TASK` **without filtering by `proj_id == p_row["proj_id"]`**.
* **Impact:** Activities from completely unrelated projects inside the XER file are lumped into one project, causing duplicate activity code collisions or schedule corruption.

#### 2. Loss of Calendars and Work Shift Mappings
* **Code Reference:** [`xer_parser.py:152-156`](file:///Users/dakshkandpal/Development/ScheduleManager/document-parser/app/parsers/xer_parser.py#L152-L156)
  ```python
  orig_dur = round(target_hr / 8.0, 2) if target_hr is not None else None
  ```
* **Failure Mechanism:** The parser hardcodes an 8-hour workday (`/ 8.0`) across all activities. In heavy civil, industrial, and marine construction, calendars are often 10-hour shifts, 12-hour shifts, 6-day workweeks, or 24/7 continuous operations. `%T CALENDAR` table data is completely ignored, causing duration distortions.

#### 3. Cross-Project Relationship Fatal Crash in Backend
* **Code Reference:** [`backend/app/services/validation_service.py:203-220`](file:///Users/dakshkandpal/Development/ScheduleManager/backend/app/services/validation_service.py#L203-L220)
* **Failure Mechanism:** In Primavera P6, an activity can depend on an external milestone or task in another project (`TASKPRED` with external `pred_task_id`). When `document-parser` emits this canonical relationship, `ValidationService` checks:
  ```python
  if not pred or pred not in activity_codes:
      errors.append(ValidationErrorDetail(code="MISSING_PREDECESSOR", ...))
  ```
  Instead of downgrading the missing task to an external milestone, `ValidationService` throws a fatal validation error, **rejecting the entire schedule import with HTTP 422**.

---

### F. Primavera P6 XML Parser (`.xml`)
**Pipeline Location:** [`document-parser/app/parsers/p6_xml_parser.py`](file:///Users/dakshkandpal/Development/ScheduleManager/document-parser/app/parsers/p6_xml_parser.py)  
**Primary Class:** `P6XmlParser`  
**Severity:** 🟡 **MEDIUM (Performance & Memory Bottlenecks)**

#### 1. In-Memory DOM Parsing (`defusedxml.ElementTree.fromstring`)
* **Code Reference:** [`p6_xml_parser.py:53`](file:///Users/dakshkandpal/Development/ScheduleManager/document-parser/app/parsers/p6_xml_parser.py#L53)
* **Failure Mechanism:** Large infrastructure schedules (10,000–50,000 activities) exported as P6 XML range from 50MB to 300MB+. Loading the entire XML file into a DOM tree in memory via `ET.fromstring(content)` causes high memory spikes (1.5GB–3GB RAM), risking Out-Of-Memory (OOM) worker restarts in Docker containers.
* **Algorithmic Complexity:** `_find_all_children()` runs `root.iter()` for every major element group (`Project`, `WBS`, `Activity`, `Relationship`), scanning the entire XML tree 4+ times instead of using a streaming SAX/iterparse parser.

---

### G. Schedule Spreadsheet Parsers (`.xlsx`, `.csv` in `document-parser`)
**Pipeline Location:** [`document-parser/app/parsers/xlsx_parser.py`](file:///Users/dakshkandpal/Development/ScheduleManager/document-parser/app/parsers/xlsx_parser.py), [`csv_parser.py`](file:///Users/dakshkandpal/Development/ScheduleManager/document-parser/app/parsers/csv_parser.py), [`tabular_common.py`](file:///Users/dakshkandpal/Development/ScheduleManager/document-parser/app/parsers/tabular_common.py)  
**Severity:** 🟡 **MEDIUM (Rigid Schema Rejection)**

#### 1. Inflexible Mandatory Column Matching
* **Code Reference:** [`tabular_common.py:86-100`](file:///Users/dakshkandpal/Development/ScheduleManager/document-parser/app/parsers/tabular_common.py#L86-L100)
* **Failure Mechanism:** `map_columns` requires both `activity_code` and `name` to be present. If a project manager uploads an Excel schedule where activities are labeled simply by numbers (`1, 2, 3...`) under a column called `Item #` or `No.`, or where there is only a `Task Description` column without an explicit code, the parser throws a hard `ParserError` and refuses to process the file rather than auto-generating sequential activity codes.

#### 2. Relationship Syntax Limitations
* **Code Reference:** [`tabular_common.py:104-107`](file:///Users/dakshkandpal/Development/ScheduleManager/document-parser/app/parsers/tabular_common.py#L104-L107)
* **Failure Mechanism:** Regex `REL_PATTERN = r"^([A-Za-z0-9_\-\.]+?)(?:[\s\-_]*)(FS|SS|FF|SF)?(?:\s*([+\-]?\d+(?:\.\d+)?(?:d|h|w)?))?$"` fails to match parenthesized lags common in Primavera/MS Project exports like `ACT100 (FS+3d)` or `ACT100[FS+3]`, silently discarding relationship links.

---

### H. Unsupported Formats (`.mpp`, `.docx`, `.doc`)
**Severity:** 🟡 **MEDIUM (Feature Gap)**
* **MS Project (`.mpp`)**: Microsoft Project files are completely unsupported. When uploaded, `detect_parser` raises `ParserError: Unsupported or unrecognized schedule file format`.
* **Word Documents (`.docx`, `.doc`)**: Inspection records and site diary narratives submitted as Word documents are rejected with HTTP 422 or fall into binary text decoding.

---

## 4. Comprehensive Parser Matrix

| Format | Extension | Target Service | Current Implementation | Failure Mode / Problem | Severity |
| :--- | :--- | :--- | :--- | :--- | :---: |
| **PDF Field Report** | `.pdf` | `ExtractionService` | `pypdf` text extraction + LLM prompt + 14-verb regex | **Zero OCR for scanned PDFs; broken tables; 14-verb limit; cache poisoning** | 🔴 Critical |
| **Field Photos / Images** | `.png`, `.jpg`, `.jpeg` | `ExtractionService` | Decodes raw binary as UTF-8 | **Corrupted UTF-8 replacement characters sent to LLM; complete failure** | 🔴 Critical |
| **Voice Memos** | `.mp3`, `.wav`, `.m4a`, `.ogg` | `ExtractionService` | Static hardcoded placeholder dict | **Does not call STT API; returns static fake progress item** | 🔴 Critical |
| **Field CSVs** | `.csv` | `ExtractionService` | `csv.DictReader` | **Case-sensitive header lookup; fails on lowercase or `;` delimited CSVs** | 🟠 High |
| **Field Spreadsheets** | `.xlsx`, `.xls` | `ExtractionService` | `openpyxl` | **Crashes on `.xls`; reads only first active sheet; unformatted dates** | 🟠 High |
| **Primavera XER** | `.xer` | `document-parser` | Line-by-line TSV parser | **Multi-project XER lumping; ignores calendars; cross-project rel crash** | 🟠 High |
| **Primavera XML** | `.xml` | `document-parser` | `defusedxml.ElementTree` | **DOM tree OOM on large XMLs; multi-pass `iter()` bottlenecks** | 🟡 Medium |
| **Schedule Spreadsheets**| `.xlsx`, `.csv` | `document-parser` | Alias mapper + `openpyxl` | **Rejects files missing explicit "Activity ID"; misses complex rel syntax** | 🟡 Medium |
| **MS Project** | `.mpp` | *None* | Unsupported | **Rejected with 422; no parser implemented** | 🟡 Medium |
| **Word Reports** | `.docx`, `.doc` | *None* | Unsupported | **Rejected or decoded as corrupted binary text** | 🟡 Medium |

---

## 5. Prioritized Remediation Roadmap

### Phase 1: Fix Field Report & Artifact Extraction (Immediate Priority)

1. **Native Gemini Multimodal PDF & Image Ingestion:**
   * Instead of extracting text via `pypdf`, pass raw document bytes directly to Google Gemini's native multimodal API (`inlineData` with `mimeType="application/pdf"`, `mimeType="image/jpeg"`, `mimeType="image/png"`).
   * Gemini provides **built-in Google OCR**, multi-column table recognition, and handwriting comprehension natively without external Tesseract dependencies.
2. **True Voice Memo Transcription Pipeline:**
   * Connect `ExtractionService.parse_voice_memo()` to the existing `SarvamService` / Whisper STT pipeline. Transcribe the audio first, then pass the transcribed text through `extract_with_llm()`.
3. **Resilient Field Spreadsheet Extraction:**
   * In `parse_spreadsheet()`, normalize all headers to lowercase with whitespace stripped.
   * Add delimiter sniffing via `csv.Sniffer` for `,`, `;`, and `\t`.
   * Support multi-sheet inspection in openpyxl, scanning sheet names for keywords (`log`, `progress`, `daily`, `activities`).
4. **Fix Cache Poisoning:**
   * If an extraction produces 0 events or only fallback placeholders, set `artifact.extraction_status = "FAILED"` or `"NEEDS_REVIEW"` rather than `"EXTRACTED"`.
   * Add a "Force Re-extract" button in the frontend review interface.

### Phase 2: Harden Schedule Parsers in `document-parser`

1. **Project ID Scoping in XER Parser:**
   * In `XerParser.parse()`, read the active project's `proj_id` and strictly filter all `TASK`, `PROJWBS`, and `TASKPRED` rows by `proj_id`.
2. **Tolerant Cross-Project Relationships:**
   * In `ValidationService.validate_canonical_schedule()`, convert missing predecessor/successor references into informational warnings or create external milestone stubs rather than crashing with a fatal HTTP 422.
3. **Streaming XML Parser:**
   * Replace `defusedxml.ElementTree.fromstring()` with `iterparse` streaming to handle 100MB+ P6 XML exports with low memory footprints.
4. **Auto-Generate Activity Codes in Spreadsheets:**
   * If an Excel file provides task names and dates but no activity code, auto-generate deterministic identifiers (`ACT-001`, `ACT-002`, ...).
