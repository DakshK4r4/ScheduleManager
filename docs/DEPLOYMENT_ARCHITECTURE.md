# ScheduleManager Deployment Architecture Specification

**Document:** Target Deployment Architecture  
**Status:** Approved for Implementation Planning  
**Audience:** DevOps Engineers, Cloud Architects, Technical Leads  

---

## 1. Architecture Overview

ScheduleManager employs a containerized service topology optimized for data locality, low operational overhead, and deterministic execution performance.

The target architecture for the current stage is **Hardened Single-Node Container Architecture with Reverse Proxy TLS Termination** (Option A), providing a clean migration path toward **AWS ECS Fargate Multi-AZ Managed Infrastructure** (Option C).

```
                                  INTERNET
                                      │
                         HTTPS (443)  │  HTTP (80) [Auto-Redirect]
                                      ▼
             ┌──────────────────────────────────────────────────┐
             │       Reverse Proxy & Edge Ingress (Caddy)       │
             │   - Automatic Let's Encrypt TLS Certificate      │
             │   - Security Headers (HSTS, CSP, X-Frame)        │
             │   - Gzip / Zstd Static Compression               │
             └───────────────────────┬──────────────────────────┘
                                     │
                    Reverse Proxy    │  HTTP (Port 3000)
                                     ▼
             ┌──────────────────────────────────────────────────┐
             │            Frontend Container (Next.js)          │
             │   - Standalone Node.js Runner                    │
             │   - Serves React UI & Static Assets              │
             │   - Proxies /api/proxy/* to backend:8000         │
             └───────────────────────┬──────────────────────────┘
                                     │
                     Docker Internal │  HTTP (Port 8000)
                     Bridge Network  │  (Not exposed to Host)
                                     ▼
             ┌──────────────────────────────────────────────────┐
             │              Backend API (FastAPI)               │
             │   - Domain Business Logic & Schemas              │
             │   - CPM Calculation & Topological Schedulers     │
             │   - Multi-Signal Match Scoring Engine            │
             │   - Synchronous Extraction Coordinator           │
             └───────┬───────────────────┬──────────────────┬───┘
                     │                   │                  │
         HTTP (8001) │      PG Wire      │          S3 API  │
         Private Net │      Port 5432    │          Port    │
                     ▼                   ▼          9000    ▼
            ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
            │ Document Parser │ │   PostgreSQL    │ │      MinIO      │
            │   (Stateless)   │ │  (Domain State) │ │ (Evidence S3)   │
            │  - XER Scoper   │ │  - Activities   │ │  - PDF Reports  │
            │  - XML Parser   │ │  - CPM Ledger   │ │  - Site Photos  │
            │  - Tabular CSV  │ │  - Audit Trail  │ │  - Voice Memos  │
            └─────────────────┘ └────────┬────────┘ └────────┬────────┘
                                         │                   │
                                         ▼                   ▼
                                 [ Docker Volume ]   [ Docker Volume ]
                                   postgres_data        minio_data
                                         │                   │
                                         └─────────┬─────────┘
                                                   │ Daily Encrypted
                                                   │ Restic / rclone
                                                   ▼
                                         [ Offsite S3 Backup ]
```

---

## 2. Service Component Matrix

| Service | Internal Hostname | Internal Port | External Exposure | Container Base | Responsibilities |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Ingress Proxy** | `caddy` / `nginx` | `80`, `443` | **Public** (Internet) | `caddy:2-alpine` | TLS termination, HTTP-to-HTTPS redirect, request routing. |
| **Frontend** | `frontend` | `3000` | Private (Behind Ingress) | `node:20-alpine` | UI rendering, client state, server-side `/api/proxy` routing. |
| **Backend API** | `backend` | `8000` | Private (Behind Frontend) | `python:3.12-slim` | Core business logic, CPM calculation, matching, database transactions. |
| **Document Parser** | `document-parser`| `8001` | Private (Internal only) | `python:3.12-slim` | Memory-efficient parsing of .XER, .XML, .XLSX, and .CSV files. |
| **Database** | `postgres` | `5432` | Private (Internal only) | `postgres:16-alpine`| Authoritative domain state, activity records, progress ledger, audit logs. |
| **Object Store** | `minio` | `9000`, `9001`| Private (Internal only) | `minio:latest` | S3-compatible raw binary artifact storage with SHA-256 deduplication. |

---

## 3. Data Flow & Communication Lifecycle

### Flow A: Schedule Import (.XER / .XML / .XLSX / .CSV)
```
1. Client drops schedule file in UI.
2. Browser POSTs multipart file to Frontend (/api/proxy/api/v1/schedules/import).
3. Next.js proxies byte stream internally to Backend (http://backend:8000/api/v1/schedules/import).
4. Backend streams file bytes across internal network to Document Parser (http://document-parser:8001/parse).
5. Document Parser:
   a. Sniffs format (XER, XML, Excel, CSV).
   b. Scopes projects and WBS nodes to target project.
   c. Normalizes task duration using CALENDAR.day_hr_cnt.
   d. Emits CanonicalSchedule JSON payload back to Backend.
6. Backend runs ValidationService:
   a. Verifies topological consistency and relationship loops.
   b. Marks external predecessors as non-fatal WARNINGs.
7. Backend opens atomic transaction in PostgreSQL:
   a. Inserts Project, WBSNodes, Activities, ActivityRelationships.
   b. Emits initial ScheduleAuditLog record.
8. Backend returns 201 Created ProjectResponse to Frontend.
```

### Flow B: Field Execution Report Ingestion & Multimodal Extraction
```
1. Site engineer uploads PDF report, site photo, or audio voice memo.
2. Browser sends file to Backend (/api/v1/projects/{id}/artifacts/upload).
3. Backend computes SHA-256 checksum and validates file size.
4. Backend uploads raw binary stream to MinIO bucket 'sih-artifacts' under:
   projects/{proj_id}/reports/{rep_id}/artifacts/{art_id}/{filename}
5. Backend commits Artifact metadata row to PostgreSQL (extraction_status="UPLOADED").
6. Frontend triggers extraction (/api/v1/artifacts/{art_id}/extract).
7. Backend ExtractionService reads file bytes from MinIO:
   a. Digital PDF: Extracts text with pypdf, checks character density (is_text_quality_sufficient).
   b. Scanned PDF / Image: Base64 encodes bytes, calls Google Gemini Multimodal Vision API.
   c. Voice Memo: Calls Sarvam AI Saaras v4 STT for Indic/English transcription.
8. Extracted items pass through schema normalization and ExtractionQualityGate.
9. Validated ExecutionEvents persist in PostgreSQL.
10. Artifact extraction_status updates to EXTRACTED or NEEDS_REVIEW.
```

### Flow C: Schedule Update & Export Roundtrip
```
1. Planner approves matched ExecutionEvent in Review Workspace.
2. Backend creates record in actual_progress_ledger.
3. ScheduleUpdateService mutates Activity actual start/finish and percent_complete.
4. CPM engine re-executes forward/backward pass, recalculating project completion and total float.
5. Mutated state logged idempotently in schedule_audit_log.
6. User clicks 'Export Updated P6 (.XER)'.
7. Backend export_p6_xer serializes current database state into valid P6 XER text stream.
8. Browser downloads updated schedule without touching third-party schedule servers.
```

---

## 4. Persistent Storage Architecture

```
Persistent Storage Mounts:
├── Host Mount: /var/lib/schedulemanager/postgres_data
│   └── Mounted to: postgres:/var/lib/postgresql/data
│       ├── Base directory & transaction write-ahead logs (WAL)
│       └── Tablespaces, indexes, and constraint metadata
│
├── Host Mount: /var/lib/schedulemanager/minio_data
│   └── Mounted to: minio:/data
│       └── Bucket 'sih-artifacts'
│           └── projects/{project_id}/reports/{report_id}/artifacts/
│
└── Offsite Backup Target (Automated S3 / Cloudflare R2 Bucket)
    ├── Daily pg_dump: s3://schedulemanager-backups/db/db_YYYYMMDD_HHMM.sql.gz
    └── Nightly Artifact Sync: rclone sync /var/lib/schedulemanager/minio_data s3://schedulemanager-backups/artifacts/
```

---

## 5. Secrets & Credential Management

All sensitive secrets are injected strictly via environment variables loaded from a secure, non-committed `.env` file on the deployment host:

| Secret Key | Description | Sensitivity | Permitted Logging |
| :--- | :--- | :---: | :---: |
| `POSTGRES_PASSWORD` | Database administrative password | **CRITICAL** | **NEVER** |
| `MINIO_ROOT_PASSWORD` | MinIO root administrative secret | **CRITICAL** | **NEVER** |
| `TIME_AGENT_GEMINI_API_KEY` | Dedicated API key for conversational agent | **HIGH** | **NEVER** |
| `EXTRACTION_GEMINI_API_KEY` | Dedicated API key for document extraction | **HIGH** | **NEVER** |
| `SARVAM_API_KEY` | Dedicated API key for Sarvam STT/TTS | **HIGH** | **NEVER** |

### Credential Safety Rules:
1. `CredentialResolver` in `backend/app/services/credential_resolver.py` strictly prevents printing, string formatting, or logging API keys.
2. In production (`ENVIRONMENT=production`), automatic fallback to the shared legacy `GEMINI_API_KEY` is disabled. Dedicated keys must be provided.

---

## 6. External SaaS API Dependencies

The platform depends on two external HTTPS cloud APIs:

```
┌─────────────────────────┐          HTTPS / REST (TLS 1.3)
│   Backend Application   │ ───────────────────────────────────────┐
└─────────────────────────┘                                        │
                                                                   ▼
                                             ┌───────────────────────────────────────────┐
                                             │    Google Gemini Generative Language API  │
                                             │    - generativelanguage.googleapis.com    │
                                             │    - Models: gemini-2.5-flash             │
                                             │              gemini-3.5-flash             │
                                             │    - Auth: x-goog-api-key header          │
                                             └───────────────────────────────────────────┘
                                                                   │
                                                                   ▼
                                             ┌───────────────────────────────────────────┐
                                             │         Sarvam AI Multilingual API        │
                                             │    - api.sarvam.ai                        │
                                             │    - Service: Saaras v4 STT / Bulbul TTS  │
                                             │    - Auth: api-subscription-key header    │
                                             └───────────────────────────────────────────┘
```

Both external integrations are non-blocking for core schedule operations: CPM analysis, schedule import/export, and manual progress tracking function 100% offline without external AI availability.
