# ScheduleManager Production Deployment Test Plan

**Document:** Comprehensive Deployment Testing & Validation Suite  
**Execution Context:** Staging Verification & Post-Deployment Validation  
**Success Requirement:** 100% Pass Rate Across All Critical Scenarios  

---

## 1. Pre-Deployment Validation (CI / Build Stage)

Before deploying container images to the host or registry:

| ID | Test Target | Command / Check | Expected Pass Result |
| :--- | :--- | :--- | :--- |
| **PRE-01** | Backend Test Suite | `docker exec primavera-backend pytest` | 174 passed, 0 failed in < 45s |
| **PRE-02** | Document Parser Suite | `docker exec primavera-document-parser pytest` | 16 passed, 0 failed in < 3s |
| **PRE-03** | Frontend Build & Typecheck | `docker build -t test-frontend ./frontend` | Next.js standalone build exits code 0 with 0 TypeScript errors |
| **PRE-04** | Secret Leak Audit | `git grep -E "(AIza\|minioadmin\|postgres:)" -- ':(exclude)*.example'` | Zero hardcoded API keys or passwords in tracked git files |

---

## 2. Infrastructure & Ingress Smoke Tests

Execute immediately following `docker compose -f docker-compose.prod.yml up -d`:

| ID | Test Scenario | Execution Step | Expected Validation |
| :--- | :--- | :--- | :--- |
| **SMK-01** | Ingress TLS Handshake | `curl -Iv https://schedule.yourcompany.com` | HTTP 200 OK, valid Let's Encrypt certificate |
| **SMK-02** | Backend Internal Health | `curl -f https://schedule.yourcompany.com/api/v1/health` | `{"status": "ok", "service": "backend"}` |
| **SMK-03** | Parser Service Health | `docker exec schedulemanager-backend curl -f http://document-parser:8001/health` | `{"status": "ok", "service": "document-parser"}` |
| **SMK-04** | PostgreSQL Health | `docker exec schedulemanager-postgres pg_isready -U primavera_admin -d primavera` | `accepting connections` |
| **SMK-05** | MinIO Bucket Existence | `docker exec schedulemanager-backend python -c "from app.services.minio_service import minio_service; assert minio_service.is_minio_connected()"` | Exits 0, MinIO reachable and bucket initialized |

---

## 3. End-to-End Functional Verification Tests

Execute via the production UI at `https://schedule.yourcompany.com`:

### FT-01: Schedule File Ingestion & Parsing
1. **Upload:** Import Primavera `samples/sample.xer`.
2. **Verification:**
   - Activities count matches source schedule.
   - WBS hierarchy renders with parent-child indentation.
   - Durations accurately reflect P6 calendar shift hours.
   - CPM calculates project start, finish, and critical path activities.

### FT-02: Multimodal Field Report Ingestion & Quality Gate
1. **Upload:** Upload a scanned or digital field report (PDF).
2. **Verification:**
   - File is written to MinIO under `projects/{id}/reports/{rep_id}/artifacts/{art_id}/...`.
   - `extract_artifact` executes cleanly.
   - High-confidence events transition artifact to `EXTRACTED`.
   - Extracted `ExecutionEvent` entities display physical quantity, unit (e.g. `m3`), location, and verbatim excerpt.
   - No fabricated placeholder `"General Site Progress"` is created.

### FT-03: Multi-Signal Matching & Planner Review
1. **Matching:** Trigger `Run Matching & Auto-Link`.
2. **Verification:**
   - Multi-signal algorithm scores candidate activities.
   - Ambiguous matches queue into `Planner Verification Queue`.
3. **Approval:** Planner approves progress with optional percent adjustment.
4. **Verification:**
   - Record created in `actual_progress_ledger`.
   - Activity status mutates (e.g. `NOT_STARTED` $\rightarrow$ `IN_PROGRESS`).
   - CPM recalculates schedule completion date.

### FT-04: Schedule Export Roundtrip
1. **Export:** Click `Export Updated P6 (.XER)`.
2. **Verification:**
   - Browser downloads updated `.xer` file.
   - Re-uploading the exported file into Document Parser produces an identical schedule with updated percent complete and actual dates.

---

## 4. Failure Mode & Resilience Tests

| ID | Test Scenario | Injection Action | Expected System Behavior |
| :--- | :--- | :--- | :--- |
| **FLR-01** | External AI Offline / Unconfigured | Remove `EXTRACTION_GEMINI_API_KEY`, restart backend, and upload a scanned image PDF. | Artifact transitions to `FAILED` or `NEEDS_REVIEW` with informative error message. Zero fake events created. Database does not crash. |
| **FLR-02** | Invalid / Corrupted File Upload | Upload a corrupted `.xer` or non-standard binary file. | Document parser rejects with HTTP 422 Unprocessable Content. Actionable error returned to UI. |
| **FLR-03** | Cross-Project Reassignment Attempt | Attempt to approve an event from Project A against an activity in Project B. | Backend returns HTTP 400 Bad Request with `"belongs to project"` violation. Cross-project contamination blocked. |
| **FLR-04** | Container Crash & Restart | Kill backend container (`docker kill schedulemanager-backend`). | Docker auto-restart policy restores container within 5 seconds. In-flight requests fail gracefully; subsequent requests succeed. |

---

## 5. Persistence & Recovery Tests

| ID | Test Scenario | Execution Step | Expected Verification |
| :--- | :--- | :--- | :--- |
| **PST-01** | Host Container Destruction & Re-creation | Run `docker compose -f docker-compose.prod.yml down`, then `docker compose -f docker-compose.prod.yml up -d`. | All imported schedules, field artifacts, progress ledgers, and audit logs remain intact and visible in the UI. Zero data loss. |
| **PST-02** | Database Backup & Point-in-Time Restore | Run `/opt/schedulemanager/scripts/backup.sh`, drop the test project, and restore from the generated `.sql.gz` dump. | Deleted project and all associated activities, ledger entries, and audit logs are fully restored. |
| **PST-03** | Artifact Cache Retry Persistence | Mark an artifact as `FAILED`, then invoke `POST /api/v1/artifacts/{id}/extract?force_reextract=true`. | Previous failure is cleared, extraction re-executes, and newly generated events persist without cache lockup. |
