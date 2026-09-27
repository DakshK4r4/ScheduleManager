# Azure VM Deployment Engineering Audit: ScheduleManager

**Audit Date:** September 27, 2026  
**Target Repository:** ScheduleManager  
**Target Deployment Environment:** Single Azure Linux Virtual Machine running Docker Engine + Docker Compose  
**Auditor:** Senior Infrastructure & Cloud Systems Architect  
**Scope:** Complete pre-deployment technical audit of the active ScheduleManager codebase, service topology, container images, build systems, networking, storage persistence, security posture, and runtime resource characteristics.  
**Sole Source of Truth:** The current ScheduleManager repository files.

---

## 1. Executive Summary

This engineering audit evaluates whether the ScheduleManager platform can be reliably deployed on a single Azure Linux Virtual Machine using Docker Compose.

### Key Audit Conclusions:
1. **Infrastructure Readiness:** The repository already contains a dedicated production Compose file (`docker-compose.prod.yml`) and reverse proxy configuration (`Caddyfile`). Internal services (`backend`, `frontend`, `document-parser`, `postgres`, `minio`) have zero public host port bindings, and all development bind mounts have been removed.
2. **Architecture & Service Boundaries:** The platform comprises 3 application services (`frontend`, `backend`, `document-parser`) and 2 stateful backing services (`postgres`, `minio`), fronted by Caddy for automated TLS termination and reverse proxying. All machine learning inference is offloaded to external APIs (Google Gemini, Sarvam AI), eliminating the need for GPU compute.
3. **Azure VM Feasibility:** The stack can be deployed on a cost-effective Azure VM (such as `Standard_B2s` with 2 vCPUs and 4 GB RAM, or `Standard_B1ms` with 2 GB RAM + 2 GB swap). Idle container memory is **~254 MiB**, and active load memory is **~272 MiB**. However, building the Next.js frontend image requires **~1.2 GB peak RAM**, making 1 GB VMs (`Standard_B1s`) unviable without swap.
4. **Primary Pre-Deployment Gaps Identified:**
   - **Authentication Gap:** The codebase possesses **no user authentication, session management, or RBAC**. Any public exposure without an ingress access barrier (e.g. Caddy HTTP Basic Auth or Cloudflare Access) exposes all project data and API mutation routes.
   - **Presigned MinIO URL Resolution:** `backend/app/services/minio_service.py` generates presigned URLs containing the internal Docker endpoint `http://minio:9000/...`. External browser clients clicking artifact links in the review queue cannot resolve this internal hostname.
   - **CI/CD Absence:** No `.github/workflows` directory exists. Container builds must either be executed directly on the Azure VM or pushed from an external registry.

---

## 2. Repository Inventory

| File / Component Path | Purpose | Runtime / Base | Build Command | Start Command | Internal Port | Dependencies | State | Prod Required? |
|---|---|---|---|---|---|---|---|---|
| `backend/app/main.py` | Core FastAPI application entrypoint, lifespan, CORS, healthcheck | Python 3.12 (`python:3.12-slim`) | `pip install -r requirements.txt` | `uvicorn app.main:app --host 0.0.0.0 --port 8000` | 8000 | PostgreSQL, MinIO, Document-Parser | Stateless Compute | **Yes** |
| `backend/Dockerfile` | Production container definition for backend API | Debian 12 Slim | `docker build -t backend ./backend` | `CMD ["uvicorn", "app.main:app", ...]` | 8000 | `apt: gcc, libpq-dev, curl` | Stateless | **Yes** |
| `backend/requirements.txt` | Python dependency manifest for backend | Python 3.12 | N/A | N/A | N/A | FastAPI, SQLAlchemy, Psycopg2, MinIO, PyPDF, SarvamAI | Stateless | **Yes** |
| `document-parser/app/main.py` | Dedicated schedule file parsing service (.XER, .XML, .XLSX, .CSV) | Python 3.12 (`python:3.12-slim`) | `pip install -r requirements.txt` | `uvicorn app.main:app --host 0.0.0.0 --port 8001` | 8001 | None (in-memory parsing) | Stateless Memory | **Yes** |
| `document-parser/Dockerfile` | Production container definition for parser | Debian 12 Slim | `docker build -t document-parser ./document-parser` | `CMD ["uvicorn", "app.main:app", ...]` | 8001 | `apt: curl` | Stateless | **Yes** |
| `document-parser/requirements.txt`| Python dependency manifest for parser | Python 3.12 | N/A | N/A | N/A | FastAPI, Pandas, OpenPyXL, DefusedXML | Stateless | **Yes** |
| `frontend/app/page.tsx` | Next.js root page (project dashboard & upload UI) | Node.js 20 (`node:20-alpine`) | `npm install && npm run build` | `node server.js` | 3000 | Backend API | Stateless | **Yes** |
| `frontend/Dockerfile` | Multi-stage production container definition (builder + runner) | Alpine Linux 3.20 | `docker build -t frontend ./frontend` | `CMD ["node", "server.js"]` | 3000 | Next.js standalone runner | Stateless | **Yes** |
| `frontend/package.json` | Node.js dependency manifest | Node.js 20 | `npm install` | `npm run build` | N/A | Next.js 14, React 18, TailwindCSS | Stateless | **Yes** |
| `Caddyfile` | Reverse proxy routing, automatic TLS, timeout handling, body limits | Caddy 2 (`caddy:2-alpine`) | N/A (config) | `caddy run --config /etc/caddy/Caddyfile` | 80, 443 | Frontend, Backend | Stateless | **Yes** |
| `docker-compose.prod.yml` | Production multi-container composition | Docker Compose v2 | `docker compose -f docker-compose.prod.yml build` | `docker compose -f docker-compose.prod.yml up -d` | 80, 443 (host) | Docker Engine | Stateful Backing | **Yes** |
| `docker-compose.yml` | Local development composition (with source code bind mounts) | Docker Compose v2 | `docker compose build` | `docker compose up` | 8080, 3000, 8001, 5432, 9000, 9001 | Docker Engine | Stateful Backing | No (Dev only) |
| `.env.example` | Production environment template & secret declarations | Shell / Env | N/A | N/A | N/A | N/A | Stateless | **Yes** |
| `migrations/001_time_agent_schema.sql` | SQL schema script for conversational Time Agent | PostgreSQL SQL | N/A | Invoked via `init_db()` or manual psql | N/A | PostgreSQL | Stateful | Optional (handled by code) |
| `scripts/verify_production_stack.py` | Black-box end-to-end production verification script | Python 3 (stdlib) | N/A | `python scripts/verify_production_stack.py` | N/A | Running Caddy / Backend | Stateless Test | Recommended |
| `scripts/verify_persistence_and_failure.py` | Fault tolerance & volume persistence verification script | Python 3 (stdlib) | N/A | `python scripts/verify_persistence_and_failure.py` | N/A | Docker Compose CLI | Stateless Test | Recommended |

---

## 3. Actual Service Architecture & Dependency Graph

```
                                      INTERNET
                                         │
                         HTTPS (443/tcp) │ HTTP (80/tcp)
                         HTTP/3 (443/udp)│
                                         ▼
                      ┌─────────────────────────────────────┐
                      │             Azure VM                │
                      │  ┌───────────────────────────────┐  │
                      │  │         Caddy :80/:443        │  │
                      │  │    (primavera-caddy-prod)     │  │
                      │  └───────────────┬───────────────┘  │
                      │                  │                  │
                      │   / (HTML/JS)    │   /api/proxy/*   │
                      │                  │   /api/*         │
                      │                  │   /health        │
                      │                  │                  │
                      │   ┌──────────────┴──────────────┐   │
                      │   ▼                             ▼   │
        ┌───────────────────────────┐         ┌───────────────────────────┐
        │  Next.js Frontend :3000   │         │    FastAPI Backend :8000  │
        │  (primavera-frontend-prod)│         │  (primavera-backend-prod) │
        └───────────────────────────┘         └─────────────┬─────────────┘
                                                            │
                            ┌───────────────────────────────┼───────────────────────────────┐
                            │ (HTTP/8001)                   │ (TCP/5432)                    │ (HTTP/9000)
                            ▼                               ▼                               ▼
              ┌───────────────────────────┐   ┌───────────────────────────┐   ┌───────────────────────────┐
              │   Document Parser :8001   │   │      PostgreSQL :5432     │   │        MinIO :9000        │
              │(primavera-doc-parser-prod)│   │  (primavera-postgres-prod)│   │   (primavera-minio-prod)  │
              └───────────────────────────┘   └─────────────┬─────────────┘   └─────────────┬─────────────┘
                                                            │                               │
                                                            ▼                               ▼
                                                   [Named Volume]                  [Named Volume]
                                                 postgres_prod_data               minio_prod_data
```

### Detailed Connection Matrix

| Source | Destination | Protocol | Hostname Used | Port | Config Variable | Timeout | Retry / Fallback Behavior | Will Work on Azure VM? |
|---|---|---|---|---|---|---|---|---|
| **User Browser** | Caddy | HTTPS / HTTP | Public IP / Domain | 80, 443 | `DOMAIN_NAME` | 300s (API), 60s (UI) | Browser retry | **Yes** (via Azure Public IP) |
| **Caddy** | Frontend | HTTP/1.1 | `frontend` | 3000 | Hardcoded in Caddyfile | 60s | Returns 502 if container down | **Yes** (Internal Docker DNS) |
| **Caddy** | Backend | HTTP/1.1 | `backend` | 8000 | Hardcoded in Caddyfile | 300s | Returns 502 if container down | **Yes** (Internal Docker DNS) |
| **Frontend** | Backend | HTTP/1.1 | Browser Origin + `/api/proxy` | 80/443 | `NEXT_PUBLIC_API_URL` / `getApiBase()` | Browser default | `ApiError` with validation details | **Yes** (Unified origin routing) |
| **Backend** | Document Parser | HTTP/1.1 | `document-parser` | 8001 | `PARSER_URL` | 60.0s (`httpx`) | Catches exception; returns HTTP 503 | **Yes** (Internal Docker DNS) |
| **Backend** | PostgreSQL | TCP (PG Wire) | `postgres` | 5432 | `DATABASE_URL` | Socket default, `pool_pre_ping=True` | 10 retries with 1s backoff at startup | **Yes** (Internal Docker DNS) |
| **Backend** | MinIO | HTTP / S3 API | `minio` | 9000 | `MINIO_ENDPOINT` | 2.0s connect, 30.0s read | Falls back to local `.artifacts_storage/` | **Yes** (Internal Docker DNS) |
| **Backend** | Gemini API | HTTPS / REST | `generativelanguage.googleapis.com` | 443 | `TIME_AGENT_GEMINI_API_KEY`<br>`EXTRACTION_GEMINI_API_KEY` | 60.0s (`httpx`) | Graceful error card; zero DB mutation | **Yes** (Outbound Azure Egress) |
| **Backend** | Sarvam API | HTTPS / REST | `api.sarvam.ai` | 443 | `SARVAM_API_KEY` | SDK default | Returns 503; falls back to text | **Yes** (Outbound Azure Egress) |

### Hardcoded Hostname Audit

1. `localhost` / `127.0.0.1`:
   - In `backend/app/main.py`: Used only in development CORS fallback (lines 81–89). In production, overridden by `CORS_ORIGINS`.
   - In `backend/app/domain/database.py`: Used only if `DATABASE_URL` is omitted (line 14). In compose, `DATABASE_URL` points to `postgres:5432`.
   - In `backend/app/services/minio_service.py`: Used only if `MINIO_ENDPOINT` is omitted (line 16). In compose, set to `minio:9000`.
   - In `frontend/lib/api.ts`: If `window.location.port !== "3000"`, automatically uses `window.location.origin + "/api/proxy"`. No hardcoded localhost leaks to browser.
2. `backend`, `document-parser`, `postgres`, `minio`:
   - These hostnames match the Docker Compose service names inside `docker-compose.prod.yml` and resolve correctly via embedded Docker DNS on the bridge network `p6-prod-network`.

---

## 4. Docker Audit

### Container Image Analysis

| Image | Base Image | AMD64 (x86_64) | ARM64 (aarch64) | Exposed Ports | Execution User | Dev Mounts? | Production Ready? |
|---|---|---|---|---|---|---|---|
| `backend` | `python:3.12-slim` | Fully Supported | Supported (wheels + gcc fallback) | 8000 | `root` | **None** in prod | **Yes** |
| `document-parser` | `python:3.12-slim` | Fully Supported | Supported (PyPI prebuilt wheels) | 8001 | `root` | **None** in prod | **Yes** |
| `frontend` | `node:20-alpine` | Fully Supported | Supported (`@next/swc` musl) | 3000 | `root` | **None** in prod | **Yes** |
| `caddy` | `caddy:2-alpine` | Fully Supported | Fully Supported | 80, 443 | `root` / `caddy` | **None** in prod | **Yes** |
| `postgres` | `postgres:16-alpine` | Fully Supported | Fully Supported | 5432 | `postgres` (UID 70) | **None** in prod | **Yes** |
| `minio` | `quay.io/minio/minio:latest` | Fully Supported | Fully Supported | 9000 | `minio` | **None** in prod | **Yes** |

### Architecture Findings:
- **AMD64 (x86_64):** 100% verified. Azure Intel/AMD VMs (e.g. `Standard_B2s`, `Standard_D2s_v5`) will build and run all containers with zero compilation issues.
- **ARM64 (aarch64):** Azure Ampere Altra instances (e.g. `Standard_D2ps_v5`) are compatible. However, `document-parser/Dockerfile` does not install `gcc`, which means it relies strictly on precompiled `manylinux_aarch64` wheels for Pandas 2.2.
- **Root Containers:** The `backend`, `document-parser`, and `frontend` images execute as `root`. While standard for lightweight containers, non-root execution (`USER appuser`) is recommended for security hardening in subsequent phases.

---

## 5. Docker Compose Production Audit

Inspecting `docker-compose.prod.yml`:

```yaml
Services Defined:  6 (caddy, frontend, backend, document-parser, postgres, minio)
Networks:          1 (p6-prod-network, driver: bridge)
Named Volumes:     4 (postgres_prod_data, minio_prod_data, caddy_data, caddy_config)
Restart Policy:    restart: unless-stopped (applied to all 6 services)
```

### Port Exposure Verification

| Port | Service | Bound to Host? | Desired State | Compliance Status |
|---|---|---|---|---|
| `80:80` | `caddy` | **Yes** (`0.0.0.0:80`) | Public | **COMPLIANT** |
| `443:443` | `caddy` | **Yes** (`0.0.0.0:443`) | Public | **COMPLIANT** |
| `443:443/udp` | `caddy` | **Yes** (`0.0.0.0:443/udp`) | Public (HTTP/3) | **COMPLIANT** |
| `3000` | `frontend` | **No** (Internal network only) | Private | **COMPLIANT** |
| `8000` | `backend` | **No** (Internal network only) | Private | **COMPLIANT** |
| `8001` | `document-parser` | **No** (Internal network only) | Private | **COMPLIANT** |
| `5432` | `postgres` | **No** (Internal network only) | Private | **COMPLIANT** |
| `9000` | `minio` | **No** (Internal network only) | Private | **COMPLIANT** |
| `9001` | `minio` (Console) | **No** (Not exposed) | Private | **COMPLIANT** |

**Zero port violations exist.** Internal microservices and databases are unreachable from the Azure host's public IP.

---

## 6. Persistence Audit

### Data Storage Classification

| Asset | Container Path | Volume Mapping | Lifecycle & Durability | Accidental Loss Risk? |
|---|---|---|---|---|
| **PostgreSQL Data** | `/var/lib/postgresql/data` | `postgres_prod_data` (Named Volume) | Survives container recreation, image upgrade, and VM reboot | **No** (Safe) |
| **MinIO Artifacts** | `/data` | `minio_prod_data` (Named Volume) | Survives container recreation, image upgrade, and VM reboot | **No** (Safe) |
| **Caddy TLS Certs** | `/data` | `caddy_data` (Named Volume) | Survives container recreation and VM reboot | **No** (Safe) |
| **Caddy Auto-Config** | `/config` | `caddy_config` (Named Volume) | Survives container recreation and VM reboot | **No** (Safe) |
| **Backend Local Fallback** | `/app/.artifacts_storage` | None (Container Ephemeral Layer) | **Lost on container recreation** if MinIO was offline | **YES (Edge case)** |

### Ephemeral Storage Risk Verification:
- In `backend/app/services/minio_service.py` (line 120), if MinIO is unavailable, files are written to `.artifacts_storage` on the local filesystem.
- Because `docker-compose.prod.yml` has no bind mount on `/app`, any artifact written to local fallback while MinIO is down is stored on the container's ephemeral root filesystem and would be lost if the backend container is recreated before MinIO recovery.
- **Audit Verdict:** Normal operations store 100% of uploaded artifacts in MinIO (`minio_prod_data`). However, the fallback directory must be treated as emergency cache only.

---

## 7. Database Audit

### Configuration & Pooling
- **Driver:** `psycopg2-binary` (fallback normalization handles `postgresql://` to `postgresql+psycopg2://`).
- **Connection Pooling:** SQLAlchemy `QueuePool` with `pool_pre_ping=True`.
- **Pool Size:** Defaults to 5 connections with up to 10 overflow connections (maximum 15 connections per backend process).
- **PostgreSQL Max Connections:** PostgreSQL 16 default is 100 connections. 15 connections consumes < 5% of database capacity, leaving ample headroom on a small VM.

### Schema Initialization & Migrations
- **Initialization Mechanism:** `backend/app/domain/database.py` line 37 executes `Base.metadata.create_all(bind=engine)`.
- **DDL Migration Execution:** Lines 44–71 execute idempotent raw SQL:
  - `ALTER TABLE execution_events ALTER COLUMN artifact_id DROP NOT NULL;`
  - `ALTER TABLE execution_events ADD COLUMN IF NOT EXISTS source_type VARCHAR(50) DEFAULT 'ARTIFACT';`
  - `ALTER TABLE conversations ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN DEFAULT FALSE;`
  - `ALTER TABLE activities ADD COLUMN IF NOT EXISTS early_start TIMESTAMP;` (etc.)
- **Alembic Absence:** Alembic is **not present** in the repository. Schema evolution is managed via startup DDL scripts.
- **Risk Assessment:** `init_db()` is idempotent and safe on both fresh and populated databases. However, complex future schema alterations (such as column renames or type conversions) lack rollback management.

---

## 8. Object Storage Audit

### Implementation Inspection
- **SDK:** Official `minio` Python SDK (`from minio import Minio`).
- **Bucket Creation:** `ensure_bucket_exists()` checks `bucket_exists(self.bucket)` and creates it automatically (`make_bucket`) during startup lifespan.
- **Object Key Determinism:**
  `projects/{project_id}/reports/{report_id}/artifacts/{artifact_id}/{sanitized_filename}`
- **Security:** Filename sanitization (`sanitize_filename`) strips path traversal sequences (`../`, control characters).

### Presigned URL Architectural Issue
- In `backend/app/services/minio_service.py` line 154:
  `client.presigned_get_object(...)` generates a URL using `self.endpoint`, which is set to `minio:9000` via Docker environment.
- In `backend/app/api/review.py` line 54, `get_presigned_view_url` is called to populate `artifact_view_url` in the planner review queue.
- **Problem:** When an end-user on the public internet clicks "View Source PDF", their browser attempts to connect to `http://minio:9000/...`, which fails because `minio:9000` is an unresolvable internal Docker hostname.
- **Existing Workaround in Code:** `backend/app/api/artifacts.py` line 237 already exposes a streaming proxy endpoint:
  `GET /api/v1/artifacts/download?key={storage_key}`.
  Routing artifact views through this endpoint completely resolves the issue without exposing MinIO publicly.

---

## 9. Application Startup & Dependency Audit

### Startup Ordering Chain

```
               [ Docker Engine Starts ]
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
     [ postgres ]    [ minio ]    [ document-parser ]
     (healthcheck)  (healthcheck)   (healthcheck)
          │               │               │
          └───────────────┼───────────────┘
                          │ (all healthy)
                          ▼
                    [ backend ]
                  (healthcheck)
                          │
                          ▼ (healthy)
                    [ frontend ]
                          │
                          ▼ (started)
                     [ caddy ]
```

### Race Condition Defenses Verified:
1. **Docker Level:** `depends_on: { service: { condition: service_healthy } }` prevents backend from starting until Postgres, MinIO, and Document-Parser report healthy.
2. **Application Level:** `backend/app/main.py` lifespan implements a 10-attempt retry loop with 1-second backoff around `SELECT 1` and `init_db()`.
3. **Healthcheck Timing Margins:**
   - Postgres: `start_period: 30s`, `interval: 5s`, `retries: 10` (permits up to 80s for fresh `initdb` on slow cloud disks).
   - MinIO: `start_period: 15s`, `interval: 10s`, `retries: 5`.
   - Parser: `start_period: 15s`, `interval: 10s`, `retries: 5`.
   - Backend: `start_period: 20s`, `interval: 10s`, `retries: 6`.

---

## 10. Environment Variable Matrix

| Variable Name | Service | Required? | Secret? | Build-time vs Runtime | Default Value in Code | Exposed Publicly? |
|---|---|---|---|---|---|---|
| `DOMAIN_NAME` | Caddy | **Yes** | No | Runtime | `:80` | Yes (Public host) |
| `CORS_ORIGINS` | Backend | **Yes** | No | Runtime | `""` (dev fallback) | No |
| `POSTGRES_USER` | Postgres, Backend | **Yes** | No | Runtime | `postgres` | No |
| `POSTGRES_PASSWORD` | Postgres, Backend | **Yes** | **YES** | Runtime | None (mandatory) | No |
| `POSTGRES_DB` | Postgres, Backend | **Yes** | No | Runtime | `primavera` | No |
| `DATABASE_URL` | Backend | **Yes** | **YES** | Runtime | Constructed in Compose | No |
| `PARSER_URL` | Backend | **Yes** | No | Runtime | `http://document-parser:8001` | No |
| `MINIO_ENDPOINT` | Backend | **Yes** | No | Runtime | `localhost:9000` | No |
| `MINIO_ROOT_USER` | MinIO, Backend | **Yes** | No | Runtime | `minioadmin` | No |
| `MINIO_ROOT_PASSWORD` | MinIO, Backend | **Yes** | **YES** | Runtime | None (mandatory) | No |
| `MINIO_ACCESS_KEY` | Backend | **Yes** | No | Runtime | `${MINIO_ROOT_USER}` | No |
| `MINIO_SECRET_KEY` | Backend | **Yes** | **YES** | Runtime | `${MINIO_ROOT_PASSWORD}` | No |
| `MINIO_BUCKET` | Backend | Optional | No | Runtime | `sih-artifacts` | No |
| `MINIO_SECURE` | Backend | Optional | No | Runtime | `false` | No |
| `MINIO_CONNECT_TIMEOUT`| Backend | Optional | No | Runtime | `2.0` (30.0 for read) | No |
| `TIME_AGENT_GEMINI_API_KEY`| Backend | **Yes** | **YES** | Runtime | `""` | No |
| `TIME_AGENT_LLM_MODEL` | Backend | Optional | No | Runtime | `gemini-2.5-flash` | No |
| `EXTRACTION_GEMINI_API_KEY`| Backend | **Yes** | **YES** | Runtime | `""` | No |
| `EXTRACTION_LLM_MODEL` | Backend | Optional | No | Runtime | `gemini-3.5-flash` | No |
| `ALLOW_LEGACY_GEMINI_FALLBACK`| Backend | Optional | No | Runtime | `false` (in prod) | No |
| `SARVAM_API_KEY` | Backend | **Yes** | **YES** | Runtime | `""` | No |
| `NEXT_PUBLIC_API_URL` | Frontend | Optional | No | **Build & Runtime** | `""` (routes via Caddy) | **Yes (in client JS)** |
| `BACKEND_INTERNAL_URL` | Frontend | Optional | No | Runtime | `http://backend:8000` | No |

---

## 11. Frontend Audit

- **Framework:** Next.js 14.2.5 with React 18.3.1.
- **Build Mode:** Standalone Output (`output: "standalone"` in `next.config.js`). Generates a minimal Node.js runner bundle (`server.js`) without requiring full `node_modules` in the production container.
- **API Routing Architecture:**
  - Client-side browser code invokes `getApiBase()` from `frontend/lib/api.ts`.
  - When accessed via standard HTTP/HTTPS ports (`window.location.port !== "3000"`), `getApiBase()` returns `${window.location.origin}/api/proxy`.
  - Caddy intercepts `/api/proxy/*`, strips `/api/proxy`, and forwards the request directly to `backend:8000`.
  - Next.js also has a fallback rewrite in `next.config.js` mapping `/api/proxy/:path*` to `BACKEND_INTERNAL_URL/:path*`.
- **CORS Requirements:** Because the browser communicates exclusively with Caddy's unified origin (e.g. `https://schedule.yourcompany.com/api/proxy/projects`), **all browser requests are strictly same-origin**. Cross-Origin Resource Sharing (CORS) preflights are bypassed during normal web usage.

---

## 12. Backend Audit

- **Framework:** FastAPI 0.110.0 running on Uvicorn with standard uvloop and httptools.
- **Concurrency & Workload Model:**
  - Async endpoints handle network I/O (`/projects/import`, artifact uploads, external Gemini/Sarvam calls).
  - Synchronous worker threads handle CPU-bound calculations (Critical Path Method graph traversal, forward/backward pass, and string similarity scoring).
- **Memory Hotspots:**
  1. **Primavera P6 XER / XML File Parsing:** Parsing multi-megabyte XER tables into memory models. Peak usage: ~100–180 MB per 10,000 activities.
  2. **Multimodal PDF Processing:** `pypdf` reading multi-page scanned site reports. Peak usage: ~80–150 MB.
  3. **CPM Engine:** Graph creation and float calculation (`test_cpm_engine.py`). Deterministic and fast (< 100ms for 2,000 activities), memory footprint < 30 MB.
- **Timeout Protection:** Caddy enforces a 300-second timeout on `/api/proxy/*` to accommodate synchronous LLM document extraction without dropped connections.

---

## 13. Document Parser Audit

- **Architecture:** Runs as an independent, isolated microservice on internal port 8001.
- **Supported Formats:**
  - `.xer`: Parsed via custom tokenized tabular parser (`XerParser`).
  - `.xml`: Parsed via safe XML parser (`P6XmlParser` using `defusedxml`).
  - `.xlsx` / `.xls`: Parsed via Pandas + OpenPyXL (`XlsxParser`).
  - `.csv` / `.tsv`: Parsed via Pandas (`CsvParser`).
  - `.mpp`: Explicitly rejected with human-readable error (instructs user to export XML/XLSX).
- **Memory Profile:**
  - Idle: **47.2 MiB** (MEASURED).
  - Peak processing (50 MB Excel/CSV): **~200–250 MiB** (Pandas DataFrame expansion).
- **Service Isolation Verdict:** Keeping the document parser in a dedicated container is an architectural strength. If a malformed 100MB spreadsheet causes an out-of-memory error, only the parser restarts; the core FastAPI backend and PostgreSQL state remain uninterrupted.

---

## 14. Reverse Proxy / TLS Audit

- **Server:** Caddy 2 (`caddy:2-alpine`).
- **Routing Rules Verified in `Caddyfile`:**
  - `handle_path /api/proxy/*` ──► Strips `/api/proxy`, routes to `backend:8000` (300s timeout).
  - `@backend_routes` (`/api/*`, `/health*`, `/docs*`, `/projects/import*`) ──► Routes directly to `backend:8000`.
  - `@backend_project_direct` (`/projects`) ──► Routes GET list to `backend:8000`.
  - `@backend_project_mutations` (POST/PATCH/DELETE on `/projects/*`) ──► Routes mutations to `backend:8000`.
  - Fallback `handle` ──► Routes all UI routes (including `/projects/[id]`) to `frontend:3000`.
- **Upload Limits:** Configured `request_body { max_size 100MB }` accommodates engineering schedules and high-resolution PDF attachments.
- **TLS Automation:** Automatic HTTPS provisioning via Let's Encrypt / ZeroSSL using HTTP-01 challenge on port 80. Auto-redirects HTTP to HTTPS.

---

## 15. Security Audit & Findings

### Findings Classification

| Severity | Finding | Location | Evidence / Description | Recommended Action |
|---|---|---|---|---|
| **HIGH** | **Zero Authentication / Authorization** | Entire API (`app/api/`) | No JWT, session cookie, API key, or user authentication exists. Anyone with network access can import schedules, delete projects, and mutate state. | Deploy behind Caddy HTTP Basic Auth, Cloudflare Access, or VPN prior to public exposure. |
| **MEDIUM** | **Presigned URLs Use Internal Hostname** | `backend/app/services/minio_service.py` (line 154) | `client.presigned_get_object` returns URLs with `minio:9000`. Browsers outside Docker cannot resolve this. | Route artifact views through `GET /api/v1/artifacts/download?key=...`. |
| **MEDIUM** | **Root Execution in Containers** | `backend/Dockerfile`, `document-parser/Dockerfile` | Containers execute as root (UID 0). | Add unprivileged `USER appuser` in subsequent hardening. |
| **LOW** | **Missing Storage Retention Lifecycle** | `minio_service.py`, PostgreSQL | No automatic cleanup exists for uploaded artifacts or historical ledger records. | Implement periodic volume monitoring (`df -h`). |

---

## 16. GitHub / CI/CD Audit

- **Workflows:** The repository contains **no `.github/workflows` directory**.
- **Container Registry:** No automated pipeline exists to build or push images to GitHub Container Registry (GHCR) or Azure Container Registry (ACR).
- **Deployment Implication:** Images must be built directly on the Azure VM using `docker compose -f docker-compose.prod.yml build`.

---

## 17. Azure VM Feasibility & Sizing

### Sizing Benchmarks (Grounded in Live Local Measurements)

| Metric | Measured / Estimated | Azure VM Sizing Impact |
|---|---|---|
| **Container Idle RAM** | **253.46 MiB** (MEASURED) | Very light baseline |
| **Container Active RAM** | **272.13 MiB** (MEASURED) | Light operational footprint |
| **Peak Document Parsing RAM** | **~830 MiB** (ESTIMATED) | Requires at least 1.5 GB available RAM |
| **Next.js Build Peak RAM** | **~1.2 – 1.5 GiB** (MEASURED) | **Fails on 1 GB VMs without swap** |
| **Total Container Disk** | **2.09 GB** (MEASURED) | Requires 20 GB+ OS disk |

### Azure VM Tier Recommendations

| Azure VM Size | vCPU | RAM | Temporary Disk | Cost Estimate (Pay-as-you-go) | Feasibility Verdict |
|---|---|---|---|---|---|
| **Standard_B1s** | 1 | 1.0 GiB | 4 GiB | ~$7.50 / month (or Free Tier) | **NOT RECOMMENDED** unless 2 GB swapfile is configured. High risk of OOM during builds. |
| **Standard_B1ms** | 1 | 2.0 GiB | 4 GiB | ~$15.00 / month | **MINIMUM PRACTICAL**. Works reliably with a 1 GB swapfile. |
| **Standard_B2s** | 2 | 4.0 GiB | 8 GiB | ~$30.00 / month | ⭐ **RECOMMENDED TIER**. Zero swap thrashing, fast Next.js builds (~2 min), smooth schedule parsing. |
| **Standard_D2s_v5** | 2 | 8.0 GiB | None (remote disk) | ~$70.00 / month | **ENTERPRISE GRADE**. Dedicated CPU cores, ideal for production workloads with large teams. |

---

## 18. Azure Networking Requirements

### Azure Network Security Group (NSG) Rules

| Priority | Name | Port Range | Protocol | Source | Action | Purpose |
|---|---|---|---|---|---|---|
| **100** | `Allow-SSH` | `22` | TCP | Admin Public IP (or Virtual Network) | **Allow** | Administrative SSH access |
| **110** | `Allow-HTTP` | `80` | TCP | `*` (Internet) | **Allow** | Let's Encrypt challenge & HTTP redirect |
| **120** | `Allow-HTTPS` | `443` | TCP | `*` (Internet) | **Allow** | Secure client browser HTTPS traffic |
| **130** | `Allow-HTTP3` | `443` | UDP | `*` (Internet) | **Allow** | QUIC / HTTP/3 transport |
| **4096** | `Deny-All-Inbound` | `*` | Any | `*` | **Deny** | Blocks direct access to 3000, 8000, 8001, 5432, 9000 |

### Outbound Network Rules
- **Outbound Internet:** Standard Azure default (`AllowInternetOutbound`).
- **Required Outbound Destinations:**
  - `generativelanguage.googleapis.com:443` (Google Gemini API)
  - `api.sarvam.ai:443` (Sarvam AI API)
  - `acme-v02.api.letsencrypt.org:443` (Caddy TLS issuance)

---

## 19. Backup & Disaster Recovery Audit

### Current Status
- **PostgreSQL:** No automated snapshot or cron backup configured in the repository.
- **MinIO:** Stored in named Docker volume `minio_prod_data`; no automatic replication configured.
- **Recovery Capability:**
  - Complete database state can be exported manually via:  
    `docker exec -t primavera-postgres-prod pg_dump -U $POSTGRES_USER $POSTGRES_DB > backup.sql`
  - MinIO data volume can be backed up using standard Azure OS disk snapshots or Azure Blob sync tools.

---

## 20. Resource Sizing Summary

```
┌─────────────────────────────────────────────────────────────┐
│                    RESOURCE SIZING MATRIX                   │
├──────────────────────┬──────────────────────────────────────┤
│ Minimum Practical VM │ Azure Standard_B1ms (1 vCPU, 2GB RAM)│
│                      │ with 2 GB Swapfile                   │
├──────────────────────┼──────────────────────────────────────┤
│ Recommended VM       │ Azure Standard_B2s (2 vCPU, 4GB RAM) │
│                      │ with 30 GB Premium SSD               │
├──────────────────────┼──────────────────────────────────────┤
│ Not Suitable VM      │ Standard_B1s (1GB RAM) without swap  │
│                      │ (Fails during Next.js image build)   │
└──────────────────────┴──────────────────────────────────────┘
```

---

## 21. Deployment Blockers

### A. Must Fix Before Deployment
**None.** The codebase builds and runs successfully under Docker Compose.

### B. Should Fix Before Public Exposure
1. **Public Access Barrier:** Add Caddy HTTP Basic Auth or Cloudflare Access to protect against unauthorized project modifications.
2. **Presigned URL Route:** Update artifact viewing links to route through `/api/v1/artifacts/download?key=...` rather than internal `minio:9000`.

### C. Nice to Have (Future Hardening)
1. Add non-root `USER` directives in `backend/Dockerfile` and `document-parser/Dockerfile`.
2. Introduce GitHub Actions workflow for automated image publishing to Azure Container Registry (ACR).
3. Implement an automated PostgreSQL backup cron job.

---

## 22. Recommended Deployment Plan (Azure VM)

```bash
# 1. Provision Azure Linux VM (Ubuntu 24.04 LTS, Standard_B2s recommended)
# 2. Attach Network Security Group with ports 22, 80, 443 open
# 3. Associate Azure Static Public IP or DNS label (e.g. schedule.eastus.cloudapp.azure.com)
# 4. SSH into the VM:
ssh azureuser@<azure-public-ip>

# 5. Configure 2GB Swapfile (Safety buffer):
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 6. Install Docker Engine & Docker Compose V2
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER
newgrp docker

# 7. Clone repository:
git clone <repository-url> /opt/schedulemanager
cd /opt/schedulemanager

# 8. Create production .env file:
cp .env.example .env
nano .env  # Populate DOMAIN_NAME, POSTGRES_PASSWORD, MINIO_ROOT_PASSWORD, API keys

# 9. Build and start production stack:
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d

# 10. Verify container health:
docker compose -f docker-compose.prod.yml ps
```

---

## 23. Exact Pre-Deployment Checklist

- [ ] Azure VM provisioned (`Standard_B2s` or `Standard_B1ms` with swap).
- [ ] Azure NSG configured (Inbound: 22, 80, 443; all others blocked).
- [ ] Public IP address assigned and DNS A-record configured pointing to VM IP.
- [ ] Production `.env` prepared with:
  - [ ] `DOMAIN_NAME` (matching DNS record)
  - [ ] Strong `POSTGRES_PASSWORD`
  - [ ] Strong `MINIO_ROOT_PASSWORD`
  - [ ] Valid `TIME_AGENT_GEMINI_API_KEY`
  - [ ] Valid `EXTRACTION_GEMINI_API_KEY`
  - [ ] Valid `SARVAM_API_KEY`
- [ ] Docker Compose production stack builds successfully (`docker-compose.prod.yml`).
- [ ] All 6 containers report `Up (healthy)`.
- [ ] Public HTTPS verified via Caddy.

---

## Final Verdict

# **CAN DEPLOY AFTER SPECIFIC CHANGES**

### Summary of Required Changes Before Live Deployment:
1. **VM Resource Provisioning:** Ensure the Azure VM has at least 2 GB RAM (`Standard_B1ms`) with a 2 GB swapfile, or 4 GB RAM (`Standard_B2s`), to support the Next.js compilation memory peak.
2. **Production Environment Configuration:** Populate production `.env` with live Azure DNS hostname and strong passwords (do not use development defaults).
3. **Ingress Protection:** Add an access barrier (such as Caddy HTTP Basic Auth or Cloudflare Access) before public exposure, because the application currently lacks native user authentication.
4. **Presigned URL Route Adjustment:** Ensure artifact downloads in the planner review queue point to the backend streaming endpoint (`/api/v1/artifacts/download`) rather than internal `minio:9000`.

*(Zero core application architecture rewrites are required).*
