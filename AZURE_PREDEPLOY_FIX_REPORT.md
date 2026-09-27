# Azure Pre-Deployment Fix Report: ScheduleManager

**Date:** September 27, 2026  
**Auditor / Engineer:** Senior Infrastructure & Cloud Systems Architect  
**Scope:** Execution of two pre-public-exposure remediation fixes for single Azure Linux VM deployment:
1. Public Ingress Access Barrier (Caddy HTTP Basic Authentication)
2. External Artifact View / MinIO URL Routing (Backend Streaming Endpoint)

---

## 1. Files Changed

1. [`Caddyfile`](file:///c:/Users/Gues/ScheduleManager/Caddyfile)
2. [`docker-compose.prod.yml`](file:///c:/Users/Gues/ScheduleManager/docker-compose.prod.yml)
3. [`.env.example`](file:///c:/Users/Gues/ScheduleManager/.env.example)
4. [`.env`](file:///c:/Users/Gues/ScheduleManager/.env) (local test configuration, gitignored)
5. [`backend/app/services/minio_service.py`](file:///c:/Users/Gues/ScheduleManager/backend/app/services/minio_service.py)
6. [`backend/app/api/artifacts.py`](file:///c:/Users/Gues/ScheduleManager/backend/app/api/artifacts.py)
7. [`backend/app/api/review.py`](file:///c:/Users/Gues/ScheduleManager/backend/app/api/review.py)
8. [`frontend/components/FieldReportsAndReview.tsx`](file:///c:/Users/Gues/ScheduleManager/frontend/components/FieldReportsAndReview.tsx)
9. [`backend/tests/test_extraction_matching_integration.py`](file:///c:/Users/Gues/ScheduleManager/backend/tests/test_extraction_matching_integration.py)
10. [`scripts/verify_caddy_auth.py`](file:///c:/Users/Gues/ScheduleManager/scripts/verify_caddy_auth.py) (new test verification script)

---

## 2. Exact Changes Made in Each File

### 1. `Caddyfile`
- **Location:** Line 16 (inside `{$DOMAIN_NAME::80}` site block).
- **Change:** Added `basic_auth` block:
  ```caddy
  # Public Ingress Access Barrier: HTTP Basic Authentication
  # Protects frontend UI, backend APIs, project mutations, and artifact endpoints.
  basic_auth {
      {$BASIC_AUTH_USER} {$BASIC_AUTH_HASH}
  }
  ```
- **Preservation:** Preserved all existing routes (`handle_path /api/proxy/*`, `@backend_routes`, `@backend_project_direct`, `@backend_project_mutations`, and `handle` for frontend SSR).

### 2. `docker-compose.prod.yml`
- **Location:** Lines 12-13 (under `caddy.environment`).
- **Change:** Injected authentication environment variables:
  ```yaml
  environment:
    - DOMAIN_NAME=${DOMAIN_NAME:-:80}
    - BASIC_AUTH_USER=${BASIC_AUTH_USER:?BASIC_AUTH_USER must be set}
    - BASIC_AUTH_HASH=${BASIC_AUTH_HASH:?BASIC_AUTH_HASH must be set}
  ```

### 3. `.env.example`
- **Location:** Section 1.5 (Lines 22-33).
- **Change:** Added documentation and variable declarations for ingress authentication without committing credentials:
  ```ini
  # ==============================================================================
  # 1.5 Ingress HTTP Basic Authentication (Public Access Barrier)
  # ==============================================================================
  # In production, the entire application is protected behind Caddy HTTP Basic Auth.
  # Credentials are never hardcoded in repository files.
  # Generate the password hash securely using Docker/Caddy on the Azure VM:
  #   docker run --rm caddy:2-alpine caddy hash-password --plaintext 'YOUR_STRONG_PASSWORD' | tr -d '\r\n' | base64
  #
  # Set the administrator username and the resulting base64-encoded bcrypt hash below:
  BASIC_AUTH_USER=admin
  BASIC_AUTH_HASH=CHANGE_ME_TO_BASE64_HASH_FROM_CADDY
  ```

### 4. `backend/app/services/minio_service.py`
- **Location:** Lines 8, 140-155, 155-177.
- **Change:**
  - Added `import urllib.parse`.
  - Hardened `get_artifact_bytes` fallback storage path to verify that resolved paths remain strictly within `self.fallback_dir` (directory traversal prevention).
  - Updated `get_presigned_view_url(object_key)` to return `/api/v1/artifacts/download?key={urllib.parse.quote(object_key, safe="")}`.
  - Added `get_s3_presigned_url(object_key)` as a fallback helper should direct public S3 presigned URLs be needed in future cloud storage setups.

### 5. `backend/app/api/artifacts.py`
- **Location:** Lines 1-6, 183-245, 264-298.
- **Change:**
  - Added `import os` and `import urllib.parse`.
  - Reordered routes: moved `download_artifact` (`GET /api/v1/artifacts/download`) **before** `get_artifact` (`GET /api/v1/artifacts/{artifact_id}`). In FastAPI / Starlette, parameterized path routes take precedence if registered earlier; having `/{artifact_id}` first caused requests to `/download` to be parsed as `artifact_id="download"`, resulting in false 404 errors.
  - Added helper `_resolve_media_type(key)` with dynamic MIME type mapping for PDF, XLSX, XLS, CSV, images (PNG, JPG, JPEG, WEBP, GIF, SVG), audio (M4A, MP3, WAV, OGG, WEBM), JSON, TXT, and XML.
  - Set response headers `Content-Disposition: inline; filename="{filename}"` and `Cache-Control: private, max-age=3600` so browser clients can view documents, play audio, and display images directly in tab.

### 6. `backend/app/api/review.py`
- **Location:** Lines 51-57.
- **Change:** Guarded `ev.storage_key` check before invoking `minio_service.get_presigned_view_url`, ensuring `artifact_view_url` in `ReviewQueueItemDTO` cleanly reflects the streaming backend URL.

### 7. `frontend/components/FieldReportsAndReview.tsx`
- **Location:** Line 521.
- **Change:** Updated evidence button text from `"View Original Evidence in MinIO"` to `"View Original Evidence"` to remove internal storage implementation details from the user interface.

### 8. `backend/tests/test_extraction_matching_integration.py`
- **Location:** Lines 150-205.
- **Change:**
  - Added assertions to `test_artifact_presigned_url` verifying:
    - `"minio:9000"` is NOT present in the returned URL.
    - `"localhost:9000"` is NOT present in the returned URL.
    - URL starts with `/api/v1/artifacts/download?key=`.
    - Downloading via the returned URL returns HTTP 200 with the exact binary payload, `Content-Type: application/pdf`, and `inline` disposition.
  - Added `test_artifact_download_media_types_and_encoding` testing XLSX, audio (M4A), and CSV artifact retrieval and MIME type resolution.

---

## 3. Why Each Change Was Required

| Fix | Problem in Previous Code | Production Impact / Risk | Remediation Rationale |
|---|---|---|---|
| **Caddy `basic_auth`** | Application has zero native user accounts, login UI, or RBAC. | Any public IP exposure permits unrestricted project deletion, activity modifications, and API quota abuse. | Ingress HTTP Basic Auth provides an immediate, foolproof security perimeter without requiring complex database auth schema rewrites. |
| **Base64 Hash in Env** | Raw bcrypt hashes contain multiple `$` signs (`$2a$14$...`). | Docker Compose v2 interpolates `$` inside `.env` and compose files, stripping characters and rendering password hashes unmatchable. | Caddy natively supports base64-encoded bcrypt strings (`JDJh...`), which are pure alphanumeric characters and immune to Docker Compose interpolation. |
| **Backend Streaming URL** | `minio_service.py` generated `http://minio:9000/...`. | Browser running outside the Azure VM cannot resolve Docker container name `minio`, breaking evidence links in the review queue. | Routing via `/api/v1/artifacts/download?key=...` keeps MinIO 100% private while allowing browsers to stream evidence securely through Caddy. |
| **FastAPI Route Reordering** | `@router.get("/api/v1/artifacts/{artifact_id}")` was defined prior to `/download`. | FastAPI matched `/artifacts/download` with `artifact_id = "download"`, querying PostgreSQL for ID `"download"` and returning 404. | Moving static `/artifacts/download` ahead of parameterized `/{artifact_id}` restores standard FastAPI routing precedence. |
| **Dynamic MIME Types** | `download_artifact` hardcoded `application/pdf` or `application/octet-stream`. | Spreadsheets, site photos, and audio notes downloaded with incorrect media types or failed to play in-browser. | Added dynamic MIME mapping and inline `Content-Disposition` headers. |

---

## 4. Environment Variables Added

| Variable Name | Service | Required? | Secret? | Default Value | Purpose |
|---|---|---|---|---|---|
| `BASIC_AUTH_USER` | `caddy` | **Yes** (Prod) | No | `admin` | Ingress administrator username for HTTP Basic Auth barrier. |
| `BASIC_AUTH_HASH` | `caddy` | **Yes** (Prod) | **Yes** | None (Template in `.env.example`) | Base64-encoded bcrypt hash of the administrator password. |

### How to Generate Password Hash on Azure VM:
```bash
# 1. Run Caddy inside a temporary container to hash your chosen production password:
docker run --rm caddy:2-alpine caddy hash-password --plaintext 'YOUR_SECURE_PASSWORD' | tr -d '\r\n' | base64

# Example output:
# JDJhJDE0JHBNQWtVSjAwN3lXem04YVBiZWVnOS5FM3RnZEFxdGV0V3VaNHBCemo3SC45U1RXakpmekdH

# 2. Add to production .env file:
# BASIC_AUTH_USER=admin
# BASIC_AUTH_HASH=JDJhJDE0JHBNQWtVSjAwN3lXem04YVBiZWVnOS5FM3RnZEFxdGV0V3VaNHBCemo3SC45U1RXakpmekdH
```

---

## 5. Tests Executed

1. **Caddyfile Configuration Validation:**
   ```bash
   docker exec -e BASIC_AUTH_USER=admin -e BASIC_AUTH_HASH=... primavera-caddy-prod caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile
   ```
2. **Ingress Authentication End-to-End Suite (`scripts/verify_caddy_auth.py`):**
   - Unauthenticated GET to root `/` -> Assert HTTP 401 Unauthorized.
   - Unauthenticated GET to API `/api/test` -> Assert HTTP 401 Unauthorized.
   - Request with invalid credentials -> Assert HTTP 401 Unauthorized.
   - Request with valid credentials (`admin:ProductionSecretPassword123!`) -> Assert HTTP 200 OK.
   - Frontend route with valid credentials -> Assert HTTP 200 OK.
3. **Artifact Streaming & View URL Tests (`backend/tests/test_extraction_matching_integration.py`):**
   - `test_artifact_presigned_url`
   - `test_artifact_download_media_types_and_encoding`
4. **Full Backend Integration Test Suite:**
   ```bash
   docker exec primavera-backend-prod pytest tests/
   ```

---

## 6. Test Results

### Ingress Basic Auth Integration:
```text
[TEST] Raw hash: $2a$14$pMAkUJ007yWzm8aPbeeg9.E3tgdAqtetWuZ4pBzj7H.9STWjJfzGG
[TEST] Base64 hash: JDJhJDE0JHBNQWtVSjAwN3lXem04YVBiZWVnOS5FM3RnZEFxdGV0V3VaNHBCemo3SC45U1RXakpmekdH
[TEST] 1. Unauthenticated request correctly rejected with 401 Unauthorized
[TEST] 2. Unauthenticated API request correctly rejected with 401 Unauthorized
[TEST] 3. Wrong password correctly rejected with 401 Unauthorized
[TEST] 4. Correct credentials authenticated successfully! (HTTP 200, Body: API_OK)
[TEST] 5. Frontend route authenticated successfully! (HTTP 200, Body: FRONTEND_OK)
[TEST] ALL CADDY BASIC AUTH TESTS PASSED!
```

### Artifact & MinIO Streaming Tests:
```text
tests/test_extraction_matching_integration.py ................           [100%]
======================== 16 passed, 1 warning in 4.02s =========================
```
- No `minio:9000` URLs returned to browser.
- Streaming endpoint `/api/v1/artifacts/download?key=...` verified for PDF, XLSX, CSV, and audio (M4A).

### Full Test Suite:
```text
================== 211 passed, 2 failed in 46.12s ==================
```
*(The two failed tests are pre-existing and unrelated to our changes: 1 CPM timing assertion on high concurrency: 3.62s vs 2.5s threshold; 1 Hindi Sarvam prompt assertion).*

---

## 7. Security Requirements Verification

- [x] **No plaintext password in tracked files:** Verified. `Caddyfile` and `docker-compose.prod.yml` reference only environment variables; `.env.example` contains only template placeholders.
- [x] **No MinIO credentials exposed to browser code:** Verified. Credentials remain exclusively on internal Docker networks.
- [x] **No MinIO hostname returned to browser-facing API responses:** Verified. Presigned URL generator returns `/api/v1/artifacts/download?key=...`.
- [x] **No port 9000 host binding added:** Verified. MinIO remains internal on `schedule_net` / `p6-prod-network`.
- [x] **No port 9001 host binding added:** Verified. MinIO console remains inaccessible from host.
- [x] **No PostgreSQL host binding added:** Verified. Port 5432 remains isolated.
- [x] **No parser host binding added:** Verified. Port 8001 remains isolated.
- [x] **No backend host binding added:** Verified. Port 8000 remains accessible only via Caddy reverse proxy.

---

## 8. Remaining Pre-Deployment Risks

1. **Host Build RAM:** The Next.js image build (`npm run build`) requires ~1.2–1.5 GB peak RAM. When deploying to an Azure `Standard_B1ms` VM (2 GB RAM), ensure a **2 GB swapfile** is enabled prior to running `docker compose -f docker-compose.prod.yml build` to prevent the Linux OOM killer from terminating the build.
2. **Production `.env` Secrets:** The administrator must generate unique production passwords for `POSTGRES_PASSWORD`, `MINIO_ROOT_PASSWORD`, and `BASIC_AUTH_HASH` when provisioning the Azure VM.
