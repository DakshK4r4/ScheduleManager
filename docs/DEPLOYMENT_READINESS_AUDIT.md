# ScheduleManager Deployment-Readiness Audit and Architecture Strategy

**Audit Date:** September 26, 2026  
**Audited Repository:** ScheduleManager (`c:\Users\Gues\ScheduleManager`)  
**Auditor:** AI Engineering & Systems Architecture Team  
**Scope:** Complete deployment-readiness audit of active codebase, service boundaries, networking, persistence, security, file processing, and infrastructure dependencies.  
**Deployment Status:** PRE-DEPLOYMENT AUDIT COMPLETE — ZERO CLOUD INFRASTRUCTURE DEPLOYED.

---

## 1. Executive Summary

ScheduleManager is a specialized construction project controls and schedule intelligence platform. It provides end-to-end capabilities to ingest and parse Primavera P6 (.XER, .XML) and tabular (.XLSX, .CSV) project schedules, ingest and extract physical construction execution progress from multimodal field reports (PDFs, spreadsheets, site photos, voice memos), reconcile progress against CPM baseline schedules via multi-signal heuristic matching, execute CPM schedule updates with audit tracking, and provide conversational schedule interaction through an Indic-aware Time Agent.

### Core Audit Discoveries:
1. **Multi-Service Topology:** The platform consists of **three custom containerized application services** (`frontend`, `backend`, `document-parser`) and **two stateful backing services** (`postgres`, `minio`).
2. **Zero GPU Infrastructure Required:** Despite advanced multimodal extraction (vision, audio STT), the system offloads all heavy inference to external SaaS APIs (Google Gemini, Sarvam AI). CPM forward/backward pass calculations, Monte Carlo sampling, and candidate matching are deterministic CPU algorithms running in milliseconds.
3. **No Task Broker or Message Queue Present:** Neither Redis, RabbitMQ, Celery, nor RQ exists in the codebase. All schedule parsing, CPM recalculation, and multimodal LLM extractions run **synchronously** within FastAPI request-response lifecycles.
4. **Authentication & Authorization Gap:** There is **no authentication middleware, session management, OAuth2, or user table**. Endpoints accept unverified caller IDs (`uploaded_by="site-user"`, `reviewer_id="planner-user"`).
5. **Wildcard CORS Configuration:** `backend/app/main.py` applies `allow_origin_regex=r"^https?://.*$"` with `allow_credentials=True`. This allows arbitrary cross-origin requests.
6. **Database Migration Gap:** The repository lacks Alembic. Database tables are created via SQLAlchemy `Base.metadata.create_all()` and an inline `ALTER TABLE` script inside `init_db()` during application lifespan startup.
7. **Storage Portability:** While currently backed by a local MinIO container, `backend/app/services/minio_service.py` uses the standard Amazon S3 API protocol via the `minio` Python SDK, making it 100% compatible with AWS S3, Cloudflare R2, or Google Cloud Storage without code rewrites.

---

## 2. Phase 1 — Exact Service Inventory

Inspecting the repository files reveals the exact deployable components:

| Service / Component | Technology / Framework | Runtime & Language | Port (Internal/Host) | Dockerfile & Command | Persistent Data | Public vs. Private | Inbound Traffic | Outbound Traffic |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Frontend** | Next.js 14.2.5, React 18, TailwindCSS, TypeScript | Node.js 20 Alpine (`server.js`) | `3000:3000` | [frontend/Dockerfile](file:///c:/Users/Gues/ScheduleManager/frontend/Dockerfile)<br>`node server.js` | None (Stateless) | **Public** | User Browsers (HTTP) | `backend:8000` via Next.js `/api/proxy` rewrites |
| **Backend API** | FastAPI 0.110.0, SQLAlchemy 2.0, Uvicorn | Python 3.12 Slim | `8000:8080` | [backend/Dockerfile](file:///c:/Users/Gues/ScheduleManager/backend/Dockerfile)<br>`uvicorn app.main:app --port 8000` | None (Stateless compute) | **Private or Semi-Public** | Frontend proxy, Direct API clients | PostgreSQL, MinIO, Document-Parser, Gemini API, Sarvam API |
| **Document Parser** | FastAPI 0.110.0, openpyxl, pypdf, defusedxml | Python 3.12 Slim | `8001:8001` | [document-parser/Dockerfile](file:///c:/Users/Gues/ScheduleManager/document-parser/Dockerfile)<br>`uvicorn app.main:app --port 8001` | None (Stateless memory parser) | **Private Only** | `backend:8000` (`POST /parse`) | None |
| **PostgreSQL** | PostgreSQL 16 Alpine | C Daemon | `5432:5432` | Standard Docker Library Image | Tables, activities, progress ledger, audit trail, conversations | **Private Only** | `backend:8000` (TCP/5432) | None |
| **MinIO** | MinIO Object Storage | Go Daemon | `9000:9000`<br>`9001:9001` | `quay.io/minio/minio:latest`<br>`server /data` | Field artifacts (PDF, XLSX, CSV, audio, images) | **Private Only** (or managed S3) | `backend:8000` (S3 API) | None |
| **External LLM** | Google Gemini (2.5-flash, 3.5-flash) | External HTTPS SaaS | `443` | External Google API | Prompt responses, structured JSON | External | Backend (`httpx` / REST) | Google Cloud |
| **External STT** | Sarvam AI (Saaras v4, Indic TTS) | External HTTPS SaaS | `443` | External Sarvam SDK / REST | Audio transcripts, audio synthesis | External | Backend (`sarvamai` SDK) | Sarvam AI Cloud |

### Non-Existent Components (Do Not Infer):
- **Redis / Memcached:** NOT used in the codebase.
- **Celery / RabbitMQ / Background Workers:** NOT present.
- **Dedicated Vector Database (Pinecone, Qdrant, pgvector):** NOT used. Candidate activity retrieval relies on token scoring, Jaccard overlap, and string heuristics.
- **Direct Primavera / MS Project Server Sync:** NOT present. Integrations are strictly file-based (.XER, .XML, .XLSX, .CSV).

---

## 3. Phase 2 — Real Dependency Graph

```
                              [ User Web Browser ]
                                       │
                                       │ HTTPS (Port 443)
                                       ▼
                       [ Reverse Proxy / TLS Termination ]
                                (Nginx or Caddy)
                                       │
                                       │ HTTP (Port 3000)
                                       ▼
                             [ Frontend (Next.js) ]
                                       │
                                       │ HTTP /api/proxy (Internal Docker Network)
                                       ▼
                            [ Backend API (FastAPI) ]
                                       │
        ┌──────────────────┬───────────┴───────────┬──────────────────┐
        │ HTTP (8001)      │ PostgreSQL (5432)     │ S3 API (9000)    │ HTTPS (443)
        ▼                  ▼                       ▼                  ▼
[ Document Parser ]   [ PostgreSQL ]            [ MinIO ]      [ External APIs ]
(Stateless .XER/XML)  (Domain State)       (Raw Artifacts)    ├─ Google Gemini
                                                              └─ Sarvam AI
```

### Connection Specification Table:

| Connection | Protocol | Hostname & Port | Auth Mechanism | Network Scope | Timeout | Retry Policy |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Browser $\rightarrow$ Frontend** | HTTPS / HTTP | Public Domain :443 / :3000 | None currently | Public | Browser default | Browser default |
| **Frontend $\rightarrow$ Backend** | HTTP REST | `backend:8000` via `/api/proxy` | None currently | Private (Docker bridge) | 60s (Next.js default) | None |
| **Backend $\rightarrow$ Document Parser** | HTTP REST | `document-parser:8001` | None | Private (Docker bridge) | 60.0s (`httpx.AsyncClient`) | Total: 0 retries |
| **Backend $\rightarrow$ PostgreSQL** | PostgreSQL Wire | `postgres:5432` | Username/Password | Private (Docker bridge) | Pool pre-ping enabled | SQLAlchemy reconnect |
| **Backend $\rightarrow$ MinIO** | HTTP / S3 API | `minio:9000` | S3 Access Key / Secret Key | Private (Docker bridge) | Connect: 0.1s, Read: 0.3s probe; Normal: standard urllib3 | 0 retries on probe |
| **Backend $\rightarrow$ Gemini API** | HTTPS REST | `generativelanguage.googleapis.com:443` | API Key (`x-goog-api-key`) | Public Egress | 45.0s (`httpx.Client`) | Fallback across model pool |
| **Backend $\rightarrow$ Sarvam API** | HTTPS REST | `api.sarvam.ai:443` | API Key (`api-subscription-key`) | Public Egress | SDK default (~30s) | None (catches exception) |

---

## 4. Phase 3 — Database Audit (PostgreSQL)

### Data Persistence Classification:
- **`PERSISTENT BUSINESS CRITICAL`**:
  - `projects`, `calendars`, `wbs`, `activities`, `activity_relationships`: Authoritative CPM schedule baseline and mutated model.
  - `artifacts`: Provenance registry for uploaded evidence files, linking MinIO object keys and SHA-256 hashes.
  - `execution_events`: Physical site progress extracted from documents and voice notes.
  - `actual_progress_ledger`: Audit log recording percent complete and installed quantities.
  - `schedule_audit_log`: Historical record of schedule mutations.
  - `conversations`, `conversation_messages`, `update_proposals`: Time Agent chat history and pending schedule revisions.
- **`EPHEMERAL / STAGING`**:
  - `domain_outbox`: Staging table for domain events (currently no background dispatcher processes this table).

### Database Management & Migration Assessment:
1. **Migration Mechanism:** **No automated migration tool (Alembic) is configured.** Schema synchronization relies entirely on `Base.metadata.create_all(bind=engine)` inside `init_db()` in [backend/app/domain/database.py](file:///c:/Users/Gues/ScheduleManager/backend/app/domain/database.py).
2. **DDL Execution:** Schema patches are hardcoded as raw SQL `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` blocks executed on application startup.
3. **Connection Pooling:** Default SQLAlchemy `QueuePool` with `pool_pre_ping=True`. Pool size is 5 connections with max overflow 10.
4. **Foreign Keys & Integrity:** Extensive use of foreign key constraints with `ondelete="CASCADE"` for hierarchical tree nodes (`WBSNode`, `Activity`, `Artifact`) and `ondelete="RESTRICT"` for critical business proposals (`UpdateProposal`).
5. **Missing Operational Tooling:**
   - No automated backup/restore scripts (`pg_dump` automation).
   - No migration versioning or rollback capability.

---

## 5. Phase 4 — MinIO & Artifact Storage Audit

### Implementation Analysis:
- **Bucket Configuration:** Default bucket is `sih-artifacts` (defined via `MINIO_BUCKET`).
- **Object Key Convention:** Deterministic hierarchical structure:
  `projects/{project_id}/reports/{report_id}/artifacts/{artifact_id}/{sanitized_filename}`
- **Presigned URLs:** Generated via `minio_service.get_presigned_view_url(object_key, expires_seconds=900)`.
  > [!WARNING]
  > When `MINIO_ENDPOINT=minio:9000` (internal Docker hostname), presigned URLs contain `http://minio:9000/...`, which cannot be resolved by an external user browser! The backend includes a fallback download proxy endpoint (`GET /api/v1/artifacts/download?key=...`) to route file downloads through the backend.
- **Credential Exposure:** Frontend never receives raw MinIO credentials. All uploads stream through the backend (`POST /api/v1/projects/{project_id}/artifacts/upload`), which writes to MinIO server-side.

### Self-Hosted MinIO vs. Managed S3 Storage Comparison:

| Criteria | Self-Hosted MinIO Container | Managed S3 (AWS S3 / Cloudflare R2 / GCS) |
| :--- | :--- | :--- |
| **Operational Complexity** | High (Requires managing disk volumes, backups, SSL certs, updates) | **Zero** (Fully managed by cloud provider) |
| **Durability & Availability** | Limited to single disk volume unless distributed cluster built | **99.999999999% (11 9s)** durability with multi-AZ replication |
| **Monthly Cost** | Compute/disk cost of VPS | **Pay-as-you-go** ($0.015–$0.023/GB/mo; R2 has $0 egress fees) |
| **Code Changes Required** | None | **None** (`minio` Python SDK natively connects to S3/R2 endpoints) |
| **Recommendation** | Prototype / air-gapped deployments | **Recommended for Production** |

---

## 6. Phase 5 — File Processing & Synchronous Workload Audit

### Request Lifecycle & Latency Profile:
- **Endpoint:** `POST /api/v1/artifacts/{artifact_id}/extract`
- **Execution Mode:** **100% Synchronous.** The HTTP request remains open while:
  1. Backend fetches file bytes from MinIO (10ms–100ms).
  2. PDF text extraction & character density checking executes (20ms–200ms).
  3. If scanned/audio or text-rich, external Gemini / Sarvam API is invoked (3,000ms–25,000ms).
  4. Extracted items pass through `ExtractionQualityGate` and persist to PostgreSQL (20ms–50ms).
- **Total Request Latency:** **3 to 30 seconds**.
- **Timeout Implications:**
  - Standard web server / reverse proxy timeouts (e.g., Nginx default 60s) will accommodate typical reports.
  - Serverless platforms with rigid 10s–30s execution limits (AWS API Gateway 29s, Vercel Hobby 10s) risk dropping requests during large multi-page extractions.
- **Background Worker Verdict:**
  - **For Current Stage:** Do NOT introduce Redis/Celery immediately. The synchronous flow works reliably within containerized environments having $\ge 60$s proxy timeouts.
  - **For Future High-Concurrency:** Migrate `POST /extract` to asynchronous processing using FastAPI `BackgroundTasks` or an external Celery/Redis queue, returning `HTTP 202 Accepted` with polling or Webhooks.

---

## 7. Phase 6 — External AI Provider Audit

### Gemini API Integration:
- **Models:** `gemini-2.5-flash`, `gemini-3.5-flash`, `gemini-1.5-flash`.
- **Credential Governance:** Enforced via `CredentialResolver` in [credential_resolver.py](file:///c:/Users/Gues/ScheduleManager/backend/app/services/credential_resolver.py). Isolate keys: `TIME_AGENT_GEMINI_API_KEY` and `EXTRACTION_GEMINI_API_KEY`.
- **Timeout:** 45.0s per model attempt via `httpx.Client(timeout=45.0)`.
- **Model Fallback Pool:** If the primary model returns non-200 or times out, the code iterates through candidate models (`gemini-2.5-flash`, `gemini-1.5-flash`, `gemini-3.5-flash`).
- **Failure Behavior:** If all models fail, `ExtractionService` falls back to degraded deterministic rule-based keyword extraction. If no events match, `ExtractionQualityGate` transitions the artifact to `NEEDS_REVIEW` without hallucinating fake events.

### Sarvam AI Integration:
- **Service:** Saaras v4 Multilingual Speech-to-Text, Language Detection, Text-to-Speech.
- **Credential:** `SARVAM_API_KEY`.
- **Failure Behavior:** When offline or unconfigured, catches errors cleanly, preserves audio in MinIO, and sets `NEEDS_REVIEW`.

---

## 8. Phase 7 — Frontend Deployment Audit

### Next.js Architecture:
- **Version:** Next.js 14.2.5 (App Router / Pages architecture).
- **Build Mode:** Standalone (`output: "standalone"` in `next.config.js`).
- **Internal Proxy:** `next.config.js` defines rewrites mapping `/api/proxy/:path*` to `process.env.BACKEND_INTERNAL_URL || "http://backend:8000"`.
- **Deployment Platform Tradeoffs:**
  - **Vercel:** Requires public exposure of backend endpoints. Serverless edge functions cannot resolve internal Docker hostnames (`http://backend:8000`).
  - **Container (Docker / VPS):** **Best Match.** Runs `node server.js` on port 3000 within the same internal Docker network as `backend:8000`.

---

## 9. Phase 8 — Backend Security & Vulnerability Audit

| Vulnerability / Finding | Severity | Location | Description & Impact |
| :--- | :---: | :--- | :--- |
| **Missing Authentication & Authorization** | **CRITICAL** | All API routes | Endpoints lack token validation. Any client with network access can read or mutate schedules and artifacts. |
| **Wildcard CORS Configuration** | **HIGH** | `backend/app/main.py` | `allow_origin_regex=r"^https?://.*$"` with `allow_credentials=True` allows cross-origin requests from any site. |
| **Containers Running as Root** | **MEDIUM** | All Dockerfiles | Containers execute as UID 0 (`root`). Escape vulnerabilities could compromise the host system. |
| **Host Port Exposure in Compose** | **MEDIUM** | `docker-compose.yml` | PostgreSQL (`5432`) and Document Parser (`8001`) are exposed on all host interfaces (`0.0.0.0`). |
| **Synchronous HTTP Extraction Latency** | **MEDIUM** | `POST /artifacts/{id}/extract` | Request can hang for up to 30s during external AI calls, risking client or proxy timeouts. |
| **Unversioned Database Schema** | **LOW** | `database.py` | Relies on startup DDL scripting without rollback capability. |
| **SQL Injection Exposure** | **INFORMATIONAL** | SQL Queries | **SECURE.** Queries use SQLAlchemy ORM parameter binding. No raw string interpolation. |
| **Path Traversal Exposure** | **INFORMATIONAL** | MinIO uploads | **SECURE.** `MinioStorageService.sanitize_filename()` strips directories and special characters. |

---

## 10. Phase 9 — Container Audit

1. **Base Images:** Python services use `python:3.12-slim`; frontend uses `node:20-alpine`. Both are modern, lightweight bases.
2. **Dependency Pinning:** `requirements.txt` uses minimum version bounds (`fastapi>=0.110.0`). In production, lockfiles (`pip-tools` or `poetry.lock`) should be used to guarantee reproducible builds.
3. **Development Mounts in Compose:** `docker-compose.yml` includes `./backend:/app` and `./document-parser:/app`. These must be removed in production deployment manifests to run immutable built images.

---

## 11. Phase 10 & 11 — Networking & Persistence Matrix

### Network Exposure Policy:
- **Public Ingress:** Ports 80 and 443 (Reverse Proxy / TLS termination).
- **Private Internal Only:**
  - Port 3000 (`frontend`)
  - Port 8000 (`backend`)
  - Port 8001 (`document-parser`)
  - Port 5432 (`postgres`)
  - Port 9000/9001 (`minio`)

### Persistence Matrix:

| Component | Target Data | Durability Classification | Backup Mechanism | Recovery RTO / RPO |
| :--- | :--- | :--- | :--- | :--- |
| **PostgreSQL** | Projects, WBS, Activities, Events, Ledger, Audit Log | **CRITICAL PERSISTENT** | Daily automated `pg_dump` to offsite S3; WAL archiving | RTO < 1h, RPO < 24h (or <15m with WAL) |
| **MinIO / S3** | Raw PDF, XLSX, CSV, PNG, M4A artifacts | **CRITICAL PERSISTENT** | S3 bucket versioning & cross-region replication | RTO < 15m, RPO 0s (active storage) |
| **Frontend** | Build artifacts (`.next/static`) | **EPHEMERAL** | Rebuilt via CI/CD from git | RTO < 5m (container redeploy) |
| **Backend** | Compute container | **EPHEMERAL** | Rebuilt via CI/CD from git | RTO < 5m (container redeploy) |
| **Document Parser** | Compute container | **EPHEMERAL** | Rebuilt via CI/CD from git | RTO < 5m (container redeploy) |

---

## 12. Phase 12 & 13 — Observability & Resource Estimation

### Minimum Required Observability:
- **Health Checks:** Existing `GET /health` on backend and document-parser; Docker Compose health checks on postgres (`pg_isready`) and minio (`/minio/health/live`).
- **Container Logging:** Standardized stdout/stderr JSON logging for ingestion into CloudWatch, Loki, or Datadog.

### Resource Sizing Estimates:

| Component | Minimum Prototype Allocation | Recommended Production Allocation | Notes |
| :--- | :--- | :--- | :--- |
| **Frontend** | 0.25 vCPU, 256 MB RAM | 0.5 vCPU, 512 MB RAM | Handles Next.js SSR and internal proxy |
| **Backend API** | 0.75 vCPU, 1 GB RAM | 2.0 vCPU, 2 GB RAM | CPU spikes during CPM recalculation and chunking |
| **Document Parser** | 0.5 vCPU, 512 MB RAM | 1.0 vCPU, 1 GB RAM | Memory scales with XML DOM and Excel row parsing |
| **PostgreSQL** | 0.5 vCPU, 512 MB RAM, 10 GB Disk | 2.0 vCPU, 4 GB RAM, 50 GB NVMe | Scale storage with activity audit volume |
| **MinIO / Storage** | 0.25 vCPU, 256 MB RAM, 20 GB Disk | Managed S3 (Serverless) | Offload to managed S3 in production |
| **Total Footprint** | **2.25 vCPU, 2.5 GB RAM, 30 GB Disk** | **5.5 vCPU, 7.5 GB RAM + Managed S3** | Easily runs on a single 4 vCPU / 8 GB VPS |

---

## 13. Phase 14 & 15 — Deployment Options Comparison & Recommendation

### Comparison of Deployment Architectures:

| Option | Architecture Summary | Monthly Cost | Operational Complexity | Data Durability | Suitability for Current State |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Option A: Single Linux VPS with Production Compose & Caddy** | Single Ubuntu 24.04 VM (Hetzner / DigitalOcean / AWS Lightsail), Docker Compose, Caddy automated TLS. | **$12 – $24 / mo** | **Very Low** (Single system, zero cloud orchestration overhead) | Good (Host volume backups to offsite S3 required) | **EXCELLENT (Recommended for Current Prototype)** |
| **Option B: Managed PaaS (Render / Railway / Fly.io)** | Container services for frontend/backend/parser, managed PostgreSQL, Cloudflare R2 for artifacts. | **$25 – $65 / mo** | **Low** (No OS management; git push to deploy) | High (Managed DB and S3) | Good (Slightly higher cost; proxy timeout limits) |
| **Option C: AWS Enterprise Serverless / Container (ECS Fargate + RDS + S3)** | ECS Fargate tasks, AWS RDS PostgreSQL multi-AZ, AWS S3, CloudFront CDN, ALB. | **$150 – $350 / mo** | **High** (Terraform, VPCs, IAM, ALB, Task definitions) | Highest (Multi-AZ enterprise durability) | Over-engineered for prototype; target for production scale |
| **Option D: Kubernetes Cluster (EKS / GKE)** | K8s cluster, Helm charts, ingress controller, cert-manager. | **$120 – $400 / mo** | **Very High** (Cluster management, ingress, storage classes) | Highest | Strongly discouraged at current stage |

### Final Recommendation:
1. **CURRENT STAGE:** **Option A (Hardened Production Compose on a Single VPS)**. It fits the current synchronous multi-container architecture without rewriting network paths, minimizes cost ($12–$24/month), and operates cleanly under Caddy automated HTTPS.
2. **FUTURE PRODUCTION EVOLUTION:** **Option C (AWS ECS Fargate + RDS PostgreSQL + S3)** when tenant isolation, formal IAM, autoscaling, and Redis-backed asynchronous workers are introduced.

---

## 14. Phase 16 — Concrete Deployment Blockers

Before initiating deployment of the current prototype, the following specific blockers must be addressed:

1. **CORS Hardening:** Replace `allow_origin_regex=r"^https?://.*$"` with the specific production domain(s) in `backend/app/main.py`.
2. **Database Migration Baseline:** Initialize an Alembic migration environment to track schema revisions rather than relying on lifespan DDL execution.
3. **Environment Security:** Enforce separate, non-default passwords for PostgreSQL (`POSTGRES_PASSWORD`) and MinIO credentials in production `.env`.
4. **Remove Development Bind Mounts:** Create a production-grade compose file (`docker-compose.prod.yml`) omitting `./backend:/app` and `./document-parser:/app`.
5. **Close Host Port Leakage:** Bind internal database and parser ports to `127.0.0.1` or keep them entirely on the internal Docker network.
